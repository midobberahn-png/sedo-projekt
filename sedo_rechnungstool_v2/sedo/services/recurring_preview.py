from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sedo.models import Invoice, RecurringInvoiceTemplate


CENT = Decimal('0.01')


def _shift_month(value: date, offset: int) -> date:
    index = value.year * 12 + value.month - 1 + offset
    year, month_index = divmod(index, 12)
    month = month_index + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def build_recurring_preview(issue_date: date, months: int = 3) -> list[dict]:
    """Berechnet kommende Abo-Läufe ohne Datenbankänderung."""
    templates = list(
        RecurringInvoiceTemplate.objects.filter(status='active')
        .select_related('customer', 'obj').prefetch_related('items')
    )
    preview_periods = []
    for offset in range(months):
        shifted = _shift_month(issue_date, offset)
        preview_periods.append((
            shifted.replace(day=1),
            date(shifted.year, shifted.month, monthrange(shifted.year, shifted.month)[1]),
        ))
    existing = set(
        Invoice.objects.filter(source='Abo Monatsrechnung', period_start__in=[p[0] for p in preview_periods])
        .exclude(lifecycle='cancelled')
        .values_list('customer_id', 'obj_id', 'period_start', 'period_end')
    )
    result = []
    for offset in range(months):
        planned_date = _shift_month(issue_date, offset)
        period_start = planned_date.replace(day=1)
        period_end = date(planned_date.year, planned_date.month, monthrange(planned_date.year, planned_date.month)[1])
        rows = []
        month_total = Decimal('0.00')
        for template in templates:
            net = sum((item.line_total for item in template.items.all()), Decimal('0.00'))
            vat = (net * template.vat_rate / Decimal('100')).quantize(CENT, rounding=ROUND_HALF_UP)
            gross = net + vat
            exists = (template.customer_id, template.obj_id, period_start, period_end) in existing
            if not exists:
                month_total += gross
            rows.append({
                'template': template, 'gross': gross, 'exists': exists,
                'due_date': planned_date + timedelta(days=(
                    template.payment_days if template.payment_days is not None
                    else template.customer.payment_days if template.customer.payment_days is not None
                    else 14
                )),
            })
        result.append({
            'period_start': period_start, 'issue_date': planned_date,
            'rows': rows, 'total': month_total,
            'pending_count': sum(1 for row in rows if not row['exists']),
        })
    return result
