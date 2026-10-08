"""Server-side roles and persistent, bounded login attempt accounting.

Staff administer the installation. Ordinary users edit shared business data;
the explicit read-only group can access only reviewed read routes. The local
throttle database contains keyed hashes, never usernames, addresses or secrets.
It is separate from business backups and survives server restarts.
"""
from contextlib import closing
import hashlib
import hmac
import ipaddress
import math
import os
from pathlib import Path
import sqlite3
import time
import unicodedata

from django.conf import settings

READ_ONLY_GROUP = 'SEDO Mitarbeiter Lesen'
SAFE_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS'})
ADMIN_VIEWS = frozenset({
    'sedo:settings', 'sedo:backup', 'sedo:readiness_toggle',
    'sedo:mail_settings', 'sedo:mail_account', 'sedo:mail_spam_settings',
    'sedo:incoming_settings',
})
# Allow-list deliberately excludes GETs that refresh business state, produce
# durable exports, prepare outgoing mail, or run restore simulations. New routes
# must be classified before they become available to the read-only group.
READ_ONLY_VIEWS = frozenset({
    'sedo:company_setup',
    'sedo:dashboard', 'sedo:customer_list', 'sedo:customer_detail',
    'sedo:customer_data_api', 'sedo:global_search', 'sedo:invoice_list',
    'sedo:invoice_detail', 'sedo:offer_list', 'sedo:offer_detail',
    'sedo:payment_list', 'sedo:cashbook_list', 'sedo:reports', 'sedo:month_close',
    'sedo:task_list', 'sedo:service_catalog_list', 'sedo:recurring_monthly',
    'sedo:incoming_index', 'sedo:incoming_detail', 'sedo:incoming_original',
    'sedo:incoming_preview', 'sedo:incoming_print', 'sedo:incoming_package',
    'sedo:incoming_package_download', 'sedo:mail_inbox', 'sedo:mail_message',
    'sedo:mail_html', 'sedo:mail_original', 'sedo:mail_attachment',
    'sedo:mail_status', 'sedo:calendar_index', 'sedo:calendar_detail',
    'sedo:security_status', 'sedo:readiness', 'sedo:audit_log', 'protected_media',
    'sedo:document_preview',
})


def is_read_only(user):
    return bool(user and user.is_authenticated and not user.is_superuser
                and user.groups.filter(name=READ_ONLY_GROUP).exists())


def is_administrator(user):
    return bool(user and user.is_authenticated and user.is_active
                and (user.is_superuser or (user.is_staff and not is_read_only(user))))


def trusted_proxy(address):
    try:
        peer = ipaddress.ip_address(address)
        return any(peer in ipaddress.ip_network(network)
                   for network in settings.SEDO_TRUSTED_PROXIES)
    except (TypeError, ValueError):
        return False


def client_address(request):
    """Use the socket peer unless a configured proxy supplied a valid chain."""
    peer = request.META.get('REMOTE_ADDR', '')
    if trusted_proxy(peer):
        raw = request.META.get('HTTP_X_FORWARDED_FOR', '')
        hops = raw.split(',') if len(raw) <= 2048 else []
        if 0 < len(hops) <= 32:
            try:
                addresses = [str(ipaddress.ip_address(item.strip())) for item in hops]
            except ValueError:
                addresses = []
            for address in reversed(addresses):
                if not trusted_proxy(address):
                    peer = address
                    break
    try:
        address = ipaddress.ip_address(peer)
        # One IPv6 client must not evade an address limit by rotating /128s.
        return str(ipaddress.ip_network(f'{address}/64', strict=False)) if address.version == 6 else str(address)
    except ValueError:
        return 'unknown-peer'


class LoginThrottleUnavailable(Exception):
    pass


class LoginThrottle:
    WINDOW = 15 * 60
    MAX_ROWS = 4096
    LIMITS = {'pair': 10, 'account': 20, 'address': 100}

    def _connect(self):
        root = Path(settings.SEDO_HOME) / 'data' / 'security'
        if root.is_symlink() or root.parent.is_symlink():
            raise LoginThrottleUnavailable()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = root / 'login-attempts.sqlite3'
        if path.is_symlink():
            raise LoginThrottleUnavailable()
        # Create atomically with private permissions, including on first use.
        fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        os.close(fd)
        os.chmod(path, 0o600)
        connection = sqlite3.connect(path, timeout=2, isolation_level=None)
        try:
            connection.execute('CREATE TABLE IF NOT EXISTS attempts (key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires REAL NOT NULL)')
            connection.execute('CREATE INDEX IF NOT EXISTS attempts_expiry ON attempts(expires)')
        except Exception:
            connection.close()
            raise
        return connection

    def keys(self, request):
        username = unicodedata.normalize('NFKC', str(request.POST.get('username', ''))[:256]).casefold()
        address = client_address(request)
        values = {'pair': address + '\0' + username, 'account': username, 'address': address}
        key = str(settings.SECRET_KEY).encode('utf-8')
        return {kind: hmac.new(key, (kind + '\0' + value).encode('utf-8'), hashlib.sha256).hexdigest()
                for kind, value in values.items()}

    def reserve(self, request):
        """Count before authenticating so concurrent attempts cannot bypass limits.

        Denied attempts never extend a cooldown. Capacity exhaustion fails closed
        until the oldest fixed window expires; storage cannot grow unboundedly.
        """
        keys, now = self.keys(request), time.time()
        try:
            with closing(self._connect()) as connection:
                connection.execute('BEGIN IMMEDIATE')
                connection.execute('DELETE FROM attempts WHERE expires <= ?', (now,))
                retry = 0
                missing = 0
                for kind, key in keys.items():
                    row = connection.execute('SELECT count, expires FROM attempts WHERE key = ?', (key,)).fetchone()
                    if row is None:
                        missing += 1
                    elif row[0] >= self.LIMITS[kind]:
                        retry = max(retry, math.ceil(row[1] - now))
                count, oldest = connection.execute('SELECT COUNT(*), MIN(expires) FROM attempts').fetchone()
                if count + missing > self.MAX_ROWS:
                    retry = max(retry, math.ceil((oldest or now + self.WINDOW) - now))
                if not retry:
                    for key in keys.values():
                        connection.execute('INSERT INTO attempts VALUES (?, 1, ?) ON CONFLICT(key) DO UPDATE SET count=count+1', (key, now + self.WINDOW))
                connection.execute('COMMIT')
                return keys, retry
        except (OSError, sqlite3.Error, LoginThrottleUnavailable) as exc:
            raise LoginThrottleUnavailable() from exc

    def succeeded(self, keys):
        # Retain the address budget to limit credential spraying even when some
        # passwords succeed. Account/pair budgets reset after a valid login.
        try:
            with closing(self._connect()) as connection:
                connection.executemany('DELETE FROM attempts WHERE key = ?',
                                       [(keys['pair'],), (keys['account'],)])
        except (OSError, sqlite3.Error, LoginThrottleUnavailable):
            pass  # Never loosen the limit if persistence is unavailable.
