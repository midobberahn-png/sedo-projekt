"""Freigegebene Härtung: explizite Abo-Auswahl und tatsächliche Mahnfälligkeit.

Nur eigene synthetische Datensätze/Medien. Ein gesetzter Status ist kein Ersatz
für eine positive, überfällige Restforderung. Keine neue Mahnstufenpolitik.
"""
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from lxml import html

from sedo.models import (AuditLog, Customer, DunningDocument, Invoice, InvoiceItem,
                         NumberRange, Payment, RecurringInvoiceItem, RecurringInvoiceTemplate)
from sedo.services.dunning import (WorkflowError, ensure_dunning_allowed, generate_dunning_document,
                                   get_dunning_candidates, mahn_hindernis, next_dunning_kind, send_bulk_dunning)
from .file_fixtures import isolate_files


class IsolatedBusinessTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('haertung', password='nur-test')

    def setUp(self):
        isolate_files(self)
        self.client.force_login(self.user)
        self.customer = Customer.objects.create(name='TEST Härtung', street='Testweg 1', postcode='10115', city='Teststadt')
        self.today = timezone.localdate()

    def business_state(self):
        tables = (Invoice.all_objects, InvoiceItem.objects, NumberRange.objects,
                  AuditLog.objects, DunningDocument.objects, Payment.all_objects)
        root = Path(settings.MEDIA_ROOT)
        return ([list(q.order_by('pk').values()) for q in tables],
                {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
                 for p in root.rglob('*') if p.is_file()})


class RecurringSelectionHardeningTests(IsolatedBusinessTest):
    def setUp(self):
        super().setUp()
        self.url = reverse('sedo:recurring_generate')
        self.a = self.template('A')
        self.b = self.template('B')
        self.paused = self.template('Pause', status='paused')

    def template(self, name, **kwargs):
        t = RecurringInvoiceTemplate.objects.create(customer=self.customer, title=name, **kwargs)
        RecurringInvoiceItem.objects.create(template=t, description='TEST Leistung', unit_price=100)
        return t

    def post(self, ids=None, **extra):
        data = {'month': self.today.strftime('%Y-%m'), 'issue_date': self.today.isoformat()}
        if ids is not None:
            data['template_ids'] = ids
        data.update(extra)
        return self.client.post(self.url, data)

    def assert_rejected(self, ids=None, **extra):
        before = self.business_state()
        with patch('sedo.views._generate_recurring_invoice_from_template',
                   side_effect=AssertionError('Generator darf bei abgewiesener Auswahl nicht starten')) as create:
            response = self.post(ids, **extra)
            create.assert_not_called()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.business_state(), before)
        self.assertTrue(any(m.level_tag == 'error' for m in get_messages(response.wsgi_request)))

    def test_leere_auswahl_ist_kein_gesamtlauf(self):
        self.assert_rejected([])

    def test_fehlende_auswahl_ist_kein_gesamtlauf(self):
        self.assert_rejected()

    def test_all_flag_umgeht_explizite_auswahl_nicht(self):
        self.assert_rejected([], all='1', action='all')

    def test_pausierte_oder_fehlende_vorlage_lehnt_ganze_auswahl_ab(self):
        for pk in (self.paused.pk, 987654321):
            with self.subTest(pk=pk):
                self.assert_rejected([self.a.pk, pk])

    def test_manipulierte_ids_lehnen_ganze_auswahl_ab(self):
        for value in ('', 'abc', '1.0', '0', '-1', '9'*40, "1 OR 1=1"):
            with self.subTest(value=value):
                self.assert_rejected([self.a.pk, value])

    def test_inzwischen_pausierte_auswahl_wird_abgewiesen(self):
        RecurringInvoiceTemplate.objects.filter(pk=self.a.pk).update(status='paused')
        self.assert_rejected([self.a.pk])

    def test_einzelwahl_erzeugt_nur_gewaehlte_rechnung_ohne_javascript(self):
        response = self.post([self.a.pk])
        self.assertEqual(response.status_code, 302)
        inv = Invoice.objects.get()
        self.assertIn('„A“', inv.note)
        self.assertTrue(inv.xml_file)
        self.assertEqual(len(self.client.session['last_recurring_invoice_ids']), 1)

    def test_mehrfach_genannte_id_wird_nur_einmal_verarbeitet(self):
        self.post([self.a.pk, self.a.pk])
        self.assertEqual(Invoice.objects.count(), 1)

    def test_alle_aktiven_ids_werden_explizit_verarbeitet(self):
        self.post([self.a.pk, self.b.pk])
        self.assertEqual(Invoice.objects.count(), 2)
        self.assertFalse(Invoice.objects.filter(note__contains='Pause').exists())

    def test_neue_vorlage_erweitert_alte_auswahl_nicht(self):
        self.template('Nachgeladen')
        self.post([self.a.pk, self.b.pk])
        self.assertEqual(Invoice.objects.count(), 2)

    def test_oberflaeche_hat_keine_vorauswahl_und_sperrt_pause(self):
        response = self.client.get(reverse('sedo:recurring_monthly'))
        page = html.fromstring(response.content)
        boxes = page.xpath('//input[@name="template_ids"]')
        self.assertEqual(len(boxes), 3)
        self.assertTrue(all('checked' not in b.attrib for b in boxes))
        paused, = [b for b in boxes if b.get('value') == str(self.paused.pk)]
        self.assertIn('disabled', paused.attrib)
        button, = page.xpath('//button[@data-recurring-select-all]')
        self.assertEqual(button.get('type'), 'button')
        self.assertContains(response, 'recurring_selection.js')

    def test_ohne_login_und_ohne_csrf_keine_erstellung(self):
        self.client.logout()
        before = self.business_state()
        self.assertEqual(self.post([self.a.pk]).status_code, 302)
        self.assertEqual(self.business_state(), before)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        before = self.business_state()  # Login/Logout haben eigene legitime Audits.
        self.assertEqual(client.post(self.url, {'template_ids': [self.a.pk]}).status_code, 403)
        self.assertEqual(self.business_state(), before)

    def test_get_ist_weiterhin_nicht_schreibend(self):
        before = self.business_state()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.assertEqual(self.business_state(), before)


