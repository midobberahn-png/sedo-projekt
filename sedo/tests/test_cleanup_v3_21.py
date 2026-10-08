"""Tests für die Bereinigung/Härtung v1.0 beta 3.21.0.

Deckt genau die Stellen ab, die vorher unbemerkt fehlerhaft waren:
Einheiten-Code (BT-130), long_text-Übertragung, Pflichtfeld BT-1, GoBD-Soft-Delete,
kollisionssichere Nummern, exakter Zahlungsabgleich und POST-Pflicht.
"""
from datetime import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from lxml import etree

from sedo.core.rechnungslauf_core import (
    D, InvoicePosition, InvoiceRow, build_xml, clone_position, unece_unit_code,
)
from sedo.models import Customer, Invoice, Payment
from sedo.services import next_number
from sedo.services.payments import _match_invoice

RAM = {'ram': 'urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100'}


def _row(**kwargs) -> InvoiceRow:
    base = dict(
        invoice_no='RE-TEST-1', object_name='Objekt', we='', ae='', nksl='',
        service='Reinigung', net=Decimal('100.00'), vat_rate=Decimal('19.00'),
        buyer_name='Synthetischer Testkunde GmbH', buyer_street='Kunden-Teststraße 2',
        buyer_postcode='10115', buyer_city='Berlin', buyer_country='DE',
        positions=[InvoicePosition('Reinigung', Decimal('100'), 'm²', Decimal('1.00'))],
    )
    base.update(kwargs)
    return InvoiceRow(**base)


class UnitCodeTests(TestCase):
    def test_mapping_bekannter_einheiten(self):
        self.assertEqual(unece_unit_code('m²'), 'MTK')
        self.assertEqual(unece_unit_code('qm'), 'MTK')
        self.assertEqual(unece_unit_code('Monat'), 'MON')
        self.assertEqual(unece_unit_code('Std.'), 'HUR')
        self.assertEqual(unece_unit_code('Stk.'), 'H87')

    def test_unbekannte_einheit_faellt_auf_h87(self):
        self.assertEqual(unece_unit_code('Zonk'), 'H87')
        self.assertEqual(unece_unit_code(''), 'H87')

    def test_billed_quantity_unitcode_in_xml(self):
        xml = build_xml(_row(), datetime(2026, 5, 1), None, None, datetime(2026, 5, 15))
        codes = etree.fromstring(xml).xpath('//ram:BilledQuantity/@unitCode', namespaces=RAM)
        self.assertEqual(codes, ['MTK'])  # früher fälschlich immer 'H87'


class ClonePositionTests(TestCase):
    def test_long_text_wird_kopiert(self):
        p = InvoicePosition('Titel', Decimal('1'), 'Stk.', Decimal('10'), long_text='BT-154 Text')
        self.assertEqual(clone_position(p).long_text, 'BT-154 Text')


class CoreGuardTests(TestCase):
    def test_leere_rechnungsnummer_wirft_valueerror(self):
        with self.assertRaises(ValueError):
            build_xml(_row(invoice_no=''), datetime(2026, 1, 1), None, None, datetime(2026, 1, 2))

    def test_ungueltiger_zahlenwert_wirft_valueerror(self):
        with self.assertRaises(ValueError):
            D('ca. 100')

    def test_deutsches_zahlenformat(self):
        self.assertEqual(D('1.234,56'), Decimal('1234.56'))


class SoftDeleteTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name='Test GmbH')

    def _invoice(self, number, gross='100.00'):
        return Invoice.objects.create(number=number, customer=self.customer, gross_total=Decimal(gross))

    def test_soft_delete_blendet_aus_bleibt_erhalten(self):
        inv = self._invoice('RE-SD-1')
        inv.soft_delete()
        self.assertFalse(Invoice.objects.filter(pk=inv.pk).exists())
        self.assertTrue(Invoice.all_objects.filter(pk=inv.pk).exists())

    def test_paid_amount_ignoriert_soft_geloeschte_zahlung(self):
        inv = self._invoice('RE-SD-2')
        today = timezone.localdate()
        Payment.objects.create(invoice=inv, booking_date=today, amount=Decimal('50.00'))
        p2 = Payment.objects.create(invoice=inv, booking_date=today, amount=Decimal('30.00'))
        p2.soft_delete()
        self.assertEqual(inv.paid_amount, Decimal('50.00'))

    def test_nummer_wird_nach_soft_delete_nicht_wiederverwendet(self):
        now = timezone.localdate()
        stem = f'RE{now.year}{now.month:02d}'
        inv = self._invoice(f'{stem}0001')
        inv.soft_delete()
        self.assertEqual(next_number('RE'), f'{stem}0002')


class BankMatchTests(TestCase):
    def test_exakter_nummern_match_ohne_substring_fehler(self):
        customer = Customer.objects.create(name='K')
        i1 = Invoice.objects.create(number='RE2025080001', customer=customer, gross_total=Decimal('10'))
        Invoice.objects.create(number='RE20250800010', customer=customer, gross_total=Decimal('10'))
        match = _match_invoice('Ueberweisung RE2025080001 vielen Dank')
        self.assertEqual(match.pk, i1.pk)


class PostOnlyTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_user('u', 'u@x.de', 'pw12345')
        self.client = Client()
        self.client.login(username='u', password='pw12345')
        self.customer = Customer.objects.create(name='K', email='k@x.de')
        self.invoice = Invoice.objects.create(number='RE-PO-1', customer=self.customer, gross_total=Decimal('10'))

    def test_email_versand_per_get_verboten(self):
        resp = self.client.get(reverse('sedo:invoice_email', args=[self.invoice.pk]))
        self.assertEqual(resp.status_code, 405)  # nur POST erlaubt

    def test_storno_per_get_verboten(self):
        resp = self.client.get(reverse('sedo:invoice_storno', args=[self.invoice.pk]))
        self.assertEqual(resp.status_code, 405)
