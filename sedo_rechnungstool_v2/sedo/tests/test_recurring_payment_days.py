"""F-63 im Abo: 0 Tage bleiben erhalten, Rueckfall nur bei None.

Persistierte Zahlungsziele sind nicht nullable. Die None-Faelle pruefen den
bestehenden defensiven Rueckfall an reinen Objekten, nicht mit ungueltigen DB-Daten.
Alle Rechnungen und Medien sind synthetisch und pro Test isoliert.
"""
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from lxml import etree

from sedo.forms import RecurringInvoiceTemplateForm
from sedo.models import Customer, Invoice, InvoiceItem, NumberRange, RecurringInvoiceItem, RecurringInvoiceTemplate
from sedo.services.invoice_archive import archivnachweis
from sedo.views import _create_recurring_template_from_invoice, _recurring_payment_days
from .file_fixtures import isolate_files


class RecurringPaymentFallbackTests(SimpleTestCase):
    def test_vorlagenwert_hat_vorrang_auch_null(self):
        for primary, customer in ((0, 14), (0, 0), (7, 0), (30, 14)):
            with self.subTest(primary=primary, customer=customer):
                self.assertEqual(_recurring_payment_days(primary, customer), primary)

    def test_fehlender_vorlagenwert_nimmt_kundenwert_auch_null(self):
        for customer in (0, 21):
            with self.subTest(customer=customer):
                self.assertEqual(_recurring_payment_days(None, customer), customer)

    def test_beide_fehlend_nimmt_konfigurierte_konstante(self):
        with patch('sedo.views.DEFAULT_PAYMENT_DAYS', 9):
            self.assertEqual(_recurring_payment_days(None, None), 9)


class RecurringPaymentDaysTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('abo-zahlungsziel', password='synthetisch')

    def setUp(self):
        isolate_files(self)
        self.client.force_login(self.user)
        self.customer = Customer.objects.create(name='TEST Abo Zahlungsziel', payment_days=14)
        self.issue = date(2026, 9, 30)
        self.url = reverse('sedo:recurring_generate')
        self.n = 0

    def template(self, days):
        self.n += 1
        tmpl = RecurringInvoiceTemplate.objects.create(customer=self.customer, title=f'TEST Abo {self.n}', payment_days=days)
        RecurringInvoiceItem.objects.create(template=tmpl, description='TEST Reinigung',
            long_text='TEST Leistungsumfang', quantity=2, unit='Monat', unit_price=100)
        return tmpl

    def generate(self, templates, issue=None, month='2026-09', skip=False):
        data = {'issue_date': (issue or self.issue).isoformat(), 'month': month,
                'template_ids': [str(t.pk) for t in templates]}
        if skip:
            data['skip_existing'] = '1'
        response = self.client.post(self.url, data)
        self.assertRedirects(response, reverse('sedo:recurring_monthly'), fetch_redirect_response=False)
        ids = self.client.session['last_recurring_invoice_ids']
        return list(Invoice.objects.filter(pk__in=ids).order_by('pk'))

    def assert_due_in_xml(self, inv, expected):
        with inv.xml_file.open('rb') as file:
            xml = etree.fromstring(file.read())
        values = xml.xpath('//*[local-name()="SpecifiedTradePaymentTerms"]/*[local-name()="DueDateDateTime"]/*[local-name()="DateTimeString"]/text()')
        self.assertEqual(values, [expected.strftime('%Y%m%d')])
        self.assertIsNotNone(archivnachweis(inv))

    def test_monatslauf_null_bleibt_sofort_faellig(self):
        tmpl = self.template(0)
        inv, = self.generate([tmpl])
        self.assertEqual(inv.due_date, self.issue)
        self.assert_due_in_xml(inv, self.issue)
        self.assertEqual(inv.gross_total, Decimal('238.00'))
        self.assertEqual(inv.items.get().long_text, 'TEST Leistungsumfang')
        self.customer.refresh_from_db()
        tmpl.refresh_from_db()
        self.assertEqual((self.customer.payment_days, tmpl.payment_days), (14, 0))

    def test_positive_vorlage_hat_vorrang_vor_kunde_null(self):
        self.customer.payment_days = 0
        self.customer.save(update_fields=['payment_days'])
        inv, = self.generate([self.template(7)])
        self.assertEqual(inv.due_date, self.issue + timedelta(days=7))
        self.assert_due_in_xml(inv, inv.due_date)

    def test_positive_vorlage_bleibt_unveraendert(self):
        inv, = self.generate([self.template(21)])
        self.assertEqual(inv.due_date, date(2026, 10, 21))

    def test_faelligkeit_basiert_auf_rechnungsdatum_nicht_leistungsmonat(self):
        inv, = self.generate([self.template(0)], month='2026-08')
        self.assertEqual((inv.period_start, inv.period_end), (date(2026, 8, 1), date(2026, 8, 31)))
        self.assertEqual(inv.issue_date, self.issue)
        self.assertEqual(inv.due_date, self.issue)
        self.assertTrue(inv.number.startswith('RE202609'))

    def test_jahreswechsel_behaelt_positive_tage(self):
        inv, = self.generate([self.template(2)], issue=date(2026, 12, 31), month='2026-12')
        self.assertEqual(inv.due_date, date(2027, 1, 2))
        self.assert_due_in_xml(inv, inv.due_date)

    def test_gemischter_sammellauf_berechnet_jede_vorlage_einzeln(self):
        first, second = self.generate([self.template(0), self.template(10)])
        self.assertEqual((first.due_date, second.due_date), (self.issue, self.issue + timedelta(days=10)))
        self.assertNotEqual(first.number, second.number)

    def test_ueberspringen_bestehender_rechnung_bleibt_erhalten(self):
        tmpl = self.template(0)
        inv, = self.generate([tmpl], skip=True)
        before = (inv.due_date, inv.updated_at, Invoice.all_objects.count())
        self.assertEqual(self.generate([tmpl], skip=True), [])
        inv.refresh_from_db()
        self.assertEqual((inv.due_date, inv.updated_at, Invoice.all_objects.count()), before)

    def test_get_erzeugt_keine_rechnungen(self):
        self.template(0)
        self.client.get(self.url)
        self.assertEqual(Invoice.all_objects.count(), 0)

    def test_pdf_fehler_rollt_auch_sofort_faellige_abo_rechnung_zurueck(self):
        tmpl = self.template(0)
        before = list(NumberRange.objects.values())
        with patch('sedo.services.invoices.create_pdf', side_effect=OSError('TEST PDF-Fehler')):
            self.assertEqual(self.generate([tmpl]), [])
        self.assertEqual(Invoice.all_objects.count(), 0)
        self.assertEqual(InvoiceItem.objects.count(), 0)
        self.assertEqual(list(NumberRange.objects.values()), before)
        tmpl.refresh_from_db()
        self.assertEqual(tmpl.payment_days, 0)

    def test_formular_erlaubt_null_tage_aber_keinen_fehlenden_wert(self):
        data = {'customer': self.customer.pk, 'title': 'TEST Null', 'status': 'active',
                'vat_rate': '19', 'payment_days': '0'}
        form = RecurringInvoiceTemplateForm(data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().payment_days, 0)
        for value in ('', '-1'):
            with self.subTest(value=value):
                form = RecurringInvoiceTemplateForm(dict(data, payment_days=value))
                self.assertFalse(form.is_valid())
                self.assertIn('payment_days', form.errors)

    def source_invoice(self, due):
        inv = Invoice.objects.create(number='RE-TEST-ABO-QUELLE', customer=self.customer,
                                     issue_date=self.issue, due_date=due)
        InvoiceItem.objects.create(invoice=inv, description='TEST Quelle', unit_price=100)
        return inv

    def test_vorlage_ohne_rechnungsfaelligkeit_uebernimmt_kunde_null(self):
        self.customer.payment_days = 0
        self.customer.save(update_fields=['payment_days'])
        inv = self.source_invoice(None)
        tmpl = _create_recurring_template_from_invoice(inv)
        self.assertEqual(tmpl.payment_days, 0)
        inv.refresh_from_db()
        self.assertIsNone(inv.due_date)

    def test_vorlage_uebernimmt_rechnungsabstand_null_vor_kundenwert(self):
        tmpl = _create_recurring_template_from_invoice(self.source_invoice(self.issue))
        self.assertEqual(tmpl.payment_days, 0)

    def test_vorlage_uebernimmt_positiven_rechnungsabstand_vor_kunde_null(self):
        self.customer.payment_days = 0
        self.customer.save(update_fields=['payment_days'])
        tmpl = _create_recurring_template_from_invoice(self.source_invoice(self.issue + timedelta(days=7)))
        self.assertEqual(tmpl.payment_days, 7)

    def test_vorlage_ohne_faelligkeit_uebernimmt_positiven_kundenwert(self):
        tmpl = _create_recurring_template_from_invoice(self.source_invoice(None))
        self.assertEqual(tmpl.payment_days, 14)

    def test_rundlauf_rechnung_vorlage_monatslauf_behaelt_null(self):
        tmpl = _create_recurring_template_from_invoice(self.source_invoice(self.issue))
        inv, = self.generate([tmpl])
        self.assertEqual(inv.due_date, self.issue)
        self.assert_due_in_xml(inv, self.issue)

    def fallback_from_view(self, customer_days, expected):
        # Beide DB-Felder verbieten NULL. Nur fuer den defensiven Zweig ein
        # ungespeichertes Testobjekt einspeisen; die echte View berechnet das Datum.
        tmpl = SimpleNamespace(pk=1, title='TEST None', payment_days=None,
                               customer=SimpleNamespace(payment_days=customer_days))
        with patch('sedo.views.RecurringInvoiceTemplate.objects.filter') as query, \
             patch('sedo.views._generate_recurring_invoice_from_template') as create:
            query.return_value.select_related.return_value = [tmpl]
            create.return_value = SimpleNamespace(id=123)
            self.client.post(self.url, {'month': '2026-09', 'issue_date': self.issue.isoformat(), 'template_ids': ['1']})
            create.assert_called_once_with(tmpl, self.issue, date(2026, 9, 1), date(2026, 9, 30),
                                           self.issue + timedelta(days=expected))

    def test_monatslauf_fehlende_vorlagentage_kundentage_null(self):
        self.fallback_from_view(0, 0)

    def test_monatslauf_fehlende_vorlagentage_kundentage_positiv(self):
        self.fallback_from_view(21, 21)

    def test_monatslauf_beide_fehlend_verwendet_standard(self):
        with patch('sedo.views.DEFAULT_PAYMENT_DAYS', 9):
            self.fallback_from_view(None, 9)
