"""Kontrollierter Dateitausch vor Django; mit dauerhaftem Rückweg bei Abbruch."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import uuid


def _sha256(path: Path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _tree_sha256(root: Path):
    digest = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise RuntimeError('Verknüpfungen sind im Wiederherstellungsordner nicht erlaubt.')
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode('utf-8'))
            digest.update(b'\0')
            digest.update(_sha256(path).encode('ascii'))
            digest.update(b'\n')
    return digest.hexdigest()


def _write_json(path, data):
    temp = path.with_suffix('.tmp')
    with temp.open('w', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def _recover(home, journal):
    """Nur bereits gesicherte Ziele ersetzen; unberührte Originale nie löschen."""
    rollback = home / journal['rollback']
    if not rollback.resolve().is_relative_to((home / 'restore-rollback').resolve()):
        raise RuntimeError('Ungültiger Rückweg im Wiederherstellungsprotokoll.')
    for name, existed in reversed(list(journal['originals'].items())):
        if name not in ('data/sedo_web.sqlite3', 'data/sedo_web.sqlite3-wal',
                        'data/sedo_web.sqlite3-shm', 'data/sedo_web.sqlite3-journal', 'media'):
            raise RuntimeError('Ungültiges Ziel im Wiederherstellungsprotokoll.')
        saved, live = rollback / name, home / name
        if saved.exists():
            if live.is_dir():
                shutil.rmtree(live)
            elif live.exists():
                live.unlink()
            live.parent.mkdir(parents=True, exist_ok=True)
            os.replace(saved, live)
        elif not existed and live.exists():
            if live.is_dir():
                shutil.rmtree(live)
            else:
                live.unlink()


def apply_pending_restore(home: Path) -> dict | None:
    home = Path(home).resolve()
    plan_path = home / 'pending_restore.json'
    journal_path = home / 'restore-transaction.json'
    if journal_path.exists():
        journal = json.loads(journal_path.read_text(encoding='utf-8'))
        if not journal.get('committed'):
            _recover(home, journal)
        else:
            _write_json(home / 'last_restore.json', journal['result'])
            plan_path.unlink(missing_ok=True)
        journal_path.unlink()
    if not plan_path.is_file():
        return None
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    staging = Path(plan['staging']).resolve()
    allowed = (home / 'restore-staging').resolve()
    if staging == allowed or not staging.is_relative_to(allowed):
        raise RuntimeError('Wiederherstellungsplan verweist außerhalb des Staging-Ordners.')
    staged_db = staging / 'data/sedo_web.sqlite3'
    if not staged_db.is_file() or _sha256(staged_db) != plan['database_sha256']:
        raise RuntimeError('Die vorbereitete Datenbank fehlt oder ihre Prüfsumme stimmt nicht.')
    tree = _tree_sha256(staging)
    if not plan.get('staging_sha256') or tree != plan['staging_sha256']:
        raise RuntimeError('Vorbereitete Wiederherstellungsdateien wurden nach der Prüfung verändert.')
    # Connection.__exit__ schließt die Datei nicht: unter Windows zwingend vor Tausch.
    with closing(sqlite3.connect(staged_db.as_uri() + '?mode=ro', uri=True)) as con:
        if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Die vorbereitete Datenbank besteht den Integritätstest nicht.')

    identity = datetime.now().strftime('%Y-%m-%d_%H-%M-%S') + '-' + uuid.uuid4().hex[:8]
    rollback = home / 'restore-rollback' / identity
    rollback.mkdir(parents=True, mode=0o700)
    # Staging bleibt bis zum erfolgreichen Abschluss unverändert und wiederholbar.
    incoming = rollback / 'incoming'
    shutil.copytree(staging, incoming)
    names = ('data/sedo_web.sqlite3', 'data/sedo_web.sqlite3-wal',
             'data/sedo_web.sqlite3-shm', 'data/sedo_web.sqlite3-journal', 'media')
    journal = {'rollback': str(rollback.relative_to(home)),
               'originals': {name: (home / name).exists() for name in names}}
    _write_json(journal_path, journal)
    try:
        for name in names:
            live, saved = home / name, rollback / name
            saved.parent.mkdir(parents=True, exist_ok=True)
            live.parent.mkdir(parents=True, exist_ok=True)
            if journal['originals'][name]:
                os.replace(live, saved)
        os.replace(incoming / 'data/sedo_web.sqlite3', home / 'data/sedo_web.sqlite3')
        (home / 'data/sedo_web.sqlite3').chmod(0o600)
        if (incoming / 'media').exists():
            os.replace(incoming / 'media', home / 'media')
        else:
            (home / 'media').mkdir()
        result = {'ok': True, 'applied_at': datetime.now().isoformat(),
                  'backup_name': plan['backup_name'], 'rollback': str(rollback)}
        _write_json(journal_path, {**journal, 'committed': True, 'result': result})
    except BaseException:
        _recover(home, journal)
        journal_path.unlink(missing_ok=True)
        raise
    # Nach dem Commit bleibt ein Abbruch beim Aufräumen beim nächsten Start heilbar.
    _write_json(home / 'last_restore.json', result)
    plan_path.unlink()
    journal_path.unlink()
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(incoming, ignore_errors=True)
    return result
