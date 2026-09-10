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
import desktop


def check() -> int:
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
    hardlinks = False
    original = home/'hardlink-probe'
    original.write_bytes(b'isolierter Buildtest')
    try:
        linked = home/'hardlink-probe.link'
        os.link(original, linked)
        hardlinks = linked.samefile(original)
    except OSError:
        pass
    result = {'ok': bool(http and assets and whisper and migrated and hardlinks),
              'http_login': http, 'static_assets': assets, 'whisper_available': whisper,
              'migrations_complete': migrated, 'hardlinks': hardlinks,
              'frozen': bool(getattr(sys, 'frozen', False)),
              'python': '.'.join(map(str, sys.version_info[:3]))}
    report = os.environ.get('SEDO_CHECK_REPORT')
    if report:
        Path(report).write_text(json.dumps(result, indent=2), encoding='utf-8')
    # Nur Testdaten im temporären Ordner; der Prozess endet samt Server-Thread.
    # Keine GUI-, Mikrofon- oder Zielrechnerabnahme daraus ableiten.
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(check() if '--check-strict' in sys.argv else desktop.main())
