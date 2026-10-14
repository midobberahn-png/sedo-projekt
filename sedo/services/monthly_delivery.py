"""E-Mail-Entwurf für einen geprüften Monatsrechnungsstapel."""
from email.message import EmailMessage
from email.utils import formataddr
from io import BytesIO
from pathlib import Path
import uuid
import zipfile
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from ..audit import write_audit
from ..company_profile import load_profile
from . import WorkflowError
from .invoice_archive import lese_archivdatei
from .invoice_delivery import ensure_invoice_zugferd_for_email as _ensure_invoice_zugferd_for_email
from .tax_export import parse_month


def monthly_zip_email(invoices, customer, month, request=None):
    """Erzeugt einen E-Mail-Entwurf nur mit vollständiger Auswahl gespeicherter Originale."""
    if not invoices or any(invoice.customer_id != customer.pk for invoice in invoices):
        raise WorkflowError('Für einen Monats-E-Mail-Entwurf müssen alle Rechnungen '
                            'zum selben Kunden gehören.')
    year, month_number = parse_month(month or timezone.localdate().strftime('%Y-%m'))
    safe_month = f'{year:04d}-{month_number:02d}'
    for invoice in invoices:
        _ensure_invoice_zugferd_for_email(invoice)
    attachments = [lese_archivdatei(inv) for inv in invoices]
    profile = load_profile()
    zip_bytes = BytesIO()
    with zipfile.ZipFile(zip_bytes, 'w', zipfile.ZIP_DEFLATED) as zf:
        for attachment in attachments:
            zf.writestr(attachment.name, attachment.data)
    msg = EmailMessage()
    msg['To'] = customer.email or ''
    msg['From'] = formataddr((profile['name'], profile['email']))
    msg['Subject'] = f'Monatsrechnungen {safe_month} - {profile["name"]}'
    body = (
        f'Sehr geehrte Damen und Herren,\n\n'
        f'anbei übersenden wir Ihnen die Monatsrechnungen für {safe_month} als ZIP-Datei.\n'
        f'Enthaltene Rechnungen: {len(invoices)}\n\n'
        f'Mit freundlichen Grüßen\n{profile["name"]}\n'
    )
    msg.set_content(body)
    msg.add_attachment(zip_bytes.getvalue(), maintype='application', subtype='zip',
                       filename=f'Monatsrechnungen_{safe_month}.zip')
    out_dir = Path(settings.MEDIA_ROOT) / 'monthly_emails'
    out_dir.mkdir(parents=True, exist_ok=True)
    eml_path = out_dir / f'Monatsrechnungen_{safe_month}_{uuid.uuid4().hex}.eml'
    with eml_path.open('xb') as output:
        output.write(msg.as_bytes())
    with transaction.atomic():
        for invoice in invoices:
            write_audit(request, 'generate', invoice,
                        f'Monats-E-Mail-Entwurf für Rechnung {invoice.number} erzeugt',
                        metadata={'vorgang': 'eml_entwurf', 'weg': 'eml',
                                  'monat': safe_month}, pflicht=True)
    return eml_path
