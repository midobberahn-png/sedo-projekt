"""Start an already installed Bonsai demo in the background, only on desktop boot.

Never install/download models, stop a shared server, or run from AppConfig/checks.
The vendor launchers choose hardware settings; SEDO forces a loopback bind.
"""
from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

from django.db import close_old_connections

from ..desktop_lock import acquire
from .ai_local import AIError, check_local_model, get_ai_config, validate_local_base_url

_guard = threading.Lock()
_worker = None
_message = 'Bonsai wurde in dieser SEDO-Sitzung noch nicht automatisch gestartet.'


def runtime_status() -> str:
    with _guard:
        return _message


def _set_status(message: str):
    global _message
    with _guard:
        _message = message


def validate_runtime_dir(value: str) -> str:
    value = value.strip()
    if not value:
        return ''
    if len(value) > 4096 or any(ord(c) < 32 for c in value) or not Path(value).expanduser().is_absolute():
        raise AIError('Bitte den vollständigen Pfad zum installierten Bonsai-demo-Ordner angeben.')
    return str(Path(value).expanduser())


def _launch_command(root: Path, windows: bool) -> list[str]:
    script = root / 'scripts' / ('start_llama_server.ps1' if windows else 'start_llama_server.sh')
    if not script.is_file():
        raise AIError('Bonsai-Startskript nicht gefunden. Bitte in den KI-Einstellungen den '
                      'installierten Bonsai-demo-Ordner auswählen. Es wurde nichts heruntergeladen.')
    if windows:
        powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        # Respect the machine's execution policy; do not silently bypass it.
        return [str(powershell), '-NoProfile', '-NonInteractive', '-File', str(script)]
    return ['/bin/sh', str(script)]


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _start(cfg: dict, wait_seconds=180):
    url = urlsplit(validate_local_base_url(cfg['base_url']))
    host, port = url.hostname, url.port or (443 if url.scheme == 'https' else 80)
    lock = None
    process = None
    try:
        # Shared across SEDO data directories, unlike the desktop data lock.
        # Hold until readiness or timeout so simultaneous starts cannot race.
        try:
            lock = acquire(Path.home() / '.sedo' / 'bonsai-start' / f'{host.replace(":", "_")}-{port}')
        except RuntimeError:
            _set_status('Bonsai wird bereits von einer anderen SEDO-Sitzung gestartet. Bitte kurz warten.')
            return
        if not _port_open(host, port):
            if url.scheme != 'http' or (os.name == 'nt' and port != 8080):
                raise AIError('Der Bonsai-Start benötigt lokales HTTP; auf Windows Port 8080. '
                              'Für eine andere Adresse bitte Bonsai separat starten.')
            folder = validate_runtime_dir(cfg['runtime_dir'])
            root = Path(folder) if folder else Path.home() / 'Documents' / 'Bonsai-demo'
            command = _launch_command(root, windows=os.name == 'nt')
            env = os.environ.copy()
            # Do not give the child access to SEDO credentials/settings.
            for key in list(env):
                if key.startswith(('SEDO_', 'DJANGO_')):
                    env.pop(key)
            for key in ('LD_LIBRARY_PATH', 'DYLD_LIBRARY_PATH'):
                original = env.pop(key + '_ORIG', None)
                if original is not None:
                    env[key] = original
                elif getattr(sys, 'frozen', False):
                    env.pop(key, None)
            env.update(BONSAI_HOST=host, PORT=str(port))
            options = {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
            _set_status('Bonsai startet und lädt das Modell. Du kannst SEDO währenddessen weiter benutzen.')
            process = subprocess.Popen(command, cwd=root, env=env, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options)
        else:
            _set_status('Ein lokaler Server läuft bereits. SEDO wartet auf das geladene Modell.')
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            try:
                check_local_model(cfg)
                _set_status('Bonsai war beim letzten Startcheck bereit. Für den aktuellen Status „Verbindung prüfen“ verwenden.')
                return
            except AIError:
                if process is not None and process.poll() is not None:
                    raise AIError('Bonsai konnte nicht gestartet werden. Bitte den Bonsai-Ordner '
                                  'und dessen Startskript prüfen oder Bonsai einmal separat starten.') from None
                time.sleep(1)
        raise AIError('Bonsai ist noch nicht bereit. Bitte später die Verbindung prüfen. '
                      'Es wurde kein weiterer Server gestartet und kein Cloud-Dienst verwendet.')
    finally:
        if lock is not None:
            lock.close()


def start_bonsai_background(*, manual=False) -> bool:
    """Return immediately; only desktop.main and the explicit admin action call this."""
    global _worker, _message
    try:
        cfg = get_ai_config()
    except Exception:
        _set_status('Bonsai-Einstellungen konnten nicht gelesen werden. SEDO bleibt ohne KI bedienbar.')
        return False
    if not cfg['enabled'] or (not manual and not cfg['autostart']):
        return False
    with _guard:
        if _worker is not None and _worker.is_alive():
            return False
        _message = 'Bonsai-Start wird im Hintergrund geprüft.'

        def run():
            close_old_connections()
            try:
                _start(cfg)
            except AIError as exc:
                _set_status(str(exc))
            except Exception:
                _set_status('Bonsai konnte nicht gestartet werden. Bitte Installation und Zugriffsrechte prüfen.')
            finally:
                close_old_connections()

        _worker = threading.Thread(target=run, name='sedo-bonsai-start', daemon=True)
        _worker.start()
        return True
