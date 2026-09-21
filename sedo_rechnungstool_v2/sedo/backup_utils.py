from __future__ import annotations

import logging
from contextlib import closing
import os
import shutil
import sqlite3
import tempfile
import hashlib
import json
import subprocess
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
import zipfile

from django.conf import settings
from django.utils import timezone
from django.db.utils import OperationalError, ProgrammingError

from .models import AppSetting

logger = logging.getLogger('sedo')

# Diese AppSetting-Schlüssel enthalten Secrets und werden aus Backups entfernt
# (Backups werden kopiert/weitergegeben – Klartext-Keys gehören da nicht hinein).
SECRET_SETTING_KEYS = ('ai_api_key', 'smtp_password', 'fints_pin', 'weather_api_key')

# Ganze Tabellen, die aus der Backup-Kopie gelöscht werden. django_session
# enthält gültige Anmelde-Sitzungen: der session_key IST der Cookie-Wert.
# Wer ein Backup bekommt, könnte den Schlüssel auslesen, ihn als Cookie setzen
# und wäre bis zum Ablauf angemeldet – ohne Passwort (Review-Fund F-03).
PURGED_BACKUP_TABLES = ('django_session',)

# Erst ab dieser Länge wird ein Secret in den Rohbytes der Kopie gesucht.
# Kürzere Werte ("1", "ja") kämen zufällig überall vor und würden jede
# Sicherung grundlos abbrechen.
MIN_SECRET_LEN_FOR_BYTE_CHECK = 12


class BackupError(RuntimeError):
    """Die Sicherung konnte nicht sicher erstellt werden.

    Wird geworfen, wenn die Secret-Bereinigung der Datenbankkopie nicht
    nachweisbar gelungen ist. Lieber gar kein Backup als eines, das entgegen
    der Zusage in der Oberfläche Bank-PIN, SMTP-Passwort oder API-Key enthält
    (Review-Fund F-81).
    """


MAX_RESTORE_FILES = 100_000
MAX_RESTORE_BYTES = 4 * 1024 * 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob('*') if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode('utf-8'))
        digest.update(b'\0')
        digest.update(_sha256(path).encode('ascii'))
        digest.update(b'\n')
    return digest.hexdigest()


def _safe_backup_path(name: str) -> Path:
    if not name or Path(name).name != name or not name.endswith('.zip'):
        raise BackupError('Ungültiger Backup-Name.')
    path = (backup_dir() / name).resolve()
    if not path.is_relative_to(backup_dir().resolve()) or not path.is_file():
        raise BackupError('Das ausgewählte Backup wurde nicht gefunden.')
    return path


def inspect_backup(name: str) -> dict:
    try:
        return _inspect_backup(name)
    except BackupError:
        raise
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, sqlite3.DatabaseError) as exc:
        raise BackupError(f'Die Sicherung konnte nicht vollständig geprüft werden: {exc}') from exc


def _simulate_restore(home):
    command = [sys.executable]
    if not getattr(sys, 'frozen', False):
        command.append(str(Path(__file__).resolve().parents[1] / 'tools/desktop_entry.py'))
    command.extend(['--check-restore', str(home)])
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                                errors='replace', timeout=120, env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BackupError('Die Wiederherstellungssimulation konnte nicht abgeschlossen werden.') from exc
    if result.returncode:
        raise BackupError((result.stdout or result.stderr or 'Wiederherstellungssimulation fehlgeschlagen.')[-1500:].strip())


