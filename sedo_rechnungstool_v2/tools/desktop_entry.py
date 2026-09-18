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
    except OSError:
        pass
    from sedo.services import stt_status
    whisper = bool(stt_status().get('available'))
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    executor = MigrationExecutor(connection)
    migrated = not executor.migration_plan(executor.loader.graph.leaf_nodes())
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
    result = {'ok': bool(http and assets and whisper and migrated and hardlinks and validator and restoration),
              'http_login': http, 'static_assets': assets, 'whisper_available': whisper,
              'migrations_complete': migrated, 'hardlinks': hardlinks,
              'invoice_validator_available': validator, 'restore_simulation': restoration,
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
