"""Gemeinsame Versandvoraussetzung für einzelne und gebündelte Rechnungen."""

from ..models import Invoice
from .invoices import WorkflowError
from .zugferd_validation import validate_invoice_zugferd


def ensure_invoice_zugferd_for_email(invoice: Invoice) -> None:
    """Prüft archivierte E-Rechnungen vor jedem Einzel- und Sammelversandweg."""
    if invoice.xml_file and invoice.zugferd_file:
        validation = validate_invoice_zugferd(invoice)
        if not validation.ok:
            raise WorkflowError(f'Rechnung {invoice.number}: ZUGFeRD-Prüfung fehlgeschlagen. '
                                'Bitte den Prüfbericht öffnen.')
