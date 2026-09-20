"""Personal dashboard persistence, authenticated reads, and mocked provider contracts."""
import json
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from lxml import html

from sedo.dashboard_widgets import DEFAULT_WIDGETS, WIDGET_KEYS, layout_for
from sedo.models import AppSetting, DashboardPreference, Payment
from sedo.services.banking import BankingError, cached_bank_balance, fetch_bank_balance
from sedo.services.dashboard_weather import API_BASE, weather_for


class DashboardLayoutTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('dashboard-one', is_staff=True)
        cls.other = get_user_model().objects.create_user('dashboard-two')

    def setUp(self):
        cache.clear()
        self.client.force_login(self.user)
        self.data = {'widgets': ['mail', 'weather', 'balance', 'drafts'],
                     'weather_country': 'DE', 'weather_postcode': '39104', 'weather_place': 'Magdeburg'}

    def save(self, **changes):
        return self.client.post(reverse('sedo:dashboard_layout'), self.data | changes)

    def test_default_mail_and_all_existing_widgets_without_creating_a_preference(self):
        response = self.client.get(reverse('sedo:dashboard'))
        root = html.fromstring(response.content)
        self.assertEqual(root.xpath('//*[@data-widget]/@data-widget'), list(DEFAULT_WIDGETS))
        self.assertIn('mail', DEFAULT_WIDGETS)
        self.assertNotIn('drafts', DEFAULT_WIDGETS)
        self.assertEqual(len(root.xpath('//input[@name="widgets"]')), len(WIDGET_KEYS))
        self.assertFalse(DashboardPreference.objects.exists())

    def test_saved_selection_survives_new_session_and_belongs_only_to_current_user(self):
        self.assertEqual(self.save(user=self.other.pk).status_code, 302)
        self.client.logout()
        self.client.force_login(self.user)
        response = self.client.get(reverse('sedo:dashboard'))
        root = html.fromstring(response.content)
        self.assertEqual(root.xpath('//*[@data-widget]/@data-widget'), ['mail', 'balance', 'weather', 'drafts'])
        self.assertContains(response, 'Zugang einrichten')
        self.assertContains(response, '?view=draft')
        self.client.force_login(self.other)
        self.assertEqual(layout_for(self.other)['widgets'], list(DEFAULT_WIDGETS))
        self.assertEqual(DashboardPreference.objects.count(), 1)

    def test_empty_selection_and_reset(self):
        self.save(widgets=[])
        response = self.client.get(reverse('sedo:dashboard'))
        self.assertContains(response, 'Platz für deinen Überblick')
        self.assertEqual(response.context['dashboard_widgets'], [])
        DashboardPreference.objects.create(user=self.other, widgets=['offers'])
        self.client.post(reverse('sedo:dashboard_layout'), {'action': 'reset'})
        self.assertEqual(layout_for(self.user)['widgets'], list(DEFAULT_WIDGETS))
        self.assertEqual(layout_for(self.other)['widgets'], ['offers'])

    def test_invalid_keys_and_locations_do_not_overwrite_saved_selection(self):
        self.save(widgets=['mail'])
        for bad in ({'widgets': ['../../settings']}, {'weather_country': 'US'},
                    {'weather_postcode': '../12'}, {'weather_place': 'x' * 81}):
            response = self.save(**bad)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(layout_for(self.user)['widgets'], ['mail'])
            self.assertTrue(html.fromstring(response.content).xpath('//details[@id="dashboard-customizer"][@open]'))

    def test_four_digit_postcode_deduplication_and_stale_saved_key(self):
        self.assertEqual(self.save(widgets=['weather', 'weather'], weather_country='AT',
                                   weather_postcode='1010', weather_place='Wien').status_code, 302)
        self.assertEqual(layout_for(self.user)['widgets'], ['weather'])
        DashboardPreference.objects.filter(user=self.user).update(widgets=['weather', 'obsolete'])
        self.assertEqual(layout_for(self.user)['widgets'], ['weather'])

    def test_mutations_require_login_post_and_csrf(self):
        routes = ('dashboard_layout', 'dashboard_balance', 'dashboard_weather')
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        for name in routes:
            url = reverse('sedo:' + name)
            self.assertEqual(self.client.get(url).status_code, 405)
            self.assertEqual(csrf_client.post(url, self.data).status_code, 403)
            self.assertEqual(Client().post(url, self.data).status_code, 302)

    @patch('sedo.services.dashboard_weather.requests.get')
    @patch('fints.client.FinTS3PinTanClient')
    def test_dashboard_get_and_hidden_widget_endpoints_never_contact_providers(self, bank, weather):
        AppSetting.set('weather_api_key', 'test-key')
        self.assertEqual(self.client.post(reverse('sedo:dashboard_balance')).status_code, 400)
        self.assertEqual(self.client.post(reverse('sedo:dashboard_weather')).status_code, 400)
        self.save()
        self.assertEqual(self.client.get(reverse('sedo:dashboard')).status_code, 200)
        bank.assert_not_called()
        weather.assert_not_called()

    @patch('sedo.dashboard_views.fetch_bank_balance')
    def test_balance_refresh_only_after_explicit_post_with_ephemeral_pin(self, bank):
        self.save()
        response = self.client.post(reverse('sedo:dashboard_balance'), {'fints_pin_once': 'private-pin'})
        self.assertRedirects(response, reverse('sedo:dashboard'))
        bank.assert_called_once_with(pin='private-pin')
        self.assertFalse(AppSetting.objects.filter(value__contains='private-pin').exists())

    def test_weather_secret_is_staff_only_never_echoed_and_blank_keeps_it(self):
        url = reverse('sedo:settings')
        self.client.post(url, {'action': 'save_weather', 'weather_api_key': 'TEST-SECRET-WEATHER'})
        self.assertEqual(AppSetting.get('weather_api_key'), 'TEST-SECRET-WEATHER')
        self.assertNotContains(self.client.get(url), 'TEST-SECRET-WEATHER')
        self.client.post(url, {'action': 'save_weather', 'weather_api_key': ''})
        self.assertEqual(AppSetting.get('weather_api_key'), 'TEST-SECRET-WEATHER')
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(url, {'action': 'save_weather', 'weather_api_key_clear': '1'}).status_code, 403)
        self.assertNotContains(self.client.get(url), 'weather-api-key')
        self.client.force_login(self.user)
        self.client.post(url, {'action': 'save_weather', 'weather_api_key': 'bad\r\nheader'})
        self.assertEqual(AppSetting.get('weather_api_key'), 'TEST-SECRET-WEATHER')
        self.client.post(url, {'action': 'save_weather', 'weather_api_key_clear': '1'})
        self.assertEqual(AppSetting.get('weather_api_key'), '')
        from sedo.backup_utils import SECRET_SETTING_KEYS
        self.assertIn('weather_api_key', SECRET_SETTING_KEYS)


