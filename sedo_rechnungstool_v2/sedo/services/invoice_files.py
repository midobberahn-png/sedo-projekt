"""Rückweg für Dateischreibfehler im Rechnungsworkflow (keine DB-Zugriffe)."""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from django.conf import settings

logger = logging.getLogger('sedo')


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


@contextmanager
def datei_rueckweg():
    """Bei einer Ausnahme vorherige Dateien wiederherstellen.

    Mit ``with datei_rueckweg() as merken, transaction.atomic():`` umschließt
    der Dateirückweg auch einen fehlgeschlagenen DB-Commit. Neue Dateien
    werden außerhalb von MEDIA_ROOT verwahrt, vorhandene zurückkopiert.
    Dies ist Fehlerkompensation, KEINE gemeinsame absturzsichere Transaktion
    zwischen SQLite und Dateisystem (z. B. bei Stromausfall).
    """
    vorher = {}
    with tempfile.TemporaryDirectory(prefix='sedo_dateirueckweg_') as tmp:
        def merken(invoice):
            root = Path(settings.MEDIA_ROOT).resolve()
            out = root / 'generated' / invoice.number
            paths = [out / f'{invoice.number}.pdf', out / 'factur-x.xml',
                     out / f'{invoice.number}_zugferd.pdf']
            paths += [Path(f.path) for f in
                      (invoice.pdf_file, invoice.xml_file, invoice.zugferd_file) if f]
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
                        quarantiniere_dateien([path])
                    else:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(backup, path)
                except OSError:
                    logger.exception('Dateirückweg fehlgeschlagen: %s', path)
            raise
