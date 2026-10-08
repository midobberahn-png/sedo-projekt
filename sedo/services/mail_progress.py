"""Transient progress for the desktop worker; no mail contents or credentials."""
import threading
import time

_lock = threading.Lock()
_running = {}


def begin(account_id):
    with _lock:
        _running[account_id] = {'updated': time.monotonic(), 'folders': {}}


def update(account_id, folder_id, loaded, total):
    with _lock:
        if account_id in _running:
            _running[account_id]['folders'][folder_id] = (min(loaded, total), total)
            _running[account_id]['updated'] = time.monotonic()


def finish(account_id):
    with _lock:
        _running.pop(account_id, None)


def snapshot(account_id):
    with _lock:
        state = _running.get(account_id)
        if state and time.monotonic() - state['updated'] < 300:
            return dict(state['folders'])
    return None
