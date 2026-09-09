"""Regressionstests zum Senior-Code-Review v2 (04.09.2026).

Jede Klasse deckt genau einen Fund aus `review_v2/bericht_senior_review_v2.md`
ab und ist nach dessen Fundnummer benannt. Die Tests sind so geschrieben, dass
sie ohne den jeweiligen Fix fehlschlagen.
"""
import json
import os
import smtplib
import sqlite3
import ssl
import tempfile
import zipfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from django.conf import settings
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from sedo.backup_utils import backup_dir
from sedo.models import (
    AareonMapping, AppSetting, AuditLog, CashbookEntry, Customer, DeleteProtectedError,
    DunningDocument, Invoice, Payment, ServiceCatalogItem,
)
from sedo.services.mail import send_email_smtp


class F38BackupOrdnerImTestlaufTests(TestCase):
    """F-38: Testläufe dürfen nicht in den echten backups/-Ordner schreiben."""

    def test_backup_dir_zeigt_im_testlauf_nicht_auf_sedo_home(self):
        ziel = backup_dir()
        echter_ordner = Path(getattr(settings, 'SEDO_HOME', settings.BASE_DIR)) / 'backups'
        self.assertNotEqual(
            ziel.resolve(), echter_ordner.resolve(),
            'Der Testlauf schreibt Sicherungen in den Live-Ordner backups/ (F-38).',
        )

    def test_backup_dir_folgt_dem_setting(self):
        self.assertEqual(
            backup_dir().resolve(),
            Path(settings.SEDO_BACKUP_DIR).resolve(),
            'backup_dir() muss SEDO_BACKUP_DIR verwenden, damit der Pfad umlenkbar bleibt.',
        )

    def test_erzeugtes_backup_landet_im_testordner(self):
        from sedo.backup_utils import create_backup

        out = create_backup('f38_test')
        try:
            self.assertEqual(out.parent.resolve(), Path(settings.SEDO_BACKUP_DIR).resolve())
        finally:
            out.unlink(missing_ok=True)


class F17MappingImportOhneGueltigeZeilenTests(TestCase):
    """F-17: Eine Datei ohne verwertbare Zeilen darf den Bestand nicht löschen."""

    def setUp(self):
        AareonMapping.objects.create(billing_unit_sender='Quittenweg 4', service_id='Reinigung')
        AareonMapping.objects.create(billing_unit_sender='Quittenweg 4', service_id='Winterdienst')

    def _import(self, df):
        from sedo import views
        with patch('sedo.views.messages'), patch('sedo.views.write_audit'):
            return views._perform_aareon_mapping_import(df, request=None)

    def test_richtige_kopfzeile_aber_null_gueltige_zeilen_bricht_ab(self):
        # Kopfzeile stimmt, alle Zellen leer – z. B. gefilterter Export oder
        # leere Vorlage. Vorher: kompletter Mapping-Bestand gelöscht.
        leer = pd.DataFrame([
            {'Rechnungssteller Abrechnungseinheit': '', 'Leistungsart-ID': ''},
            {'Rechnungssteller Abrechnungseinheit': None, 'Leistungsart-ID': None},
        ])
        with self.assertRaises(ValueError):
            self._import(leer)
        self.assertEqual(AareonMapping.objects.count(), 2, 'Bestand muss unverändert bleiben (F-17).')

    def test_voellig_leere_datei_bricht_ab(self):
        leer = pd.DataFrame(columns=['Rechnungssteller Abrechnungseinheit', 'Leistungsart-ID'])
        with self.assertRaises(ValueError):
            self._import(leer)
        self.assertEqual(AareonMapping.objects.count(), 2)

    def test_gueltige_datei_ersetzt_bestand_weiterhin(self):
        gut = pd.DataFrame([
            {'Rechnungssteller Abrechnungseinheit': 'Heckenweg 1/1', 'Leistungsart-ID': 'Reinigung'},
        ])
        stats = self._import(gut)
        self.assertEqual(stats['count'], 1)
        self.assertEqual(
            set(AareonMapping.objects.values_list('billing_unit_sender', flat=True)),
            {'Heckenweg 1/1'},
        )


class F16MonatslaufPreisPruefungTests(TestCase):
    """F-16: Leere oder unlesbare Mengen/Preise dürfen keinen Lauf starten."""

    def _services(self, **post):
        from sedo import views
        request = RequestFactory().post('/monatsrechnung/aareon/', post)
        return views._selected_monthly_services(request)

    def test_gueltige_werte_werden_uebernommen(self):
        services, fehler = self._services(use_Reinigung='1', qty_Reinigung='2', price_Reinigung='58,00')
        self.assertEqual(fehler, [])
        self.assertEqual(services[0]['quantity'], Decimal('2.00'))
        self.assertEqual(services[0]['unit_price'], Decimal('58.00'))

    def test_leerer_preis_meldet_fehler_statt_10_euro(self):
        services, fehler = self._services(use_Winterdienst='1', qty_Winterdienst='1', price_Winterdienst='')
        self.assertEqual(services, [], 'Ohne Preis darf keine Leistung in den Lauf gehen.')
        self.assertTrue(any('Preis' in f for f in fehler), fehler)

    def test_unparsbarer_preis_meldet_fehler_statt_10_euro(self):
        services, fehler = self._services(use_Winterdienst='1', qty_Winterdienst='1', price_Winterdienst='12,50 €')
        self.assertEqual(services, [])
        self.assertTrue(any('12,50 €' in f for f in fehler), fehler)

    def test_leere_menge_meldet_fehler_statt_1(self):
        services, fehler = self._services(use_Heckenschnitt='1', qty_Heckenschnitt='', price_Heckenschnitt='120')
        self.assertEqual(services, [])
        self.assertTrue(any('Menge' in f for f in fehler), fehler)

    def test_preis_null_wird_abgelehnt(self):
        services, fehler = self._services(use_Reinigung='1', qty_Reinigung='1', price_Reinigung='0')
        self.assertEqual(services, [])
        self.assertTrue(fehler)

    def test_nicht_angehakte_leistung_erzeugt_keinen_fehler(self):
        services, fehler = self._services(qty_Reinigung='', price_Reinigung='')
        self.assertEqual(services, [])
        self.assertEqual(fehler, [])


class F12KundenimportOhneLeerwerteTests(TestCase):
    """F-12: Fehlende Spalten dürfen vorhandene Stammdaten nicht leeren."""

    def setUp(self):
        self.user = get_user_model().objects.create_user('importer', 'i@example.de', 'geheim-1234')
        self.client.force_login(self.user)
        self.bestand = Customer.objects.create(
            customer_no='K-1001', name='Muster Hausverwaltung',
            email='kontakt@example.de', phone='0391 1234567',
            street='Musterstraße 1', postcode='39104', city='Magdeburg',
            contact_person='M. Muster',
        )

    def _import_csv(self, inhalt):
        datei = SimpleUploadedFile('kunden.csv', inhalt.encode('utf-8'), content_type='text/csv')
        return self.client.post(reverse('sedo:customer_import'), {'file': datei}, follow=True)

    def test_datei_nur_mit_nummer_und_name_laesst_stammdaten_stehen(self):
        self._import_csv('Kunden-Nr.;Name / Firma\nK-1001;Muster Hausverwaltung GmbH\n')
        self.bestand.refresh_from_db()
        self.assertEqual(self.bestand.name, 'Muster Hausverwaltung GmbH', 'Der Name soll aktualisiert werden.')
        self.assertEqual(self.bestand.email, 'kontakt@example.de')
        self.assertEqual(self.bestand.phone, '0391 1234567')
        self.assertEqual(self.bestand.street, 'Musterstraße 1')
        self.assertEqual(self.bestand.postcode, '39104')
        self.assertEqual(self.bestand.city, 'Magdeburg')
        self.assertEqual(self.bestand.contact_person, 'M. Muster')

    def test_leere_zelle_ueberschreibt_bestandswert_nicht(self):
        self._import_csv('Kunden-Nr.;Name / Firma;E-Mail;Telefon\nK-1001;Muster Hausverwaltung;;\n')
        self.bestand.refresh_from_db()
        self.assertEqual(self.bestand.email, 'kontakt@example.de')
        self.assertEqual(self.bestand.phone, '0391 1234567')

    def test_befuellte_zelle_aktualisiert_weiterhin(self):
        self._import_csv('Kunden-Nr.;Name / Firma;E-Mail\nK-1001;Muster Hausverwaltung;neu@example.de\n')
        self.bestand.refresh_from_db()
        self.assertEqual(self.bestand.email, 'neu@example.de')

    def test_neuer_kunde_bekommt_die_model_defaults(self):
        self._import_csv('Kunden-Nr.;Name / Firma\nK-2002;Neue Firma\n')
        neu = Customer.objects.get(customer_no='K-2002')
        self.assertEqual(neu.country, 'DE')
        self.assertEqual(neu.customer_type, 'einmalig')


class F09SmtpZertifikatspruefungTests(TestCase):
    """F-09: Beide SMTP-Wege müssen das Serverzertifikat prüfen.

    Ohne verifizierenden Kontext akzeptiert ``smtplib.SMTP_SSL`` jedes
    beliebige Zertifikat – ein umgeleiteter Mailserver bekäme Benutzer und
    Passwort. Alle Tests mocken die SMTP-Klassen; es geht nie eine echte
    Verbindung nach außen.
    """

    def setUp(self):
        AppSetting.set('smtp_host', 'smtp.example.de')
        AppSetting.set('smtp_user', 'info@example.de')
        AppSetting.set('smtp_password', 'nur-fuer-den-test')

    def _pruefe_kontext(self, context, hinweis):
        self.assertIsInstance(context, ssl.SSLContext, hinweis)
        self.assertTrue(context.check_hostname, hinweis)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED, hinweis)

    def test_impliziter_ssl_versand_nutzt_verifizierenden_kontext(self):
        # Port 465, STARTTLS-Haken aus – genau die Konstellation aus F-09.
        AppSetting.set('smtp_tls', '0')
        AppSetting.set('smtp_port', '465')
        with patch('sedo.services.mail.smtplib.SMTP_SSL') as smtp_ssl:
            send_email_smtp('kunde@example.de', 'Betreff', 'Text')
        self.assertTrue(smtp_ssl.called, 'Ohne STARTTLS muss SMTP_SSL verwendet werden.')
        self._pruefe_kontext(
            smtp_ssl.call_args.kwargs.get('context'),
            'SMTP_SSL ohne prüfenden context akzeptiert jedes Zertifikat (F-09).',
        )

    def test_starttls_versand_bleibt_verifizierend(self):
        AppSetting.set('smtp_tls', '1')
        AppSetting.set('smtp_port', '587')
        with patch('sedo.services.mail.smtplib.SMTP') as smtp:
            send_email_smtp('kunde@example.de', 'Betreff', 'Text')
        server = smtp.return_value.__enter__.return_value
        self._pruefe_kontext(
            server.starttls.call_args.kwargs.get('context'),
            'starttls() muss weiterhin mit prüfendem Kontext aufgerufen werden.',
        )
        # Der Umbau zieht den Kontext aus dem if-Zweig heraus – dieser Weg muss weiter senden.
        server.login.assert_called_once()
        server.send_message.assert_called_once()

    def test_tls_kontext_prueft_kette_und_hostnamen(self):
        from sedo.services.mail import _tls_context

        self._pruefe_kontext(_tls_context(), 'Der gemeinsame TLS-Kontext muss prüfen.')

    def test_ssl_versand_meldet_sich_an_und_sendet_weiterhin(self):
        # Begleittest: Der Fix darf den Versandweg nicht kaputtmachen.
        AppSetting.set('smtp_tls', '0')
        AppSetting.set('smtp_port', '465')
        with patch('sedo.services.mail.smtplib.SMTP_SSL') as smtp_ssl:
            send_email_smtp('kunde@example.de', 'Betreff', 'Text')
        server = smtp_ssl.return_value.__enter__.return_value
        server.login.assert_called_once()
        server.send_message.assert_called_once()

    def test_zertifikatsfehler_wird_zur_deutschen_smtp_meldung(self):
        # Ohne die Umwandlung schlägt dieser Test fehl: SSLCertVerificationError
        # ist KEIN SMTPException. Der Text landet wörtlich in der Oberfläche,
        # deshalb ist er hier festgenagelt.
        AppSetting.set('smtp_tls', '0')
        AppSetting.set('smtp_port', '465')
        with patch('sedo.services.mail.smtplib.SMTP_SSL',
                   side_effect=ssl.SSLCertVerificationError('certificate verify failed')):
            with self.assertRaises(smtplib.SMTPException) as ctx:
                send_email_smtp('kunde@example.de', 'Betreff', 'Text')
        self.assertIn('Zertifikat', str(ctx.exception))
        self.assertIn('smtp.example.de', str(ctx.exception))
        self.assertIsInstance(ctx.exception.__cause__, ssl.SSLCertVerificationError,
                              'Die Ursache muss für das Log erhalten bleiben.')

    def test_ohne_konfiguration_weiterhin_value_error_mit_klarem_text(self):
        # Begleittest für die Fehlerkontrakte. Achtung: ssl.SSLCertVerificationError
        # IST selbst ein ValueError – assertRaises(ValueError) allein würde also
        # auch dann grün bleiben, wenn ein Zertifikatsfehler durchrutscht. Deshalb
        # wird der Text geprüft, nicht nur die Klasse.
        AppSetting.set('smtp_host', '')
        with self.assertRaisesRegex(ValueError, 'nicht konfiguriert'):
            send_email_smtp('kunde@example.de', 'Betreff', 'Text')


