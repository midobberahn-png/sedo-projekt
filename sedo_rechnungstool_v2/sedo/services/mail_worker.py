"""One small worker for the running desktop process; no network from AppConfig."""
import logging
import threading
from datetime import timedelta

from django.db import close_old_connections
from django.db.models import Q
from django.utils import timezone

from sedo.models import MailAccount, MailDraft
from .mail_imap import MailBusy, sync_account
from .mail_secrets import MailError

logger = logging.getLogger('sedo')
_guard = threading.Lock()
_active = set()
_worker = None
_wake = threading.Event()
POLL_SECONDS = 300


def recover_interrupted_sends():
    # A process can disappear after SMTP accepted DATA and before writing the result.
    stale = timezone.now() - timedelta(minutes=5)
    MailDraft.objects.filter(state='sending', updated_at__lt=stale).filter(
        Q(account__lease_until__lt=timezone.now()) | Q(account__lease_until__isnull=True)).update(state='unknown',
            error='SEDO wurde während des Versands beendet. Bitte den Versandstatus im Webmail prüfen; es erfolgt kein automatischer Neuversand.')


def _sync(pk):
    close_old_connections()
    try:
        sync_account(pk)
    except MailBusy:
        pass
    except MailError:
        pass  # already persisted as a safe account.last_error
    except Exception:
        # No exception text/traceback: an IMAP response could contain credentials.
        logger.error('Postfachabgleich konnte nicht abgeschlossen werden (Konto %s).', pk)
        MailAccount.objects.filter(pk=pk).update(last_error='Der Abgleich wurde unterbrochen. Der nächste Versuch erfolgt automatisch.')
    finally:
        close_old_connections()
        with _guard:
            _active.discard(pk)


def request_sync(pk):
    with _guard:
        if pk in _active:
            return False
        _active.add(pk)
    threading.Thread(target=_sync, args=(pk,), name=f'sedo-mail-{pk}', daemon=True).start()
    return True


def start_mail_worker():
    global _worker
    if _worker and _worker.is_alive():
        return
    def loop():
        while True:
            close_old_connections()
            try:
                recover_interrupted_sends()
                for pk in MailAccount.objects.filter(enabled=True).exclude(password_encrypted='').values_list('pk', flat=True):
                    request_sync(pk)
            except Exception:
                logger.error('E-Mail-Hintergrundabruf pausiert; bitte Datenbank prüfen.')
            finally:
                close_old_connections()
            _wake.wait(POLL_SECONDS)
            _wake.clear()
    _worker = threading.Thread(target=loop, name='sedo-mail-poll', daemon=True)
    _worker.start()