def _inspect_backup(name: str) -> dict:
    """Prüft ZIP, Pfade, SQLite-Integrität und zeigt eine Wiederherstellungsvorschau."""
    path = _safe_backup_path(name)
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_RESTORE_FILES or sum(info.file_size for info in infos) > MAX_RESTORE_BYTES:
            raise BackupError('Das Backup überschreitet die sichere Größenbegrenzung.')
        files = []
        seen = set()
        for info in infos:
            member = Path(info.filename)
            mode = info.external_attr >> 16
            if (member.is_absolute() or '..' in member.parts or '\\' in info.filename or ':' in info.filename
                    or (mode & 0o170000) == 0o120000):
                raise BackupError(f'Unsicherer Archivpfad: {info.filename}')
            if not member.parts or member.parts[0] not in ('data', 'media', 'BACKUP_INFO.txt'):
                raise BackupError(f'Unerwartete Datei in der Sicherung: {info.filename}')
            if member.parts[0] == 'data' and not info.is_dir() and not info.filename.endswith('.sqlite3'):
                raise BackupError(f'Unerwartete Datenbank-Begleitdatei: {info.filename}')
            normalized = member.as_posix().casefold()
            if normalized in seen:
                raise BackupError(f'Doppelter Archivpfad: {info.filename}')
            seen.add(normalized)
            if not info.is_dir():
                files.append(info.filename)
        db_names = [item for item in files if item.startswith('data/') and item.endswith('.sqlite3')]
        if len(db_names) != 1:
            raise BackupError('Das Backup muss genau eine Datenbank unter data/ enthalten.')
        with tempfile.TemporaryDirectory(prefix='sedo_restore_check_') as tmp:
            db_path = Path(tmp) / 'data/sedo_web.sqlite3'
            db_path.parent.mkdir()
            with archive.open(db_names[0]) as source, db_path.open('wb') as target:
                shutil.copyfileobj(source, target)
            try:
                with closing(sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True)) as con:
                    integrity = con.execute('PRAGMA integrity_check').fetchone()[0]
                    tables = _table_names(con)
                    counts = {}
                    for table, label in (
                        ('sedo_customer', 'Kunden'), ('sedo_invoice', 'Rechnungen'),
                        ('sedo_offer', 'Angebote'), ('sedo_payment', 'Zahlungen')):
                        counts[label] = con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] if table in tables else 0
                    migrations = con.execute(
                        "SELECT name FROM django_migrations WHERE app='sedo' ORDER BY applied DESC LIMIT 1"
                    ).fetchone() if 'django_migrations' in tables else None
                    for table, columns in (
                        ('sedo_invoice', ('pdf_file', 'xml_file', 'zugferd_file')),
                        ('sedo_offer', ('pdf_file',)), ('sedo_dunningdocument', ('pdf_file',))):
                        if table not in tables:
                            continue
                        actual = {column[1] for column in con.execute(f'PRAGMA table_info({table})')}
                        for column in set(columns) & actual:
                            for value, in con.execute(f'SELECT {column} FROM {table} WHERE {column} != \'\''):
                                if 'media/' + value.replace('\\', '/') not in files:
                                    raise BackupError(f'Eine referenzierte Belegdatei fehlt: {value}')
            except sqlite3.DatabaseError as exc:
                raise BackupError(f'Datenbank im Backup ist nicht lesbar: {exc}') from exc
            if integrity != 'ok':
                raise BackupError(f'Datenbank-Integritätstest fehlgeschlagen: {integrity}')
            required = {'django_migrations', 'sedo_customer', 'sedo_invoice'}
            if not required.issubset(tables):
                raise BackupError('Das Backup enthält keine vollständige SEDO-Datenbank.')
            digest = _sha256(db_path)
            _simulate_restore(Path(tmp))
        bad_member = archive.testzip()
        if bad_member:
            raise BackupError(f'Beschädigte Datei in der Sicherung: {bad_member}')
    media_files = [item for item in files if item.startswith('media/')]
    return {
        'name': name, 'path': path, 'size': path.stat().st_size,
        'archive_sha256': _sha256(path),
        'database_member': db_names[0], 'database_sha256': digest,
        'counts': counts, 'media_files': len(media_files),
        'migration': migrations[0] if migrations else 'unbekannt', 'integrity': 'ok',
        'file_count': len(files),
    }


