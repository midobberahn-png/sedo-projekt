from datetime import datetime, time

from django.db.models import Prefetch, Q
from django.urls import reverse
from django.utils import timezone

from sedo.models import AuditLog, CustomerNote, DunningDocument, Invoice, Offer, Payment


KINDS = (
    ('all', 'Alles'), ('note', 'Notizen'), ('invoice', 'Rechnungen'),
    ('offer', 'Angebote'), ('payment', 'Zahlungen'),
    ('dunning', 'Mahnungen'), ('email', 'E-Mails'),
)


def _business_time(value):
    """Macht aus einem Geschäftsdatum einen stabil sortierbaren Zeitpunkt."""
    return timezone.make_aware(datetime.combine(value, time(hour=12)))


def _entry(kind, occurred_at, title, detail='', *, link='', amount=None, actor='', show_time=False):
    return {
        'kind': kind, 'occurred_at': occurred_at, 'title': title, 'detail': detail,
        'link': link, 'amount': amount, 'has_amount': amount is not None, 'actor': actor,
        'show_time': show_time,
    }


def build_customer_timeline(customer, kind='all'):
    """Führt vorhandene Kundenvorgänge zu einer rein lesenden Chronik zusammen."""
    if kind not in dict(KINDS):
        kind = 'all'
    entries = []
    include = lambda value: kind in ('all', value)

    invoices = list(Invoice.objects.filter(customer=customer).only(
        'id', 'number', 'issue_date', 'due_date', 'gross_total', 'lifecycle', 'status', 'invoice_type',
    ).prefetch_related(
        Prefetch('payments', queryset=Payment.objects.filter(deleted_at__isnull=True),
                 to_attr='_timeline_active_payments'), 'dunning_documents'))
    offers = list(Offer.objects.filter(customer=customer).only(
        'id', 'number', 'issue_date', 'gross_total', 'status',
    ))
    invoice_by_id = {item.pk: item for item in invoices}
    offer_by_id = {item.pk: item for item in offers}

    if include('invoice'):
        for invoice in invoices:
            entries.append(_entry(
                'invoice', _business_time(invoice.issue_date),
                f'Rechnung {invoice.number}',
                f'{invoice.get_invoice_type_display()} · {invoice.display_state_label}',
                link=reverse('sedo:invoice_detail', args=[invoice.pk]), amount=invoice.gross_total,
            ))
    if include('offer'):
        for offer in offers:
            entries.append(_entry(
                'offer', _business_time(offer.issue_date), f'Angebot {offer.number}',
                offer.get_status_display(), link=reverse('sedo:offer_detail', args=[offer.pk]),
                amount=offer.gross_total,
            ))
    if include('payment') and invoice_by_id:
        payments = Payment.objects.filter(
            invoice_id__in=invoice_by_id, invoice__deleted_at__isnull=True,
        ).select_related('invoice').only('booking_date', 'amount', 'purpose', 'invoice__id', 'invoice__number')
        for payment in payments:
            entries.append(_entry(
                'payment', _business_time(payment.booking_date),
                f'Zahlung zu {payment.invoice.number}', payment.purpose,
                link=reverse('sedo:invoice_detail', args=[payment.invoice_id]), amount=payment.amount,
            ))
    if include('dunning') and invoice_by_id:
        documents = DunningDocument.objects.filter(invoice_id__in=invoice_by_id).select_related('invoice')
        for document in documents:
            entries.append(_entry(
                'dunning', _business_time(document.document_date), document.get_kind_display(),
                f'Zu Rechnung {document.invoice.number}',
                link=reverse('sedo:invoice_detail', args=[document.invoice_id]),
                amount=document.amount_open,
            ))
    if include('note'):
        for note in CustomerNote.objects.filter(customer=customer).select_related('author'):
            entries.append(_entry(
                'note', note.created_at, 'Notiz', note.text,
                actor=note.author.get_username() if note.author_id else 'System', show_time=True,
            ))
    if include('email'):
        object_filter = Q(object_type='Customer', object_id=str(customer.pk))
        if invoice_by_id:
            object_filter |= Q(object_type='Invoice', object_id__in=[str(pk) for pk in invoice_by_id])
        if offer_by_id:
            object_filter |= Q(object_type='Offer', object_id__in=[str(pk) for pk in offer_by_id])
        mail_filter = Q(message__icontains='e-mail') | Q(message__icontains='.eml') | Q(message__icontains='versand')
        for audit in AuditLog.objects.filter(object_filter).filter(mail_filter).select_related('user'):
            link = ''
            try:
                object_id = int(audit.object_id)
            except (TypeError, ValueError):
                object_id = None
            if audit.object_type == 'Invoice' and object_id in invoice_by_id:
                link = reverse('sedo:invoice_detail', args=[object_id])
            elif audit.object_type == 'Offer' and object_id in offer_by_id:
                link = reverse('sedo:offer_detail', args=[object_id])
            entries.append(_entry(
                'email', audit.created_at, 'E-Mail-Aktivität', audit.message or audit.object_repr,
                link=link, actor=audit.user.get_username() if audit.user_id else 'System', show_time=True,
            ))

    entries.sort(key=lambda item: item['occurred_at'], reverse=True)
    return entries
