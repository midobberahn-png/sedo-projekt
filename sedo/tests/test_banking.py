"""Tests für die FinTS-Kontoanbindung (v2.3) – Bank komplett gemockt.

Es werden ausschließlich synthetische Konten und gemockte Bankdialoge genutzt.
Abgesichert werden Konfiguration, Normalisierung
der mt940-Umsätze, Übergabe an die Import-Pipeline, PIN-Handling und
Backup-Bereinigung.
"""
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from sedo.models import AppSetting, Customer, Invoice, Payment
from sedo.services import (
    BankingError,
    banking_is_configured,
    fetch_bank_transactions,
)
from sedo.services.banking import _normalize_mt940


class _FakeAmount:
    def __init__(self, amount):
        self.amount = Decimal(amount)


class _FakeTransaction:
    def __init__(self, **data):
        self.data = data


def _fake_client_returning(transactions):
    """Baut einen FinTS3PinTanClient-Ersatz, der Umsätze liefert."""
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    client.init_tan_response = None
    account = MagicMock()
    account.iban = 'DE89370400440532013000'
    client.get_sepa_accounts.return_value = [account]
    client.get_transactions.return_value = transactions
    return client


class BankingConfigTests(TestCase):
    def test_unkonfiguriert_liefert_klare_meldung(self):
        self.assertFalse(banking_is_configured())
        with self.assertRaises(BankingError):
            fetch_bank_transactions(days=30, pin='1234')

    def test_konfiguriert_aber_ohne_pin(self):
        AppSetting.set('fints_blz', '37040044')
        AppSetting.set('fints_endpoint', 'https://example.test/fints30')
        AppSetting.set('fints_login', 'kunde1')
        self.assertTrue(banking_is_configured())
        with self.assertRaises(BankingError):
            fetch_bank_transactions(days=30, pin='')

    def test_pin_gehoert_zu_den_backup_geheimnissen(self):
        from sedo.backup_utils import SECRET_SETTING_KEYS
        self.assertIn('fints_pin', SECRET_SETTING_KEYS)


class Mt940NormalizeTests(TestCase):
    def test_normalisierung_und_stabile_id(self):
        rows = _normalize_mt940([
            _FakeTransaction(date=date(2026, 8, 1), amount=_FakeAmount('119.00'),
                             applicant_name='Müller GmbH',
                             purpose='Zahlung RE2026080001',
                             end_to_end_reference='E2E-42'),
            _FakeTransaction(date=date(2026, 8, 2), amount=_FakeAmount('50.00'),
                             applicant_name='Ohne Referenz',
                             purpose='Danke', end_to_end_reference='NOTPROVIDED'),
        ])
        self.assertEqual(rows[0]['transaktions_id'], 'E2E-42')
        self.assertEqual(rows[0]['betrag'], '119.00')
        self.assertEqual(rows[1]['transaktions_id'], '')
        # Fehlt eine Bankkennung, darf aus identischen Textdaten keine
        # scheinbar eindeutige ID erzeugt werden.
        again = _normalize_mt940([
            _FakeTransaction(date=date(2026, 8, 2), amount=_FakeAmount('50.00'),
                             applicant_name='Ohne Referenz',
                             purpose='Danke', end_to_end_reference='NONREF'),
        ])
        self.assertEqual(rows[1]['transaktions_id'], again[0]['transaktions_id'])

    def test_bankreferenz_hat_vorrang_vor_end_to_end_referenz(self):
        rows = _normalize_mt940([_FakeTransaction(
            date=date(2026, 8, 1), amount=_FakeAmount('50.00'),
            bank_reference='BANK-42', end_to_end_reference='E2E-42')])
        self.assertEqual(rows[0]['transaktions_id'], 'BANK-42')

    def test_fehlende_bankreferenz_nutzt_gueltige_end_to_end_referenz(self):
        rows = _normalize_mt940([_FakeTransaction(
            date=date(2026, 8, 1), amount=_FakeAmount('50.00'),
            bank_reference='NOTPROVIDED', end_to_end_reference='E2E-42')])
        self.assertEqual(rows[0]['transaktions_id'], 'E2E-42')


