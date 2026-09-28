"""SEC-01..06: real HTTP boundaries and synthetic, isolated regressions only."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import SetPasswordForm
from django.core.management import call_command
from django.test import Client, TestCase, SimpleTestCase, override_settings
from django.urls import reverse

from sedo.models import AppSetting
from sedo.services.credential_binding import (CredentialError, identity_digest, load_config, save_config, validate_config)
from sedo.services.mail import get_smtp_config, send_email_smtp
from sedo.services.banking import BankingError, fetch_bank_balance, fetch_bank_transactions

SMTP = {'host': 'smtp.example.invalid', 'port': 587, 'user': 'test@example.invalid', 'tls': True, 'from_name': 'Test'}
BANK = {'endpoint': 'https://bank.example.invalid/fints', 'blz': '12345678', 'login': 'synthetic-user', 'iban': '', 'product_id': ''}
PASSWORD = 'Nur-synthetische-Testdaten-2026!'


class IsolatedTestCase(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='sedo-remediation-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.override = override_settings(SEDO_HOME=self.home, MEDIA_ROOT=self.home/'media', SEDO_BACKUP_DIR=self.home/'backups')
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.env = patch.dict(os.environ, {'SEDO_SMTP_PASSWORD': '', 'SEDO_FINTS_PIN': '', 'SEDO_SMTP_IDENTITY_SHA256': '', 'SEDO_FINTS_IDENTITY_SHA256': ''})
        self.env.start()
        self.addCleanup(self.env.stop)
        AppSetting.set('auto_weekly_backup_enabled', '0')
        self.user = get_user_model().objects.create_user('security-ordinary', password=PASSWORD)
        self.admin = get_user_model().objects.create_user('security-staff', password=PASSWORD, is_staff=True)


class AdministrativeBoundaryTests(IsolatedTestCase):
    def test_every_system_setting_post_requires_staff_before_writes(self):
        self.client.force_login(self.user)
        before = dict(AppSetting.objects.values_list('key', 'value'))
        actions = ['save_smtp', 'save_banking', 'test_smtp', 'save_ai', 'start_ai', 'test_ai', 'save_weather', 'import_mapping', 'save_settings', 'unknown']
        with patch('sedo.views.send_email_smtp') as send:
            for action in actions:
                with self.subTest(action=action):
                    response = self.client.post(reverse('sedo:settings'), {'action': action, 'smtp_host': 'other.invalid', 'test_email': 'nobody@example.invalid'})
                    self.assertEqual(response.status_code, 403)
            send.assert_not_called()
        self.assertEqual(dict(AppSetting.objects.values_list('key', 'value')), before)

    def test_settings_and_backup_reads_require_staff(self):
        self.client.force_login(self.user)
        for route in ('settings', 'backup'):
            self.assertEqual(self.client.get(reverse('sedo:'+route)).status_code, 403)

    def test_backup_actions_are_denied_before_inspection_or_restore(self):
        self.client.force_login(self.user)
        with patch('sedo.backup_utils.create_backup') as backup, patch('sedo.backup_utils.inspect_backup') as inspect, patch('sedo.backup_utils.stage_restore') as restore:
            for action in ('backup_now', 'disable_weekly', 'enable_weekly', 'inspect_restore', 'stage_restore'):
                with self.subTest(action=action):
                    response = self.client.post(reverse('sedo:backup'), {'action': action, 'backup': 'fake.zip', 'review_sha256': '0'*64, 'confirmation': 'WIEDERHERSTELLEN'})
                    self.assertEqual(response.status_code, 403)
            for service in (backup, inspect, restore):
                service.assert_not_called()
        self.assertEqual(AppSetting.get('auto_weekly_backup_enabled'), '0')
        self.assertFalse((self.home/'pending_restore.json').exists())

    def test_anonymous_requests_still_redirect_to_login(self):
        for route in ('settings', 'backup'):
            for verb in ('get', 'post'):
                self.assertEqual(getattr(self.client, verb)(reverse('sedo:'+route)).status_code, 302)

    def test_staff_can_save_smtp_and_secret_is_not_plaintext(self):
        self.client.force_login(self.admin)
        data = {'action': 'save_smtp', **{'smtp_'+key: value for key, value in SMTP.items()}, 'smtp_password': PASSWORD, 'smtp_tls': '1'}
        response = self.client.post(reverse('sedo:settings'), data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(get_smtp_config()['password'], PASSWORD)
        self.assertNotIn(PASSWORD, AppSetting.get('smtp_password'))
        self.assertNotContains(self.client.get(reverse('sedo:settings')), PASSWORD)

    def test_staff_can_save_banking(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('sedo:settings'), {'action': 'save_banking', **{'fints_'+key: value for key, value in BANK.items()}, 'fints_pin': PASSWORD})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(load_config('fints')['pin'], PASSWORD)
        self.assertNotIn(PASSWORD, AppSetting.get('fints_pin'))

    def test_staff_backup_enable_disable_remains_usable(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('sedo:backup'), {'action': 'enable_weekly'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(AppSetting.get('auto_weekly_backup_enabled'), '1')

    def test_staff_write_still_requires_csrf(self):
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.admin)
        response = csrf.post(reverse('sedo:settings'), {'action': 'save_smtp', 'smtp_password': PASSWORD})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(AppSetting.get('smtp_password', ''))

    def test_csrf_token_allows_authorized_configuration_write(self):
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.admin)
        self.assertEqual(csrf.get(reverse('sedo:settings')).status_code, 200)
        data = {'action': 'save_smtp', **{'smtp_'+key: value for key, value in SMTP.items()}, 'smtp_password': PASSWORD,
                'smtp_tls': '1', 'csrfmiddlewaretoken': csrf.cookies['csrftoken'].value}
        self.assertEqual(csrf.post(reverse('sedo:settings'), data).status_code, 302)
        self.assertEqual(get_smtp_config()['password'], PASSWORD)


class IdentityBindingTests(IsolatedTestCase):
    def test_every_smtp_identity_change_revokes_old_secret(self):
        for changes in ({'host': 'different.invalid'}, {'port': 465}, {'user': 'someone@example.invalid'}, {'tls': False}):
            with self.subTest(changes=changes):
                save_config('smtp', SMTP, PASSWORD)
                self.assertTrue(save_config('smtp', SMTP | changes))
                self.assertEqual(load_config('smtp')['password'], '')

    def test_same_identity_keeps_secret_but_display_name_may_change(self):
        save_config('smtp', SMTP, PASSWORD)
        self.assertFalse(save_config('smtp', SMTP | {'from_name': 'New label'}))
        self.assertEqual(load_config('smtp')['password'], PASSWORD)

    def test_reentry_binds_new_secret_to_new_identity(self):
        save_config('smtp', SMTP, PASSWORD)
        save_config('smtp', SMTP | {'host': 'new.invalid'}, 'new-synthetic-secret')
        self.assertEqual(load_config('smtp')['password'], 'new-synthetic-secret')

    def test_ciphertext_cannot_be_replayed_under_different_identity(self):
        save_config('smtp', SMTP, PASSWORD)
        old = AppSetting.get('smtp_password')
        AppSetting.set('smtp_host', 'attacker.invalid')
        AppSetting.set('smtp_password', old)
        self.assertEqual(load_config('smtp')['password'], '')

    def test_ciphertext_cannot_be_replayed_across_services(self):
        save_config('smtp', SMTP, PASSWORD)
        save_config('fints', BANK, PASSWORD)
        AppSetting.set('fints_pin', AppSetting.get('smtp_password'))
        self.assertEqual(load_config('fints')['pin'], '')

    def test_corrupt_ciphertext_fails_closed(self):
        save_config('smtp', SMTP, PASSWORD)
        AppSetting.set('smtp_password', AppSetting.get('smtp_password')[:-8]+'corrupt!')
        self.assertEqual(load_config('smtp')['password'], '')

    def test_legacy_plaintext_is_never_silently_trusted(self):
        save_config('smtp', SMTP)
        AppSetting.set('smtp_password', PASSWORD)
        self.assertEqual(load_config('smtp')['password'], '')
        self.assertEqual(AppSetting.get('smtp_password'), PASSWORD)  # Not deleted by a read.

    def test_env_secret_requires_operator_pinned_identity(self):
        save_config('smtp', SMTP, 'database-secret')
        with patch.dict(os.environ, {'SEDO_SMTP_PASSWORD': PASSWORD}):
            self.assertEqual(load_config('smtp')['password'], '')
            with patch.dict(os.environ, {'SEDO_SMTP_IDENTITY_SHA256': identity_digest('smtp', validate_config('smtp', SMTP))}):
                self.assertEqual(load_config('smtp')['password'], PASSWORD)
                save_config('smtp', SMTP | {'host': 'attacker.invalid'})
                self.assertEqual(load_config('smtp')['password'], '')

    def test_bank_env_secret_never_follows_a_changed_endpoint(self):
        save_config('fints', BANK)
        digest = identity_digest('fints', validate_config('fints', BANK))
        with patch.dict(os.environ, {'SEDO_FINTS_PIN': PASSWORD, 'SEDO_FINTS_IDENTITY_SHA256': digest}):
            self.assertEqual(load_config('fints')['pin'], PASSWORD)
            AppSetting.set('fints_endpoint', 'https://attacker.invalid/fints')
            self.assertEqual(load_config('fints')['pin'], '')

    def test_all_bank_identity_components_are_bound(self):
        for key, value in [('endpoint', 'https://new.invalid/'), ('login', 'different'), ('blz', '87654321'), ('iban', 'DE123'), ('product_id', 'new-product')]:
            with self.subTest(key=key):
                save_config('fints', BANK, PASSWORD)
                self.assertTrue(save_config('fints', BANK | {key: value}))
                self.assertEqual(load_config('fints')['pin'], '')

    def test_invalid_update_does_not_partially_overwrite_config(self):
        save_config('smtp', SMTP, PASSWORD)
        before = dict(AppSetting.objects.values_list('key', 'value'))
        with self.assertRaises(CredentialError):
            save_config('smtp', SMTP | {'port': 'bad'}, 'different')
        self.assertEqual(dict(AppSetting.objects.values_list('key', 'value')), before)

    def test_plain_http_or_embedded_credentials_are_rejected_for_bank(self):
        for endpoint in ('http://bank.invalid/', 'https://user:secret@bank.invalid/', 'https://bank.invalid/#secret', 'https://bank.invalid:123/'):
            with self.subTest(endpoint=endpoint), self.assertRaises(CredentialError):
                save_config('fints', BANK | {'endpoint': endpoint}, PASSWORD)

    def test_explicit_clear_revokes_secret(self):
        save_config('smtp', SMTP, PASSWORD)
        save_config('smtp', SMTP, clear=True)
        self.assertEqual(load_config('smtp')['password'], '')

    def test_no_smtp_connection_after_identity_change(self):
        save_config('smtp', SMTP, PASSWORD)
        AppSetting.set('smtp_host', 'attacker.invalid')
        with patch('sedo.services.mail.smtplib.SMTP') as smtp, patch('sedo.services.mail.smtplib.SMTP_SSL') as ssl:
            with self.assertRaises(ValueError):
                send_email_smtp('recipient@example.invalid', 'Test', 'Synthetic')
            smtp.assert_not_called()
            ssl.assert_not_called()

    def test_bound_smtp_still_authenticates_with_verified_tls(self):
        save_config('smtp', SMTP, PASSWORD)
        with patch('sedo.services.mail.smtplib.SMTP') as smtp:
            send_email_smtp('recipient@example.invalid', 'Test', 'Synthetic')
            server = smtp.return_value.__enter__.return_value
            server.login.assert_called_once_with(SMTP['user'], PASSWORD)
            self.assertTrue(server.starttls.call_args.kwargs['context'].check_hostname)
            server.send_message.assert_called_once()

    def test_both_bank_consumers_stop_before_connecting_after_change(self):
        save_config('fints', BANK, PASSWORD)
        AppSetting.set('fints_endpoint', 'https://attacker.invalid/')
        with patch('fints.client.FinTS3PinTanClient') as client:
            for fn in (fetch_bank_transactions, fetch_bank_balance):
                with self.subTest(function=fn.__name__), self.assertRaises(BankingError):
                    fn()
            client.assert_not_called()

    def test_secrets_removed_from_backups(self):
        from sedo.backup_utils import SECRET_SETTING_KEYS
        self.assertIn('smtp_password', SECRET_SETTING_KEYS)
        self.assertIn('fints_pin', SECRET_SETTING_KEYS)


class PasswordPolicyTests(IsolatedTestCase):
    def test_one_character_password_rejected(self):
        self.assertFalse(SetPasswordForm(self.user, {'new_password1': '1', 'new_password2': '1'}).is_valid())

    def test_short_common_numeric_passwords_rejected(self):
        for password in ('shortpass!', '12345678901234567890', 'password'):
            with self.subTest(password=password):
                self.assertFalse(SetPasswordForm(self.user, {'new_password1': password, 'new_password2': password}).is_valid())

    def test_long_password_is_accepted(self):
        self.assertTrue(SetPasswordForm(self.user, {'new_password1': PASSWORD, 'new_password2': PASSWORD}).is_valid())

    def test_bootstrap_admin_password_satisfies_policy(self):
        from django.contrib.auth.password_validation import validate_password
        output = StringIO()
        call_command('ensure_admin', stdout=output)
        password = next(line.split(':', 1)[1].strip() for line in output.getvalue().splitlines() if 'Einmalig angezeigtes Passwort:' in line)
        self.assertGreaterEqual(len(password), 15)
        validate_password(password, get_user_model().objects.get(username='admin'))


class LoginThrottleTests(IsolatedTestCase):
    def test_sixth_attempt_is_blocked_even_with_correct_password(self):
        for _ in range(5):
            self.assertEqual(self.client.post('/login/', {'username': self.user.username, 'password': 'wrong'}).status_code, 200)
        response = self.client.post('/login/', {'username': self.user.username, 'password': PASSWORD})
        self.assertEqual(response.status_code, 429)
        self.assertGreater(int(response['Retry-After']), 0)
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_admin_login_and_normal_login_share_account_budget(self):
        for _ in range(5):
            self.client.post('/login/', {'username': self.admin.username, 'password': 'wrong'})
        self.assertEqual(self.client.post('/admin/login/', {'username': self.admin.username, 'password': PASSWORD}).status_code, 429)

    def test_admin_login_is_also_throttled_when_used_first(self):
        for _ in range(5):
            self.assertEqual(self.client.post('/admin/login/', {'username': self.admin.username, 'password': 'wrong'}).status_code, 200)
        self.assertEqual(self.client.post('/admin/login/', {'username': self.admin.username, 'password': PASSWORD}).status_code, 429)

    def test_rotating_ip_or_proxy_header_does_not_reset_account_budget(self):
        for number in range(5):
            self.client.post('/login/', {'username': self.user.username, 'password': 'wrong'}, REMOTE_ADDR='192.0.2.'+str(number), HTTP_X_FORWARDED_FOR='198.51.100.'+str(number))
        self.assertEqual(self.client.post('/login/', {'username': self.user.username, 'password': PASSWORD}, REMOTE_ADDR='203.0.113.1').status_code, 429)

    def test_other_local_account_is_not_locked_by_single_account_failure(self):
        for _ in range(5):
            self.client.post('/login/', {'username': self.user.username, 'password': 'wrong'})
        self.assertEqual(self.client.post('/login/', {'username': self.admin.username, 'password': PASSWORD}).status_code, 302)

    def test_success_resets_account_failures_before_limit(self):
        for _ in range(4):
            self.client.post('/login/', {'username': self.user.username, 'password': 'wrong'})
        self.assertEqual(self.client.post('/login/', {'username': self.user.username, 'password': PASSWORD}).status_code, 302)
        self.client.logout()
        self.assertEqual(self.client.post('/login/', {'username': self.user.username, 'password': PASSWORD}).status_code, 302)

    def test_time_expiry_releases_account(self):
        from sedo.login_throttle import reserve
        for _ in range(5):
            self.assertEqual(reserve('time-test', '127.0.0.1', now=1000)[0], 0)
        self.assertGreater(reserve('time-test', '127.0.0.1', now=1001)[0], 0)
        self.assertEqual(reserve('time-test', '127.0.0.1', now=1301)[0], 0)

    def test_concurrent_reservations_never_exceed_limit(self):
        from sedo.login_throttle import reserve
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _: reserve('concurrent', '127.0.0.1')[0], range(20)))
        self.assertEqual(results.count(0), 5)
        self.assertEqual(sum(x > 0 for x in results), 15)

    def test_storage_contains_no_cleartext_username_ip_or_password(self):
        from sedo.login_throttle import reserve
        reserve('sensitive-test-identity', '192.0.2.123')
        data = (self.home/'security-state/login.sqlite3').read_bytes()
        for token in (b'sensitive-test-identity', b'192.0.2.123', PASSWORD.encode()):
            self.assertNotIn(token, data)

    def test_store_failures_deny_login_instead_of_disabling_throttle(self):
        with patch('sedo.login_throttle._connect', side_effect=sqlite3.OperationalError('private-detail')):
            response = self.client.post('/login/', {'username': self.user.username, 'password': PASSWORD})
        self.assertEqual(response.status_code, 503)
        self.assertNotContains(response, 'private-detail', status_code=503)

    def test_csrf_failure_does_not_consume_budget(self):
        client = Client(enforce_csrf_checks=True)
        for _ in range(8):
            self.assertEqual(client.post('/login/', {'username': self.user.username, 'password': 'wrong'}).status_code, 403)
        self.assertEqual(self.client.post('/login/', {'username': self.user.username, 'password': PASSWORD}).status_code, 302)

    def test_source_budget_limits_password_spraying(self):
        from sedo.login_throttle import reserve
        for number in range(100):
            self.assertEqual(reserve('different-'+str(number), '192.0.2.1')[0], 0)
        self.assertGreater(reserve('yet-another', '192.0.2.1')[0], 0)

    def test_new_process_observes_existing_account_lock(self):
        from sedo.login_throttle import reserve
        for _ in range(5):
            reserve('persisted-lock', '127.0.0.1')
        env = dict(os.environ, SEDO_HOME=str(self.home), DJANGO_SECRET_KEY=settings.SECRET_KEY, DJANGO_SETTINGS_MODULE='config.settings')
        script = "import django; django.setup(); from sedo.login_throttle import reserve; print(reserve('persisted-lock','127.0.0.1')[0])"
        result = subprocess.run([sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[2], env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreater(int(result.stdout.strip()), 0)


class DocumentBudgetTests(SimpleTestCase):
    def test_regular_invoice_amount_formats_remain_supported(self):
        from sedo.services.incoming_detection import classify_text
        for amount in ('123,45 EUR', '123.45 USD', '1.234,56 €', 'EUR 123,45', 'Gesamtbetrag: 123,45', 'Total: 123.45'):
            with self.subTest(amount=amount):
                self.assertEqual(classify_text('Rechnung 123\n'+amount).kind, 'invoice')

    def test_overlong_numeric_runs_cannot_be_matched_as_suffix_amounts(self):
        from sedo.services.incoming_detection import classify_text
        for number in ('9'*24000, '9'*24000+',99 EUR', '9'*200+',99 EUR', '9.'*10000):
            self.assertEqual(classify_text('Rechnung 123\n'+number).kind, 'unknown')

    def test_other_document_heading_wins(self):
        from sedo.services.incoming_detection import classify_text
        self.assertEqual(classify_text('Angebot\nRechnungsnummer: 123\n100,00 EUR').kind, 'other')

    def test_real_pdf_is_classified_in_bounded_child(self):
        from reportlab.pdfgen import canvas
        from sedo.services.incoming_detection import classify_document
        raw = BytesIO()
        doc = canvas.Canvas(raw)
        doc.drawString(30, 800, 'Rechnung 123')
        doc.drawString(30, 770, 'Gesamtbetrag: 123,45 EUR')
        doc.save()
        self.assertEqual(classify_document(raw.getvalue(), 'rechnung.pdf').kind, 'invoice')

    def test_oversize_document_is_rejected_without_spawning(self):
        from sedo.services.incoming_detection import classify_document, MAX_DOCUMENT_BYTES
        with patch('sedo.services.incoming_detection.subprocess.Popen') as start:
            self.assertEqual(classify_document(b'0'*(MAX_DOCUMENT_BYTES+1), 'x.pdf').kind, 'unknown')
            start.assert_not_called()

    def test_timeout_kills_child_and_returns_unknown(self):
        from sedo.services.incoming_detection import classify_document
        child = MagicMock()
        child.wait.side_effect = [subprocess.TimeoutExpired('test', 5), 0]
        child.poll.return_value = None
        with patch('sedo.services.incoming_detection.subprocess.Popen', return_value=child):
            self.assertEqual(classify_document(b'%PDF-', 'x.pdf').kind, 'unknown')
        child.kill.assert_called_once()

    def test_child_environment_does_not_receive_secrets(self):
        from sedo.services.incoming_detection import classify_document
        child = MagicMock()
        child.communicate.return_value = (b'{"kind":"unknown","reason":"test"}', None)
        child.returncode = 0
        with patch.dict(os.environ, {'SEDO_FINTS_PIN': PASSWORD, 'DJANGO_SECRET_KEY': PASSWORD, 'SEDO_SMTP_PASSWORD': PASSWORD}), patch('sedo.services.incoming_detection.subprocess.Popen', return_value=child) as start:
            classify_document(b'%PDF-', 'x.pdf')
        env = start.call_args.kwargs['env']
        for name in ('SEDO_FINTS_PIN', 'DJANGO_SECRET_KEY', 'SEDO_SMTP_PASSWORD'):
            self.assertNotIn(name, env)

    def test_malformed_worker_output_never_auto_approves(self):
        from sedo.services.incoming_detection import classify_document
        for output in (b'garbage', b'[]', b'{"kind":"invoice","reason":17}', b'0'*4097):
            child = MagicMock(returncode=0)
            child.returncode = 0
            def start(command, **kwargs):
                (Path(command[-1]) / 'result.json').write_bytes(output)
                return child
            with patch('sedo.services.incoming_detection.subprocess.Popen', side_effect=start):
                self.assertEqual(classify_document(b'%PDF-', 'x.pdf').kind, 'unknown')

    def test_worker_protocol_does_not_require_console_streams(self):
        import struct
        from reportlab.pdfgen import canvas
        data = BytesIO()
        doc = canvas.Canvas(data)
        doc.drawString(30, 800, 'Rechnung 123')
        doc.drawString(30, 770, 'Gesamtbetrag: 123,45 EUR')
        doc.save()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            header = json.dumps({'filename':'rechnung.pdf', 'subject':''}).encode()
            (root/'request.bin').write_bytes(struct.pack('!I', len(header))+header+data.getvalue())
            script = "import sys; from sedo.document_probe import worker_main; sys.stdin=sys.stdout=sys.stderr=None; raise SystemExit(worker_main(sys.argv[1]))"
            result = subprocess.run([sys.executable, '-c', script, directory], cwd=Path(__file__).resolve().parents[2], timeout=8)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(json.loads((root/'result.json').read_text())['kind'], 'invoice')

    def test_xml_entities_are_not_auto_imported(self):
        from sedo.services.incoming_detection import classify_document
        data = b'<!DOCTYPE Invoice [<!ENTITY x SYSTEM "file:///not-a-real-file">]><Invoice>&x;</Invoice>'
        self.assertEqual(classify_document(data, 'rechnung.xml').kind, 'unknown')


class NativeSecuritySelfCheckTests(TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='sedo_build_check_')
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name)
        scope = override_settings(SEDO_HOME=self.home, MEDIA_ROOT=self.home/'media', SEDO_BACKUP_DIR=self.home/'backups')
        scope.enable()
        self.addCleanup(scope.disable)
        env = patch.dict(os.environ, {'SEDO_FINTS_PIN':'', 'SEDO_SMTP_PASSWORD':''})
        env.start()
        self.addCleanup(env.stop)
        AppSetting.set('auto_weekly_backup_enabled', '0')

    def test_all_source_self_checks_run_without_external_connections(self):
        from sedo.security_selfcheck import check
        result = check(self.home)
        self.assertEqual(len(result), 7)
        self.assertTrue(all(result.values()), result)

    def test_mismatching_home_is_rejected(self):
        from sedo.security_selfcheck import check
        with self.assertRaises(RuntimeError):
            check(self.home/'not-the-configured-home')

    def test_non_build_home_is_rejected(self):
        from sedo.security_selfcheck import check
        with tempfile.TemporaryDirectory(prefix='not-build-') as directory, override_settings(SEDO_HOME=Path(directory)), self.assertRaises(RuntimeError):
            check(Path(directory))

    def test_inherited_credentials_are_rejected(self):
        from sedo.security_selfcheck import check
        with patch.dict(os.environ, {'SEDO_FINTS_PIN':'synthetic-inherited-pin'}), self.assertRaises(RuntimeError):
            check(self.home)
