"""Tests für den zentralen Nummernkreis (NumberRange, v3.22-Härtung).

Abgedeckt: deterministische fortlaufende Vergabe, Seeding aus Altbestand,
Nachziehen des Zählers bei Kollision, Rollback ohne „verbrannte“ Nummern und
die nicht-verbrauchende Formular-Vorschau.
"""
from decimal import Decimal

from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from sedo.models import Customer, Invoice, NumberRange, Offer
from sedo.services import (
    create_numbered_invoice,
    create_numbered_offer,
    next_number,
    save_with_unique_number,
)


def _stem(prefix):
    now = timezone.localdate()
    return f'{prefix}{now.year}{now.month:02d}'


class NumberRangeTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name='Kunde')

    def test_fortlaufende_vergabe_ueber_nummernkreis(self):
        a = create_numbered_invoice(customer=self.customer, status='erstellt')
        b = create_numbered_invoice(customer=self.customer, status='erstellt')
        self.assertEqual(a.number, f'{_stem("RE")}0001')
        self.assertEqual(b.number, f'{_stem("RE")}0002')
        row = NumberRange.objects.get(prefix='RE')
        self.assertEqual(row.next_number, 3)

    def test_seeding_aus_altbestand(self):
        Invoice.objects.create(number=f'{_stem("RE")}0041', customer=self.customer,
                               status='erstellt')
        inv = create_numbered_invoice(customer=self.customer, status='erstellt')
        self.assertEqual(inv.number, f'{_stem("RE")}0042')

    def test_kollision_mit_altbestand_zieht_zaehler_nach(self):
        # Nummernkreis existiert bereits, hängt aber hinter dem Altbestand
        # (z. B. nach dem Einspielen einer alten Datenbank).
        now = timezone.localdate()
        NumberRange.objects.create(prefix='RE', year=now.year, month=now.month,
                                   next_number=1)
        Invoice.objects.create(number=f'{_stem("RE")}0001', customer=self.customer,
                               status='erstellt')
        inv = create_numbered_invoice(customer=self.customer, status='erstellt')
        self.assertEqual(inv.number, f'{_stem("RE")}0002')

    def test_soft_geloeschte_nummern_werden_nicht_wiederverwendet(self):
        inv = create_numbered_invoice(customer=self.customer, status='erstellt')
        first_number = inv.number
        inv.soft_delete()
        neu = create_numbered_invoice(customer=self.customer, status='erstellt')
        self.assertNotEqual(neu.number, first_number)

    def test_fehlgeschlagene_anlage_verbrennt_keine_nummer(self):
        create_numbered_invoice(customer=self.customer, status='erstellt')
        try:
            with transaction.atomic():
                create_numbered_invoice(customer=self.customer, status='erstellt')
                raise RuntimeError('Simulierter Fehler nach der Anlage')
        except RuntimeError:
            pass
        # Der Zähler ist mit zurückgerollt: nächste Rechnung setzt lückenlos fort.
        inv = create_numbered_invoice(customer=self.customer, status='erstellt')
        self.assertEqual(inv.number, f'{_stem("RE")}0002')

    def test_vorschau_verbraucht_keine_nummer(self):
        preview1 = next_number('RE')
        preview2 = next_number('RE')
        self.assertEqual(preview1, preview2)
        inv = create_numbered_invoice(customer=self.customer, status='erstellt')
        self.assertEqual(inv.number, preview1)

    def test_angebote_haben_eigenen_nummernkreis(self):
        create_numbered_invoice(customer=self.customer, status='erstellt')
        offer = create_numbered_offer(customer=self.customer, status='offen')
        self.assertEqual(offer.number, f'{_stem("AG")}0001')

    def test_save_with_unique_number_nutzt_nummernkreis(self):
        off = Offer(customer=self.customer, status='offen',
                    issue_date=timezone.localdate(), vat_rate=Decimal('19.00'))
        save_with_unique_number(off, 'AG')
        self.assertEqual(off.number, f'{_stem("AG")}0001')
        off2 = Offer(customer=self.customer, status='offen',
                     issue_date=timezone.localdate(), vat_rate=Decimal('19.00'))
        save_with_unique_number(off2, 'AG')
        self.assertEqual(off2.number, f'{_stem("AG")}0002')
