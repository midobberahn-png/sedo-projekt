"""H2: Desktop-Einstieg mit strengem, isoliertem Build-Selbsttest.

Normale Starts delegieren unverändert an desktop.main. Kein Audio-/Modell-Upload,
kein Abruf eines Whisper-Modells und keine vorhandene Anwendungsdatenbank.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import urllib.request

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def check() -> int:
    import desktop
    home = Path(tempfile.mkdtemp(prefix='sedo_build_check_'))
    os.environ['SEDO_HOME'] = str(home)
    app, _ = desktop._bootstrap()
    port = desktop._free_port()
    desktop._serve_in_thread(app, port)
    http = desktop._wait_until_up(port, timeout=30)
    assets = False
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/static/sedo/app.css', timeout=10) as response:
            assets = response.status == 200 and bool(response.read())
        for name in ('dashboard-widgets.css', 'dashboard-widgets.js', 'calendar.css', 'quiet-ui.css', 'quiet-ui.js', 'mail.js', 'accessibility-v2.js'):
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/static/sedo/{name}', timeout=10) as response:
                assets = assets and response.status == 200 and bool(response.read())
    except OSError:
        assets = False
    from sedo.services import stt_status
    whisper = bool(stt_status().get('available'))
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    executor = MigrationExecutor(connection)
    migrated = not executor.migration_plan(executor.loader.graph.leaf_nodes())
    # Prove the frozen package can encrypt credentials and load its CA bundle.
    # No real account and no connection to an external mailserver are involved.
    import ssl
    from sedo.models import MailAccount
    from sedo.services.mail_secrets import encrypt_password, decrypt_password
    from sedo.services.mail_imap import tls_context
    probe = MailAccount(email='build@example.invalid', username='build@example.invalid')
    probe.password_encrypted = encrypt_password(probe, 'isolierter-postfach-test')
    tls = tls_context()
    mail_available = (decrypt_password(probe) == 'isolierter-postfach-test'
                      and tls.check_hostname and tls.verify_mode == ssl.CERT_REQUIRED
                      and bool(tls.get_ca_certs()))
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/static/sedo/mail.css', timeout=10) as response:
            mail_available = mail_available and response.status == 200 and bool(response.read())
    except OSError:
        mail_available = False
    from datetime import date
    from types import SimpleNamespace
    from sedo.models import CalendarConnection
    from sedo.services.calendar_sync import encrypt, client_for
    from sedo.services.calendar_data import fields, occurrences, aware
    calendar_probe = CalendarConnection(user_id=1, username='calendar@example.invalid')
    calendar_probe.password_encrypted = encrypt(calendar_probe, 'isolierter-kalender-test')
    raw_calendar = ('BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:build-check\r\n'
                    'DTSTART:20260923T080000Z\r\nDTEND:20260923T090000Z\r\n'
                    'SUMMARY:Buildtest\r\nRRULE:FREQ=DAILY;COUNT=2\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n')
    calendar_values = fields(raw_calendar)
    calendar_rows, calendar_errors = occurrences(
        [SimpleNamespace(raw_ical=raw_calendar, recurring=True, title='Buildtest', pk=0)],
        aware(date(2026, 9, 23)), aware(date(2026, 9, 26)))
    calendar_available = bool(client_for(calendar_probe).authorization and calendar_values['recurring']
                              and len(calendar_rows) == 2 and not calendar_errors)
    # Die ausgelieferte App muss die Prüfschemata tatsächlich öffnen können.
    from sedo.services.zugferd_validation import _official_rules, _profile_schema
    validator = sum(len(assertions) for _, assertions in _official_rules()) == 806
    validator = validator and _profile_schema('extended') is not None and _profile_schema('en16931') is not None
    from sedo.backup_utils import create_backup, inspect_backup
    backup = create_backup('build_check')
    restoration = inspect_backup(backup.name)['integrity'] == 'ok'
    hardlinks = False
    original = home/'hardlink-probe'
    original.write_bytes(b'isolierter Buildtest')
    try:
        linked = home/'hardlink-probe.link'
        os.link(original, linked)
        hardlinks = linked.samefile(original)
    except OSError:
        pass
    result = {'ok': bool(http and assets and whisper and migrated and hardlinks and validator and restoration and mail_available and calendar_available),
              'http_login': http, 'static_assets': assets, 'whisper_available': whisper,
              'migrations_complete': migrated, 'hardlinks': hardlinks,
              'invoice_validator_available': validator, 'restore_simulation': restoration,
              'mail_available': bool(mail_available),
              'calendar_available': calendar_available,
              'frozen': bool(getattr(sys, 'frozen', False)),
              'python': '.'.join(map(str, sys.version_info[:3]))}
    report = os.environ.get('SEDO_CHECK_REPORT')
    if report:
        Path(report).write_text(json.dumps(result, indent=2), encoding='utf-8')
    # Nur Testdaten im temporären Ordner; der Prozess endet samt Server-Thread.
    # Keine GUI-, Mikrofon- oder Zielrechnerabnahme daraus ableiten.
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--check-restore':
        from sedo.restore_check import main
        raise SystemExit(main(sys.argv[2]))
    if '--check-strict' in sys.argv:
        raise SystemExit(check())
    import desktop
    raise SystemExit(desktop.main())
