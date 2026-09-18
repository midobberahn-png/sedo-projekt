"""Wendet eine zuvor geprüfte Wiederherstellung vor dem Django-Start an."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from datetime import datetime


def _sha256(path: Path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _tree_sha256(root: Path):
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob('*') if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode('utf-8'))
        digest.update(b'\0')
        digest.update(_sha256(path).encode('ascii'))
        digest.update(b'\n')
    return digest.hexdigest()


def apply_pending_restore(home: Path) -> dict | None:
    """Atomarer DB-Tausch plus Medien-Tausch; bei Fehlern Rückweg herstellen."""
    home = Path(home)
    plan_path = home / 'pending_restore.json'
    if not plan_path.is_file():
        return None
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    staging = Path(plan['staging']).resolve()
    allowed = (home / 'restore-staging').resolve()
    if not staging.is_relative_to(allowed):
        raise RuntimeError('Wiederherstellungsplan verweist außerhalb des Staging-Ordners.')
    staged_db = staging / 'data' / 'sedo_web.sqlite3'
    if not staged_db.is_file() or _sha256(staged_db) != plan['database_sha256']:
        raise RuntimeError('Die vorbereitete Datenbank fehlt oder ihre Prüfsumme stimmt nicht.')
    expected_tree = plan.get('staging_sha256')
    if expected_tree and _tree_sha256(staging) != expected_tree:
        raise RuntimeError('Vorbereitete Wiederherstellungsdateien wurden nach der Prüfung verändert.')
    with sqlite3.connect(f'file:{staged_db}?mode=ro', uri=True) as con:
        if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Die vorbereitete Datenbank besteht den Integritätstest nicht.')

    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    rollback = home / 'restore-rollback' / timestamp
    rollback.mkdir(parents=True, exist_ok=False)
    live_db = home / 'data' / 'sedo_web.sqlite3'
    live_media = home / 'media'
    staged_media = staging / 'media'
    rollback_db = rollback / 'sedo_web.sqlite3'
    rollback_media = rollback / 'media'
    try:
        live_db.parent.mkdir(parents=True, exist_ok=True)
        if live_db.exists():
            os.replace(live_db, rollback_db)
        os.replace(staged_db, live_db)
        live_db.chmod(0o600)
        if live_media.exists():
            os.replace(live_media, rollback_media)
        if staged_media.exists():
            os.replace(staged_media, live_media)
        else:
            live_media.mkdir(parents=True, exist_ok=True)
    except BaseException:
        if live_db.exists():
            live_db.unlink()
        if rollback_db.exists():
            os.replace(rollback_db, live_db)
        if live_media.exists():
            shutil.rmtree(live_media)
        if rollback_media.exists():
            os.replace(rollback_media, live_media)
        raise
    result = {
        'ok': True, 'applied_at': datetime.now().isoformat(),
        'backup_name': plan['backup_name'], 'rollback': str(rollback),
    }
    (home / 'last_restore.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    plan_path.unlink()
    shutil.rmtree(staging, ignore_errors=True)
    return result
