"""H2: historische Zeilenenden, Manifest-Staging und lokale Fremddateien."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

APP = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('h2_checkout_tools', APP/'tools/teststand.py')
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)


class TeststandCheckoutTests(unittest.TestCase):
    def archive(self, path, files, manifest_files=None):
        hashes = {n: hashlib.sha256(v).hexdigest() for n, v in files.items()}
        with zipfile.ZipFile(path, 'w') as z:
            for name, data in files.items():
                z.writestr(name, data)
            z.writestr('TESTSTAND_MANIFEST.json', json.dumps({'files': hashes if manifest_files is None else manifest_files}))
        return path

    def test_build_staging_nimmt_nur_manifestierte_quellen(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            # Nicht versionierte Nachbardatei darf nicht im Build auftauchen.
            (root/'local_injection.py').write_text('NICHT IM BUILD')
            archive=self.archive(root/'source.zip', {'sedo/models.py': b'# geprueft\n'})
            t.stage_source(archive, root/'staged')
            self.assertEqual([str(p.relative_to(root/'staged')) for p in (root/'staged').rglob('*') if p.is_file()], ['sedo/models.py'])

    def test_zusaetzliche_archivdatei_wird_abgewiesen(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            archive=self.archive(root/'source.zip', {'sedo/models.py': b'# neu'}, {})
            with self.assertRaises(t.BuildError): t.stage_source(archive, root/'staged')
            self.assertFalse((root/'staged').exists())

    def test_manifest_pruefsumme_wird_vor_schreiben_geprueft(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            archive=self.archive(root/'source.zip', {'sedo/models.py': b'# neu'}, {'sedo/models.py': '0'*64})
            with self.assertRaises(t.BuildError): t.stage_source(archive, root/'staged')
            self.assertFalse((root/'staged').exists())

    def test_pfadflucht_wird_auch_mit_passendem_hash_abgewiesen(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            archive=self.archive(root/'source.zip', {'../ausserhalb.py': b'# nein'})
            with self.assertRaises(t.BuildError): t.stage_source(archive, root/'staged')
            self.assertFalse((root/'ausserhalb.py').exists())

    def test_staging_ueberschreibt_keinen_bestehenden_ordner(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dest=root/'staged';dest.mkdir();marker=dest/'marker';marker.write_bytes(b'unveraendert')
            archive=self.archive(root/'source.zip', {'sedo/models.py': b'# ok'})
            with self.assertRaises(FileExistsError): t.stage_source(archive,dest)
            self.assertEqual(marker.read_bytes(),b'unveraendert')

    def test_historische_crlf_starter_bleiben_im_checkout_sauber(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'repo';root.mkdir()
            def git(*args, cwd=root):
                return subprocess.check_output(['git',*args],cwd=cwd,stderr=subprocess.DEVNULL)
            git('init');git('config','user.name','Buildtest');git('config','user.email','test@example.invalid')
            git('config','core.autocrlf','false')
            old=b'@echo off\r\necho unveraendert\r\n'
            (root/'ALT.bat').write_bytes(old)
            (root/'requirements.txt').write_bytes(b'django==5.2.17\n')
            git('add','.');git('commit','-m','historische Dateibytes')
            (root/'.gitattributes').write_bytes((APP/'.gitattributes').read_bytes())
            git('add','.gitattributes');git('commit','-m','Checkoutvertrag')
            for mode in ('true','false'):
                with self.subTest(autocrlf=mode):
                    clone=Path(tmp)/('clone-'+mode)
                    git('-c','core.autocrlf='+mode,'clone','--no-local',str(root),str(clone))
                    self.assertEqual(git('status','--porcelain',cwd=clone),b'')
                    self.assertEqual((clone/'ALT.bat').read_bytes(),old)
                    self.assertEqual((clone/'requirements.txt').read_bytes(),b'django==5.2.17\n')