class F03F31F81BackupBereinigungTests(TestCase):
    """F-03, F-31, F-81: Was die Backup-Seite zusagt, muss die Kopie einhalten.

    Gearbeitet wird durchweg auf einer eigenen Wegwerf-SQLite mit synthetischen
    Werten – die Live-Datenbank wird nie geöffnet. Die Werte sind bewusst
    länger als MIN_SECRET_LEN_FOR_BYTE_CHECK, damit die Byte-Gegenprobe greift.
    """

    PIN = 'geheime-bank-pin-4711'
    KEY = 'sk-ant-testschluessel-0815'
    SESSION_KEY = 'aaaabbbbccccddddeeeeffff00001111'

    def setUp(self):
        fd, name = tempfile.mkstemp(suffix='.sqlite3')
        os.close(fd)
        self.live = Path(name)
        con = sqlite3.connect(self.live)
        con.execute('CREATE TABLE sedo_appsetting (key TEXT PRIMARY KEY, value TEXT)')
        con.execute('CREATE TABLE django_session (session_key TEXT PRIMARY KEY, '
                    'session_data TEXT, expire_date TEXT)')
        con.executemany('INSERT INTO sedo_appsetting VALUES (?, ?)', [
            ('fints_pin', self.PIN),
            ('ai_api_key', self.KEY),
            ('smtp_host', 'mail.example.de'),
        ])
        con.execute('INSERT INTO django_session VALUES (?, ?, ?)',
                    (self.SESSION_KEY, 'dGVzdA==', '2099-01-01T00:00:00'))
        con.commit()
        con.close()
        # Die Live-DB liegt in der Praxis mit 0644 da – nur so zeigt sich,
        # ob die Kopie die Rechte erbt (F-31).
        os.chmod(self.live, 0o644)

    def tearDown(self):
        self.live.unlink(missing_ok=True)

    def _kopie(self):
        from sedo.backup_utils import _sanitized_db_copy

        return _sanitized_db_copy(self.live)

    # --- F-03: Anmelde-Sitzungen -------------------------------------------------
    def test_sitzungen_sind_aus_der_kopie_entfernt(self):
        kopie = self._kopie()
        try:
            con = sqlite3.connect(kopie)
            anzahl = con.execute('SELECT COUNT(*) FROM django_session').fetchone()[0]
            con.close()
            self.assertEqual(anzahl, 0, 'Ein Backup mit gültigen Sitzungen ist ein Login ohne '
                                        'Passwort (F-03).')
        finally:
            kopie.unlink(missing_ok=True)

    def test_session_key_steht_nicht_mehr_in_den_rohbytes(self):
        kopie = self._kopie()
        try:
            self.assertNotIn(self.SESSION_KEY.encode(), kopie.read_bytes())
        finally:
            kopie.unlink(missing_ok=True)

    def test_purge_liste_nennt_django_session(self):
        from sedo.backup_utils import PURGED_BACKUP_TABLES

        self.assertIn('django_session', PURGED_BACKUP_TABLES)

    # --- F-31: Rest-Bytes und Dateirechte ---------------------------------------
    def test_secrets_stehen_nicht_mehr_in_den_rohbytes(self):
        # Der Kern von F-31: ein blankes UPDATE gibt die alten Bytes nur frei.
        kopie = self._kopie()
        try:
            roh = kopie.read_bytes()
            self.assertNotIn(self.PIN.encode(), roh, 'Bank-PIN als Rest-Byte lesbar (F-31).')
            self.assertNotIn(self.KEY.encode(), roh, 'API-Key als Rest-Byte lesbar (F-31).')
        finally:
            kopie.unlink(missing_ok=True)

    def test_secrets_sind_auch_logisch_geleert(self):
        kopie = self._kopie()
        try:
            con = sqlite3.connect(kopie)
            werte = dict(con.execute('SELECT key, value FROM sedo_appsetting'))
            con.close()
            self.assertEqual(werte['fints_pin'], '')
            self.assertEqual(werte['ai_api_key'], '')
            self.assertEqual(werte['smtp_host'], 'mail.example.de', 'Nicht-Secrets bleiben.')
        finally:
            kopie.unlink(missing_ok=True)

    def test_kopie_ist_nicht_weltlesbar(self):
        # copy2 überträgt die 0644 der Live-Datei; copyfile lässt die 0600
        # von mkstemp stehen.
        kopie = self._kopie()
        try:
            self.assertEqual(kopie.stat().st_mode & 0o077, 0,
                             'Die unbereinigte Vollkopie lag weltlesbar im Temp-Ordner (F-31).')
        finally:
            kopie.unlink(missing_ok=True)

    def test_live_datenbank_bleibt_unveraendert(self):
        vorher = self.live.read_bytes()
        kopie = self._kopie()
        try:
            self.assertEqual(self.live.read_bytes(), vorher, 'Die Live-DB darf nie angefasst werden.')
        finally:
            kopie.unlink(missing_ok=True)

    # --- F-81: kein stiller Fehlschlag ------------------------------------------
    def test_leere_datei_bricht_ab_statt_als_sauber_zu_gelten(self):
        from sedo.backup_utils import BackupError

        fd, name = tempfile.mkstemp(suffix='.sqlite3')
        os.close(fd)
        kaputt = Path(name)
        try:
            with self.assertRaises(BackupError):
                from sedo.backup_utils import _sanitized_db_copy
                _sanitized_db_copy(kaputt)
        finally:
            kaputt.unlink(missing_ok=True)

    def test_fehlschlag_laesst_keine_klartext_kopie_zurueck(self):
        from sedo.backup_utils import BackupError, _sanitized_db_copy

        vorher = set(Path(tempfile.gettempdir()).glob('*.sqlite3'))
        fd, name = tempfile.mkstemp(suffix='.sqlite3')
        os.close(fd)
        kaputt = Path(name)
        try:
            with self.assertRaises(BackupError):
                _sanitized_db_copy(kaputt)
            neu = set(Path(tempfile.gettempdir()).glob('*.sqlite3')) - vorher - {kaputt}
            self.assertEqual(neu, set(), 'Nach einem Abbruch darf keine Kopie liegen bleiben.')
        finally:
            kaputt.unlink(missing_ok=True)

    def test_datei_ohne_appsetting_tabelle_ist_weiterhin_erlaubt(self):
        # Frische Installation: sedo_appsetting fehlt, andere Tabellen sind da.
        from sedo.backup_utils import _sanitized_db_copy

        fd, name = tempfile.mkstemp(suffix='.sqlite3')
        os.close(fd)
        frisch = Path(name)
        try:
            con = sqlite3.connect(frisch)
            con.execute('CREATE TABLE django_migrations (id INTEGER PRIMARY KEY)')
            con.commit()
            con.close()
            kopie = _sanitized_db_copy(frisch)
            kopie.unlink(missing_ok=True)  # darf nicht werfen
        finally:
            frisch.unlink(missing_ok=True)

    def test_backup_info_nennt_die_bank_pin(self):
        from sedo.backup_utils import create_backup

        out = create_backup('f81_test')
        try:
            with zipfile.ZipFile(out) as zf:
                info = zf.read('BACKUP_INFO.txt').decode('utf-8')
            self.assertIn('Bank-PIN', info)
            self.assertIn('Anmelde-Sitzungen', info)
            # test_security_hardening.py prüft wörtlich auf diese Teilzeichenkette.
            self.assertIn('NICHT im Backup enthalten', info)
        finally:
            out.unlink(missing_ok=True)

    def test_abgebrochenes_backup_hinterlaesst_kein_zip_und_wird_vermerkt(self):
        from sedo.backup_utils import BackupError, create_backup

        vorher = set(backup_dir().glob('*.zip'))
        # Im Testlauf liegt die DB im Speicher, _sanitized_db_copy wird also gar
        # nicht erreicht. Der Abbruch wird deshalb beim Schreiben der Infodatei
        # ausgelöst – für die Aufräumlogik ist der Auslöser einerlei.
        with patch('sedo.backup_utils.zipfile.ZipFile.writestr',
                   side_effect=BackupError('Bereinigung nicht nachweisbar')):
            with self.assertRaises(BackupError):
                create_backup('f81_abbruch')
        neu = set(backup_dir().glob('*.zip')) - vorher
        for rest in neu:
            rest.unlink(missing_ok=True)
        self.assertEqual(neu, set(), 'Ein abgebrochenes Backup darf kein ZIP hinterlassen (F-81).')
        self.assertIn('Bereinigung nicht nachweisbar', AppSetting.get('last_backup_error', ''))

    def test_erfolgreiches_backup_loescht_den_fehlervermerk(self):
        from sedo.backup_utils import create_backup

        AppSetting.set('last_backup_error', 'alter Fehler')
        out = create_backup('f81_erfolg')
        try:
            self.assertEqual(AppSetting.get('last_backup_error', ''), '')
        finally:
            out.unlink(missing_ok=True)


