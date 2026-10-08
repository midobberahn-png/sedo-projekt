"""Private, rebuildable IMAP cache; business sources are pinned into the DB.

The cache is outside MEDIA_ROOT and deliberately excluded from finance backups.
Only authenticated download endpoints can read it. Filenames are derived from
the account id and content hash, never from sender-supplied attachment names.
"""
import hashlib
import os
import re
import tempfile
from pathlib import Path

from django.conf import settings

from .mail_secrets import MailError

MAX_BYTES = 50 * 1024 * 1024


def cache_path(account_id, digest):
    if not re.fullmatch(r'[0-9a-f]{64}', digest) or not isinstance(account_id, int) or account_id <= 0:
        raise MailError('Ungültige Kennung des E-Mail-Zwischenspeichers.')
    root = Path(settings.SEDO_HOME) / 'mail-cache'
    if root.is_symlink():
        raise MailError('Der E-Mail-Zwischenspeicher darf keine symbolische Verknüpfung sein.')
    path = root / f'{account_id}-{digest}.eml'
    if path.is_symlink():
        raise MailError('Die zwischengespeicherte Nachricht darf keine symbolische Verknüpfung sein.')
    return path


def cache_exists(message):
    return not message.raw_cached or cache_path(message.account_id, message.digest).is_file()


def write_cached(account_id, digest, raw):
    path = cache_path(account_id, digest)
    temp = None
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix='.mail-', dir=path.parent)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except OSError as exc:
        raise MailError('Die E-Mail konnte nicht lokal gespeichert werden. Bitte freien Speicherplatz und Datenordner prüfen.') from exc
    finally:
        if temp:
            Path(temp).unlink(missing_ok=True)


def original_bytes(message):
    if not message.raw_cached:
        return bytes(message.raw)
    path = cache_path(message.account_id, message.digest)
    try:
        with path.open('rb') as handle:
            raw = handle.read(MAX_BYTES + 1)
    except OSError as exc:
        raise MailError('Die lokale Mailkopie fehlt. Bitte das Postfach erneut abrufen; das Original liegt auf dem Mailserver.') from exc
    if len(raw) > MAX_BYTES or hashlib.sha256(raw).hexdigest() != message.digest:
        # This is exclusively a generated cache file. Removing a corrupt copy
        # makes the next sync refetch it instead of silently pinning bad bytes.
        path.unlink(missing_ok=True)
        raise MailError('Die lokale Mailkopie ist beschädigt. Bitte das Postfach erneut abrufen.')
    return raw


def pin_message(message):
    """A business link must survive a restore and later server-side deletion."""
    if message.size > MAX_BYTES:
        raise MailError('Diese Nachricht liegt nur als Kopfzeile vor. Eine Geschäftsquelle benötigt das vollständige Original; bitte im Webmail prüfen.')
    if message.raw_cached:
        raw = original_bytes(message)
        from sedo.models import MailMessage
        MailMessage.objects.filter(pk=message.pk, raw_cached=True).update(raw=raw, raw_cached=False)
        message.raw, message.raw_cached = raw, False


def remove_cached(message):
    """Delete only a generated copy, e.g. after an explicit permanent deletion."""
    cache_path(message.account_id, message.digest).unlink(missing_ok=True)
