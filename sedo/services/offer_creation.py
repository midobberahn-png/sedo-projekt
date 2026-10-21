"""Atomare Angebotsanlage aus bereits validierten Formularen."""

from pathlib import Path

from django.conf import settings
from django.db import transaction

from .calc import recalc_offer
from .invoice_files import datei_rueckweg, merke_veroeffentlichung
from .item_positions import normalize_item_positions
from .mail_storage import pin_message
from .numbering import save_with_unique_number
from .pdf import generate_offer_pdf


def save_new_offer(offer, formset, *, prepare_offer, source_mail=None):
    """Speichert Stammdaten, Nummer, Positionen und PDF als einen Vorgang.

    Die View validiert die Eingaben. Der Dateirückweg kompensiert Fehler beim
    Schreiben oder DB-Commit; wie bei Rechnungen ist das keine absturzsichere
    Transaktion zwischen Datenbank und Dateisystem.
    """
    with datei_rueckweg() as merken, transaction.atomic():
        if source_mail:
            pin_message(source_mail)
        prepare_offer()
        save_with_unique_number(offer, 'AG')
        formset.instance = offer
        formset.save()
        normalize_item_positions(offer.items.all())
        recalc_offer(offer)
        offer.save()
        merken(offer)
        pdf_path = Path(settings.MEDIA_ROOT) / 'generated' / offer.number / f'{offer.number}.pdf'
        try:
            generate_offer_pdf(offer)
        finally:
            # Auch eine mitten im PDF-Schreiben abgebrochene Datei zurücknehmen.
            if pdf_path.is_file():
                merke_veroeffentlichung(pdf_path)
    return offer
