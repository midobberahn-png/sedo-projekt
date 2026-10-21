"""Regressionen für den signaturgebundenen Windows-Updatepfad."""
import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from sedo import windows_updates as updates
from tools.windows_update_release import build_manifest


class WindowsUpdateTests(unittest.TestCase):
    @staticmethod
    def next_version():
        parts = updates.WINDOWS_VERSION.split('.')
        parts[-1] = str(int(parts[-1]) + 1)
        return '.'.join(parts)

    def setUp(self):
        self.private = Ed25519PrivateKey.generate()
        public = self.private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw)
        self.key_patch = patch.object(updates, 'PUBLIC_KEY_B64', base64.b64encode(public).decode('ascii'))
        self.key_patch.start()
        self.addCleanup(self.key_patch.stop)

    def release(self, version=None, size=7):
        version = version or self.next_version()
        tag = f'v{version}-windows'
        return {'tag_name': tag, 'prerelease': True, 'draft': False, 'assets': [
            {'name': updates.MANIFEST_NAME,
             'browser_download_url': updates._release_url(tag, updates.MANIFEST_NAME)},
            {'name': f'SEDO_Rechnungstool_Setup_{version}.exe', 'size': size,
             'browser_download_url': updates._release_url(tag, f'SEDO_Rechnungstool_Setup_{version}.exe')},
        ]}

    def manifest(self, version=None, payload=b'example'):
        version = version or self.next_version()
        body = {'schema': 1, 'channel': 'test', 'version': version,
                'installer_sha256': hashlib.sha256(payload).hexdigest(),
                'installer_size': len(payload)}
        body['signature'] = base64.b64encode(self.private.sign(updates._json_bytes(body))).decode('ascii')
        return json.dumps(body).encode('utf-8')

    def test_signed_update_is_selected_but_not_installed_by_check(self):
        release = self.release()
        raw = self.manifest()
        with patch.object(updates, '_fetch', side_effect=[json.dumps([release]).encode(), raw]) as fetch:
            found = updates.check()
        self.assertEqual(found.version, self.next_version())
        self.assertEqual(found.size, 7)
        self.assertEqual(fetch.call_count, 2)

    def test_older_releases_do_not_trigger_an_update(self):
        with patch.object(updates, '_fetch', return_value=json.dumps([self.release('3.21.0.4')]).encode()) as fetch:
            self.assertIsNone(updates.check())
        self.assertEqual(fetch.call_count, 1)

    def test_changed_manifest_or_wrong_asset_is_rejected(self):
        release = self.release()
        changed = json.loads(self.manifest())
        changed['installer_sha256'] = '0' * 64
        with self.assertRaises(updates.UpdateError):
            updates._verify_manifest(json.dumps(changed).encode(), release)
        release['assets'][1]['browser_download_url'] = 'https://example.invalid/installer.exe'
        with self.assertRaises(updates.UpdateError):
            updates._verify_manifest(self.manifest(), release)

    def test_missing_public_key_blocks_before_network(self):
        with patch.object(updates, 'PUBLIC_KEY_B64', ''), patch.object(updates, '_fetch') as fetch:
            with self.assertRaises(updates.UpdateError):
                updates.check()
            fetch.assert_not_called()

    def test_truncated_or_modified_download_leaves_no_installer(self):
        payload = b'example'
        update = updates._verify_manifest(self.manifest(payload=payload), self.release())
        for downloaded in (payload[:-1], b'changed'):
            with self.subTest(downloaded=downloaded), tempfile.TemporaryDirectory() as temp:
                class Response:
                    def __enter__(self): return self
                    def __exit__(self, *_): pass
                    def geturl(self): return update.url
                    def read(self, _):
                        value, self.data = self.data, b''
                        return value
                response = Response()
                response.data = downloaded
                with patch.object(updates, 'urlopen', return_value=response):
                    with self.assertRaises(updates.UpdateError):
                        updates.download(update, Path(temp))
                self.assertEqual(list(Path(temp).iterdir()), [])

    def test_verified_download_is_written_atomically(self):
        payload = b'example'
        update = updates._verify_manifest(self.manifest(payload=payload), self.release())
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def geturl(self): return update.url
            def read(self, _):
                value, self.data = self.data, b''
                return value
        response = Response()
        response.data = payload
        with tempfile.TemporaryDirectory() as temp, patch.object(updates, 'urlopen', return_value=response):
            result = updates.download(update, Path(temp))
            self.assertEqual(result.read_bytes(), payload)
            self.assertEqual([p.name for p in Path(temp).iterdir()], [result.name])

    def test_signer_requires_matching_green_installer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            version = updates.WINDOWS_VERSION
            setup = root / f'SEDO_Rechnungstool_Setup_{version}.exe'
            setup.write_bytes(b'MZsynthetic')
            digest = hashlib.sha256(setup.read_bytes()).hexdigest()
            info = root / 'BUILD_INFO.json'
            smoke = root / 'INSTALLATIONSTEST.json'
            info.write_text(json.dumps({'version': version, 'setup_sha256': digest,
                                        'windows_checks': 30, 'commit': 'test-commit'}), encoding='utf-8')
            smoke.write_text(json.dumps({'installer_sha256': digest,
                                         'installed_program_self_check': True,
                                         'upgrade_preserved_data': True,
                                         'uninstall_successful': True,
                                         'github_commit': 'test-commit'}), encoding='utf-8')
            seed = self.private.private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption())
            manifest = build_manifest(setup, info, smoke, base64.b64encode(seed).decode())
            release = self.release(version, len(setup.read_bytes()))
            self.assertEqual(updates._verify_manifest(json.dumps(manifest).encode(), release).sha256, digest)
            smoke.write_text(json.dumps({'installer_sha256': digest,
                                         'installed_program_self_check': False,
                                         'upgrade_preserved_data': True,
                                         'uninstall_successful': True,
                                         'github_commit': 'test-commit'}), encoding='utf-8')
            with self.assertRaises(ValueError):
                build_manifest(setup, info, smoke, base64.b64encode(seed).decode())
