"""Tests für das Sicherheitspaket v3.21: Zufallspasswort, Backup-Secrets, .env-Loader."""
import io
import os
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from config.settings import _load_dotenv
from sedo.backup_utils import SECRET_SETTING_KEYS, _sanitized_db_copy
from sedo.models import AppSetting


class EnsureAdminTests(TestCase):
    def _run(self):
        out = io.StringIO()
        call_command('ensure_admin', stdout=out)
        return out.getvalue()

    def test_neuer_admin_bekommt_zufallspasswort(self):
        output = self._run()
        self.assertIn('Einmalig angezeigtes Passwort', output)
        user = get_user_model().objects.get(username='admin')
        self.assertFalse(user.check_password('sedo1234'))  # nie das alte Standard-Passwort
        # Das angezeigte Passwort funktioniert wirklich
        shown = output.split('Einmalig angezeigtes Passwort:')[1].split()[0]
        self.assertTrue(user.check_password(shown))

    def test_warnung_bei_altem_standard_passwort(self):
        get_user_model().objects.create_superuser('admin', 'a@x.de', 'sedo1234')
        output = self._run()
        self.assertIn('Standard-Passwort', output)
        self.assertIn('changepassword', output)

    def test_kein_hinweis_bei_eigenem_passwort(self):
        get_user_model().objects.create_superuser('admin', 'a@x.de', 'ganz-eigenes-pw')
        output = self._run()
        self.assertIn('existiert bereits', output)
        self.assertNotIn('Standard-Passwort', output)


class BackupSecretTests(TestCase):
    def test_sanitized_copy_entfernt_secrets_live_db_bleibt(self):
        # Eine Mini-SQLite mit AppSetting-Tabelle simulieren
        fd, db_name = tempfile.mkstemp(suffix='.sqlite3')
        os.close(fd)
        db = Path(db_name)
        try:
            con = sqlite3.connect(db)
            con.execute('CREATE TABLE sedo_appsetting (key TEXT PRIMARY KEY, value TEXT)')
            # Alle Secret-Keys dynamisch anlegen – so bricht der Test sofort,
            # wenn ein neues Secret (z. B. fints_pin) nicht bereinigt würde.
            con.executemany('INSERT INTO sedo_appsetting VALUES (?, ?)',
                            [(key, f'geheim-{key}') for key in SECRET_SETTING_KEYS]
                            + [('smtp_host', 'mail.example.de')])
            con.commit()
            con.close()

            sanitized = _sanitized_db_copy(db)
            try:
                con = sqlite3.connect(sanitized)
                rows = dict(con.execute('SELECT key, value FROM sedo_appsetting'))
                con.close()
                for key in SECRET_SETTING_KEYS:
                    self.assertEqual(rows[key], '', f'{key} muss im Backup leer sein')
                self.assertEqual(rows['smtp_host'], 'mail.example.de')  # Nicht-Secrets bleiben
                # Original unangetastet
                con = sqlite3.connect(db)
                live = dict(con.execute('SELECT key, value FROM sedo_appsetting'))
                con.close()
                self.assertEqual(live['ai_api_key'], 'geheim-ai_api_key')
            finally:
                sanitized.unlink(missing_ok=True)
        finally:
            db.unlink(missing_ok=True)

    def test_create_backup_hinweis_und_kein_secret_im_zip(self):
        """Der ganze Weg bis ins ZIP – nicht nur die Infodatei.

        Dieser Test sicherte lange Zeit nichts zu: Django gibt der Test-
        Datenbank den Namen ``file:memorydb_default?mode=memory&cache=shared``,
        damit ist ``db_path.exists()`` in ``create_backup`` immer falsch, es
        landet keine .sqlite3 im ZIP, ``db_names`` bleibt leer und die
        prüfende Schleife läuft über nichts. Der Test konnte nicht rot werden.

        Deshalb wird für die Dauer des Aufrufs eine Wegwerf-Datenbank als
        Live-Datenbank untergeschoben. Das innere Dict ist eine Kopie, damit
        ``connection.settings_dict`` unberührt bleibt; die echte
        ``data/sedo_web.sqlite3`` wird nie geöffnet.
        """
        from unittest.mock import patch

        from django.conf import settings as dj_settings

        from sedo.backup_utils import create_backup

        AppSetting.set('ai_api_key', 'sk-test-geheim-123')

        fd, name = tempfile.mkstemp(suffix='.sqlite3')
        os.close(fd)
        wegwerf = Path(name)
        con = sqlite3.connect(wegwerf)
        con.execute('CREATE TABLE sedo_appsetting (key TEXT PRIMARY KEY, value TEXT)')
        con.execute("INSERT INTO sedo_appsetting VALUES ('ai_api_key', 'sk-test-geheim-123')")
        con.execute("INSERT INTO sedo_appsetting VALUES ('fints_pin', 'geheime-bank-pin-4711')")
        con.commit()
        con.close()

        try:
            with patch.dict(dj_settings.DATABASES, {
                'default': {**dj_settings.DATABASES['default'], 'NAME': str(wegwerf)},
            }):
                out = create_backup('security_test')
            try:
                with zipfile.ZipFile(out) as zf:
                    info = zf.read('BACKUP_INFO.txt').decode('utf-8')
                    self.assertIn('NICHT im Backup enthalten', info)
                    db_names = [n for n in zf.namelist() if n.endswith('.sqlite3')]
                    self.assertEqual(
                        len(db_names), 1,
                        'Ohne Datenbank im ZIP prüft dieser Test nichts – genau das war der Fehler.',
                    )
                    rohbytes = zf.read(db_names[0])
                    self.assertNotIn(b'sk-test-geheim-123', rohbytes)
                    self.assertNotIn(b'geheime-bank-pin-4711', rohbytes)
            finally:
                out.unlink(missing_ok=True)
        finally:
            wegwerf.unlink(missing_ok=True)

        # Live-Einstellung bleibt erhalten
        self.assertEqual(AppSetting.get('ai_api_key'), 'sk-test-geheim-123')


class DotenvLoaderTests(TestCase):
    def test_laedt_werte_und_respektiert_vorrang(self):
        fd, name = tempfile.mkstemp(suffix='.env')
        os.close(fd)
        p = Path(name)
        try:
            p.write_text('# Kommentar\nTESTKEY_SEDO="wert-aus-datei"\nTESTKEY_SEDO2=zweiter\n\nkaputtezeile\n')
            os.environ.pop('TESTKEY_SEDO', None)
            os.environ['TESTKEY_SEDO2'] = 'bereits-gesetzt'
            try:
                _load_dotenv(p)
                self.assertEqual(os.environ['TESTKEY_SEDO'], 'wert-aus-datei')
                self.assertEqual(os.environ['TESTKEY_SEDO2'], 'bereits-gesetzt')  # env gewinnt
            finally:
                os.environ.pop('TESTKEY_SEDO', None)
                os.environ.pop('TESTKEY_SEDO2', None)
        finally:
            p.unlink(missing_ok=True)

    def test_fehlende_datei_ist_ok(self):
        _load_dotenv(Path('/gibt/es/nicht/.env'))  # darf nicht werfen
