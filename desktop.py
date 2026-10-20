"""SEDO als richtiges Desktop-Programm (v2.2).

Startet den lokalen Django-Server in einem Thread (waitress) und öffnet die
Oberfläche in einem NATIVEN Programmfenster (pywebview → WKWebView auf macOS,
WebView2 auf Windows) – ganz ohne Browser. Gedacht für das per PyInstaller
gebündelte Komplettpaket; funktioniert aber auch direkt aus der venv:

    python desktop.py            # Programmfenster
    python desktop.py --check    # Headless-Selbsttest (CI/Build-Verifikation)

Im gebündelten Betrieb (sys.frozen) liegen alle SCHREIBENDEN Daten unter
SEDO_HOME (macOS: ~/Library/Application Support/SEDO, Windows: %APPDATA%/SEDO)
– die App selbst bleibt schreibgeschützt. Beim allerersten Start wird das
Admin-Zufallspasswort einmalig im Fenster angezeigt.
"""
import os
import socket
import sys
import threading
import time
from pathlib import Path

APP_TITLE = 'SEDO Rechnungstool'
_DATA_LOCKS = {}


def _user_home() -> Path:
    """Schreibbarer Datenordner für das gebündelte Programm."""
    if sys.platform == 'darwin':
        base = Path.home() / 'Library' / 'Application Support' / 'SEDO'
    elif os.name == 'nt':
        base = Path(os.environ.get('APPDATA', str(Path.home()))) / 'SEDO'
    else:
        base = Path.home() / '.sedo'
    return base


def _free_port(preferred: int = 8765) -> int:
    """Bevorzugter Port, sonst ein freier Ephemeral-Port (kein Konflikt mit
    dem klassischen Starter auf 8000)."""
    for port in (preferred, 0):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('127.0.0.1', port))
                return s.getsockname()[1]
        except OSError:
            continue
    return 0


def _bootstrap() -> tuple[object, str | None]:
    """Django initialisieren: Datenordner, Migrationen, Admin-Konto.

    Gibt (WSGI-App, einmalig_angezeigtes_passwort_oder_None) zurück.
    """
    os.environ['SEDO_LISTEN_HOST'] = '127.0.0.1'
    if getattr(sys, 'frozen', False):
        os.environ.setdefault('SEDO_HOME', str(_user_home()))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    # Hilfscaches liegen im SEDO-Datenordner. Modelle werden separat unter
    # whisper-models an feste Revisionen gebunden; keine Geschäftsdatensicherung.
    home = os.environ.setdefault('SEDO_HOME', str(Path(__file__).resolve().parent))
    if home:
        from sedo.desktop_lock import acquire
        canonical_home = str(Path(home).resolve())
        if canonical_home not in _DATA_LOCKS:
            _DATA_LOCKS[canonical_home] = acquire(home)
        os.environ.setdefault('HF_HOME', str(Path(home) / 'whisper-cache'))

        # Eine Wiederherstellung wird bewusst vor Django und vor Migrationen
        # angewendet. So gibt es keine offenen DB-Verbindungen und die App kann
        # anschließend ältere Sicherungen kontrolliert auf den aktuellen Stand
        # migrieren.
        from sedo.restore_bootstrap import apply_pending_restore
        apply_pending_restore(Path(home))

    import django
    django.setup()
    from django.conf import settings

    for sub in ('data', 'media', 'logs'):
        (Path(settings.SEDO_HOME) / sub).mkdir(parents=True, exist_ok=True)

    from io import StringIO
    from django.core.management import call_command
    call_command('migrate', interactive=False, verbosity=0)
    from sedo.services.credentials import migrate_legacy_credentials
    migrate_legacy_credentials()
    out = StringIO()
    call_command('ensure_admin', stdout=out)
    first_password = None
    for line in out.getvalue().splitlines():
        if 'Einmalig angezeigtes Passwort:' in line:
            first_password = line.split(':', 1)[1].strip()
            break

    from django.core.wsgi import get_wsgi_application
    return get_wsgi_application(), first_password


