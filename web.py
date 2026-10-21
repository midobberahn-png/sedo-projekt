"""Run the one-company SEDO web service behind a same-host HTTPS proxy.

    python web.py prepare
    python web.py serve --port 8766

Only one process may own SEDO_HOME. The public proxy must terminate TLS,
overwrite X-Forwarded-For and forward only HTTPS requests to loopback.
"""
import argparse
import os
from pathlib import Path


def _settings():
    os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings_web'
    from django.conf import settings
    return settings


def _lock(home):
    from sedo.desktop_lock import acquire
    return acquire(home)


def _prepare(home):
    import django
    from django.core.management import call_command

    with _lock(home):
        django.setup()
        for name in ('data', 'media', 'logs'):
            (home / name).mkdir(parents=True, exist_ok=True, mode=0o700)
        call_command('check', deploy=True)
        call_command('migrate', interactive=False, verbosity=0)
        call_command('collectstatic', interactive=False, verbosity=0)
        # Printed only in this interactive preparation step, never in service logs.
        call_command('ensure_admin')


def _serve(home, port):
    import django
    from django.core.management import call_command
    from waitress import serve

    with _lock(home):
        # A staged restore replaces SQLite/media only while the web server is
        # stopped and its process lock is exclusively held.
        from sedo.restore_bootstrap import apply_pending_restore
        apply_pending_restore(home)
        django.setup()
        call_command('migrate', interactive=False, verbosity=0)
        call_command('collectstatic', interactive=False, verbosity=0)
        from django.conf import settings
        if not (Path(settings.STATIC_ROOT) / 'admin' / 'css' / 'base.css').is_file():
            raise RuntimeError('Statische Dateien fehlen. Zuerst python web.py prepare ausführen.')
        from django.contrib.auth import get_user_model
        if not get_user_model().objects.filter(is_active=True, is_staff=True).exists():
            raise RuntimeError('Administrationskonto fehlt. Zuerst python web.py prepare ausführen.')
        from config.wsgi import application
        from sedo.services.mail_worker import start_mail_worker
        from sedo.services.calendar_worker import start_calendar_worker
        start_mail_worker()
        start_calendar_worker()
        serve(application, host='127.0.0.1', port=port, threads=4,
              url_scheme='https', trusted_proxy='127.0.0.1',
              trusted_proxy_count=1, trusted_proxy_headers={'x-forwarded-for'},
              clear_untrusted_proxy_headers=True,
              max_request_body_size=32 * 1024 * 1024)


def main(argv=None):
    parser = argparse.ArgumentParser(description='SEDO-Webdienst auf dem lokalen Proxy-Port')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('prepare', help='Migrationen, statische Dateien und Admin-Konto vorbereiten')
    server = commands.add_parser('serve', help='Webdienst nur auf 127.0.0.1 starten')
    server.add_argument('--port', type=int, default=8766)
    args = parser.parse_args(argv)
    settings = _settings()
    home = Path(settings.SEDO_HOME)
    if args.command == 'prepare':
        _prepare(home)
        return 0
    if not 1 <= args.port <= 65535:
        parser.error('Port muss zwischen 1 und 65535 liegen.')
    _serve(home, args.port)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
