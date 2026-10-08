"""E-Mail: .eml-Entwürfe erzeugen und SMTP-Direktversand."""
from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from ..models import AppSetting
from ..company_profile import SELLER, SellerProfileError, seller_document
from .credentials import configuration, secret_for

DEFAULT_SMTP_PORT = 587
DEFAULT_FROM_ADDR = ''
SMTP_TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class EmailAttachment:
    """Einmal gelesene Originalbytes, auch nach der Archivprüfung unveränderlich."""
    name: str
    data: bytes


def _attachment(attachment_path):
    if isinstance(attachment_path, EmailAttachment):
        return attachment_path
    if attachment_path is None:
        return None
    path = Path(attachment_path)
    # Eine ausdrücklich verlangte Anlage darf nicht stillschweigend entfallen.
    return EmailAttachment(path.name, path.read_bytes())


@seller_document
def invoice_email_subject_body(invoice) -> tuple[str, str]:
    """Einheitlicher Betreff + Text für eine Rechnungs-E-Mail (früher 4× dupliziert)."""
    subject = f'Rechnung {invoice.number} – {SELLER["name"]}'
    body = (
        'Sehr geehrte Damen und Herren,\n\n'
        f'anbei erhalten Sie unsere Rechnung {invoice.number}.\n\n'
        f'Rechnungsbetrag: {invoice.gross_total} EUR\n'
        f'Fälligkeitsdatum: {invoice.due_date or "siehe Rechnung"}\n\n'
        f'Mit freundlichen Grüßen\n{SELLER["name"]}\n'
    )
    return subject, body


