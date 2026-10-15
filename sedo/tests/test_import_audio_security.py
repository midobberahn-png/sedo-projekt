"""Bounded import/audio contracts; speech tests never download or load a model."""
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import date
import io
import math
from pathlib import Path
import struct
import tempfile
import threading
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch
import zipfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from sedo import audio_probe, import_probe
from sedo.document_probe import DocumentProbeError
from sedo.services import transcription


def spreadsheet(rows):
    from openpyxl import Workbook
    book = Workbook()
    for row in rows:
        book.active.append(row)
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def archive(entries, compression=zipfile.ZIP_STORED):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=compression) as zipped:
        for name, data in entries:
            zipped.writestr(name, data)
    return output.getvalue()


def legacy_spreadsheet():
    """Minimal BIFF2 worksheet: synthetic labels, no external writer/package."""
    def record(kind, data):
        return struct.pack('<HH', kind, len(data)) + data
    data = record(0x0009, struct.pack('<HH', 7, 0x0010))
    data += record(0x0042, struct.pack('<H', 1252))
    for row, values in enumerate((('name', 'amount'), ('Synthetic', '12.5'))):
        for column, value in enumerate(values):
            encoded = value.encode('ascii')
            data += record(0x0004, struct.pack('<HH3sB', row, column, b'\0\0\0', len(encoded)) + encoded)
    return data + record(0x000A, b'')


class UploadBoundaryTests(SimpleTestCase):
    def test_declared_oversize_rejected_without_reading(self):
        upload = Mock(size=11)
        with self.assertRaises(ValueError):
            import_probe.read_upload(upload, limit=10)
        upload.read.assert_not_called()

    def test_stream_size_is_checked_even_when_upload_size_lies(self):
        upload = Mock(size=1)
        upload.read.return_value = b'x' * 11
        with self.assertRaises(ValueError):
            import_probe.read_upload(upload, limit=10)
        upload.read.assert_called_once_with(11)

    def test_empty_and_nonseekable_uploads(self):
        upload = Mock(size=0)
        upload.seek.side_effect = OSError('synthetic non-seekable stream')
        upload.read.return_value = b''
        with self.assertRaises(ValueError):
            import_probe.read_upload(upload, limit=10)
        upload.read.return_value = b'abc'
        self.assertEqual(import_probe.read_upload(upload, limit=10), b'abc')

    def test_unsupported_extension_never_starts_parser(self):
        with patch('sedo.document_probe.run_operation') as run, self.assertRaises(ValueError):
            import_probe.read_dataframe(SimpleUploadedFile('input.html', b'<html>test</html>'))
        run.assert_not_called()

    def test_worker_failure_becomes_bounded_user_error(self):
        with patch('sedo.document_probe.run_operation', side_effect=DocumentProbeError()), self.assertRaises(ValueError):
            import_probe.read_dataframe(SimpleUploadedFile('input.csv', b'name\nTest\n'))

    def test_parent_rejects_malformed_worker_schema_and_cell_values(self):
        invalid = [
            {'columns': None, 'rows': []}, {'columns': ['x'], 'rows': 'invalid'},
            {'columns': ['x'], 'rows': [[1, 2]]}, {'columns': ['x'], 'rows': [[{'nested': 1}]]},
            {'columns': ['x'], 'rows': [['x' * (import_probe.MAX_CELL_CHARS + 1)]]},
            {'columns': ['x'], 'rows': [[float('nan')]]},
            {'columns': ['x'], 'rows': [[float('inf')]]},
            {'columns': ['x' * (import_probe.MAX_CELL_CHARS + 1)], 'rows': []},
        ]
        for result in invalid:
            with self.subTest(result_type=str(result)[:100]), patch('sedo.document_probe.run_operation', return_value=result), self.assertRaises(ValueError):
                import_probe.read_dataframe(SimpleUploadedFile('input.csv', b'x\n1\n'))


