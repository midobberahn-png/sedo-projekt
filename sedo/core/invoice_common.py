"""Gemeinsame Rechnungsdaten und Formatierung für XML, PDF und Excel-Import."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Any

import pandas as pd
from lxml import etree

from sedo.company_profile import SELLER  # re-exported by rechnungslauf_core


NSMAP = {
    "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "qdt": "urn:un:unece:uncefact:data:standard:QualifiedDataType:100",
    "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
    "xs": "http://www.w3.org/2001/XMLSchema",
    "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}


RSM = "{%s}" % NSMAP["rsm"]


RAM = "{%s}" % NSMAP["ram"]


UDT = "{%s}" % NSMAP["udt"]


BUYER = {'id': '', 'name': '', 'street': '', 'postcode': '', 'city': '', 'country': 'DE'}


MONTHS_DE = {
    1: "Januar", 2: "Februar", 3: "März", 4: "April", 5: "Mai", 6: "Juni",
    7: "Juli", 8: "August", 9: "September", 10: "Oktober", 11: "November", 12: "Dezember",
}


_UNIT_CODE_MAP = {
    "stk": "H87", "stück": "H87", "stueck": "H87", "st": "H87",
    "stk.": "H87", "pcs": "H87", "stück.": "H87",
    "m²": "MTK", "m2": "MTK", "qm": "MTK", "quadratmeter": "MTK",
    "m": "MTR", "meter": "MTR", "lfm": "MTR", "laufmeter": "MTR", "laufender meter": "MTR",
    "km": "KMT",
    "monat": "MON", "monate": "MON", "mtl": "MON", "mon": "MON",
    "stunde": "HUR", "stunden": "HUR", "std": "HUR", "std.": "HUR", "h": "HUR",
    "tag": "DAY", "tage": "DAY",
    "woche": "WEE", "wochen": "WEE",
    "jahr": "ANN", "jahre": "ANN",
    "kg": "KGM", "l": "LTR", "liter": "LTR",
    "pauschal": "C62", "pauschale": "C62", "psch": "C62", "pausch": "C62",
}


def unece_unit_code(unit: Any) -> str:
    """Liefert den UN/ECE-Rec-20-Mengeneinheitscode (BT-130) für eine Einheit.

    Unbekannte Einheiten fallen bewusst auf ``H87`` (Stück) zurück, damit die
    XML gültig bleibt. So steht in der XML z. B. bei ``m²`` korrekt ``MTK``
    statt weiterhin ``H87``.
    """
    key = clean_text(unit).strip().lower().rstrip(".")
    if not key:
        return "H87"
    if key in _UNIT_CODE_MAP:
        return _UNIT_CODE_MAP[key]
    # Auch mit angehängtem Punkt versuchen (z. B. "Stk.")
    return _UNIT_CODE_MAP.get(key + ".", "H87")


def D(value: Any, default: str = "0.00") -> Decimal:
    """Wandelt einen Excel-/Eingabewert robust in ``Decimal``.

    Erkennt deutsche (1.032,54) wie englische (1032.54) Zahlformate. Bei einem
    nicht interpretierbaren Wert wird eine ``ValueError`` mit Klartext geworfen,
    statt den ganzen Lauf mit einer kryptischen ``InvalidOperation`` abzubrechen.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return Decimal(default)
    txt = str(value).strip().replace("€", "").replace(" ", "")
    # Deutsche Formate wie 1.032,54 richtig lesen; englische 1032.54 unverändert lassen
    if "," in txt:
        txt = txt.replace(".", "").replace(",", ".")
    if txt == "" or txt.lower() == "nan":
        return Decimal(default)
    try:
        return Decimal(txt)
    except InvalidOperation:
        raise ValueError(f"Ungültiger Zahlenwert: {value!r}") from None


def money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def de_money(value: Decimal) -> str:
    """Formatiert einen Betrag im deutschen Zahlenformat mit Tausender-Trennzeichen.
    Beispiel: 1234.56 → '1.234,56 €'
    """
    rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    # format() mit Tausender-Trennzeichen (US-Stil), dann in deutsches Format tauschen
    us_fmt = format(rounded, ",.2f")  # z. B. "1,234.56"
    de_fmt = us_fmt.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{de_fmt} €"


def de_qty(value: Decimal) -> str:
    q = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if q == q.to_integral_value():
        return str(int(q))
    return str(q).replace(".", ",")


