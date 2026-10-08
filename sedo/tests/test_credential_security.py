"""Synthetic credentials only; network calls are intercepted before transport."""
import json
import os
import smtplib
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from sedo.models import AppSetting
from sedo.services.banking import BankingError, _FinTSSession, fetch_bank_transactions, get_banking_config
from sedo.services.credentials import (
    PREFIX, CredentialError, checked_fints_endpoint, configuration,
    migrate_initialized_credentials, migrate_legacy_credentials, save_configuration, secret_for,
)
from sedo.services.mail import get_smtp_config, send_email_smtp


SMTP = dict(host='mail.example.test', port='587', user='synthetic-user', tls='1', from_name='TEST')
BANK = dict(endpoint='https://bank.example.test/fints', blz='12345678', login='synthetic-user', iban='', product_id='')


class CredentialStorageTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='sedo-credential-test-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        override = override_settings(SEDO_HOME=self.home)
        override.enable()
        self.addCleanup(override.disable)
        environment = patch.dict(os.environ, {'SEDO_SMTP_PASSWORD': '', 'SEDO_FINTS_PIN': ''})
        environment.start()
        self.addCleanup(environment.stop)

    def legacy(self, kind, cfg, secret='synthetic-old-secret'):
        for key, value in cfg.items():
            AppSetting.set(kind + '_' + key, value)
        AppSetting.set('smtp_password' if kind == 'smtp' else 'fints_pin', secret)

    def test_startup_migrates_existing_plaintext_without_changing_target(self):
        for kind, cfg, key in (('smtp', SMTP, 'smtp_password'), ('fints', BANK, 'fints_pin')):
            self.legacy(kind, cfg)
        migrate_legacy_credentials()
        for kind, cfg, key in (('smtp', SMTP, 'smtp_password'), ('fints', BANK, 'fints_pin')):
            value = AppSetting.get(key)
            self.assertTrue(value.startswith(PREFIX))
            self.assertNotIn('synthetic-old-secret', value)
            self.assertEqual(secret_for(kind, cfg), 'synthetic-old-secret')
        if os.name != 'nt':
            self.assertEqual((self.home / 'data/.credentials_key').stat().st_mode & 0o777, 0o600)

    def test_settings_metadata_never_exposes_or_decrypts_secrets(self):
        self.legacy('smtp', SMTP)
        self.legacy('fints', BANK)
        with patch('sedo.services.credentials._cipher', side_effect=AssertionError('not for display')):
            self.assertEqual(get_smtp_config()['password'], '')
            self.assertEqual(get_banking_config()['pin'], '')

    def test_new_credentials_encrypted_and_bound_to_remote_identity(self):
        for kind, cfg, key in (('smtp', SMTP, 'smtp_password'), ('fints', BANK, 'fints_pin')):
            with self.subTest(kind=kind):
                save_configuration(kind, cfg, secret='synthetic-new-secret')
                self.assertNotIn('synthetic-new-secret', AppSetting.get(key))
                self.assertEqual(secret_for(kind, cfg), 'synthetic-new-secret')
                altered = {**cfg, 'user' if kind == 'smtp' else 'login': 'different-user'}
                with self.assertRaises(CredentialError):
                    secret_for(kind, altered)

    def test_legacy_secret_cannot_be_migrated_onto_changed_destination(self):
        for kind, cfg, field, new in (('smtp', SMTP, 'host', 'other.example.test'),
                                      ('fints', BANK, 'endpoint', 'https://other.example.test/fints')):
            with self.subTest(kind=kind):
                self.legacy(kind, cfg)
                with self.assertRaises(CredentialError):
                    save_configuration(kind, {**cfg, field: new})
                self.assertEqual(configuration(kind)[field], cfg[field])
                self.assertEqual(secret_for(kind, cfg), 'synthetic-old-secret')

    def test_legacy_secret_cannot_bind_to_a_callers_stale_or_different_config(self):
        self.legacy('smtp', SMTP)
        with self.assertRaises(CredentialError):
            secret_for('smtp', SMTP | {'host': 'attacker.example.test'})
        self.assertEqual(secret_for('smtp', SMTP), 'synthetic-old-secret')

    def test_same_identity_metadata_change_keeps_credentials(self):
        for kind, cfg, changes in (('smtp', SMTP, {'from_name': 'New sender'}),
                                    ('fints', BANK, {'iban': 'DE99999', 'product_id': 'new-product'})):
            save_configuration(kind, cfg, secret='synthetic-secret')
            saved = save_configuration(kind, cfg | changes)
            self.assertEqual(secret_for(kind, saved), 'synthetic-secret')

    def test_explicit_reentry_binds_new_secret_only_to_new_target(self):
        save_configuration('smtp', SMTP, secret='old-synthetic')
        changed = SMTP | {'host': 'new.example.test'}
        save_configuration('smtp', changed, secret='new-synthetic')
        self.assertEqual(secret_for('smtp', changed), 'new-synthetic')
        with self.assertRaises(CredentialError):
            secret_for('smtp', SMTP)

    def test_explicit_clear_allows_bank_identity_change_without_pin_transfer(self):
        save_configuration('fints', BANK, secret='synthetic-pin')
        changed = BANK | {'endpoint': 'https://new.example.test/fints'}
        save_configuration('fints', changed, clear=True)
        self.assertEqual(secret_for('fints', changed), '')

    def test_environment_secret_cannot_follow_config_edit_even_before_first_use(self):
        for kind, cfg, variable, field, new in (
                ('smtp', SMTP, 'SEDO_SMTP_PASSWORD', 'host', 'new.example.test'),
                ('fints', BANK, 'SEDO_FINTS_PIN', 'endpoint', 'https://new.example.test/fints')):
            save_configuration(kind, cfg)
            with patch.dict(os.environ, {variable: 'synthetic-environment-secret'}):
                with self.assertRaises(CredentialError):
                    save_configuration(kind, cfg | {field: new}, secret='explicit-but-env-wins')
                self.assertEqual(configuration(kind)[field], cfg[field])
                self.assertEqual(secret_for(kind, cfg), 'synthetic-environment-secret')
                AppSetting.set(kind + '_' + field, new)
                with self.assertRaises(CredentialError):
                    secret_for(kind, configuration(kind))
                self.assertFalse(AppSetting.objects.filter(value__contains='synthetic-environment-secret').exists())

    def test_missing_or_corrupt_key_fails_before_smtp_connect(self):
        save_configuration('smtp', SMTP, secret='synthetic-secret')
        key = self.home / 'data/.credentials_key'
        original = key.read_bytes()
        key.unlink()
        with patch('sedo.services.mail.smtplib.SMTP') as connect:
            with self.assertRaises(CredentialError):
                send_email_smtp('recipient@example.test', 'Test', 'Test')
            connect.assert_not_called()
        key.write_bytes(original)
        value = AppSetting.get('smtp_password')
        AppSetting.set('smtp_password', value[:-5] + 'badxx')
        with self.assertRaises(CredentialError):
            secret_for('smtp', SMTP)

    def test_smtp_uses_environment_override_only_for_bound_server(self):
        save_configuration('smtp', SMTP, secret='synthetic-local')
        with patch.dict(os.environ, {'SEDO_SMTP_PASSWORD': 'synthetic-env'}), patch('sedo.services.mail.smtplib.SMTP') as connect:
            send_email_smtp('recipient@example.test', 'Test', 'Test')
            connect.return_value.__enter__.return_value.login.assert_called_once_with(SMTP['user'], 'synthetic-env')

    def test_smtp_error_never_echoes_server_supplied_secret(self):
        save_configuration('smtp', SMTP, secret='synthetic-secret')
        with patch('sedo.services.mail.smtplib.SMTP') as connect:
            connect.return_value.__enter__.return_value.login.side_effect = smtplib.SMTPAuthenticationError(535, b'synthetic-secret')
            with self.assertRaises(smtplib.SMTPException) as caught:
                send_email_smtp('recipient@example.test', 'Test', 'Test')
        self.assertNotIn('synthetic-secret', str(caught.exception))

    def test_http_bank_endpoint_rejected_before_client_or_pin_use(self):
        self.legacy('fints', BANK | {'endpoint': 'http://bank.example.test/fints'})
        with patch('fints.client.FinTS3PinTanClient') as client:
            with self.assertRaises(BankingError):
                fetch_bank_transactions(pin='synthetic-pin')
            client.assert_not_called()

    def test_settings_endpoint_reentry_required_and_failure_is_atomic(self):
        user = get_user_model().objects.create_user('credential-admin', is_staff=True)
        self.client.force_login(user)
        save_configuration('fints', BANK, secret='original-synthetic')
        data = {'action': 'save_banking', **{'fints_' + key: value for key, value in BANK.items()}}
        data['fints_endpoint'] = 'https://new.example.test/fints'
        self.assertEqual(self.client.post(reverse('sedo:settings'), data).status_code, 302)
        self.assertEqual(configuration('fints')['endpoint'], BANK['endpoint'])
        data['fints_pin'] = 'new-synthetic'
        self.assertEqual(self.client.post(reverse('sedo:settings'), data).status_code, 302)
        self.assertEqual(secret_for('fints', configuration('fints')), 'new-synthetic')


