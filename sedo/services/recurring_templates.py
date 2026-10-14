"""Zahlungsziel und Übernahme einer Rechnung als Monatsvorlage."""

from ..models import Invoice, RecurringInvoiceItem, RecurringInvoiceTemplate


DEFAULT_PAYMENT_DAYS = 14


def recurring_payment_days(payment_days: int | None,
                           customer_payment_days: int | None) -> int:
    """Vorlage/Rechnung vor Kundenwert; nur None fehlt, 0 bedeutet sofort fällig."""
    if payment_days is not None:
        return payment_days
    if customer_payment_days is not None:
        return customer_payment_days
    return DEFAULT_PAYMENT_DAYS


def create_recurring_template_from_invoice(inv: Invoice,
                                           title: str | None = None):
    """Speichert eine erzeugte Rechnung als Abo-/Monatsrechnungsvorlage."""
    tmpl = RecurringInvoiceTemplate.objects.create(
        customer=inv.customer,
        obj=inv.obj,
        title=title or f'Monatsrechnung {inv.customer.name}' + (f' · {inv.obj.name}' if inv.obj else ''),
        status='active',
        vat_rate=inv.vat_rate,
        payment_days=recurring_payment_days(
            (inv.due_date - inv.issue_date).days if inv.due_date is not None else None,
            inv.customer.payment_days,
        ),
        note=inv.note or '',
    )
    for idx, item in enumerate(inv.items.all(), start=1):
        RecurringInvoiceItem.objects.create(
            template=tmpl,
            position=idx,
            description=item.description,
            long_text=item.long_text,
            quantity=item.quantity,
            unit=item.unit,
            unit_price=item.unit_price,
            article_no=item.article_no,
        )
    return tmpl
