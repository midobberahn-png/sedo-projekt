"""Atomare Anlage einer Rechnung aus bereits validierten Formularen."""

from django.db import transaction

from ..audit import write_audit
from .calc import recalc_invoice
from .invoice_files import datei_rueckweg
from .invoices import generate_invoice_files, link_offer_to_invoice, mark_invoice_corrected
from .item_positions import normalize_item_positions
from .numbering import save_with_unique_number


def save_new_invoice(inv, formset, *, action, prepare_invoice=None, source_offer=None,
                     correction_source=None, request=None):
    """Speichert Nummer, Positionen, Archiv und Quellen als eine Einheit.

    Die View prüft Eingaben und entscheidet über die HTTP-Antwort. Hier bleiben
    Datenbank- und Datei-Rollback sowie der Schutz paralleler Vorgänge zusammen.
    """
    with datei_rueckweg() as merken, transaction.atomic():
        if prepare_invoice is not None:
            prepare_invoice()
        save_with_unique_number(inv, 'EN' if action == 'draft' else 'RE')
        formset.instance = inv
        formset.save()
        normalize_item_positions(inv.items.all())
        recalc_invoice(inv)
        inv.save()
        merken(inv)
        generate_invoice_files(inv, erstmalig=action != 'draft', request=request)
        if source_offer:
            previous_status = link_offer_to_invoice(source_offer, inv)
            if action == 'draft':
                # Beim Verwerfen muss ein zuvor offenes Angebot offen bleiben.
                # Der Nachweis ist Teil derselben Transaktion wie die Verknüpfung.
                write_audit(request, 'update', source_offer,
                            f'Angebot {source_offer.number} mit Entwurf {inv.number} verknüpft',
                            pflicht=True, metadata={
                                'anlass': 'angebot_an_entwurf',
                                'rechnung_id': inv.pk,
                                'vorheriger_status': previous_status,
                            })
        if correction_source and action != 'draft':
            mark_invoice_corrected(correction_source, request=request)
    return inv
