from __future__ import annotations

import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
import zipfile

from django.conf import settings
from django.utils import timezone
from django.db.utils import OperationalError, ProgrammingError

from .models import AppSetting

# Diese AppSetting-Schlüssel enthalten Secrets und werden aus Backups entfernt
# (Backups werden kopiert/weitergegeben – Klartext-Keys gehören da nicht hinein).
SECRET_SETTING_KEYS = ('ai_api_key', 'smtp_password', 'fints_pin')


def _safe_name(value: str) -> str:
    return ''.join(c if c.isalnum() or c in ('-', '_', '.') else '_' for c in str(value))


def _sanitized_db_copy(db_path: Path) -> Path:
    """Erzeugt eine temporäre DB-Kopie, in der die Secret-Werte geleert sind.

    Die Live-Datenbank bleibt unverändert; nur die Kopie fürs Backup wird
    bereinigt. Nach einer Wiederherstellung müssen Claude-API-Key und
    SMTP-Passwort einmal neu eingetragen werden.
    """
    fd, tmp_name = tempfile.mkstemp(suffix='.sqlite3')
    import os
    os.close(fd)
    tmp = Path(tmp_name)
    shutil.copy2(db_path, tmp)
    con = sqlite3.connect(tmp)
    try:
        placeholders = ','.join('?' * len(SECRET_SETTING_KEYS))
        con.execute(f"UPDATE sedo_appsetting SET value='' WHERE key IN ({placeholders})",
                    SECRET_SETTING_KEYS)
        con.commit()
    except sqlite3.Error:
        pass  # Tabelle existiert evtl. noch nicht (frische Installation)
    finally:
        con.close()
    return tmp


def backup_dir() -> Path:
    path = Path(getattr(settings, 'SEDO_HOME', settings.BASE_DIR)) / 'backups'
    path.mkdir(parents=True, exist_ok=True)
    return path


def create_backup(reason: str = 'manual') -> Path:
    """Erstellt ein Komplettbackup der Web-App-Daten.

    Enthalten sind Datenbank, Medien/PDFs/XMLs/EMLs und eine kleine Infodatei.
    Der Code selbst und Secrets/.env werden bewusst nicht archiviert; dafür bleibt das jeweilige ZIP der Version erhalten.
    """
    ts = timezone.localtime().strftime('%Y-%m-%d_%H-%M-%S')
    reason_safe = _safe_name(reason or 'manual')
    out = backup_dir() / f'SEDO_Backup_{ts}_{reason_safe}.zip'
    base = Path(getattr(settings, 'SEDO_HOME', settings.BASE_DIR))
    db_path = Path(settings.DATABASES['default']['NAME'])
    media_root = Path(settings.MEDIA_ROOT)

    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zf:
        info = (
            f'SEDO Rechnungstool Backup\n'
            f'Zeitpunkt: {timezone.localtime().isoformat()}\n'
            f'Grund: {reason}\n'
            f'Datenbank: {db_path}\n'
            f'Medienordner: {media_root}\n'
            f'Hinweis: Claude-API-Key und SMTP-Passwort sind aus Sicherheitsgründen\n'
            f'NICHT im Backup enthalten – nach einer Wiederherstellung bitte unter\n'
            f'Einstellungen neu eintragen.\n'
        )
        zf.writestr('BACKUP_INFO.txt', info)
        if db_path.exists():
            sanitized = _sanitized_db_copy(db_path)
            try:
                zf.write(sanitized, arcname='data/' + db_path.name)
            finally:
                sanitized.unlink(missing_ok=True)
        if media_root.exists():
            for p in media_root.rglob('*'):
                if p.is_file():
                    # Keine alten Backups in neue Backups verschachteln
                    if 'backups' in p.parts:
                        continue
                    try:
                        arc = p.relative_to(base)
                    except ValueError:
                        arc = Path('media') / p.name
                    zf.write(p, arcname=str(arc))
    AppSetting.set('last_backup_path', str(out))
    AppSetting.set('last_backup_at', timezone.localtime().isoformat())
    AppSetting.set('last_backup_reason', reason)
    return out


def ensure_weekly_backup() -> None:
    """Erstellt höchstens einmal pro Woche automatisch ein Backup.

    Wird bei Seitenaufrufen geprüft. Fehler werden geschluckt, damit die App nicht am Backup scheitert.
    """
    try:
        enabled = AppSetting.get('auto_weekly_backup_enabled', '1')
        if enabled not in ('1', 'true', 'True', 'ja', 'Ja'):
            return
        last = AppSetting.get('last_weekly_backup_at', '')
        now = timezone.localtime()
        if last:
            try:
                last_dt = datetime.fromisoformat(last)
                if timezone.is_naive(last_dt):
                    last_dt = timezone.make_aware(last_dt, timezone.get_current_timezone())
                if now - last_dt < timedelta(days=7):
                    return
            except Exception:
                pass
        path = create_backup('weekly_auto')
        AppSetting.set('last_weekly_backup_at', now.isoformat())
        AppSetting.set('last_weekly_backup_path', str(path))
    except (OperationalError, ProgrammingError):
        return
    except Exception:
        return
