"""Mahnwächter: echte Belegketten, veraltete Aufrufe und atomare Schreibwege.

Ausschließlich synthetische Daten/eigene Medien. Keine Änderung der manuellen
Fälligkeitsregel: diese Restaufgabe ist nicht der separate F-68-Regelumbau.
"""
import os
import tempfile
import threading
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from unittest import skipUnless

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import OperationalError, close_old_connections, connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from sedo.audit import AuditWriteError
from sedo.models import AuditLog, Customer, DunningDocument, Invoice, InvoiceItem, Payment
from sedo.services import create_storno, finalisiere_entwurf
from sedo.services.dunning import (
    WorkflowError, ensure_dunning_allowed, generate_dunning_document,
    get_dunning_candidates, mahn_hindernis, next_dunning_kind, send_bulk_dunning,
)
from .file_fixtures import isolate_files


class DunningGuardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('mahn-guard', password='synthetisch')

    def setUp(self):
        isolate_files(self)
        self.client.force_login(self.user)
        self.customer = Customer.objects.create(name='TEST Mahnwächter', email='mahn@example.invalid')
        self.n = 0
        self.invoice = self.new_invoice()

    def new_invoice(self, **kwargs):
        self.n += 1
        data = dict(customer=self.customer, number=f'RE-MAHN-{self.n}',
                    status='ueberfaellig', gross_total='119.00',
                    issue_date=timezone.localdate()-timedelta(days=50),
                    due_date=timezone.localdate()-timedelta(days=30))
        data.update(kwargs)
        return Invoice.objects.create(**data)

    def followup(self, kind='384', **kwargs):
        data = dict(reference_invoice=self.invoice, invoice_type=kind, status='erstellt')
        data.update(kwargs)
        return self.new_invoice(**data)

    def state(self):
        media = Path(settings.MEDIA_ROOT)
        return (list(Invoice.all_objects.order_by('pk').values()),
                list(DunningDocument.objects.order_by('pk').values()),
                list(AuditLog.objects.order_by('pk').values()),
                {str(p.relative_to(media)): (p.read_bytes(), p.stat().st_mtime_ns)
                 for p in media.rglob('*') if p.is_file()})

    def assert_guarded(self, invoice=None, expected='kann nicht gemahnt'):
        invoice = invoice or self.invoice
        before = self.state()
        self.assertIn(expected, mahn_hindernis(invoice))
        self.assertIsNone(next_dunning_kind(invoice))
        for kind in ('reminder', 'dunning'):
            with self.assertRaises(WorkflowError):
                ensure_dunning_allowed(invoice, kind)
            with self.assertRaises(WorkflowError):
                generate_dunning_document(invoice, kind)
        self.assertEqual(self.state(), before)

    def test_korrekturgraph_sperrt_trotz_falschem_status(self):
        folge = self.followup('384')
        self.assert_guarded()
        self.assertIn(folge.number, mahn_hindernis(self.invoice))

    def test_stornograph_sperrt_trotz_falschem_status(self):
        folge = self.followup('457')
        self.assert_guarded()
        self.assertIn(folge.number, mahn_hindernis(self.invoice))

    def test_alle_offenen_originalstatus_werden_am_graphen_geprueft(self):
        for status in ('erstellt', 'versendet', 'ueberfaellig', 'teilbezahlt', 'gemahnt'):
            for typ in ('384', '457'):
                with self.subTest(status=status, typ=typ):
                    inv = self.new_invoice(status=status)
                    self.followup(typ, reference_invoice=inv)
                    self.assert_guarded(inv)

    def test_geschuetzte_status_bleiben_auch_ohne_folgebeleg_gesperrt(self):
        for status in Invoice.GESPERRT_FUER_MAHNUNG:
            with self.subTest(status=status):
                self.assert_guarded(self.new_invoice(status=status))

    def test_stornobeleg_mit_falsch_positivem_betrag_ist_gesperrt(self):
        inv = self.new_invoice(invoice_type='457', gross_total='119.00')
        self.assert_guarded(inv)
        self.assertNotIn(inv.pk, [c.pk for c in get_dunning_candidates()])

    def test_korrekturentwurf_blockiert_das_original_nicht(self):
        self.followup('384', status='entwurf')
        self.assertEqual(mahn_hindernis(self.invoice), '')
        self.assertIn(self.invoice.pk, [c.pk for c in get_dunning_candidates()])
        doc = generate_dunning_document(self.invoice)
        self.assertTrue(Path(doc.pdf_file.path).is_file())

    def test_stornoentwurf_blockiert_das_original_nicht(self):
        self.followup('457', status='entwurf')
        ensure_dunning_allowed(self.invoice, 'reminder')
        self.assertEqual(next_dunning_kind(self.invoice), 'reminder')

    def test_ausgeblendete_folgebelege_blockieren_nicht(self):
        for typ in ('384', '457'):
            self.followup(typ, deleted_at=timezone.now())
        self.assertEqual(mahn_hindernis(self.invoice), '')
        self.assertEqual(send_bulk_dunning([self.invoice.pk]), {'created': 1, 'skipped': 0})

    def test_andere_belegtypen_sind_keine_ersetzung(self):
        self.followup('380')
        self.assertEqual(mahn_hindernis(self.invoice), '')

    def test_korrekturrechnung_selbst_bleibt_mahnbar(self):
        folge = self.followup('384')
        self.assert_guarded()
        self.assertEqual(mahn_hindernis(folge), '')
        self.assertEqual(generate_dunning_document(folge).invoice_id, folge.pk)

    def test_spaeter_stornierte_korrektur_reaktiviert_original_nicht(self):
        self.followup('384', status='storniert')
        self.assert_guarded()

    def test_doppelfolge_meldet_storno_vorrangig(self):
        self.followup('384')
        storno = self.followup('457')
        self.assertIn(storno.number, mahn_hindernis(self.invoice))

    def test_prefetch_und_altes_pythonobjekt_umgehen_graphen_nicht(self):
        old = Invoice.objects.prefetch_related('followups', 'dunning_documents').get(pk=self.invoice.pk)
        self.followup('384')
        self.assert_guarded(old)

    def test_alter_status_auf_pythonobjekt_wird_nicht_vertraut(self):
        Invoice.objects.filter(pk=self.invoice.pk).update(status='entwurf')
        self.assertEqual(self.invoice.status, 'ueberfaellig')
        self.assert_guarded()

    def test_alte_mahndokumentliste_umgeht_duplikatsperre_nicht(self):
        old = Invoice.objects.prefetch_related('dunning_documents').get(pk=self.invoice.pk)
        DunningDocument.objects.create(invoice=self.invoice, kind='reminder')
        with self.assertRaises(WorkflowError):
            ensure_dunning_allowed(old, 'reminder')

    def test_ausgeblendetes_original_bleibt_gesperrt(self):
        Invoice.objects.filter(pk=self.invoice.pk).update(deleted_at=timezone.now())
        self.assert_guarded()

    def test_ungespeicherter_beleg_ist_gesperrt(self):
        inv = Invoice(customer=self.customer, number='TEST-NICHT-GESPEICHERT')
        self.assert_guarded(inv)

    def test_direkter_generator_blockiert_vor_pdf_und_audit(self):
        self.followup('384')
        before = self.state()
        with patch('sedo.services.dunning._render_dunning_pdf') as pdf:
            with self.assertRaises(WorkflowError):
                generate_dunning_document(self.invoice)
            pdf.assert_not_called()
        self.assertEqual(self.state(), before)

    def test_kandidaten_filtern_graphen_im_sql_ohne_folgeabfragen(self):
        # Alle übrigen Belege sind 457 oder mit einer aktiven Folge gesperrt.
        self.followup('457')
        with CaptureQueriesContext(connection) as calls:
            self.assertEqual(get_dunning_candidates(), [])
        self.assertEqual(len(calls), 1)
        self.assertIn('EXISTS', calls[0]['sql'].upper())

    def test_pruefwege_schreiben_nichts(self):
        before = self.state()
        if connection.vendor == 'sqlite':
            connection.cursor().execute('PRAGMA query_only=ON')
        try:
            ensure_dunning_allowed(self.invoice, 'reminder')
            self.assertEqual(next_dunning_kind(self.invoice), 'reminder')
            self.assertEqual(mahn_hindernis(self.invoice), '')
            self.assertEqual(len(get_dunning_candidates()), 1)
        finally:
            if connection.vendor == 'sqlite':
                connection.cursor().execute('PRAGMA query_only=OFF')
        self.assertEqual(self.state(), before)

    def test_einzel_post_lehnt_ersetztes_original_mit_meldung_ab(self):
        folge = self.followup('384')
        before = self.state()
        response = self.client.post(reverse('sedo:invoice_reminder', args=[self.invoice.pk, 'dunning']), follow=True)
        self.assertContains(response, folge.number)
        self.assertContains(response, 'kann nicht gemahnt werden')
        self.assertEqual(self.state(), before)

    def test_detail_hat_keine_mahnaktionen_fuer_ersetztes_original(self):
        self.followup('457')
        response = self.client.get(reverse('sedo:invoice_detail', args=[self.invoice.pk]))
        self.assertContains(response, 'Mahnungen gesperrt')
        for kind in ('reminder', 'dunning'):
            self.assertNotContains(response, reverse('sedo:invoice_reminder', args=[self.invoice.pk, kind]))

    def test_detail_des_originals_bei_korrekturentwurf_hat_mahnaktionen(self):
        self.followup('384', status='entwurf')
        response = self.client.get(reverse('sedo:invoice_detail', args=[self.invoice.pk]))
        self.assertContains(response, reverse('sedo:invoice_reminder', args=[self.invoice.pk, 'reminder']))

    def test_batch_explizit_ueberspringt_ersetzte_belege(self):
        self.followup('384')
        valid = self.new_invoice()
        result = send_bulk_dunning([self.invoice.pk, valid.pk], 'dunning')
        self.assertEqual(result, {'created': 1, 'skipped': 1})
        self.assertEqual(list(DunningDocument.objects.values_list('invoice_id', flat=True)), [valid.pk])

    def test_batch_http_und_automat_uebergehen_graphen_nicht(self):
        self.followup('457')
        for kind in ('reminder', 'dunning', 'auto'):
            self.client.post(reverse('sedo:bulk_dunning'), {'kind': kind, 'invoice_ids': [self.invoice.pk]})
        self.client.post(reverse('sedo:bulk_dunning'), {'kind': 'auto'})
        self.assertEqual(DunningDocument.objects.count(), 0)

    def test_alte_auswahl_wird_vor_automatischem_schreiben_neu_geprueft(self):
        selected = [i.pk for i in get_dunning_candidates()]
        self.followup('384')
        self.assertEqual(send_bulk_dunning(selected, 'auto'), {'created': 0, 'skipped': 1})

    def test_auto_prueft_geaenderte_faelligkeit_und_echte_zahlung_neu(self):
        selected = [self.invoice.pk]
        Invoice.objects.filter(pk=self.invoice.pk).update(due_date=timezone.localdate()+timedelta(days=2))
        self.assertEqual(send_bulk_dunning(selected, 'auto'), {'created': 0, 'skipped': 1})
        Invoice.objects.filter(pk=self.invoice.pk).update(due_date=timezone.localdate()-timedelta(days=40))
        Payment.objects.create(invoice=self.invoice, amount='119', booking_date=timezone.localdate())
        self.assertEqual(send_bulk_dunning(selected, 'auto'), {'created': 0, 'skipped': 1})
        self.assertFalse(DunningDocument.objects.exists())

    def test_unbekannte_stufe_erzeugt_nicht_stillschweigend_erinnerung(self):
        before = self.state()
        with self.assertRaises(WorkflowError):
            generate_dunning_document(self.invoice, 'falsch')
        with self.assertRaises(WorkflowError):
            send_bulk_dunning([self.invoice.pk], 'falsch')
        self.client.post(reverse('sedo:invoice_reminder', args=[self.invoice.pk, 'falsch']))
        self.assertEqual(self.state(), before)

    def test_batch_doppelte_und_ungueltige_ids_bleiben_sicher(self):
        self.assertEqual(send_bulk_dunning([self.invoice.pk, self.invoice.pk, 'kaputt', 999999]),
                         {'created': 1, 'skipped': 3})
        self.assertEqual(AuditLog.objects.filter(metadata__anlass='mahnung').count(), 1)

    def test_http_get_und_csrf_schreiben_nichts(self):
        url = reverse('sedo:invoice_reminder', args=[self.invoice.pk, 'reminder'])
        self.assertEqual(self.client.get(url).status_code, 405)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(url).status_code, 403)
        self.assertFalse(DunningDocument.objects.exists())

    def test_erfolgreicher_einzelweg_hat_genau_einen_pflichtaudit(self):
        response = self.client.post(reverse('sedo:invoice_reminder', args=[self.invoice.pk, 'reminder']))
        self.assertEqual(response.status_code, 302)
        doc = DunningDocument.objects.get(invoice=self.invoice)
        audits = AuditLog.objects.filter(object_type='DunningDocument', object_id=str(doc.pk))
        self.assertEqual(audits.count(), 1)
        self.assertEqual(audits.get().user_id, self.user.pk)
        self.assertEqual(audits.get().metadata['von'], 'ueberfaellig')
        self.assertEqual(audits.get().metadata['nach'], 'gemahnt')
        self.assertEqual(audits.get().metadata['anlass'], 'mahnung')
        self.assertTrue(Path(doc.pdf_file.path).read_bytes().startswith(b'%PDF'))

    def test_schreibsperre_ist_erstes_datenstatement(self):
        with CaptureQueriesContext(connection) as calls:
            generate_dunning_document(self.invoice)
        statements = [q['sql'].lstrip().upper() for q in calls
                      if q['sql'].lstrip().upper().startswith(('SELECT', 'UPDATE', 'INSERT', 'DELETE'))]
        self.assertTrue(statements[0].startswith('UPDATE'), statements[0])

    def test_auto_stufe_wird_nach_alter_auswahl_neu_bestimmt(self):
        self.assertEqual(next_dunning_kind(self.invoice), 'reminder')
        DunningDocument.objects.create(invoice=self.invoice, kind='reminder',
            document_date=timezone.localdate()-timedelta(days=10),
            due_date_new=timezone.localdate()-timedelta(days=3), amount_open=119)
        self.assertEqual(send_bulk_dunning([self.invoice.pk], 'auto'), {'created': 1, 'skipped': 0})
        self.assertTrue(DunningDocument.objects.filter(invoice=self.invoice, kind='dunning').exists())

    def test_spaetere_korrektur_erhaelt_berechtigte_alte_mahnung(self):
        doc = generate_dunning_document(self.invoice)
        original = Path(doc.pdf_file.path).read_bytes()
        self.followup('384')
        self.assert_guarded()
        self.assertEqual(DunningDocument.objects.count(), 1)
        self.assertEqual(Path(doc.pdf_file.path).read_bytes(), original)

    def test_pflichtauditfehler_rollt_gesamten_einzelbeleg_zurueck(self):
        before = self.state()
        def fail_if_required(*args, **kwargs):
            if kwargs.get('pflicht'):
                raise AuditWriteError('TEST Pflichtaudit fehlt')
        with patch('sedo.services.dunning.write_audit', side_effect=fail_if_required):
            with self.assertRaises(WorkflowError):
                generate_dunning_document(self.invoice)
        self.assertEqual(self.state(), before)

    def test_pdf_fehler_verbraucht_keine_stufe(self):
        before = self.state()
        with patch('sedo.services.dunning._render_dunning_pdf', side_effect=OSError('TEST PDF defekt')):
            with self.assertRaises(WorkflowError):
                generate_dunning_document(self.invoice)
        self.assertEqual(self.state(), before)
        generate_dunning_document(self.invoice)
        self.assertEqual(DunningDocument.objects.count(), 1)

    def test_statuswechsel_zwischen_guard_und_update_wird_nicht_ueberschrieben(self):
        from sedo.services.dunning import _render_dunning_pdf
        before = self.state()
        def change(*args):
            Invoice.objects.filter(pk=self.invoice.pk).update(status='versendet')
            _render_dunning_pdf(*args)
        with patch('sedo.services.dunning._render_dunning_pdf', side_effect=change):
            with self.assertRaises(WorkflowError):
                generate_dunning_document(self.invoice)
        self.assertEqual(self.state(), before)

    def test_neuer_folgebeleg_zwischen_guard_und_update_wird_nicht_ueberschrieben(self):
        from sedo.services.dunning import _render_dunning_pdf
        before = self.state()
        def change(*args):
            self.followup('384')
            _render_dunning_pdf(*args)
        with patch('sedo.services.dunning._render_dunning_pdf', side_effect=change):
            with self.assertRaises(WorkflowError):
                generate_dunning_document(self.invoice)
        self.assertEqual(self.state(), before)

    def test_vorhandene_mahndatei_wird_nicht_ersetzt(self):
        target = Path(settings.MEDIA_ROOT)/'dunning'/self.invoice.number/f'{self.invoice.number}_reminder.pdf'
        target.parent.mkdir(parents=True)
        target.write_bytes(b'TEST vorhandener Originalbeleg')
        before = self.state()
        with self.assertRaises(WorkflowError):
            generate_dunning_document(self.invoice)
        self.assertEqual(self.state(), before)

    def test_zwischenzeitlich_belegtes_dateiziel_bleibt_unveraendert(self):
        from sedo.services.dunning import _render_dunning_pdf
        target = Path(settings.MEDIA_ROOT)/'dunning'/self.invoice.number/f'{self.invoice.number}_reminder.pdf'
        def occupy(*args):
            _render_dunning_pdf(*args)
            target.write_bytes(b'TEST fremdes Original')
        with patch('sedo.services.dunning._render_dunning_pdf', side_effect=occupy):
            with self.assertRaises(WorkflowError):
                generate_dunning_document(self.invoice)
        self.assertEqual(target.read_bytes(), b'TEST fremdes Original')
        self.assertFalse(DunningDocument.objects.exists())
        self.assertFalse(AuditLog.objects.filter(metadata__anlass='mahnung').exists())
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, 'ueberfaellig')

    def test_kein_unsicherer_fallback_ohne_hardlinks(self):
        before = self.state()
        with patch('sedo.services.dunning.os.link', side_effect=OSError('TEST keine Hardlinks')):
            with self.assertRaises(WorkflowError):
                generate_dunning_document(self.invoice)
        self.assertEqual(self.state(), before)

    def test_originaldateien_und_betraege_bleiben_unveraendert(self):
        original = Path(settings.MEDIA_ROOT)/'generated'/'original.pdf'
        original.parent.mkdir(parents=True)
        original.write_bytes(b'TEST Rechnung Originalbytes')
        Invoice.objects.filter(pk=self.invoice.pk).update(pdf_file='generated/original.pdf')
        before = Invoice.objects.values().get(pk=self.invoice.pk)
        stat = original.stat().st_mtime_ns
        generate_dunning_document(self.invoice)
        after = Invoice.objects.values().get(pk=self.invoice.pk)
        self.assertEqual({k:v for k,v in before.items() if k not in ('status','updated_at')},
                         {k:v for k,v in after.items() if k not in ('status','updated_at')})
        self.assertEqual(original.read_bytes(), b'TEST Rechnung Originalbytes')
        self.assertEqual(original.stat().st_mtime_ns, stat)

    def test_path_traversal_und_symlink_werden_abgewiesen(self):
        bad = self.new_invoice(number='../TEST-MAHN')
        with self.assertRaises(WorkflowError):
            generate_dunning_document(bad)
        root = Path(settings.MEDIA_ROOT)
        root.mkdir(parents=True, exist_ok=True)
        outside = root.parent/'extern'; outside.mkdir()
        (root/'dunning').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(WorkflowError):
            generate_dunning_document(self.invoice)
        self.assertFalse(list(outside.iterdir()))
        self.assertFalse(DunningDocument.objects.exists())