class FetchAndImportTests(TestCase):
    def setUp(self):
        AppSetting.set('fints_blz', '37040044')
        AppSetting.set('fints_endpoint', 'https://example.test/fints30')
        AppSetting.set('fints_login', 'kunde1')
        self.customer = Customer.objects.create(name='Müller GmbH')
        self.invoice = Invoice.objects.create(number='RE2026080001',
                                              customer=self.customer,
                                              status='versendet',
                                              gross_total=Decimal('119.00'))

    def _tx(self):
        return [_FakeTransaction(date=date(2026, 8, 1), amount=_FakeAmount('119.00'),
                                 applicant_name='Müller GmbH',
                                 purpose='Zahlung RE2026080001',
                                 end_to_end_reference='E2E-1')]

    def test_abruf_importiert_und_ordnet_zu(self):
        client = _fake_client_returning(self._tx())
        with patch('fints.client.FinTS3PinTanClient', return_value=client):
            result = fetch_bank_transactions(days=30, pin='1234')
        self.assertEqual(result['abgerufen'], 1)
        self.assertEqual(result['imported'], 1)
        self.assertEqual(result['matched'], 1)
        payment = Payment.all_objects.get(external_id='E2E-1')
        self.assertEqual(payment.invoice_id, self.invoice.id)

    def test_zweiter_abruf_ignoriert_bekannte_umsaetze(self):
        client = _fake_client_returning(self._tx())
        with patch('fints.client.FinTS3PinTanClient', return_value=client):
            fetch_bank_transactions(days=30, pin='1234')
            result = fetch_bank_transactions(days=30, pin='1234')
        self.assertEqual(result['imported'], 0)
        self.assertEqual(result['ignored'], 1)
        self.assertEqual(Payment.all_objects.count(), 1)

    def test_umsatz_ohne_referenz_wird_bei_wiederholung_als_mehrdeutig_gemeldet(self):
        transactions = [
            _FakeTransaction(date=date(2026, 8, 1), amount=_FakeAmount('50.00'),
                             applicant_name='Ohne Referenz', purpose='Danke',
                             end_to_end_reference='NOTPROVIDED') for _ in range(2)
        ]
        client = _fake_client_returning(transactions)
        with patch('fints.client.FinTS3PinTanClient', return_value=client):
            first = fetch_bank_transactions(days=30, pin='1234')
            second = fetch_bank_transactions(days=30, pin='1234')
        self.assertEqual(first['imported'], 2)
        self.assertEqual(second['imported'], 0)
        self.assertEqual(len(second['ambiguous']), 2)
        self.assertEqual(Payment.all_objects.count(), 2)

    def test_iban_filter_findet_konto(self):
        AppSetting.set('fints_iban', 'DE89 3704 0044 0532 0130 00')
        client = _fake_client_returning(self._tx())
        with patch('fints.client.FinTS3PinTanClient', return_value=client):
            result = fetch_bank_transactions(days=30, pin='1234')
        self.assertEqual(result['imported'], 1)

    def test_unbekannte_iban_bricht_verstaendlich_ab(self):
        AppSetting.set('fints_iban', 'DE00000000000000000000')
        client = _fake_client_returning(self._tx())
        with patch('fints.client.FinTS3PinTanClient', return_value=client):
            with self.assertRaises(BankingError):
                fetch_bank_transactions(days=30, pin='1234')
        self.assertEqual(Payment.all_objects.count(), 0)

    def test_view_abruf_mit_einmal_pin(self):
        get_user_model().objects.create_user('u', password='pw12345')
        self.client.login(username='u', password='pw12345')
        client = _fake_client_returning(self._tx())
        with patch('fints.client.FinTS3PinTanClient', return_value=client):
            resp = self.client.post(reverse('sedo:payment_list'), {
                'action': 'fints_fetch', 'days': '30', 'fints_pin_once': '1234'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Payment.all_objects.count(), 1)