def clean_text(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def clean_code(value: Any) -> str:
    """Bereinigt technische Codes aus Excel, z. B. 4200.0 -> 4200."""
    txt = clean_text(value)
    if txt == "":
        return ""
    if re.fullmatch(r"\d+\.0", txt):
        return txt[:-2]
    if re.fullmatch(r"\d+,0", txt):
        return txt[:-2]
    return txt


def country_code(value: Any) -> str:
    """ZUGFeRD/Factur-X erwartet ISO-3166-1-alpha-2 Codes (z. B. DE), nicht Ländernamen."""
    txt = clean_text(value)
    if not txt:
        return "DE"
    key = txt.strip().upper()
    mapping = {
        "DEUTSCHLAND": "DE",
        "GERMANY": "DE",
        "GER": "DE",
        "D": "DE",
        "DE": "DE",
        "ÖSTERREICH": "AT",
        "OESTERREICH": "AT",
        "AUSTRIA": "AT",
        "AT": "AT",
        "SCHWEIZ": "CH",
        "SWITZERLAND": "CH",
        "CH": "CH",
        "SPANIEN": "ES",
        "SPAIN": "ES",
        "ES": "ES",
    }
    if key in mapping:
        return mapping[key]
    # Nur echte zweistellige Codes zulassen; alles andere sicherheitshalber DE.
    if re.fullmatch(r"[A-Z]{2}", key):
        return key
    return "DE"


def safe_filename(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_\-]+", "_", text.strip())
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "rechnung"


def qn(ns: str, tag: str) -> str:
    if ns == "rsm":
        return RSM + tag
    if ns == "ram":
        return RAM + tag
    if ns == "udt":
        return UDT + tag
    raise ValueError(ns)


def sub(parent, ns: str, tag: str, text: Any | None = None, **attrs):
    elem = etree.SubElement(parent, qn(ns, tag), **{k: str(v) for k, v in attrs.items()})
    if text is not None:
        elem.text = str(text)
    return elem


@dataclass
class InvoicePosition:
    description: str
    quantity: Decimal = Decimal("1.00")
    unit: str = "Stk."
    unit_price: Decimal = Decimal("10.00")
    article_no: str = ""
    leistungsart_id: str = ""
    leistungsart_text: str = ""
    # ARS/Aareon-Mappingdaten je Position. Diese Werte werden primär für
    # Prüfbericht und spätere Erweiterungen gespeichert. Die XML-Leistungsart
    # kommt weiterhin aus leistungsart_id/leistungsart_text.
    we: str = ""
    ae: str = ""
    nksl: str = ""
    sachkonto: str = ""
    # Leistungsbeschreibung (optional): erscheint im PDF unter dem Titel und als
    # BT-154 (SpecifiedTradeProduct/Description) in der ZUGFeRD-XML.
    # WICHTIG: steht bewusst am Ende, damit bestehende positionsbasierte Aufrufe
    # von InvoicePosition(...) unverändert funktionieren.
    long_text: str = ""

    @property
    def line_total(self) -> Decimal:
        return (self.quantity * self.unit_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class InvoiceRow:
    invoice_no: str
    object_name: str
    we: str
    ae: str
    nksl: str
    service: str
    net: Decimal
    vat_rate: Decimal
    positions: list[InvoicePosition] = field(default_factory=list)
    object_street: str = ""
    object_postcode: str = ""
    object_city: str = ""
    object_country: str = "DE"
    sap_mapping: bool = False
    buyer_id: str = BUYER["id"]
    buyer_name: str = BUYER["name"]
    buyer_name2: str = ""
    buyer_street: str = BUYER["street"]
    buyer_postcode: str = BUYER["postcode"]
    buyer_city: str = BUYER["city"]
    buyer_country: str = BUYER["country"]
    one_time: bool = False
    # True nur fuer die neue Aareon/ARS-Mappingdatei. In diesem Modus wird in der XML
    # als Abrechnungseinheit der vom Rechnungssteller gelieferte Mappingwert, z. B.
    # "Beispielstraße 1", uebertragen. WE/AE/NkSl bleiben intern fuer Pruefung/Report.
    ars_mapping: bool = False
    seller_id: str = ""
    include_seller_assigned_id: bool = True
    # Dokument-Steuerung (früher lose per setattr gesetzt; jetzt echte Felder,
    # damit Tippfehler nicht still auf einen Default zurückfallen).
    document_title: str = "Rechnung"
    document_type_code: str = "380"
    is_draft: bool = False
    intro_text: str = ""
    reference_invoice_no: str = ""

    @property
    def effective_positions(self) -> list[InvoicePosition]:
        if self.positions:
            return self.positions
        return [InvoicePosition(description=self.service or "Leistung", quantity=Decimal("1.00"), unit="Stk.", unit_price=self.net)]

    @property
    def net_total(self) -> Decimal:
        return sum((p.line_total for p in self.effective_positions), Decimal("0.00")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @property
    def vat_amount(self) -> Decimal:
        return (self.net_total * self.vat_rate / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @property
    def gross(self) -> Decimal:
        return (self.net_total + self.vat_amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def leistungsart_content_for_position(self, pos: InvoicePosition | None = None) -> str:
        """Format des Kunden für Leistungsart-Hinweise in ZUGFeRD/CII.

        Eine Leistungsart je Rechnung: Kopfebene ExchangedDocument/IncludedNote.
        Unterschiedliche Leistungsarten je Rechnung: positionsweise in
        IncludedSupplyChainTradeLineItem/AssociatedDocumentLineDocument/IncludedNote.
        """
        lid = clean_code(getattr(pos, "leistungsart_id", "")) if pos is not None else ""
        if not lid:
            lid = clean_code(self.nksl)
        ltxt = clean_text(getattr(pos, "leistungsart_text", "")) if pos is not None else ""
        if not ltxt:
            # Ohne positionsweise abweichenden Leistungsart-Text gilt die Rechnungs-/Zeilen-
            # Standardleistungsart. So bleibt eine Rechnung mit mehreren Positionen, aber
            # nur einer Leistungsart, sauber auf Kopfebene.
            ltxt = clean_text(self.service)
        if not ltxt and pos is not None:
            ltxt = clean_text(getattr(pos, "description", ""))
        if not ltxt:
            ltxt = "Leistung"
        return f"Leistungsart: LeistungsartID:{lid};LeistungsartTEXT:{ltxt}"

    @property
    def leistungsart_content(self) -> str:
        return self.leistungsart_content_for_position(None)

    @property
    def leistungsart_contents_for_positions(self) -> list[str]:
        if not self.sap_mapping:
            return []
        contents = []
        for pos in self.effective_positions:
            lid = clean_code(pos.leistungsart_id) or clean_code(self.nksl)
            if lid:
                contents.append(self.leistungsart_content_for_position(pos))
        return contents

    @property
    def has_multiple_leistungsarten(self) -> bool:
        contents = self.leistungsart_contents_for_positions
        return len(set(contents)) > 1


def default_positions(net: Decimal | None = None, service: str = "Hausreinigung") -> list[InvoicePosition]:
    return [InvoicePosition(description=service or "Leistung", quantity=Decimal("1.00"), unit="Stk.", unit_price=net if net is not None else Decimal("10.00"))]


def clone_position(p: InvoicePosition) -> InvoicePosition:
    """Erzeugt eine unabhängige Kopie einer Position.

    Kopiert bewusst ALLE Felder inkl. ``long_text`` (BT-154) – dieses fehlte
    früher, wodurch die Leistungsbeschreibung im Excel-Massenlauf still verloren
    ging.
    """
    return InvoicePosition(
        p.description,
        p.quantity,
        p.unit,
        p.unit_price,
        p.article_no,
        getattr(p, "leistungsart_id", ""),
        getattr(p, "leistungsart_text", ""),
        getattr(p, "we", ""),
        getattr(p, "ae", ""),
        getattr(p, "nksl", ""),
        getattr(p, "sachkonto", ""),
        getattr(p, "long_text", ""),
    )


def normalize_ars_service(value: Any) -> str:
    """Normalisiert Leistungsbezeichnungen auf die drei ARS-Leistungsarten."""
    txt = clean_text(value).lower()
    txt = txt.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    if "winter" in txt:
        return "Winterdienst"
    if "heck" in txt or "schnitt" in txt:
        return "Heckenschnitt"
    if "reinig" in txt or "haus" in txt:
        return "Reinigung"
    return clean_text(value)


def service_key(value: Any) -> str:
    return normalize_ars_service(value).lower().strip()


def _validate_recipient(inv: InvoiceRow) -> None:
    from sedo.workflow_errors import WorkflowError
    if not all(clean_text(getattr(inv, field)) for field in
               ('buyer_name', 'buyer_street', 'buyer_postcode', 'buyer_city', 'buyer_country')):
        raise WorkflowError('Empfängername und vollständige Empfängeradresse fehlen. Bitte die Kundendaten vor der Belegerzeugung ergänzen.')


def format_period_de(period_start: datetime | None, period_end: datetime | None) -> str:
    """Deutsche Beschriftung eines Leistungszeitraums.

    Gleicher Monat/Jahr → "Mai 2026", sonst "01.05.2026 - 31.05.2026".
    Leer, wenn kein vollständiger Zeitraum vorliegt.
    """
    if period_start is None or period_end is None:
        return ""
    if period_start.year == period_end.year and period_start.month == period_end.month:
        return f"{MONTHS_DE[period_start.month]} {period_start.year}"
    return f"{period_start.strftime('%d.%m.%Y')} - {period_end.strftime('%d.%m.%Y')}"
