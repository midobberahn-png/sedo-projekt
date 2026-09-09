"""Tests für den Angebots-Soft-Delete (v3.22-Härtung).

Angebote sind Geschäftsbriefe: Löschen blendet nur aus (wie bei Rechnungen
und Zahlungen), Nummern werden nie wiederverwendet, Kundenansichten filtern
die all_objects-Rückwärtsrelation.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from sedo.models import Customer, Offer
from sedo.services import create_numbered_offer


class OfferSoftDeleteTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_user('u', password='pw12345')
        self.client.login(username='u', password='pw12345')
        self.customer = Customer.objects.create(name='Kunde')
        self.offer = Offer.objects.create(
            number='AG2026080001', customer=self.customer, status='offen',
            issue_date=timezone.localdate(), vat_rate=Decimal('19.00'))

    def test_loeschen_blendet_nur_aus(self):
        resp = self.client.post(reverse('sedo:offer_delete', args=[self.offer.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Offer.objects.count(), 0)          # Standard-Manager blendet aus
        self.assertEqual(Offer.all_objects.count(), 1)       # Datensatz bleibt erhalten
        self.assertIsNotNone(Offer.all_objects.get(pk=self.offer.pk).deleted_at)

    def test_nummer_wird_nach_soft_delete_nicht_wiederverwendet(self):
        first = create_numbered_offer(customer=self.customer, status='offen')
        first_number = first.number
        first.soft_delete()
        second = create_numbered_offer(customer=self.customer, status='offen')
        self.assertNotEqual(second.number, first_number)

    def test_kundendetail_zeigt_ausgeblendete_angebote_nicht(self):
        self.offer.soft_delete()
        resp = self.client.get(reverse('sedo:customer_detail', args=[self.customer.pk]))
        self.assertNotContains(resp, self.offer.number)

    def test_angebotsliste_zeigt_ausgeblendete_nicht(self):
        self.offer.soft_delete()
        resp = self.client.get(reverse('sedo:offer_list'))
        self.assertNotContains(resp, self.offer.number)