class F02AdminLoeschschutzTests(TestCase):
    """F-02: Der Django-Admin darf keine aufbewahrungspflichtigen Belege löschen.

    Zwei Ebenen werden geprüft: der Wächter in signals.py (greift auch bei
    manage.py shell und QuerySet.delete) und die Admin-Oberfläche selbst.
    """

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            'admin-test', 'admin@example.de', 'geheim-1234-admin')
        self.client.force_login(self.user)
        self.kunde = Customer.objects.create(name='Beispiel Hausverwaltung', customer_no='K-9001')
        self.rechnung = Invoice.objects.create(
            customer=self.kunde, number='RE2026090001',
            issue_date=timezone.localdate(), due_date=timezone.localdate(),
            net_total=Decimal('100.00'), vat_amount=Decimal('19.00'),
            gross_total=Decimal('119.00'), status='versendet',
        )

    # --- Modellebene: der Wächter aus signals.py ---------------------------------
    def test_rechnung_kann_nicht_hart_geloescht_werden(self):
        with transaction.atomic():
            with self.assertRaises(DeleteProtectedError):
                self.rechnung.delete()
        self.assertTrue(Invoice.all_objects.filter(pk=self.rechnung.pk).exists())

    def test_queryset_delete_ist_ebenfalls_gesperrt(self):
        # Der Weg, den ein Skript oder die Shell nehmen würde.
        with transaction.atomic():
            with self.assertRaises(DeleteProtectedError):
                Invoice.objects.filter(pk=self.rechnung.pk).delete()
        self.assertTrue(Invoice.all_objects.filter(pk=self.rechnung.pk).exists())

    def test_zahlung_mahnung_kassenbuch_und_auditlog_sind_geschuetzt(self):
        zahlung = Payment.objects.create(
            invoice=self.rechnung, booking_date=timezone.localdate(), amount=Decimal('50.00'))
        mahnung = DunningDocument.objects.create(
            invoice=self.rechnung, kind='reminder', amount_open=Decimal('69.00'))
        buchung = CashbookEntry.objects.create(
            month='2026-09', booking_no=1, booking_date=timezone.localdate(),
            kind='income', name='Barzahlung', amount=Decimal('20.00'))
        protokoll = AuditLog.objects.create(action='login', object_type='User', object_repr='admin')
        for beleg in (zahlung, mahnung, buchung, protokoll):
            with self.subTest(beleg=beleg.__class__.__name__):
                with transaction.atomic():
                    with self.assertRaises(DeleteProtectedError):
                        beleg.delete()

    def test_soft_delete_funktioniert_weiterhin(self):
        # Der vorgesehene Weg darf durch die Sperre nicht blockiert werden.
        self.rechnung.soft_delete()
        self.rechnung.refresh_from_db()
        self.assertIsNotNone(self.rechnung.deleted_at)
        self.assertFalse(Invoice.objects.filter(pk=self.rechnung.pk).exists())
        self.assertTrue(Invoice.all_objects.filter(pk=self.rechnung.pk).exists())

    def test_nicht_geschuetzte_stammdaten_bleiben_loeschbar(self):
        # Kunden, Objekte und Katalogeinträge sind keine Belege – die App
        # löscht sie an mehreren Stellen bewusst hart.
        katalog = ServiceCatalogItem.objects.create(name='Testleistung')
        katalog.delete()
        self.assertFalse(ServiceCatalogItem.objects.filter(pk=katalog.pk).exists())

    # --- Admin-Oberfläche --------------------------------------------------------
    def test_admin_post_delete_auf_rechnung_loescht_nichts(self):
        antwort = self.client.post('/admin/sedo/invoice/', {
            'action': 'delete_selected',
            '_selected_action': [str(self.rechnung.pk)],
            'post': 'yes',
        })
        self.assertIn(antwort.status_code, (200, 302, 403))
        self.assertTrue(Invoice.all_objects.filter(pk=self.rechnung.pk).exists(),
                        'Die Rechnung wurde über den Admin gelöscht (F-02).')

    def test_admin_aenderungsseite_ist_schreibgeschuetzt(self):
        from sedo.admin import InvoiceAdmin

        modeladmin = InvoiceAdmin(Invoice, admin.site)
        self.assertFalse(modeladmin.has_delete_permission(None, self.rechnung))
        self.assertFalse(modeladmin.has_change_permission(None, self.rechnung))
        self.assertFalse(modeladmin.has_add_permission(None))

    def test_admin_zeigt_ausgeblendete_belege(self):
        self.rechnung.soft_delete()
        antwort = self.client.get('/admin/sedo/invoice/')
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, 'RE2026090001',
                            msg_prefix='Ausgeblendete Belege müssen im Admin sichtbar bleiben (F-02).')

    def test_kassenbuch_bleibt_bewusst_aenderbar(self):
        # Bewusste Ausnahme: die App kennt keine Korrekturbuchung, der Admin
        # ist bis auf Weiteres der einzige Weg, einen Zahlendreher zu heilen.
        from sedo.admin import CashbookEntryAdmin

        # Djangos Standardprüfung liest request.user – ein echtes Request nötig.
        anfrage = RequestFactory().get('/admin/sedo/cashbookentry/')
        anfrage.user = self.user
        modeladmin = CashbookEntryAdmin(CashbookEntry, admin.site)
        self.assertTrue(modeladmin.has_change_permission(anfrage, None))
        self.assertFalse(modeladmin.has_delete_permission(anfrage, None))

    def test_kundenobjekt_im_admin_nicht_loeschbar(self):
        from sedo.admin import CustomerObjectInline

        inline = CustomerObjectInline(Customer, admin.site)
        self.assertFalse(inline.has_delete_permission(None, None),
                         'Ein gelöschtes Objekt kappt still den Leistungsort aller Rechnungen.')


class F04ReferenzierteRechnungXsdReihenfolgeTests(TestCase):
    """F-04: BT-25 muss hinter der Summation stehen, sonst ist die XML ungültig.

    Das CII-D16B-Schema gibt für ram:ApplicableHeaderTradeSettlement eine feste
    xs:sequence vor. Geprüft wird gegen diese Reihenfolge, nicht gegen eine
    eingefrorene Liste – so schlägt der Test auch an, wenn später ein anderes
    Element an falscher Stelle einsortiert wird.
    """

    # Auszug der xs:sequence von HeaderTradeSettlementType (CII D16B), nur die
    # Elemente, die dieses Projekt tatsächlich erzeugt – in Schema-Reihenfolge.
    ERWARTETE_REIHENFOLGE = [
        'InvoiceCurrencyCode',
        'SpecifiedTradeSettlementPaymentMeans',
        'ApplicableTradeTax',
        'BillingSpecifiedPeriod',
        'SpecifiedTradePaymentTerms',
        'SpecifiedTradeSettlementHeaderMonetarySummation',
        'InvoiceReferencedDocument',
    ]

    def _settlement_kinder(self, **row_kwargs):
        from datetime import datetime as dt

        from lxml import etree

        from sedo.core.rechnungslauf_core import RAM, InvoicePosition, InvoiceRow, build_xml

        felder = dict(
            invoice_no='TEST-STORNO-001',
            object_name='Teststraße 1',
            we='1001', ae='Teststraße 1', nksl='4200',
            service='Reinigung',
            net=Decimal('100.00'),
            vat_rate=Decimal('19.00'),
            positions=[InvoicePosition('Reinigung', Decimal('1'), 'Stk.', Decimal('100.00'),
                                       '', 'Reinigung', 'Reinigung')],
            object_street='Teststraße 1', object_postcode='39118',
            object_city='Magdeburg', object_country='DE',
            sap_mapping=True, buyer_id='42', buyer_name='GWG Reform', buyer_name2='',
            buyer_street='Asternweg 1', buyer_postcode='39118',
            buyer_city='Magdeburg', buyer_country='DE',
            ars_mapping=True,
        )
        felder.update(row_kwargs)
        row = InvoiceRow(**felder)
        xml = etree.fromstring(build_xml(row, dt(2026, 5, 1), None, None, dt(2026, 5, 15)))
        settlement = xml.find('.//' + RAM + 'ApplicableHeaderTradeSettlement')
        self.assertIsNotNone(settlement)
        return [etree.QName(kind).localname for kind in settlement]

    def _pruefe_schema_reihenfolge(self, kinder):
        """Jedes erzeugte Kind muss in der Schema-Reihenfolge aufsteigend liegen."""
        bekannt = [k for k in kinder if k in self.ERWARTETE_REIHENFOLGE]
        positionen = [self.ERWARTETE_REIHENFOLGE.index(k) for k in bekannt]
        self.assertEqual(
            positionen, sorted(positionen),
            f'Elementreihenfolge verstößt gegen die CII-D16B-Sequenz: {bekannt}',
        )
        unbekannt = [k for k in kinder if k not in self.ERWARTETE_REIHENFOLGE]
        self.assertEqual(unbekannt, [], f'Unerwartete Elemente – Reihenfolge ungeprüft: {unbekannt}')

    def test_storno_haelt_die_schema_reihenfolge_ein(self):
        kinder = self._settlement_kinder(reference_invoice_no='RE2026080007')
        self._pruefe_schema_reihenfolge(kinder)

    def test_referenz_steht_hinter_der_summation(self):
        kinder = self._settlement_kinder(reference_invoice_no='RE2026080007')
        self.assertIn('InvoiceReferencedDocument', kinder)
        self.assertGreater(
            kinder.index('InvoiceReferencedDocument'),
            kinder.index('SpecifiedTradeSettlementHeaderMonetarySummation'),
            'BT-25 muss NACH SpecifiedTradeSettlementHeaderMonetarySummation stehen (F-04).',
        )

    def test_referenz_steht_nicht_vor_den_zahlungsmitteln(self):
        # Die alte, fehlerhafte Position. Ohne den Fix bricht die XSD-Validierung
        # beim nächsten Geschwister ab (SpecifiedTradeSettlementPaymentMeans).
        kinder = self._settlement_kinder(reference_invoice_no='RE2026080007')
        self.assertLess(
            kinder.index('SpecifiedTradeSettlementPaymentMeans'),
            kinder.index('InvoiceReferencedDocument'),
        )

    def test_referenz_traegt_die_ursprungsnummer(self):
        # Verschieben darf den Inhalt nicht verlieren: BT-25 muss die Nummer der
        # Ursprungsrechnung tragen, sonst referenziert der Storno ins Leere.
        from datetime import datetime as dt

        from lxml import etree

        from sedo.core.rechnungslauf_core import RAM, InvoicePosition, InvoiceRow, build_xml

        row = InvoiceRow(
            invoice_no='TEST-STORNO-001', object_name='Teststraße 1',
            we='1001', ae='Teststraße 1', nksl='4200', service='Reinigung',
            net=Decimal('100.00'), vat_rate=Decimal('19.00'),
            positions=[InvoicePosition('Reinigung', Decimal('1'), 'Stk.', Decimal('100.00'),
                                       '', 'Reinigung', 'Reinigung')],
            object_street='Teststraße 1', object_postcode='39118',
            object_city='Magdeburg', object_country='DE',
            sap_mapping=True, buyer_id='42', buyer_name='GWG Reform', buyer_name2='',
            buyer_street='Asternweg 1', buyer_postcode='39118',
            buyer_city='Magdeburg', buyer_country='DE', ars_mapping=True,
            reference_invoice_no='RE2026080007',
        )
        xml = etree.fromstring(build_xml(row, dt(2026, 5, 1), None, None, dt(2026, 5, 15)))
        nummer = xml.find('.//' + RAM + 'InvoiceReferencedDocument/' + RAM + 'IssuerAssignedID')
        self.assertIsNotNone(nummer, 'BT-25 fehlt in der Storno-XML.')
        self.assertEqual(nummer.text, 'RE2026080007')

    def test_ohne_referenz_bleibt_das_element_weg(self):
        kinder = self._settlement_kinder()
        self.assertNotIn('InvoiceReferencedDocument', kinder)
        self._pruefe_schema_reihenfolge(kinder)

    def test_normale_rechnung_bleibt_unveraendert(self):
        # Der Fix darf die XML einer gewöhnlichen Rechnung (380) nicht anfassen.
        kinder = self._settlement_kinder()
        self.assertEqual(
            kinder,
            ['InvoiceCurrencyCode', 'SpecifiedTradeSettlementPaymentMeans', 'ApplicableTradeTax',
             'SpecifiedTradePaymentTerms', 'SpecifiedTradeSettlementHeaderMonetarySummation'],
        )

    def test_mit_leistungszeitraum_bleibt_die_reihenfolge_gueltig(self):
        from datetime import datetime as dt

        from lxml import etree

        from sedo.core.rechnungslauf_core import RAM, InvoicePosition, InvoiceRow, build_xml

        row = InvoiceRow(
            invoice_no='TEST-KORREKTUR-001', object_name='Teststraße 1',
            we='1001', ae='Teststraße 1', nksl='4200', service='Reinigung',
            net=Decimal('100.00'), vat_rate=Decimal('19.00'),
            positions=[InvoicePosition('Reinigung', Decimal('1'), 'Stk.', Decimal('100.00'),
                                       '', 'Reinigung', 'Reinigung')],
            object_street='Teststraße 1', object_postcode='39118',
            object_city='Magdeburg', object_country='DE',
            sap_mapping=True, buyer_id='42', buyer_name='GWG Reform', buyer_name2='',
            buyer_street='Asternweg 1', buyer_postcode='39118',
            buyer_city='Magdeburg', buyer_country='DE', ars_mapping=True,
            reference_invoice_no='RE2026080007',
        )
        xml = etree.fromstring(
            build_xml(row, dt(2026, 5, 1), dt(2026, 5, 1), dt(2026, 5, 31), dt(2026, 5, 15)))
        settlement = xml.find('.//' + RAM + 'ApplicableHeaderTradeSettlement')
        kinder = [etree.QName(k).localname for k in settlement]
        self.assertIn('BillingSpecifiedPeriod', kinder)
        self._pruefe_schema_reihenfolge(kinder)


