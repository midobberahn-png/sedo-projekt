"""Tests für die Workflow-Sperren der v3.22-Härtung (zweiter externer Review).

Abgedeckt: kein Doppel-Storno, kein Storno von Stornos/korrigierten Rechnungen,
Angebot→Rechnung nur einmal (Service + beide View-Pfade), strenger
Korrektur-Workflow (Original wird markiert, zweite Korrektur blockiert).
"""
from decimal import Decimal

from .file_fixtures import isolate_files

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from sedo.models import Customer, Invoice, InvoiceItem, Offer, OfferItem
from sedo.services import (
    WorkflowError,
    create_invoice_from_offer,
    create_storno,
    ensure_correction_allowed,
)


def _invoice(customer, number, **kwargs):
    defaults = dict(
        invoice_type='380', status='erstellt',
        gross_total=Decimal('119.00'), net_total=Decimal('100.00'),
        vat_amount=Decimal('19.00'),
    )
    defaults.update(kwargs)
    inv = Invoice.objects.create(number=number, customer=customer, **defaults)
    InvoiceItem.objects.create(invoice=inv, position=1, description='Reinigung',
                               quantity=Decimal('1'), unit='pauschal',
                               unit_price=Decimal('100.00'))
    return inv


class StornoLockTests(TestCase):
    def setUp(self):
        isolate_files(self)
        self.customer = Customer.objects.create(name='GWG', street='Weg 1',
                                                postcode='39104', city='Magdeburg')
        self.original = _invoice(self.customer, 'RE2026080001')

    def test_zweites_storno_wird_blockiert(self):
        storno = create_storno(self.original)
        self.assertEqual(storno.invoice_type, '457')
        self.original.refresh_from_db()
        self.assertEqual(self.original.status, 'storniert')
        with self.assertRaises(WorkflowError):
            create_storno(self.original)
        self.assertEqual(
            Invoice.all_objects.filter(invoice_type='457',
                                       reference_invoice=self.original).count(), 1)

    def test_storno_eines_stornos_wird_blockiert(self):
        storno = create_storno(self.original)
        with self.assertRaises(WorkflowError):
            create_storno(storno)

    def test_storno_einer_korrigierten_rechnung_wird_blockiert(self):
        self.original.status = 'korrigiert'
        self.original.save(update_fields=['status'])
        with self.assertRaises(WorkflowError):
            create_storno(self.original)

    def test_storno_view_zeigt_fehlermeldung_statt_zweitem_storno(self):
        get_user_model().objects.create_user('u', password='pw12345')
        self.client.login(username='u', password='pw12345')
        create_storno(self.original)
        resp = self.client.post(reverse('sedo:invoice_storno', args=[self.original.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Invoice.all_objects.filter(invoice_type='457').count(), 1)

    def test_soft_geloeschtes_storno_blockiert_nicht(self):
        # Wurde das Storno ausgeblendet, bleibt die Rechnung storniert –
        # aber die Folgebeleg-Sperre greift nur für AKTIVE Stornos.
        storno = create_storno(self.original)
        storno.soft_delete()
        self.original.status = 'erstellt'
        self.original.save(update_fields=['status'])
        neu = create_storno(self.original)
        self.assertEqual(neu.invoice_type, '457')


class OfferToInvoiceLockTests(TestCase):
    def setUp(self):
        isolate_files(self)
        self.customer = Customer.objects.create(name='Kunde', payment_days=14)
        self.offer = Offer.objects.create(number='AG2026080001', customer=self.customer,
                                          status='angenommen', vat_rate=Decimal('19.00'))
        OfferItem.objects.create(offer=self.offer, position=1, description='Leistung',
                                 quantity=Decimal('1'), unit='Stk.',
                                 unit_price=Decimal('100.00'))

    def test_zweite_rechnung_aus_angebot_wird_blockiert(self):
        inv = create_invoice_from_offer(self.offer)
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.linked_invoice_id, inv.id)
        with self.assertRaises(WorkflowError):
            create_invoice_from_offer(self.offer)
        self.assertEqual(Invoice.all_objects.filter(customer=self.customer).count(), 1)

    def test_status_in_rechnung_blockiert_auch_ohne_verknuepfung(self):
        self.offer.status = 'in_rechnung'
        self.offer.save(update_fields=['status'])
        with self.assertRaises(WorkflowError):
            create_invoice_from_offer(self.offer)

    def test_view_blockiert_zweiten_post(self):
        get_user_model().objects.create_user('u', password='pw12345')
        self.client.login(username='u', password='pw12345')
        url = reverse('sedo:offer_to_invoice', args=[self.offer.pk])
        self.client.post(url)
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Invoice.all_objects.filter(customer=self.customer).count(), 1)

    def test_formular_pfad_blockiert_bereits_verrechnetes_angebot(self):
        get_user_model().objects.create_user('u', password='pw12345')
        self.client.login(username='u', password='pw12345')
        create_invoice_from_offer(self.offer)
        resp = self.client.get(reverse('sedo:invoice_create') + f'?from_offer={self.offer.pk}')
        # Statt Formular: Redirect zurück zum Angebot mit Fehlermeldung
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('sedo:offer_detail', args=[self.offer.pk]), resp.url)


class CorrectionLockTests(TestCase):
    def setUp(self):
        isolate_files(self)
        self.user = get_user_model().objects.create_user('u', password='pw12345')
        self.client.login(username='u', password='pw12345')
        self.customer = Customer.objects.create(name='Kunde', payment_days=14)
        self.original = _invoice(self.customer, 'RE2026080010')

    def _post_correction(self):
        return self.client.post(reverse('sedo:invoice_create'), {
            'customer': self.customer.pk,
            'name': self.customer.name,
            'issue_date': '2026-08-14',
            'payment_days': '14',
            'vat_rate': '19.00',
            'correction_of': str(self.original.pk),
            'action': 'save',
            'items-TOTAL_FORMS': '1',
            'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '1',
            'items-MAX_NUM_FORMS': '1000',
            'items-0-description': 'Reinigung (korrigiert)',
            'items-0-quantity': '1',
            'items-0-unit': 'pauschal',
            'items-0-unit_price': '90.00',
        })

    def test_korrektur_markiert_original_und_verweist(self):
        resp = self._post_correction()
        self.assertEqual(resp.status_code, 302)
        self.original.refresh_from_db()
        self.assertEqual(self.original.status, 'korrigiert')
        correction = self.original.active_correction
        self.assertIsNotNone(correction)
        self.assertEqual(correction.invoice_type, '384')
        self.assertEqual(correction.reference_invoice_id, self.original.id)

    def test_zweite_korrektur_wird_blockiert(self):
        self._post_correction()
        before = Invoice.all_objects.count()
        resp = self._post_correction()
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Invoice.all_objects.count(), before)

    def test_korrektur_link_fuer_stornierte_rechnung_blockiert(self):
        self.original.status = 'storniert'
        self.original.save(update_fields=['status'])
        resp = self.client.get(reverse('sedo:invoice_correction', args=[self.original.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('sedo:invoice_detail', args=[self.original.pk]), resp.url)

    def test_korrektur_formular_get_blockiert_bei_vorhandener_korrektur(self):
        self._post_correction()
        resp = self.client.get(reverse('sedo:invoice_create') + f'?correction_of={self.original.pk}')
        self.assertEqual(resp.status_code, 302)

    def test_ensure_correction_allowed_fuer_storno_rechnung(self):
        storno = create_storno(self.original)
        with self.assertRaises(WorkflowError):
            ensure_correction_allowed(storno)