class DunningDueHardeningTests(IsolatedBusinessTest):
    def setUp(self):
        super().setUp()
        self.inv = Invoice.objects.create(customer=self.customer, number='RE-TEST-F68',
            status='erstellt', gross_total=119, due_date=self.today-timedelta(days=2))

    def assert_blocked(self, reason):
        before = self.business_state()
        self.assertIn(reason, mahn_hindernis(self.inv))
        self.assertIsNone(next_dunning_kind(self.inv))
        for kind in ('reminder', 'dunning', 'auto'):
            with patch('sedo.services.dunning._render_dunning_pdf') as pdf:
                with self.assertRaises(WorkflowError):
                    generate_dunning_document(self.inv, kind)
                pdf.assert_not_called()
            if kind != 'auto':
                with self.assertRaises(WorkflowError):
                    ensure_dunning_allowed(self.inv, kind)
        self.assertEqual(self.business_state(), before)

    def test_zukuenftige_rechnung_ist_auch_manuell_gesperrt(self):
        Invoice.objects.filter(pk=self.inv.pk).update(due_date=self.today+timedelta(days=1))
        self.assert_blocked('nicht überfällig')

    def test_faelligkeitstag_bleibt_zahlungstag(self):
        Invoice.objects.filter(pk=self.inv.pk).update(due_date=self.today)
        self.assert_blocked('nicht überfällig')
        self.assertEqual(get_dunning_candidates(0), [])

    def test_ohne_faelligkeitsdatum_keine_erfundene_frist(self):
        Invoice.objects.filter(pk=self.inv.pk).update(due_date=None)
        self.assert_blocked('kein Fälligkeitsdatum')

    def test_vollbezahlt_trotz_altem_status_gesperrt(self):
        Payment.objects.create(invoice=self.inv, amount=119, booking_date=self.today)
        self.assert_blocked('keinen positiven offenen Betrag')

    def test_ueberbezahlt_gesperrt(self):
        Payment.objects.create(invoice=self.inv, amount=120, booking_date=self.today)
        self.assert_blocked('keinen positiven offenen Betrag')

    def test_null_und_negative_forderungen_gesperrt(self):
        for amount in (0, -1):
            with self.subTest(amount=amount):
                Invoice.objects.filter(pk=self.inv.pk).update(gross_total=amount)
                self.assert_blocked('keinen positiven offenen Betrag')

    def test_teilbezahlt_nur_tatsaechlicher_restbetrag(self):
        Payment.objects.create(invoice=self.inv, amount=19, booking_date=self.today)
        doc = generate_dunning_document(self.inv, 'reminder')
        self.assertEqual(doc.amount_open, Decimal('100.00'))
        self.assertTrue(Path(doc.pdf_file.path).is_file())
        self.assertEqual(AuditLog.objects.filter(object_type='DunningDocument').count(), 1)

    def test_ausgeblendete_zahlung_gleicht_nicht_aus(self):
        Payment.objects.create(invoice=self.inv, amount=119, booking_date=self.today, deleted_at=timezone.now())
        self.assertEqual(generate_dunning_document(self.inv).amount_open, Decimal('119.00'))

    def test_vortag_manuell_erlaubt_automatikschwelle_bleibt(self):
        Invoice.objects.filter(pk=self.inv.pk).update(due_date=self.today-timedelta(days=1))
        self.assertEqual(get_dunning_candidates(), [])
        self.assertEqual(next_dunning_kind(self.inv), 'reminder')
        self.assertEqual(generate_dunning_document(self.inv, 'dunning').kind, 'dunning')

    def test_heute_parameter_der_stufenabfrage_wird_beachtet(self):
        self.assertIsNone(next_dunning_kind(self.inv, today=self.inv.due_date))
        self.assertEqual(next_dunning_kind(self.inv, today=self.inv.due_date+timedelta(days=1)), 'reminder')

    def test_alter_cache_und_zahlung_nach_vorpruefung_sind_keine_freigabe(self):
        old = Invoice.objects.prefetch_related('payments').get(pk=self.inv.pk)
        ensure_dunning_allowed(old, 'reminder')
        Payment.objects.create(invoice=self.inv, amount=119, booking_date=self.today)
        before = self.business_state()
        with self.assertRaises(WorkflowError):
            generate_dunning_document(old)
        self.assertEqual(self.business_state(), before)

    def test_post_sperrt_und_detailseite_erklaert(self):
        Invoice.objects.filter(pk=self.inv.pk).update(due_date=self.today)
        before = self.business_state()
        for kind in ('reminder', 'dunning'):
            response = self.client.post(reverse('sedo:invoice_reminder', args=[self.inv.pk, kind]))
            self.assertEqual(response.status_code, 302)
            self.assertTrue(any('nicht überfällig' in str(m) for m in get_messages(response.wsgi_request)))
        page = self.client.get(reverse('sedo:invoice_detail', args=[self.inv.pk]))
        self.assertContains(page, 'nicht überfällig')
        self.assertEqual(self.business_state(), before)

    def test_sammellauf_ueberspringt_unfaellige_und_ausgeglichene(self):
        paid = Invoice.objects.create(customer=self.customer, number='RE-PAID', gross_total=10,
                                      due_date=self.today-timedelta(days=30))
        Payment.objects.create(invoice=paid, amount=10, booking_date=self.today)
        future = Invoice.objects.create(customer=self.customer, number='RE-FUTURE', gross_total=10,
                                        due_date=self.today+timedelta(days=1))
        self.assertEqual(send_bulk_dunning([paid.pk, future.pk, self.inv.pk], 'reminder'),
                         {'created': 1, 'skipped': 2})
        self.assertEqual(DunningDocument.objects.get().invoice_id, self.inv.pk)

    def test_generator_prueft_restbetrag_ohne_vorab_guard(self):
        Payment.objects.create(invoice=self.inv, amount=119, booking_date=self.today)
        before = self.business_state()
        with self.assertRaises(WorkflowError):
            generate_dunning_document(self.inv, 'dunning')
        self.assertEqual(self.business_state(), before)
