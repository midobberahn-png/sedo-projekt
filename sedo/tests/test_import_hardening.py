"""Tests für die Import-/Versand-Härtung der v3.22 (zweiter externer Review).

Abgedeckt: Doubletten-Erkennung über die Bank-Transaktions-ID (zwei echte
Zahlungen mit identischen Textdaten bleiben unterscheidbar), verschärfter
Rechnungsnummern-Regex und die Kunden-Sperre im Monats-Sammelversand.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from sedo.models import Customer, Invoice, Payment
from sedo.services import import_bank_json
from sedo.services.payments import _INVOICE_NO_PATTERN


class PaymentImportDedupTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name='Müller GmbH')

    def _tx(self, **overrides):
        base = {'datum': '2026-08-01', 'betrag': '1000.00',
                'name': 'Müller GmbH', 'verwendungszweck': 'Rechnung 123'}
        base.update(overrides)
        return base

    def test_gleiche_transaktions_id_wird_nur_einmal_importiert(self):
        import_bank_json([self._tx(transaktions_id='TX-1')])
        result = import_bank_json([self._tx(transaktions_id='TX-1')])
        self.assertEqual(result['imported'], 0)
        self.assertEqual(result['ignored'], 1)
        self.assertEqual(Payment.all_objects.count(), 1)

    def test_zwei_echte_zahlungen_mit_gleichen_daten_aber_anderer_id(self):
        # Vorher wurden identische Textdaten pauschal als Doublette verworfen –
        # mit stabiler Bank-ID sind beide Zahlungen unterscheidbar.
        result = import_bank_json([
            self._tx(transaktions_id='TX-1'),
            self._tx(transaktions_id='TX-2'),
        ])
        self.assertEqual(result['imported'], 2)
        self.assertEqual(Payment.all_objects.count(), 2)

    def test_ohne_id_greift_die_alte_heuristik(self):
        import_bank_json([self._tx()])
        result = import_bank_json([self._tx()])
        self.assertEqual(result['ignored'], 1)
        self.assertEqual(Payment.all_objects.count(), 1)

    def test_soft_geloeschte_zahlung_blockiert_reimport(self):
        import_bank_json([self._tx(transaktions_id='TX-9')])
        Payment.all_objects.get(external_id='TX-9').soft_delete()
        result = import_bank_json([self._tx(transaktions_id='TX-9')])
        self.assertEqual(result['ignored'], 1)

    def test_zahlung_auf_korrigierte_rechnung_geht_an_die_korrektur(self):
        # Mireks Entscheid (14.08.): RE-alt wurde korrigiert → Zahlung mit der
        # alten Nummer im Verwendungszweck landet auf der Korrekturrechnung.
        alt = Invoice.objects.create(number='RE2026080021', customer=self.customer,
                                     status='korrigiert', gross_total=Decimal('1000.00'))
        korrektur = Invoice.objects.create(number='RE2026080022', customer=self.customer,
                                           invoice_type='384', reference_invoice=alt,
                                           status='versendet', gross_total=Decimal('900.00'))
        result = import_bank_json([self._tx(
            transaktions_id='TX-K1', verwendungszweck='Zahlung RE2026080021')])
        self.assertEqual(result['matched'], 1)
        payment = Payment.all_objects.get(external_id='TX-K1')
        self.assertEqual(payment.invoice_id, korrektur.id)

    def test_zahlung_auf_korrigierte_rechnung_ohne_korrektur_bleibt_unklar(self):
        Invoice.objects.create(number='RE2026080023', customer=self.customer,
                               status='korrigiert', gross_total=Decimal('500.00'))
        result = import_bank_json([self._tx(
            transaktions_id='TX-K2', verwendungszweck='Zahlung RE2026080023')])
        self.assertEqual(result['matched'], 0)
        self.assertEqual(result['unclear'], 1)
        self.assertIsNone(Payment.all_objects.get(external_id='TX-K2').invoice_id)

    def test_zuordnung_nur_fuer_eigene_praefixe(self):
        inv = Invoice.objects.create(number='RE2026080001', customer=self.customer,
                                     gross_total=Decimal('1000.00'),
                                     net_total=Decimal('840.34'),
                                     vat_amount=Decimal('159.66'), status='versendet')
        # Fremdkennzeichen im selben Format (2 Buchstaben + Ziffern) wird
        # nicht mehr als Rechnungsnummern-Kandidat extrahiert …
        self.assertEqual(_INVOICE_NO_PATTERN.findall('KD2026080001'), [])
        # … die eigene Nummer schon.
        result = import_bank_json([self._tx(
            transaktions_id='TX-5', verwendungszweck='Zahlung RE2026080001')])
        self.assertEqual(result['matched'], 1)
        payment = Payment.all_objects.get(external_id='TX-5')
        self.assertEqual(payment.invoice_id, inv.id)


class MonthlySendCustomerLockTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_user('u', password='pw12345')
        self.client.login(username='u', password='pw12345')
        self.a = Customer.objects.create(name='Kunde A', email='a@example.org')
        self.b = Customer.objects.create(name='Kunde B', email='b@example.org')
        self.inv_a = Invoice.objects.create(number='RE2026080101', customer=self.a,
                                            gross_total=Decimal('119.00'), status='erstellt')
        self.inv_b = Invoice.objects.create(number='RE2026080102', customer=self.b,
                                            gross_total=Decimal('119.00'), status='erstellt')

    def test_monthly_service_form_contains_no_internal_review_note(self):
        response = self.client.get(reverse('sedo:aareon_monthly'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertNotIn('Review-Fund', content)
        self.assertNotIn('{#', content)
        self.assertIn('aria-label="Menge Heckenschnitt"', content)

    def test_gemischte_kunden_werden_blockiert(self):
        resp = self.client.post(reverse('sedo:aareon_monthly'), {
            'action': 'send_monthly_email',
            'month': '2026-08',
            'invoice_ids': [str(self.inv_a.pk), str(self.inv_b.pk)],
        })
        # Kein Datei-Download (keine ZIP-Mail), sondern Seite mit Fehlermeldung
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode('utf-8')
        self.assertIn('mehreren Kunden', content)
