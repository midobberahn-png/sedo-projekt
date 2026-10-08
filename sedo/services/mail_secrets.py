"""Authenticated encryption with a local key that is never part of a backup."""
import json
import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class MailError(Exception):
    """Only deliberately written, credential-free messages may reach the UI."""


def _cipher(create=False):
    directory = Path(settings.SEDO_HOME) / 'data'
    path = directory / '.mail_key'
    if create and not path.exists():
        directory.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix='.mail-key-', dir=directory)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(Fernet.generate_key())
                handle.flush()
                os.fsync(handle.fileno())
            try:
                # Atomic publication: another worker's valid key is never replaced.
                os.link(temp, path)
            except FileExistsError:
                pass
        finally:
            Path(temp).unlink(missing_ok=True)
    try:
        if path.is_symlink():
            raise ValueError('symlink')
        return Fernet(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise MailError('Der lokale Postfach-Schlüssel fehlt oder ist beschädigt. Bitte den Datenordner prüfen.') from exc


def encrypt_password(account, password):
    payload = json.dumps({'email': account.email, 'username': account.username, 'password': password}).encode()
    return _cipher(create=True).encrypt(payload).decode('ascii')


def decrypt_password(account):
    if not account.password_encrypted:
        raise MailError('Bitte das Postfach-Passwort in den E-Mail-Einstellungen hinterlegen.')
    try:
        payload = json.loads(_cipher().decrypt(account.password_encrypted.encode('ascii')))
        if payload['email'] != account.email or payload['username'] != account.username:
            raise ValueError('identity changed')
        return payload['password']
    except (InvalidToken, ValueError, KeyError, UnicodeError) as exc:
        raise MailError('Das gespeicherte Passwort passt nicht mehr. Bitte für dieses Postfach neu eingeben.') from exc
