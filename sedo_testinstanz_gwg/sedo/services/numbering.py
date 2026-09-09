"""Vergabe fortlaufender Rechnungs-/Angebots-/Kassenbuchnummern.

GoBD verlangt einen lückenlosen, eindeutigen Nummernkreis. Seit v3.22 führt
die Tabelle ``NumberRange`` den Zähler je Präfix und Monat zentral:

- ``_allocate_number`` zählt den Nummernkreis mit einem atomaren F()-Update
  hoch (``select_for_update`` ist auf SQLite wirkungslos – das UPDATE
  serialisiert die Vergabe genauso zuverlässig) und läuft in DERSELBEN
  Transaktion wie die Beleganlage: schlägt die Anlage fehl, rollt auch der
  Zähler zurück, es entstehen keine „verbrannten“ Nummern.
- Der Zähler wird beim ersten Zugriff aus dem Bestand geseedet (über
  ``all_objects``, damit einmal vergebene Nummern nach Soft-Delete nie
  wiederverwendet werden) und bei Kollisionen mit Altbestand nachgezogen.
- ``next_number`` bleibt eine NICHT-verbrauchende Vorschau für Formulare.
"""
from __future__ import annotations

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from ..models import CashbookEntry, Invoice, NumberRange, Offer

# Wie oft bei einer Nummernkollision (Altbestand/parallele Anlage) neu versucht wird.
_MAX_NUMBER_RETRIES = 6


def _model_for(prefix: str):
    return Offer if prefix == 'AG' else Invoice


def _scan_next_seq(prefix: str, now) -> int:
    """Nächste laufende Nummer laut Datenbestand (inkl. soft-gelöschter Belege)."""
    stem = f'{prefix}{now.year}{now.month:02d}'
    model = _model_for(prefix)
    manager = getattr(model, 'all_objects', model.objects)
    existing = manager.filter(number__startswith=stem).order_by('-number').first()
    if not existing:
        return 1
    try:
        return int(existing.number[-4:]) + 1
    except (ValueError, TypeError):
        return 1


def next_number(prefix: str = 'RE') -> str:
    """Vorschau der nächsten Nummer (REYYYYMMNNNN) – verbraucht NICHTS.

    Wird in Formularen als Platzhalter angezeigt; die verbindliche Vergabe
    passiert erst beim Speichern über den Nummernkreis.
    """
    now = timezone.localdate()
    seq = _scan_next_seq(prefix, now)
    range_row = NumberRange.objects.filter(prefix=prefix, year=now.year, month=now.month).first()
    if range_row:
        seq = max(seq, range_row.next_number)
    return f'{prefix}{now.year}{now.month:02d}{seq:04d}'


def _allocate_number(prefix: str) -> str:
    """Reserviert die nächste Nummer aus dem Nummernkreis (transaktional).

    MUSS innerhalb einer umgebenden Transaktion laufen – nur dann rollt der
    Zähler bei einer fehlgeschlagenen Beleganlage mit zurück.
    """
    now = timezone.localdate()
    range_row, _ = NumberRange.objects.get_or_create(
        prefix=prefix, year=now.year, month=now.month,
        defaults={'next_number': _scan_next_seq(prefix, now)},
    )
    NumberRange.objects.filter(pk=range_row.pk).update(next_number=F('next_number') + 1)
    range_row.refresh_from_db()
    seq = range_row.next_number - 1
    return f'{prefix}{now.year}{now.month:02d}{seq:04d}'


def _sync_range_past_existing(prefix: str) -> None:
    """Zieht den Zähler hinter den Altbestand (z. B. nach DB-Import)."""
    now = timezone.localdate()
    seq = _scan_next_seq(prefix, now)
    NumberRange.objects.filter(
        prefix=prefix, year=now.year, month=now.month, next_number__lt=seq,
    ).update(next_number=seq)


def _is_number_collision(exc: IntegrityError) -> bool:
    """Nur ECHTE Nummernkollisionen rechtfertigen einen Retry mit neuer Nummer.

    Andere UNIQUE-Verletzungen (z. B. der Idempotenz-Schutz des GWG-Monatslaufs)
    müssen sofort nach oben – ein Retry würde dort nur sinnlos wiederholen.
    """
    text = str(exc)
    return '.number' in text or 'number' in text.rsplit(':', 1)[-1]


def _create_numbered(model, prefix: str, **fields):
    """Legt ``model`` mit kollisionssicherer, fortlaufender Nummer an."""
    last_exc: IntegrityError | None = None
    for _ in range(_MAX_NUMBER_RETRIES):
        try:
            with transaction.atomic():
                return model.objects.create(number=_allocate_number(prefix), **fields)
        except IntegrityError as exc:
            if not _is_number_collision(exc):
                raise
            # Kollision mit Altbestand (Zähler hing hinterher) → nachziehen
            last_exc = exc
            _sync_range_past_existing(prefix)
    raise last_exc


def create_numbered_invoice(**fields) -> Invoice:
    return _create_numbered(Invoice, 'RE', **fields)


def create_numbered_offer(**fields) -> Offer:
    return _create_numbered(Offer, 'AG', **fields)


def save_with_unique_number(instance, prefix: str = 'RE'):
    """Vergibt eine kollisionssichere Nummer und speichert die (neue) Instanz.

    Für formularbasierte Anlage gedacht: Nummer kommt aus dem Nummernkreis;
    bei Kollision mit Altbestand wird der Zähler nachgezogen und erneut versucht.
    """
    last_exc: IntegrityError | None = None
    for _ in range(_MAX_NUMBER_RETRIES):
        try:
            with transaction.atomic():
                instance.number = _allocate_number(prefix)
                instance.save()
            return instance
        except IntegrityError as exc:
            if not _is_number_collision(exc):
                raise
            last_exc = exc
            _sync_range_past_existing(prefix)
    raise last_exc


def next_cashbook_no(month: str) -> int:
    """Nächste Buchungsnummer im Kassenbuchmonat (fortlaufend ab 1)."""
    last = CashbookEntry.objects.filter(month=month).order_by('-booking_no').first()
    return (last.booking_no + 1) if last else 1