class FinTSTransportTests(SimpleTestCase):
    def test_invalid_schemes_and_ambiguous_urls_rejected(self):
        for url in ('http://bank.example.test/fints', 'file:///bank', 'https://user:pin@bank.example.test/',
                    'https://bank.example.test/#fragment', 'https://bank.example.test:99999/',
                    'https://bank.example.test\\@other.example.test/', 'https://bank.example.test/\npath'):
            with self.subTest(url=url), self.assertRaises(CredentialError):
                checked_fints_endpoint(url)
        self.assertEqual(checked_fints_endpoint('https://BANK.EXAMPLE.TEST:443/fints'), BANK['endpoint'])

    def test_https_transport_fixes_timeouts_verification_and_redirect_policy(self):
        response = MagicMock(status_code=200)
        response.iter_content.return_value = [b'one', b'two']
        with _FinTSSession(BANK['endpoint']) as session, patch('requests.Session.post', return_value=response) as post:
            self.assertFalse(session.trust_env)
            result = session.post(BANK['endpoint'], data=b'synthetic-wire', verify=False, allow_redirects=True)
        self.assertEqual(post.call_args.kwargs['timeout'], (5, 20))
        self.assertTrue(post.call_args.kwargs['verify'])
        self.assertFalse(post.call_args.kwargs['allow_redirects'])
        self.assertEqual(result._content, b'onetwo')
        response.close.assert_called_once()

    def test_redirect_different_destination_and_oversized_response_fail_closed(self):
        with _FinTSSession(BANK['endpoint']) as session:
            with patch('requests.Session.post') as post:
                with self.assertRaises(BankingError):
                    session.post('https://different.example.test/fints')
                post.assert_not_called()
            for response in (MagicMock(status_code=307), MagicMock(status_code=200)):
                response.iter_content.return_value = [b'x' * (session.MAX_RESPONSE + 1)]
                with patch('requests.Session.post', return_value=response), self.assertRaises(BankingError):
                    session.post(BANK['endpoint'])
                response.close.assert_called_once()

    def test_startup_scrubs_synthetic_file_database_and_wal(self):
        for entrypoint in ('config.wsgi', 'config.asgi'):
            with self.subTest(entrypoint=entrypoint):
                self._check_startup_scrubbing(entrypoint)

    def _check_startup_scrubbing(self, entrypoint):
        with tempfile.TemporaryDirectory(prefix='sedo-credential-wal-') as directory:
            source = textwrap.dedent('''
                import asyncio, importlib, json, os, sys
                from pathlib import Path
                import django
                django.setup()
                from django.db import connection
                from sedo.models import AppSetting
                from sedo.services.credentials import migrate_legacy_credentials, secret_for, configuration
                with connection.schema_editor() as schema:
                    schema.create_model(AppSetting)
                with connection.cursor() as cursor:
                    cursor.execute('PRAGMA journal_mode=WAL')
                for key, value in {'fints_endpoint': 'https://bank.example.test/fints', 'fints_blz': '12345678',
                                   'fints_login': 'synthetic-user', 'fints_pin': 'synthetic-legacy-pin-audit-12345'}.items():
                    AppSetting.set(key, value)
                if sys.argv[1] == 'config.asgi':
                    async def load_application():
                        importlib.import_module(sys.argv[1])
                    asyncio.run(load_application())
                else:
                    importlib.import_module(sys.argv[1])
                assert secret_for('fints', configuration('fints')) == 'synthetic-legacy-pin-audit-12345'
                files = list((Path(os.environ['SEDO_HOME']) / 'data').glob('*.sqlite3*'))
                assert files
                assert all(b'synthetic-legacy-pin-audit-12345' not in path.read_bytes() for path in files)
                print(json.dumps({'legacy_plaintext_removed': True, 'pin_recoverable_for_original_bank': True}))
            ''')
            (Path(directory) / 'data').mkdir()
            env = {**os.environ, 'SEDO_HOME': directory, 'DJANGO_SETTINGS_MODULE': 'config.settings',
                   'DJANGO_SECRET_KEY': 'synthetic-test-key', 'SEDO_FINTS_PIN': '', 'PYTHONDONTWRITEBYTECODE': '1'}
            result = subprocess.run([sys.executable, '-B', '-c', source, entrypoint], env=env,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)['legacy_plaintext_removed'])

    def test_server_startup_skips_absent_database_or_schema(self):
        with tempfile.TemporaryDirectory(prefix='sedo-credential-empty-') as directory:
            with patch('sedo.services.credentials.connection') as database, patch('sedo.services.credentials.migrate_legacy_credentials') as migrate:
                database.vendor = 'sqlite'
                database.settings_dict = {'NAME': str(Path(directory) / 'missing.sqlite3')}
                self.assertFalse(migrate_initialized_credentials())
                database.introspection.table_names.assert_not_called()
                migrate.assert_not_called()
                database.settings_dict = {'NAME': ':memory:'}
                database.introspection.table_names.return_value = []
                self.assertFalse(migrate_initialized_credentials())
                migrate.assert_not_called()

    def test_server_startup_propagates_security_failure(self):
        with patch('sedo.services.credentials.connection') as database, patch('sedo.services.credentials.migrate_legacy_credentials', side_effect=CredentialError('synthetic failure')):
            database.vendor = 'sqlite'
            database.settings_dict = {'NAME': ':memory:'}
            database.introspection.table_names.return_value = ['sedo_appsetting']
            with self.assertRaises(CredentialError):
                migrate_initialized_credentials()
