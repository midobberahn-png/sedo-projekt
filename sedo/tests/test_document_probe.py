"""Security regression tests for the untrusted-document process boundary."""
import json
import os
from pathlib import Path
import subprocess
import sys
import io
import tempfile
from unittest import skipUnless
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from pypdf import PdfReader

from sedo.document_probe import (DocumentProbeError, MAX_DOCUMENT_BYTES,
    check_isolation, classify_document, extract_pdf_text, extract_xml_text,
    image_pdf, image_preview, merge_pdfs, validate_image, validate_pdf,
    _resource_limits, _runtime_read_paths, _sandbox_command)
from .document_fixtures import text_pdf


class DocumentProbeTests(SimpleTestCase):
    def test_macos_frozen_bundle_grants_only_its_linked_resource_directory(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            contents = root / 'Synthetic.app' / 'Contents'
            runtime = contents / 'Frameworks'
            resources = contents / 'Resources'
            runtime.mkdir(parents=True)
            resources.mkdir()
            (resources / 'base_library.zip').write_bytes(b'synthetic-runtime')
            (runtime / 'base_library.zip').symlink_to('../Resources/base_library.zip')
            with patch('sedo.document_probe.sys.platform', 'darwin'), \
                    patch('sedo.document_probe.sys.frozen', True, create=True), \
                    patch('sedo.document_probe.sys._MEIPASS', str(runtime), create=True), \
                    patch('sedo.document_probe.sys.executable', str(contents / 'MacOS' / 'SEDO')):
                readable = _runtime_read_paths()
            self.assertIn(runtime.resolve(), readable)
            self.assertIn(resources.resolve(), readable)
            self.assertNotIn(contents.resolve(), readable)
            self.assertNotIn(root.resolve(), readable)
            # Windows may resolve the same temporary directory once through
            # its short 8.3 alias. Compare the actual directory identity.
            linked_parent = (runtime / 'base_library.zip').resolve().parent
            self.assertTrue(linked_parent.samefile(resources))

    def test_non_bundle_or_mismatched_executable_never_grants_sibling_resources(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            for bundle_name, matching_executable in (
                    ('not-a-bundle', True), ('Synthetic.app', False)):
                with self.subTest(bundle=bundle_name):
                    contents = root / bundle_name / 'Contents'
                    executable = (contents / 'MacOS' / 'SEDO' if matching_executable
                                  else root / 'unrelated' / 'SEDO')
                    runtime, resources = contents / 'Frameworks', contents / 'Resources'
                    runtime.mkdir(parents=True)
                    resources.mkdir()
                    with patch('sedo.document_probe.sys.platform', 'darwin'), \
                            patch('sedo.document_probe.sys.frozen', True, create=True), \
                            patch('sedo.document_probe.sys._MEIPASS', str(runtime), create=True), \
                            patch('sedo.document_probe.sys.executable', str(executable)):
                        self.assertNotIn(resources.resolve(), _runtime_read_paths())

    def test_spreadsheet_build_gate_exercises_csv_xlsx_and_legacy_xls(self):
        import importlib.util
        entry_path = Path(__file__).resolve().parents[2] / 'tools/desktop_entry.py'
        spec = importlib.util.spec_from_file_location('sedo_desktop_entry_check', entry_path)
        entry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(entry)
        self.assertEqual(entry._check_spreadsheet_workers(), {'csv': True, 'xlsx': True, 'xls': True})

    def test_classification_extraction_and_merge_work_in_child(self):
        data = text_pdf('Rechnung 123\nGesamtbetrag: 123,45 EUR')
        self.assertEqual(classify_document(data, 'rechnung.pdf').kind, 'invoice')
        self.assertIn('Rechnung', extract_pdf_text(data))
        merged = merge_pdfs([data, data])
        self.assertEqual(len(PdfReader(__import__('io').BytesIO(merged)).pages), 2)

    def test_oversize_document_is_rejected_before_process_start(self):
        with patch('sedo.document_probe.subprocess.Popen') as start:
            result = classify_document(b'0' * (MAX_DOCUMENT_BYTES + 1), 'rechnung.pdf')
        self.assertEqual(result.kind, 'unknown')
        start.assert_not_called()

    def test_worker_receives_no_application_secrets(self):
        seen = {}

        class Finished:
            returncode = 0
            args = ['worker']

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

        def start(command, **kwargs):
            seen.update(kwargs['env'])
            directory = Path(command[-1])
            (directory / 'result.json').write_text(
                json.dumps({'ok': True, 'kind': 'unknown', 'reason': 'test'}), encoding='utf-8')
            return Finished()

        secret_names = ('DJANGO_SECRET_KEY', 'SEDO_FINTS_PIN', 'SEDO_SMTP_PASSWORD', 'OPENAI_API_KEY',
                        'PYTHONPATH', 'PYTHONHOME', 'LD_PRELOAD', 'DYLD_INSERT_LIBRARIES')
        with patch.dict(os.environ, {name: 'synthetic-secret' for name in secret_names}), \
                patch('sedo.document_probe.subprocess.Popen', side_effect=start):
            classify_document(b'%PDF-', 'rechnung.pdf')
        for name in secret_names:
            self.assertNotIn(name, seen)

    def test_timeout_kills_worker_and_fails_closed(self):
        child = MagicMock(args=['worker'], returncode=None)
        child.poll.return_value = None
        with patch('sedo.document_probe.subprocess.Popen', return_value=child), \
                patch('sedo.document_probe._wait_bounded',
                      side_effect=subprocess.TimeoutExpired('worker', 5)):
            result = classify_document(b'%PDF-', 'rechnung.pdf')
        self.assertEqual(result.kind, 'unknown')
        child.kill.assert_called_once()

    def test_malformed_worker_result_never_approves(self):
        class Finished:
            returncode = 0
            args = ['worker']

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

        for output in (b'garbage', b'[]', b'{"ok":true,"kind":"invoice","reason":17}',
                       b'[' * 1200 + b'0' + b']' * 1200):
            with self.subTest(output=output):
                def start(command, **kwargs):
                    (Path(command[-1]) / 'result.json').write_bytes(output)
                    return Finished()
                with patch('sedo.document_probe.subprocess.Popen', side_effect=start):
                    self.assertEqual(classify_document(b'%PDF-', 'rechnung.pdf').kind, 'unknown')

    def test_empty_scan_and_invalid_pdf_are_safe_failures(self):
        blank = merge_pdfs([text_pdf('')])
        self.assertEqual(len(PdfReader(__import__('io').BytesIO(blank)).pages), 1)
        with self.assertRaises(DocumentProbeError):
            extract_pdf_text(text_pdf(''))
        with self.assertRaises(DocumentProbeError):
            merge_pdfs([b'not a pdf'])

    def test_source_worker_does_not_require_console_streams(self):
        data = text_pdf('Rechnung 123\nGesamtbetrag: 123,45 EUR')
        with patch.object(sys, 'stdout', None), patch.object(sys, 'stderr', None):
            # The public call starts a fresh interpreter; it must remain usable
            # even when its parent represents a windowed native application.
            result = classify_document(data, 'rechnung.pdf')
        self.assertEqual(result.kind, 'invoice')

    def test_raw_pdf_xml_and_image_apis_never_call_parent_parsers(self):
        from PIL import Image
        picture = io.BytesIO()
        Image.new('RGB', (32, 16), 'white').save(picture, 'PNG')
        raster = picture.getvalue()
        pdf = text_pdf('synthetic')
        with patch('pypdf.PdfReader', side_effect=AssertionError('parent PDF parser')), \
                patch('PIL.Image.open', side_effect=AssertionError('parent image parser')), \
                patch('lxml.etree.fromstring', side_effect=AssertionError('parent XML parser')):
            self.assertEqual(validate_pdf(pdf), 1)
            self.assertEqual(validate_image(raster), 'PNG')
            self.assertTrue(image_preview(raster).startswith(b'\x89PNG'))
            self.assertTrue(image_pdf(raster).startswith(b'%PDF-'))
            self.assertIn('synthetic', extract_xml_text(b'<Invoice>synthetic</Invoice>'))

    def test_xml_dtd_and_external_entities_fail_closed(self):
        with self.assertRaises(DocumentProbeError):
            extract_xml_text(b'<!DOCTYPE Invoice [<!ENTITY secret SYSTEM "file:///nonexistent-synthetic">]>'
                             b'<Invoice>&secret;</Invoice>')

    @skipUnless(os.name == 'posix', 'POSIX limit contract')
    def test_linux_unlimited_sentinel_is_replaced_by_finite_hard_limits(self):
        import resource
        with patch('sedo.document_probe.sys.platform', 'linux'), \
                patch.object(resource, 'RLIM_INFINITY', -1), \
                patch.object(resource, 'getrlimit', return_value=(-1, -1)), \
                patch.object(resource, 'setrlimit') as set_limit:
            _resource_limits()
        limits = {call.args[0]: call.args[1] for call in set_limit.call_args_list}
        self.assertEqual(limits[resource.RLIMIT_CPU], (8, 8))
        self.assertEqual(limits[resource.RLIMIT_FSIZE], (120 * 1024 * 1024,) * 2)
        self.assertEqual(limits[resource.RLIMIT_AS], (768 * 1024 * 1024,) * 2)

    def test_windows_job_object_contract_and_failure_are_checked(self):
        # This validates the ctypes contract on every host; a native Windows
        # process check is still required and is not claimed by this mock test.
        kernel = MagicMock()
        kernel.CreateJobObjectW.return_value = 7
        kernel.SetInformationJobObject.return_value = 1
        kernel.AssignProcessToJobObject.return_value = 1
        with patch('sedo.document_probe.os.name', 'nt'), \
                patch('ctypes.WinDLL', create=True, return_value=kernel):
            _resource_limits()
            info = kernel.SetInformationJobObject.call_args.args[2]._obj
            self.assertEqual(info.basic.active, 1)
            self.assertEqual(info.basic.process_time, 80_000_000)
            self.assertEqual(info.process_memory, 768 * 1024 * 1024)
            self.assertEqual(info.basic.flags & 0x210a, 0x210a)
            kernel.AssignProcessToJobObject.return_value = 0
            with self.assertRaises(OSError):
                _resource_limits()

    @skipUnless(sys.platform == 'darwin' or sys.platform.startswith('linux'), 'OS filesystem sandbox')
    def test_actual_worker_cannot_read_write_external_markers_or_connect_to_host(self):
        result = check_isolation()
        self.assertTrue(result['filesystem_network_sandbox'])
        self.assertTrue(result['filesystem_read_blocked'])
        self.assertTrue(result['filesystem_write_blocked'])
        self.assertTrue(result['network_blocked'])

    def test_linux_without_sandbox_never_falls_back_to_unrestricted_parser(self):
        with tempfile.TemporaryDirectory() as name, \
                patch('sedo.document_probe.sys.platform', 'linux'), \
                patch('sedo.document_probe.shutil.which', return_value=None):
            with self.assertRaises(DocumentProbeError):
                _sandbox_command([sys.executable, '-V'], Path(name))
