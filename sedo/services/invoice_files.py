"""Rückweg für Dateischreibfehler bei Rechnungen und Angeboten (keine DB-Zugriffe)."""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from uuid import uuid4

from django.conf import settings

logger = logging.getLogger('sedo')
_RUECKWEGE = ContextVar('sedo_dateirueckwege', default=())


def _medienpfad(path: Path) -> Path:
    """Nur Dateien innerhalb von MEDIA_ROOT dürfen verschoben werden."""
    root = Path(settings.MEDIA_ROOT).resolve()
    path = path.resolve()
    if not path.is_relative_to(root):
        raise ValueError('Rechnungsdatei liegt außerhalb des Medienordners.')
    return path


def quarantiniere_dateien(paths) -> None:
    """Dateien erhalten, aber außerhalb der ausgelieferten Medien ablegen.

    Kein Löschen: alte Entwurfs-XMLs und fehlgeschlagene Veröffentlichungen
    bleiben für eine spätere Prüfung erhalten. Der Pfad hängt an MEDIA_ROOT,
    nicht an SEDO_HOME, damit Testläufe niemals den Live-Ordner beschreiben.
    """
    root = Path(settings.MEDIA_ROOT).resolve()
    ziel = root.parent / f'.{root.name}_quarantaene' / uuid4().hex
    for path in dict.fromkeys(_medienpfad(Path(p)) for p in paths):
        if path.is_file():
            dest = ziel / path.relative_to(root)
            dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.replace(path, dest)


def merke_veroeffentlichung(path: Path) -> None:
    """Nur tatsächlich von uns veröffentlichte neue Dateien zurücknehmen.

    Ein nach dem Snapshot von jemand anderem belegtes Ziel gehört nicht diesem
    Vorgang. Insbesondere darf ein abgewiesener No-Clobber-Versuch es nicht
    anschließend über die Fehlerkompensation aus dem Archiv verschieben.
    """
    path = _medienpfad(path)
    stat = path.stat()
    for vorher, erstellt in _RUECKWEGE.get():
        if path in vorher and vorher[path] is None:
            erstellt[path] = (stat.st_dev, stat.st_ino)


def veroeffentliche_neue_datei(source: Path, target: Path) -> None:
    """Vollständige Datei exklusiv veröffentlichen; vorhandene Ziele nie ersetzen.

    Quelle liegt auf demselben Dateisystem im temporären Medienverzeichnis.
    Ein nicht unterstütztes Hardlink-Dateisystem führt zum Abbruch, niemals
    zum unsicheren Fallback auf os.replace oder überschreibendes open().
    """
    os.link(source, target)
    merke_veroeffentlichung(target)


@contextmanager
def datei_rueckweg():
    """Bei einer Ausnahme vorherige Dateien wiederherstellen.

    Mit ``with datei_rueckweg() as merken, transaction.atomic():`` umschließt
    der Dateirückweg auch einen fehlgeschlagenen DB-Commit. Neue Dateien
    werden außerhalb von MEDIA_ROOT verwahrt, vorhandene zurückkopiert.
    Dies ist Fehlerkompensation, KEINE gemeinsame absturzsichere Transaktion
    zwischen SQLite und Dateisystem (z. B. bei Stromausfall).
    """
    vorher, erstellt = {}, {}
    with tempfile.TemporaryDirectory(prefix='sedo_dateirueckweg_') as tmp:
        token = _RUECKWEGE.set((*_RUECKWEGE.get(), (vorher, erstellt)))
        def merken(beleg):
            root = Path(settings.MEDIA_ROOT).resolve()
            out = root / 'generated' / beleg.number
            paths = [out / f'{beleg.number}.pdf']
            if hasattr(beleg, 'xml_file'):
                paths += [out / 'factur-x.xml', out / f'{beleg.number}_zugferd.pdf']
            paths += [Path(f.path) for field in ('pdf_file', 'xml_file', 'zugferd_file')
                      if (f := getattr(beleg, field, None))]
            for path in paths:
                path = _medienpfad(path)
                if path in vorher:
                    continue
                backup = Path(tmp) / str(len(vorher)) if path.is_file() else None
                if backup is not None:
                    shutil.copy2(path, backup)
                vorher[path] = backup
        try:
            yield merken
        except Exception:
            for path, backup in vorher.items():
                try:
                    if backup is None:
                        ident = erstellt.get(path)
                        stat = path.stat() if path.is_file() else None
                        if ident and stat and ident == (stat.st_dev, stat.st_ino):
                            quarantiniere_dateien([path])
                    elif not path.is_file() or path.read_bytes() != backup.read_bytes():
                        path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(backup, path)
                except OSError:
                    logger.exception('Dateirückweg fehlgeschlagen: %s', path)
            raise
        finally:
            _RUECKWEGE.reset(token)
