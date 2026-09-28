"""Persistent, bounded login admission control for both Django login screens.

Five attempts per normalized account / five minutes, plus a larger source
budget against spraying. Slots are reserved under BEGIN IMMEDIATE before
password verification; parallel requests cannot all pass a stale count.
Passwords and cleartext usernames/IPs are never stored. Proxy headers are
not trusted. A broken store fails closed rather than disabling the control.
"""
from __future__ import annotations

import hashlib
import hmac
import math
import os
from pathlib import Path
import sqlite3
import time
import unicodedata

from django.conf import settings
from django.contrib.auth.signals import user_logged_in
from django.contrib.auth.views import LoginView
from django.dispatch import receiver
from django.http import HttpResponse
from django.utils.deprecation import MiddlewareMixin

MAX_ROWS = 10000


@receiver(user_logged_in, dispatch_uid='sedo.throttle.success')
def _login_success(sender, request, **kwargs):
    if request is not None:
        request._sedo_login_succeeded = True


def _key(kind: str, value: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), (kind+'\0'+value).encode(), hashlib.sha256).hexdigest()


def _limits():
    # No zero/negative "disable" setting. These bounds also limit DoS exposure.
    window = min(3600, max(30, int(getattr(settings, 'SEDO_LOGIN_WINDOW', 300))))
    account = min(20, max(3, int(getattr(settings, 'SEDO_LOGIN_ATTEMPTS', 5))))
    source = min(2000, max(50, int(getattr(settings, 'SEDO_LOGIN_SOURCE_ATTEMPTS', 100))))
    return window, account, source


def _connect():
    directory = Path(settings.SEDO_HOME) / 'security-state'
    path = directory / 'login.sqlite3'
    if directory.is_symlink() or path.is_symlink():
        raise OSError('Unsafe security state path')
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    except FileExistsError:
        pass
    else:
        os.close(fd)
    if os.name == 'posix':
        os.chmod(directory, 0o700)
        os.chmod(path, 0o600)
    db = sqlite3.connect(path, timeout=1, isolation_level=None)
    try:
        db.execute('CREATE TABLE IF NOT EXISTS attempts (key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires REAL NOT NULL)')
        db.execute('CREATE INDEX IF NOT EXISTS attempts_expiry ON attempts(expires)')
    except BaseException:
        db.close()
        raise
    return db


def reserve(username: str, remote: str, now=None) -> tuple[int, str, float]:
    now = time.time() if now is None else now
    window, account_limit, source_limit = _limits()
    username = unicodedata.normalize('NFKC', username[:1024]).casefold().strip()
    account_key = _key('account', username)
    source_key = _key('source', remote[:200])
    db = _connect()
    try:
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM attempts WHERE expires <= ?', (now,))
        rows = dict((key, (count, expires)) for key, count, expires in db.execute(
            'SELECT key, count, expires FROM attempts WHERE key IN (?, ?)', (source_key, account_key)))
        retry = 0
        for key, limit in ((source_key, source_limit), (account_key, account_limit)):
            count, expires = rows.get(key, (0, now+window))
            if count >= limit:
                retry = max(retry, max(1, math.ceil(expires-now)))
        if retry:
            db.execute('COMMIT')
            return retry, account_key, rows.get(account_key, (0, now+window))[1]
        if db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0] + 2 > MAX_ROWS:
            raise sqlite3.OperationalError('Admission store capacity reached')
        for key in (source_key, account_key):
            count, expires = rows.get(key, (0, now+window))
            db.execute('INSERT INTO attempts(key,count,expires) VALUES(?,?,?) '
                       'ON CONFLICT(key) DO UPDATE SET count=excluded.count, expires=excluded.expires',
                       (key, count+1, expires))
        db.execute('COMMIT')
        return 0, account_key, rows.get(account_key, (0, now+window))[1]
    finally:
        db.close()  # sqlite rolls back unfinished transactions.


def reset_success(account_key: str, expires: float):
    db = _connect()
    try:
        # Do not reset a newer window. Keep the source budget, even on success.
        db.execute('DELETE FROM attempts WHERE key=? AND expires=?', (account_key, expires))
    finally:
        db.close()


def _denied(status: int, retry: int):
    text = ('Zu viele Anmeldeversuche. Bitte später erneut versuchen.' if status == 429 else
            'Anmeldung vorübergehend nicht verfügbar. Bitte später erneut versuchen.')
    response = HttpResponse(text, status=status, content_type='text/plain; charset=utf-8')
    response['Retry-After'] = str(retry)
    response['Cache-Control'] = 'no-store'
    return response


class LoginThrottleMiddleware(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        cls = getattr(view_func, 'view_class', None)
        match = request.resolver_match
        is_admin_login = bool(match and 'admin' in match.app_names and match.url_name == 'login')
        is_login_view = isinstance(cls, type) and issubclass(cls, LoginView)
        if request.method != 'POST' or not (is_login_view or is_admin_login):
            return None
        try:
            retry, key, expires = reserve(request.POST.get('username', ''), request.META.get('REMOTE_ADDR', ''))
        except (OSError, sqlite3.Error, ValueError):
            return _denied(503, 30)
        if retry:
            return _denied(429, retry)
        request._sedo_login_reservation = (key, expires)
        return None

    def process_response(self, request, response):
        reservation = getattr(request, '_sedo_login_reservation', None)
        if reservation and getattr(request, '_sedo_login_succeeded', False):
            try:
                reset_success(*reservation)
            except (OSError, sqlite3.Error):
                # The successful session exists; retaining the stricter counter
                # is safe and preferable to weakening the admission control.
                pass
        return response
