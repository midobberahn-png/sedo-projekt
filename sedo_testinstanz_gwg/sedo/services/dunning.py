"""Mahnwesen: Zahlungserinnerungen/Mahnungen erzeugen und Kandidaten finden."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas

from ..core.rechnungslauf_core import SELLER
from ..models import DunningDocument, Invoice
from .formatting import euro
from .invoices import WorkflowError
from . import pdf_chrome as chrome
from .pdf_chrome import HEADER_H, INK, MARGIN

# Neue Zahlungsfrist, die eine Mahnung/Erinnerung setzt.
DUNNING_GRACE_DAYS = 7
# Ab wie vielen Tagen Überfälligkeit eine Rechnung als Mahnkandidat gilt.
DEFAULT_DUNNING_THRESHOLD_DAYS = 14

# Rechnungen in diesen Status werden nie gemahnt.
_UNDUNNABLE_STATUS = ('bezahlt', 'storniert', 'korrigiert', 'entwurf')

_KIND_LABELS = {'reminder': 'Zahlungserinnerung', 'dunning': 'Mahnung'}


def next_dunning_kind(invoice: Invoice, today=None) -> str | None:
    """Ermittelt die nächste fällige Mahnstufe einer Rechnung.

    Stufenmodell: keine Mahnung → Zahlungserinnerung; Erinnerung vorhanden und
    deren neue Frist verstrichen → Mahnung; Mahnung vorhanden → keine weitere
    automatische Stufe. (Vorher fiel eine Rechnung nach der ERSTEN Erinnerung
    für immer aus der Kandidatenliste – ein Mahnprozess war unmöglich.)
    """
    if invoice.status in _UNDUNNABLE_STATUS:
        return None
    docs = list(invoice.dunning_documents.all())
    if any(d.kind == 'dunning' for d in docs):
        return None
    reminders = [d for d in docs if d.kind == 'reminder']
    if not reminders:
        return 'reminder'
    last = max(reminders, key=lambda d: (d.document_date, d.id))
    today = today or timezone.localdate()
    if last.due_date_new and last.due_date_new < today:
        return 'dunning'
    return None  # Frist der Erinnerung läuft noch


def ensure_dunning_allowed(invoice: Invoice, kind: str) -> None:
    """Blockiert doppelte Mahnungen derselben Stufe (auch im manuellen Weg)."""
    if invoice.status in _UNDUNNABLE_STATUS:
        raise WorkflowError(
            f'Rechnung {invoice.number} ({invoice.get_status_display()}) kann nicht gemahnt werden.')
    docs = list(invoice.dunning_documents.all())
    same = [d for d in docs if d.kind == kind]
    if same:
        last = max(same, key=lambda d: (d.document_date, d.id))
        raise WorkflowError(
            f'{_KIND_LABELS[kind]} zu {invoice.number} wurde bereits am '
            f'{last.document_date.strftime("%d.%m.%Y")} erzeugt.')
    if kind == 'reminder' and any(d.kind == 'dunning' for d in docs):
        raise WorkflowError(
            f'Zu {invoice.number} existiert bereits eine Mahnung – '
            f'eine Zahlungserinnerung wäre ein Rückschritt in der Mahnstufe.')


def generate_dunning_document(invoice: Invoice, kind: str = 'reminder') -> DunningDocument:
    """Erzeugt eine Zahlungserinnerung (reminder) oder Mahnung (dunning) als PDF."""
    paid = invoice.payments.filter(deleted_at__isnull=True).aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
    open_amount = (invoice.gross_total - paid).quantize(Decimal('0.01'))
    new_due = timezone.localdate() + timedelta(days=DUNNING_GRACE_DAYS)
    try:
        # Eigenes atomic (Savepoint): Schlägt der INSERT am Unique-Constraint
        # fehl, bleibt eine umgebende Transaktion (z. B. im Test) intakt.
        with transaction.atomic():
            doc = DunningDocument.objects.create(
                invoice=invoice, kind=kind, document_date=timezone.localdate(),
                due_date_new=new_due, amount_open=open_amount,
            )
    except IntegrityError as exc:
        # Unique-Constraint (invoice, kind): Zwei parallele Requests können
        # ensure_dunning_allowed beide passieren – der zweite endet hier.
        raise WorkflowError(
            f'{_KIND_LABELS.get(kind, kind)} zu {invoice.number} wurde soeben '
            f'bereits erzeugt (paralleler Vorgang).') from exc
    out_dir = Path(settings.MEDIA_ROOT) / 'dunning' / invoice.number
    out_dir.mkdir(parents=True, exist_ok=True)
    label = 'Zahlungserinnerung' if kind == 'reminder' else 'Mahnung'
    pdf_path = out_dir / f'{invoice.number}_{kind}.pdf'

    c = canvas.Canvas(str(pdf_path), pagesize=A4)
    w, h = A4
    left, right = MARGIN, w - MARGIN
    chrome.draw_header(c)
    sender_y = h - HEADER_H - 4 * mm
    chrome.draw_sender_line(c, sender_y)

    # Empfänger links
    ry = sender_y - 8 * mm
    c.setFillColorRGB(*INK)
    c.setFont('Helvetica-Bold', 10)
    c.drawString(left, ry, invoice.customer.name)
    c.setFont('Helvetica', 10)
    for line in [invoice.customer.name2, invoice.customer.street,
                 f'{invoice.customer.postcode} {invoice.customer.city}'.strip()]:
        if (line or '').strip():
            ry -= 5 * mm
            c.drawString(left, ry, line)

    # Eckdaten rechts (ohne Box)
    info_rows = [
        ('Datum', timezone.localdate().strftime('%d.%m.%Y')),
        ('Rechnung', invoice.number),
        ('Fällig war', invoice.due_date.strftime('%d.%m.%Y') if invoice.due_date else '—'),
        ('Neue Frist', new_due.strftime('%d.%m.%Y')),
        ('Offener Betrag', euro(open_amount)),
    ]
    iy = chrome.draw_info_block(c, right, sender_y - 2 * mm, info_rows)

    # Titel + Fließtext
    y = min(ry, iy) - 12 * mm
    chrome.draw_title(c, y, label)
    y -= 10 * mm
    issue = invoice.issue_date.strftime('%d.%m.%Y')
    if kind == 'reminder':
        intro = (f'zu unserer Rechnung {invoice.number} vom {issue} konnten wir noch keinen '
                 f'vollständigen Zahlungseingang feststellen.')
    else:
        intro = (f'trotz Fälligkeit ist unsere Rechnung {invoice.number} vom {issue} noch nicht '
                 f'vollständig ausgeglichen.')
    c.setFillColorRGB(*INK)
    c.setFont('Helvetica', 10)
    c.drawString(left, y, 'Sehr geehrte Damen und Herren,')
    y -= 7 * mm
    paragraphs = [
        intro,
        (f'Bitte überweisen Sie den offenen Betrag von {euro(open_amount)} bis spätestens '
         f'{new_due.strftime("%d.%m.%Y")} unter Angabe der Rechnungsnummer {invoice.number}.'),
        'Sollten Sie den Betrag bereits überwiesen haben, betrachten Sie dieses Schreiben bitte als gegenstandslos.',
    ]
    for part in paragraphs:
        for line in simpleSplit(part, 'Helvetica', 10, right - left):
            c.drawString(left, y, line)
            y -= 5 * mm
        y -= 2.5 * mm
    y -= 3 * mm
    c.drawString(left, y, 'Mit freundlichen Grüßen')
    y -= 5 * mm
    c.setFont('Helvetica-Bold', 10)
    c.drawString(left, y, SELLER['name'])

    chrome.draw_footer(c)
    c.save()

    doc.pdf_file.name = str(pdf_path.relative_to(settings.MEDIA_ROOT))
    doc.save(update_fields=['pdf_file', 'updated_at'])
    if invoice.status not in _UNDUNNABLE_STATUS:
        invoice.status = 'gemahnt'
        invoice.save(update_fields=['status', 'updated_at'])
    return doc


def get_dunning_candidates(days_overdue_threshold: int = DEFAULT_DUNNING_THRESHOLD_DAYS):
    """Überfällige Rechnungen mit ihrer jeweils nächsten fälligen Mahnstufe.

    Gibt eine Liste von Rechnungen zurück; jede trägt ``next_kind``
    ('reminder'/'dunning') und ``next_kind_label`` für die Anzeige.
    """
    today = timezone.localdate()
    cutoff = today - timedelta(days=days_overdue_threshold)
    queryset = (
        Invoice.objects
        .exclude(status__in=_UNDUNNABLE_STATUS)
        .filter(due_date__lte=cutoff)
        .select_related('customer', 'obj')
        .prefetch_related('dunning_documents', 'payments')
        .order_by('due_date')
    )
    candidates = []
    for inv in queryset:
        kind = next_dunning_kind(inv, today)
        if not kind:
            continue
        if inv.open_amount <= Decimal('0.00'):
            continue  # bereits ausgeglichen, nur der Status hinkt nach
        inv.next_kind = kind
        inv.next_kind_label = _KIND_LABELS[kind]
        candidates.append(inv)
    return candidates


def send_bulk_dunning(invoice_ids: list, kind: str = 'reminder') -> dict:
    """Erstellt Mahnungen/Erinnerungen für mehrere Rechnungs-IDs auf einmal.

    ``kind='auto'`` wählt je Rechnung die nächste fällige Stufe. Rechnungen,
    deren Stufe bereits existiert oder die nicht mahnbar sind, werden
    übersprungen. Gibt {'created': n, 'skipped': n} zurück.
    """
    created = skipped = 0
    for pk in invoice_ids:
        inv = Invoice.objects.filter(pk=pk).first()
        if not inv:
            skipped += 1
            continue
        use_kind = next_dunning_kind(inv) if kind == 'auto' else kind
        if not use_kind:
            skipped += 1
            continue
        try:
            ensure_dunning_allowed(inv, use_kind)
            generate_dunning_document(inv, use_kind)
        except WorkflowError:
            skipped += 1
            continue
        created += 1
    return {'created': created, 'skipped': skipped}
