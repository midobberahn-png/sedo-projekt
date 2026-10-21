"""The hosted profile and desktop client must not silently fall back to local data."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings

import desktop
from sedo.services.ai_local import get_ai_config
from sedo.services.transcription import TranscriptionUnavailable, stt_status, transcribe_audio
from sedo.web_origin import validate_origin


class WebOriginTests(SimpleTestCase):
    def test_only_exact_https_origin_is_accepted(self):
        self.assertEqual(validate_origin('https://rechnung.example.de/'), 'https://rechnung.example.de')
        for value in ('http://rechnung.example.de', 'https://user:pw@rechnung.example.de',
                      'https://rechnung.example.de:8443', 'https://rechnung.example.de/admin/',
                      'https://rechnung.example.de?next=other', 'https://*.example.de',
                      'https://localhost', 'https://127.0.0.1', 'https://rechnung.example.de\n'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_origin(value)


class DesktopOnlineModeTests(SimpleTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='sedo_web_client_')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        patcher = patch.object(desktop, '_user_home', return_value=self.home)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_connect_and_next_start_use_only_online_server(self):
        with patch.object(sys, 'argv', ['desktop.py', '--connect', 'https://rechnung.example.de']), \
             patch.object(desktop, '_run_online', return_value=0) as online, \
             patch.object(desktop, '_bootstrap', side_effect=AssertionError('local DB opened')):
            self.assertEqual(desktop.main(), 0)
            self.assertEqual(online.call_args.args, ('https://rechnung.example.de',))
            online.reset_mock()
            with patch.object(sys, 'argv', ['desktop.py']):
                self.assertEqual(desktop.main(), 0)
            self.assertEqual(online.call_args.args, ('https://rechnung.example.de',))
        self.assertEqual(json.loads((self.home / 'web_connection.json').read_text())['origin'],
                         'https://rechnung.example.de')

    def test_corrupt_online_connection_does_not_open_local_database(self):
        (self.home / 'web_connection.json').write_text('{broken', encoding='utf-8')
        with patch.object(sys, 'argv', ['desktop.py']), \
             patch.object(desktop, '_bootstrap', side_effect=AssertionError('local DB opened')):
            self.assertEqual(desktop.main(), 2)

    def test_insecure_address_is_not_saved(self):
        with self.assertRaises(ValueError):
            desktop._save_online_origin('http://rechnung.example.de')
        self.assertFalse((self.home / 'web_connection.json').exists())


class HostedSettingsTests(SimpleTestCase):
    def _import(self, home, **values):
        env = os.environ.copy()
        env.update(SEDO_HOME=str(home), DJANGO_SETTINGS_MODULE='config.settings_web',
                   DJANGO_SECRET_KEY='long-random-test-key-for-web-mode-1234567890-abcdefXYZ',
                   SEDO_WEB_ORIGIN='https://rechnung.example.de', DJANGO_DEBUG='0',
                   PYTHONDONTWRITEBYTECODE='1')
        env.update(values)
        return subprocess.run([sys.executable, '-c',
            'import django; django.setup(); import json; '
            'from django.conf import settings; from django.urls import get_resolver; '
            'print(json.dumps({"hosts": settings.ALLOWED_HOSTS, '
            '"csrf": settings.CSRF_TRUSTED_ORIGINS, "ssl": settings.SECURE_SSL_REDIRECT, '
            '"cookie": settings.SESSION_COOKIE_SECURE, "local": settings.SEDO_LOCAL_ONLY, '
            '"static": str(settings.STATIC_ROOT), "static_url": settings.STATIC_URL, '
            '"media_route": any(getattr(route, "name", None) == "protected_media" '
            'for route in get_resolver().url_patterns), '
            '"db": str(settings.DATABASES["default"]["NAME"])}))'],
            cwd=Path(__file__).resolve().parents[2], env=env,
            capture_output=True, text=True, timeout=15)

    def test_hosted_profile_is_fixed_to_one_https_origin_and_separate_data(self):
        with tempfile.TemporaryDirectory(prefix='sedo_web_settings_') as tmp:
            result = self._import(Path(tmp))
            self.assertEqual(result.returncode, 0, result.stderr)
            values = json.loads(result.stdout)
            self.assertEqual(values['hosts'], ['rechnung.example.de'])
            self.assertEqual(values['csrf'], ['https://rechnung.example.de'])
            self.assertTrue(values['ssl'] and values['cookie'])
            self.assertFalse(values['local'])
            self.assertEqual(values['db'], str(Path(tmp) / 'data' / 'sedo_web.sqlite3'))
            self.assertEqual(values['static'], str(Path(tmp) / 'staticfiles'))
            self.assertEqual(values['static_url'], '/static/')
            self.assertFalse(values['media_route'])

    def test_hosted_profile_fails_without_origin_or_explicit_secret(self):
        with tempfile.TemporaryDirectory(prefix='sedo_web_settings_') as tmp:
            self.assertNotEqual(self._import(Path(tmp), SEDO_WEB_ORIGIN='').returncode, 0)
            self.assertNotEqual(self._import(Path(tmp), DJANGO_SECRET_KEY='').returncode, 0)
            self.assertNotEqual(self._import(Path(tmp), DJANGO_DEBUG='1').returncode, 0)

    def test_hosted_profile_rejects_public_data_directory(self):
        with tempfile.TemporaryDirectory(prefix='sedo_web_settings_') as tmp:
            os.chmod(tmp, 0o755)
            self.assertNotEqual(self._import(Path(tmp)).returncode, 0)


class HostedAiTests(TestCase):
    @override_settings(SEDO_WEB_MODE=True, SEDO_WEB_AI_ENABLED=False)
    def test_hosted_ai_needs_explicit_server_opt_in(self):
        self.assertFalse(get_ai_config()['enabled'])

    @override_settings(SEDO_WEB_MODE=True, SEDO_WEB_STT_ENABLED=False)
    def test_hosted_dictation_cannot_download_a_model_without_opt_in(self):
        self.assertFalse(stt_status()['available'])
        with self.assertRaises(TranscriptionUnavailable):
            transcribe_audio(b'synthetic audio')
