"""Non-destructive data-home migration tested only with synthetic SQLite files."""
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tools.security_storage import StorageError, copy_home, inventory


class SecurityStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='sedo-storage-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'source'
        self.destination = self.root/'destination'
        (self.source/'data').mkdir(parents=True)
        (self.source/'media').mkdir()
        (self.source/'media/example.txt').write_text('synthetic invoice placeholder')
        (self.source/'data/.secret_key').write_text('old-synthetic-django-key')
        (self.source/'data/.mail_key').write_text('preserve-synthetic-mail-key')
        with sqlite3.connect(self.source/'data/sedo_web.sqlite3') as db:
            db.execute('CREATE TABLE business(id INTEGER PRIMARY KEY, value TEXT)')
            db.execute("INSERT INTO business(value) VALUES('synthetic-business-row')")
            db.execute('CREATE TABLE django_session(session_key TEXT)')
            db.execute("INSERT INTO django_session VALUES('synthetic-session')")

    def hashes(self):
        return {str(p.relative_to(self.source)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.source.rglob('*') if p.is_file() and p.name != '.desktop.lock'}

    def test_dry_run_does_not_write_or_reveal_values(self):
        before = self.hashes()
        result = inventory(self.source)
        self.assertEqual(before, self.hashes())
        self.assertFalse(result['executed'])
        self.assertNotIn('old-synthetic-django-key', str(result))
        self.assertFalse(self.destination.exists())

    def test_copy_preserves_source_and_business_rows(self):
        before = self.hashes()
        report = copy_home(self.source, self.destination, offline_confirm=True)
        self.assertEqual(before, self.hashes())
        self.assertEqual(report['database_integrity'], 'ok')
        with sqlite3.connect(self.destination/'data/sedo_web.sqlite3') as db:
            self.assertEqual(db.execute('SELECT value FROM business').fetchone()[0], 'synthetic-business-row')
        self.assertEqual((self.destination/'media/example.txt').read_bytes(), (self.source/'media/example.txt').read_bytes())

    def test_rotation_changes_destination_key_only_and_clears_destination_sessions(self):
        before = self.hashes()
        with patch.dict(os.environ, {'DJANGO_SECRET_KEY': ''}):
            report = copy_home(self.source, self.destination, offline_confirm=True, rotate_django_key=True)
        self.assertTrue(report['django_key_rotated'])
        self.assertEqual(before, self.hashes())
        self.assertNotEqual((self.destination/'data/.secret_key').read_bytes(), (self.source/'data/.secret_key').read_bytes())
        self.assertEqual((self.destination/'data/.mail_key').read_bytes(), (self.source/'data/.mail_key').read_bytes())
        with sqlite3.connect(self.destination/'data/sedo_web.sqlite3') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM django_session').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM business').fetchone()[0], 1)

    def test_existing_destination_is_never_overwritten(self):
        self.destination.mkdir()
        (self.destination/'keep.txt').write_text('keep')
        with self.assertRaises(StorageError):
            copy_home(self.source, self.destination, offline_confirm=True)
        self.assertEqual((self.destination/'keep.txt').read_text(), 'keep')

    def test_confirmation_is_required(self):
        with self.assertRaises(StorageError):
            copy_home(self.source, self.destination)
        self.assertFalse(self.destination.exists())

    def test_nested_destination_is_refused(self):
        with self.assertRaises(StorageError):
            copy_home(self.source, self.source/'nested', offline_confirm=True)

    def test_pending_restore_is_refused(self):
        (self.source/'pending_restore.json').write_text('{}')
        with self.assertRaises(StorageError):
            copy_home(self.source, self.destination, offline_confirm=True)

    def test_rotation_with_env_override_is_refused(self):
        with patch.dict(os.environ, {'DJANGO_SECRET_KEY': 'operator-managed-test-key'}), self.assertRaises(StorageError):
            copy_home(self.source, self.destination, offline_confirm=True, rotate_django_key=True)
        self.assertFalse(self.destination.exists())

    def test_rotation_with_dotenv_override_is_refused(self):
        (self.source/'.env').write_text('DJANGO_SECRET_KEY=operator-managed-key\n')
        with patch.dict(os.environ, {'DJANGO_SECRET_KEY': ''}), self.assertRaises(StorageError):
            copy_home(self.source, self.destination, offline_confirm=True, rotate_django_key=True)

    def test_bad_database_is_not_published(self):
        (self.source/'data/sedo_web.sqlite3').write_bytes(b'not a database')
        with self.assertRaises(sqlite3.Error):
            copy_home(self.source, self.destination, offline_confirm=True)
        self.assertFalse(self.destination.exists())
        self.assertFalse(list(self.root.glob('.sedo-move-*')))

    @unittest.skipUnless(os.name == 'posix', 'POSIX file mode evidence only')
    def test_private_modes_on_copied_database_and_key(self):
        copy_home(self.source, self.destination, offline_confirm=True)
        for path in ('data/sedo_web.sqlite3', 'data/.secret_key', 'data/.mail_key'):
            self.assertEqual((self.destination/path).stat().st_mode & 0o777, 0o600)

    @unittest.skipUnless(os.name == 'posix', 'Symlink creation may require elevated Windows privileges')
    def test_symlink_is_refused(self):
        (self.source/'media/link').symlink_to(self.root)
        with self.assertRaises(StorageError):
            copy_home(self.source, self.destination, offline_confirm=True)