@skipUnless(connection.vendor == 'sqlite', 'Echte SQLite-Verbindungen; andere Backends nicht behauptet')
class DunningGuardConcurrencyTests(TransactionTestCase):
    def setUp(self):
        isolate_files(self)
        customer = Customer.objects.create(name='TEST Mahnparallelität')
        self.invoice = Invoice.objects.create(customer=customer, number='RE-MAHN-PARALLEL',
            status='ueberfaellig', gross_total='119.00', due_date=timezone.localdate()-timedelta(days=30))
        InvoiceItem.objects.create(invoice=self.invoice, description='TEST Leistung', unit_price=100)

    def held_writer(self, function, patch_target, real_function):
        reached, release = threading.Event(), threading.Event()
        result = []
        def hold(*args, **kwargs):
            value = real_function(*args, **kwargs)
            reached.set()
            if not release.wait(15):
                raise RuntimeError('TEST Haltepunkt nicht freigegeben')
            return value
        def worker():
            close_old_connections()
            try:
                function()
                result.append('ok')
            except Exception as exc:
                result.append(repr(exc))
            finally:
                close_old_connections()
        with patch(patch_target, side_effect=hold):
            thread = threading.Thread(target=worker)
            thread.start()
            try:
                self.assertTrue(reached.wait(15), 'Schreibtransaktion nicht erreicht')
                with self.assertRaises(WorkflowError):
                    generate_dunning_document(self.invoice)
            finally:
                release.set()
                thread.join(15)
            self.assertFalse(thread.is_alive())
        self.assertEqual(result, ['ok'])

    def test_parallel_zum_storno_entsteht_keine_mahnung(self):
        from sedo.services.invoices import _create_storno_inner
        self.held_writer(lambda: create_storno(Invoice.objects.get(pk=self.invoice.pk)),
                         'sedo.services.invoices._create_storno_inner', _create_storno_inner)
        with self.assertRaises(WorkflowError):
            generate_dunning_document(self.invoice)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, 'storniert')
        self.assertFalse(DunningDocument.objects.exists())
        self.assertFalse((Path(settings.MEDIA_ROOT)/'dunning').exists())

    def test_parallel_zur_korrekturfinalisierung_entsteht_keine_mahnung(self):
        from sedo.services.invoices import _generate_invoice_files_inner
        correction = Invoice.objects.create(customer=self.invoice.customer, number='EN-MAHN-FOLGE',
            reference_invoice=self.invoice, invoice_type='384', status='entwurf')
        InvoiceItem.objects.create(invoice=correction, description='TEST Korrektur', unit_price=100)
        self.held_writer(lambda: finalisiere_entwurf(Invoice.objects.get(pk=correction.pk)),
                         'sedo.services.invoices._generate_invoice_files_inner', _generate_invoice_files_inner)
        with self.assertRaises(WorkflowError):
            generate_dunning_document(self.invoice)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, 'korrigiert')
        self.assertFalse(DunningDocument.objects.exists())

    def test_zwei_parallel_mahnende_verbindungen_erzeugen_nur_einen_beleg(self):
        from sedo.services.dunning import _render_dunning_pdf
        self.held_writer(lambda: generate_dunning_document(Invoice.objects.get(pk=self.invoice.pk)),
                         'sedo.services.dunning._render_dunning_pdf', _render_dunning_pdf)
        self.assertEqual(DunningDocument.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(metadata__anlass='mahnung').count(), 1)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, 'gemahnt')

    def test_commitfehler_nimmt_eigene_veroeffentlichung_zurueck(self):
        with patch.object(connection, 'commit', side_effect=OperationalError('TEST Commit fehlgeschlagen')):
            with self.assertRaises(OperationalError):
                generate_dunning_document(self.invoice)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, 'ueberfaellig')
        self.assertFalse(DunningDocument.objects.exists())
        self.assertFalse(AuditLog.objects.filter(metadata__anlass='mahnung').exists())
        self.assertFalse(list(Path(settings.MEDIA_ROOT).rglob('*.pdf')))
        quarantine = Path(settings.MEDIA_ROOT).parent/'.media_quarantaene'
        self.assertEqual(len(list(quarantine.rglob('*.pdf'))), 1)
