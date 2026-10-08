"""Zahlungsimport aus Bank-JSON und Zuordnung zu Rechnungen."""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from ..models import Invoice, Payment
from .calc import refresh_invoice_payment_status

# Rechnungsnummern-Muster im Verwendungszweck, z. B. RE2025080001.
# Bewusst auf die eigenen Präfixe RE/AG eingeschränkt – ein beliebiges
# Fremdkennzeichen (z. B. „KD12345678“) wird gar nicht erst als Kandidat
# extrahiert. Der Treffer wird ohnehin exakt gegen echte Nummern aufgelöst.
_INVOICE_NO_PATTERN = re.compile(r'(?:RE|AG)\d{6,}')
_BANK_DATE_FORMATS = ('%Y-%m-%d', '%d.%m.%Y', '%Y%m%d')


def parse_bank_date(value):
    """Parst ein Bank-Datum; gibt ``None`` zurück, wenn es unlesbar ist.

    Früher fiel diese Funktion still auf das heutige Datum zurück. Das Ergebnis
    landet direkt als ``Payment.booking_date`` in einem aufbewahrungspflichtigen
    Zahlungsbeleg – und hebelt zugleich die Dublettenerkennung aus, die auf
    Datum, Betrag, Name und Verwendungszweck prüft (Review-Fund F-48).

    Der Rückgabetyp hat sich damit geändert: Aufrufer müssen ``None``
    behandeln.
    """
    # datetime VOR date prüfen – datetime ist eine Unterklasse von date.
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value or '').strip()
    for fmt in _BANK_DATE_FORMATS:
        try:
            return datetime.strptime(s[:10] if fmt == '%Y-%m-%d' else s, fmt).date()
        except ValueError:
            continue
    return None


def _match_invoice(purpose: str) -> Invoice | None:
    """Findet die Rechnung zum Verwendungszweck über exakte Nummern-Token.

    Vermeidet den früheren Substring-Fehler (RE…0001 ⊂ RE…00010) und die
    N+1-Iteration über alle Rechnungen: Nummern werden per Regex extrahiert und
    in einer Abfrage nachgeschlagen.
    """
    tokens = set(_INVOICE_NO_PATTERN.findall((purpose or '').upper()))
    if not tokens:
        return None
    # 'korrigiert' bleibt hier bewusst in der Kandidatenmenge – sonst findet die
    # Kettenauflösung unten die Rechnung gar nicht mehr und leitet die Zahlung
    # nicht an die Korrektur weiter. Entwürfe fliegen raus: auf einen nie
    # ausgestellten Beleg gehört keine Bankzahlung.
    invoice = (
        Invoice.objects.exclude(lifecycle__in=('cancelled', 'draft'))
        .filter(number__in=tokens)
        .order_by('-issue_date')
        .first()
    )
    # Korrigierte Rechnungen sind ersetzt: Zahlt der Kunde auf die alte Nummer,
    # wird der Zahlungseingang der AKTIVEN Korrekturrechnung zugeordnet
    # (Mireks Entscheid 14.08.). Ohne aktive Korrektur bleibt die Zahlung
    # „unklar“ und wird manuell zugeordnet.
    depth = 0
    while invoice is not None and invoice.lifecycle == 'corrected' and depth < 5:
        invoice = invoice.active_correction
        depth += 1
    # Endprüfung über ALLE Belegzustände: active_correction filtert nur auf
    # invoice_type und deleted_at – die Kette kann deshalb in einem
    # Korrektur-ENTWURF enden, und die Bankzahlung würde an einem nie
    # ausgestellten Beleg festgemacht.
    if invoice is not None and invoice.lifecycle != 'issued':
        return None  # Kette nicht auflösbar → manuell klären
    return invoice


def import_bank_json(data) -> dict:
    """Importiert Bank-Transaktionen (JSON) und ordnet sie Rechnungen zu."""
    imported = matched = unclear = ignored = 0
    unassigned = []
    date_errors = []
    if isinstance(data, dict):
        data = data.get('transactions') or data.get('umsatz') or data.get('data') or []
    for nummer, t in enumerate(data, 1):
        try:
            amount = Decimal(str(t.get('betrag', t.get('amount', '0')))).quantize(Decimal('0.01'))
        except (InvalidOperation, ValueError, TypeError):
            ignored += 1
            continue
        if amount <= 0:
            ignored += 1
            continue
        booking_date = parse_bank_date(t.get('datum') or t.get('date') or t.get('booking_date'))
        name = str(t.get('name') or t.get('applicant_name') or t.get('payer') or '')[:255]
        if booking_date is None:
            # Kein stiller Rückfall auf heute (F-48): ein falsch datierter
            # Zahlungsbeleg ist schlechter als eine abgewiesene Zeile. Die
            # Rohdaten kommen mit in die Meldung, damit sich die Zahlung von
            # Hand erfassen lässt – beim FinTS-Abruf gibt es keine Datei zum
            # Nachbessern.
            date_errors.append({
                'zeile': nummer,
                'datum': str(t.get('datum') or t.get('date') or t.get('booking_date') or ''),
                'betrag': str(amount),
                'name': name,
                'verwendungszweck': str(t.get('verwendungszweck') or t.get('purpose')
                                        or t.get('text') or '')[:200],
            })
            ignored += 1
            continue
        purpose = str(t.get('verwendungszweck') or t.get('purpose') or t.get('text') or '')
        external_id = str(t.get('transaktions_id') or t.get('transaction_id')
                          or t.get('transactionId') or t.get('bank_reference')
                          or t.get('reference_id') or t.get('id') or '').strip()[:120]
        # Doublettenprüfung: bevorzugt über die stabile Bank-Transaktions-ID
        # (zwei echte Zahlungen mit gleichem Datum/Betrag/Text bleiben dann
        # unterscheidbar). Nur ohne ID greift die alte Text-Heuristik.
        # all_objects: auch soft-gelöschte Zahlungen zählen als bereits importiert.
        if external_id:
            if Payment.all_objects.filter(external_id=external_id).exists():
                ignored += 1
                continue
        elif Payment.all_objects.filter(booking_date=booking_date, amount=amount,
                                        payer_name=name, purpose=purpose).exists():
            ignored += 1
            continue
        invoice = _match_invoice(purpose)
        payment = Payment.objects.create(
            invoice=invoice, booking_date=booking_date, amount=amount,
            payer_name=name, purpose=purpose, external_id=external_id,
            raw=t, matched_automatically=bool(invoice),
        )
        imported += 1
        if invoice:
            matched += 1
            refresh_invoice_payment_status(invoice)
        else:
            unclear += 1
            unassigned.append(payment)
    return {'imported': imported, 'matched': matched, 'unclear': unclear,
            'ignored': ignored, 'unassigned': unassigned, 'date_errors': date_errors}
