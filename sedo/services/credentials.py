"""Installation-local encryption bound to the configured remote identity.

The key lives outside backups. No secret is decrypted for settings-page display.
Legacy plaintext is upgraded before use and, via migrate_legacy_credentials, at
startup. Changing the destination never transfers a saved credential to it.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import DatabaseError, connection, transaction

from ..models import AppSetting

PREFIX = 'sedo-credential-v1:'
SERVICES = {
    'smtp': {'secret': 'smtp_password', 'env': 'SEDO_SMTP_PASSWORD',
             'fields': ('host', 'port', 'user', 'tls', 'from_name'),
             'defaults': {'port': '587', 'tls': '1', 'from_name': 'SEDO'}},
    'fints': {'secret': 'fints_pin', 'env': 'SEDO_FINTS_PIN',
              'fields': ('blz', 'endpoint', 'login', 'iban', 'product_id'), 'defaults': {}},
}


class CredentialError(ValueError):
    """Deliberately credential-free text suitable for the application UI."""


def _cipher(create=False):
    directory = Path(settings.SEDO_HOME) / 'data'
    path = directory / '.credentials_key'
    try:
        if directory.is_symlink() or path.is_symlink():
            raise ValueError('symlink')
        if create and not path.exists():
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, name = tempfile.mkstemp(prefix='.credentials-key-', dir=directory)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(Fernet.generate_key())
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(name, path)
                except FileExistsError:
                    pass
            finally:
                Path(name).unlink(missing_ok=True)
        if path.is_symlink():
            raise ValueError('symlink')
        return Fernet(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise CredentialError('Der lokale Zugangsdaten-Schlüssel fehlt oder ist beschädigt. '
                              'Bitte die Zugangsdaten erneut einrichten.') from exc


def configuration(kind):
    """Read the complete metadata in one database snapshot; never return secrets."""
    spec = SERVICES[kind]
    values = dict(AppSetting.objects.filter(
        key__in=[f'{kind}_{field}' for field in spec['fields']]).values_list('key', 'value'))
    return {field: values.get(f'{kind}_{field}', spec['defaults'].get(field, ''))
            for field in spec['fields']}


def _host(value):
    value = value.strip().rstrip('.').lower()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    try:
        value = value.encode('idna').decode('ascii')
    except UnicodeError:
        raise CredentialError('Bitte einen gültigen Servernamen ohne URL oder Zugangsdaten angeben.') from None
    if (not value or len(value) > 253 or not all(
            re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part)
            for part in value.split('.'))):
        raise CredentialError('Bitte einen gültigen Servernamen ohne URL oder Zugangsdaten angeben.')
    return value


def checked_fints_endpoint(value):
    """Reject plaintext, userinfo and ambiguous URL syntax before handling a PIN."""
    try:
        if not value or any(ord(c) <= 32 or ord(c) == 127 for c in value) or '\\' in value:
            raise ValueError()
        parsed = urlsplit(value)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.fragment:
            raise ValueError()
        host = _host(parsed.hostname)
        port = parsed.port or 443
        if not 1 <= port <= 65535:
            raise ValueError()
        host = f'[{host}]' if ':' in host else host
        authority = host if port == 443 else f'{host}:{port}'
        return urlunsplit(('https', authority, parsed.path or '/', parsed.query, ''))
    except (ValueError, UnicodeError):
        raise CredentialError('Die FinTS-Adresse muss eine gültige HTTPS-Adresse ohne eingebettete Zugangsdaten sein.') from None


def validate_configuration(kind, cfg, *, allow_empty=False):
    if kind == 'smtp' and isinstance(cfg.get('tls'), bool):
        cfg = {**cfg, 'tls': '1' if cfg['tls'] else '0'}
    cfg = {field: str(cfg.get(field, '')).strip() for field in SERVICES[kind]['fields']}
    required = ('host', 'user') if kind == 'smtp' else ('blz', 'endpoint', 'login')
    if allow_empty and not any(cfg[field] for field in required):
        return cfg
    if not all(cfg[field] for field in required):
        raise CredentialError('Der Zugang ist unvollständig. Bitte Server und Anmeldenamen vollständig eintragen.')
    if any(any(ord(c) < 32 or ord(c) == 127 for c in cfg[field]) for field in required):
        raise CredentialError('Die Zugangseinstellungen enthalten ungültige Zeichen.')
    if kind == 'fints':
        cfg['endpoint'] = checked_fints_endpoint(cfg['endpoint'])
    else:
        cfg['host'] = _host(cfg['host'])
        try:
            port = int(cfg['port'])
            if not 1 <= port <= 65535 or cfg['tls'] not in ('0', '1'):
                raise ValueError()
        except ValueError:
            raise CredentialError('Bitte einen gültigen SMTP-Port und eine TLS-Verbindung wählen.') from None
        cfg['port'] = str(port)
    return cfg


def identity(kind, cfg):
    """Also handles old/incomplete metadata so migration never changes its target."""
    if kind == 'smtp':
        keys = ('host', 'port', 'user', 'tls')
        cfg = {**cfg, 'host': cfg.get('host', '').strip().rstrip('.').lower()}
    else:
        keys = ('endpoint', 'blz', 'login')
        try:
            cfg = {**cfg, 'endpoint': checked_fints_endpoint(cfg.get('endpoint', ''))}
        except CredentialError:
            pass
    return [str(cfg.get(key, '')) for key in keys]


def _seal(kind, cfg, secret):
    data = {'kind': kind, 'identity': identity(kind, cfg), 'secret': secret}
    return PREFIX + _cipher(create=True).encrypt(json.dumps(data).encode('utf-8')).decode('ascii')


def _open(kind, cfg, value):
    try:
        if not value.startswith(PREFIX):
            raise ValueError()
        data = json.loads(_cipher().decrypt(value[len(PREFIX):].encode('ascii')))
        if data.get('kind') != kind or data.get('identity') != identity(kind, cfg) or not isinstance(data.get('secret'), str):
            raise ValueError()
        return data['secret']
    except (InvalidToken, UnicodeError, ValueError, TypeError, AttributeError):
        raise CredentialError('Die gespeicherten Zugangsdaten passen nicht zu diesem Server und Anmeldenamen. '
                              'Bitte das Passwort beziehungsweise die PIN neu eingeben.') from None


def _secure_update(key, old, new):
    # Overwrite SQLite free-cell contents when migrating historical plaintext.
    # External old copies and historic filesystem snapshots require separate care.
    if connection.vendor == 'sqlite':
        with connection.cursor() as cursor:
            cursor.execute('PRAGMA secure_delete=ON')
    if not AppSetting.objects.filter(key=key, value=old).update(value=new):
        raise CredentialError('Die Zugangseinstellungen wurden gleichzeitig geändert. Bitte erneut versuchen.')


def _migrate_one(kind, cfg):
    key = SERVICES[kind]['secret']
    value = AppSetting.get(key, '')
    if value and not value.startswith(PREFIX):
        sealed = _seal(kind, cfg, value)
        _secure_update(key, value, sealed)
        AppSetting.set('credential_storage_cleanup_pending', '1')
        return sealed
    return value


def migrate_legacy_credentials():
    """Call during startup before serving requests; never contacts a remote server."""
    with transaction.atomic():
        for kind in SERVICES:
            _migrate_one(kind, configuration(kind))
    # Startup runs this before request/worker threads. A pending flag survives a
    # failed cleanup, so the next start retries without re-exposing credentials.
    if AppSetting.get('credential_storage_cleanup_pending') == '1' and not connection.in_atomic_block:
        try:
            if connection.vendor == 'sqlite':
                with connection.cursor() as cursor:
                    for statement in ('PRAGMA wal_checkpoint(TRUNCATE)', 'VACUUM', 'PRAGMA wal_checkpoint(TRUNCATE)'):
                        cursor.execute(statement)
                        if statement.startswith('PRAGMA') and cursor.fetchone()[0] != 0:
                            raise CredentialError('Die lokale Zugangsdaten-Bereinigung ist noch gesperrt. Bitte andere SEDO-Prozesse schließen und neu starten.')
            AppSetting.set('credential_storage_cleanup_pending', '0')
        except DatabaseError:
            raise CredentialError('Die lokale Zugangsdaten-Bereinigung konnte nicht abgeschlossen werden. '
                                  'Bitte freien Speicherplatz prüfen und SEDO neu starten.') from None


def migrate_initialized_credentials():
    """WSGI/ASGI startup gate: do not create a database or require fresh schema.

    Once the settings table exists, any migration/key/cleanup failure must abort
    application loading. The caller deliberately does not swallow these errors.
    """
    name = str(connection.settings_dict.get('NAME') or '')
    memory_database = name == ':memory:' or name.startswith('file:memorydb')
    if connection.vendor == 'sqlite' and not memory_database and not Path(name).is_file():
        return False
    if AppSetting._meta.db_table not in connection.introspection.table_names():
        return False
    migrate_legacy_credentials()
    return True


def secret_for(kind, cfg):
    cfg = validate_configuration(kind, cfg)
    spec = SERVICES[kind]
    with transaction.atomic():
        current = configuration(kind)
        if identity(kind, current) != identity(kind, cfg):
            raise CredentialError('Die Zugangseinstellungen wurden gleichzeitig geändert. Bitte erneut versuchen.')
        stored = _migrate_one(kind, current)
        environment = os.environ.get(spec['env'], '')
        if environment:
            if stored:
                _open(kind, cfg, stored)
            binding_key = spec['secret'] + '_env_binding'
            binding = AppSetting.objects.filter(key=binding_key).first()
            if binding is None:
                binding, _ = AppSetting.objects.get_or_create(
                    key=binding_key, defaults={'value': _seal(kind, cfg, '')})
            if not binding.value:
                _secure_update(binding_key, '', _seal(kind, cfg, ''))
            else:
                _open(kind, cfg, binding.value)
            return environment
        return _open(kind, cfg, stored) if stored else ''


def save_configuration(kind, cfg, *, secret='', clear=False):
    """Atomically validate metadata and bind only explicitly entered new secrets."""
    cfg = validate_configuration(kind, cfg, allow_empty=True)
    spec = SERVICES[kind]
    if len(secret) > 10000:
        raise CredentialError('Das Passwort beziehungsweise die PIN ist zu lang.')
    with transaction.atomic():
        previous = configuration(kind)
        stored = _migrate_one(kind, previous)
        changed = identity(kind, previous) != identity(kind, cfg)
        if changed and os.environ.get(spec['env'], ''):
            raise CredentialError('Für diesen Zugang ist ein Passwort oder eine PIN in der Umgebung gesetzt. '
                                  'Bitte diese Vorgabe zuerst entfernen und SEDO neu starten, bevor Server oder Anmeldename geändert werden.')
        if changed and stored and not (secret or clear):
            raise CredentialError('Für einen anderen Server oder Anmeldenamen das Passwort beziehungsweise die PIN neu eingeben '
                                  'oder die gespeicherten Zugangsdaten ausdrücklich löschen.')
        if secret:
            validate_configuration(kind, cfg)
            stored = _seal(kind, cfg, secret)
        elif clear:
            stored = ''
        elif stored:
            _open(kind, cfg, stored)
        for field, value in cfg.items():
            AppSetting.set(f'{kind}_{field}', value)
        AppSetting.set(spec['secret'], stored)
        if not os.environ.get(spec['env'], ''):
            AppSetting.set(spec['secret'] + '_env_binding', '')
    return cfg
