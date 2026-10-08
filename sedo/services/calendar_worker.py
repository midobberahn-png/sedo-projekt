"""Desktop-only polling, no network during Django setup or page rendering."""
import logging
import threading

from django.db import close_old_connections

from sedo.models import CalendarConnection
from .calendar_data import CalendarError
from .calendar_sync import sync_connection

_guard = threading.Lock()
_active = set()
_worker = None
logger = logging.getLogger('sedo')


def _sync(pk):
    close_old_connections()
    try:
        sync_connection(pk)
    except CalendarError:
        pass
    except Exception:
        logger.error('Kalenderabgleich unterbrochen (Verbindung %s).', pk)
        CalendarConnection.objects.filter(pk=pk).update(last_error='Der Kalenderabgleich wurde unterbrochen. Bitte erneut versuchen.')
    finally:
        close_old_connections()
        with _guard:
            _active.discard(pk)


def request_sync(pk):
    with _guard:
        if pk in _active:
            return False
        _active.add(pk)
    threading.Thread(target=_sync, args=(pk,), name=f'sedo-calendar-{pk}', daemon=True).start()
    return True


def start_calendar_worker():
    global _worker
    if _worker and _worker.is_alive():
        return
    def loop():
        while True:
            close_old_connections()
            try:
                for pk in CalendarConnection.objects.filter(enabled=True).exclude(password_encrypted='').values_list('pk', flat=True):
                    request_sync(pk)
            except Exception:
                logger.error('Kalender-Hintergrundabgleich pausiert; bitte Datenbank prüfen.')
            finally:
                close_old_connections()
            threading.Event().wait(120)
    _worker = threading.Thread(target=loop, name='sedo-calendar-poll', daemon=True)
    _worker.start()
