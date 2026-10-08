"""Permissions and login abuse defenses exercised through real Django routes."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings

from sedo.middleware import RoleRequiredMiddleware, TrustedProxyHeadersMiddleware
from sedo.models import AppSetting
from sedo.security import LoginThrottle, client_address


class PrivateHomeMixin:
    def setUp(self):
        super().setUp()
        self.home = tempfile.TemporaryDirectory(prefix='sedo-auth-test-')
        self.addCleanup(self.home.cleanup)
        self.override = override_settings(SEDO_HOME=Path(self.home.name),
            SEDO_BACKUP_DIR=Path(self.home.name) / 'backups',
            MEDIA_ROOT=Path(self.home.name) / 'media')
        self.override.enable()
        self.addCleanup(self.override.disable)


class AuthorizationTests(PrivateHomeMixin, TestCase):
    def setUp(self):
        super().setUp()
        AppSetting.set('auto_weekly_backup_enabled', '0')
        self.user = get_user_model().objects.create_user('worker', password='Strong-test-passphrase!')
        self.client.force_login(self.user)

    def test_ordinary_user_cannot_reconfigure_secrets_or_manage_backups(self):
        AppSetting.set('smtp_host', 'original.invalid')
        AppSetting.set('smtp_password', 'synthetic-existing-secret')
        for path, data in [
            ('/einstellungen/', {'action': 'save_smtp', 'smtp_host': 'changed.invalid'}),
            ('/einstellungen/', {'action': 'save_banking', 'fints_endpoint': 'https://changed.invalid'}),
            ('/einstellungen/', {'action': 'test_smtp', 'test_email': 'target@example.invalid'}),
            ('/backup/', {'action': 'disable_weekly'}),
            ('/backup/', {'action': 'stage_restore', 'confirmation': 'WIEDERHERSTELLEN'}),
            ('/startklar/windows_acceptance/', {'checked': '1'}),
        ]:
            with self.subTest(path=path, action=data):
                self.assertEqual(self.client.post(path, data).status_code, 403)
        self.assertEqual(AppSetting.get('smtp_host'), 'original.invalid')
        self.assertEqual(AppSetting.get('smtp_password'), 'synthetic-existing-secret')
        self.assertEqual(self.client.get('/backup/?backup=any.zip').status_code, 403)
        self.assertEqual(self.client.get('/einstellungen/').status_code, 403)

    def test_staff_can_manage_installation(self):
        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])
        self.assertEqual(self.client.post('/backup/', {'action': 'enable_weekly'}).status_code, 302)
        self.assertEqual(AppSetting.get('auto_weekly_backup_enabled'), '1')

    def test_readonly_blocks_all_unsafe_app_routes_before_views(self):
        self.user.groups.add(Group.objects.get_or_create(name='SEDO Mitarbeiter Lesen')[0])
        from sedo.urls import urlpatterns
        for pattern in urlpatterns:
            route = str(pattern.pattern)
            for key, converter in pattern.pattern.converters.items():
                value = '1' if converter.__class__.__name__ == 'IntConverter' else 'probe'
                import re
                route = re.sub(r'<(?:[^:>]+:)?' + key + '>', value, route)
            for method in ('post', 'put', 'patch', 'delete'):
                with self.subTest(route=route, method=method):
                    self.assertEqual(getattr(self.client, method)('/' + route).status_code, 403)

    def test_readonly_blocks_stateful_gets_and_allows_reviewed_reads(self):
        self.user.groups.add(Group.objects.get_or_create(name='SEDO Mitarbeiter Lesen')[0])
        for path in ('/offene-posten/', '/kassenbuch/pdf/', '/email/entwuerfe/',
                     '/email/entwurf/1/pruefen/', '/rechnungen/1/zugferd-pruefung/', '/admin/'):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 403)
        self.assertEqual(self.client.get('/kunden/').status_code, 200)
        self.assertEqual(self.client.get('/rechnungen/').status_code, 200)
        self.assertEqual(self.client.post('/logout/').status_code, 302)

    def test_readonly_is_fail_closed_for_new_views_even_if_staff(self):
        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])
        self.user.groups.add(Group.objects.get_or_create(name='SEDO Mitarbeiter Lesen')[0])
        request = RequestFactory().get('/future/')
        request.user = self.user
        request.resolver_match = SimpleNamespace(view_name='sedo:future_route')
        middleware = RoleRequiredMiddleware(lambda req: HttpResponse('ok'))
        self.assertEqual(middleware.process_view(request, None, (), {}).status_code, 403)
        self.assertEqual(self.client.post('/backup/', {'action': 'enable_weekly'}).status_code, 403)

    def test_ordinary_business_post_still_works(self):
        response = self.client.post('/kunden/neu/', {'name': 'Synthetic Customer', 'country': 'DE',
            'customer_type': 'einmalig', 'payment_days': 14})
        self.assertNotEqual(response.status_code, 403)

    def test_csrf_and_anonymous_boundaries_remain_enforced(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.get('/einstellungen/').status_code, 302)
        self.assertEqual(client.get('/media/secret.pdf').status_code, 302)
        client.force_login(self.user)
        self.assertEqual(client.post('/kunden/neu/', {'name': 'Synthetic'}).status_code, 403)


class LoginEndpointTests(PrivateHomeMixin, TestCase):
    def setUp(self):
        super().setUp()
        AppSetting.set('auto_weekly_backup_enabled', '0')
        self.user = get_user_model().objects.create_user('target', password='Strong-test-passphrase!', is_staff=True)

    def test_login_and_admin_share_persistent_limit_and_cooldown(self):
        with patch.object(LoginThrottle, 'LIMITS', {'pair': 2, 'account': 4, 'address': 20}):
            self.assertEqual(self.client.post('/login/', {'username': 'target', 'password': 'wrong'}).status_code, 200)
            self.assertEqual(Client().post('/admin/login/', {'username': 'target', 'password': 'wrong'}).status_code, 200)
            blocked = Client().post('/login/', {'username': 'target', 'password': 'Strong-test-passphrase!'})
            self.assertEqual(blocked.status_code, 429)
            self.assertGreater(int(blocked['Retry-After']), 0)
            self.assertEqual(blocked['Cache-Control'], 'no-store')
            with patch('sedo.security.time.time', return_value=10_000_000_000):
                self.assertEqual(Client().post('/login/', {'username': 'target', 'password': 'Strong-test-passphrase!'}).status_code, 302)

    def test_success_resets_account_not_address_budget(self):
        with patch.object(LoginThrottle, 'LIMITS', {'pair': 2, 'account': 4, 'address': 3}):
            self.client.post('/login/', {'username': 'target', 'password': 'wrong'})
            self.assertEqual(self.client.post('/login/', {'username': 'target', 'password': 'Strong-test-passphrase!'}).status_code, 302)
            self.assertEqual(Client().post('/login/', {'username': 'target', 'password': 'wrong'}).status_code, 200)
            self.assertEqual(Client().post('/login/', {'username': 'different', 'password': 'wrong'}).status_code, 429)

    def test_unavailable_throttle_fails_closed(self):
        with patch('sedo.security.LoginThrottle._connect', side_effect=sqlite3.OperationalError('synthetic')):
            response = self.client.post('/login/', {'username': 'target', 'password': 'Strong-test-passphrase!'})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_cross_origin_login_remains_csrf_protected(self):
        client = Client(enforce_csrf_checks=True)
        client.get('/login/')
        response = client.post('/login/', {'username': 'target', 'password': 'Strong-test-passphrase!',
            'csrfmiddlewaretoken': client.cookies['csrftoken'].value}, HTTP_ORIGIN='https://attacker.trycloudflare.com')
        self.assertEqual(response.status_code, 403)


class ThrottleStorageTests(PrivateHomeMixin, SimpleTestCase):
    def test_reservations_are_atomic_across_instances(self):
        request = RequestFactory().post('/login/', {'username': 'synthetic-account'})
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: LoginThrottle().reserve(request)[1], range(16)))
        self.assertEqual(results.count(0), 10)
        path = Path(self.home.name) / 'data/security/login-attempts.sqlite3'
        contents = path.read_bytes()
        self.assertNotIn(b'synthetic-account', contents)
        self.assertNotIn(b'127.0.0.1', contents)

    def test_storage_capacity_and_expiry_are_bounded(self):
        with patch.object(LoginThrottle, 'MAX_ROWS', 3):
            first = RequestFactory().post('/login/', {'username': 'first'})
            second = RequestFactory().post('/login/', {'username': 'second'})
            self.assertEqual(LoginThrottle().reserve(first)[1], 0)
            self.assertGreater(LoginThrottle().reserve(second)[1], 0)
            with patch('sedo.security.time.time', return_value=10_000_000_000):
                self.assertEqual(LoginThrottle().reserve(second)[1], 0)

    @override_settings(SEDO_TRUSTED_PROXIES=[])
    def test_forwarding_headers_from_untrusted_peer_are_ignored(self):
        request = RequestFactory().post('/login/', HTTP_X_FORWARDED_FOR='203.0.113.99',
            HTTP_X_FORWARDED_PROTO='https', REMOTE_ADDR='192.0.2.1')
        self.assertEqual(client_address(request), '192.0.2.1')
        TrustedProxyHeadersMiddleware(lambda req: HttpResponse())(request)
        self.assertNotIn('HTTP_X_FORWARDED_PROTO', request.META)
        self.assertNotIn('HTTP_X_FORWARDED_FOR', request.META)

    @override_settings(SEDO_TRUSTED_PROXIES=['127.0.0.1/32'])
    def test_trusted_proxy_chain_uses_nearest_untrusted_hop(self):
        request = RequestFactory().post('/login/', HTTP_X_FORWARDED_FOR='198.51.100.9, 203.0.113.4, 127.0.0.1', REMOTE_ADDR='127.0.0.1')
        self.assertEqual(client_address(request), '203.0.113.4')

    def test_ipv6_rotation_cannot_evade_address_budget(self):
        first = RequestFactory().post('/login/', REMOTE_ADDR='2001:db8:1::1')
        second = RequestFactory().post('/login/', REMOTE_ADDR='2001:db8:1::2')
        self.assertEqual(client_address(first), client_address(second))

    def test_password_validation_rejects_short_numeric_and_common_secrets(self):
        for password in ('a', '1234567890123456', 'password'):
            with self.subTest(password=password), self.assertRaises(ValidationError):
                validate_password(password)
        validate_password('A-long-unique-test-passphrase!')
