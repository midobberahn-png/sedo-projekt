"""Company-profile backup/restore proof using newly migrated synthetic DBs only."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

from django.conf import settings
from django.test import TestCase, override_settings

from sedo.backup_utils import SECRET_SETTING_KEYS, create_backup, inspect_backup, stage_restore
from sedo.company_profile import PROFILE_KEY, load_profile, save_profile, validate_profile
from sedo.restore_bootstrap import apply_pending_restore
from sedo.tests.company_fixtures import SYNTHETIC_PROFILE
from sedo.tests.test_features_7_10 import make_restore_database


class CompanyProfileBackupTests(TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix='sedo_synthetic_company_restore_')
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.db = self.home / 'data' / 'sedo_web.sqlite3'
        self.db.parent.mkdir()
        (self.home / 'media').mkdir()
        (self.home / 'media' / 'synthetic.txt').write_text('synthetic backup media', encoding='utf-8')
        # Reuse the existing isolated migration fixture, never the installed DB.
        make_restore_database(self.db)
        self.profile_json = json.dumps(validate_profile(SYNTHETIC_PROFILE), sort_keys=True)
        self.secrets = {key: f'synthetic-backup-secret-{key}-only' for key in SECRET_SETTING_KEYS}
        with closing(sqlite3.connect(self.db)) as con:
            con.executemany(
                'INSERT INTO sedo_appsetting (key, value, created_at, updated_at) VALUES (?, ?, ?, ?)',
                [(key, value, '2026-10-08 00:00:00', '2026-10-08 00:00:00')
                 for key, value in {PROFILE_KEY: self.profile_json, **self.secrets}.items()],
            )
            con.execute('INSERT INTO django_session (session_key, session_data, expire_date) VALUES (?, ?, ?)',
                        ('synthetic-session-only', 'synthetic-session-data', '2099-01-01 00:00:00'))
            con.commit()
        for name in ('.secret_key', '.credentials_key', '.mail_key', '.calendar_key'):
            (self.db.parent / name).write_text('synthetic-private-key-file-only', encoding='utf-8')
        (self.home / '.env').write_text('SYNTHETIC_ONLY=do-not-back-up', encoding='utf-8')
        configuration = override_settings(SEDO_HOME=self.home, MEDIA_ROOT=self.home / 'media',
                                          SEDO_BACKUP_DIR=self.home / 'backups')
        configuration.enable()
        self.addCleanup(configuration.disable)
        # The nested copy redirects file-based backup reads, not Django's active
        # connection. AppSetting status writes remain in the runner's test DB.
        database = patch.dict(settings.DATABASES, {
            'default': {**settings.DATABASES['default'], 'NAME': str(self.db)},
        })
        database.start()
        self.addCleanup(database.stop)
        self.parent_profile = {**SYNTHETIC_PROFILE, 'name': 'Separate synthetische Testprozess-Firma'}
        save_profile(self.parent_profile)

    def read_profile(self, path):
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as con:
            raw = con.execute('SELECT value FROM sedo_appsetting WHERE key=?', (PROFILE_KEY,)).fetchone()[0]
        return validate_profile(json.loads(raw))

    def assert_sanitized(self, path):
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as con:
            rows = dict(con.execute('SELECT key, value FROM sedo_appsetting'))
            self.assertEqual(con.execute('SELECT COUNT(*) FROM django_session').fetchone()[0], 0)
        for key, value in self.secrets.items():
            self.assertEqual(rows[key], '')
            self.assertNotIn(value.encode('utf-8'), path.read_bytes())

    def test_backup_keeps_company_profile_without_secrets_or_local_key_files(self):
        original_digest = hashlib.sha256(self.db.read_bytes()).hexdigest()
        archive = create_backup('synthetic_company_profile')
        copy = self.home / 'copied-backup.sqlite3'
        with ZipFile(archive) as backup:
            self.assertEqual(set(backup.namelist()), {
                'BACKUP_INFO.txt', 'data/sedo_web.sqlite3', 'media/synthetic.txt',
            })
            copy.write_bytes(backup.read('data/sedo_web.sqlite3'))
        self.assertEqual(self.read_profile(copy), SYNTHETIC_PROFILE)
        self.assert_sanitized(copy)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), original_digest)
        self.assertEqual(load_profile(), self.parent_profile)

    def test_checked_staged_and_applied_restore_recovers_company_profile_on_copies_only(self):
        archive = create_backup('synthetic_company_restore')
        current_profile = {**SYNTHETIC_PROFILE, 'name': 'Andere synthetische Firma vor Rücksicherung'}
        with closing(sqlite3.connect(self.db)) as con:
            con.execute('UPDATE sedo_appsetting SET value=? WHERE key=?',
                        (json.dumps(current_profile), PROFILE_KEY))
            con.commit()
        (self.home / 'media' / 'synthetic.txt').write_text('synthetic current media', encoding='utf-8')
        original_digest = hashlib.sha256(self.db.read_bytes()).hexdigest()
        # No mock: the ordinary preview migrates an extracted copy in a child
        # process, and the ordinary staging creates an actual safety backup.
        preview = inspect_backup(archive.name)
        self.assertEqual(preview['integrity'], 'ok')
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), original_digest)
        plan = stage_restore(archive.name, expected_digest=preview['archive_sha256'])
        self.assertEqual(self.read_profile(self.db), current_profile)
        self.assertTrue(Path(plan['safety_backup']).is_file())
        self.assertTrue(Path(plan['staging']).is_relative_to(self.home / 'restore-staging'))
        result = apply_pending_restore(self.home)
        self.assertTrue(result['ok'])
        self.assertEqual(self.read_profile(self.db), SYNTHETIC_PROFILE)
        self.assert_sanitized(self.db)
        self.assertEqual((self.home / 'media' / 'synthetic.txt').read_text(), 'synthetic backup media')
        self.assertEqual(self.read_profile(Path(result['rollback']) / 'data' / 'sedo_web.sqlite3'), current_profile)
        self.assertFalse((self.home / 'pending_restore.json').exists())
        self.assertEqual(load_profile(), self.parent_profile)
