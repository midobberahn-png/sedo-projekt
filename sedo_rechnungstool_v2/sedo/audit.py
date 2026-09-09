from __future__ import annotations

import logging

from django.contrib import messages
from django.db import transaction
from django.db.utils import OperationalError, ProgrammingError
from django.utils import timezone

logger = logging.getLogger('sedo')

# Schlüssel für den Vermerk in AppSetting. Ohne LOGGING-Konfiguration
# (Review-Fund F-25) läuft der Logger im gebündelten Desktop-Betrieb ins
# Leere – dieser Vermerk ist dort der einzige sichtbare Kanal.
AUDIT_ERROR_KEY = 'last_audit_error'
AUDIT_ERROR_AT_KEY = 'last_audit_error_at'


class AuditWriteError(Exception):
    """Der Protokolleintrag konnte nicht geschrieben werden.

    Wird nur bei ``pflicht=True`` geworfen – dort, wo der Eintrag den Vorgang
    selbst belegt und nicht nachträglich rekonstruierbar wäre (Ausblenden von
    Rechnung, Angebot, Zahlung).
    """


def _client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '') if request else ''
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR') if request else None


def _json_sicher(metadata):
    """Macht die Metadaten JSON-tauglich.

    Ein nicht serialisierbarer Wert (etwa ein Decimal oder ein Model-Objekt)
    ließ den gesamten Eintrag scheitern – und das wurde bisher verschluckt.
    Lieber ein Eintrag mit gestutzten Metadaten als gar keiner.
    """
    if not isinstance(metadata, dict):
        return {} if metadata is None else {'wert': str(metadata)[:500]}
    sauber = {}
    for schluessel, wert in metadata.items():
        if isinstance(wert, (str, int, float, bool)) or wert is None:
            sauber[str(schluessel)] = wert
        else:
            sauber[str(schluessel)] = str(wert)[:500]
    return sauber


def _remember_audit_error(text: str) -> None:
    """Hält den letzten Protokollfehler für die Oberfläche fest.

    Läuft in einer eigenen Transaktion: Der Aufrufer steckt unter Umständen in
    einer bereits als fehlerhaft markierten Transaktion, dann wäre jeder
    weitere Schreibversuch dort ohnehin verloren. Darf selbst nicht werfen.
    """
    try:
        from .models import AppSetting
        with transaction.atomic():
            AppSetting.set(AUDIT_ERROR_KEY, str(text)[:500])
            AppSetting.set(AUDIT_ERROR_AT_KEY, timezone.localtime().isoformat())
    except Exception:
        logger.warning('Protokollfehler konnte nicht vermerkt werden.', exc_info=True)


def _melde_audit_fehler(request, action, object_repr, fehler, pflicht):
    text = (f'Protokolleintrag „{action}" für „{object_repr or "—"}" konnte nicht '
            f'geschrieben werden: {fehler}')
    logger.error(text, exc_info=True)
    _remember_audit_error(text)
    if request is not None:
        try:
            messages.error(request, text)
        except Exception:
            # Ohne Messages-Middleware (z. B. im Signal-Kontext) egal.
            pass
    if pflicht:
        raise AuditWriteError(text) from fehler


def write_audit(request, action: str, obj=None, message: str = '', metadata: dict | None = None,
                object_type: str = '', object_id: str = '', object_repr: str = '',
                pflicht: bool = False):
    """Schreibt einen Eintrag ins Audit-Log.

    Das Audit-Log ist der GoBD-Nachweis über Storno, Zahlungseingang,
    Ausblenden, Mahnungen und Einstellungsänderungen. Bisher verschluckte
    diese Funktion **jeden** Schreibfehler: kein Log, keine Meldung, kein
    Vermerk. Weil sie an den Ausblende-Stellen VOR der eigentlichen Aktion
    läuft, verschwand im Fehlerfall der Nachweis, während die Aktion trotzdem
    ausgeführt wurde.

    Jetzt drei Kanäle: Logger, Vermerk in ``AppSetting`` (in der Oberfläche
    sichtbar) und eine Meldung an den Nutzer.

    ``pflicht=True`` macht den Eintrag zur Bedingung: schlägt er fehl, wird
    ``AuditWriteError`` geworfen und der Aufrufer bricht ab. Das ist genau an
    den drei Ausblende-Stellen gesetzt. Überall sonst bleibt es bei
    ``pflicht=False`` – ein Anmeldevorgang darf nicht daran scheitern, dass
    sein Protokolleintrag klemmt.
    """
    try:
        from .models import AuditLog
        user = getattr(request, 'user', None)
        if not getattr(user, 'is_authenticated', False):
            user = None
        if obj is not None:
            object_type = object_type or obj.__class__.__name__
            object_id = object_id or str(getattr(obj, 'pk', '') or '')
            object_repr = object_repr or str(obj)
        # Savepoint: Scheitert der Insert innerhalb einer umgebenden
        # Transaktion, bleibt diese benutzbar – ohne ihn wäre sie als
        # fehlerhaft markiert und der Aufrufer könnte nicht sauber abbrechen.
        with transaction.atomic():
            AuditLog.objects.create(
                user=user,
                action=action,
                object_type=object_type or '',
                object_id=object_id or '',
                object_repr=(object_repr or '')[:255],
                message=message or '',
                ip_address=_client_ip(request),
                user_agent=(request.META.get('HTTP_USER_AGENT', '') if request else '')[:2000],
                metadata=_json_sicher(metadata),
            )
    except (OperationalError, ProgrammingError) as fehler:
        # Migration läuft noch oder die Tabelle fehlt – während der
        # Einrichtung normal, sonst ein echtes Problem.
        _melde_audit_fehler(request, action, object_repr, fehler, pflicht)
    except Exception as fehler:
        _melde_audit_fehler(request, action, object_repr, fehler, pflicht)
