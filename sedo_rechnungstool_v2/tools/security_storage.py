#!/usr/bin/env python3
"""Plan/copy a stopped SEDO data home without deleting the original.

Default is a read-only inventory. --execute --offline-confirm copies only
operational directories to a NEW destination, checks the database and every
copied file, and atomically publishes the destination. Never rewrites Git
history. --rotate-django-key only rotates the destination, not the source.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import sys
import tempfile

ROOTS = ('data', 'media', 'backups', 'mail-cache', 'security-state')
FILES = ('.env', 'window.json', 'last_restore.json')


class StorageError(ValueError):
    pass


def _files(source):
    for name in (*ROOTS, *FILES):
        path = source / name
        if path.is_symlink():
            raise StorageError('Symbolische Verknüpfung im Datenbestand; keine automatische Übernahme.')
        if path.is_file():
            yield path
        elif path.is_dir():
            for item in sorted(path.rglob('*')):
                if item.is_symlink() or (item.exists() and not item.is_file() and not item.is_dir()):
                    raise StorageError('Nicht reguläre Datei im Datenbestand; Übernahme abgebrochen.')
                if item.is_file():
                    yield item


def inventory(source):
    source = Path(source).resolve(strict=True)
    if (source / 'pending_restore.json').exists():
        raise StorageError('Zuerst die vorgemerkte Wiederherstellung klären.')
    files = list(_files(source))
    return {'file_count': len(files), 'bytes': sum(p.stat().st_size for p in files),
            'database_present': (source/'data/sedo_web.sqlite3').is_file(),
            'django_key_present': (source/'data/.secret_key').is_file(),
            'mail_key_present': (source/'data/.mail_key').is_file(),
            'source_business_data_unchanged': True, 'executed': False}


def _hash(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def _env_override(source):
    if os.environ.get('DJANGO_SECRET_KEY', '').strip():
        return True
    path = source/'.env'
    if path.is_file():
        for line in path.read_text(encoding='utf-8').splitlines():
            if line.strip().startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            if key.strip() == 'DJANGO_SECRET_KEY' and value.strip().strip('"').strip("'"):
                return True
    return False


def copy_home(source, destination, *, offline_confirm=False, rotate_django_key=False):
    if not offline_confirm:
        raise StorageError('SEDO vollständig beenden und --offline-confirm angeben.')
    source = Path(source).resolve(strict=True)
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise StorageError('Ziel muss neu sein; vorhandene Daten werden nie überschrieben.')
    if any(p.is_symlink() for p in destination.parents):
        raise StorageError('Zielpfad darf keine symbolischen Verknüpfungen enthalten.')
    if destination.resolve().is_relative_to(source) or source.is_relative_to(destination.resolve()):
        raise StorageError('Quell- und Zielordner müssen getrennt sein.')
    report = inventory(source)
    if not report['database_present']:
        raise StorageError('Keine SEDO-Datenbank im Quellordner.')
    if rotate_django_key and _env_override(source):
        raise StorageError('DJANGO_SECRET_KEY wird per Umgebung/.env überschrieben. Diesen externen Schlüssel zuerst separat kontrolliert rotieren.')
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from sedo.desktop_lock import acquire
    lock = acquire(source)
    stage = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix='.sedo-move-', dir=destination.parent))
        hashes = {}
        for original in _files(source):
            relative = original.relative_to(source)
            if relative.as_posix() in ('data/sedo_web.sqlite3', 'data/sedo_web.sqlite3-wal', 'data/sedo_web.sqlite3-shm', 'data/sedo_web.sqlite3-journal'):
                continue
            target = stage/relative
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copyfile(original, target)
            os.chmod(target, 0o600)
            digest = _hash(original)
            if _hash(target) != digest:
                raise StorageError('Kopierprüfung fehlgeschlagen.')
            hashes[relative.as_posix()] = digest
        db_path = stage/'data/sedo_web.sqlite3'
        db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        origin = sqlite3.connect((source/'data/sedo_web.sqlite3').as_uri()+'?mode=ro', uri=True)
        target_db = sqlite3.connect(db_path)
        try:
            origin.backup(target_db)
            if target_db.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or target_db.execute('PRAGMA foreign_key_check').fetchall():
                raise StorageError('Datenbankprüfung fehlgeschlagen; Ziel wird nicht veröffentlicht.')
            if rotate_django_key:
                if target_db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='django_session'").fetchone():
                    target_db.execute('DELETE FROM django_session')
                    target_db.commit()
        finally:
            origin.close()
            target_db.close()
        os.chmod(db_path, 0o600)
        if rotate_django_key:
            key = stage/'data/.secret_key'
            key.write_text(secrets.token_urlsafe(64), encoding='utf-8')
            os.chmod(key, 0o600)
            hashes['data/.secret_key'] = _hash(key)
        hashes['data/sedo_web.sqlite3'] = _hash(db_path)
        report.update(executed=True, django_key_rotated=rotate_django_key, database_integrity='ok', verified_files=len(hashes))
        # The private manifest contains hashes, never secret contents.
        manifest = stage/'SECURITY_MOVE_MANIFEST.json'
        manifest.write_text(json.dumps({'report': report, 'sha256': hashes}, indent=2), encoding='utf-8')
        os.chmod(manifest, 0o600)
        if destination.exists():
            raise StorageError('Ziel wurde inzwischen angelegt; keine Überschreibung.')
        stage.rename(destination)
        stage = None
        return report
    finally:
        lock.close()
        if stage is not None:
            shutil.rmtree(stage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--offline-confirm', action='store_true')
    parser.add_argument('--rotate-django-key', action='store_true')
    args = parser.parse_args()
    try:
        if args.execute:
            if args.destination is None:
                parser.error('--destination fehlt')
            result = copy_home(args.source, args.destination, offline_confirm=args.offline_confirm, rotate_django_key=args.rotate_django_key)
        else:
            result = inventory(args.source)
        print(json.dumps(result, indent=2))
        return 0
    except (StorageError, OSError, sqlite3.Error, RuntimeError) as exc:
        print('Übernahme abgebrochen: '+str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