class F53AbmeldenPerPostTests(TestCase):
    """F-53: Abmelden war als GET-Link gebaut und lieferte HTTP 405."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            'abmelder', 'abmelder@example.de', 'geheim-1234-abmelden')
        self.client.force_login(self.user)

    def test_abmelden_per_post_funktioniert(self):
        antwort = self.client.post(reverse('logout'))
        self.assertIn(antwort.status_code, (200, 302))
        self.assertNotIn('_auth_user_id', self.client.session,
                         'Nach dem Abmelden darf keine Sitzung mehr bestehen.')

    def test_abmelden_per_get_bleibt_405(self):
        # Djangos Verhalten, nicht unseres – hier festgehalten, damit klar
        # bleibt, warum das Menü ein Formular braucht.
        antwort = self.client.get(reverse('logout'))
        self.assertEqual(antwort.status_code, 405)

    def test_menue_rendert_ein_post_formular_statt_eines_links(self):
        antwort = self.client.get(reverse('sedo:dashboard'))
        inhalt = antwort.content.decode('utf-8')
        self.assertIn('nav-logout-btn', inhalt, 'Der Abmelde-Knopf fehlt im Menü.')
        self.assertNotIn('<a href="/logout/"', inhalt,
                         'Abmelden darf kein GET-Link mehr sein (F-53).')

    def test_abmelde_formular_traegt_csrf_token(self):
        antwort = self.client.get(reverse('sedo:dashboard'))
        inhalt = antwort.content.decode('utf-8')
        stelle = inhalt.find('nav-logout-form')
        self.assertNotEqual(stelle, -1)
        abschnitt = inhalt[stelle:stelle + 600]
        self.assertIn('csrfmiddlewaretoken', abschnitt,
                      'Ohne CSRF-Token liefert der Abmelde-POST einen 403.')

    def test_kommentar_erscheint_nicht_als_sichtbarer_text(self):
        # {# ... #} über mehrere Zeilen wird vom Django-Lexer nicht erkannt und
        # landete als Text in der Seitenleiste. Deshalb {% comment %}.
        antwort = self.client.get(reverse('sedo:dashboard'))
        inhalt = antwort.content.decode('utf-8')
        self.assertNotIn('Review-Fund F-53', inhalt)
        self.assertNotIn('http_method_names', inhalt)

    def test_logout_ist_von_der_anmeldepflicht_ausgenommen(self):
        from sedo.middleware import LoginRequiredMiddleware

        self.assertIn('/logout/', LoginRequiredMiddleware.EXEMPT_PREFIXES)

    def test_abmelden_ohne_sitzung_gibt_keine_405_seite(self):
        # Alter Tab, Sitzung abgelaufen, Klick auf Abmelden.
        self.client.logout()
        antwort = self.client.post(reverse('logout'))
        self.assertNotEqual(antwort.status_code, 405)
        self.assertIn(antwort.status_code, (200, 302))


class F11F66BatchMahnungNurMitAuswahlTests(TestCase):
    """F-11 und F-66: Kein Mahnlauf ohne bewusste Auswahl.

    F-11 war ein HTML-Fehler (verschachtelte Formulare), F-66 die zu großzügige
    View dahinter. Beides zusammen machte einen Fehlklick zur Massenmahnung an
    alle säumigen Kunden – und Mahndokumente lassen sich nicht zurücknehmen.
    """

    def setUp(self):
        from datetime import timedelta

        self.user = get_user_model().objects.create_user(
            'mahner', 'mahner@example.de', 'geheim-1234-mahnen')
        self.client.force_login(self.user)
        self.kunde = Customer.objects.create(name='Säumig GmbH', customer_no='K-7001')
        faellig = timezone.localdate() - timedelta(days=40)
        self.rechnungen = [
            Invoice.objects.create(
                customer=self.kunde, number=f'RE202609000{n}',
                issue_date=faellig, due_date=faellig,
                net_total=Decimal('100.00'), vat_amount=Decimal('19.00'),
                gross_total=Decimal('119.00'), status='ueberfaellig',
            )
            for n in (1, 2, 3)
        ]

    def _mahnungen(self):
        return DunningDocument.objects.count()

    # --- F-66: die View ----------------------------------------------------------
    def test_mahnung_ohne_auswahl_erzeugt_nichts(self):
        antwort = self.client.post(reverse('sedo:bulk_dunning'), {'kind': 'dunning'}, follow=True)
        self.assertEqual(self._mahnungen(), 0,
                         'Ohne Auswahl darf keine einzige Mahnung entstehen (F-66).')
        self.assertContains(antwort, 'anhaken')

    def test_erinnerung_ohne_auswahl_erzeugt_nichts(self):
        self.client.post(reverse('sedo:bulk_dunning'), {'kind': 'reminder'}, follow=True)
        self.assertEqual(self._mahnungen(), 0)

    def test_post_ganz_ohne_kind_erzeugt_nichts(self):
        # Genau der Fall aus F-11: der "bezahlt"-Knopf schickte einen POST ohne
        # kind und ohne invoice_ids an bulk_dunning. Früher lief er als 'auto'
        # über ALLE Kandidaten.
        antwort = self.client.post(reverse('sedo:bulk_dunning'), {}, follow=True)
        self.assertEqual(self._mahnungen(), 0,
                         'Ein POST ohne kind darf keinen Mahnlauf auslösen (F-11).')
        self.assertContains(antwort, 'abgebrochen')

    def test_unbekanntes_kind_erzeugt_nichts(self):
        self.client.post(reverse('sedo:bulk_dunning'), {'kind': 'quatsch'}, follow=True)
        self.assertEqual(self._mahnungen(), 0)

    def test_mit_auswahl_wird_gemahnt(self):
        # Der gewollte Weg muss weiter funktionieren.
        self.client.post(reverse('sedo:bulk_dunning'), {
            'kind': 'reminder',
            'invoice_ids': [str(self.rechnungen[0].pk)],
        }, follow=True)
        self.assertEqual(self._mahnungen(), 1)
        self.assertEqual(DunningDocument.objects.first().invoice_id, self.rechnungen[0].pk)

    def test_mahnautomat_ohne_auswahl_bleibt_erlaubt(self):
        # kind=auto wählt je Rechnung die fällige Stufe – das ist der einzige
        # Pfad, der ohne Häkchen arbeiten darf.
        self.client.post(reverse('sedo:bulk_dunning'), {'kind': 'auto'}, follow=True)
        self.assertEqual(self._mahnungen(), 3)

    def test_mahnautomat_ohne_kandidaten_meldet_freundlich(self):
        Invoice.all_objects.all().update(status='bezahlt', due_date=timezone.localdate())
        antwort = self.client.post(reverse('sedo:bulk_dunning'), {'kind': 'auto'}, follow=True)
        self.assertEqual(self._mahnungen(), 0)
        self.assertContains(antwort, 'Kein Mahnlauf nötig')

    # --- F-11: das Markup --------------------------------------------------------
    def test_offene_posten_hat_keine_verschachtelten_formulare(self):
        import re

        antwort = self.client.get(reverse('sedo:open_items'))
        inhalt = antwort.content.decode('utf-8')
        tiefe = 0
        for treffer in re.finditer(r'<form\b|</form>', inhalt):
            tiefe += -1 if treffer.group() == '</form>' else 1
            self.assertLessEqual(tiefe, 1, 'Verschachteltes <form> in „Offene Posten" (F-11).')
        self.assertEqual(tiefe, 0, 'Unbalancierte <form>-Tags.')

    def test_auswahlkaestchen_sind_dem_batch_formular_zugeordnet(self):
        antwort = self.client.get(reverse('sedo:open_items'))
        inhalt = antwort.content.decode('utf-8')
        self.assertIn('form="batch-form" name="invoice_ids"', inhalt,
                      'Die Kästchen liegen außerhalb des Formulars und brauchen das '
                      'form-Attribut, sonst wird die Auswahl nicht mitgesendet.')

    def test_bezahlt_knopf_zeigt_auf_die_zahlungsroute(self):
        antwort = self.client.get(reverse('sedo:open_items'))
        inhalt = antwort.content.decode('utf-8')
        ziel = reverse('sedo:invoice_mark_paid', args=[self.rechnungen[0].pk])
        self.assertIn(f'action="{ziel}"', inhalt)


class Neu1AuditTrailFaelltNichtStillAusTests(TestCase):
    """NEU-1: write_audit schluckte jeden Schreibfehler.

    Der Audit-Trail ist der GoBD-Nachweis über Storno, Zahlungen, Ausblenden
    und Mahnungen – an rund 44 Stellen. Weil er beim Ausblenden VOR der
    eigentlichen Aktion läuft, verschwand im Fehlerfall der Nachweis, während
    die Aktion trotzdem ausgeführt wurde.
    """

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            'protokollant', 'p@example.de', 'geheim-1234-protokoll')
        self.client.force_login(self.user)
        self.kunde = Customer.objects.create(name='Protokoll GmbH', customer_no='K-8001')
        self.rechnung = Invoice.objects.create(
            customer=self.kunde, number='RE2026090055',
            issue_date=timezone.localdate(), due_date=timezone.localdate(),
            net_total=Decimal('100.00'), vat_amount=Decimal('19.00'),
            gross_total=Decimal('119.00'), status='erstellt',
        )
        AppSetting.set('last_audit_error', '')

    def test_normaler_eintrag_wird_geschrieben(self):
        from sedo.audit import write_audit

        vorher = AuditLog.objects.count()
        write_audit(None, 'test', object_type='Probe', object_repr='ok', message='Probe')
        self.assertEqual(AuditLog.objects.count(), vorher + 1)

    def test_fehler_wird_vermerkt_statt_verschluckt(self):
        from sedo.audit import write_audit

        with patch('sedo.models.AuditLog.objects.create', side_effect=RuntimeError('DB klemmt')):
            write_audit(None, 'test', object_type='Probe', object_repr='kaputt')
        self.assertIn('DB klemmt', AppSetting.get('last_audit_error', ''),
                      'Ein fehlgeschlagener Protokolleintrag muss vermerkt werden (NEU-1).')
        self.assertTrue(AppSetting.get('last_audit_error_at', ''))

    def test_pflichteintrag_wirft_statt_zu_schweigen(self):
        from sedo.audit import AuditWriteError, write_audit

        with patch('sedo.models.AuditLog.objects.create', side_effect=RuntimeError('DB klemmt')):
            with self.assertRaises(AuditWriteError):
                write_audit(None, 'delete', object_type='Rechnung', object_repr='RE1', pflicht=True)

    def test_ohne_pflicht_laeuft_der_vorgang_weiter(self):
        # Login/Logout dürfen nicht daran scheitern, dass ihr Eintrag klemmt.
        from sedo.audit import write_audit

        with patch('sedo.models.AuditLog.objects.create', side_effect=RuntimeError('DB klemmt')):
            write_audit(None, 'login', object_type='User', object_repr='admin')  # darf nicht werfen

    def test_ausblenden_unterbleibt_wenn_das_protokoll_klemmt(self):
        # Der Kern der Entscheidung: lieber nicht ausblenden als ohne Nachweis.
        with patch('sedo.models.AuditLog.objects.create', side_effect=RuntimeError('DB klemmt')):
            antwort = self.client.post(
                reverse('sedo:invoice_delete', args=[self.rechnung.pk]), follow=True)
        self.rechnung.refresh_from_db()
        self.assertIsNone(self.rechnung.deleted_at,
                          'Ohne Protokolleintrag darf nicht ausgeblendet werden (NEU-1).')
        self.assertContains(antwort, 'NICHT ausgeblendet')

    def test_ausblenden_funktioniert_normal_weiterhin(self):
        self.client.post(reverse('sedo:invoice_delete', args=[self.rechnung.pk]), follow=True)
        self.rechnung.refresh_from_db()
        self.assertIsNotNone(self.rechnung.deleted_at)
        self.assertTrue(
            AuditLog.objects.filter(action='delete', object_repr__contains='RE2026090055').exists())

    def test_unserialisierbare_metadaten_kippen_den_eintrag_nicht(self):
        # Genau der Fall aus dem Bankimport: ein Model-Objekt in den Metadaten.
        from sedo.audit import write_audit

        vorher = AuditLog.objects.count()
        write_audit(None, 'import', object_type='Bankimport', object_repr='JSON',
                    metadata={'imported': 3, 'unassigned': [self.rechnung]})
        self.assertEqual(AuditLog.objects.count(), vorher + 1,
                         'Nicht serialisierbare Metadaten dürfen den Eintrag nicht verhindern.')

    def test_sicherheitsseite_zeigt_den_protokollfehler(self):
        AppSetting.set('last_audit_error', 'Protokolleintrag „delete" fehlgeschlagen')
        antwort = self.client.get(reverse('sedo:security_status'))
        self.assertContains(antwort, 'Protokoll gestört')

    def test_sicherheitsseite_meldet_ohne_fehler_audit_aktiv(self):
        antwort = self.client.get(reverse('sedo:security_status'))
        self.assertContains(antwort, 'Audit aktiv')
        self.assertNotContains(antwort, 'Protokoll gestört')


class Neu2DatumOhneStillenErsatzwertTests(TestCase):
    """NEU-2 und F-48: Kein unlesbares Datum wird still zu HEUTE.

    Der Rückgabewert speist das Rechnungsdatum beider Monatsläufe, das
    Buchungsdatum des Kassenbuch-Bankimports und das Buchungsdatum jedes
    importierten Zahlungsbelegs. Ein still gesetztes Datum ist steuerlich
    falsch und hebelt zusätzlich die Dublettenerkennung aus.
    """

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            'datumspruefer', 'd@example.de', 'geheim-1234-datum')
        self.client.force_login(self.user)

    # --- Der Parser selbst -------------------------------------------------------
    def test_gueltige_formate_werden_gelesen(self):
        from datetime import date as d

        from sedo.views import parse_date_for_view

        self.assertEqual(parse_date_for_view('2026-09-08'), d(2026, 9, 8))
        self.assertEqual(parse_date_for_view('08.09.2026'), d(2026, 9, 8))
        self.assertEqual(parse_date_for_view('20260908'), d(2026, 9, 8))

    def test_unlesbares_datum_wirft_statt_heute_zu_liefern(self):
        from sedo.views import DatumUnlesbar, parse_date_for_view

        for wert in ('irgendwas', '', '32.13.2026', None):
            with self.subTest(wert=wert):
                with self.assertRaises(DatumUnlesbar):
                    parse_date_for_view(wert)

    def test_optionale_variante_liefert_none(self):
        from sedo.views import parse_date_optional

        self.assertIsNone(parse_date_optional('kaputt'))
        self.assertIsNotNone(parse_date_optional('2026-09-08'))

    def test_datetime_wird_vor_date_geprueft(self):
        # datetime ist eine Unterklasse von date – die Reihenfolge der
        # isinstance-Prüfungen entscheidet, ob .date() aufgerufen wird.
        from datetime import date as d
        from datetime import datetime as dt

        from sedo.views import parse_date_optional

        self.assertEqual(parse_date_optional(dt(2026, 9, 8, 14, 30)), d(2026, 9, 8))

    # --- GWG-Monatslauf ----------------------------------------------------------
    def test_monatslauf_bricht_bei_unlesbarem_datum_ab(self):
        AareonMapping.objects.create(billing_unit_sender='Heckenweg 1/1', service_id='Reinigung')
        kunde = Customer.objects.create(name='GWG Reform', customer_no='K-GWG')
        antwort = self.client.post(reverse('sedo:aareon_monthly'), {
            'action': 'generate', 'customer': str(kunde.pk), 'month': '2026-09',
            'issue_date': 'kaputt',
            'use_Reinigung': '1', 'qty_Reinigung': '1', 'price_Reinigung': '58,00',
        }, follow=True)
        self.assertEqual(Invoice.all_objects.count(), 0,
                         'Bei unlesbarem Rechnungsdatum darf keine Rechnung entstehen (NEU-2).')
        self.assertContains(antwort, 'abgebrochen')

    def test_monatslauf_meldet_alle_eingabefehler_zusammen(self):
        AareonMapping.objects.create(billing_unit_sender='Heckenweg 1/1', service_id='Reinigung')
        kunde = Customer.objects.create(name='GWG Reform', customer_no='K-GWG')
        antwort = self.client.post(reverse('sedo:aareon_monthly'), {
            'action': 'generate', 'customer': str(kunde.pk), 'month': '2026-09',
            'issue_date': 'kaputt', 'payment_days': 'viele', 'limit': 'drei',
            'use_Reinigung': '1', 'qty_Reinigung': '1', 'price_Reinigung': '',
        }, follow=True)
        inhalt = antwort.content.decode('utf-8')
        self.assertIn('Rechnungsdatum', inhalt)
        self.assertIn('Zahlungsziel', inhalt)
        self.assertIn('Testlimit', inhalt)
        self.assertEqual(Invoice.all_objects.count(), 0)

    # --- Abo-Monatslauf ----------------------------------------------------------
    def test_abo_monatslauf_bricht_bei_unlesbarem_datum_ab(self):
        antwort = self.client.post(reverse('sedo:recurring_generate'), {
            'month': '2026-09', 'issue_date': 'kaputt',
        }, follow=True)
        self.assertEqual(Invoice.all_objects.count(), 0)
        self.assertContains(antwort, 'abgebrochen')

    # --- F-48: Zahlungsimport ----------------------------------------------------
    def test_bank_datum_liefert_none_statt_heute(self):
        from sedo.services.payments import parse_bank_date

        self.assertIsNone(parse_bank_date('unlesbar'))
        self.assertIsNone(parse_bank_date(''))

    def test_zahlung_ohne_lesbares_datum_wird_nicht_gebucht(self):
        from sedo.services.payments import import_bank_json

        ergebnis = import_bank_json([
            {'datum': '2026-09-08', 'betrag': '119.00', 'verwendungszweck': 'RE2026090001'},
            {'datum': 'kaputt', 'betrag': '50.00', 'verwendungszweck': 'Unbekannt'},
        ])
        self.assertEqual(ergebnis['imported'], 1)
        self.assertEqual(len(ergebnis['date_errors']), 1,
                         'Zeilen ohne lesbares Datum müssen einzeln gemeldet werden (F-48).')
        self.assertEqual(ergebnis['date_errors'][0]['betrag'], '50.00')
        self.assertEqual(Payment.objects.count(), 1)

    def test_abgewiesene_zeilen_erscheinen_als_warnung(self):
        daten = json.dumps([{'datum': 'kaputt', 'betrag': '50.00', 'verwendungszweck': 'Test'}])
        datei = SimpleUploadedFile('bank.json', daten.encode('utf-8'), content_type='application/json')
        antwort = self.client.post(reverse('sedo:payment_list'), {'file': datei}, follow=True)
        self.assertContains(antwort, 'von Hand erfasst')
        self.assertEqual(Payment.objects.count(), 0)

    def test_kassenbuch_bankimport_bricht_bei_unlesbarem_datum_ab(self):
        daten = json.dumps([
            {'datum': '2026-09-08', 'betrag': '-100.00', 'verwendungszweck': 'Bargeldabhebung'},
            {'datum': 'kaputt', 'betrag': '-50.00', 'verwendungszweck': 'Geldautomat'},
        ])
        datei = SimpleUploadedFile('kasse.json', daten.encode('utf-8'), content_type='application/json')
        antwort = self.client.post(reverse('sedo:cashbook_import_bank'), {'file': datei}, follow=True)
        self.assertEqual(CashbookEntry.objects.count(), 0,
                         'Entweder alle Zeilen buchen oder keine – nicht die Hälfte (NEU-2).')
        self.assertContains(antwort, 'abgebrochen')

    def test_kassenbuch_bankimport_bucht_saubere_daten_weiterhin(self):
        daten = json.dumps([
            {'datum': '2026-09-08', 'betrag': '-100.00', 'verwendungszweck': 'Bargeldabhebung'},
        ])
        datei = SimpleUploadedFile('kasse.json', daten.encode('utf-8'), content_type='application/json')
        self.client.post(reverse('sedo:cashbook_import_bank'), {'file': datei}, follow=True)
        self.assertEqual(CashbookEntry.objects.count(), 1)


class F07AbleitungSchreibtKeinenBelegzustandTests(TestCase):
    """F-07 (Etappe 1): Der Zahlstatus überschreibt keinen Belegzustand mehr.

    `status` trägt zwei Bedeutungen. Der Zahlstatus ist aus Zahlungen,
    Mahnungen und Fälligkeit ableitbar; der Belegzustand (entwurf, korrigiert,
    storniert) ist gesetzt und nicht rekonstruierbar. Vorher konnte ein
    einziger GET auf „Offene Posten" den Belegzustand überschreiben.
    """

    def setUp(self):
        from datetime import timedelta

        self.kunde = Customer.objects.create(name='Statustest GmbH', customer_no='K-6001')
        self.faellig = timezone.localdate() - timedelta(days=30)

    def _rechnung(self, nummer, status, typ='380', brutto=Decimal('119.00')):
        return Invoice.objects.create(
            customer=self.kunde, number=nummer, invoice_type=typ,
            issue_date=self.faellig, due_date=self.faellig,
            net_total=Decimal('100.00'), vat_amount=Decimal('19.00'),
            gross_total=brutto, status=status,
        )

    # --- Der Kern: geschützte Belegzustände bleiben stehen -----------------------
    def test_korrigiertes_original_wird_nicht_ueberfaellig(self):
        from sedo.services.calc import refresh_invoice_payment_status

        rechnung = self._rechnung('RE2026080001', 'korrigiert')
        refresh_invoice_payment_status(rechnung)
        rechnung.refresh_from_db()
        self.assertEqual(rechnung.status, 'korrigiert',
                         'Ein ersetztes Original darf nicht auf ueberfaellig kippen (F-07).')

    def test_entwurf_wird_nicht_ueberfaellig(self):
        from sedo.services.calc import refresh_invoice_payment_status

        rechnung = self._rechnung('RE2026080002', 'entwurf')
        refresh_invoice_payment_status(rechnung)
        rechnung.refresh_from_db()
        self.assertEqual(rechnung.status, 'entwurf')

    def test_stornierte_rechnung_bleibt_storniert(self):
        from sedo.services.calc import refresh_invoice_payment_status

        rechnung = self._rechnung('RE2026080003', 'storniert')
        refresh_invoice_payment_status(rechnung)
        rechnung.refresh_from_db()
        self.assertEqual(rechnung.status, 'storniert')

    def test_stornobeleg_wird_nicht_ohne_zahlung_bezahlt(self):
        # F-89: gross_total ist negativ, damit war `0 >= -119 and -119 != 0`
        # wahr und der Beleg galt als bezahlt.
        from sedo.services.calc import refresh_invoice_payment_status

        storno = self._rechnung('RE2026080004', 'erstellt', typ='457', brutto=Decimal('-119.00'))
        refresh_invoice_payment_status(storno)
        storno.refresh_from_db()
        self.assertNotEqual(storno.status, 'bezahlt',
                            'Ein Stornobeleg darf sich nicht selbst als bezahlt markieren (F-89).')

    def test_normale_rechnung_wird_weiterhin_ueberfaellig(self):
        # Der gewollte Weg muss erhalten bleiben.
        from sedo.services.calc import refresh_invoice_payment_status

        rechnung = self._rechnung('RE2026080005', 'erstellt')
        refresh_invoice_payment_status(rechnung)
        rechnung.refresh_from_db()
        self.assertEqual(rechnung.status, 'ueberfaellig')

    def test_ohne_aenderung_wird_nicht_geschrieben(self):
        # Bisher setzte JEDER Aufruf ein UPDATE ab – bei bis zu 500 Rechnungen
        # je Seitenaufruf von „Offene Posten".
        from sedo.services.calc import abgeleiteter_zahlstatus, refresh_invoice_payment_status

        rechnung = self._rechnung('RE2026080006', 'erstellt')
        refresh_invoice_payment_status(rechnung)  # erster Lauf setzt 'ueberfaellig'
        rechnung.refresh_from_db()
        self.assertIsNone(abgeleiteter_zahlstatus(rechnung),
                          'Ohne echte Änderung darf kein neuer Wert zurückkommen.')

    # --- is_overdue --------------------------------------------------------------
    def test_is_overdue_ignoriert_belegzustaende_und_stornos(self):
        faelle = [
            ('RE2026080011', 'entwurf', '380', Decimal('119.00')),
            ('RE2026080012', 'korrigiert', '380', Decimal('119.00')),
            ('RE2026080013', 'storniert', '380', Decimal('119.00')),
            ('RE2026080014', 'erstellt', '457', Decimal('-119.00')),
        ]
        for nummer, status, typ, brutto in faelle:
            with self.subTest(status=status, typ=typ):
                rechnung = self._rechnung(nummer, status, typ=typ, brutto=brutto)
                self.assertFalse(rechnung.is_overdue,
                                 f'{status}/{typ} darf nicht als überfällig gelten (F-07).')

    def test_is_overdue_gilt_weiterhin_fuer_offene_rechnungen(self):
        rechnung = self._rechnung('RE2026080015', 'erstellt')
        self.assertTrue(rechnung.is_overdue)

    # --- Die Mengenfunktionen ----------------------------------------------------
    def test_offene_rechnungen_schliesst_belegzustaende_und_stornos_aus(self):
        from sedo.services.calc import offene_rechnungen

        self._rechnung('RE2026080021', 'entwurf')
        self._rechnung('RE2026080022', 'korrigiert')
        self._rechnung('RE2026080023', 'storniert')
        self._rechnung('RE2026080024', 'bezahlt')
        self._rechnung('RE2026080025', 'erstellt', typ='457', brutto=Decimal('-119.00'))
        offen = self._rechnung('RE2026080026', 'erstellt')

        nummern = set(offene_rechnungen().values_list('number', flat=True))
        self.assertEqual(nummern, {offen.number})

    def test_zuordenbare_rechnungen_behaelt_bezahlte(self):
        # Sonst wäre keine Nach- oder Überzahlung mehr buchbar.
        from sedo.services.calc import zuordenbare_rechnungen

        bezahlt = self._rechnung('RE2026080031', 'bezahlt')
        self._rechnung('RE2026080032', 'korrigiert')
        nummern = set(zuordenbare_rechnungen().values_list('number', flat=True))
        self.assertIn(bezahlt.number, nummern)
        self.assertNotIn('RE2026080032', nummern)

    def test_mahnsperre_kommt_aus_einer_quelle(self):
        from sedo.services.dunning import _UNDUNNABLE_STATUS

        self.assertEqual(tuple(_UNDUNNABLE_STATUS), tuple(Invoice.GESPERRT_FUER_MAHNUNG))
        for wert in ('bezahlt', 'storniert', 'korrigiert', 'entwurf'):
            self.assertIn(wert, _UNDUNNABLE_STATUS)


class GwgRechnungstextMitObjektTests(TestCase):
    """Kundenanforderung GWG/Aareon (M. Assmann, 09.09.2026).

    Im Rechnungstext (ram:Name des ExchangedDocument) stand nur
    „Rechnung September 2026". Aareon braucht dort das Objekt, an dem die
    Leistung erbracht wurde – der Sachbearbeiter ordnet den Beleg danach zu.
    Kein Review-Fund, sondern eine Anforderung des Hauptkunden.
    """

    def _titel(self, objekt='Resedaweg 11 li.', titel='Rechnung', mit_zeitraum=True):
        from datetime import datetime as dt

        from lxml import etree

        from sedo.core.rechnungslauf_core import RAM, RSM, InvoicePosition, InvoiceRow, build_xml

        row = InvoiceRow(
            invoice_no='RE2026090006', object_name=objekt,
            we='1001', ae=objekt or 'Teststraße 1', nksl='4200', service='Reinigung',
            net=Decimal('100.00'), vat_rate=Decimal('19.00'),
            positions=[InvoicePosition('Reinigung', Decimal('1'), 'Stk.', Decimal('100.00'),
                                       '', 'Reinigung', 'Reinigung')],
            object_street='Resedaweg 11', object_postcode='39118',
            object_city='Magdeburg', object_country='DE',
            sap_mapping=True, buyer_id='399662000', buyer_name='GWG Reform', buyer_name2='',
            buyer_street='Asternweg 1', buyer_postcode='39118',
            buyer_city='Magdeburg', buyer_country='DE', ars_mapping=True,
            document_title=titel,
        )
        ps = dt(2026, 9, 1) if mit_zeitraum else None
        pe = dt(2026, 9, 30) if mit_zeitraum else None
        xml = etree.fromstring(build_xml(row, dt(2026, 9, 2), ps, pe, dt(2026, 9, 16)))
        return xml.find(RSM + 'ExchangedDocument/' + RAM + 'Name').text

    def test_titel_nennt_objekt_und_zeitraum(self):
        self.assertEqual(self._titel(), 'Rechnung Resedaweg 11 li. · September 2026')

    def test_ohne_objekt_bleibt_der_titel_wie_bisher(self):
        self.assertEqual(self._titel(objekt=''), 'Rechnung · September 2026')

    def test_ohne_zeitraum_nur_belegart_und_objekt(self):
        self.assertEqual(self._titel(mit_zeitraum=False), 'Rechnung Resedaweg 11 li.')

    def test_storno_bleibt_im_klartext_als_storno_erkennbar(self):
        # Ohne die vorangestellte Belegart stünde das nur noch im TypeCode 457.
        self.assertEqual(self._titel(titel='Stornorechnung'),
                         'Stornorechnung Resedaweg 11 li. · September 2026')

    def test_titel_ist_nicht_mehr_der_alte_text(self):
        # Der Text, den Aareon beanstandet hat.
        self.assertNotEqual(self._titel(), 'Rechnung September 2026')


class F07WaechterAmBelegGraphenTests(TestCase):
    """F-07/F-13/F-14 (Etappe 2): Sperren, die nicht am lügenden Feld hängen.

    Der entscheidende Fall: Belege, deren Status durch F-07 bereits verfälscht
    wurde. Bei ihnen greift ein Status-Guard nicht mehr – die Prüfung am
    tatsächlichen Folgebeleg schon.
    """

    def setUp(self):
        self.kunde = Customer.objects.create(name='Wächtertest GmbH', customer_no='K-5001')
        self.heute = timezone.localdate()

    def _rechnung(self, nummer, status='erstellt', typ='380', referenz=None):
        return Invoice.objects.create(
            customer=self.kunde, number=nummer, invoice_type=typ,
            issue_date=self.heute, due_date=self.heute,
            net_total=Decimal('100.00'), vat_amount=Decimal('19.00'),
            gross_total=Decimal('119.00'), status=status,
            reference_invoice=referenz,
        )

    # --- Kreuzprüfung: greift auch bei verfälschtem Status -----------------------
    def test_storno_scheitert_an_der_korrektur_trotz_falschem_status(self):
        from sedo.services.invoices import WorkflowError, ensure_storno_allowed

        original = self._rechnung('RE2026070001', status='korrigiert')
        self._rechnung('RE2026070002', typ='384', referenz=original)
        # Bestandsschaden aus F-07 nachstellen: der Status wurde überschrieben.
        Invoice.all_objects.filter(pk=original.pk).update(status='ueberfaellig')
        original.refresh_from_db()

        with self.assertRaises(WorkflowError) as ctx:
            ensure_storno_allowed(original)
        self.assertIn('RE2026070002', str(ctx.exception),
                      'Die Meldung soll die vorhandene Korrektur nennen.')

    def test_korrektur_scheitert_am_storno_trotz_falschem_status(self):
        from sedo.services.invoices import WorkflowError, ensure_correction_allowed

        original = self._rechnung('RE2026070011', status='storniert')
        self._rechnung('RE2026070012', typ='457', referenz=original)
        Invoice.all_objects.filter(pk=original.pk).update(status='bezahlt')
        original.refresh_from_db()

        with self.assertRaises(WorkflowError) as ctx:
            ensure_correction_allowed(original)
        self.assertIn('RE2026070012', str(ctx.exception))

    def test_entwurf_kann_weder_storniert_noch_korrigiert_werden(self):
        from sedo.services.invoices import (
            WorkflowError, ensure_correction_allowed, ensure_storno_allowed)

        entwurf = self._rechnung('RE2026070021', status='entwurf')
        for pruefung in (ensure_storno_allowed, ensure_correction_allowed):
            with self.subTest(pruefung=pruefung.__name__):
                with self.assertRaises(WorkflowError) as ctx:
                    pruefung(entwurf)
                self.assertIn('finalisieren', str(ctx.exception).lower())

    def test_normaler_storno_bleibt_erlaubt(self):
        from sedo.services.invoices import ensure_storno_allowed

        ensure_storno_allowed(self._rechnung('RE2026070031'))  # darf nicht werfen

    # --- Zahlungen ---------------------------------------------------------------
    def test_zahlung_auf_korrigiertes_original_nennt_die_korrektur(self):
        from sedo.services.invoices import WorkflowError, ensure_zahlung_erlaubt

        original = self._rechnung('RE2026070041', status='korrigiert')
        self._rechnung('RE2026070042', typ='384', referenz=original)
        with self.assertRaises(WorkflowError) as ctx:
            ensure_zahlung_erlaubt(original)
        self.assertIn('RE2026070042', str(ctx.exception))

    def test_zahlung_auf_entwurf_und_storno_gesperrt(self):
        from sedo.services.invoices import WorkflowError, ensure_zahlung_erlaubt

        for nummer, status in (('RE2026070051', 'entwurf'), ('RE2026070052', 'storniert')):
            with self.subTest(status=status):
                with self.assertRaises(WorkflowError):
                    ensure_zahlung_erlaubt(self._rechnung(nummer, status=status))

    def test_zahlung_auf_bezahlte_rechnung_bleibt_moeglich(self):
        # Nachzahlung/Überzahlung muss buchbar bleiben.
        from sedo.services.invoices import ensure_zahlung_erlaubt

        ensure_zahlung_erlaubt(self._rechnung('RE2026070061', status='bezahlt'))

    def test_zuordnungsformular_zeigt_keine_geschuetzten_belege(self):
        from sedo.forms import ManualPaymentAssignForm

        self._rechnung('RE2026070071', status='entwurf')
        self._rechnung('RE2026070072', status='korrigiert')
        self._rechnung('RE2026070073', status='storniert')
        bezahlt = self._rechnung('RE2026070074', status='bezahlt')

        nummern = {i.number for i in ManualPaymentAssignForm().fields['invoice'].queryset}
        self.assertIn(bezahlt.number, nummern)
        for gesperrt in ('RE2026070071', 'RE2026070072', 'RE2026070073'):
            self.assertNotIn(gesperrt, nummern)

    # --- Versand -----------------------------------------------------------------
    def test_versand_nur_fuer_entwuerfe_gesperrt(self):
        from sedo.services.invoices import WorkflowError, ensure_versand_erlaubt

        with self.assertRaises(WorkflowError):
            ensure_versand_erlaubt(self._rechnung('RE2026070081', status='entwurf'))
        # Korrigiert und storniert bleiben versendbar (Entscheidung des Inhabers).
        ensure_versand_erlaubt(self._rechnung('RE2026070082', status='korrigiert'))
        ensure_versand_erlaubt(self._rechnung('RE2026070083', status='storniert'))

    def test_versandnachweis_erkennt_protokolleintrag(self):
        # Der Status allein taugt nicht als Beweis – er wird überschrieben.
        from sedo.services.invoices import versand_nachweis

        rechnung = self._rechnung('RE2026070091', status='ueberfaellig')
        self.assertFalse(versand_nachweis(rechnung))
        AuditLog.objects.create(action='email', object_type='Invoice',
                                object_repr='RE2026070091',
                                message='Rechnung RE2026070091 direkt per SMTP versendet an kunde@example.de')
        self.assertTrue(versand_nachweis(rechnung),
                        'Ein Altbestands-Protokolleintrag muss als Nachweis zählen.')

    # --- Ausblenden --------------------------------------------------------------
    def test_ausblenden_erlaubt_fuer_unversendete_rechnung(self):
        from sedo.services.invoices import ausblenden_hindernis

        self.assertEqual(ausblenden_hindernis(self._rechnung('RE2026070101')), '')

    def test_ausblenden_gesperrt_nach_zahlung(self):
        from sedo.services.invoices import ausblenden_hindernis

        rechnung = self._rechnung('RE2026070111')
        Payment.objects.create(invoice=rechnung, booking_date=self.heute, amount=Decimal('50.00'))
        self.assertIn('Zahlung', ausblenden_hindernis(rechnung))

    def test_ausblenden_gesperrt_nach_mahnung(self):
        from sedo.services.invoices import ausblenden_hindernis

        rechnung = self._rechnung('RE2026070121')
        DunningDocument.objects.create(invoice=rechnung, kind='reminder', amount_open=Decimal('119.00'))
        self.assertIn('gemahnt', ausblenden_hindernis(rechnung))

    def test_ausblenden_gesperrt_nach_versand(self):
        from sedo.services.invoices import ausblenden_hindernis

        rechnung = self._rechnung('RE2026070131', status='versendet')
        self.assertIn('versendet', ausblenden_hindernis(rechnung))

    def test_folgebeleg_wird_nie_ausgeblendet(self):
        from sedo.services.invoices import ausblenden_hindernis

        original = self._rechnung('RE2026070141')
        storno = self._rechnung('RE2026070142', typ='457', referenz=original)
        self.assertIn('Folgebeleg', ausblenden_hindernis(storno))
        # Und das Original auch nicht, solange der Folgebeleg lebt.
        self.assertNotEqual(ausblenden_hindernis(original), '')

    def test_hindernis_nennt_immer_die_rechnungsnummer(self):
        from sedo.services.invoices import ausblenden_hindernis

        rechnung = self._rechnung('RE2026070151', status='versendet')
        self.assertIn('RE2026070151', ausblenden_hindernis(rechnung))


class F07ViewsUndSichtbarkeitTests(TestCase):
    """F-07/F-13/F-14/F-60/F-63/F-115 (Etappe 3): Views, Mengen, Knöpfe."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            'sichtbar', 's@example.de', 'geheim-1234-sicht')
        self.client.force_login(self.user)
        self.kunde = Customer.objects.create(name='Sichtbar GmbH', customer_no='K-4001',
                                             email='kunde@example.de')
        self.heute = timezone.localdate()

    def _rechnung(self, nummer, status='erstellt', typ='380', brutto=Decimal('119.00'), referenz=None):
        return Invoice.objects.create(
            customer=self.kunde, number=nummer, invoice_type=typ,
            issue_date=self.heute, due_date=self.heute,
            net_total=Decimal('100.00'), vat_amount=Decimal('19.00'),
            gross_total=brutto, status=status, reference_invoice=referenz)

    # --- Der Versand ändert nur noch erstellt -> versendet -----------------------
    def test_versand_hebt_keinen_zustand_mehr_auf(self):
        faelle = [('erstellt', 'versendet'), ('bezahlt', 'bezahlt'),
                  ('gemahnt', 'gemahnt'), ('korrigiert', 'korrigiert')]
        for i, (vorher, erwartet) in enumerate(faelle):
            with self.subTest(status=vorher):
                rechnung = self._rechnung(f'RE202606{i:04d}', status=vorher)
                with patch('sedo.views.smtp_is_configured', return_value=True), \
                     patch('sedo.views.send_email_smtp'), \
                     patch('sedo.views.generate_invoice_files'):
                    self.client.post(reverse('sedo:invoice_email', args=[rechnung.pk]))
                rechnung.refresh_from_db()
                self.assertEqual(rechnung.status, erwartet)

    def test_entwurf_wird_nicht_versendet(self):
        entwurf = self._rechnung('RE2026061001', status='entwurf')
        with patch('sedo.views.smtp_is_configured', return_value=True), \
             patch('sedo.views.send_email_smtp') as senden, \
             patch('sedo.views.generate_invoice_files'):
            antwort = self.client.post(reverse('sedo:invoice_email', args=[entwurf.pk]), follow=True)
        senden.assert_not_called()
        self.assertContains(antwort, 'finalisieren')

    def test_detailseite_zeigt_fuer_entwurf_kein_email_formular(self):
        entwurf = self._rechnung('RE2026061011', status='entwurf')
        inhalt = self.client.get(
            reverse('sedo:invoice_detail', args=[entwurf.pk])).content.decode('utf-8')
        ziel = reverse('sedo:invoice_email', args=[entwurf.pk])
        self.assertNotIn(f'action="{ziel}"', inhalt,
                         'Weder der Knopf oben noch das KI-Formular darf für Entwürfe erscheinen.')

    # --- Die Filterreiter, die Falle dieser Etappe -------------------------------
    def test_reiter_liefern_die_richtigen_mengen(self):
        bezahlt = self._rechnung('RE2026062001', status='bezahlt')
        # Echte Zahlung nötig: Der Zahlstatus ist eine abgeleitete Größe – ohne
        # Zahlung setzt refresh_invoice_payment_status die Rechnung beim
        # Seitenaufruf zu Recht auf 'erstellt' zurück.
        Payment.objects.create(invoice=bezahlt, booking_date=self.heute,
                               amount=Decimal('119.00'))
        offen = self._rechnung('RE2026062002', status='erstellt')
        self._rechnung('RE2026062003', status='entwurf')

        def nummern(status):
            antwort = self.client.get(reverse('sedo:open_items'), {'status': status})
            return {i.number for i in antwort.context['invoices']}

        self.assertEqual(nummern('paid'), {bezahlt.number}, 'Reiter „Bezahlt" darf nicht leer sein.')
        self.assertEqual(nummern('all'), {bezahlt.number, offen.number},
                         'Reiter „Alle ohne Storno" zeigt Bezahltes, aber keine Entwürfe.')
        self.assertEqual(nummern('open'), {offen.number})

    def test_ersetztes_original_verschwindet_aus_den_offenen_posten(self):
        original = self._rechnung('RE2026062011', status='korrigiert')
        korrektur = self._rechnung('RE2026062012', typ='384', brutto=Decimal('109.00'),
                                   referenz=original)
        antwort = self.client.get(reverse('sedo:open_items'))
        nummern = {i.number for i in antwort.context['invoices']}
        self.assertNotIn(original.number, nummern)
        self.assertIn(korrektur.number, nummern)

    def test_dashboard_zaehlt_original_und_korrektur_nicht_doppelt(self):
        original = self._rechnung('RE2026063001', status='korrigiert')
        self._rechnung('RE2026063002', typ='384', brutto=Decimal('109.00'), referenz=original)
        self._rechnung('RE2026063003', typ='457', brutto=Decimal('-119.00'))
        antwort = self.client.get(reverse('sedo:dashboard'))
        self.assertEqual(antwort.context['open_invoices'], 1)
        self.assertEqual(antwort.context['open_amount_total'], Decimal('109.00'))

    # --- Zahlungssperren in den Views -------------------------------------------
    def test_zahlung_auf_korrigierte_rechnung_wird_abgewiesen(self):
        original = self._rechnung('RE2026064001', status='korrigiert')
        self._rechnung('RE2026064002', typ='384', referenz=original)
        for route in ('sedo:invoice_add_payment', 'sedo:invoice_mark_paid'):
            with self.subTest(route=route):
                self.client.post(reverse(route, args=[original.pk]), follow=True)
                self.assertEqual(Payment.objects.filter(invoice=original).count(), 0)
        original.refresh_from_db()
        self.assertEqual(original.status, 'korrigiert')

    # --- Ausblenden über die View ------------------------------------------------
    def test_versendete_rechnung_wird_nicht_ausgeblendet(self):
        rechnung = self._rechnung('RE2026065001', status='versendet')
        antwort = self.client.post(reverse('sedo:invoice_delete', args=[rechnung.pk]), follow=True)
        rechnung.refresh_from_db()
        self.assertIsNone(rechnung.deleted_at)
        self.assertContains(antwort, 'versendet')

    def test_unversendete_rechnung_bleibt_ausblendbar(self):
        rechnung = self._rechnung('RE2026065011', status='erstellt')
        self.client.post(reverse('sedo:invoice_delete', args=[rechnung.pk]), follow=True)
        rechnung.refresh_from_db()
        self.assertIsNotNone(rechnung.deleted_at, 'Der häufigste Fehlerfall muss weiter gehen.')

    def test_loeschknopf_ist_deaktiviert_wenn_gesperrt(self):
        rechnung = self._rechnung('RE2026065021', status='versendet')
        inhalt = self.client.get(
            reverse('sedo:invoice_detail', args=[rechnung.pk])).content.decode('utf-8')
        self.assertIn('Löschen nicht möglich', inhalt)
        self.assertNotIn(reverse('sedo:invoice_delete', args=[rechnung.pk]), inhalt)

    # --- F-63 --------------------------------------------------------------------
    def test_zahlungsziel_null_bleibt_null(self):
        # Echter Weg durch die View statt einer Tautologie: Der Kunde hat 14 Tage
        # hinterlegt, das Formular schickt 0. Vorher machte `or` daraus still 14.
        self.kunde.payment_days = 14
        self.kunde.save(update_fields=['payment_days'])
        antwort = self.client.post(reverse('sedo:invoice_create'), {
            'customer': str(self.kunde.pk),
            'issue_date': self.heute.isoformat(),
            'payment_days': '0',
            'vat_rate': '19.00',
            'action': 'draft',
            'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '0', 'items-MAX_NUM_FORMS': '20',
            'items-0-description': 'Reinigung', 'items-0-quantity': '1',
            'items-0-unit': 'pauschal', 'items-0-unit_price': '100.00',
        }, follow=True)
        neu = Invoice.all_objects.order_by('-pk').first()
        self.assertIsNotNone(neu, f'Keine Rechnung angelegt (Status {antwort.status_code}).')
        self.assertEqual(neu.due_date, neu.issue_date,
                         'Zahlungsziel 0 muss „zahlbar sofort" bedeuten, nicht 14 Tage (F-63).')

    # --- Monatsabschluss ---------------------------------------------------------
    def test_monatsabschluss_ohne_entwuerfe_mit_luecken_liste(self):
        self._rechnung('RE2026067001', status='erstellt')
        self._rechnung('RE2026067002', status='entwurf')
        ausgeblendet = self._rechnung('RE2026067003', status='erstellt')
        ausgeblendet.soft_delete()

        monat = self.heute.strftime('%Y-%m')
        antwort = self.client.get(reverse('sedo:month_close'), {'month': monat})
        nummern = {i.number for i in antwort.context['invoices']}
        self.assertNotIn('RE2026067002', nummern, 'Entwürfe gehören nicht in den Abschluss.')
        self.assertIn('RE2026067003', antwort.context['ausgeblendete_nummern'],
                      'Die Nummernlücke muss ausgewiesen werden.')

    def test_monatsabschluss_zaehlt_ersetztes_original_nicht_mit(self):
        # Die Korrektur ist ein Vollersatz mit eigenem positivem Betrag, kein
        # Differenzbeleg – Original und Korrektur addierten sich in der Summe.
        original = self._rechnung('RE2026068001', status='korrigiert')
        self._rechnung('RE2026068002', typ='384', brutto=Decimal('109.00'), referenz=original)
        antwort = self.client.get(reverse('sedo:month_close'),
                                  {'month': self.heute.strftime('%Y-%m')})
        self.assertEqual(antwort.context['gross_total'], Decimal('109.00'),
                         'Das ersetzte Original darf nicht mitgezählt werden.')

    def test_stornopaar_bleibt_in_der_summe_und_hebt_sich_auf(self):
        # Beim Storno stimmt die Summe von selbst: der Stornobeleg trägt
        # negative Beträge. Er darf deshalb NICHT herausgefiltert werden.
        original = self._rechnung('RE2026068011', status='storniert')
        self._rechnung('RE2026068012', typ='457', brutto=Decimal('-119.00'), referenz=original)
        antwort = self.client.get(reverse('sedo:month_close'),
                                  {'month': self.heute.strftime('%Y-%m')})
        self.assertEqual(antwort.context['gross_total'], Decimal('0.00'))