class WeatherAdapterTests(TestCase):
    def setUp(self):
        cache.clear()
        self.layout = {'weather_country': 'DE', 'weather_postcode': '39104'}
        self.env = patch.dict('os.environ', {'SEDO_WEATHER_API_KEY': ''})
        self.env.start()
        self.addCleanup(self.env.stop)
        AppSetting.set('weather_api_key', 'test-weather-secret')
        self.payload = {'realtimeWeather': {'items': [{'from': timezone.now().isoformat(),
            'temperature': {'value': 0, 'unit': '°C'}, 'weather': {'text': 'Wolkig'}}]}}

    def response(self, payload=None, status=200):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status_code = status
        response.iter_content.return_value = [json.dumps(self.payload if payload is None else payload).encode()]
        return response

    @patch('sedo.services.dashboard_weather.requests.get')
    def test_official_contract_zero_temperature_and_shared_cache(self, get):
        get.return_value = self.response()
        self.assertEqual(weather_for(self.layout)['state'], 'loading')
        get.assert_not_called()
        data = weather_for(self.layout, refresh=True)
        self.assertEqual(data['temperature'], '0')
        self.assertEqual(data['state'], 'ready')
        self.assertEqual(get.call_args.args[0], API_BASE + '/DE/39104')
        self.assertEqual(get.call_args.kwargs['headers']['x-api-key'], 'test-weather-secret')
        self.assertFalse(get.call_args.kwargs['allow_redirects'])
        self.assertEqual(weather_for(self.layout, refresh=True), data)
        get.assert_called_once()
        self.assertNotIn('secret', json.dumps(data))
        AppSetting.set('weather_api_key', 'another-key')
        self.assertEqual(weather_for(self.layout)['state'], 'loading')
        self.assertEqual(weather_for(self.layout | {'weather_postcode': '10115'})['state'], 'loading')

    @patch('sedo.services.dashboard_weather.requests.get')
    def test_missing_configuration_and_bad_location_never_make_requests(self, get):
        self.assertEqual(weather_for(self.layout | {'weather_postcode': '../evil'}, True)['state'], 'error')
        AppSetting.set('weather_api_key', '')
        self.assertEqual(weather_for(self.layout, True)['state'], 'setup')
        get.assert_not_called()

    @patch('sedo.services.dashboard_weather.requests.get')
    def test_bad_data_outdated_readings_redirects_and_failures_are_safe_and_cached(self, get):
        bad_readings = [[], {}, {'realtimeWeather': {'items': []}}]
        for mutation in ({'from': (timezone.now() - timedelta(days=1)).isoformat()},
                         {'from': '2026-09-20T10:00:00'},
                         {'temperature': {'value': True, 'unit': '°C'}},
                         {'temperature': {'value': float('nan'), 'unit': '°C'}},
                         {'temperature': {'value': 250, 'unit': 'K'}}):
            bad_readings.append({'realtimeWeather': {'items': [self.payload['realtimeWeather']['items'][0] | mutation]}})
        for bad in bad_readings:
            cache.clear()
            get.return_value = self.response(bad)
            self.assertEqual(weather_for(self.layout, True)['state'], 'error')
        cache.clear()
        get.return_value = self.response(status=302)
        self.assertEqual(weather_for(self.layout, True)['state'], 'error')
        cache.clear()
        get.side_effect = requests.Timeout('test-weather-secret')
        data = weather_for(self.layout, True)
        self.assertEqual(data['state'], 'error')
        self.assertNotIn('test-weather-secret', json.dumps(data))
        get.reset_mock()
        self.assertEqual(weather_for(self.layout, True), data)
        get.assert_not_called()

    @patch('sedo.services.dashboard_weather.requests.get')
    def test_response_limit_and_negative_temperature(self, get):
        response = self.response()
        response.iter_content.return_value = [b'x' * 1_000_001]
        get.return_value = response
        self.assertEqual(weather_for(self.layout, True)['state'], 'error')
        cache.clear()
        self.payload['realtimeWeather']['items'][0]['temperature']['value'] = -2.5
        get.return_value = self.response()
        self.assertEqual(weather_for(self.layout, True)['temperature'], '-2,5')

    def test_environment_key_takes_priority(self):
        from sedo.services.dashboard_weather import weather_key
        with patch.dict('os.environ', {'SEDO_WEATHER_API_KEY': 'env-key'}):
            self.assertEqual(weather_key(), 'env-key')


class BankBalanceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.env = patch.dict('os.environ', {'SEDO_FINTS_PIN': ''})
        self.env.start()
        self.addCleanup(self.env.stop)
        for key, value in {'blz': '12345678', 'endpoint': 'https://bank.example/fints',
                           'login': 'test-user', 'iban': 'DE1234567890'}.items():
            AppSetting.set('fints_' + key, value)
        self.bank = MagicMock()
        self.bank.__enter__.return_value = self.bank
        self.bank.__exit__.return_value = False
        self.bank.init_tan_response = None
        self.account = SimpleNamespace(iban='DE1234567890')
        self.bank.get_sepa_accounts.return_value = [self.account]
        self.bank.get_balance.return_value = SimpleNamespace(
            amount=SimpleNamespace(amount=Decimal('-42.50'), currency='EUR'), date=date(2026, 9, 20))
        mock = patch('fints.client.FinTS3PinTanClient', return_value=self.bank)
        self.factory = mock.start()
        self.addCleanup(mock.stop)

    def test_real_signed_balance_persists_without_transactions_payments_or_pin(self):
        result = fetch_bank_balance(pin='one-time-pin')
        self.assertEqual(result['amount'], Decimal('-42.50'))
        self.assertEqual(result['account_label'], 'Konto …7890')
        self.assertEqual(result['currency'], 'EUR')
        self.assertEqual(cached_bank_balance(), result)
        self.bank.get_balance.assert_called_once_with(self.account)
        self.bank.get_transactions.assert_not_called()
        self.assertFalse(Payment.all_objects.exists())
        self.assertFalse(AppSetting.objects.filter(value__contains='one-time-pin').exists())
        self.bank.get_balance.return_value.amount.amount = Decimal('0')
        self.assertEqual(fetch_bank_balance(pin='pin')['amount'], 0)

    def test_missing_pin_unknown_or_ambiguous_account_never_requests_balance(self):
        with self.assertRaises(BankingError):
            fetch_bank_balance()
        self.factory.assert_not_called()
        AppSetting.set('fints_iban', 'DE99999')
        with self.assertRaises(BankingError):
            fetch_bank_balance(pin='pin')
        AppSetting.set('fints_iban', '')
        self.bank.get_sepa_accounts.return_value.append(SimpleNamespace(iban='DE00000'))
        with self.assertRaises(BankingError):
            fetch_bank_balance(pin='pin')
        self.bank.get_balance.assert_not_called()

    def test_failure_preserves_snapshot_and_hides_bank_error_details(self):
        fetch_bank_balance(pin='pin')
        original = AppSetting.get('bank_balance_snapshot')
        self.bank.get_balance.side_effect = RuntimeError('private-pin account-details')
        with self.assertRaises(BankingError) as caught:
            fetch_bank_balance(pin='pin')
        self.assertNotIn('private-pin', str(caught.exception))
        self.assertEqual(AppSetting.get('bank_balance_snapshot'), original)
        self.bank.get_balance.side_effect = None
        for invalid in ('NaN', 'Infinity', '1e30'):
            self.bank.get_balance.return_value.amount.amount = Decimal(invalid)
            with self.assertRaises(BankingError):
                fetch_bank_balance(pin='pin')
            self.assertEqual(AppSetting.get('bank_balance_snapshot'), original)

    def test_configuration_change_invalidates_snapshot_and_in_flight_request(self):
        fetch_bank_balance(pin='pin')
        AppSetting.set('fints_login', 'other-user')
        self.assertIsNone(cached_bank_balance())
        original = AppSetting.get('bank_balance_snapshot')
        def changed(_):
            AppSetting.set('fints_iban', 'DE555')
            return self.bank.get_balance.return_value
        self.bank.get_balance.side_effect = changed
        with self.assertRaises(BankingError):
            fetch_bank_balance(pin='pin')
        self.assertEqual(AppSetting.get('bank_balance_snapshot'), original)

    def test_tan_challenge_can_only_use_decoupled_flow(self):
        challenge = type('NeedTANResponse', (), {})()
        challenge.decoupled = False
        self.bank.get_balance.return_value = challenge
        with self.assertRaises(BankingError):
            fetch_bank_balance(pin='pin')
        challenge.decoupled = True
        real = SimpleNamespace(amount=SimpleNamespace(amount=Decimal('100'), currency='EUR'), date=date.today())
        with patch('sedo.services.banking._await_decoupled', return_value=real) as wait:
            self.assertEqual(fetch_bank_balance(pin='pin')['amount'], 100)
            wait.assert_called_once_with(self.bank, challenge)

    def test_parallel_request_lock_and_malformed_snapshot(self):
        from sedo.services.banking import _balance_identity, get_banking_config
        cache.add('dashboard-balance-' + _balance_identity(get_banking_config()), 'another-request', timeout=30)
        with self.assertRaises(BankingError):
            fetch_bank_balance(pin='pin')
        self.factory.assert_not_called()
        for malformed in ('null', '[]', '{', '{}'):
            AppSetting.set('bank_balance_snapshot', malformed)
            self.assertIsNone(cached_bank_balance())
