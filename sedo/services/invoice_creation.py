"""Atomare Anlage einer Rechnung aus bereits validierten Formularen."""

from django.db import transaction
from django.utils import timezone

from ..models import Offer
from .calc import recalc_invoice
from .invoice_files import datei_rueckweg
from .invoices import WorkflowError, generate_invoice_files, mark_invoice_corrected
from .item_positions import normalize_item_positions
from .numbering import save_with_unique_number


def save_new_invoice(inv, formset, *, action, source_offer=None,
                     correction_source=None, request=None):
    """Speichert Nummer, Positionen, Archiv und Quellen als eine Einheit.

    Die View prüft Eingaben und entscheidet über die HTTP-Antwort. Hier bleiben
    Datenbank- und Datei-Rollback sowie der Schutz paralleler Vorgänge zusammen.
    """
    with datei_rueckweg() as merken, transaction.atomic():
        save_with_unique_number(inv, 'EN' if action == 'draft' else 'RE')
        formset.instance = inv
        formset.save()
        normalize_item_positions(inv.items.all())
        recalc_invoice(inv)
        inv.save()
        merken(inv)
        generate_invoice_files(inv, erstmalig=action != 'draft', request=request)
        if source_offer:
            updated = Offer.objects.filter(
                pk=source_offer.pk, linked_invoice__isnull=True,
            ).exclude(status='in_rechnung').update(
                status='in_rechnung', linked_invoice=inv,
                updated_at=timezone.now(),
            )
            if not updated:
                raise WorkflowError(
                    f'Aus Angebot {source_offer.number} wurde soeben bereits '
                    f'eine Rechnung erzeugt (paralleler Vorgang).')
        if correction_source and action != 'draft':
            mark_invoice_corrected(correction_source, request=request)
    return inv
