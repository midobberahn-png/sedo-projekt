"""Datei-Fixtures müssen wie die Testdatenbank pro Test isoliert sein."""
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import override_settings


def isolate_files(test):
    """Kein Aufräumen fremder Testdateien, keine Lockerung der Archivschranke."""
    tmp = TemporaryDirectory(prefix='sedo_fixture_')
    test.addCleanup(tmp.cleanup)
    home = Path(tmp.name)
    settings = override_settings(MEDIA_ROOT=home / 'media', SEDO_BACKUP_DIR=home / 'backups')
    settings.enable()
    test.addCleanup(settings.disable)
