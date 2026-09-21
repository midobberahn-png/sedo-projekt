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
    if getattr(sys, 'frozen', False):
        os.environ.setdefault('SEDO_HOME', str(_user_home()))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    # Whisper-Modell (~460 MB, lädt beim ersten Diktat) in den SEDO-Datenordner
    # statt in versteckte Cache-Pfade – so wandert es bei Backups/Umzügen mit.
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
        kwargs=dict(app=app, host='127.0.0.1', port=port, threads=6, _quiet=True),
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
    from sedo.services.bonsai_runtime import start_bonsai_background
    start_bonsai_background()
    port = _free_port()
    _serve_in_thread(app, port)
    if not _wait_until_up(port):
        print('SEDO-Server ist nicht gestartet – Details ggf. in den Logs.', file=sys.stderr)
        return 1

    import webview

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

    window = webview.create_window(
        APP_TITLE,
        f'http://127.0.0.1:{port}/',
        width=geometry['width'], height=geometry['height'],
        x=geometry['x'], y=geometry['y'], min_size=(1100, 700),
    )

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
            window.evaluate_js(
                'alert("SEDO Rechnungstool v2.3\\n'
                'Lokales Rechnungs- und Abrechnungswerkzeug\\n'
                'SEDO Objektdienstleistungen GmbH")')
        except Exception:
            pass

    menu = [
        wm.Menu('Ablage', [
            wm.MenuAction('Backup & Sicherung', open_backup),
            wm.MenuSeparator(),
            wm.MenuAction('SEDO beenden', lambda: window.destroy()),
        ]),
        wm.Menu('Hilfe', [
            wm.MenuAction('Über SEDO', show_about),
        ]),
    ]

    webview.start(menu=menu)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
