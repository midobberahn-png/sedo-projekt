"""Betrags- und Statusberechnung für Rechnungen und Angebote."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from django.db.models import DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce
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


def abgeleiteter_zahlstatus(invoice):
    """Berechnet den Zahlstatus aus Zahlungen, Mahnungen und Fälligkeit.

    Reine Funktion – sie schreibt nichts. Gibt ``None`` zurück, wenn der
    aktuelle Status bereits stimmt oder sich kein Zahlstatus ableiten lässt.

    Der Zahlstatus ist vollständig aus anderen Daten reproduzierbar; genau
    deshalb darf er den Belegzustand nie überschreiben (F-07).
    """
    # Nur aktive (nicht soft-gelöschte) Zahlungen zählen.
    paid = invoice.payments.filter(deleted_at__isnull=True).aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
    today = timezone.localdate()
    if invoice.lifecycle != 'issued' or invoice.invoice_type == '457':
        return None
    gross = Decimal(str(invoice.gross_total))
    if paid >= gross and gross > Decimal('0.00'):
        neu = 'bezahlt'
    elif paid > Decimal('0.00'):
        neu = 'teilbezahlt'
    elif invoice.dunning_documents.exists():
        neu = 'gemahnt'
    elif invoice.due_date and invoice.due_date < today:
        neu = 'ueberfaellig'
    else:
        neu = 'versendet' if invoice.status == 'versendet' else 'erstellt'
    return neu if neu != invoice.status else None


def refresh_invoice_payment_status(invoice):
    """Aktualisiert den Zahlstatus – und lässt den Belegzustand in Ruhe.

    Vorher stand hier nur ein früher Return für 'storniert'. Damit konnte ein
    einziger Blick auf „Offene Posten" einen Entwurf oder ein durch eine
    Korrektur ersetztes Original auf 'ueberfaellig' setzen: die Storno-Sperre
    fiel, der Beleg wurde Mahnkandidat, und Dashboard wie Offene Posten
    zählten Original und Korrektur beide als offene Forderung (F-07).

    Zusätzlich wird nur noch geschrieben, wenn sich der Wert wirklich ändert.
    Bisher setzte jeder Aufruf ein UPDATE ab – bei bis zu 500 Rechnungen je
    Seitenaufruf.
    """
    from ..models import Invoice as _Invoice
    if invoice.lifecycle != 'issued' or invoice.invoice_type == '457':
        return invoice
    neu = abgeleiteter_zahlstatus(invoice)
    if neu:
        invoice.status = neu
        invoice.save(update_fields=['status', 'updated_at'])
    return invoice


def offene_rechnungen(basis=None):
    """Rechnungen, die eine offene Forderung darstellen.

    Ersetzt die verstreuten ``exclude(status__in=['bezahlt','storniert'])`` in
    Dashboard und Offenen Posten. Neu ausgeschlossen: Entwürfe, ersetzte
    Originale und Stornobelege – sie wurden bisher als Forderung mitgezählt,
    das Original zusätzlich zu seiner Korrektur (F-07, F-60).

    Deutscher Name mit Absicht: In ``views.py`` gibt es bereits eine lokale
    Variable und einen Kontextschlüssel namens ``open_invoices``; ein
    gleichnamiger Import erzeugte dort einen UnboundLocalError.
    """
    from ..models import Invoice as _Invoice
    basis = _Invoice.objects if basis is None else basis
    from .invoice_state import issued_claims
    basis = issued_claims(basis)
    paid = Coalesce(
        Sum('payments__amount', filter=Q(payments__deleted_at__isnull=True)),
        Value(Decimal('0.00')),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    return basis.annotate(_active_paid=paid).filter(gross_total__gt=F('_active_paid'))


def zuordenbare_rechnungen(basis=None):
    """Rechnungen, denen sich eine Zahlung zuordnen lässt.

    'bezahlt' bleibt hier bewusst enthalten – sonst wäre keine Nach- oder
    Überzahlung mehr buchbar. Ausgeschlossen sind nur die Belegzustände: auf
    einen Entwurf, ein ersetztes Original oder einen Stornobeleg gehört keine
    Zahlung.
    """
    from ..models import Invoice as _Invoice
    basis = _Invoice.objects if basis is None else basis
    from .invoice_state import payment_assignable
    return payment_assignable(basis)
