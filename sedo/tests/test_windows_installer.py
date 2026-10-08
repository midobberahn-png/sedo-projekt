"""Installer must never certify failed checks or package changed/unsafe payloads."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

spec = importlib.util.spec_from_file_location('sedo_windows_installer',
    Path(__file__).resolve().parents[2] / 'tools/windows_installer.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class WindowsInstallerTests(unittest.TestCase):
    def test_installer_revision_cannot_hide_changed_or_added_application_code(self):
        tested = {'desktop.py': 'tested', 'sedo/views.py': 'tested', 'sedo_setup_windows.iss': 'old'}
        current = {**tested, 'sedo_setup_windows.iss': 'new-runtime-check'}
        installer.verify_app_sources(current, tested)
        for changed in ({**current, 'desktop.py': 'changed'}, {**current, 'sedo/new.py': 'added'}):
            with self.assertRaises(ValueError): installer.verify_app_sources(changed, tested)

    def test_runtime_download_rejects_foreign_or_injectable_addresses(self):
        good = 'https://download.visualstudio.microsoft.com/download/pr/release/hash/VC_redist.x64.exe'
        self.assertEqual(installer.checked_runtime_url(good), good)
        for url in (good.replace('https:', 'http:'), good.replace('microsoft.com', 'microsoft.com.evil.test'),
                    good + '?other=1', good + '#fragment', good.replace('/hash/', "/hash'/")):
            with self.assertRaises(ValueError): installer.checked_runtime_url(url)

    def fixture(self, root):
        (root / 'native').mkdir()
        steps = ['source-a', 'source-b', 'native-build']
        for series in ('5.2', '6.0'):
            suffixes = ['install', 'dependencies', 'environment', 'tests', 'sqlite', 'index-50000', 'migrationen']
            suffixes += ['gegenproben-' + p for p in ('entwurf', 'belegketten', 'indizes', 'belegarchiv',
                'mahnwaechter', 'abo_zahlungsziel', 'bedienrisiken', 'teststand', 'mail', 'incoming', 'spam', 'calendar')]
            steps += [f'django-{series}-{s}' for s in suffixes]
        report = {'status': 'passed', 'development_ready': True, 'host': {'os': 'Windows'},
            'commit': 'commit', 'tree': 'tree', 'steps': [{'name': s, 'status': 'passed'} for s in steps]}
        archive = root / 'native/app.zip'
        archive.write_bytes(b'synthetic payload')
        native = {'kind': 'native-teststand', 'os': 'Windows', 'commit': 'commit', 'tree': 'tree',
            'self_check': {'ok': True, 'frozen': True, 'whisper_available': True},
            'archive_sha256': installer.digest(archive)}
        (root / 'report.json').write_text(json.dumps(report), encoding='utf-8')
        (root / 'native/BUILD_MANIFEST.json').write_text(json.dumps(native), encoding='utf-8')
        return report, archive

    def test_failed_missing_or_foreign_platform_checks_block_packaging(self):
        for mutation in ('failed', 'missing', 'darwin'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                report, _ = self.fixture(root)
                if mutation == 'failed': report['steps'][0]['status'] = 'failed'
                if mutation == 'missing': report['steps'].pop(5)
                if mutation == 'darwin': report['host']['os'] = 'Darwin'
                (root / 'report.json').write_text(json.dumps(report), encoding='utf-8')
                with self.assertRaises(ValueError): installer.verified_build(root)

    def test_changed_binary_cannot_reuse_successful_test_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, archive = self.fixture(root)
            self.assertEqual(installer.verified_build(root)[2], archive)
            archive.write_bytes(b'changed payload')
            with self.assertRaises(ValueError): installer.verified_build(root)

    def test_traversal_or_live_database_rejected_before_extraction(self):
        for name in ('SEDO Rechnungstool/../escape', 'SEDO Rechnungstool/.env',
                     'SEDO Rechnungstool/data/sedo_web.sqlite3', 'C:/escape'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                archive = root / 'app.zip'
                with zipfile.ZipFile(archive, 'w') as z: z.writestr(name, 'private')
                with self.assertRaises(ValueError): installer.unpack(archive, root / 'unpacked')
                self.assertFalse((root / 'unpacked').exists())

    def test_wrong_architecture_cannot_be_shipped_as_x64(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / 'app.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('SEDO Rechnungstool/SEDO Rechnungstool.exe', b'not a Windows executable')
            with self.assertRaises(ValueError): installer.unpack(archive, root / 'unpacked')
