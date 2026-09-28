"""Identity-bound SMTP/FinTS secrets. Legacy plaintext is never auto-trusted.

Configuration is read in a single query and written atomically. An endpoint
change without a freshly supplied password revokes the old credential. Env
secrets additionally require an operator-controlled identity fingerprint.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
from urllib.parse import urlsplit, urlunsplit

from django.db import transaction
from ..models import AppSetting
from .mail_secrets import MailError, _cipher

PREFIX = 'bound-v1:'
KEYS = {
    'smtp': ('host', 'port', 'user', 'from_name', 'tls', 'password'),
    'fints': ('blz', 'endpoint', 'login', 'iban', 'product_id', 'pin'),
}
IDENTITY_KEYS = {
    'smtp': ('host', 'port', 'user', 'tls'),
    'fints': ('blz', 'endpoint', 'login', 'iban', 'product_id'),
}
SECRET_KEYS = {'smtp': 'password', 'fints': 'pin'}
ENV_KEYS = {'smtp': 'SEDO_SMTP_PASSWORD', 'fints': 'SEDO_FINTS_PIN'}


class CredentialError(ValueError):
    """Credential-free error safe for display."""


def identity_digest(service: str, config: dict) -> str:
    identity = [service, *(config.get(key, '') for key in IDENTITY_KEYS[service])]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()


def _host(value: str) -> str:
    if not value or len(value) > 253 or any(c.isspace() for c in value):
        raise CredentialError('Bitte einen gültigen SMTP-Host eingeben.')
    value = value.rstrip('.').lower()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        import re
        try:
            value = value.encode('idna').decode('ascii')
        except UnicodeError:
            raise CredentialError('Bitte einen gültigen SMTP-Host eingeben.') from None
        if not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in value.split('.')):
            raise CredentialError('Bitte einen gültigen SMTP-Host ohne URL oder Zugangsdaten eingeben.')
        return value


def validate_config(service: str, config: dict) -> dict:
    result = {key: config.get(key, '') for key in KEYS[service] if key != SECRET_KEYS[service]}
    if any(not isinstance(v, (str, int, bool)) for v in result.values()):
        raise CredentialError('Ungültige Verbindungseinstellungen.')
    for key, value in result.items():
        if isinstance(value, str):
            if len(value) > 2000 or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise CredentialError('Verbindungseinstellungen enthalten ungültige Zeichen.')
            result[key] = value.strip()
    if service == 'smtp':
        result['host'] = _host(result['host'])
        try:
            result['port'] = int(result['port'] or 587)
            if not 1 <= result['port'] <= 65535:
                raise ValueError()
        except (ValueError, TypeError):
            raise CredentialError('Bitte einen SMTP-Port zwischen 1 und 65535 eingeben.') from None
        result['tls'] = result['tls'] in (True, '1', 'true', 'on')
        if not result['user']:
            raise CredentialError('Bitte den SMTP-Anmeldenamen eingeben.')
    else:
        try:
            url = urlsplit(result['endpoint'])
            if (url.scheme != 'https' or not url.hostname or url.username is not None
                    or url.password is not None or url.fragment or '\\' in result['endpoint']
                    or url.port not in (None, 443)):
                raise ValueError()
            host = _host(url.hostname)
            result['endpoint'] = urlunsplit(('https', '['+host+']' if ':' in host else host, url.path or '/', url.query, ''))
        except (ValueError, TypeError):
            raise CredentialError('Bitte eine gültige HTTPS-FinTS-Adresse ohne eingebettete Zugangsdaten verwenden.') from None
        if not result['blz'] or not result['login']:
            raise CredentialError('Bitte BLZ und Bank-Anmeldenamen eingeben.')
        result['iban'] = result['iban'].replace(' ', '').upper()
    return result


def _read_config(service: str) -> tuple[dict, str]:
    keys = KEYS[service]
    stored = dict(AppSetting.objects.filter(key__in=[service+'_'+k for k in keys]).values_list('key', 'value'))
    raw = {key: stored.get(service+'_'+key, '') for key in keys}
    secret = raw.pop(SECRET_KEYS[service])
    if service == 'smtp':
        raw['from_name'] = raw['from_name'] or 'SEDO Objektdienstleistungen GmbH'
        raw['tls'] = stored.get('smtp_tls', '1') == '1'
        try:
            raw['port'] = int(raw['port'] or 587)
        except ValueError:
            raw['port'] = 587
    return raw, secret


def _secret(service: str, config: dict, stored: str) -> str:
    env = os.environ.get(ENV_KEYS[service], '')
    digest = identity_digest(service, config)
    if env:
        expected = os.environ.get('SEDO_'+service.upper()+'_IDENTITY_SHA256', '')
        return env if hmac.compare_digest(expected, digest) else ''
    if not stored.startswith(PREFIX):
        return ''  # No silent migration or trust-on-first-use of legacy plaintext.
    try:
        payload = json.loads(_cipher().decrypt(stored[len(PREFIX):].encode('ascii')))
        if (payload.get('service') != service or not isinstance(payload.get('identity'), str)
                or not hmac.compare_digest(payload['identity'], digest)
                or not isinstance(payload.get('secret'), str)):
            return ''
        return payload['secret']
    except Exception:
        # Ciphertext and decryption exceptions must never appear in a log/UI.
        return ''


def load_config(service: str) -> dict:
    config, stored = _read_config(service)
    try:
        config = validate_config(service, config)
    except CredentialError:
        config['host' if service == 'smtp' else 'endpoint'] = ''
        config[SECRET_KEYS[service]] = ''
        return config
    config[SECRET_KEYS[service]] = _secret(service, config, stored)
    return config


@transaction.atomic
def save_config(service: str, config: dict, new_secret: str = '', *, clear=False) -> bool:
    """Return whether a previous secret was revoked; only an admin view calls this."""
    if not isinstance(new_secret, str) or len(new_secret) > 4096:
        raise CredentialError('Die Zugangsdaten sind ungültig oder zu lang.')
    new = validate_config(service, config)
    old, old_secret = _read_config(service)
    try:
        old = validate_config(service, old)
    except CredentialError:
        pass
    changed = identity_digest(service, old) != identity_digest(service, new)
    stored = '' if clear or changed else old_secret
    if new_secret and not clear:
        try:
            payload = json.dumps({'service': service, 'identity': identity_digest(service, new), 'secret': new_secret}).encode()
            stored = PREFIX + _cipher(create=True).encrypt(payload).decode('ascii')
        except MailError:
            raise CredentialError('Zugangsdaten konnten nicht sicher gespeichert werden. Datenordner prüfen.') from None
    for key, value in new.items():
        AppSetting.set(service+'_'+key, ('1' if value else '0') if isinstance(value, bool) else str(value))
    AppSetting.set(service+'_'+SECRET_KEYS[service], stored)
    return bool(old_secret and (clear or changed) and not new_secret)