@seller_document
def create_email_draft(recipient: str, subject: str, body: str,
                       attachment_path: Path | EmailAttachment | None, out_dir: Path, prefix: str) -> Path:
    """Schreibt einen E-Mail-Entwurf als .eml-Datei (kein Versand)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    msg = EmailMessage()
    msg['To'] = recipient or ''
    msg['From'] = formataddr((SELLER['name'], SELLER['email']))
    msg['Subject'] = subject
    msg.set_content(body)
    attachment = _attachment(attachment_path)
    if attachment is not None:
        msg.add_attachment(attachment.data, maintype='application', subtype='pdf', filename=attachment.name)
    eml_path = out_dir / f'{prefix}.eml'
    eml_path.write_bytes(msg.as_bytes())
    return eml_path


def _smtp_port() -> int:
    """SMTP-Port aus den Einstellungen; fällt bei ungültigem Wert auf 587 zurück."""
    raw = (AppSetting.get('smtp_port', '') or '').strip()
    try:
        return int(raw) if raw else DEFAULT_SMTP_PORT
    except ValueError:
        return DEFAULT_SMTP_PORT


def _tls_context() -> ssl.SSLContext:
    """Verifizierender TLS-Kontext für beide Versandwege.

    ``ssl.create_default_context()`` prüft Zertifikatskette UND Hostnamen.
    Ohne mitgegebenen Kontext baut ``smtplib.SMTP_SSL`` intern selbst einen
    auf – ohne jede Prüfung (CERT_NONE, check_hostname=False). Ein
    umgeleiteter Mailserver bekäme dann Benutzer und Passwort im Klartext
    (Review-Fund F-09).
    """
    return ssl.create_default_context()


def get_smtp_config() -> dict:
    """Metadata only: rendering settings must never expose or decrypt a password."""
    cfg = configuration('smtp')
    if not cfg['from_name'].strip():
        try:
            cfg['from_name'] = SELLER['name']
        except SellerProfileError:
            # Settings and recovery remain readable before company setup.
            cfg['from_name'] = ''
    try:
        port = int(cfg['port'])
    except ValueError:
        port = DEFAULT_SMTP_PORT
    return {**cfg, 'port': port, 'tls': cfg['tls'] == '1', 'password': ''}


def smtp_is_configured() -> bool:
    """True, wenn Host und Benutzer konfiguriert sind."""
    cfg = get_smtp_config()
    return bool(cfg['host'] and cfg['user'])


def send_email_smtp(recipient: str, subject: str, body: str,
                    attachment_path: Path | EmailAttachment | None = None) -> None:
    """Sendet eine E-Mail direkt per SMTP.

    Beide Wege – STARTTLS (Port 587) und implizites TLS (Port 465) – prüfen
    das Zertifikat des Mailservers. Ein Server, dessen Identität nicht belegt
    ist, bekommt Benutzer und Passwort nicht zu sehen.

    Wirft ``ValueError`` wenn SMTP nicht konfiguriert ist und
    ``smtplib.SMTPException`` bei Authentifizierungs- und Zertifikatsfehlern.
    Reine Verbindungsfehler (Host unbekannt, Port zu, Zeitüberschreitung)
    reicht ``smtplib`` unverändert als ``OSError`` durch.
    """
    cfg = get_smtp_config()
    if not cfg['host'] or not cfg['user']:
        raise ValueError(
            'SMTP nicht konfiguriert. Bitte Host und Benutzer in den Einstellungen hinterlegen.'
        )
    cfg['password'] = secret_for('smtp', cfg)
    if not cfg['password']:
        raise ValueError('Bitte das SMTP-Passwort in den Einstellungen neu eingeben.')
    msg = MIMEMultipart()
    msg['From'] = formataddr((cfg['from_name'], cfg['user']))
    msg['To'] = recipient
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain', 'utf-8'))
    attachment = _attachment(attachment_path)
    if attachment is not None:
        part = MIMEApplication(attachment.data, Name=attachment.name)
        part.add_header('Content-Disposition', 'attachment', filename=attachment.name)
        msg.attach(part)
    # Ein gemeinsamer, prüfender Kontext für beide Wege – kein Zweig darf
    # ungeprüft senden (F-09).
    context = _tls_context()
    try:
        if cfg['tls']:
            with smtplib.SMTP(cfg['host'], cfg['port'], timeout=SMTP_TIMEOUT_SECONDS) as server:
                server.ehlo()
                server.starttls(context=context)
                server.login(cfg['user'], cfg['password'])
                server.send_message(msg)
        else:
            # Implizites TLS (Port 465): context ist hier Pflicht. Ohne ihn
            # akzeptiert smtplib jedes beliebige Zertifikat – und server.login()
            # schickt Benutzer und Passwort trotzdem los.
            with smtplib.SMTP_SSL(cfg['host'], cfg['port'], timeout=SMTP_TIMEOUT_SECONDS,
                                  context=context) as server:
                server.login(cfg['user'], cfg['password'])
                server.send_message(msg)
    except ssl.SSLCertVerificationError as exc:
        # Klartext statt OpenSSL-Meldung: Dieser Text landet 1:1 in der
        # Oberfläche (Rechnungsversand und SMTP-Test in views.py).
        #
        # Die Umwandlung ist Pflicht, nicht Kosmetik: ssl.SSLCertVerificationError
        # erbt von SSLError UND von ValueError. Ohne sie käme ein Zertifikatsfehler
        # als ValueError an und wäre von „SMTP nicht konfiguriert" (oben) nicht
        # mehr zu unterscheiden.
        #
        # Grenze: Im STARTTLS-Zweig kann smtplib beim Verlassen des with-Blocks
        # (QUIT über den halb toten Socket) einen anderen OSError nachschieben,
        # der hier vorbeiläuft. Im SSL-Zweig – dem Zweig dieses Fundes – entsteht
        # der Fehler schon im Konstruktor, dort greift es zuverlässig.
        raise smtplib.SMTPException(
            f'Das Zertifikat des Mailservers {cfg["host"]} konnte nicht geprüft werden. '
            'SEDO sendet Benutzer und Passwort nicht an einen Server, dessen Identität '
            'nicht belegt ist. Bitte Host, Port und die STARTTLS-Einstellung prüfen.'
        ) from exc
    except smtplib.SMTPException:
        # A remote SMTP server can echo supplied credentials in its error text.
        raise smtplib.SMTPException('Der SMTP-Versand ist fehlgeschlagen. Bitte Zugang und Empfänger prüfen.') from None