def _serve_in_thread(app, port: int) -> threading.Thread:
    from waitress import serve
    thread = threading.Thread(
        target=serve,
        kwargs=dict(app=app, host='127.0.0.1', port=port, threads=6, _quiet=True,
                    max_request_body_size=32 * 1024 * 1024),
        daemon=True,   # Fenster zu → Prozess (und damit der Server) endet
    )
    thread.start()
    return thread


def _wait_until_up(port: int, timeout: float = 20.0) -> bool:
    import urllib.request
    deadline = time.monotonic() + timeout
    url = f'http://127.0.0.1:{port}/login/'
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except OSError:
            time.sleep(0.25)
    return False


def _self_check() -> int:
    """Headless-Selbsttest für den Build: booten, migrieren, eine Seite laden."""
    import tempfile
    os.environ['SEDO_HOME'] = tempfile.mkdtemp(prefix='sedo_check_')
    app, _ = _bootstrap()
    port = _free_port()
    _serve_in_thread(app, port)
    ok = _wait_until_up(port, timeout=30)
    from sedo.services import stt_status
    stt = stt_status().get('available', False)
    print('SELF-CHECK', 'OK' if ok else 'FEHLGESCHLAGEN',
          f'(Port {port}, Whisper-Diktat: {"verfügbar" if stt else "NICHT verfügbar"})')
    return 0 if ok else 1


