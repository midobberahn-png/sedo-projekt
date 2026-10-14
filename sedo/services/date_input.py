"""Validierung von Datumswerten aus Formularen und Importen."""
from datetime import date, datetime

_DATE_FORMATS_FOR_VIEW = ['%Y-%m-%d', '%d.%m.%Y', '%Y%m%d']


def parse_date_optional(value):
    """Parst ein Datum; gibt None zurück, wenn es nicht lesbar ist."""
    # datetime VOR date prüfen – datetime ist eine Unterklasse von date.
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value or '').strip()
    for fmt in _DATE_FORMATS_FOR_VIEW:
        try:
            return datetime.strptime(s[:10] if fmt == '%Y-%m-%d' else s, fmt).date()
        except ValueError:
            continue
    return None


def parse_date_for_view(value, feld='Datum'):
    """Parst ein Pflichtdatum aus dem Request – ohne stillen Ersatzwert.

    Bisher wurde ein unlesbares Datum stumm durch HEUTE ersetzt. Der Wert
    speist das Rechnungsdatum des GWG- und des Abo-Monatslaufs sowie das
    Buchungsdatum des Kassenbuch-Bankimports: ein ganzer Massenlauf konnte
    lautlos falsch datiert werden. Das Rechnungsdatum ist steuerlich relevant
    und bestimmt über services/numbering.py zusätzlich den Nummernkreis-Monat.

    Wirft ``DatumUnlesbar``; die drei Aufrufer fangen das ab und brechen ab.
    """
    ergebnis = parse_date_optional(value)
    if ergebnis is None:
        raise DatumUnlesbar(f'{feld}: „{str(value or "").strip() or "leer"}" ist kein gültiges Datum.')
    return ergebnis


class DatumUnlesbar(ValueError):
    """Ein Pflichtdatum ließ sich nicht lesen.

    Bewusst eine eigene Ausnahme statt eines Ersatzwerts: ein still auf HEUTE
    gesetztes Rechnungs- oder Buchungsdatum landet unbemerkt in einem
    aufbewahrungspflichtigen Beleg (Nebenbefund NEU-2).
    """
