"""Fail-closed local development evidence without touching a real installation."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[2] / 'tools'
spec = importlib.util.spec_from_file_location('sedo_local_checks', TOOLS / 'local_checks.py')
checks = importlib.util.module_from_spec(spec)
with patch.object(sys, 'path', [str(TOOLS), *sys.path]):
    spec.loader.exec_module(checks)


class LocalChecksTests(unittest.TestCase):
    def test_environment_never_reuses_live_home_secrets_or_python_injection(self):
        with patch.dict(os.environ, {'SEDO_HOME': '/live', 'SEDO_AI_API_KEY': 'secret',
                                    'DJANGO_SECRET_KEY': 'real-secret', 'PYTHONPATH': '/injection',
                                    'SEDO_MAIL_WORKER': '1'}):
            env = checks.isolated_env(Path('/isolated'))
        self.assertEqual(env['SEDO_HOME'], '/isolated')
        self.assertNotEqual(env['DJANGO_SECRET_KEY'], 'real-secret')
        for key in ('SEDO_AI_API_KEY', 'PYTHONPATH', 'SEDO_MAIL_WORKER'):
            self.assertNotIn(key, env)

    def test_wrong_python_or_django_series_is_rejected(self):
        for info in ({'python': [3, 12, 1], 'django': '5.2.17'},
                     {'python': [3, 13, 15], 'django': '6.0.8'}):
            with self.assertRaises(checks.teststand.BuildError):
                checks.validate_interpreter(info, '5.2')

    def test_existing_evidence_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / 'report.json'
            marker.write_text('previous evidence', encoding='utf-8')
            with self.assertRaises(FileExistsError):
                checks.Evidence(Path(tmp), 'commit', 'tree')
            self.assertEqual(marker.read_text(encoding='utf-8'), 'previous evidence')

    def test_failed_child_is_recorded_and_cannot_finish_green(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence = checks.Evidence(root / 'evidence', 'commit', 'tree')
            with self.assertRaises(subprocess.CalledProcessError):
                evidence.run('failure', [sys.executable, '-c', 'raise SystemExit(7)'], root, os.environ.copy())
            evidence.finish()
            report = json.loads((evidence.output / 'report.json').read_text(encoding='utf-8'))
            self.assertFalse(report['development_ready'])
            self.assertEqual(report['status'], 'failed')
            self.assertEqual(report['steps'][0]['status'], 'failed')

    def test_interrupted_report_is_not_green_and_does_not_claim_other_platforms(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(checks.platform, 'system', return_value='Darwin'):
            evidence = checks.Evidence(Path(tmp) / 'evidence', 'commit', 'tree')
            report = json.loads((evidence.output / 'report.json').read_text(encoding='utf-8'))
            self.assertFalse(report['development_ready'])
            self.assertEqual(report['status'], 'running')
            self.assertEqual(report['not_tested'], ['Windows', 'Linux'])
            evidence.data['steps'].append({'name': 'interrupted', 'status': 'running', 'log': 'x.log'})
            evidence.finish()
            self.assertFalse(evidence.data['development_ready'])

    def test_changed_commit_invalidates_result(self):
        with patch.object(checks, 'revision', return_value=('new-commit', 'new-tree')):
            with self.assertRaises(checks.teststand.BuildError):
                checks.verify_revision(Path('/unused'), ('old-commit', 'old-tree'))

    def test_dirty_checkout_cannot_be_certified_as_commit(self):
        with patch.object(checks.subprocess, 'check_output', return_value=' M sedo/views.py\n'):
            with self.assertRaises(checks.teststand.BuildError):
                checks.revision(Path('/unused'))

    def test_failure_stops_pipeline_and_preserves_failed_report(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(checks, 'revision', return_value=('commit', 'tree')), \
             patch.object(checks.sys, 'version_info', (3, 13, 15)), \
             patch.object(checks.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'source')) as run:
            output = Path(tmp) / 'evidence'
            self.assertEqual(checks.main(['--output', str(output)]), 1)
            self.assertEqual(run.call_count, 1)
            report = json.loads((output / 'report.json').read_text(encoding='utf-8'))
            self.assertFalse(report['development_ready'])
            self.assertEqual(report['status'], 'failed')
