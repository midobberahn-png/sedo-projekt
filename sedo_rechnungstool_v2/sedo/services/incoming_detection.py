"""Bounded process boundary for every untrusted incoming document.

Private files, not console pipes, also work with Windows windowed executables.
A timeout/crash/limit always means unknown, never automatic approval. The
standalone text/XML functions remain available for focused unit tests.
"""
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading

from ..document_probe import (Detection, classify_text, classify_xml, normalized,
    INVOICE, OTHER, INVOICE_WORD, OTHER_WORD, TITLE, NUMBER, AMOUNT, INVOICE_CODES,
    MAX_DOCUMENT_BYTES)

_SLOTS = threading.BoundedSemaphore(2)
WALL_TIMEOUT = 5


def _run(data, filename, subject, directory):
    header = json.dumps({'filename': filename, 'subject': subject}, ensure_ascii=True).encode()
    if len(header) > 8192:
        raise ValueError('Header too large')
    request_path = directory / 'request.bin'
    with request_path.open('xb') as handle:
        os.chmod(request_path, 0o600)
        handle.write(struct.pack('!I', len(header)))
        handle.write(header)
        handle.write(data)
    if getattr(sys, 'frozen', False):
        command = [sys.executable, '--sedo-document-probe', str(directory)]
    else:
        command = [sys.executable, str(Path(__file__).resolve().parents[1] / 'document_probe.py'), str(directory)]
    # Keep interpreter/runtime paths, never SEDO/SMTP/FinTS/AI credentials.
    allowed = {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'TMPDIR', 'LANG', 'LC_ALL', 'PYTHONPATH', 'PYTHONHOME'}
    env = {k: v for k, v in os.environ.items() if k in allowed}
    env['PYTHONIOENCODING'] = 'utf-8'
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, env=env, close_fds=True,
        **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
    try:
        process.wait(timeout=WALL_TIMEOUT)
        if process.returncode != 0:
            raise ValueError('Worker failed')
        result_path = directory / 'result.json'
        if result_path.is_symlink():
            raise ValueError('Invalid worker result')
        with result_path.open('rb') as handle:
            output = handle.read(4097)
        if len(output) > 4096:
            raise ValueError('Worker result too large')
        result = json.loads(output)
        if (not isinstance(result, dict) or result.get('kind') not in ('invoice', 'other', 'unknown')
                or not isinstance(result.get('reason'), str) or len(result['reason']) > 500):
            raise ValueError('Invalid worker result')
        return Detection(result['kind'], result['reason'])
    finally:
        if process.poll() is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            process.wait(timeout=2)


def classify_document(data, filename, subject=''):
    unknown = Detection('unknown', 'Dokument konnte innerhalb der Sicherheitsgrenzen nicht geprüft werden')
    if not isinstance(data, bytes) or not isinstance(filename, str) or not isinstance(subject, str):
        return unknown
    if len(data) > MAX_DOCUMENT_BYTES or len(filename) > 240 or len(subject) > 2000:
        return unknown
    if not _SLOTS.acquire(blocking=False):
        return Detection('unknown', 'Dokumentprüfung ausgelastet; bitte manuell prüfen')
    try:
        with tempfile.TemporaryDirectory(prefix='sedo-document-') as directory:
            return _run(data, filename, subject, Path(directory))
    except (OSError, ValueError, subprocess.TimeoutExpired, TypeError):
        return unknown
    finally:
        _SLOTS.release()
