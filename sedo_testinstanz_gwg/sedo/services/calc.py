"""Betrags- und Statusberechnung für Rechnungen und Angebote."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Sum
from django.utils import timezone

CENT = Decimal('0.01')


def _totals(items, vat_rate: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    """Netto, USt und Brutto aus Positionen berechnen.

    Jede Position wird einzeln kaufmännisch gerundet (konsistent mit
    ``rechnungslauf_core``), dann summiert.
    """
    net = sum(
        ((item.quantity * item.unit_price).quantize(CENT, rounding=ROUND_HALF_UP) for item in items),
        Decimal('0.00'),
    ).quantize(CENT, rounding=ROUND_HALF_UP)
    vat = (net * vat_rate / Decimal('100')).quantize(CENT, rounding=ROUND_HALF_UP)
    gross = (net + vat).quantize(CENT, rounding=ROUND_HALF_UP)
    return net, vat, gross


def recalc_invoice(invoice):
    """Setzt net_total/vat_amount/gross_total der Rechnung neu (ohne save)."""
    invoice.net_total, invoice.vat_amount, invoice.gross_total = _totals(
        invoice.items.all(), invoice.vat_rate
    )
    return invoice


def recalc_offer(offer):
    """Setzt net_total/vat_amount/gross_total des Angebots neu (ohne save)."""
    offer.net_total, offer.vat_amount, offer.gross_total = _totals(
        offer.items.all(), offer.vat_rate
    )
    return offer


def refresh_invoice_payment_status(invoice):
    """Aktualisiert den Rechnungsstatus anhand der zugeordneten Zahlungen."""
    if invoice.status == 'storniert':
        return invoice
    # Nur aktive (nicht soft-gelöschte) Zahlungen zählen.
    paid = invoice.payments.filter(deleted_at__isnull=True).aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
    today = timezone.localdate()
    if paid >= invoice.gross_total and invoice.gross_total != Decimal('0.00'):
        invoice.status = 'bezahlt'
    elif paid > Decimal('0.00'):
        invoice.status = 'teilbezahlt'
    elif invoice.dunning_documents.exists():
        invoice.status = 'gemahnt'
    elif invoice.due_date and invoice.due_date < today:
        invoice.status = 'ueberfaellig'
    elif invoice.status in ['bezahlt', 'teilbezahlt', 'ueberfaellig', 'gemahnt']:
        invoice.status = 'erstellt'
    invoice.save(update_fields=['status', 'updated_at'])
    return invoice
