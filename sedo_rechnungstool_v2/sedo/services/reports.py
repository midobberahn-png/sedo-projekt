from collections import defaultdict
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from sedo.models import Invoice, InvoiceItem, Offer
from sedo.services.calc import offene_rechnungen


ZERO = Decimal('0.00')
MONTHS = ('Jan', 'Feb', 'Mär', 'Apr', 'Mai', 'Jun', 'Jul', 'Aug', 'Sep', 'Okt', 'Nov', 'Dez')


def _issued_invoices():
    return Invoice.objects.filter(lifecycle='issued').exclude(invoice_type='457')


def _money(value):
    return (value or ZERO).quantize(Decimal('0.01'))


def build_reports(year):
    """Berechnet die vier betriebswirtschaftlichen Übersichten ohne Schreibzugriffe."""
    issued = _issued_invoices()
    period = issued.filter(issue_date__year=year)

    customer_rows = list(
        period.values('customer__name').annotate(total=Sum('gross_total')).order_by('-total', 'customer__name')
    )
    customer_max = max((_money(row['total']) for row in customer_rows), default=ZERO)
    for row in customer_rows:
        row['total'] = _money(row['total'])
        row['percent'] = round(row['total'] / customer_max * 100) if customer_max else 0

    service_totals = defaultdict(lambda: ZERO)
    items = InvoiceItem.objects.filter(invoice__in=period).values_list(
        'leistungsart_text', 'description', 'quantity', 'unit_price',
    )
    for service, description, quantity, unit_price in items.iterator(chunk_size=2000):
        service_totals[(service or description or 'Ohne Bezeichnung').strip()] += quantity * unit_price
    service_rows = [
        {'name': name, 'total': _money(total)} for name, total in service_totals.items()
    ]
    service_rows.sort(key=lambda row: (-row['total'], row['name'].casefold()))
    service_max = max((row['total'] for row in service_rows), default=ZERO)
    for row in service_rows:
        row['percent'] = round(row['total'] / service_max * 100) if service_max else 0

    today = timezone.localdate()
    aging = [
        {'key': 'not_due', 'label': 'Noch nicht fällig', 'count': 0, 'total': ZERO},
        {'key': '1_30', 'label': '1–30 Tage', 'count': 0, 'total': ZERO},
        {'key': '31_60', 'label': '31–60 Tage', 'count': 0, 'total': ZERO},
        {'key': '61_90', 'label': '61–90 Tage', 'count': 0, 'total': ZERO},
        {'key': 'over_90', 'label': 'Über 90 Tage', 'count': 0, 'total': ZERO},
        {'key': 'no_due', 'label': 'Ohne Fälligkeit', 'count': 0, 'total': ZERO},
    ]
    aging_by_key = {row['key']: row for row in aging}
    for due_date, gross, paid in offene_rechnungen().values_list('due_date', 'gross_total', '_active_paid'):
        open_amount = _money(gross - paid)
        if not due_date:
            key = 'no_due'
        else:
            days = (today - due_date).days
            key = 'not_due' if days <= 0 else '1_30' if days <= 30 else '31_60' if days <= 60 else '61_90' if days <= 90 else 'over_90'
        aging_by_key[key]['count'] += 1
        aging_by_key[key]['total'] += open_amount
    aging_max = max((row['total'] for row in aging), default=ZERO)
    for row in aging:
        row['total'] = _money(row['total'])
        row['percent'] = round(row['total'] / aging_max * 100) if aging_max else 0

    offers = Offer.objects.filter(issue_date__year=year)
    accepted = offers.filter(status__in=('angenommen', 'in_rechnung')).count()
    rejected = offers.filter(status='abgelehnt').count()
    open_offers = offers.filter(status='offen').count()
    decided = accepted + rejected
    offer_quote = round(accepted / decided * 100) if decided else None

    monthly_by_year = {}
    for comparison_year in (year - 1, year):
        monthly_by_year[comparison_year] = {
            row['issue_date__month']: _money(row['total'])
            for row in issued.filter(issue_date__year=comparison_year)
            .values('issue_date__month').annotate(total=Sum('gross_total'))
        }
    monthly_max = max((value for data in monthly_by_year.values() for value in data.values()), default=ZERO)
    months = []
    for month, label in enumerate(MONTHS, start=1):
        current = monthly_by_year[year].get(month, ZERO)
        previous = monthly_by_year[year - 1].get(month, ZERO)
        months.append({
            'label': label, 'current': current, 'previous': previous,
            'current_percent': round(current / monthly_max * 100) if monthly_max else 0,
            'previous_percent': round(previous / monthly_max * 100) if monthly_max else 0,
        })

    revenue = _money(period.aggregate(total=Sum('gross_total'))['total'])
    return {
        'year': year, 'previous_year': year - 1, 'revenue': revenue,
        'customer_rows': customer_rows, 'service_rows': service_rows,
        'aging': aging, 'open_total': _money(sum((row['total'] for row in aging), ZERO)),
        'accepted_offers': accepted, 'rejected_offers': rejected, 'open_offers': open_offers,
        'offer_quote': offer_quote, 'months': months,
    }