def stage_restore(name: str, expected_digest=None) -> dict:
    """Prüft erneut, legt Sicherheitsbackup an und plant den Tausch beim Neustart."""
    report = inspect_backup(name)
    if expected_digest is not None and expected_digest != report['archive_sha256']:
        raise BackupError('Die Sicherung hat sich seit der Vorschau geändert. Bitte erneut prüfen.')
    home = Path(getattr(settings, 'SEDO_HOME', settings.BASE_DIR)).resolve()
    if (home / 'pending_restore.json').exists():
        raise BackupError('Es ist bereits eine Wiederherstellung für den nächsten Start vorgemerkt.')
    safety = create_backup('pre_restore')
    staging_root = home / 'restore-staging'
    staging_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = Path(tempfile.mkdtemp(prefix='restore-', dir=staging_root))
    source = report['path']
    try:
        with zipfile.ZipFile(source) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_RESTORE_FILES or sum(info.file_size for info in infos) > MAX_RESTORE_BYTES:
                raise BackupError('Das Backup überschreitet die sichere Größenbegrenzung.')
            for info in infos:
                if info.is_dir():
                    continue
                member = Path(info.filename)
                mode = info.external_attr >> 16
                if (member.is_absolute() or '..' in member.parts or '\\' in info.filename or ':' in info.filename
                        or (mode & 0o170000) == 0o120000):
                    raise BackupError(f'Unsicherer Archivpfad: {info.filename}')
                target = staging / member
                if not target.resolve().is_relative_to(staging.resolve()):
                    raise BackupError(f'Unsicherer Archivpfad: {info.filename}')
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as src, target.open('xb') as dst:
                    shutil.copyfileobj(src, dst)
        if _sha256(source) != report['archive_sha256']:
            raise BackupError('Das Backup wurde während der Vorbereitung verändert.')
        staged_db = staging / report['database_member']
        target_db = staging / 'data' / 'sedo_web.sqlite3'
        if staged_db != target_db:
            target_db.parent.mkdir(parents=True, exist_ok=True)
            staged_db.replace(target_db)
        if _sha256(target_db) != report['database_sha256']:
            raise BackupError('Prüfsumme der vorbereiteten Datenbank stimmt nicht.')
        # Migrationen laufen schon auf der isolierten Kopie. Der Neustart bekommt
        # exakt den geprüften, aktuellen Datenstand und muss ihn nur noch tauschen.
        _simulate_restore(staging)
        plan = {
            'schema': 1, 'backup_name': name, 'staging': str(staging),
            'database_sha256': _sha256(target_db), 'safety_backup': str(safety),
            'staging_sha256': _tree_sha256(staging),
            'staged_at': timezone.localtime().isoformat(),
        }
        # Exklusiv veröffentlichen: ein zweiter Klick darf einen bestehenden Plan
        # nicht ersetzen oder dessen vorbereitete Dateien löschen.
        temp = home / f'pending_restore-{uuid.uuid4().hex}.tmp'
        try:
            temp.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
            os.link(temp, home / 'pending_restore.json')
        except FileExistsError as exc:
            raise BackupError('Es ist bereits eine Wiederherstellung vorgemerkt.') from exc
        finally:
            temp.unlink(missing_ok=True)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return plan


def _safe_name(value: str) -> str:
    return ''.join(c if c.isalnum() or c in ('-', '_', '.') else '_' for c in str(value))


