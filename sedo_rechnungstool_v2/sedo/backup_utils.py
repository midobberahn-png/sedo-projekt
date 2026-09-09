from __future__ import annotations

import logging
import os
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

logger = logging.getLogger('sedo')

# Diese AppSetting-Schlüssel enthalten Secrets und werden aus Backups entfernt
# (Backups werden kopiert/weitergegeben – Klartext-Keys gehören da nicht hinein).
SECRET_SETTING_KEYS = ('ai_api_key', 'smtp_password', 'fints_pin')

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
    bereinigt. Nach einer Wiederherstellung müssen Claude-API-Key,
    SMTP-Passwort und Bank-PIN einmal neu eingetragen werden.

    Drei Dinge, die vorher fehlten:
    - ``django_session`` wird gelöscht, sonst liegt im Backup ein gültiger
      Anmelde-Cookie (F-03).
    - ``PRAGMA secure_delete`` und ``VACUUM``: ein blankes UPDATE gibt die
      alten Klartext-Bytes nur als freien Platz frei, sie bleiben mit
      ``strings`` lesbar (F-31).
    - Kein ``except: pass`` mehr. Lässt sich die Bereinigung nicht belegen,
      wird die halbfertige Kopie verworfen und ``BackupError`` geworfen (F-81).

    ``copyfile`` statt ``copy2``: copy2 überträgt die Rechte der Live-Datei
    (0644) auf die Kopie und macht die noch unbereinigte Vollkopie damit für
    die Dauer der Sicherung im gemeinsamen Temp-Ordner weltlesbar. copyfile
    lässt die 0600 von ``mkstemp`` stehen.
    """
    fd, tmp_name = tempfile.mkstemp(suffix='.sqlite3')
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        shutil.copyfile(db_path, tmp)
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
        path = Path(tempfile.gettempdir()) / 'sedo_test_backups'
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
    # Ein paralleler Lauf in derselben Sekunde träfe denselben Dateinamen –
    # dessen fertiges ZIP darf ein Abbruch hier nicht wegräumen.
    bereits_vorhanden = out.exists()

    try:
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zf:
            info = (
                f'SEDO Rechnungstool Backup\n'
                f'Zeitpunkt: {timezone.localtime().isoformat()}\n'
                f'Grund: {reason}\n'
                f'Datenbank: {db_path}\n'
                f'Medienordner: {media_root}\n'
                f'Hinweis: Claude-API-Key, SMTP-Passwort und Bank-PIN sind aus Sicherheitsgründen\n'
                f'NICHT im Backup enthalten – auch nicht als Rest-Bytes; die Datenbankkopie wird\n'
                f'dafür neu aufgebaut. Ebenfalls entfernt: alle aktiven Anmelde-Sitzungen.\n'
                f'Nach einer Wiederherstellung bitte unter Einstellungen neu eintragen.\n'
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
    except BaseException as fehler:
        # Ohne das bliebe nach einem Abbruch ein ZIP liegen, das nur die
        # BACKUP_INFO.txt enthält – es sähe wie eine gültige Sicherung aus
        # und wäre schlimmer als gar keine (F-81).
        if not bereits_vorhanden:
            out.unlink(missing_ok=True)
        _remember_backup_error(str(fehler) or fehler.__class__.__name__)
        raise
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
