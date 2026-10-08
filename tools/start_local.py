"""Hash-locked source installation and loopback-only production server."""
from pathlib import Path
import os
import sys

APP = Path(__file__).resolve().parents[1]


def main():
    import teststand
    teststand.validate_lock(APP)
    if sys.version_info[:3] != teststand.PYTHON:
        raise teststand.BuildError('Bitte Python 3.13.15 verwenden oder das geprüfte Desktop-Paket installieren.')
    if '--serve' not in sys.argv:
        digest = teststand.sha((APP / 'requirements-release.lock').read_bytes())
        target = APP / '.runtimes' / digest[:20]
        receipt = target / '.verified-lock'
        if not target.exists():
            python = teststand.install(target, APP)
            receipt.write_text(digest, encoding='ascii')
        elif not receipt.is_file() or receipt.read_text(encoding='ascii') != digest:
            raise teststand.BuildError('Unvollständige lokale Installation. Bitte den Support kontaktieren.')
        else:
            python = target / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        os.execv(str(python), [str(python), str(Path(__file__).resolve()), '--serve'])
    sys.path.insert(0, str(APP))
    os.environ.setdefault('SEDO_HOME', str(APP))
    os.environ['SEDO_LISTEN_HOST'] = '127.0.0.1'
    from desktop import _bootstrap
    app, first_password = _bootstrap()
    if first_password:
        print('Benutzer: admin\nEinmalig angezeigtes Passwort:', first_password, flush=True)
    from sedo.services.mail_worker import start_mail_worker
    from sedo.services.calendar_worker import start_calendar_worker
    start_mail_worker()
    start_calendar_worker()
    print('SEDO läuft ausschließlich auf diesem Rechner: http://127.0.0.1:8000', flush=True)
    from waitress import serve
    serve(app, host='127.0.0.1', port=8000, threads=6, max_request_body_size=32 * 1024 * 1024)


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, ValueError) as exc:
        print(f'Start abgebrochen: {exc}', file=sys.stderr)
        raise SystemExit(1)
