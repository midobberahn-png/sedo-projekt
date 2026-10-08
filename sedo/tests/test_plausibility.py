"""Tests für den Plausibilitäts-Check (lokal + KI-Endpoint).

Der lokale Teil wird gegen synthetische Datenbank-Historie getestet (Ausreißer,
Zahlendreher, Dubletten, Nullwerte). Beim KI-Teil wird geprüft, dass an die
KI-Funktion ausschließlich die Positionen übergeben werden.
"""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from sedo.models import AppSetting, Customer, Invoice, InvoiceItem
from sedo.services import check_positions_local


def _pos(bezeichnung, preis, menge='1', einheit='Monat'):
    return {'bezeichnung': bezeichnung, 'menge': menge, 'einheit': einheit, 'einzelpreis': preis}


class LocalCheckTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name='Synthetischer Testkunde eG')
        inv = Invoice.objects.create(number='RE-H-1', customer=self.customer,
                                     status='bezahlt', issue_date=date(2026, 6, 10))
        InvoiceItem.objects.create(invoice=inv, position=1,
                                   description='Unterhaltsreinigung Treppenhaus',
                                   quantity=Decimal('1'), unit='Monat', unit_price=Decimal('85.00'))

    def test_ausreisser_mit_zahlendreher_hinweis(self):
        findings = check_positions_local(self.customer.pk,
                                         [_pos('Unterhaltsreinigung Treppenhaus', '58,00')])
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f['schwere'], 'warnung')
        self.assertIn('85', f['text'])
        self.assertIn('RE-H-1', f['text'])
        self.assertIn('Zahlendreher', f['text'])

    def test_kleine_abweichung_ist_ok(self):
        # 85 → 80: nur ~6 % Abweichung → keine Warnung
        findings = check_positions_local(self.customer.pk,
                                         [_pos('Unterhaltsreinigung Treppenhaus', '80,00')])
        self.assertEqual(findings, [])

    def test_storno_und_entwurf_zaehlen_nicht_als_historie(self):
        other = Customer.objects.create(name='Anderer Kunde')
        inv = Invoice.objects.create(number='RE-H-2', customer=other,
                                     status='storniert', issue_date=date(2026, 6, 1))
        InvoiceItem.objects.create(invoice=inv, position=1, description='Winterdienst',
                                   quantity=Decimal('1'), unit='Monat', unit_price=Decimal('500.00'))
        findings = check_positions_local(other.pk, [_pos('Winterdienst', '100,00')])
        self.assertEqual(findings, [])

    def test_dubletten_erkannt(self):
        findings = check_positions_local(None, [
            _pos('Glasreinigung', '120,00'),
            _pos('Glasreinigung', '120,00'),
        ])
        self.assertEqual(len(findings), 1)
        self.assertIn('doppelt', findings[0]['text'])

    def test_nullpreis_hinweis(self):
        findings = check_positions_local(None, [_pos('Sonderleistung', '0,00')])
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['schwere'], 'hinweis')
        self.assertIn('0 €', findings[0]['text'])

    def test_neuer_kunde_ohne_historie_ok(self):
        findings = check_positions_local(self.customer.pk, [_pos('Voellig neue Leistung', '999,00')])
        self.assertEqual(findings, [])


class PlausibilityEndpointTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_user('u', 'u@x.de', 'pw12345')
        self.client = Client()
        self.client.login(username='u', password='pw12345')
        self.url = reverse('sedo:ai_plausibility')

    def _post(self, payload):
        import json
        return self.client.post(self.url, json.dumps(payload), content_type='application/json')

    def test_get_verboten(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_ohne_positionen_400(self):
        self.assertEqual(self._post({'positionen': []}).status_code, 400)

    def test_lokal_funktioniert_ohne_api_key(self):
        resp = self._post({'positionen': [_pos('Glas', '10'), _pos('Glas', '10')], 'mode': 'lokal'})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data['lokal']), 1)
        self.assertIsNone(data['ki'])

    def test_ki_deaktiviert_liefert_fehlertext_aber_lokal(self):
        AppSetting.set('ai_local_enabled', '0')
        resp = self._post({'positionen': [_pos('Glas', '10')], 'mode': 'ki'})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('deaktiviert', data['ki_error'])
        self.assertEqual(data['lokal'], [])

    def test_ki_erhaelt_nur_positionen(self):
        """Datenschutz: an die KI-Funktion gehen nur die Positionsdaten."""
        AppSetting.set('ai_local_enabled', '1')
        captured = {}

        def fake_review(positionen):
            captured['args'] = positionen
            return [{'position': 1, 'schwere': 'hinweis', 'text': 'Testbefund'}]

        with patch('sedo.views.ai_review_positions', side_effect=fake_review):
            resp = self._post({'positionen': [_pos('Glasreinigung', '120')],
                               'customer': 42, 'mode': 'ki'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['ki'][0]['text'], 'Testbefund')
        # Nur die Positionsliste – keine Kunden-ID, keine weiteren Daten
        self.assertEqual(captured['args'], [_pos('Glasreinigung', '120')])