def _table_names(con: sqlite3.Connection) -> set[str]:
    """Namen aller Tabellen in der geöffneten Datei."""
    return {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _remember_backup_error(text: str) -> None:
    """Hält den letzten Sicherungsfehler für die Oberfläche fest.

    Ohne LOGGING-Konfiguration (Review-Fund F-25) läuft der Logger im
    gebündelten Desktop-Betrieb ins Leere – dort ist dieser Vermerk der
    einzige Kanal, über den ein Fehlschlag sichtbar wird. Darf selbst nicht
    werfen: er läuft in Fehlerpfaden, in denen die Datenbank schon klemmen kann.
    """
    try:
        AppSetting.set('last_backup_error', str(text)[:500])
        AppSetting.set('last_backup_error_at', timezone.localtime().isoformat())
    except Exception:
        logger.warning('Sicherungsfehler konnte nicht vermerkt werden.', exc_info=True)


def _sanitized_db_copy(db_path: Path) -> Path:
    """Erzeugt eine temporäre DB-Kopie, in der die Secret-Werte entfernt sind.

    Die Live-Datenbank bleibt unverändert; nur die Kopie fürs Backup wird
    bereinigt. Nach einer Wiederherstellung müssen alte KI-API-Schlüssel,
    SMTP-Passwort und Bank-PIN einmal neu eingetragen werden.

    Drei Dinge, die vorher fehlten:
    - ``django_session`` wird gelöscht, sonst liegt im Backup ein gültiger
      Anmelde-Cookie (F-03).
    - ``PRAGMA secure_delete`` und ``VACUUM``: ein blankes UPDATE gibt die
      alten Klartext-Bytes nur als freien Platz frei, sie bleiben mit
      ``strings`` lesbar (F-31).
    - Kein ``except: pass`` mehr. Lässt sich die Bereinigung nicht belegen,
      wird die halbfertige Kopie verworfen und ``BackupError`` geworfen (F-81).

    Die SQLite-Backup-API liefert einen konsistenten Snapshot einschließlich
    bestätigter WAL-Daten. Die Kopie behält die privaten 0600-Rechte von mkstemp.
    """
    fd, tmp_name = tempfile.mkstemp(suffix='.sqlite3')
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        # SQLite-Snapshot nimmt auch bereits bestätigte WAL-Änderungen konsistent mit.
        with closing(sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(tmp)) as target:
                source.backup(target)
        # isolation_level=None: ohne Autocommit öffnet das sqlite3-Modul vor
        # jedem DML eine implizite Transaktion, und VACUUM scheitert dann mit
        # "cannot VACUUM from within a transaction".
        con = sqlite3.connect(tmp, isolation_level=None)
        alte_werte = []
        try:
            tabellen = _table_names(con)
            if not tabellen:
                raise BackupError(
                    'Die Datenbankkopie enthält keine einzige Tabelle – sie ist leer oder '
                    'abgeschnitten. Die Sicherung wurde abgebrochen.'
                )
            con.execute('PRAGMA secure_delete=ON')

            # Fehlt nur sedo_appsetting, ist das eine frische Installation –
            # kein Grund abzubrechen. Eine Datei ganz ohne Tabellen dagegen
            # schon (oben behandelt).
            if 'sedo_appsetting' in tabellen:
                platzhalter = ','.join('?' * len(SECRET_SETTING_KEYS))
                alte_werte = [
                    row[0] for row in con.execute(
                        f'SELECT value FROM sedo_appsetting WHERE key IN ({platzhalter})',
                        SECRET_SETTING_KEYS,
                    ) if row[0]
                ]
                con.execute(
                    f"UPDATE sedo_appsetting SET value='' WHERE key IN ({platzhalter})",
                    SECRET_SETTING_KEYS,
                )
                verbliebene = con.execute(
                    f'SELECT COUNT(*) FROM sedo_appsetting '
                    f"WHERE key IN ({platzhalter}) AND value != ''",
                    SECRET_SETTING_KEYS,
                ).fetchone()[0]
                if verbliebene:
                    raise BackupError(
                        f'{verbliebene} Secret-Werte ließen sich nicht aus der Backup-Kopie '
                        'entfernen. Die Sicherung wurde abgebrochen.'
                    )

            if 'sedo_mailaccount' in tabellen:
                # The encryption key is local and deliberately not included in a backup.
                # Remove ciphertext too; restored accounts require explicit reactivation.
                con.execute("UPDATE sedo_mailaccount SET password_encrypted='', enabled=0, lease_token='', lease_until=NULL")
                if con.execute("SELECT COUNT(*) FROM sedo_mailaccount WHERE password_encrypted != ''").fetchone()[0]:
                    raise BackupError('Postfach-Zugangsdaten konnten nicht aus der Sicherung entfernt werden.')
            if 'sedo_mailfolder' in tabellen:
                con.execute('UPDATE sedo_mailfolder SET cached_count=0')

            for tabelle in PURGED_BACKUP_TABLES:
                if tabelle in tabellen:
                    con.execute(f'DELETE FROM {tabelle}')

            # VACUUM steht bewusst außerhalb jeder Fehlertoleranz: es ist der
            # Schritt, der die alten Klartext-Bytes tatsächlich verschwinden
            # lässt. Wird er übersprungen, ist die Kopie nicht sauber.
            con.execute('VACUUM')
        finally:
            con.close()

        # Gegenprobe an den Rohbytes: erst sie belegt, dass die Werte wirklich
        # weg sind und nicht nur logisch überschrieben wurden.
        rohbytes = tmp.read_bytes()
        for wert in alte_werte:
            if len(wert) >= MIN_SECRET_LEN_FOR_BYTE_CHECK and wert.encode('utf-8') in rohbytes:
                raise BackupError(
                    'Ein Secret steht nach der Bereinigung weiterhin in den Rohbytes der '
                    'Backup-Kopie. Die Sicherung wurde abgebrochen.'
                )
    except BaseException:
        # Auch bei Strg-C darf keine unbereinigte Klartext-Kopie liegen bleiben.
        tmp.unlink(missing_ok=True)
        raise
    return tmp


def backup_dir() -> Path:
    """Zielordner der Sicherungen.

    Kommt aus dem Setting SEDO_BACKUP_DIR; im Testlauf zeigt es ins
    Temp-Verzeichnis, damit Testläufe nicht in den echten backups/-Ordner
    schreiben (Review-Fund F-38). Der Fallback auf SEDO_HOME/backups hält
    ältere Konfigurationen ohne dieses Setting am Laufen.
    """
    configured = getattr(settings, 'SEDO_BACKUP_DIR', None)
    path = Path(configured) if configured else Path(getattr(settings, 'SEDO_HOME', settings.BASE_DIR)) / 'backups'
    # Zweiter Riegel: Der Testzweig in settings.py hängt an 'test' in sys.argv
    # und greift nicht, wenn Django programmatisch gestartet wird (django.setup()
    # plus Test-Client). Genau so sind 91 weekly_auto-ZIPs im echten Ordner
    # gelandet. Der Name der Datenbank verrät den Testbetrieb zuverlässiger.
    _db = str(settings.DATABASES.get('default', {}).get('NAME', ''))
    if 'memorydb' in _db or Path(_db).name.startswith('test_'):
        path = Path(tempfile.gettempdir()) / f'sedo_test_backups_{os.getpid()}'
    path.mkdir(parents=True, exist_ok=True)
    return path


def create_backup(reason: str = 'manual') -> Path:
    """Erstellt ein Komplettbackup der Web-App-Daten.

    Enthalten sind Datenbank, Medien/PDFs/XMLs/EMLs und eine kleine Infodatei.
    Der Code selbst und Secrets/.env werden bewusst nicht archiviert; dafür bleibt das jeweilige ZIP der Version erhalten.
    """
    ts = timezone.localtime().strftime('%Y-%m-%d_%H-%M-%S-%f') + '_' + uuid.uuid4().hex[:8]
    reason_safe = _safe_name(reason or 'manual')
    out = backup_dir() / f'SEDO_Backup_{ts}_{reason_safe}.zip'
    base = Path(getattr(settings, 'SEDO_HOME', settings.BASE_DIR))
    db_path = Path(settings.DATABASES['default']['NAME'])
    media_root = Path(settings.MEDIA_ROOT)
    fd, temporary_name = tempfile.mkstemp(prefix='.backup-', suffix='.part', dir=out.parent)
    os.close(fd)
    temporary = Path(temporary_name)

    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as zf:
            info = (
                f'SEDO Rechnungstool Backup\n'
                f'Zeitpunkt: {timezone.localtime().isoformat()}\n'
                f'Grund: {reason}\n'
                f'Datenbank: {db_path}\n'
                f'Medienordner: {media_root}\n'
                f'Hinweis: alte KI-API-Schlüssel, SMTP-Passwort und Bank-PIN sind aus Sicherheitsgründen\n'
                f'NICHT im Backup enthalten – auch nicht als Rest-Bytes; die Datenbankkopie wird\n'
                f'dafür neu aufgebaut. Ebenfalls entfernt: alle aktiven Anmelde-Sitzungen.\n'
                f'Nach einer Wiederherstellung bitte unter Einstellungen neu eintragen.\n'
                f'Postfach-Passwörter und der lokale IMAP-Zwischenspeicher sind nicht enthalten.\n'
                f'Mail-Geschäftsquellen, Antwortentwürfe und Versandbelege sind in der Datenbank gesichert.\n'
                f'Normale Servernachrichten werden nach erneuter Postfach-Einrichtung nachgeladen.\n'
                f'Lässt sich die Bereinigung nicht belegen, bricht die Sicherung ab.\n'
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
        # Beide Dateien liegen auf demselben Laufwerk; auch externe FAT/exFAT-
        # Sicherungsziele unterstützen diesen atomaren Namenswechsel.
        os.replace(temporary, out)
    except BaseException as fehler:
        # Ohne das bliebe nach einem Abbruch ein ZIP liegen, das nur die
        # BACKUP_INFO.txt enthält – es sähe wie eine gültige Sicherung aus
        # und wäre schlimmer als gar keine (F-81).
        _remember_backup_error(str(fehler) or fehler.__class__.__name__)
        raise
    finally:
        temporary.unlink(missing_ok=True)
    AppSetting.set('last_backup_path', str(out))
    AppSetting.set('last_backup_at', timezone.localtime().isoformat())
    AppSetting.set('last_backup_reason', reason)
    AppSetting.set('last_backup_error', '')
    return out


def ensure_weekly_backup() -> None:
    """Erstellt höchstens einmal pro Woche automatisch ein Backup.

    Wird bei Seitenaufrufen geprüft. Ein Fehlschlag darf die App nicht
    blockieren, wird aber nicht mehr spurlos verschluckt: ``create_backup``
    hinterlegt ihn über ``_remember_backup_error`` für die Backup-Seite,
    zusätzlich geht er ins Log (Review-Funde F-81, F-38).
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
        # Migration läuft noch oder die Tabelle fehlt – kein Sicherheitsproblem.
        return
    except BackupError as fehler:
        # Der Vermerk steht schon (create_backup), hier nur noch ins Log.
        logger.warning('Automatisches Wochenbackup abgebrochen: %s', fehler)
        return
    except Exception:
        logger.exception('Automatisches Wochenbackup fehlgeschlagen.')
        return
