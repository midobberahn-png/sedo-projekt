"""H2: Testbuild-Vertrag, ohne Laufzeitdatenbank oder Plattformbehauptungen."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

APP = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('sedo_teststand_tools', APP/'tools/teststand.py')
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)


class TeststandTests(unittest.TestCase):
    def test_echte_lock_datei_ist_vollstaendig(self):
        entries = t.validate_lock(APP)
        self.assertGreater(len(entries), 40)
        self.assertTrue(any(e.startswith('faster-whisper==') for e in entries))

    def test_lock_ohne_hash_wird_abgewiesen(self):
        with self.assertRaises(t.BuildError):
            t.lock_entries('django==5.2.17')

    def test_lock_ohne_versionsbindung_wird_abgewiesen(self):
        with self.assertRaises(t.BuildError):
            t.lock_entries('django>=5.2 --hash=sha256:'+'a'*64)

    def test_fremde_quelle_wird_abgewiesen(self):
        for flag in ('--index-url https://example.invalid', '--extra-index-url=https://example.invalid'):
            with self.subTest(flag=flag), self.assertRaises(t.BuildError):
                t.lock_entries('django==5.2.17 --hash=sha256:'+'a'*64+' '+flag)

    def test_falscher_zusaetzlicher_hash_wird_abgewiesen(self):
        with self.assertRaises(t.BuildError):
            t.lock_entries('django==5.2.17 --hash=sha256:'+'a'*64+' --hash=sha256:kaputt')

    def test_unvollstaendige_fortsetzung_wird_abgewiesen(self):
        with self.assertRaises(t.BuildError):
            t.lock_entries('django==5.2.17 \\')

    def test_plattformmarker_bleiben_erhalten(self):
        entry = "colorama==0.4.6 ; sys_platform == 'win32' --hash=sha256:"+'a'*64
        self.assertEqual(t.lock_entries(entry), [entry])

    def test_betriebsdateien_nie_im_quellpaket(self):
        for name in ('data/sedo_web.sqlite3', 'media/invoice.pdf', '.env', '.secret_key',
                     'sedo/data/demo.json', 'sedo/__pycache__/x.pyc', 'sedo/.env',
                     '../escape.py', '/abs.py', r'sedo\..\data\database.py', 'logs/test.txt'):
            with self.subTest(name=name):
                self.assertFalse(t.allowed(name))

    def test_erforderliche_quelltypen_werden_mitgenommen(self):
        for name in ('sedo/models.py', 'sedo/migrations/0013_invoice_query_indexes.py',
                     'sedo/templates/sedo/base.html', 'sedo/static/sedo/app.css',
                     'sedo/validation/en16931/1.3.16/EN16931-CII-validation-preprocessed.sch',
                     '.env.example', 'tools/teststand.py', 'requirements-release.lock'):
            with self.subTest(name=name):
                self.assertTrue(t.allowed(name))

    def test_vorhandene_buildumgebung_bleibt_unberuehrt(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(t.sys, 'version_info', (3,13,15)):
            marker = Path(tmp)/'marker'; marker.write_text('unverändert')
            with self.assertRaises(t.BuildError): t.install(Path(tmp), APP)
            self.assertEqual(marker.read_text(), 'unverändert')

    def test_falscher_python_startet_keine_installation(self):
        with patch.object(t.sys, 'version_info', (3,9,1)), patch.object(t, 'run') as run:
            with self.assertRaises(t.BuildError): t.install(Path('/not-created'), APP)
            run.assert_not_called()

    def fixture(self, root):
        for name in (*t.INPUTS, 'requirements-release.lock', 'release-lock.json'):
            (root/name).write_bytes((APP/name).read_bytes())
        for name in ('manage.py','desktop.py','sedo_desktop.spec','tools/teststand.py','config/settings.py','sedo/models.py'):
            p=root/name; p.parent.mkdir(exist_ok=True,parents=True); p.write_text('# synthetisch\n')
        def git(*args):
            subprocess.run(['git',*args],cwd=root,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        git('init');git('config','user.name','Buildtest');git('config','user.email','test@example.invalid')
        git('add','.');git('commit','-m','synthetischer Quellstand')
        return git

    def test_quellpaket_ist_reproduzierbar_und_enthaelt_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'app';root.mkdir();self.fixture(root)
            (root/'.env').write_text('NICHT EXPORTIEREN')
            first=t.source(Path(tmp)/'a',root);second=t.source(Path(tmp)/'b',root)
            self.assertEqual(first.read_bytes(),second.read_bytes())
            with zipfile.ZipFile(first) as z:
                self.assertNotIn('.env',z.namelist())
                m=json.loads(z.read('TESTSTAND_MANIFEST.json'))
                for name,digest in m['files'].items():
                    self.assertEqual(hashlib.sha256(z.read(name)).hexdigest(),digest)

    def test_geaenderte_quellen_werden_nicht_als_commitstand_ausgegeben(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root);(root/'manage.py').write_text('# geändert\n')
            with self.assertRaises(t.BuildError):t.source(root/'out',root)

    def test_veralteter_lock_wird_abgewiesen(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root)
            with (root/'requirements.txt').open('a') as f:f.write('\nextra-package\n')
            with self.assertRaises(t.BuildError):t.validate_lock(root)

    def test_manipulierter_lock_wird_abgewiesen(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root)
            with (root/'requirements-release.lock').open('a') as f:f.write('\n# manipuliert\n')
            with self.assertRaises(t.BuildError):t.validate_lock(root)
