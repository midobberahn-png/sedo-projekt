"""Resource-bounded child process for untrusted document parsing.

The parent and child exchange only private files.  This also works in the
windowed Windows build, where stdin/stdout may not exist.  Any timeout, crash,
limit or malformed result fails closed in the parent process. macOS Seatbelt
and Linux bubblewrap additionally restrict filesystem and network access.
Windows Job Objects impose resource limits, not a filesystem/network sandbox.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from pathlib import PurePath
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from dataclasses import dataclass


MAX_DOCUMENT_BYTES = 25 * 1024 * 1024
MAX_BATCH_BYTES = 100 * 1024 * 1024
MAX_OUTPUT_BYTES = 120 * 1024 * 1024
MAX_RESULT_BYTES = 200 * 1024
WALL_TIMEOUT = {'classify': 5, 'extract_text': 5, 'merge_pdf': 15,
                'validate_pdf': 5, 'image_preview': 10, 'image_pdf': 15,
                'validate_image': 5,
                'xml_text': 5, 'table_rows': 20, 'transcribe': 120,
                'isolation_check': 5}
BINARY_ACTIONS = {'merge_pdf', 'image_preview', 'image_pdf'}
RESULT_LIMITS = {'table_rows': 8 * 1024 * 1024}
MEMORY_LIMITS = {'transcribe': 1536 * 1024 * 1024}
_SLOTS = threading.BoundedSemaphore(2)


class DocumentProbeError(ValueError):
    """A document operation could not finish safely."""

    def __init__(self, code='unsafe'):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Detection:
    kind: str
    reason: str


def _normalized(value):
    return unicodedata.normalize('NFKC', value).casefold().replace('_', ' ')


_INVOICE = r'(?:rechnung(?:skopie|sduplikat)?|abschlagsrechnung|teilrechnung|schlussrechnung|stornorechnung|invoice|tax invoice)'
_OTHER = r'(?:angebot|kostenvoranschlag|lieferschein|lieferavis|kommissionierschein|auftragsbestätigung|auftragsbestaetigung|bestellbestätigung|bestellbestaetigung|bestellung|mahnung|zahlungserinnerung|gutschrift|proforma(?:[ -]rechnung|[ -]invoice)?|quotation|quote|delivery note|credit note|termin(?:bestätigung|bestaetigung|ankündigung|ankuendigung|erinnerung)?|zählerablesung|zaehlerablesung|ablesetermin|ablesung)'
_INVOICE_WORD = re.compile(r'(?<!\w)' + _INVOICE + r'(?![a-zäöüß])')
_OTHER_WORD = re.compile(r'(?<!\w)' + _OTHER + r'(?![a-zäöüß])')
_TITLE = re.compile(r'^\s*(?:betreff\s*:\s*)?(' + _OTHER + '|' + _INVOICE + r')(?=$|[\s:№#./\d-])')
_NUMBER = re.compile(r'\b(?:rechnungs(?:nummer|[ -]?nr\.?)|invoice\s*(?:number|no\.?|#))\s*[:#.-]?\s*\S+')
_VALUE = r'(?<![\d.,])\d[\d .]{0,40}[,.]\d{2}(?![\d.,])'
_AMOUNT = re.compile(
    _VALUE + r'\s{0,12}(?:eur\b|€|usd\b|gbp\b|chf\b)'
    + r'|(?:eur|€|usd|gbp|chf)\s{0,12}' + _VALUE
    + r'|\b(?:gesamtbetrag|rechnungsbetrag|zahlbetrag|zu zahlen|bruttobetrag|summe brutto|gesamt|total|amount due)'
      r'\s{0,12}[:a-z€ ]{0,32}' + _VALUE)
_INVOICE_CODES = {'82', '326', '331', '380', '384', '386', '388', '389', '393',
                  '395', '575', '623', '780', '870', '875', '876', '877'}


def _classify_text(text):
    lines = _normalized(text[:24000])[:24000].splitlines()
    title = None
    for line in lines[:100]:
        if _NUMBER.match(line.strip()):
            title = 'rechnung'
            break
        match = _TITLE.match(line)
        if match:
            title = match.group(1)
            break
    if title and _OTHER_WORD.fullmatch(title):
        return Detection('other', 'Dokumenttyp: ' + title)
    content = '\n'.join(lines)
    if (title or _NUMBER.search(content)) and _AMOUNT.search(content):
        return Detection('invoice', 'Rechnungskennzeichen und Betrag im Dokument erkannt')
    return Detection('unknown', 'Kein eindeutiger Rechnungsinhalt erkannt')


def _classify_xml(data):
    from lxml import etree
    try:
        parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
        root = etree.fromstring(data, parser)
        if root.getroottree().docinfo.doctype:
            return Detection('unknown', 'XML mit Dokumenttyp-Deklaration wird nicht automatisch übernommen')
        tag = etree.QName(root)
        if tag.namespace == 'urn:oasis:names:specification:ubl:schema:xsd:Invoice-2' and tag.localname == 'Invoice':
            ns = {'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2',
                  'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2'}
            code = root.findtext('cbc:InvoiceTypeCode', namespaces=ns)
            number = root.findtext('cbc:ID', namespaces=ns)
            total = root.findtext('cac:LegalMonetaryTotal/cbc:PayableAmount', namespaces=ns)
        elif tag.namespace == 'urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100' and tag.localname == 'CrossIndustryInvoice':
            ns = {'rsm': tag.namespace,
                  'ram': 'urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100'}
            code = root.findtext('rsm:ExchangedDocument/ram:TypeCode', namespaces=ns)
            number = root.findtext('rsm:ExchangedDocument/ram:ID', namespaces=ns)
            total = root.findtext('rsm:SupplyChainTradeTransaction/ram:ApplicableHeaderTradeSettlement/ram:SpecifiedTradeSettlementHeaderMonetarySummation/ram:GrandTotalAmount', namespaces=ns)
        else:
            if tag.localname in ('Order', 'Quotation', 'DespatchAdvice', 'CreditNote') and (tag.namespace or '').startswith('urn:oasis:names:specification:ubl:schema:xsd:'):
                return Detection('other', 'XML-Dokumenttyp: ' + tag.localname)
            return Detection('unknown', 'Kein unterstütztes Rechnungs-XML')
        if code and code.strip() not in _INVOICE_CODES:
            return Detection('other' if code.strip() in ('325', '381') else 'unknown',
                             'XML-Belegart ist keine unterstützte Rechnung')
        if code and number and number.strip() and total and re.fullmatch(r'-?\d+(?:\.\d+)?', total.strip()):
            return Detection('invoice', 'Strukturiertes Rechnungs-XML erkannt')
    except (etree.XMLSyntaxError, ValueError, TypeError):
        pass
    return Detection('unknown', 'Rechnungs-XML nicht eindeutig lesbar')


def _classify_document(data, filename, subject=''):
    import io
    filename = _normalized(PurePath(filename).name)
    named_other = _OTHER_WORD.search(filename)
    ext = PurePath(filename).suffix
    if ext == '.xml':
        return _classify_xml(data)
    text = ''
    if ext == '.pdf':
        try:
            from pypdf import PdfReader
            if not data.startswith(b'%PDF-'):
                return Detection('unknown', 'Keine lesbare PDF-Datei')
            reader = PdfReader(io.BytesIO(data), strict=False)
            if reader.is_encrypted or len(reader.pages) > 200:
                return Detection('unknown', 'PDF nicht automatisch lesbar')
            text = '\n'.join((page.extract_text() or '')[:12000] for page in reader.pages[:3])[:24000]
        except Exception:
            return Detection('unknown', 'PDF nicht automatisch lesbar')
        if text.strip():
            detected = _classify_text(text)
            if detected.kind != 'unknown' or not named_other:
                return detected
    elif ext in ('.png', '.jpg', '.jpeg', '.tif', '.tiff'):
        try:
            from PIL import Image
            with Image.open(io.BytesIO(data)) as picture:
                if picture.width * picture.height > 25_000_000:
                    return Detection('unknown', 'Bild nicht automatisch lesbar')
                picture.verify()
        except Exception:
            return Detection('unknown', 'Bild nicht automatisch lesbar')
    else:
        return Detection('unknown', 'Kein unterstütztes Rechnungsformat')
    if named_other:
        return Detection('other', 'Dateiname kennzeichnet ' + named_other.group())
    if text.strip():
        return Detection('unknown', 'Kein eindeutiger Rechnungsinhalt erkannt')
    subject = _normalized(subject)
    if _INVOICE_WORD.search(filename) and _INVOICE_WORD.search(subject) and not _OTHER_WORD.search(subject):
        return Detection('invoice', 'Scan in Dateiname und Betreff ausdrücklich als Rechnung bezeichnet')
    return Detection('unknown', 'Scan nicht eindeutig als Rechnung bezeichnet')


# Focused rule helpers remain importable for unit tests and callers that only
# classify already trusted text/XML. Raw documents must use classify_document.
normalized = _normalized
INVOICE = _INVOICE
OTHER = _OTHER
INVOICE_WORD = _INVOICE_WORD
OTHER_WORD = _OTHER_WORD
TITLE = _TITLE
NUMBER = _NUMBER
AMOUNT = _AMOUNT
INVOICE_CODES = _INVOICE_CODES
classify_text = _classify_text
classify_xml = _classify_xml


def _memory_limit(action):
    return MEMORY_LIMITS.get(action, 768 * 1024 * 1024)


def _resource_limits(action='classify'):
    """Apply memory, CPU and output limits before importing parsers."""
    if os.name == 'posix':
        import resource
        cpu = {'table_rows': 18, 'transcribe': 110}.get(action, 8)
        limits = [(resource.RLIMIT_CPU, cpu), (resource.RLIMIT_FSIZE, MAX_OUTPUT_BYTES)]
        # Darwin does not reliably enforce RLIMIT_AS. The parent monitors RSS
        # through libproc and kills a child that exceeds the memory ceiling.
        if sys.platform != 'darwin':
            limits.append((resource.RLIMIT_AS, _memory_limit(action)))
        for key, soft in limits:
            _, hard = resource.getrlimit(key)
            limit = soft if hard == resource.RLIM_INFINITY else min(soft, hard)
            # RLIM_INFINITY is -1 on Linux and a positive sentinel on Darwin.
            # A parser must not be able to raise a soft limit afterwards.
            resource.setrlimit(key, (limit, limit))
        return
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]

        class Basic(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                        ('flags', wintypes.DWORD), ('min_working_set', ctypes.c_size_t),
                        ('max_working_set', ctypes.c_size_t), ('active', wintypes.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD),
                        ('scheduling', wintypes.DWORD)]

        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', IO), ('process_memory', ctypes.c_size_t),
                        ('job_memory', ctypes.c_size_t), ('peak_process_memory', ctypes.c_size_t),
                        ('peak_job_memory', ctypes.c_size_t)]

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                   ctypes.c_void_p, wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        global _JOB_HANDLE
        _JOB_HANDLE = kernel.CreateJobObjectW(None, None)
        info = Extended()
        info.basic.flags = 0x100 | 0x8 | 0x2 | 0x2000  # memory, one process, CPU, kill-on-close
        info.basic.process_time = {'table_rows': 18, 'transcribe': 110}.get(action, 8) * 10_000_000
        info.basic.active = 1
        info.process_memory = _memory_limit(action)
        if (not _JOB_HANDLE
                or not kernel.SetInformationJobObject(_JOB_HANDLE, 9, ctypes.byref(info), ctypes.sizeof(info))
                or not kernel.AssignProcessToJobObject(_JOB_HANDLE, kernel.GetCurrentProcess())):
            raise OSError('Cannot enforce document process limits')
        return
    raise OSError('Unsupported resource isolation')


def _private_write(path, data):
    with path.open('xb') as handle:
        os.chmod(path, 0o600)
        handle.write(data)


def _parent_command(directory):
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--sedo-document-probe', str(directory)], None
    # -I ignores injected PYTHONPATH, user site packages and the current folder.
    return [sys.executable, '-I', '-B', str(Path(__file__).resolve()), str(directory)], None


def _runtime_read_paths():
    paths = {Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve(),
             Path(sys.executable).resolve(), Path(__file__).resolve().parent}
    if getattr(sys, 'frozen', False):
        runtime = Path(sys._MEIPASS)
        paths.add(runtime.resolve())
        # PyInstaller's macOS bundle links data (including base_library.zip)
        # from Frameworks to Resources. Seatbelt checks the resolved target;
        # grant only this application's resource directory, never all Contents
        # or a sibling host directory in non-bundle/frozen layouts.
        contents = runtime.parent
        if (sys.platform == 'darwin' and runtime.name == 'Frameworks'
                and contents.name == 'Contents' and contents.parent.suffix == '.app'
                and Path(sys.executable).parent == contents / 'MacOS'):
            resources = contents / 'Resources'
            if resources.is_dir():
                paths.add(resources.resolve())
    if any(path == Path('/') or path == Path.home() for path in paths):
        raise DocumentProbeError('sandbox_unavailable')
    return paths


def _sandbox_command(command, directory, read_paths=()):
    """Build a platform restriction; never silently fall back on macOS/Linux.

    Windows retains an explicitly different guarantee: bounded Job Object,
    inherited-user filesystem access. Build diagnostics report that distinction.
    """
    directory = directory.resolve()
    paths = _runtime_read_paths() | {Path(path).resolve() for path in read_paths}
    if sys.platform == 'darwin':
        # Python's trusted startup precedes sandbox_init. No document is read
        # until the child installs the profile; missing APIs fail closed there.
        return command, 'macos-seatbelt'
    if sys.platform.startswith('linux'):
        executable = shutil.which('bwrap', path=os.defpath)
        if not executable:
            raise DocumentProbeError('sandbox_unavailable')
        # An empty mount namespace exposes only runtime code, the input/output
        # directory and kernel-provided /proc and /dev. No host home or /tmp.
        bind_paths = paths | {Path(path) for path in ('/usr', '/lib', '/lib64') if Path(path).exists()}
        args = [executable, '--unshare-all', '--die-with-parent', '--new-session',
                '--cap-drop', 'ALL', '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp']
        for path in sorted(bind_paths, key=lambda item: (len(item.parts), str(item))):
            args += ['--ro-bind', str(path), str(path)]
        args += ['--bind', str(directory), str(directory), '--chdir', str(directory), '--', *command]
        return args, 'linux-bubblewrap'
    if os.name == 'nt':
        return command, 'windows-jobobject'
    raise DocumentProbeError('sandbox_unavailable')


def _macos_profile(directory, read_paths=()):
    # SBPL strings use the same quoted escaping needed here as JSON strings.
    def rule(path):
        return '(' + ('subpath' if path.is_dir() else 'literal') + ' ' + json.dumps(str(path)) + ')'
    system = [Path(path) for path in ('/System', '/usr/lib', '/usr/share',
              '/Library/Apple', '/private/var/db/dyld', '/dev/null', '/dev/urandom', '/dev/random')]
    paths = _runtime_read_paths() | {Path(path).resolve() for path in read_paths} | set(system)
    readable = ' '.join(rule(path) for path in sorted(paths, key=str))
    return ('(version 1) (deny default) (allow file-read-metadata) '
            '(allow sysctl-read) (allow mach-lookup '
            '(global-name "com.apple.system.logger") (global-name "com.apple.logd")) '
            '(allow file-read* ' + readable + ' (subpath ' + json.dumps(str(directory.resolve())) + ')) '
            '(allow file-write* (subpath ' + json.dumps(str(directory.resolve())) + ') (literal "/dev/null"))')


def _enter_macos_sandbox(directory, read_paths=()):
    import ctypes
    profile = _macos_profile(directory, read_paths).encode('utf-8')
    library = ctypes.CDLL('/usr/lib/libsandbox.dylib', use_errno=True)
    library.sandbox_init.argtypes = [ctypes.c_char_p, ctypes.c_uint64, ctypes.POINTER(ctypes.c_void_p)]
    library.sandbox_init.restype = ctypes.c_int
    library.sandbox_free_error.argtypes = [ctypes.c_void_p]
    error = ctypes.c_void_p()
    result = library.sandbox_init(profile, 0, ctypes.byref(error))
    if error:
        library.sandbox_free_error(error)
    if result != 0:
        raise DocumentProbeError('sandbox_unavailable')


def _model_read_paths(metadata):
    if 'model_path' not in metadata:
        return ()
    directory = Path(metadata['model_path']).resolve()
    if not directory.is_dir() or directory in (Path('/'), Path.home()):
        raise DocumentProbeError()
    files = list(directory.rglob('*'))
    if len(files) > 256:
        raise DocumentProbeError()
    # HuggingFace snapshots can link exact files into a sibling blob store.
    # Grant those files, never the cache directory containing credentials.
    return [directory, *(path.resolve() for path in files if path.is_file())]


def _wait_bounded(process, timeout, max_memory_bytes=768 * 1024 * 1024):
    if sys.platform != 'darwin':
        return process.wait(timeout=timeout)
    deadline = time.monotonic() + timeout
    while process.poll() is None:
        if time.monotonic() >= deadline:
            raise subprocess.TimeoutExpired(process.args, timeout)
        try:
            import ctypes
            import struct
            libproc = ctypes.CDLL('libproc.dylib')
            usage = ctypes.create_string_buffer(256)
            # RUSAGE_INFO_V2 starts with uuid_t followed by the v0 counters;
            # resident_size is the seventh uint64 at byte offset 64.
            if libproc.proc_pid_rusage(process.pid, 2, ctypes.byref(usage)) != 0:
                raise OSError('Cannot read child resource usage')
            rss_bytes = struct.unpack_from('=Q', usage.raw, 64)[0]
        except (OSError, ValueError) as exc:
            raise DocumentProbeError() from exc
        if rss_bytes > max_memory_bytes:
            raise DocumentProbeError('unsafe')
        time.sleep(0.05)
    return process.returncode


def _run(action, documents, **metadata):
    if (action not in WALL_TIMEOUT or not isinstance(documents, (list, tuple))
            or not 0 < len(documents) <= 100):
        raise DocumentProbeError()
    if any(not isinstance(item, bytes) or len(item) > MAX_DOCUMENT_BYTES for item in documents):
        raise DocumentProbeError('too_large')
    if sum(map(len, documents)) > MAX_BATCH_BYTES:
        raise DocumentProbeError('too_large')
    if not _SLOTS.acquire(blocking=False):
        raise DocumentProbeError('busy')
    try:
        with tempfile.TemporaryDirectory(prefix='sedo-document-') as name:
            directory = Path(name)
            request = {'action': action, 'input_count': len(documents), **metadata}
            header = json.dumps(request, ensure_ascii=True).encode()
            if len(header) > 8192:
                raise DocumentProbeError()
            _private_write(directory / 'request.json', header)
            for index, data in enumerate(documents):
                _private_write(directory / f'input-{index}.bin', data)
            command, cwd = _parent_command(directory)
            command, backend = _sandbox_command(command, directory,
                _model_read_paths(metadata) if action == 'transcribe' else ())
            allowed = {'SYSTEMROOT', 'WINDIR', 'LANG', 'LC_ALL'}
            env = {key: value for key, value in os.environ.items() if key in allowed}
            env.update(PATH=os.defpath, PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1',
                       PYTHONNOUSERSITE='1', TMPDIR=str(directory), TMP=str(directory), TEMP=str(directory),
                       SEDO_PARSER_BACKEND=backend)
            process = subprocess.Popen(command, cwd=directory, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, close_fds=True,
                **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}))
            try:
                _wait_bounded(process, WALL_TIMEOUT[action], _memory_limit(action))
                if process.returncode != 0:
                    raise DocumentProbeError()
                result_path = directory / 'result.json'
                if result_path.is_symlink():
                    raise DocumentProbeError()
                result_limit = RESULT_LIMITS.get(action, MAX_RESULT_BYTES)
                with result_path.open('rb') as handle:
                    result = handle.read(result_limit + 1)
                if len(result) > result_limit:
                    raise DocumentProbeError()
                response = json.loads(result)
                if not isinstance(response, dict) or not isinstance(response.get('ok'), bool):
                    raise DocumentProbeError()
                if not response['ok']:
                    code = response.get('error', 'unsafe')
                    if code not in {'unsafe', 'encrypted', 'too_many_pages', 'empty', 'too_large', 'invalid_xml'}:
                        code = 'unsafe'
                    raise DocumentProbeError(code)
                if action in BINARY_ACTIONS:
                    output_path = directory / 'output.bin'
                    if output_path.is_symlink():
                        raise DocumentProbeError()
                    with output_path.open('rb') as handle:
                        output = handle.read(MAX_OUTPUT_BYTES + 1)
                    if not output or len(output) > MAX_OUTPUT_BYTES:
                        raise DocumentProbeError()
                    return output
                return response
            except subprocess.TimeoutExpired as exc:
                raise DocumentProbeError('unsafe') from exc
            finally:
                if process.poll() is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass
    except (OSError, TypeError, ValueError, RecursionError, json.JSONDecodeError) as exc:
        if isinstance(exc, DocumentProbeError):
            raise
        raise DocumentProbeError() from exc
    finally:
        _SLOTS.release()


def run_operation(action, documents, **metadata):
    """Shared bounded worker API for trusted parent-side import integrations."""
    return _run(action, documents, **metadata)


def classify_document(data, filename, subject=''):
    unknown = Detection('unknown', 'Dokument konnte innerhalb der Sicherheitsgrenzen nicht geprüft werden')
    if not isinstance(filename, str) or not isinstance(subject, str):
        return unknown
    if len(filename) > 240 or len(subject) > 2000:
        return unknown
    try:
        result = _run('classify', [data], filename=filename, subject=subject)
        if (result.get('kind') not in ('invoice', 'other', 'unknown')
                or not isinstance(result.get('reason'), str) or len(result['reason']) > 500):
            return unknown
        return Detection(result['kind'], result['reason'])
    except DocumentProbeError as exc:
        if exc.code == 'busy':
            return Detection('unknown', 'Dokumentprüfung ausgelastet; bitte manuell prüfen')
        return unknown


def extract_pdf_text(data):
    result = _run('extract_text', [data])
    text = result.get('text')
    if not isinstance(text, str) or len(text) > 24000:
        raise DocumentProbeError()
    return text


def validate_pdf(data, max_pages=500):
    result = _run('validate_pdf', [data], max_pages=max_pages)
    pages = result.get('pages')
    if not isinstance(pages, int) or not 0 < pages <= max_pages:
        raise DocumentProbeError()
    return pages


def validate_image(data):
    result = _run('validate_image', [data])
    if result.get('format') not in ('PNG', 'JPEG', 'TIFF'):
        raise DocumentProbeError()
    return result['format']


def image_preview(data):
    return _run('image_preview', [data])


def image_pdf(data):
    return _run('image_pdf', [data])


def extract_xml_text(data):
    result = _run('xml_text', [data])
    text = result.get('text')
    if not isinstance(text, str) or len(text) > 24000:
        raise DocumentProbeError()
    return text


def merge_pdfs(documents):
    return _run('merge_pdf', documents)


def _read_request(directory):
    request_path = directory / 'request.json'
    if request_path.is_symlink():
        raise ValueError()
    raw = request_path.read_bytes()
    if not raw or len(raw) > 8192:
        raise ValueError()
    request = json.loads(raw)
    action = request.get('action')
    count = request.get('input_count')
    if action not in WALL_TIMEOUT or not isinstance(count, int) or not 0 < count <= 100:
        raise ValueError()
    documents = []
    for index in range(count):
        path = directory / f'input-{index}.bin'
        if path.is_symlink():
            raise ValueError()
        with path.open('rb') as handle:
            data = handle.read(MAX_DOCUMENT_BYTES + 1)
        if len(data) > MAX_DOCUMENT_BYTES:
            raise ValueError()
        documents.append(data)
    if sum(map(len, documents)) > MAX_BATCH_BYTES:
        raise ValueError()
    return request, documents


def _pdf_reader(data):
    import io
    from pypdf import PdfReader
    if not data.startswith(b'%PDF-'):
        raise DocumentProbeError()
    reader = PdfReader(io.BytesIO(data), strict=False)
    if reader.is_encrypted:
        raise DocumentProbeError('encrypted')
    return reader


def _open_image(data):
    import io
    from PIL import Image
    picture = Image.open(io.BytesIO(data))
    if picture.format not in ('PNG', 'JPEG', 'TIFF') or picture.width * picture.height > 25_000_000:
        picture.close()
        raise DocumentProbeError('too_large')
    return picture


def _image_output(data, preview=False):
    import io
    from PIL import Image, ImageOps
    output = io.BytesIO()
    with _open_image(data) as source:
        if preview:
            source.thumbnail((2400, 2400))
            source.convert('RGB').save(output, format='PNG')
        else:
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.utils import ImageReader
            from reportlab.pdfgen.canvas import Canvas
            canvas = Canvas(output, pagesize=A4)
            pixels = 0
            frames = getattr(source, 'n_frames', 1)
            if not 0 < frames <= 100:
                raise DocumentProbeError('too_many_pages')
            for index in range(frames):
                source.seek(index)
                pixels += source.width * source.height
                if source.width * source.height > 25_000_000 or pixels > 100_000_000:
                    raise DocumentProbeError('too_large')
                picture = ImageOps.exif_transpose(source).convert('RGBA')
                background = Image.new('RGB', picture.size, 'white')
                background.paste(picture, mask=picture.getchannel('A'))
                width, height = A4
                scale = min((width - 48) / picture.width, (height - 48) / picture.height)
                draw_width, draw_height = picture.width * scale, picture.height * scale
                canvas.drawImage(ImageReader(background), (width - draw_width) / 2,
                                 (height - draw_height) / 2, draw_width, draw_height)
                canvas.showPage()
            canvas.save()
    result = output.getvalue()
    if len(result) > 32 * 1024 * 1024:
        raise DocumentProbeError('too_large')
    return result


def _isolation_check(request):
    """Probe capabilities against disposable markers, never application files."""
    import errno
    import socket
    outside = Path(request['forbidden_dir'])
    blocked = {}
    try:
        (outside / 'read-marker').read_bytes()
        blocked['filesystem_read_blocked'] = False
    except OSError as exc:
        blocked['filesystem_read_blocked'] = exc.errno in (errno.EPERM, errno.EACCES, errno.ENOENT)
    try:
        with (outside / 'write-marker').open('xb') as handle:
            handle.write(b'synthetic-isolation-check')
        blocked['filesystem_write_blocked'] = False
    except OSError as exc:
        blocked['filesystem_write_blocked'] = exc.errno in (errno.EPERM, errno.EACCES, errno.ENOENT)
    try:
        with socket.socket() as client:
            client.settimeout(0.2)
            client.connect(('127.0.0.1', request['host_port']))
        blocked['network_blocked'] = False
    except OSError as exc:
        # Linux's network namespace has no host loopback listener; Seatbelt
        # actively rejects connect. The declared backend is checked as well.
        blocked['network_blocked'] = (exc.errno in (errno.EPERM, errno.EACCES)
            or (sys.platform.startswith('linux') and exc.errno in (errno.ENETUNREACH, errno.ECONNREFUSED)))
    backend = os.environ.get('SEDO_PARSER_BACKEND', '')
    if os.name == 'posix':
        import resource
        bounded = (resource.getrlimit(resource.RLIMIT_CPU) == (8, 8)
                   and resource.getrlimit(resource.RLIMIT_FSIZE) == (MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES))
        if sys.platform != 'darwin':
            bounded = bounded and resource.getrlimit(resource.RLIMIT_AS) == (_memory_limit('isolation_check'),) * 2
    else:
        # This field is true only after successful native SetInformationJobObject
        # and AssignProcessToJobObject calls in this actual worker process.
        bounded = bool(globals().get('_JOB_HANDLE'))
    return {'ok': True, 'backend': backend,
            'resource_limits_applied': bounded,
            'filesystem_network_sandbox': backend in ('macos-seatbelt', 'linux-bubblewrap') and all(blocked.values()),
            **blocked}


def check_isolation():
    import socket
    with tempfile.TemporaryDirectory(prefix='sedo-protected-check-') as name:
        outside = Path(name)
        _private_write(outside / 'read-marker', b'synthetic-isolation-check')
        # A known listening endpoint distinguishes isolation from an arbitrary
        # unused port. No payload is sent and no external host is contacted.
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen(1)
            return _run('isolation_check', [b'check'], forbidden_dir=str(outside),
                        host_port=listener.getsockname()[1])


def _execute(request, documents, directory):
    action = request['action']
    if action == 'isolation_check':
        return _isolation_check(request)
    if action == 'table_rows':
        from sedo.import_probe import worker_rows
        return worker_rows(request, documents)
    if action == 'transcribe':
        from sedo.audio_probe import worker_transcribe
        return worker_transcribe(request, documents)
    if action == 'validate_pdf':
        maximum = request.get('max_pages', 500)
        if not isinstance(maximum, int) or not 0 < maximum <= 500:
            raise DocumentProbeError()
        pages = len(_pdf_reader(documents[0]).pages)
        if not pages:
            raise DocumentProbeError('empty')
        if pages > maximum:
            raise DocumentProbeError('too_many_pages')
        return {'ok': True, 'pages': pages}
    if action == 'validate_image':
        with _open_image(documents[0]) as picture:
            kind = picture.format
            picture.verify()
        return {'ok': True, 'format': kind}
    if action in ('image_preview', 'image_pdf'):
        _private_write(directory / 'output.bin', _image_output(documents[0], preview=action == 'image_preview'))
        return {'ok': True}
    if action == 'xml_text':
        from lxml import etree
        root = etree.fromstring(documents[0], etree.XMLParser(
            resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False))
        if root.getroottree().docinfo.doctype:
            raise DocumentProbeError('invalid_xml')
        return {'ok': True, 'text': etree.tostring(root, encoding='unicode')[:24000]}
    if action == 'classify':
        filename, subject = request.get('filename'), request.get('subject')
        if not isinstance(filename, str) or not isinstance(subject, str):
            raise DocumentProbeError()
        result = _classify_document(documents[0], filename, subject)
        return {'ok': True, 'kind': result.kind, 'reason': result.reason[:500]}
    if action == 'extract_text':
        reader = _pdf_reader(documents[0])
        if len(reader.pages) > 50:
            raise DocumentProbeError('too_many_pages')
        text = '\n'.join((page.extract_text() or '')[:12000] for page in reader.pages[:10])[:24000]
        if not text.strip():
            raise DocumentProbeError('empty')
        return {'ok': True, 'text': text}
    if action == 'merge_pdf':
        import io
        from pypdf import PdfWriter
        writer = PdfWriter()
        pages = 0
        for data in documents:
            reader = _pdf_reader(data)
            pages += len(reader.pages)
            if pages > 500:
                raise DocumentProbeError('too_many_pages')
            for page in reader.pages:
                for key in ('/AA', '/Annots'):
                    if key in page:
                        del page[key]
                writer.add_page(page)
        if not pages:
            raise DocumentProbeError('empty')
        output = io.BytesIO()
        writer.write(output)
        _private_write(directory / 'output.bin', output.getvalue())
        return {'ok': True}
    raise DocumentProbeError()


def worker_main(name):
    directory = Path(name)
    if directory.is_symlink() or not directory.is_dir():
        return 2
    response = {'ok': False, 'error': 'unsafe'}
    try:
        # The tiny header is bounded before selecting an operation's limits;
        # the document bytes and their parsers are not touched until afterwards.
        with (directory / 'request.json').open('rb') as handle:
            header = handle.read(8193)
        if len(header) > 8192:
            raise DocumentProbeError()
        request_header = json.loads(header)
        action = request_header.get('action')
        if action not in WALL_TIMEOUT:
            raise DocumentProbeError()
        _resource_limits(action)
        if sys.platform == 'darwin':
            _enter_macos_sandbox(directory,
                _model_read_paths(request_header) if action == 'transcribe' else ())
        for stream in ('stdout', 'stderr'):
            if getattr(sys, stream) is None:
                setattr(sys, stream, open(os.devnull, 'w'))
        # openpyxl and other libraries construct MimeTypes while importing.
        # Built-in mappings suffice; never inspect administrator/user MIME files.
        import mimetypes
        mimetypes.knownfiles = []
        mimetypes.init(files=[])
        request, documents = _read_request(directory)
        response = _execute(request, documents, directory)
    except DocumentProbeError as exc:
        response = {'ok': False, 'error': exc.code}
    except Exception:
        pass
    try:
        _private_write(directory / 'result.json', json.dumps(response, ensure_ascii=True).encode())
    except OSError:
        return 2
    return 0


if __name__ == '__main__':
    # Only the known application package is added after isolated Python startup.
    # The OS sandbox grants code reads but no application data directories.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(worker_main(sys.argv[1]) if len(sys.argv) == 2 else 2)
