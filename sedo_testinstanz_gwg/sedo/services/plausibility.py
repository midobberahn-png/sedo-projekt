"""Lokale Plausibilitätsprüfung von Angebots-/Rechnungspositionen.

Bewusst OHNE KI: Preis-Ausreißer werden deterministisch gegen die eigene
Rechnungshistorie des Kunden geprüft (präziser als jede Schätzung, kostenlos,
offline – und die Kundendaten verlassen nie den eigenen Server). Dazu kommen
Dubletten- und Nullwert-Prüfungen innerhalb des aktuellen Dokuments.

Rückgabeformat je Befund: {'position': int|None, 'schwere': 'warnung'|'hinweis',
'text': str} – identisch zum KI-Check, damit die Oberfläche beides gleich rendert.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from ..models import InvoiceItem

# Ab dieser relativen UND absoluten Abweichung vom letzten Preis desselben
# Kunden für dieselbe Leistung wird gewarnt (Zahlendreher, Tippfehler).
OUTLIER_MIN_RELATIVE = Decimal('0.25')     # ≥ 25 % Abweichung
OUTLIER_MIN_ABSOLUTE = Decimal('5.00')     # und ≥ 5 € Unterschied
# Wie viele historische Positionen je Kunde maximal betrachtet werden.
HISTORY_LIMIT = 500
# Obergrenze der zurückgegebenen Befunde (Oberfläche bleibt lesbar).
MAX_FINDINGS = 12


def _norm_desc(value) -> str:
    """Normalisiert eine Positionsbezeichnung für den Vergleich."""
    return re.sub(r'\s+', ' ', str(value or '')).strip().lower()


def _to_decimal(value) -> Decimal | None:
    """Wandelt Formularwerte ('58', '58,00', 58.0) defensiv in Decimal um."""
    if value is None:
        return None
    s = str(value).strip().replace('€', '').replace(' ', '')
    if not s:
        return None
    if ',' in s:
        s = s.replace('.', '').replace(',', '.')
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _de(value: Decimal) -> str:
    """Betrag im deutschen Format, z. B. 58,00 €."""
    return f'{value:.2f}'.replace('.', ',') + ' €'


def _is_digit_swap(a: Decimal, b: Decimal) -> bool:
    """Erkennt klassische Zahlendreher (85 ↔ 58) im Ganzzahlteil."""
    da, db = str(int(abs(a))), str(int(abs(b)))
    return len(da) == len(db) and da != db and sorted(da) == sorted(db)


def _customer_price_history(customer_id) -> dict[str, InvoiceItem]:
    """Letzter berechneter Preis je (normalisierter) Leistung dieses Kunden.

    Basis: echte Rechnungen (kein Entwurf, kein Storno, nicht ausgeblendet),
    neueste zuerst – der erste Treffer je Leistung ist der zuletzt gültige Preis.
    """
    items = (
        InvoiceItem.objects
        .filter(invoice__customer_id=customer_id, invoice__deleted_at__isnull=True)
        .exclude(invoice__status__in=['entwurf', 'storniert'])
        .exclude(invoice__invoice_type='457')
        .select_related('invoice')
        .order_by('-invoice__issue_date', '-invoice__id')
    )
    history: dict[str, InvoiceItem] = {}
    for item in items[:HISTORY_LIMIT]:
        key = _norm_desc(item.description)
        if key and key not in history:
            history[key] = item
    return history


def check_positions_local(customer_id, positionen: list[dict]) -> list[dict]:
    """Deterministische Prüfung der aktuellen Positionen (ohne KI).

    ``positionen``: Liste aus {'bezeichnung', 'menge', 'einheit', 'einzelpreis'}
    in Dokumentreihenfolge (Position 1 = Index 0).
    """
    findings: list[dict] = []

    # 1. Dubletten innerhalb des Dokuments
    seen: dict[str, int] = {}
    for idx, pos in enumerate(positionen, start=1):
        key = _norm_desc(pos.get('bezeichnung'))
        if not key:
            continue
        if key in seen:
            findings.append({
                'position': idx, 'schwere': 'warnung',
                'text': f'Position {idx} („{pos.get("bezeichnung", "")[:40]}“) ist doppelt – '
                        f'gleiche Bezeichnung wie Position {seen[key]}. Absicht?',
            })
        else:
            seen[key] = idx

    # 2. Nullwerte
    for idx, pos in enumerate(positionen, start=1):
        if not _norm_desc(pos.get('bezeichnung')):
            continue
        price = _to_decimal(pos.get('einzelpreis'))
        qty = _to_decimal(pos.get('menge'))
        if price is not None and price <= 0:
            findings.append({
                'position': idx, 'schwere': 'hinweis',
                'text': f'Position {idx}: Einzelpreis ist 0 € – bitte prüfen, ob das beabsichtigt ist.',
            })
        if qty is not None and qty <= 0:
            findings.append({
                'position': idx, 'schwere': 'hinweis',
                'text': f'Position {idx}: Menge ist 0 – die Position geht nicht in die Summe ein.',
            })

    # 3. Preis-Ausreißer gegen die Kundenhistorie
    if customer_id:
        history = _customer_price_history(customer_id)
        for idx, pos in enumerate(positionen, start=1):
            key = _norm_desc(pos.get('bezeichnung'))
            price = _to_decimal(pos.get('einzelpreis'))
            if not key or price is None or price <= 0:
                continue
            last = history.get(key)
            if not last or last.unit_price <= 0:
                continue
            deviation = abs(price - last.unit_price)
            if deviation < OUTLIER_MIN_ABSOLUTE:
                continue
            if deviation / last.unit_price < OUTLIER_MIN_RELATIVE:
                continue
            swap_hint = ' Zahlendreher?' if _is_digit_swap(price, last.unit_price) else ''
            last_date = last.invoice.issue_date.strftime('%d.%m.%Y')
            findings.append({
                'position': idx, 'schwere': 'warnung',
                'text': (f'Position {idx} („{pos.get("bezeichnung", "")[:40]}“): {_de(price)} – '
                         f'zuletzt wurden diesem Kunden {_de(last.unit_price)} berechnet '
                         f'(Rechnung {last.invoice.number} vom {last_date}).{swap_hint}'),
            })

    return findings[:MAX_FINDINGS]
