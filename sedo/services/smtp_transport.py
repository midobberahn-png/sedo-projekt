"""Gemeinsamer Transportvertrag für Rechnungs- und Postfachversand."""

import smtplib
import ssl
from enum import Enum

import certifi


class DeliveryOutcome(Enum):
    REJECTED = 'rejected'
    UNKNOWN = 'unknown'
    ACCEPTED = 'accepted'


def verified_tls_context() -> ssl.SSLContext:
    """Prüft Host und Zertifikat, auch im gebündelten Desktopprogramm."""
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    return context


def classify_failure(exc: BaseException, *, data_may_have_started: bool,
                     accepted: bool) -> DeliveryOutcome:
    """Nach SMTP-Antwort und Übertragungsphase über erneutes Senden entscheiden.

    Nach einer bestätigten DATA-Annahme bleibt der Versand erfolgreich, auch wenn
    später die Verbindung oder lokale Ablage scheitert. Eine ausdrückliche
    SMTP-Ablehnung ist wiederholbar; ein Abbruch an der DATA-Grenze nicht.
    """
    if accepted:
        return DeliveryOutcome.ACCEPTED
    if data_may_have_started and not isinstance(exc, (
        smtplib.SMTPDataError, smtplib.SMTPRecipientsRefused,
        smtplib.SMTPSenderRefused,
    )):
        return DeliveryOutcome.UNKNOWN
    return DeliveryOutcome.REJECTED