class TableWorkerTests(SimpleTestCase):
    def setUp(self):
        # In production each parser process exits; direct unit calls must not
        # leak their CSV field limit into unrelated tests in this interpreter.
        previous = csv.field_size_limit()
        self.addCleanup(csv.field_size_limit, previous)

    def test_real_csv_and_xlsx_round_trip_through_worker(self):
        csv = SimpleUploadedFile('customers.csv', 'name;city\nPrüfung;Berlin\n'.encode())
        frame = import_probe.read_dataframe(csv)
        self.assertEqual(frame.to_dict('records'), [{'name': 'Prüfung', 'city': 'Berlin'}])
        xlsx = SimpleUploadedFile('customers.xlsx', spreadsheet([['name', 'amount'], ['Test', 12.5]]))
        self.assertEqual(import_probe.read_dataframe(xlsx).to_dict('records'), [{'name': 'Test', 'amount': 12.5}])

    def test_xlsx_second_header_row_and_dates(self):
        data = spreadsheet([['Export title'], ['name', 'date'], ['Test', date(2026, 1, 1)]])
        frame = import_probe.read_dataframe(SimpleUploadedFile('input.xlsx', data), header=1)
        self.assertEqual(list(frame.columns), ['name', 'date'])
        self.assertEqual(frame.iloc[0]['date'], '2026-01-01T00:00:00')

    def test_real_legacy_xls_round_trip_through_worker(self):
        frame = import_probe.read_dataframe(SimpleUploadedFile('legacy.xls', legacy_spreadsheet()))
        self.assertEqual(frame.to_dict('records'), [{'name': 'Synthetic', 'amount': '12.5'}])

    def test_csv_rejects_extra_fields_but_pads_missing_cells(self):
        result = import_probe.worker_rows({'extension': '.csv'}, [b'a,b\n1\n'])
        self.assertEqual(result['rows'], [['1', None]])
        with self.assertRaises(ValueError):
            import_probe.worker_rows({'extension': '.csv'}, [b'a,b\n1,2,3\n'])

    def test_csv_and_xlsx_enforce_row_column_and_cell_limits(self):
        for extension, data in [('.csv', b'a\n1\n2\n3\n'),
                                ('.xlsx', spreadsheet([['a'], [1], [2], [3]]))]:
            with self.subTest(extension=extension), patch.object(import_probe, 'MAX_ROWS', 2), self.assertRaises(ValueError):
                import_probe.worker_rows({'extension': extension}, [data])
        for extension, data in [('.csv', b'a,b,c\n1,2,3\n'),
                                ('.xlsx', spreadsheet([['a', 'b', 'c'], [1, 2, 3]]))]:
            with self.subTest(extension=extension), patch.object(import_probe, 'MAX_COLUMNS', 2), self.assertRaises(ValueError):
                import_probe.worker_rows({'extension': extension}, [data])
        for extension, data in [('.csv', b'a\n123456789\n'),
                                ('.xlsx', spreadsheet([['a'], ['123456789']]))]:
            with self.subTest(extension=extension), patch.object(import_probe, 'MAX_CELL_CHARS', 8), self.assertRaises((ValueError, csv.Error)):
                import_probe.worker_rows({'extension': extension}, [data])

    def test_invalid_header_extension_and_upload_ceiling(self):
        for request in ({'extension': '.csv', 'header': 10}, {'extension': '.html'}):
            with self.subTest(request=request), self.assertRaises(ValueError):
                import_probe.worker_rows(request, [b'anything'])
        with patch.object(import_probe, 'MAX_UPLOAD_BYTES', 3), self.assertRaises(ValueError):
            import_probe.worker_rows({'extension': '.csv'}, [b'a\n12'])

    def test_nonfinite_spreadsheet_cells_become_empty(self):
        self.assertIsNone(import_probe._cell(math.inf))
        self.assertIsNone(import_probe._cell(math.nan))
        self.assertEqual(import_probe._cell(date(2026, 1, 1)), '2026-01-01')

    def test_xlsx_archive_rejects_traversal_duplicates_and_expansion(self):
        for name in ('../outside', '/absolute', 'dir\\file.xml'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                content = archive([('dir/file.xml' if '\\' in name else name, b'test')])
                if '\\' in name:
                    # zipfile normalizes names while writing on Windows; keep
                    # the malicious raw entry name in both ZIP headers.
                    content = content.replace(b'dir/file.xml', b'dir\\file.xml')
                import_probe._check_xlsx(content)
        with self.assertWarns(UserWarning):
            duplicate = archive([('same.xml', b'a'), ('same.xml', b'b')])
        with self.assertRaises(ValueError):
            import_probe._check_xlsx(duplicate)
        with self.assertRaises(ValueError):
            import_probe._check_xlsx(archive([('content.xml', b'0' * 100_000)], zipfile.ZIP_DEFLATED))
        with patch.object(import_probe, 'MAX_EXPANDED_BYTES', 3), self.assertRaises(ValueError):
            import_probe._check_xlsx(archive([('a.xml', b'1234')]))

    def test_xlsx_archive_count_and_encryption_are_rejected_before_parsing(self):
        with self.assertRaises(ValueError):
            import_probe._check_xlsx(archive([(f'{i}.xml', b'') for i in range(2001)]))
        member = SimpleNamespace(filename='encrypted.xml', file_size=1, compress_size=1, flag_bits=1)
        with patch('sedo.import_probe.zipfile.ZipFile') as zipped:
            zipped.return_value.__enter__.return_value.infolist.return_value = [member]
            with self.assertRaises(ValueError):
                import_probe._check_xlsx(b'fake')


class AudioWorkerTests(SimpleTestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory(prefix='sedo-audio-unit-')
        self.addCleanup(self.home.cleanup)
        self.model_dir = Path(self.home.name)
        (self.model_dir / 'model.bin').write_bytes(b'synthetic, never loaded')
        self.model = Mock()
        self.model.transcribe.return_value = ([SimpleNamespace(text=' Guten Tag '), SimpleNamespace(text=' Test ')], None)
        self.constructor = Mock(return_value=self.model)
        self.decoder = Mock(return_value=[0.0] * 16)
        package, audio = ModuleType('faster_whisper'), ModuleType('faster_whisper.audio')
        package.WhisperModel, audio.decode_audio = self.constructor, self.decoder
        self.modules = patch.dict('sys.modules', {'faster_whisper': package, 'faster_whisper.audio': audio})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def test_model_is_local_only_and_prompt_is_bounded(self):
        result = audio_probe.worker_transcribe({'model_path': str(self.model_dir), 'prompt': 'p' * 3000}, [b'fake-audio'])
        self.assertEqual(result['text'], 'Guten Tag Test')
        self.constructor.assert_called_once_with(str(self.model_dir), device='cpu', compute_type='int8', cpu_threads=2, num_workers=1, local_files_only=True)
        self.assertEqual(len(self.model.transcribe.call_args.kwargs['initial_prompt']), 2000)
        self.assertEqual(self.decoder.call_args.kwargs['sampling_rate'], 16000)

    def test_missing_model_never_decodes_audio_or_loads_model(self):
        with self.assertRaises(ValueError):
            audio_probe.worker_transcribe({'model_path': str(self.model_dir / 'missing')}, [b'fake'])
        self.decoder.assert_not_called()
        self.constructor.assert_not_called()

    def test_decoded_duration_is_checked_before_model_load(self):
        class TooLong:
            def __len__(self):
                return audio_probe.MAX_DURATION_SECONDS * 16000 + 1
        self.decoder.return_value = TooLong()
        with self.assertRaises(ValueError):
            audio_probe.worker_transcribe({'model_path': str(self.model_dir)}, [b'compressed-audio'])
        self.constructor.assert_not_called()

    def test_transcript_length_is_checked_while_iterating(self):
        seen = []
        def segments():
            for _ in range(100):
                seen.append(1)
                yield SimpleNamespace(text='x' * 10)
        self.model.transcribe.return_value = (segments(), None)
        with patch.object(audio_probe, 'MAX_TRANSCRIPT_CHARS', 20), self.assertRaises(ValueError):
            audio_probe.worker_transcribe({'model_path': str(self.model_dir)}, [b'fake'])
        self.assertEqual(len(seen), 2)


class TranscriptionBoundaryTests(SimpleTestCase):
    def test_empty_or_oversize_audio_never_starts_download_or_worker(self):
        with patch.object(transcription, '_get_model_path') as model, patch('sedo.document_probe.run_operation') as run:
            for data in (b'', b'x' * 11):
                with self.subTest(length=len(data)), patch.object(transcription, 'STT_MAX_AUDIO_BYTES', 10), self.assertRaises(ValueError):
                    transcription.transcribe_audio(data)
        model.assert_not_called()
        run.assert_not_called()

    def test_worker_schema_and_error_release_concurrency_slot(self):
        with patch.object(transcription, '_get_model_path', return_value=Path('/synthetic/model')):
            for result in ({}, {'text': 1}, {'text': 'x' * 50_001}):
                with self.subTest(result_type=type(result.get('text')).__name__), patch('sedo.document_probe.run_operation', return_value=result), self.assertRaises(ValueError):
                    transcription.transcribe_audio(b'fake')
            with patch('sedo.document_probe.run_operation', side_effect=DocumentProbeError()), self.assertRaises(ValueError):
                transcription.transcribe_audio(b'fake')
            with patch('sedo.document_probe.run_operation', side_effect=DocumentProbeError('busy')), self.assertRaisesMessage(transcription.TranscriptionUnavailable, 'ausgelastet'):
                transcription.transcribe_audio(b'fake')
            with patch('sedo.document_probe.run_operation', return_value={'text': 'ok'}):
                self.assertEqual(transcription.transcribe_audio(b'fake'), 'ok')

    def test_concurrent_recording_is_rejected_before_second_model_access(self):
        entered, finish = threading.Event(), threading.Event()
        def run(*args, **kwargs):
            entered.set()
            if not finish.wait(5):
                raise AssertionError('test synchronization timeout')
            return {'text': 'first'}
        with patch.object(transcription, '_get_model_path', return_value=Path('/synthetic/model')) as model, patch('sedo.document_probe.run_operation', side_effect=run):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(transcription.transcribe_audio, b'first')
                try:
                    self.assertTrue(entered.wait(5))
                    with self.assertRaises(transcription.TranscriptionUnavailable):
                        transcription.transcribe_audio(b'second')
                    model.assert_called_once()
                finally:
                    finish.set()
                self.assertEqual(future.result(), 'first')

    def test_missing_dependency_status_has_no_network_fallback(self):
        with patch.dict('sys.modules', {'faster_whisper': None}):
            self.assertFalse(transcription.stt_status()['available'])

    def test_model_name_is_allowlisted_before_download(self):
        with patch.object(transcription, 'stt_model_name', return_value='https://attacker.invalid/model'), self.assertRaises(transcription.TranscriptionUnavailable):
            transcription._get_model_path()

    def test_medium_is_unavailable_before_download_or_worker(self):
        utils = ModuleType('faster_whisper.utils')
        utils.download_model = Mock(side_effect=AssertionError('Unsupported models must not download'))
        modules = {'faster_whisper': ModuleType('faster_whisper'), 'faster_whisper.utils': utils}
        with patch.dict('sys.modules', modules), patch.object(transcription, 'stt_model_name', return_value='medium'), patch('sedo.document_probe.run_operation') as run:
            status = transcription.stt_status()
            self.assertFalse(status['available'])
            self.assertIn('tiny, base oder small', status['reason'])
            with self.assertRaisesMessage(transcription.TranscriptionUnavailable, 'Speichergrenze'):
                transcription.transcribe_audio(b'synthetic audio')
        utils.download_model.assert_not_called()
        run.assert_not_called()
        self.assertNotIn('medium', transcription._MODEL_REVISIONS)

    def test_supported_model_status_never_downloads_and_small_stays_default(self):
        self.assertEqual(transcription.STT_DEFAULT_MODEL, 'small')
        self.assertEqual(transcription._MODEL_NAMES, frozenset(('tiny', 'base', 'small')))
        with patch.dict('sys.modules', {'faster_whisper': ModuleType('faster_whisper')}), patch.object(transcription, '_get_model_path') as load:
            for name in ('tiny', 'base', 'small'):
                with self.subTest(model=name), patch.object(transcription, 'stt_model_name', return_value=name):
                    self.assertEqual(transcription.stt_status(), {'available': True, 'model': name})
        load.assert_not_called()

    def test_cached_model_needs_no_download_and_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix='sedo-model-unit-') as name:
            home = Path(name)
            model = home / 'whisper-models' / ('small-' + transcription._MODEL_REVISIONS['small'])
            model.mkdir(parents=True)
            for required in ('model.bin', 'config.json', 'tokenizer.json'):
                (model / required).write_bytes(b'fake')
            utils = ModuleType('faster_whisper.utils')
            utils.download_model = Mock(side_effect=AssertionError('No model downloads in unit tests'))
            with override_settings(SEDO_HOME=home), patch.object(transcription, 'stt_model_name', return_value='small'), patch.dict('sys.modules', {'faster_whisper': ModuleType('faster_whisper'), 'faster_whisper.utils': utils}):
                self.assertEqual(transcription._get_model_path(), model.resolve())
                (model / 'unexpected-link').symlink_to(model / 'model.bin')
                with self.assertRaises(transcription.TranscriptionUnavailable):
                    transcription._get_model_path()
            utils.download_model.assert_not_called()

    def test_model_root_or_directory_links_are_rejected_before_download(self):
        for link_root in (True, False):
            with self.subTest(link_root=link_root), tempfile.TemporaryDirectory(prefix='sedo-model-link-') as name:
                root = Path(name)
                home, outside = root / 'home', root / 'outside'
                home.mkdir()
                outside.mkdir()
                models = home / 'whisper-models'
                if link_root:
                    models.symlink_to(outside, target_is_directory=True)
                else:
                    models.mkdir()
                    (models / ('small-' + transcription._MODEL_REVISIONS['small'])).symlink_to(outside, target_is_directory=True)
                utils = ModuleType('faster_whisper.utils')
                utils.download_model = Mock(side_effect=AssertionError('Must reject before downloading'))
                with override_settings(SEDO_HOME=home), patch.object(transcription, 'stt_model_name', return_value='small'), patch.dict('sys.modules', {'faster_whisper': ModuleType('faster_whisper'), 'faster_whisper.utils': utils}), self.assertRaises(transcription.TranscriptionUnavailable):
                    transcription._get_model_path()
                utils.download_model.assert_not_called()
                self.assertEqual(list(outside.iterdir()), [])

    def test_download_uses_fixed_provider_revision_without_account_token(self):
        with tempfile.TemporaryDirectory(prefix='sedo-model-revision-') as name:
            home = Path(name).resolve()
            revision = transcription._MODEL_REVISIONS['small']
            self.assertRegex(revision, r'^[0-9a-f]{40}$')
            model = home / 'whisper-models' / ('small-' + revision)
            # Old unversioned files must not satisfy a pinned model request.
            legacy = home / 'whisper-models/small'
            legacy.mkdir(parents=True)
            for filename in ('model.bin', 'config.json', 'tokenizer.json'):
                (legacy / filename).write_bytes(b'unversioned synthetic cache')
            def fake_download(repository, **kwargs):
                target = Path(kwargs['output_dir'])
                self.assertEqual(target, model)
                target.mkdir(parents=True)
                for filename in ('model.bin', 'config.json', 'tokenizer.json'):
                    (target / filename).write_bytes(b'synthetic pinned cache, no download')
            utils = ModuleType('faster_whisper.utils')
            utils.download_model = Mock(side_effect=fake_download)
            with override_settings(SEDO_HOME=home), patch.object(transcription, 'stt_model_name', return_value='small'), patch.dict('sys.modules', {'faster_whisper': ModuleType('faster_whisper'), 'faster_whisper.utils': utils}):
                self.assertEqual(transcription._get_model_path(), model.resolve())
            utils.download_model.assert_called_once_with('Systran/faster-whisper-small', revision=revision,
                output_dir=str(model), use_auth_token=False)
