"""Etappe 5: ausschließlich synthetischer Bestand, keine Betriebsdateien."""
import json
from contextlib import closing
import re
import tempfile
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from sedo.audit import AuditWriteError, write_audit
from sedo.models import (AppSetting, AuditLog, Customer, DunningDocument, Invoice,
                         InvoiceItem, NumberRange, Offer, Payment)
from sedo.services import create_storno
from sedo.services.belegketten import (BelegkettenKonflikt, _repariere_befund,
                                      pruefe_belegketten)
from sedo.views import security_status


class BelegkettenTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.customer = Customer.objects.create(name='Synthetischer Prüfkunde')
        cls.user = get_user_model().objects.create_user('ketten-test')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='sedo_ketten_test_')
        self.addCleanup(self.tmp.cleanup)
        self.medien = Path(self.tmp.name) / 'media'
        self.medien.mkdir()
        override = override_settings(MEDIA_ROOT=self.medien,
            SEDO_BACKUP_DIR=Path(self.tmp.name) / 'backups')
        override.enable()
        self.addCleanup(override.disable)
        self.heute = timezone.localdate()
        self.nr = 0

    def beleg(self, **kwargs):
        self.nr += 1
        daten = dict(number=f'RE209801{self.nr:04}', customer=self.customer,
                     status='erstellt', issue_date=self.heute,
                     due_date=self.heute + timedelta(days=14), gross_total=Decimal('119.00'))
        daten.update(kwargs)
        return Invoice.objects.create(**daten)

    def folge(self, original, typ='384', **kwargs):
        return self.beleg(reference_invoice=original, invoice_type=typ, **kwargs)

    def snapshot(self):
        return {model.__name__: list(getattr(model, 'all_objects', model.objects)
                                    .order_by('pk').values())
                for model in (Invoice, InvoiceItem, Payment, Offer, NumberRange, AuditLog, AppSetting)}

    def fund(self, inv, bericht=None):
        bericht = bericht or pruefe_belegketten()
        return next(f for f in bericht['funde'] if f['invoice_id'] == inv.pk)

    def audit(self):
        return AuditLog.objects.filter(metadata__anlass='reparatur')

    def fuenf_faelle(self):
        a = self.beleg(status='ueberfaellig')
        self.folge(a)
        b = self.beleg(status='bezahlt')
        self.folge(b, '457')
        c = self.beleg(status='ueberfaellig')
        self.folge(c)
        self.folge(c, '457')
        d = self.beleg(status='storniert')
        sauber = self.beleg()
        return a, b, c, d, sauber

    def test_f07_trockenlauf_meldet_vier_funde_ohne_schreibzugriff(self):
        self.fuenf_faelle()
        vorher = self.snapshot()
        with CaptureQueriesContext(connection) as queries:
            bericht = pruefe_belegketten()
        self.assertEqual(bericht['zusammenfassung']['befunde'], 4)
        self.assertEqual(bericht['zusammenfassung']['reparierbar'], 4)
        self.assertEqual(bericht['aenderungen'], [])
        self.assertEqual(self.snapshot(), vorher)
        self.assertFalse(any(re.match(r'\s*(UPDATE|INSERT|DELETE|REPLACE|CREATE|ALTER|DROP)\b',
                                      q['sql'], re.I) for q in queries))

    def test_f07_vier_reparaturen_genau_vier_audits(self):
        faelle = self.fuenf_faelle()
        bericht = pruefe_belegketten(reparieren=True)
        self.assertEqual(len(bericht['aenderungen']), 4)
        self.assertEqual(self.audit().count(), 4)
        for inv, ziel in zip(faelle, ['korrigiert', 'storniert', 'storniert', 'erstellt', 'erstellt']):
            inv.refresh_from_db()
            self.assertEqual(inv.status, ziel)
        self.assertEqual(bericht['nachpruefung']['zusammenfassung']['reparierbar'], 0)

    def test_f07_storno_hat_vorrang_und_beide_nummern_sind_sichtbar(self):
        inv = self.beleg(status='bezahlt')
        korrektur, storno = self.folge(inv), self.folge(inv, '457')
        bericht = pruefe_belegketten(reparieren=True)
        inv.refresh_from_db()
        self.assertEqual(inv.status, 'storniert')
        fund = self.fund(inv, bericht)
        self.assertEqual(fund['storno_nummern'], [storno.number])
        self.assertEqual(fund['korrektur_nummern'], [korrektur.number])
        self.assertTrue(fund['bitte_pruefen'])
        self.assertEqual(bericht['nachpruefung']['zusammenfassung']['kettenfehler'], 1)
        self.assertTrue(self.fund(inv, bericht['nachpruefung'])['bitte_pruefen'])
        meta = self.audit().get().metadata
        self.assertEqual(meta['storno_nummern'], storno.number)
        self.assertEqual(meta['korrektur_nummern'], korrektur.number)

    def test_f07_korrektur_repariert_ueberfaelliges_original(self):
        inv = self.beleg(status='ueberfaellig')
        self.folge(inv)
        pruefe_belegketten(reparieren=True)
        inv.refresh_from_db()
        self.assertEqual(inv.status, 'korrigiert')

    def test_f07_aktive_belegdefinition_stimmt_mit_modell_ueberein(self):
        for typ, attr in [('384', 'active_correction'), ('457', 'active_storno')]:
            with self.subTest(typ=typ):
                inv = self.beleg()
                folge = self.folge(inv, typ, status='entwurf')
                self.assertIsNone(getattr(inv, attr))
                self.assertFalse(any(f['invoice_id'] == inv.pk for f in pruefe_belegketten()['funde']))
                folge.status = 'erstellt'
                folge.lifecycle = 'issued'
                folge.save(update_fields=['status', 'lifecycle'])
                self.assertEqual(getattr(inv, attr).pk, folge.pk)
                self.assertTrue(self.fund(inv)['reparierbar'])

    def test_f13_korrekturentwurf_blockiert_original_nicht(self):
        inv = self.beleg()
        self.folge(inv, status='entwurf')
        pruefe_belegketten(reparieren=True)
        inv.refresh_from_db()
        self.assertEqual(inv.status, 'erstellt')
        self.assertEqual(self.audit().count(), 0)

    def test_f13_stornoentwurf_zaehlt_nicht(self):
        inv = self.beleg()
        self.folge(inv, '457', status='entwurf')
        self.assertEqual(pruefe_belegketten()['zusammenfassung']['befunde'], 0)

    def test_f14_ausgeblendeter_folgebeleg_zaehlt_nicht(self):
        inv = self.beleg(status='storniert')
        self.folge(inv, '457', deleted_at=timezone.now())
        pruefe_belegketten(reparieren=True)
        inv.refresh_from_db()
        self.assertEqual(inv.status, 'erstellt')
        self.assertEqual(self.audit().get().metadata['gruppe'], 'B')

    def test_f14_ausgeblendetes_original_bleibt_unveraendert(self):
        inv = self.beleg(status='bezahlt', deleted_at=timezone.now())
        self.folge(inv, '457')
        vorher = self.snapshot()
        self.assertEqual(pruefe_belegketten(reparieren=True)['aenderungen'], [])
        self.assertEqual(self.snapshot(), vorher)

    def test_f14_normaler_folgebeleg_ist_keine_korrektur(self):
        inv = self.beleg(status='storniert')
        self.folge(inv, '380')
        pruefe_belegketten(reparieren=True)
        inv.refresh_from_db()
        self.assertEqual(inv.status, 'erstellt')

    def test_f14_gruppe_b_ohne_zahlung_wird_erstellt(self):
        inv = self.beleg(status='korrigiert')
        pruefe_belegketten(reparieren=True)
        inv.refresh_from_db()
        self.assertEqual(inv.status, 'erstellt')

    def test_f14_gruppe_b_nur_entwurf_wird_freigegeben(self):
        inv = self.beleg(status='korrigiert')
        self.folge(inv, status='entwurf')
        pruefe_belegketten(reparieren=True)
        inv.refresh_from_db()
        self.assertEqual(inv.status, 'erstellt')

    def test_f14_gruppe_b_leitet_bezahlt_ab_und_auditiert_endzustand(self):
        inv = self.beleg(status='storniert')
        Payment.objects.create(invoice=inv, amount='119.00', booking_date=self.heute)
        self.assertEqual(self.fund(inv)['nach'], 'bezahlt')
        pruefe_belegketten(reparieren=True)
        inv.refresh_from_db()
        self.assertEqual(inv.status, 'bezahlt')
        self.assertEqual(self.audit().get().metadata['nach'], 'bezahlt')
        self.assertEqual(self.audit().count(), 1)

    def test_f14_gruppe_b_leitet_teilbezahlt_ab(self):
        inv = self.beleg(status='korrigiert')
        Payment.objects.create(invoice=inv, amount='20.00', booking_date=self.heute)
        pruefe_belegketten(reparieren=True)
        inv.refresh_from_db()
        self.assertEqual(inv.status, 'teilbezahlt')

    def test_f14_gruppe_b_leitet_gemahnt_ab(self):
        inv = self.beleg(status='korrigiert', due_date=self.heute - timedelta(days=2))
        DunningDocument.objects.create(invoice=inv, kind='reminder')
        pruefe_belegketten(reparieren=True)
        inv.refresh_from_db()
        self.assertEqual(inv.status, 'gemahnt')

    def test_f14_gruppe_b_leitet_ueberfaellig_ab(self):
        inv = self.beleg(status='storniert', due_date=self.heute - timedelta(days=1))
        pruefe_belegketten(reparieren=True)
        inv.refresh_from_db()
        self.assertEqual(inv.status, 'ueberfaellig')

    def test_f14_gruppe_b_ignoriert_ausgeblendete_zahlungen(self):
        inv = self.beleg(status='storniert')
        Payment.objects.create(invoice=inv, amount='119.00', booking_date=self.heute,
                               deleted_at=timezone.now())
        pruefe_belegketten(reparieren=True)
        inv.refresh_from_db()
        self.assertEqual(inv.status, 'erstellt')

    def test_f14_nach_reparatur_ist_echtes_storno_moeglich(self):
        inv = self.beleg(status='storniert')
        InvoiceItem.objects.create(invoice=inv, description='Testleistung', quantity=1, unit_price=100)
        pruefe_belegketten(reparieren=True)
        inv.refresh_from_db()
        storno = create_storno(inv)
        inv.refresh_from_db()
        self.assertEqual(inv.status, 'storniert')
        self.assertEqual(storno.reference_invoice_id, inv.pk)
        self.assertTrue(Path(storno.xml_file.path).exists())

    def test_f89_storno_bezahlt_wird_erstellt(self):
        inv = self.beleg(invoice_type='457', status='bezahlt', gross_total='-119.00')
        pruefe_belegketten(reparieren=True)
        inv.refresh_from_db()
        self.assertEqual(inv.status, 'erstellt')
        self.assertEqual(self.audit().get().metadata['gruppe'], 'D')

    def test_f89_gruppe_b_storno_keine_faelligkeitsableitung(self):
        inv = self.beleg(invoice_type='457', status='storniert', gross_total='-119.00',
                         due_date=self.heute - timedelta(days=30))
        pruefe_belegketten(reparieren=True)
        inv.refresh_from_db()
        self.assertEqual(inv.status, 'erstellt')

    def test_f13_alter_entwurf_nur_hinweis(self):
        inv = self.beleg(number='EN2098010001', status='entwurf')
        Invoice.objects.filter(pk=inv.pk).update(created_at=timezone.now() - timedelta(days=31))
        vorher = self.snapshot()
        b = pruefe_belegketten(reparieren=True)
        self.assertEqual(b['zusammenfassung']['gruppen']['D'], 1)
        self.assertEqual(self.fund(inv, b)['code'], 'entwurf_aelter_30_tage')
        self.assertEqual(b['aenderungen'], [])
        self.assertEqual(self.snapshot(), vorher)

    def test_f13_alter_grenze_genau_30_tage(self):
        inv = self.beleg(status='entwurf')
        Invoice.objects.filter(pk=inv.pk).update(created_at=timezone.now() - timedelta(days=30))
        self.assertEqual(pruefe_belegketten()['funde'], [])

    def test_f13_alter_bezieht_sich_auf_anlage_nicht_rechnungsdatum(self):
        self.beleg(status='entwurf', issue_date=self.heute - timedelta(days=60))
        self.assertEqual(pruefe_belegketten()['funde'], [])

    def test_f13_xml_verweise_nur_melden_keine_dateien_aendern(self):
        inv = self.beleg(status='entwurf', xml_file='test.xml', zugferd_file='test.pdf')
        (self.medien / 'test.xml').write_bytes(b'<synthetisch/>')
        (self.medien / 'test.pdf').write_bytes(b'%PDF-synthetisch')
        vorher = self.snapshot()
        dateien = {p.name: p.read_bytes() for p in self.medien.iterdir()}
        b = pruefe_belegketten(reparieren=True)
        self.assertEqual(self.fund(inv, b)['gruppe'], 'C')
        self.assertFalse(self.fund(inv, b)['reparierbar'])
        self.assertEqual(self.snapshot(), vorher)
        self.assertEqual({p.name: p.read_bytes() for p in self.medien.iterdir()}, dateien)

    def test_f13_dateisystem_wird_gar_nicht_abgefragt(self):
        self.beleg(status='entwurf', xml_file='fehlt.xml')
        with patch.object(Path, 'exists', side_effect=AssertionError('Dateizugriff')):
            b = pruefe_belegketten(reparieren=True)
        self.assertEqual(b['zusammenfassung']['gruppen']['C'], 1)

    def test_f13_auch_nur_zugferd_verweis_wird_gemeldet(self):
        inv = self.beleg(status='entwurf', zugferd_file='fehlt.pdf')
        b = pruefe_belegketten()
        self.assertEqual(b['zusammenfassung']['gruppen']['C'], 1)
        self.assertEqual(self.fund(inv, b)['gruppe'], 'C')

    def test_f13_alter_entwurf_mit_xml_sind_zwei_befunde_ein_beleg(self):
        inv = self.beleg(status='entwurf', xml_file='fehlt.xml')
        Invoice.objects.filter(pk=inv.pk).update(created_at=timezone.now() - timedelta(days=31))
        s = pruefe_belegketten()['zusammenfassung']
        self.assertEqual(s['befunde'], 2)
        self.assertEqual(s['betroffene_belege'], 1)

    def test_f13_entwurf_mit_folgebeleg_wird_nie_ausgestellt(self):
        inv = self.beleg(status='entwurf')
        self.folge(inv, '457')
        pruefe_belegketten(reparieren=True)
        inv.refresh_from_db()
        self.assertEqual(inv.status, 'entwurf')
        self.assertTrue(self.fund(inv)['bitte_pruefen'])

    def test_f13_en_mit_falschem_status_wird_nicht_ausgestellt(self):
        inv = self.beleg(number='EN2098010001', status='storniert')
        pruefe_belegketten(reparieren=True)
        inv.refresh_from_db()
        self.assertEqual(inv.status, 'storniert')
        self.assertFalse(self.fund(inv)['reparierbar'])

    def test_f07_storniert_nur_mit_korrektur_bleibt_manuell(self):
        inv = self.beleg(status='storniert')
        self.folge(inv)
        pruefe_belegketten(reparieren=True)
        inv.refresh_from_db()
        self.assertEqual(inv.status, 'storniert')
        self.assertEqual(self.fund(inv)['code'], 'storniert_nur_mit_korrektur')
        self.assertTrue(self.fund(inv)['bitte_pruefen'])

    def test_reparatur_ist_idempotent_ohne_neue_audits(self):
        self.fuenf_faelle()
        pruefe_belegketten(reparieren=True)
        vorher = self.snapshot()
        b = pruefe_belegketten(reparieren=True)
        self.assertEqual(b['aenderungen'], [])
        self.assertEqual(self.snapshot(), vorher)

    def test_audit_nennt_von_nach_anlass_und_lauf(self):
        inv = self.beleg(status='storniert')
        b = pruefe_belegketten(reparieren=True)
        audit = self.audit().get()
        self.assertEqual(audit.action, 'update')
        self.assertEqual(audit.object_id, str(inv.pk))
        self.assertEqual(audit.object_repr, inv.number)
        self.assertIsNone(audit.user_id)
        self.assertEqual(audit.metadata['von'], 'storniert')
        self.assertEqual(audit.metadata['nach'], 'erstellt')
        self.assertEqual(audit.metadata['anlass'], 'reparatur')
        self.assertEqual(audit.metadata['lauf_id'], b['lauf_id'])

    def test_auditfehler_rollt_status_und_zeitstempel_zurueck(self):
        self.beleg(status='storniert')
        vorher = self.snapshot()
        with patch.object(AuditLog.objects, 'create', side_effect=RuntimeError('Audit-Testfehler')):
            with self.assertRaises(AuditWriteError):
                pruefe_belegketten(reparieren=True)
        self.assertEqual(self.snapshot(), vorher)

    def test_zweiter_auditfehler_rollt_ganzen_lauf_zurueck(self):
        self.beleg(status='storniert')
        self.beleg(status='korrigiert')
        vorher = self.snapshot()
        aufrufe = []
        def audit(*args, **kwargs):
            aufrufe.append(1)
            if len(aufrufe) == 2:
                raise AuditWriteError('Zweiter Audit kaputt')
            return write_audit(*args, **kwargs)
        with patch('sedo.services.belegketten.write_audit', side_effect=audit):
            with self.assertRaises(AuditWriteError):
                pruefe_belegketten(reparieren=True)
        self.assertEqual(len(aufrufe), 2)
        self.assertEqual(self.snapshot(), vorher)

    def test_guard_weist_veralteten_status_ab(self):
        inv = self.beleg(status='storniert')
        f = self.fund(inv)
        Invoice.objects.filter(pk=inv.pk).update(status='versendet')
        with self.assertRaises(BelegkettenKonflikt):
            _repariere_befund(f, 'testlauf')
        inv.refresh_from_db()
        self.assertEqual(inv.status, 'versendet')
        self.assertEqual(self.audit().count(), 0)

    def test_guard_weist_veraltete_revision_ab(self):
        inv = self.beleg(status='storniert')
        f = self.fund(inv)
        Invoice.objects.filter(pk=inv.pk).update(updated_at=timezone.now())
        with self.assertRaises(BelegkettenKonflikt):
            _repariere_befund(f, 'testlauf')
        inv.refresh_from_db()
        self.assertEqual(inv.status, 'storniert')

    def test_guard_reaktiviert_keinen_ausgeblendeten_beleg(self):
        inv = self.beleg(status='storniert')
        f = self.fund(inv)
        Invoice.objects.filter(pk=inv.pk).update(deleted_at=timezone.now())
        with self.assertRaises(BelegkettenKonflikt):
            _repariere_befund(f, 'testlauf')
        inv.refresh_from_db()
        self.assertIsNotNone(inv.deleted_at)

    def test_reparatur_schreibsperre_kommt_vor_select(self):
        self.beleg(status='storniert')
        with CaptureQueriesContext(connection) as queries:
            pruefe_belegketten(reparieren=True)
        datenzugriffe = [q['sql'] for q in queries if re.match(r'\s*(SELECT|UPDATE)\b', q['sql'], re.I)]
        self.assertTrue(datenzugriffe[0].startswith('UPDATE'))
        self.assertIn('updated_at', datenzugriffe[0])

    def test_schreibschutz_fuer_nicht_sqlite(self):
        self.beleg(status='storniert')
        vorher = self.snapshot()
        with patch.object(connection, 'vendor', 'postgresql'):
            with self.assertRaises(BelegkettenKonflikt):
                pruefe_belegketten(reparieren=True)
            self.assertEqual(pruefe_belegketten()['zusammenfassung']['befunde'], 1)
        self.assertEqual(self.snapshot(), vorher)

    def test_reparatur_aendert_keine_anderen_belegdaten(self):
        inv = self.beleg(status='storniert', note='Nicht ändern', source='Prüfung', xml_file='fehlt.xml')
        InvoiceItem.objects.create(invoice=inv, description='Leistung', quantity=1, unit_price=100)
        vorher = self.snapshot()
        pruefe_belegketten(reparieren=True)
        nachher = self.snapshot()
        for zeile in vorher['Invoice'] + nachher['Invoice']:
            zeile.pop('status')
            zeile.pop('lifecycle')
            zeile.pop('updated_at')
        vorher.pop('AuditLog')
        nachher.pop('AuditLog')
        self.assertEqual(nachher, vorher)

    def test_kein_500er_limit_und_ein_graph_select(self):
        Invoice.objects.bulk_create([Invoice(number=f'RE209802{i:04}', customer=self.customer)
                                      for i in range(600)])
        inv = self.beleg(status='bezahlt')
        self.folge(inv, '457')
        with CaptureQueriesContext(connection) as queries:
            b = pruefe_belegketten()
        self.assertEqual(b['zusammenfassung']['belege_geprueft'], 602)
        self.assertEqual(b['zusammenfassung']['befunde'], 1)
        self.assertEqual(sum(q['sql'].lstrip().upper().startswith('SELECT') for q in queries), 1)

    def test_leerer_bestand(self):
        self.assertEqual(pruefe_belegketten()['zusammenfassung']['befunde'], 0)
        self.assertEqual(pruefe_belegketten(reparieren=True)['aenderungen'], [])

    def test_command_standard_ist_lesend(self):
        self.fuenf_faelle()
        vorher = self.snapshot()
        out = StringIO()
        call_command('belegketten_pruefen', stdout=out)
        self.assertIn('TROCKENLAUF', out.getvalue())
        self.assertIn('4 Befunde', out.getvalue())
        self.assertEqual(self.snapshot(), vorher)

    def test_command_json_ist_parsebar_und_lesend(self):
        self.fuenf_faelle()
        vorher = self.snapshot()
        out = StringIO()
        call_command('belegketten_pruefen', '--json', stdout=out)
        b = json.loads(out.getvalue())
        self.assertEqual(b['schema_version'], 1)
        self.assertEqual(b['modus'], 'trockenlauf')
        self.assertEqual(b['zusammenfassung']['befunde'], 4)
        self.assertEqual(self.snapshot(), vorher)

    def test_command_reparieren_json_mit_nachpruefung(self):
        self.fuenf_faelle()
        out = StringIO()
        call_command('belegketten_pruefen', '--reparieren', '--json', stdout=out)
        b = json.loads(out.getvalue())
        self.assertEqual(b['modus'], 'reparatur')
        self.assertEqual(len(b['aenderungen']), 4)
        self.assertEqual(b['nachpruefung']['zusammenfassung']['reparierbar'], 0)

    def test_command_auditfehler_mit_gueltigem_fehler_json(self):
        self.beleg(status='storniert')
        vorher = self.snapshot()
        out = StringIO()
        with patch('sedo.services.belegketten.write_audit', side_effect=AuditWriteError('Test')):
            with self.assertRaises(CommandError):
                call_command('belegketten_pruefen', '--reparieren', '--json', stdout=out)
        b = json.loads(out.getvalue())
        self.assertEqual(b['aenderungen'], [])
        self.assertIn('keine Reparatur', b['fehler'])
        self.assertEqual(self.snapshot(), vorher)

    def test_security_zeigt_fehler_ohne_writes(self):
        self.beleg(status='storniert')
        request = RequestFactory().get(reverse('sedo:security_status'))
        request.user = self.user
        vorher = self.snapshot()
        with patch('sedo.views.render') as render:
            with CaptureQueriesContext(connection) as queries:
                security_status(request)
        c = next(c for c in render.call_args.args[2]['checks'] if c['label'] == 'Belegketten in Ordnung')
        self.assertFalse(c['ok'])
        self.assertEqual(c['value'], 1)
        self.assertEqual(self.snapshot(), vorher)
        self.assertFalse(any(re.match(r'\s*(INSERT|UPDATE|DELETE)\b', q['sql'], re.I) for q in queries))

    def test_security_saubere_kette_ist_ok(self):
        inv = self.beleg(status='korrigiert')
        self.folge(inv)
        request = RequestFactory().get(reverse('sedo:security_status'))
        request.user = self.user
        with patch('sedo.views.render') as render:
            security_status(request)
        c = next(c for c in render.call_args.args[2]['checks'] if c['label'] == 'Belegketten in Ordnung')
        self.assertTrue(c['ok'])
        self.assertEqual(c['value'], 0)

    def test_security_get_rendert_hinweis_und_keinen_reparaturknopf(self):
        self.beleg(status='storniert')
        self.client.force_login(self.user)
        # Die bestehende Backup-Middleware ist nicht Teil der neuen Prüfzeile.
        with patch('sedo.middleware.ensure_weekly_backup'):
            vorher = self.snapshot()
            antwort = self.client.get(reverse('sedo:security_status'))
        self.assertContains(antwort, 'Belegketten in Ordnung')
        from pathlib import Path
        from django.conf import settings
        self.assertNotContains(antwort, 'Trockenlauf')
        self.assertIn('Trockenlauf', (Path(settings.BASE_DIR) / 'BEDIENHINWEISE.md').read_text())
        self.assertEqual(self.snapshot(), vorher)
        self.assertNotIn('name="reparieren"', antwort.content.decode())

    def test_f13_en_storno_mit_zahlstatus_bleibt_manuell(self):
        inv = self.beleg(number='EN2098010001', invoice_type='457', status='bezahlt')
        pruefe_belegketten(reparieren=True)
        inv.refresh_from_db()
        self.assertEqual(inv.status, 'bezahlt')
        self.assertFalse(self.fund(inv)['reparierbar'])

    def test_unveraenderter_beleg_behaelt_zeitstempel_beim_sperren(self):
        sauber = self.beleg()  # erste Zeile = Ziel des unverändernden Sperr-UPDATE
        kaputt = self.beleg(status='storniert')
        vorher = sauber.updated_at
        pruefe_belegketten(reparieren=True)
        sauber.refresh_from_db()
        self.assertEqual(sauber.updated_at, vorher)
        kaputt.refresh_from_db()
        self.assertEqual(kaputt.status, 'erstellt')

    def test_fehler_bei_nachpruefung_rollt_auch_audits_zurueck(self):
        from sedo.services import belegketten
        self.beleg(status='storniert')
        vorher = self.snapshot()
        echter_bericht = belegketten._bericht
        aufrufe = []
        def pruefen(zeit):
            aufrufe.append(1)
            if len(aufrufe) == 2:
                raise BelegkettenKonflikt('Nachprüfung fehlgeschlagen')
            return echter_bericht(zeit)
        with patch.object(belegketten, '_bericht', side_effect=pruefen):
            with self.assertRaises(BelegkettenKonflikt):
                pruefe_belegketten(reparieren=True)
        self.assertEqual(self.snapshot(), vorher)

    def test_hinweisbefund_darf_nicht_direkt_repariert_werden(self):
        inv = self.beleg(status='entwurf', xml_file='fehlt.xml')
        vorher = self.snapshot()
        with self.assertRaises(BelegkettenKonflikt):
            _repariere_befund(self.fund(inv), 'testlauf')
        self.assertEqual(self.snapshot(), vorher)

    def test_ausgestellte_xmls_werden_nicht_als_entwurfsdateien_gemeldet(self):
        self.beleg(xml_file='fehlt.xml', zugferd_file='fehlt.pdf')
        self.assertEqual(pruefe_belegketten()['funde'], [])

    def test_f89_normale_bezahlte_rechnung_bleibt_bezahlt(self):
        inv = self.beleg(status='bezahlt')
        Payment.objects.create(invoice=inv, amount='119.00', booking_date=self.heute)
        vorher = self.snapshot()
        pruefe_belegketten(reparieren=True)
        self.assertEqual(self.snapshot(), vorher)

    def test_trockenlauf_sperrt_nicht_zum_schreiben(self):
        with patch('sedo.services.belegketten._sperre_reparaturlauf') as sperre:
            pruefe_belegketten()
        sperre.assert_not_called()

    def test_trockenlauf_funktioniert_mit_sqlite_query_only(self):
        self.fuenf_faelle()
        with connection.cursor() as cursor:
            cursor.execute('PRAGMA query_only = ON')
        try:
            out = StringIO()
            call_command('belegketten_pruefen', '--json', stdout=out)
            self.assertEqual(json.loads(out.getvalue())['zusammenfassung']['befunde'], 4)
        finally:
            with connection.cursor() as cursor:
                cursor.execute('PRAGMA query_only = OFF')


    def test_gwg_constraintkonflikt_rollt_ganzen_lauf_zurueck(self):
        from sedo.models import CustomerObject
        self.beleg(status='storniert')  # diese erste Reparatur muss ebenfalls zurückrollen
        obj = CustomerObject.objects.create(customer=self.customer, name='Synthetisches GWG-Objekt')
        daten = dict(obj=obj, source='GWG Monatsrechnung',
                     period_start=self.heute.replace(day=1), period_end=self.heute)
        self.beleg(status='storniert', **daten)
        self.beleg(**daten)  # erneuter Lauf ist bei storniertem Altbeleg bisher erlaubt
        vorher = self.snapshot()
        with self.assertRaises(CommandError):
            call_command('belegketten_pruefen', '--reparieren', stdout=StringIO())
        self.assertEqual(self.snapshot(), vorher)