def main() -> int:
    if '--check' in sys.argv:
        return _self_check()

    app, first_password = _bootstrap()
    from sedo.services.mail_worker import start_mail_worker
    start_mail_worker()
    from sedo.services.calendar_worker import start_calendar_worker
    start_calendar_worker()
    from sedo.services.bonsai_runtime import start_bonsai_background
    start_bonsai_background()
    port = _free_port()
    _serve_in_thread(app, port)
    if not _wait_until_up(port):
        print('SEDO-Server ist nicht gestartet – Details ggf. in den Logs.', file=sys.stderr)
        return 1

    import webview
    # Explicit downloads use the native save dialog; document previews stay in SEDO.
    webview.settings['ALLOW_DOWNLOADS'] = True

    # Fensterposition/-größe der letzten Sitzung wiederherstellen
    import json
    state_file = Path(os.environ.get('SEDO_HOME', '.')) / 'window.json'
    geometry = {'width': 1440, 'height': 920, 'x': None, 'y': None}
    try:
        saved = json.loads(state_file.read_text(encoding='utf-8'))
        for key in geometry:
            if isinstance(saved.get(key), int):
                geometry[key] = saved[key]
    except OSError:
        pass
    except ValueError:
        pass

    from sedo.desktop_print import IncomingPrintAPI
    print_api = IncomingPrintAPI(f'http://127.0.0.1:{port}')
    window = webview.create_window(
        APP_TITLE,
        f'http://127.0.0.1:{port}/',
        width=geometry['width'], height=geometry['height'],
        x=geometry['x'], y=geometry['y'], min_size=(1100, 700), js_api=print_api,
    )
    print_api._window = window

    def save_geometry():
        try:
            state_file.write_text(json.dumps({
                'width': window.width, 'height': window.height,
                'x': window.x, 'y': window.y,
            }), encoding='utf-8')
        except Exception:
            pass

    def tame_webview():
        # Rechtsklick-Menü des Webviews nur in Eingabefeldern zulassen –
        # sonst fühlt es sich nach Webseite statt Programm an.
        window.evaluate_js("""
            document.addEventListener('contextmenu', function (e) {
                var t = e.target;
                var editable = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable);
                if (!editable) e.preventDefault();
            });
        """)

    window.events.closing += save_geometry
    window.events.loaded += tame_webview

    if first_password:
        shown = {'done': False}

        def show_password():
            # loaded feuert bei JEDER Seitennavigation – Dialog nur einmal zeigen.
            if shown['done']:
                return
            shown['done'] = True
            msg = ('Dein SEDO-Zugang wurde angelegt.\\n\\n'
                   'Benutzer: admin\\n'
                   f'Passwort: {first_password}\\n\\n'
                   'Bitte JETZT notieren – es wird nur dieses eine Mal angezeigt.')
            try:
                window.evaluate_js(f'alert("{msg}")')
            except Exception:
                print('Erststart-Passwort für admin:', first_password)

        window.events.loaded += show_password

    import webview.menu as wm

    def open_backup():
        window.load_url(f'http://127.0.0.1:{port}/backup/')

    def show_about():
        try:
            version = ''
            if os.name == 'nt':
                from sedo.windows_updates import WINDOWS_VERSION
                version = f' – Version {WINDOWS_VERSION}'
            window.evaluate_js(
                f'alert("SEDO Rechnungstool{version} – Veröffentlichungskandidat\\n'
                'Lokales Rechnungs- und Abrechnungswerkzeug\\n'
                'Verarbeitung auf diesem Rechner")')
        except Exception:
            pass

    update_actions = []
    update_lock = threading.Lock()
    if os.name == 'nt' and getattr(sys, 'frozen', False):
        def check_windows_update():
            if not update_lock.acquire(blocking=False):
                return

            def dialog(message: str, *, confirm: bool = False):
                import json
                function = 'confirm' if confirm else 'alert'
                return window.evaluate_js(f'{function}({json.dumps(message, ensure_ascii=False)})')

            def notice(message: str):
                import json
                try:
                    window.run_js('if (window.showToast) window.showToast('
                                  + json.dumps(message, ensure_ascii=False) + ', "info");')
                except Exception:
                    pass  # Noch nicht geladene Seite darf die Prüfung nicht verhindern.

            def work():
                try:
                    from sedo import windows_updates
                    from sedo.backup_utils import create_backup, inspect_backup
                    notice('SEDO prüft den Windows-Updatekanal …')
                    update = windows_updates.check()
                    if update is None:
                        dialog('SEDO ist auf diesem Rechner aktuell.')
                        return
                    approved = dialog(
                        f'Windows-Update {update.version} installieren?\n\n'
                        'Bitte offene Eingaben vorher speichern. SEDO erstellt '
                        'eine Sicherung, lädt das geprüfte Update und startet danach neu.',
                        confirm=True)
                    if not approved:
                        return
                    notice('SEDO erstellt und prüft die Datensicherung …')
                    backup = create_backup('pre_windows_update')
                    if inspect_backup(backup.name).get('integrity') != 'ok':
                        raise windows_updates.UpdateError('Die Datensicherung konnte nicht geprüft werden.')
                    notice('SEDO lädt das geprüfte Windows-Update …')
                    home = Path(os.environ['SEDO_HOME'])
                    installer = windows_updates.download(update, home / 'updates')
                    log = home / 'logs' / f'windows-update-{update.version}.log'
                    windows_updates.start_installer(installer, Path(sys.executable), log)
                    window.destroy()
                except Exception as exc:
                    from sedo.windows_updates import UpdateError
                    message = str(exc) if isinstance(exc, UpdateError) else (
                        'Das Update konnte nicht vorbereitet werden. SEDO bleibt geöffnet; '
                        'bitte Verbindung und freien Speicher prüfen.')
                    try:
                        dialog(message)
                    except Exception:
                        print('Windows-Update:', message, file=sys.stderr)
                finally:
                    update_lock.release()

            threading.Thread(target=work, name='sedo-windows-update', daemon=True).start()

        update_actions = [wm.MenuSeparator(), wm.MenuAction('Auf Windows-Updates prüfen', check_windows_update)]

    menu = [
        wm.Menu('Ablage', [
            wm.MenuAction('Backup & Sicherung', open_backup),
            wm.MenuSeparator(),
            wm.MenuAction('SEDO beenden', lambda: window.destroy()),
        ]),
        wm.Menu('Hilfe', [
            wm.MenuAction('Über SEDO', show_about),
            *update_actions,
        ]),
    ]

    webview.start(menu=menu)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
