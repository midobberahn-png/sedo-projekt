"""Monatslauf nach Trennung von HTTP-Eingabe und Rechnungserzeugung."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from sedo.company_profile import save_profile
from sedo.models import AareonMapping, Customer, CustomerObject, Invoice
from sedo.services.invoice_archive import archivnachweis
from sedo.services.monthly_invoices import generate_gwg_monthly
from sedo.tests.company_fixtures import SYNTHETIC_PROFILE
from .file_fixtures import isolate_files


class MonthlyInvoiceTests(TestCase):
    def setUp(self):
        isolate_files(self)
        save_profile(SYNTHETIC_PROFILE)
        self.user = get_user_model().objects.create_user('monatstest')
        self.client.force_login(self.user)
        self.customer = Customer.objects.create(
            name='Synthetischer Monatskunde', street='Testweg 1',
            postcode='12345', city='Teststadt', country='DE',
        )
        AareonMapping.objects.create(
            billing_unit_sender='Testhaus 7', service_id='Reinigung',
            nksl_internal='TEST-NKSL',
        )

    def _run(self):
        return generate_gwg_monthly(
            customer=self.customer, month='2026-09',
            issue_date=date(2026, 10, 1), due_date=date(2026, 10, 15),
            period_start=date(2026, 9, 1), period_end=date(2026, 9, 30),
            services=[{'id': 'Reinigung', 'description': 'Test Reinigung',
                       'quantity': Decimal('2'), 'unit': 'Stk.',
                       'unit_price': Decimal('50')}],
            skip_existing=True, limit=None, request=None,
        )

    def test_erzeugung_und_wiederholung(self):
        first = self._run()
        self.assertEqual((first['created'], first['skipped'], first['missing']), (1, 0, 0))
        invoice = Invoice.objects.get(pk=first['invoice_ids'][0])
        self.assertEqual(invoice.gross_total, Decimal('119.00'))
        self.assertIsNotNone(archivnachweis(invoice))
        second = self._run()
        self.assertEqual((second['created'], second['skipped']), (0, 1))
        self.assertEqual(Invoice.objects.count(), 1)

    def test_dateifehler_rollt_auch_neues_objekt_zurueck(self):
        with patch('sedo.services.monthly_invoices.generate_invoice_files',
                   side_effect=RuntimeError('Synthetischer Dokumentfehler')):
            result = self._run()
        self.assertEqual(result['created'], 0)
        self.assertEqual(len(result['errors']), 1)
        self.assertEqual(Invoice.objects.count(), 0)
        self.assertEqual(CustomerObject.objects.count(), 0)

    def test_ungueltiger_monat_wird_ohne_rechnung_gemeldet(self):
        response = self.client.post(reverse('sedo:aareon_monthly'), {
            'action': 'generate', 'customer': self.customer.pk,
            'month': '2026-13', 'issue_date': '2026-10-01',
            'use_Reinigung': '1', 'qty_Reinigung': '2', 'price_Reinigung': '50',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Invoice.objects.count(), 0)
        self.assertIn('Monatslauf abgebrochen',
                      ' '.join(str(message) for message in response.wsgi_request._messages))