class BelegkettenParallelTests(TransactionTestCase):
    def test_guard_ausserhalb_transaktion_abgewiesen(self):
        self.assertFalse(connection.in_atomic_block)
        with self.assertRaises(BelegkettenKonflikt):
            _repariere_befund({}, 'testlauf')

    def test_sqlite_parallelwartung_verwendet_frischen_graphen(self):
        """Echte zweite Verbindung/Prozess, eigene Datei aus synthetischer Test-DB."""
        import os
        import sqlite3
        import subprocess
        import sys
        import time
        from django.conf import settings

        self.assertEqual(connection.vendor, 'sqlite')
        self.assertFalse(connection.in_atomic_block)
        kunde = Customer.objects.create(name='Paralleltest')
        inv = Invoice.objects.create(number='RE2098030001', customer=kunde, status='storniert')
        folge = Invoice.objects.create(number='RE2098030002', customer=kunde,
            invoice_type='457', status='entwurf', reference_invoice=inv)
        with tempfile.TemporaryDirectory(prefix='sedo_parallel_ketten_') as tmp:
            home = Path(tmp)
            (home / 'data').mkdir()
            with closing(sqlite3.connect(home / 'data' / 'sedo_web.sqlite3')) as schreiber:
                # Quelle ist ausdrücklich die gerade erzeugte Test-DB im Speicher.
                self.assertIn('memory', str(connection.settings_dict['NAME']))
                connection.connection.backup(schreiber)
                code = ("import sys, django; django.setup(); "
                        "print('bereit', flush=True); sys.stdin.readline(); "
                        "from django.core.management import call_command; "
                        "call_command('belegketten_pruefen', '--reparieren', '--json')")
                env = dict(os.environ, SEDO_HOME=str(home), DJANGO_SETTINGS_MODULE='config.settings',
                           DJANGO_SECRET_KEY='synthetischer-paralleltest', PYTHONDONTWRITEBYTECODE='1')
                proc = subprocess.Popen([sys.executable, '-c', code], cwd=settings.BASE_DIR, env=env,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    self.assertEqual(proc.stdout.readline().strip(), 'bereit')
                    schreiber.execute('BEGIN IMMEDIATE')
                    schreiber.execute('UPDATE sedo_invoice SET status=?, lifecycle=? WHERE id=?', ('bezahlt', 'issued', inv.pk))
                    schreiber.execute('UPDATE sedo_invoice SET status=?, lifecycle=? WHERE id=?', ('erstellt', 'issued', folge.pk))
                    proc.stdin.write('start\n')
                    proc.stdin.flush()
                    time.sleep(0.25)
                    self.assertIsNone(proc.poll(), 'Reparatur muss auf die SQLite-Schreibsperre warten.')
                    schreiber.commit()
                    stdout, stderr = proc.communicate(timeout=20)
                    self.assertEqual(proc.returncode, 0, stderr)
                    bericht = json.loads(stdout)
                    self.assertEqual(len(bericht['aenderungen']), 1)
                    self.assertEqual(bericht['aenderungen'][0]['von'], 'bezahlt')
                    self.assertEqual(bericht['aenderungen'][0]['nach'], 'storniert')
                    self.assertEqual(schreiber.execute('SELECT status, lifecycle FROM sedo_invoice WHERE id=?',
                                                       (inv.pk,)).fetchone(), ('storniert', 'cancelled'))
                    self.assertEqual(schreiber.execute("SELECT count(*) FROM sedo_auditlog").fetchone()[0], 1)
                finally:
                    if proc.poll() is None:
                        proc.kill()
                    proc.communicate()
