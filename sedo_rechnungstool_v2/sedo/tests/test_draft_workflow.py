"""Welle 2, F-13/F-14: Entwurf, EN-Nummernkreis und atomare Finalisierung.

Ausschließlich synthetische Daten, eigene temporäre Medien. Die echte
PDF-/XML-Erzeugung bleibt aktiv; nur gezielte Fehler werden simuliert.
"""
import tempfile
import threading
import zipfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import OperationalError, close_old_connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from pypdf import PdfReader

from sedo.audit import AuditWriteError, write_audit
from sedo.models import AuditLog, Customer, CustomerObject, Invoice, InvoiceItem, NumberRange, Offer, Payment
from sedo.services import (WorkflowError, create_storno, ensure_offer_invoicing_allowed,
                           finalisiere_entwurf, generate_invoice_files, import_bank_json,
                           invoice_to_core_row, mark_invoice_corrected, next_number,
                           save_with_unique_number)


class DraftWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('entwurf-test')
        cls.customer = Customer.objects.create(name='Testkunde', payment_days=14)
        cls.obj = CustomerObject.objects.create(customer=cls.customer, name='Testobjekt')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='sedo_entwurf_test_')
        self.addCleanup(self.tmp.cleanup)
        self.settings_override = override_settings(
            MEDIA_ROOT=Path(self.tmp.name) / 'media',
            SEDO_BACKUP_DIR=Path(self.tmp.name) / 'backups',
            PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.client.force_login(self.user)
        self.today = timezone.localdate()

    def draft(self, *, prefix='EN', **kwargs):
        data = dict(customer=self.customer, obj=self.obj, status='entwurf',
                    issue_date=self.today, due_date=self.today + timedelta(days=14))
        data.update(kwargs)
        inv = Invoice(**data)
        save_with_unique_number(inv, prefix)
        InvoiceItem.objects.create(invoice=inv, description='Reinigung', quantity=1,
                                   unit='pauschal', unit_price='100.00', long_text='Testleistung')
        return inv

    def payload(self, inv=None, **changes):
        fields = {
            'customer': str(self.customer.pk), 'obj': str(self.obj.pk),
            'issue_date': self.today.isoformat(), 'payment_days': '14', 'vat_rate': '19.00',
            'action': 'draft', 'items-TOTAL_FORMS': '1',
            'items-INITIAL_FORMS': '1' if inv else '0', 'items-MIN_NUM_FORMS': '1',
            'items-MAX_NUM_FORMS': '1000', 'items-0-description': 'Reinigung',
            'items-0-quantity': '2', 'items-0-unit': 'pauschal', 'items-0-unit_price': '100.00',
            'items-0-long_text': 'Geänderte Leistung',
        }
        if inv:
            fields.update({'revision': inv.updated_at.isoformat(),
                           'items-0-id': str(inv.items.first().pk)})
        fields.update(changes)
        return fields

    def confirm(self, inv, **changes):
        fields = {'confirm': '1', 'revision': inv.updated_at.isoformat()}
        fields.update(changes)
        return self.client.post(reverse('sedo:invoice_finalize', args=[inv.pk]), fields)

    def snapshot(self, inv):
        return Invoice.all_objects.filter(pk=inv.pk).values().get()

    def generated(self):
        return {str(p.relative_to(settings.MEDIA_ROOT)): p.read_bytes()
                for p in Path(settings.MEDIA_ROOT).rglob('*') if p.is_file()}

    def test_f13_neuer_entwurf_bekommt_en_ohne_re_verbrauch(self):
        vorher = next_number('RE')
        response = self.client.post(reverse('sedo:invoice_create'), self.payload())
        self.assertEqual(response.status_code, 302)
        inv = Invoice.objects.get()
        self.assertTrue(inv.number.startswith('EN'))
        self.assertEqual(next_number('RE'), vorher)
        self.assertTrue(NumberRange.objects.filter(prefix='EN').exists())
        self.assertFalse(NumberRange.objects.filter(prefix='RE').exists())
        self.assertTrue(Path(inv.pdf_file.path).is_file())
        self.assertFalse(inv.xml_file or inv.zugferd_file)

    def test_f13_entwurf_ruft_keinen_xml_generator_auf(self):
        inv = self.draft()
        from sedo.services.invoices import build_xml, embed_xml_into_pdf
        with patch('sedo.services.invoices.build_xml', wraps=build_xml) as xml, patch('sedo.services.invoices.embed_xml_into_pdf', wraps=embed_xml_into_pdf) as embed:
            generate_invoice_files(inv)
        xml.assert_not_called()
        embed.assert_not_called()
        self.assertTrue(invoice_to_core_row(inv).is_draft)
        self.assertIn('ENTWURF', ''.join(p.extract_text() for p in PdfReader(inv.pdf_file.path).pages))
        self.assertFalse(inv.xml_file or inv.zugferd_file)
        self.assertEqual(len(self.generated()), 1)

    def test_f13_dateien_neu_erzeugen_bleibt_entwurfs_pdf(self):
        inv = self.draft()
        for _ in range(2):
            response = self.client.post(reverse('sedo:invoice_generate', args=[inv.pk]))
            self.assertEqual(response.status_code, 302)
        inv.refresh_from_db()
        self.assertEqual(inv.status, 'entwurf')
        self.assertFalse(inv.xml_file or inv.zugferd_file)
        self.assertEqual(len(self.generated()), 1)

    def test_f13_bestands_xml_wird_erhalten_aber_nicht_mehr_ausgeliefert(self):
        inv = self.draft(prefix='RE', status='erstellt')
        generate_invoice_files(inv)
        xml_path = Path(inv.xml_file.path)
        xml_bytes = xml_path.read_bytes()
        Invoice.objects.filter(pk=inv.pk).update(status='entwurf')
        generate_invoice_files(inv)
        self.assertFalse(inv.xml_file or inv.zugferd_file)
        self.assertFalse(xml_path.exists())
        quarantine = Path(self.tmp.name) / '.media_quarantaene'
        self.assertTrue(any(p.read_bytes() == xml_bytes for p in quarantine.rglob('factur-x.xml')))

    def test_f13_detail_versteckt_auch_alte_entwurfs_xml_links(self):
        inv = self.draft(xml_file='alt.xml', zugferd_file='alt_zugferd.pdf')
        response = self.client.get(reverse('sedo:invoice_detail', args=[inv.pk]))
        self.assertContains(response, 'Entwurf verwerfen')
        self.assertContains(response, 'Bearbeiten')
        self.assertContains(response, 'Rechnung finalisieren')
        self.assertNotContains(response, 'href="/media/alt.xml"')
        self.assertNotContains(response, 'href="/media/alt_zugferd.pdf"')

    def test_f13_finalisieren_erzeugt_re_mit_xml_ohne_wasserzeichen(self):
        inv = self.draft()
        generate_invoice_files(inv)
        alt = inv.number
        alt_pdf = Path(inv.pdf_file.path)
        finalisiere_entwurf(inv)
        self.assertEqual(inv.status, 'erstellt')
        self.assertTrue(inv.number.startswith('RE'))
        self.assertNotEqual(inv.number, alt)
        self.assertTrue(alt_pdf.exists())
        self.assertFalse(invoice_to_core_row(inv).is_draft)
        self.assertIn(inv.number.encode(), Path(inv.xml_file.path).read_bytes())
        self.assertNotIn('ENTWURF', ''.join(p.extract_text() for p in PdfReader(inv.pdf_file.path).pages))
        self.assertTrue(Path(inv.zugferd_file.path).exists())
        eintrag = AuditLog.objects.get(metadata__anlass='finalisieren')
        self.assertEqual(eintrag.metadata['alte_nummer'], alt)
        self.assertEqual(eintrag.metadata['neue_nummer'], inv.number)
        self.assertEqual(eintrag.metadata['von'], 'entwurf')
        self.assertEqual(eintrag.metadata['nach'], 'erstellt')

    def test_f13_veraltete_python_instanz_darf_nicht_erneut_finalisieren(self):
        inv = self.draft()
        stale = Invoice.objects.get(pk=inv.pk)
        finalisiere_entwurf(inv)
        vorher = next_number('RE'), self.generated(), AuditLog.objects.count()
        with self.assertRaises(WorkflowError):
            finalisiere_entwurf(stale)
        self.assertEqual((next_number('RE'), self.generated(), AuditLog.objects.count()), vorher)

    def test_f13_zweiter_finalisierungs_post_ist_wirkungslos(self):
        inv = self.draft()
        self.assertEqual(self.confirm(inv).status_code, 302)
        before = self.snapshot(inv), next_number('RE'), self.generated()
        self.assertEqual(self.confirm(inv).status_code, 302)
        self.assertEqual((self.snapshot(inv), next_number('RE'), self.generated()), before)

    def test_f13_bestandsentwurf_behaelt_re_nummer(self):
        inv = self.draft(prefix='RE')
        vorher = inv.number, next_number('RE')
        finalisiere_entwurf(inv)
        self.assertEqual((inv.number, next_number('RE')), vorher)

    def test_f13_generator_frisch_liest_status_nach_finalisierung(self):
        inv = self.draft()
        stale = Invoice.objects.get(pk=inv.pk)
        finalisiere_entwurf(inv)
        generate_invoice_files(stale)
        self.assertEqual(stale.number, inv.number)
        self.assertFalse(invoice_to_core_row(stale).is_draft)
        self.assertTrue(stale.xml_file)

    def test_f13_finalize_get_ist_405(self):
        inv = self.draft()
        before = self.snapshot(inv)
        self.assertEqual(self.client.get(reverse('sedo:invoice_finalize', args=[inv.pk])).status_code, 405)
        self.assertEqual(self.snapshot(inv), before)

    def test_f13_finalize_braucht_login_und_csrf(self):
        inv = self.draft()
        url = reverse('sedo:invoice_finalize', args=[inv.pk])
        self.assertEqual(Client().post(url).status_code, 302)
        secure = Client(enforce_csrf_checks=True)
        secure.force_login(self.user)
        self.assertEqual(secure.post(url).status_code, 403)
        self.assertEqual(Invoice.objects.get(pk=inv.pk).status, 'entwurf')

    def test_f13_finalisierung_verschickt_keine_mail(self):
        inv = self.draft()
        with patch('sedo.views.send_email_smtp') as smtp, patch('sedo.views.create_email_draft') as eml:
            self.confirm(inv)
        smtp.assert_not_called()
        eml.assert_not_called()
        self.assertEqual(Invoice.objects.get(pk=inv.pk).status, 'erstellt')

    def test_f13_bestaetigungsseite_schreibt_nichts(self):
        inv = self.draft(issue_date=self.today.replace(day=1) - timedelta(days=1))
        before = self.snapshot(inv), next_number('RE'), AuditLog.objects.count()
        response = self.client.post(reverse('sedo:invoice_finalize', args=[inv.pk]))
        self.assertContains(response, 'Rechnungsdatum auf heute setzen')
        self.assertNotContains(response, 'checked')
        self.assertEqual((self.snapshot(inv), next_number('RE'), AuditLog.objects.count()), before)

    def test_f13_vormonat_braucht_explizite_bestaetigung(self):
        inv = self.draft(issue_date=self.today.replace(day=1) - timedelta(days=1))
        before = self.snapshot(inv), next_number('RE')
        with self.assertRaises(WorkflowError):
            finalisiere_entwurf(inv)
        self.assertEqual((self.snapshot(inv), next_number('RE')), before)

    def test_f13_vormonat_datum_bleibt_ohne_haekchen(self):
        alt = self.today.replace(day=1) - timedelta(days=1)
        inv = self.draft(issue_date=alt, due_date=alt + timedelta(days=14))
        self.confirm(inv)
        inv.refresh_from_db()
        self.assertEqual(inv.status, 'erstellt')
        self.assertEqual(inv.issue_date, alt)
        self.assertEqual(inv.due_date, alt + timedelta(days=14))

    def test_f13_datum_heute_erhaelt_zahlungsziel_und_leistungszeitraum(self):
        alt = self.today.replace(day=1) - timedelta(days=1)
        inv = self.draft(issue_date=alt, due_date=alt + timedelta(days=7), period_start=alt, period_end=alt)
        self.confirm(inv, datum_heute='1')
        inv.refresh_from_db()
        self.assertEqual(inv.issue_date, self.today)
        self.assertEqual(inv.due_date, self.today + timedelta(days=7))
        self.assertEqual((inv.period_start, inv.period_end), (alt, alt))

    def test_f63_datum_heute_erhaelt_zahlungsziel_null(self):
        alt = self.today.replace(day=1) - timedelta(days=1)
        inv = self.draft(issue_date=alt, due_date=alt)
        self.confirm(inv, datum_heute='1')
        inv.refresh_from_db()
        self.assertEqual(inv.due_date, self.today)

    def test_f13_vormonatspruefung_am_jahreswechsel(self):
        inv = self.draft(issue_date=date(2025, 12, 31))
        with patch('sedo.services.invoices.timezone.localdate', return_value=date(2026, 1, 1)):
            with self.assertRaises(WorkflowError):
                finalisiere_entwurf(inv)

    def test_f13_finalisieren_ohne_position_wird_abgewiesen(self):
        inv = Invoice.objects.create(number='EN9999010001', customer=self.customer, status='entwurf')
        with self.assertRaises(WorkflowError):
            finalisiere_entwurf(inv)
        self.assertEqual(inv.status, 'entwurf')

    def test_f13_fehlender_bestaetigungsstand_veraendert_nichts(self):
        inv = self.draft()
        before = self.snapshot(inv)
        self.confirm(inv, revision='')
        self.assertEqual(self.snapshot(inv), before)

    def test_f13_stale_revision_verhindert_finalisierung(self):
        inv = self.draft()
        revision = inv.updated_at.isoformat()
        self.client.post(reverse('sedo:invoice_edit', args=[inv.pk]), self.payload(inv))
        before = self.snapshot(inv)
        self.confirm(inv, revision=revision)
        self.assertEqual(self.snapshot(inv), before)
        self.assertEqual(Invoice.objects.get(pk=inv.pk).status, 'entwurf')

    def test_f14_bearbeiten_aendert_betraege_ohne_neue_kunden_oder_nummer(self):
        inv = self.draft()
        before = Customer.objects.count(), inv.number
        response = self.client.post(reverse('sedo:invoice_edit', args=[inv.pk]), self.payload(inv))
        self.assertEqual(response.status_code, 302)
        inv.refresh_from_db()
        self.assertEqual(inv.gross_total, Decimal('238.00'))
        self.assertEqual((Customer.objects.count(), inv.number), before)
        self.assertEqual(inv.items.first().long_text, 'Geänderte Leistung')
        self.assertFalse(inv.xml_file or inv.zugferd_file)

    def test_f63_bearbeiten_erhaelt_zahlungsziel_null(self):
        inv = self.draft()
        self.client.post(reverse('sedo:invoice_edit', args=[inv.pk]), self.payload(inv, payment_days='0'))
        inv.refresh_from_db()
        self.assertEqual(inv.due_date, inv.issue_date)

    def test_f14_edit_get_zeigt_echtes_zahlungsziel(self):
        inv = self.draft(due_date=self.today)
        before = self.snapshot(inv)
        response = self.client.get(reverse('sedo:invoice_edit', args=[inv.pk]))
        self.assertContains(response, 'Entwurf speichern')
        self.assertEqual(response.context['form']['payment_days'].value(), 0)
        self.assertEqual(self.snapshot(inv), before)

    def test_f14_leerer_kunde_ist_formularfehler_ohne_aenderung(self):
        inv = self.draft()
        before = self.snapshot(inv), Customer.objects.count()
        response = self.client.post(reverse('sedo:invoice_edit', args=[inv.pk]), self.payload(inv, customer='', name='Nicht anlegen'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('customer', response.context['form'].errors)
        self.assertEqual((self.snapshot(inv), Customer.objects.count()), before)

    def test_f14_fremdes_objekt_wird_abgewiesen(self):
        inv = self.draft()
        other = Customer.objects.create(name='Anderer Kunde')
        obj = CustomerObject.objects.create(customer=other, name='Anderes Objekt')
        before = self.snapshot(inv)
        response = self.client.post(reverse('sedo:invoice_edit', args=[inv.pk]), self.payload(inv, obj=str(obj.pk)))
        self.assertEqual(response.status_code, 200)
        self.assertIn('obj', response.context['form'].errors)
        self.assertEqual(self.snapshot(inv), before)

    def test_f14_kundenwechsel_mit_passendem_objekt(self):
        inv = self.draft()
        other = Customer.objects.create(name='Neuer Kunde')
        obj = CustomerObject.objects.create(customer=other, name='Neues Objekt')
        before = Customer.objects.count()
        response = self.client.post(reverse('sedo:invoice_edit', args=[inv.pk]), self.payload(inv, customer=str(other.pk), obj=str(obj.pk)))
        self.assertEqual(response.status_code, 302)
        inv.refresh_from_db()
        self.assertEqual((inv.customer_id, inv.obj_id), (other.pk, obj.pk))
        self.assertEqual(Customer.objects.count(), before)

    def test_f14_alte_verwerfensbestaetigung_verwirft_keine_finalisierte_rechnung(self):
        inv = self.draft()
        revision = inv.updated_at.isoformat()
        finalisiere_entwurf(inv)
        before = self.snapshot(inv)
        self.client.post(reverse('sedo:invoice_delete', args=[inv.pk]), {'invoice_revision': revision})
        self.assertEqual(self.snapshot(inv), before)

    def test_f14_belegart_und_herkunft_sind_nicht_editierbar(self):
        original = self.draft(prefix='RE', status='erstellt')
        inv = self.draft(invoice_type='384', reference_invoice=original, source='Korrektur')
        self.client.post(reverse('sedo:invoice_edit', args=[inv.pk]), self.payload(inv, invoice_type='380', status='erstellt', source='Manuell', reference_invoice=''))
        inv.refresh_from_db()
        self.assertEqual((inv.status, inv.invoice_type, inv.source, inv.reference_invoice_id), ('entwurf', '384', 'Korrektur', original.pk))

    def test_f14_edit_ausgestellter_rechnung_ist_gesperrt(self):
        inv = self.draft(status='erstellt', prefix='RE')
        before = self.snapshot(inv)
        url = reverse('sedo:invoice_edit', args=[inv.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        self.assertEqual(self.client.post(url, self.payload(inv)).status_code, 302)
        self.assertEqual(self.snapshot(inv), before)

    def test_f14_zweites_browserfenster_ueberschreibt_nicht(self):
        inv = self.draft()
        post = self.payload(inv)
        url = reverse('sedo:invoice_edit', args=[inv.pk])
        self.client.post(url, post)
        before = self.snapshot(inv)
        post['items-0-unit_price'] = '999'
        self.client.post(url, post)
        self.assertEqual(self.snapshot(inv), before)
        self.assertEqual(inv.items.first().unit_price, Decimal('100.00'))

    def test_f14_keine_leere_positionsliste(self):
        inv = self.draft()
        before = self.snapshot(inv)
        response = self.client.post(reverse('sedo:invoice_edit', args=[inv.pk]), self.payload(inv, **{'items-0-DELETE': 'on'}))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['formset'].non_form_errors())
        self.assertEqual(self.snapshot(inv), before)
        self.assertEqual(inv.items.count(), 1)

    def test_f14_positionswechsel_mit_loeschen_und_hinzufuegen(self):
        inv = self.draft()
        post = self.payload(inv, **{'items-TOTAL_FORMS': '2', 'items-0-DELETE': 'on',
                                  'items-1-description': 'Ersatz', 'items-1-quantity': '3',
                                  'items-1-unit': 'Std.', 'items-1-unit_price': '10'})
        self.client.post(reverse('sedo:invoice_edit', args=[inv.pk]), post)
        inv.refresh_from_db()
        self.assertEqual(inv.gross_total, Decimal('35.70'))
        self.assertEqual(inv.items.count(), 1)
        self.assertEqual(inv.items.get().position, 1)

    def test_f14_fremde_positions_id_wird_nicht_geaendert(self):
        inv = self.draft()
        other = self.draft()
        item = other.items.get()
        self.client.post(reverse('sedo:invoice_edit', args=[inv.pk]), self.payload(inv, **{'items-0-id': str(item.pk), 'items-0-unit_price': '999'}))
        item.refresh_from_db()
        self.assertEqual(item.unit_price, Decimal('100.00'))

    def test_f14_korrekturentwurf_finalisiert_original(self):
        original = self.draft(prefix='RE', status='erstellt')
        inv = self.draft(invoice_type='384', reference_invoice=original)
        finalisiere_entwurf(inv)
        original.refresh_from_db()
        self.assertEqual(original.status, 'korrigiert')
        self.assertEqual(original.active_correction.pk, inv.pk)
        self.assertTrue(AuditLog.objects.filter(metadata__anlass='korrektur', metadata__von='erstellt', metadata__nach='korrigiert').exists())

    def test_f14_zwischenzeitliches_storno_blockiert_korrektur_finalisieren(self):
        original = self.draft(prefix='RE', status='erstellt')
        inv = self.draft(invoice_type='384', reference_invoice=original)
        create_storno(original)
        before = self.snapshot(inv), next_number('RE'), self.generated(), AuditLog.objects.count()
        with self.assertRaises(WorkflowError):
            finalisiere_entwurf(inv)
        self.assertEqual((self.snapshot(inv), next_number('RE'), self.generated(), AuditLog.objects.count()), before)

    def test_f14_storno_graph_blockiert_auch_bei_falschem_originalstatus(self):
        original = self.draft(prefix='RE', status='erstellt')
        inv = self.draft(invoice_type='384', reference_invoice=original)
        create_storno(original)
        Invoice.objects.filter(pk=original.pk).update(status='ueberfaellig')
        with self.assertRaises(WorkflowError):
            finalisiere_entwurf(inv)
        self.assertEqual(inv.status, 'entwurf')

    def test_f14_ausgeblendetes_original_blockiert_finalisierung(self):
        original = self.draft(prefix='RE', status='erstellt')
        inv = self.draft(invoice_type='384', reference_invoice=original)
        original.soft_delete()
        with self.assertRaises(WorkflowError):
            finalisiere_entwurf(inv)
        self.assertEqual(inv.status, 'entwurf')

    def linked_offer(self):
        offer = Offer.objects.create(number='AG2099010001', customer=self.customer, status='angenommen')
        response = self.client.post(reverse('sedo:invoice_create'), self.payload(source_offer=str(offer.pk)))
        self.assertEqual(response.status_code, 302)
        offer.refresh_from_db()
        return offer, offer.linked_invoice

    def test_f14_angebot_nach_entwurf_verwerfen_wieder_frei(self):
        offer, inv = self.linked_offer()
        before = AuditLog.objects.count()
        self.client.post(reverse('sedo:invoice_delete', args=[inv.pk]))
        offer.refresh_from_db()
        inv.refresh_from_db()
        self.assertIsNone(offer.linked_invoice_id)
        self.assertEqual(offer.status, 'angenommen')
        self.assertIsNotNone(inv.deleted_at)
        ensure_offer_invoicing_allowed(offer)
        entries = AuditLog.objects.filter(metadata__anlass='entwurf_verwerfen')
        self.assertEqual(AuditLog.objects.count() - before, 2)
        self.assertEqual(entries.count(), 2)
        for entry in entries:
            self.assertIn(inv.number, entry.message)
            self.assertIn(offer.number, entry.message)

    def test_f14_zweites_pflichtaudit_rollback_des_gesamten_verwerfens(self):
        offer, inv = self.linked_offer()
        before = self.snapshot(inv), AuditLog.objects.count()
        def failing(request, action, obj=None, *args, **kwargs):
            if isinstance(obj, Offer):
                raise AuditWriteError('Angebotsprotokoll nicht verfügbar')
            return write_audit(request, action, obj, *args, **kwargs)
        with patch('sedo.views.write_audit', side_effect=failing):
            response = self.client.post(reverse('sedo:invoice_delete', args=[inv.pk]))
        self.assertEqual(response.status_code, 302)
        offer.refresh_from_db()
        self.assertEqual((self.snapshot(inv), AuditLog.objects.count()), before)
        self.assertEqual((offer.status, offer.linked_invoice_id), ('in_rechnung', inv.pk))

    def test_f14_verworfenes_entwurf_laesst_sich_nicht_finalisieren(self):
        inv = self.draft()
        inv.soft_delete()
        with self.assertRaises(WorkflowError):
            finalisiere_entwurf(inv)
        self.assertFalse(NumberRange.objects.filter(prefix='RE').exists())

    def test_f13_pflichtauditfehler_rollt_nummer_status_und_dateien_zurueck(self):
        inv = self.draft()
        generate_invoice_files(inv)
        before = self.snapshot(inv), self.generated(), next_number('RE')
        with patch('sedo.models.AuditLog.objects.create', side_effect=RuntimeError('Audit absichtlich defekt')):
            with self.assertRaises(AuditWriteError):
                finalisiere_entwurf(inv)
        self.assertEqual((self.snapshot(inv), self.generated(), next_number('RE')), before)
        self.assertEqual(inv.status, 'entwurf')

    def test_f13_einbettungsfehler_rollt_finalisierung_zurueck(self):
        inv = self.draft()
        generate_invoice_files(inv)
        before = self.snapshot(inv), self.generated(), next_number('RE'), AuditLog.objects.count()
        with patch('sedo.services.invoices.embed_xml_into_pdf', side_effect=RuntimeError('Einbettung defekt')):
            with self.assertRaises(RuntimeError):
                finalisiere_entwurf(inv)
        self.assertEqual((self.snapshot(inv), self.generated(), next_number('RE'), AuditLog.objects.count()), before)

    def test_f13_fehler_nach_dateiveroeffentlichung_entfernt_neue_xml_aus_medien(self):
        inv = self.draft()
        generate_invoice_files(inv)
        before = self.snapshot(inv), self.generated(), next_number('RE'), AuditLog.objects.count()
        real = generate_invoice_files
        def fail_after(invoice):
            real(invoice)
            raise RuntimeError('Fehler nach Veröffentlichung')
        with patch('sedo.services.invoices.generate_invoice_files', side_effect=fail_after):
            with self.assertRaises(RuntimeError):
                finalisiere_entwurf(inv)
        self.assertEqual((self.snapshot(inv), self.generated(), next_number('RE'), AuditLog.objects.count()), before)
        self.assertTrue(list((Path(self.tmp.name) / '.media_quarantaene').rglob('factur-x.xml')))

    def test_f13_re_altentwurf_dateirueckweg_stellt_pdf_wieder_her(self):
        inv = self.draft(prefix='RE')
        generate_invoice_files(inv)
        before = self.generated()
        real = generate_invoice_files
        def fail_after(invoice):
            real(invoice)
            raise RuntimeError('Nach Dateischreiben')
        with patch('sedo.services.invoices.generate_invoice_files', side_effect=fail_after):
            with self.assertRaises(RuntimeError):
                finalisiere_entwurf(inv)
        self.assertEqual(self.generated(), before)

    def test_f14_bearbeiten_dateifehler_rollt_kopf_positionen_und_audit_zurueck(self):
        inv = self.draft()
        generate_invoice_files(inv)
        before = self.snapshot(inv), self.generated(), AuditLog.objects.count()
        with patch('sedo.views.generate_invoice_files', side_effect=RuntimeError('PDF defekt')):
            self.client.post(reverse('sedo:invoice_edit', args=[inv.pk]), self.payload(inv))
        self.assertEqual((self.snapshot(inv), self.generated(), AuditLog.objects.count()), before)
        self.assertEqual(inv.items.get().quantity, Decimal('1'))

    def test_f13_korrekturfehler_rollt_auch_original_zurueck(self):
        original = self.draft(prefix='RE', status='erstellt')
        inv = self.draft(invoice_type='384', reference_invoice=original)
        before = self.snapshot(original), AuditLog.objects.count()
        with patch('sedo.services.invoices.build_xml', side_effect=RuntimeError('XML defekt')):
            with self.assertRaises(RuntimeError):
                finalisiere_entwurf(inv)
        self.assertEqual((self.snapshot(original), AuditLog.objects.count()), before)
        self.assertEqual(inv.status, 'entwurf')

    def test_f14_status_audit_fuer_storno_und_korrektur(self):
        a = self.draft(prefix='RE', status='erstellt')
        b = self.draft(prefix='RE', status='erstellt')
        create_storno(a)
        mark_invoice_corrected(b)
        for anlass, nach in [('storno', 'storniert'), ('korrektur', 'korrigiert')]:
            self.assertTrue(AuditLog.objects.filter(metadata__anlass=anlass, metadata__von='erstellt', metadata__nach=nach).exists())

    def test_f14_offene_posten_get_erzeugt_keine_status_audits(self):
        for _ in range(20):
            self.draft(prefix='RE', status='erstellt')
        before = AuditLog.objects.count()
        self.client.get(reverse('sedo:open_items'))
        self.assertEqual(AuditLog.objects.count(), before)

    def test_f13_monatsabschluss_schliesst_entwurf_aus(self):
        inv = self.draft(prefix='RE', status='erstellt')
        generate_invoice_files(inv)
        draft = self.draft()
        generate_invoice_files(draft)
        response = self.client.get(reverse('sedo:month_close'), {'month': self.today.strftime('%Y-%m')})
        self.assertEqual(response.context['invoice_count'], 1)
        self.assertEqual(response.context['gross_total'], Decimal('119.00'))
        self.client.post(reverse('sedo:month_close'), {'month': self.today.strftime('%Y-%m'), 'action': 'export_tax_email'})
        zips = list(Path(settings.MEDIA_ROOT).rglob('*Steuerberater_PDF.zip'))
        self.assertEqual(len(zips), 1)
        with zipfile.ZipFile(zips[0]) as zf:
            self.assertTrue(any(inv.number in name for name in zf.namelist()))
            self.assertFalse(any(draft.number in name for name in zf.namelist()))

    def test_f13_bankimport_en_und_re_entwurf_bleiben_unzugeordnet(self):
        for prefix in ('EN', 'RE'):
            inv = self.draft(prefix=prefix)
            result = import_bank_json([{'date': self.today.isoformat(), 'amount': '119.00', 'purpose': inv.number, 'id': inv.number}])
            self.assertEqual(result['imported'], 1)
            self.assertEqual(result['matched'], 0)
            self.assertIsNone(Payment.objects.get(external_id=inv.number).invoice_id)


class DraftConcurrencyTests(TransactionTestCase):
    """Zwei echte DB-Verbindungen, nicht zwei aufeinanderfolgende Doppelklicks."""
    def test_f13_zwei_parallel_finalisierungen_erzeugen_nur_eine_rechnung(self):
        customer = Customer.objects.create(name='Paralleltest')
        inv = Invoice(customer=customer, status='entwurf')
        save_with_unique_number(inv, 'EN')
        InvoiceItem.objects.create(invoice=inv, description='Leistung', quantity=1, unit_price=10)
        stale = Invoice.objects.get(pk=inv.pk)
        reached, release = threading.Event(), threading.Event()
        results = []
        real = generate_invoice_files
        def hold(invoice):
            reached.set()
            if not release.wait(10):
                raise RuntimeError('Paralleltest: Freigabe fehlt')
            return real(invoice)
        def worker():
            close_old_connections()
            try:
                finalisiere_entwurf(stale)
                results.append('ok')
            except Exception as exc:
                results.append(exc)
            finally:
                close_old_connections()
        with tempfile.TemporaryDirectory(prefix='sedo_parallel_') as tmp, override_settings(MEDIA_ROOT=Path(tmp) / 'media'):
            with patch('sedo.services.invoices.generate_invoice_files', side_effect=hold):
                thread = threading.Thread(target=worker)
                thread.start()
                try:
                    self.assertTrue(reached.wait(10), 'Der erste Request hat die Schreibsperre nicht erreicht.')
                    with self.assertRaises((WorkflowError, OperationalError)):
                        finalisiere_entwurf(inv)
                finally:
                    release.set()
                    thread.join(10)
                self.assertFalse(thread.is_alive())
        self.assertEqual(results, ['ok'])
        inv.refresh_from_db()
        self.assertEqual(inv.status, 'erstellt')
        self.assertTrue(inv.number.startswith('RE'))
        self.assertEqual(NumberRange.objects.get(prefix='RE').next_number, 2)
        self.assertEqual(AuditLog.objects.filter(metadata__anlass='finalisieren').count(), 1)
