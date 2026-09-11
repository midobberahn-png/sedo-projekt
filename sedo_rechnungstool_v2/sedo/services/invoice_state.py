"""H3: kanonischer Belegzustand und abgeleitete Zahlungs-/Arbeitsanzeige.

`Invoice.lifecycle` ist die einzige gespeicherte Belegwahrheit. `status` bleibt
vorübergehend als Legacy-Anzeige-/Kompatibilitätscache bestehen und darf keine
fachliche Entscheidung mehr über Entwurf/Korrektur/Storno treffen.
"""
from __future__ import annotations

from decimal import Decimal

from django.db.models import Q, Sum
from django.utils import timezone

from ..models import Invoice
from .workflow_errors import WorkflowError

LIFECYCLE_TO_LEGACY = {
    'draft': 'entwurf',
    'corrected': 'korrigiert',
    'cancelled': 'storniert',
}
LEGACY_TO_LIFECYCLE = {v: k for k, v in LIFECYCLE_TO_LEGACY.items()}


def lifecycle_from_legacy(status: str) -> str:
    return LEGACY_TO_LIFECYCLE.get(status, 'issued')


def derived_payment_state(invoice: Invoice) -> str:
    gross = Decimal(str(invoice.gross_total))
    if gross <= Decimal('0.00'):
        return 'unpaid'
    paid = (invoice.payments.filter(deleted_at__isnull=True)
            .aggregate(total=Sum('amount'))['total'] or Decimal('0.00'))
    if paid >= gross:
        return 'paid'
    if paid > Decimal('0.00'):
        return 'partial'
    return 'unpaid'


def derived_display_status(invoice: Invoice, *, today=None) -> str:
    """Kompatible UI-Kennung, ohne sie als Belegwahrheit zu verwenden."""
    lifecycle_status = LIFECYCLE_TO_LEGACY.get(invoice.lifecycle)
    if lifecycle_status:
        return lifecycle_status
    if invoice.invoice_type == '457':
        return 'erstellt'
    payment = derived_payment_state(invoice)
    if payment == 'paid':
        return 'bezahlt'
    if payment == 'partial':
        return 'teilbezahlt'
    if invoice.dunning_documents.exists():
        return 'gemahnt'
    today = timezone.localdate() if today is None else today
    if invoice.due_date and invoice.due_date < today:
        return 'ueberfaellig'
    # Historischer Versandcache wird nur als Anzeige benutzt; Versandwahrheit
    # liegt in Versandnachweisen/Audits, nicht im Lifecycle.
    if invoice.status == 'versendet':
        return 'versendet'
    return 'erstellt'


def issued_claims(qs=None):
    qs = Invoice.objects if qs is None else qs
    return qs.filter(lifecycle='issued').exclude(invoice_type='457')


def payment_assignable(qs=None):
    return issued_claims(qs)


def require_lifecycle(invoice: Invoice, *allowed: str) -> None:
    if invoice.lifecycle not in allowed:
        raise WorkflowError(
            f'Rechnung {invoice.number} hat den Belegzustand '
            f'{invoice.get_lifecycle_display()} und ist für diesen Vorgang gesperrt.')


def guarded_lifecycle_update(invoice: Invoice, *, expected: str, target: str,
                             legacy_status: str | None = None) -> None:
    """Zentrale atomare Zustandsänderung; Audit bleibt im aufrufenden Fachservice.

    Der Legacy-Cache wird nur zusammen mit lifecycle geändert. Andere Pfade dürfen
    lifecycle nicht über QuerySet.update verändern; Gegenproben sichern die
    zentralen Fachpfade ab.
    """
    values = {'lifecycle': target, 'updated_at': timezone.now()}
    if legacy_status is not None:
        values['status'] = legacy_status
    updated = Invoice.objects.filter(pk=invoice.pk, lifecycle=expected).update(**values)
    if not updated:
        raise WorkflowError('Der Belegzustand wurde parallel geändert. Bitte neu laden.')
    invoice.refresh_from_db()
