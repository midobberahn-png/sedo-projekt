"""E-Mail: .eml-Entwürfe erzeugen und SMTP-Direktversand."""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from ..models import AppSetting

DEFAULT_SMTP_PORT = 587
DEFAULT_FROM_NAME = 'SEDO Objektdienstleistungen GmbH'
DEFAULT_FROM_ADDR = 'info@sedo-magdeburg.de'
SMTP_TIMEOUT_SECONDS = 20


def invoice_email_subject_body(invoice) -> tuple[str, str]:
    """Einheitlicher Betreff + Text für eine Rechnungs-E-Mail (früher 4× dupliziert)."""
    subject = f'Rechnung {invoice.number} der SEDO Objektdienstleistungen GmbH'
    body = (
        'Sehr geehrte Damen und Herren,\n\n'
        f'anbei erhalten Sie unsere Rechnung {invoice.number}.\n\n'
        f'Rechnungsbetrag: {invoice.gross_total} EUR\n'
        f'Fälligkeitsdatum: {invoice.due_date or "siehe Rechnung"}\n\n'
        'Mit freundlichen Grüßen\nSEDO Objektdienstleistungen GmbH\n'
    )
    return subject, body


def create_email_draft(recipient: str, subject: str, body: str,
                       attachment_path: Path | None, out_dir: Path, prefix: str) -> Path:
    """Schreibt einen E-Mail-Entwurf als .eml-Datei (kein Versand)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    msg = EmailMessage()
    msg['To'] = recipient or ''
    msg['From'] = DEFAULT_FROM_ADDR
    msg['Subject'] = subject
    msg.set_content(body)
    if attachment_path and attachment_path.exists():
        data = attachment_path.read_bytes()
        msg.add_attachment(data, maintype='application', subtype='pdf', filename=attachment_path.name)
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


def get_smtp_config() -> dict:
    """Liest die SMTP-Konfiguration aus den AppSettings."""
    return {
        'host': AppSetting.get('smtp_host', ''),
        'port': _smtp_port(),
        'user': AppSetting.get('smtp_user', ''),
        # Env-Variable (z. B. via .env) hat Vorrang – dann kein Passwort in der DB.
        'password': os.environ.get('SEDO_SMTP_PASSWORD', '') or AppSetting.get('smtp_password', ''),
        'from_name': AppSetting.get('smtp_from_name', DEFAULT_FROM_NAME),
        'tls': AppSetting.get('smtp_tls', '1') == '1',
    }


def smtp_is_configured() -> bool:
    """True, wenn Host und Benutzer konfiguriert sind."""
    cfg = get_smtp_config()
    return bool(cfg['host'] and cfg['user'])


def send_email_smtp(recipient: str, subject: str, body: str,
                    attachment_path: Path | None = None) -> None:
    """Sendet eine E-Mail direkt per SMTP.

    Wirft ``ValueError`` wenn SMTP nicht konfiguriert ist und
    ``smtplib.SMTPException`` bei Verbindungs-/Authentifizierungsfehlern.
    """
    cfg = get_smtp_config()
    if not cfg['host'] or not cfg['user']:
        raise ValueError(
            'SMTP nicht konfiguriert. Bitte Host und Benutzer in den Einstellungen hinterlegen.'
        )
    msg = MIMEMultipart()
    msg['From'] = f"{cfg['from_name']} <{cfg['user']}>"
    msg['To'] = recipient
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain', 'utf-8'))
    if attachment_path:
        p = Path(attachment_path)
        if p.exists():
            with open(p, 'rb') as fh:
                part = MIMEApplication(fh.read(), Name=p.name)
            part['Content-Disposition'] = f'attachment; filename="{p.name}"'
            msg.attach(part)
    if cfg['tls']:
        context = ssl.create_default_context()
        with smtplib.SMTP(cfg['host'], cfg['port'], timeout=SMTP_TIMEOUT_SECONDS) as server:
            server.ehlo()
            server.starttls(context=context)
            server.login(cfg['user'], cfg['password'])
            server.send_message(msg)
    else:
        with smtplib.SMTP_SSL(cfg['host'], cfg['port'], timeout=SMTP_TIMEOUT_SECONDS) as server:
            server.login(cfg['user'], cfg['password'])
            server.send_message(msg)