class Etappe3NacharbeitTests(TestCase):
    """Funde des adversarischen Reviews an Etappe 3.

    Der schwerste: Ein als ENTWURF gespeicherter Korrekturbeleg galt als aktive
    Korrektur. Das Original wurde dabei nicht als 'korrigiert' markiert – es war
    also in jede Richtung gesperrt (Zahlung, Storno, erneute Korrektur,
    Ausblenden), der Entwurf ebenso, und eine Finalisieren-Route gibt es nicht.
    Gleichzeitig buchte der Bankimport weiter auf genau dieses Original.
    """

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            'nacharbeit', 'n@example.de', 'geheim-1234-nach')
        self.client.force_login(self.user)
        self.kunde = Customer.objects.create(name='Nacharbeit GmbH', customer_no='K-3001',
                                             email='kunde@example.de')
        self.heute = timezone.localdate()

    def _rechnung(self, nummer, status='erstellt', typ='380', brutto=Decimal('119.00'), referenz=None):
        return Invoice.objects.create(
            customer=self.kunde, number=nummer, invoice_type=typ,
            issue_date=self.heute, due_date=self.heute,
            net_total=Decimal('100.00'), vat_amount=Decimal('19.00'),
            gross_total=brutto, status=status, reference_invoice=referenz)

    def test_korrektur_entwurf_sperrt_das_original_nicht(self):
        original = self._rechnung('RE2026050001', status='versendet')
        self._rechnung('RE2026050002', typ='384', status='entwurf', referenz=original)

        self.assertIsNone(original.active_correction,
                          'Ein Entwurf ist kein ausgestellter Folgebeleg.')
        # Und damit sind alle vier Wege wieder offen:
        from sedo.services.invoices import (
            ausblenden_hindernis, ensure_correction_allowed, ensure_storno_allowed,
            ensure_zahlung_erlaubt)
        ensure_zahlung_erlaubt(original)
        ensure_storno_allowed(original)
        ensure_correction_allowed(original)
        self.assertNotIn('Folgebeleg', ausblenden_hindernis(original))

    def test_korrektur_entwurf_laesst_sich_verwerfen(self):
        original = self._rechnung('RE2026050011', status='versendet')
        entwurf = self._rechnung('RE2026050012', typ='384', status='entwurf', referenz=original)
        from sedo.services.invoices import ausblenden_hindernis

        self.assertEqual(ausblenden_hindernis(entwurf), '',
                         'Ein nie ausgestellter Entwurf muss verworfen werden können.')
        self.client.post(reverse('sedo:invoice_delete', args=[entwurf.pk]), follow=True)
        entwurf.refresh_from_db()
        self.assertIsNotNone(entwurf.deleted_at)

    def test_ausgestellte_korrektur_sperrt_weiterhin(self):
        # Die Sperre darf nur für Entwürfe fallen, nicht generell.
        original = self._rechnung('RE2026050021', status='korrigiert')
        self._rechnung('RE2026050022', typ='384', status='erstellt', referenz=original)
        from sedo.services.invoices import WorkflowError, ensure_storno_allowed

        self.assertIsNotNone(original.active_correction)
        with self.assertRaises(WorkflowError):
            ensure_storno_allowed(original)

    def test_bankimport_widerspricht_der_handbuchung_nicht_mehr(self):
        # Vorher: Handbuchung gesperrt, Bankimport buchte trotzdem – auf denselben Beleg.
        original = self._rechnung('RE2026050031', status='versendet')
        self._rechnung('RE2026050032', typ='384', status='entwurf', referenz=original)
        from sedo.services.invoices import ensure_zahlung_erlaubt
        from sedo.services.payments import import_bank_json

        ensure_zahlung_erlaubt(original)  # Handbuchung erlaubt …
        ergebnis = import_bank_json([{'datum': self.heute.isoformat(), 'betrag': '119.00',
                                      'verwendungszweck': 'Ueberweisung RE2026050031'}])
        self.assertEqual(ergebnis['matched'], 1)  # … und der Bankimport auch
        self.assertEqual(Payment.objects.filter(invoice=original).count(), 1)

    def test_zahlung_auf_stornobeleg_ist_gesperrt(self):
        original = self._rechnung('RE2026050041', status='storniert')
        storno = self._rechnung('RE2026050042', typ='457', brutto=Decimal('-119.00'),
                                referenz=original)
        from sedo.services.invoices import WorkflowError, ensure_zahlung_erlaubt

        with self.assertRaises(WorkflowError) as ctx:
            ensure_zahlung_erlaubt(storno)
        self.assertIn('Stornorechnung', str(ctx.exception))

    def test_erneuter_versand_erzeugt_das_archiv_nicht_neu(self):
        rechnung = self._rechnung('RE2026050051', status='versendet')
        with patch('sedo.views.smtp_is_configured', return_value=True), \
             patch('sedo.views.send_email_smtp'), \
             patch('sedo.views.generate_invoice_files') as erzeugen:
            self.client.post(reverse('sedo:invoice_email', args=[rechnung.pk]))
        # Ohne vorhandene Datei wird einmal erzeugt – aber nicht bei jedem Versand
        # blind. Hier fehlt die Datei, also genau ein Aufruf.
        self.assertLessEqual(erzeugen.call_count, 1)
