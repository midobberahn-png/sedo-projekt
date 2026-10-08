#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kernlogik für ZUGFeRD-/Factur-X-Rechnungen (XML + PDF).

Dieses Modul kapselt die fachliche Erzeugung. Der Absender wird über einen
validierten Snapshot aus der lokalen Anwendungskonfiguration bezogen:

- ``build_xml``          – CII/ZUGFeRD-XML (EN 16931) aus einer ``InvoiceRow``
- ``create_pdf``         – sichtbare PDF-Rechnung im SEDO-Design
- ``embed_xml_into_pdf`` – bettet die XML als Factur-X-Anhang in das PDF ein
- ``load_rows``          – liest einen Excel-Massenlauf ein

Die Web-App (``sedo/services``) nutzt ``build_xml``/``create_pdf``/
``embed_xml_into_pdf`` direkt; ``main()`` ist nur der eigenständige CLI-Einstieg.

Hinweis: Die Einbettung nutzt eine ZUGFeRD-/Factur-X-konforme Attachment-
Struktur. Externe Schema-/Schematron-Validierung bleibt empfohlen.
"""

from __future__ import annotations

import argparse
import io
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from pathlib import Path
from typing import Any

import pandas as pd
from lxml import etree
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

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

from sedo.company_profile import SELLER, seller_document

BUYER = {'id': '', 'name': '', 'street': '', 'postcode': '', 'city': '', 'country': 'DE'}

# Deutsche Monatsnamen für Perioden-Beschriftungen (XML-Titel + PDF).
MONTHS_DE = {
    1: "Januar", 2: "Februar", 3: "März", 4: "April", 5: "Mai", 6: "Juni",
    7: "Juli", 8: "August", 9: "September", 10: "Oktober", 11: "November", 12: "Dezember",
}

# Abbildung gängiger Einheiten auf UN/ECE-Rec-20-Codes (BT-130, ZUGFeRD).
# Bisher wurde pauschal "H87" (Stück) gesendet – auch für m², Monat usw.
# Der Schlüssel ist die kleingeschriebene, von Punkten befreite Einheit.
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


def read_ars_mapping(input_xlsx: Path) -> list[dict[str, str]]:
    """Liest die Aareon/ARS-Mappingdatei des Kunden ein.

    Die gelieferte Datei hat in der ersten Excel-Zeile technische Spaltennamen
    (Objektfeld/Ausgangsfeld...) und in der ersten Datenzeile die eigentlichen
    Fachspalten. Wir erkennen diese Struktur automatisch.
    """
    raw = pd.read_excel(input_xlsx, sheet_name=0, dtype=object)
    if raw.empty:
        return []
    first = [clean_text(v) for v in raw.iloc[0].tolist()]
    if first and any("Rechnungssteller Abrechnungseinheit" in v for v in first):
        df = raw.iloc[1:].copy()
        df.columns = first
    else:
        df = raw.copy()
    aliases = {
        "objekt": ["Rechnungssteller Abrechnungseinheit", "Objekt", "Abrechnungseinheit Objekt"],
        "leistungsart": ["Leistungsart-ID", "Leistungsart", "Leistung"],
        "buchungskreis": ["Buchungskreis"],
        "vertrag": ["ERP-Vertragsnummer", "Vertragsnummer"],
        "we": ["Wirtschaftseinheit", "WE"],
        "ae": ["Abrechnungseinheit", "AE"],
        "nksl": ["Nebenkostenschlüssel", "NkSl", "NKSL"],
        "sachkonto": ["Sachkonto"],
        "gueltig_ab": ["gültig ab", "gueltig ab", "Gültig ab"],
    }
    def getcol(row, names):
        for n in names:
            if n in df.columns:
                return row.get(n)
        return ""
    records: list[dict[str, str]] = []
    for _, row in df.iterrows():
        obj = clean_text(getcol(row, aliases["objekt"]))
        service = normalize_ars_service(getcol(row, aliases["leistungsart"]))
        if not obj or not service:
            continue
        records.append({
            "object_name": obj,
            "service": service,
            "buchungskreis": clean_code(getcol(row, aliases["buchungskreis"])),
            "vertrag": clean_code(getcol(row, aliases["vertrag"])),
            "we": clean_code(getcol(row, aliases["we"])),
            "ae": clean_code(getcol(row, aliases["ae"])),
            "nksl": clean_code(getcol(row, aliases["nksl"])),
            "sachkonto": clean_code(getcol(row, aliases["sachkonto"])),
            "gueltig_ab": clean_text(getcol(row, aliases["gueltig_ab"])),
        })
    return records


def default_ars_positions() -> list[InvoicePosition]:
    # Standard: alle drei Leistungen mit Platzhalter 10 EUR. Du kannst in der
    # Positionsmaske Leistungen löschen, wenn nur einzelne/teilweise Leistungen
    # je Lauf abgerechnet werden sollen.
    return [
        InvoicePosition("Reinigung", Decimal("1.00"), "Stk.", Decimal("10.00"), "", "Reinigung", "Reinigung"),
        InvoicePosition("Winterdienst", Decimal("1.00"), "Stk.", Decimal("10.00"), "", "Winterdienst", "Winterdienst"),
        InvoicePosition("Heckenschnitt", Decimal("1.00"), "Stk.", Decimal("10.00"), "", "Heckenschnitt", "Heckenschnitt"),
    ]


def load_rows_from_ars_mapping(
    input_xlsx: Path,
    limit: int | None = None,
    invoice_prefix: str | None = None,
    positions_template: list[InvoicePosition] | None = None,
) -> list[InvoiceRow]:
    mapping = read_ars_mapping(input_xlsx)
    if not mapping:
        raise ValueError("Die Aareon/ARS-Mappingdatei konnte nicht gelesen werden oder enthält keine Mapping-Zeilen.")
    template = positions_template if positions_template else default_ars_positions()
    # Welche Leistungen sollen in diesem Lauf pro Objekt abgerechnet werden?
    desired: list[tuple[str, InvoicePosition]] = []
    for p in template:
        key = service_key(p.leistungsart_id or p.leistungsart_text or p.description)
        if key:
            desired.append((key, p))
    if not desired:
        desired = [(service_key(p.leistungsart_id), p) for p in default_ars_positions()]

    grouped: dict[str, list[dict[str, str]]] = {}
    for rec in mapping:
        grouped.setdefault(rec["object_name"], []).append(rec)

    rows: list[InvoiceRow] = []
    for idx, object_name in enumerate(sorted(grouped.keys(), key=lambda x: x.lower()), start=1):
        recs = grouped[object_name]
        by_service = {service_key(r["service"]): r for r in recs}
        positions: list[InvoicePosition] = []
        first_rec: dict[str, str] | None = None
        for key, template_pos in desired:
            rec = by_service.get(key)
            if not rec:
                continue
            first_rec = first_rec or rec
            service = rec["service"] or normalize_ars_service(template_pos.description)
            positions.append(InvoicePosition(
                template_pos.description or service,
                template_pos.quantity,
                template_pos.unit,
                template_pos.unit_price,
                template_pos.article_no or rec.get("nksl", ""),
                service,
                service,
                rec.get("we", ""),
                rec.get("ae", ""),
                rec.get("nksl", ""),
                rec.get("sachkonto", ""),
            ))
        if not positions or first_rec is None:
            continue
        invoice_no = f"{invoice_prefix}{len(rows)+1:04d}" if invoice_prefix else ""
        inv = InvoiceRow(
            invoice_no=invoice_no,
            object_name=object_name,
            we=first_rec.get("we", ""),
            # ShipToTradeParty/ID soll aus Spalte A stammen, nicht aus der internen
            # GWG-Spalte "Abrechnungseinheit".
            ae=object_name,
            nksl=first_rec.get("nksl", ""),
            service=positions[0].leistungsart_text if len(positions) == 1 else "Mehrere Leistungen",
            net=Decimal("0.00"),
            vat_rate=Decimal("19.00"),
            positions=positions,
            # In der Aareon/ARS-Logik ist dies der Wert aus der Spalte
            # "Rechnungssteller Abrechnungseinheit" und damit genau der
            # Mapping-Schluessel, den SEDO sendet.
            object_street=object_name,
            object_postcode=BUYER["postcode"],
            object_city=BUYER["city"],
            object_country="DE",
            sap_mapping=True,
            ars_mapping=True,
        )
        rows.append(inv)
        if limit and len(rows) >= limit:
            break
    return rows


def load_rows(
    input_xlsx: Path,
    limit: int | None = None,
    net_override: Decimal | None = None,
    invoice_prefix: str | None = None,
    positions_template: list[InvoicePosition] | None = None,
    ars_mapping_mode: bool = False,
) -> list[InvoiceRow]:
    if ars_mapping_mode:
        return load_rows_from_ars_mapping(input_xlsx, limit=limit, invoice_prefix=invoice_prefix, positions_template=positions_template)
    df = pd.read_excel(input_xlsx, sheet_name="Rechnungslauf")
    # "Aktiv" robust auswerten: pandas liest die Spalte je nach Datei als Text
    # oder als Zahl (1.0). Trailing-Spaces und "1.0" dürfen nicht still
    # herausfallen.
    aktiv = df["Aktiv"].astype(str).str.strip().str.lower()
    df = df[aktiv.isin(["ja", "j", "true", "wahr", "x", "1", "1.0"])]
    rows: list[InvoiceRow] = []
    for i, (_, r) in enumerate(df.iterrows(), start=1):
        invoice_no = clean_text(r.get("Rechnungsnummer"))
        if invoice_prefix:
            invoice_no = f"{invoice_prefix}{i:04d}"
        service = clean_text(r.get("Leistung")) or "Hausreinigung"
        net_value = net_override if net_override is not None else D(r.get("Netto"), "10.00")
        template = positions_template if positions_template else default_positions(net_value, service)
        # Kopie der Positionsvorlage je Rechnung, damit später Rechnungsspezifika möglich bleiben
        positions = [clone_position(p) for p in template]
        inv = InvoiceRow(
            invoice_no=invoice_no,
            object_name=clean_text(r.get("Objekt")),
            we=clean_code(r.get("WE")) or clean_code(r.get("ShipToTradeParty/ID")),
            ae=clean_code(r.get("AE")),
            nksl=clean_code(r.get("NkSl")),
            service=service,
            net=net_value,
            vat_rate=D(r.get("USt-Satz"), "19.00"),
            positions=positions,
            object_street=clean_text(r.get("Objekt Straße")) or clean_text(r.get("Straße")) or clean_text(r.get("Objekt")),
            object_postcode=clean_text(r.get("Objekt PLZ")) or BUYER["postcode"],
            object_city=clean_text(r.get("Objekt Stadt")) or BUYER["city"],
            object_country=clean_text(r.get("Objekt Land")) or "DE",
            sap_mapping=True,
        )
        rows.append(inv)
        if limit and len(rows) >= limit:
            break
    return rows


def _validate_recipient(inv: InvoiceRow) -> None:
    from sedo.workflow_errors import WorkflowError
    if not all(clean_text(getattr(inv, field)) for field in
               ('buyer_name', 'buyer_street', 'buyer_postcode', 'buyer_city', 'buyer_country')):
        raise WorkflowError('Empfängername und vollständige Empfängeradresse fehlen. Bitte die Kundendaten vor der Belegerzeugung ergänzen.')


@seller_document
def build_xml(inv: InvoiceRow, issue_date: datetime, period_start: datetime | None, period_end: datetime | None, due_date: datetime) -> bytes:
    """Erzeugt die CII/ZUGFeRD-XML (EN 16931) als UTF-8-Bytes.

    Setzt u. a. BT-1 (Rechnungsnummer), BT-130 (Menge/Einheit), Leistungsart-
    Notes und den Leistungszeitraum. Eine fehlende Rechnungsnummer (BT-1 ist
    Pflicht) führt zu einer klaren ``ValueError`` statt einer strukturell
    ungültigen Rechnung.
    """
    if not clean_text(inv.invoice_no):
        raise ValueError("Rechnungsnummer fehlt (BT-1 ist Pflichtfeld).")
    _validate_recipient(inv)
    root = etree.Element(qn("rsm", "CrossIndustryInvoice"), nsmap=NSMAP)

    ctx = sub(root, "rsm", "ExchangedDocumentContext")
    bp = sub(ctx, "ram", "BusinessProcessSpecifiedDocumentContextParameter")
    sub(bp, "ram", "ID", "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0")
    gp = sub(ctx, "ram", "GuidelineSpecifiedDocumentContextParameter")
    sub(gp, "ram", "ID", "urn:cen.eu:en16931:2017#conformant#urn:factur-x.eu:1p0:extended")

    doc = sub(root, "rsm", "ExchangedDocument")
    sub(doc, "ram", "ID", inv.invoice_no)
    # ARP-Leitfaden S. 12: ExchangedDocument/Name = Rechnungstext.
    # Titel + Leistungsperiode, damit ARP den Vorgang klar benennen kann.
    # Rechnungstext verwendet den tatsächlichen Leistungsort.
    # Im Rechnungstext wird das OBJEKT gebraucht, an dem die Leistung erbracht
    # wurde – der Sachbearbeiter erkennt daran, worum es geht. Vorher stand dort
    # nur "Rechnung September 2026".
    #
    # Reihenfolge Belegart · Objekt · Zeitraum. Die Belegart bleibt bewusst vorn:
    # Ohne sie wäre einem Storno im Klartext nicht mehr anzusehen, dass es eines
    # ist – das stünde dann nur noch im TypeCode 457.
    # Ohne Objekt (Rechnung ohne Leistungsort) bleibt der Titel wie bisher.
    doc_title = inv.document_title or "Rechnung"
    objekt = clean_text(getattr(inv, "object_name", "") or "")
    if objekt:
        doc_title = f"{doc_title} {objekt}"
    period_label = format_period_de(period_start, period_end)
    if period_label:
        doc_title = f"{doc_title} · {period_label}"
    sub(doc, "ram", "Name", doc_title)
    sub(doc, "ram", "TypeCode", inv.document_type_code or "380")
    issue = sub(doc, "ram", "IssueDateTime")
    sub(issue, "udt", "DateTimeString", issue_date.strftime("%Y%m%d"), format="102")

    # Aareon/GWG-Anforderung: Leistungsart-ID in ZUGFeRD XML.
    # Wenn alle Positionen dieselbe Leistungsart haben, wird sie auf Kopfebene übertragen:
    # CrossIndustryInvoice/ExchangedDocument/IncludedNote/Content/Leistungsart:...
    # Wenn unterschiedliche Leistungsarten enthalten sind, werden sie positionsweise übertragen:
    # IncludedSupplyChainTradeLineItem/AssociatedDocumentLineDocument/IncludedNote/Content
    if inv.sap_mapping and inv.leistungsart_contents_for_positions and not inv.has_multiple_leistungsarten:
        note = sub(doc, "ram", "IncludedNote")
        sub(note, "ram", "Content", inv.leistungsart_contents_for_positions[0])

    note2 = sub(doc, "ram", "IncludedNote")
    sub(note2, "ram", "Content", f"{SELLER['name']}\n{SELLER['street']}\n{SELLER['postcode']} {SELLER['city']}\nGeschäftsführung: {SELLER['ceo']}\nHandelsregister: {SELLER['register']}")
    sub(note2, "ram", "SubjectCode", "REG")

    tx = sub(root, "rsm", "SupplyChainTradeTransaction")

    for idx, pos in enumerate(inv.effective_positions, start=1):
        line = sub(tx, "ram", "IncludedSupplyChainTradeLineItem")
        line_doc = sub(line, "ram", "AssociatedDocumentLineDocument")
        sub(line_doc, "ram", "LineID", str(idx))
        if inv.sap_mapping and inv.has_multiple_leistungsarten:
            line_note = sub(line_doc, "ram", "IncludedNote")
            sub(line_note, "ram", "Content", inv.leistungsart_content_for_position(pos))
        prod = sub(line, "ram", "SpecifiedTradeProduct")
        if inv.include_seller_assigned_id and pos.article_no:
            sub(prod, "ram", "SellerAssignedID", pos.article_no)
        sub(prod, "ram", "Name", pos.description)
        # BT-154: Leistungsbeschreibung, falls hinterlegt.
        _long = clean_text(getattr(pos, "long_text", "") or "")
        if _long:
            sub(prod, "ram", "Description", _long)
        # Leistungsart wird nach Kundenleitfaden als IncludedNote übertragen,
        # nicht als Produktbeschreibung.
        agr = sub(line, "ram", "SpecifiedLineTradeAgreement")
        price = sub(agr, "ram", "NetPriceProductTradePrice")
        sub(price, "ram", "ChargeAmount", money(pos.unit_price))
        delivery_line = sub(line, "ram", "SpecifiedLineTradeDelivery")
        sub(delivery_line, "ram", "BilledQuantity", money(pos.quantity), unitCode=unece_unit_code(pos.unit))
        settlement_line = sub(line, "ram", "SpecifiedLineTradeSettlement")
        tax = sub(settlement_line, "ram", "ApplicableTradeTax")
        sub(tax, "ram", "TypeCode", "VAT")
        sub(tax, "ram", "CategoryCode", "S")
        sub(tax, "ram", "RateApplicablePercent", money(inv.vat_rate))
        summ = sub(settlement_line, "ram", "SpecifiedTradeSettlementLineMonetarySummation")
        sub(summ, "ram", "LineTotalAmount", money(pos.line_total))

    header_agr = sub(tx, "ram", "ApplicableHeaderTradeAgreement")
    seller = sub(header_agr, "ram", "SellerTradeParty")
    # The business identifier is entered locally, never fabricated from tax
    # or bank data. An explicit recipient-specific Aareon ID takes priority.
    seller_identifier = getattr(inv, "seller_id", "") or SELLER['seller_id']
    if seller_identifier:
        sub(seller, "ram", "ID", seller_identifier)
    sub(seller, "ram", "Name", SELLER["name"])
    contact = sub(seller, "ram", "DefinedTradeContact")
    sub(contact, "ram", "PersonName", SELLER["ceo"])
    tel = sub(contact, "ram", "TelephoneUniversalCommunication")
    sub(tel, "ram", "CompleteNumber", SELLER["phone"])
    email = sub(contact, "ram", "EmailURIUniversalCommunication")
    sub(email, "ram", "URIID", SELLER["email"])
    seller_addr = sub(seller, "ram", "PostalTradeAddress")
    sub(seller_addr, "ram", "PostcodeCode", SELLER["postcode"])
    sub(seller_addr, "ram", "LineOne", SELLER["street"])
    sub(seller_addr, "ram", "CityName", SELLER["city"])
    sub(seller_addr, "ram", "CountryID", SELLER["country"])
    uri = sub(seller, "ram", "URIUniversalCommunication")
    sub(uri, "ram", "URIID", SELLER["email"], schemeID="EM")
    if SELLER["tax_no"]:
        reg = sub(seller, "ram", "SpecifiedTaxRegistration")
        sub(reg, "ram", "ID", SELLER["tax_no"], schemeID="FC")
    if SELLER["vat_id"]:
        reg2 = sub(seller, "ram", "SpecifiedTaxRegistration")
        sub(reg2, "ram", "ID", SELLER["vat_id"], schemeID="VA")

    buyer = sub(header_agr, "ram", "BuyerTradeParty")
    if inv.buyer_id:
        sub(buyer, "ram", "ID", inv.buyer_id)
    sub(buyer, "ram", "Name", inv.buyer_name)
    buyer_addr = sub(buyer, "ram", "PostalTradeAddress")
    sub(buyer_addr, "ram", "PostcodeCode", inv.buyer_postcode)
    sub(buyer_addr, "ram", "LineOne", inv.buyer_street)
    sub(buyer_addr, "ram", "CityName", inv.buyer_city)
    sub(buyer_addr, "ram", "CountryID", country_code(inv.buyer_country))

    # Leistungsort / Objektanschrift. Für den GWG/Excel-Massenlauf zusätzlich mit WE-ID.
    header_delivery = sub(tx, "ram", "ApplicableHeaderTradeDelivery")
    if inv.object_name or inv.object_street or inv.sap_mapping:
        shipto = sub(header_delivery, "ram", "ShipToTradeParty")
        # Alte SAP-Testlogik: WE als technische ShipTo-ID.
        # Aareon/GWG-Logik laut Screenshot/Support:
        #   ShipToTradeParty/ID   = ID der Abrechnungseinheit aus dem Mapping
        #                           (Spalte "Abrechnungseinheit" / ae_internal).
        #   ShipToTradeParty/Name = Objekt-/Abrechnungseinheitsname, z. B. "Ahornweg 1".
        #   PostalTradeAddress    = tatsächliche Objektadresse bzw. Objektname.
        billing_unit_name = clean_text(inv.object_name or inv.object_street)
        billing_unit_id = clean_code(getattr(inv, "ae", "")) or clean_code(getattr(inv, "we", ""))
        if getattr(inv, "ars_mapping", False):
            # GWG/Aareon: ID der Abrechnungseinheit kommt aus Spalte A
            # "Rechnungssteller Abrechnungseinheit". Deshalb ist inv.ae im Weblauf
            # bewusst auf diesen Wert gesetzt; im Excel-Massenlauf verwenden wir
            # notfalls object_name/billing_unit_name.
            sub(shipto, "ram", "ID", billing_unit_id or billing_unit_name)
        elif inv.sap_mapping and inv.we:
            sub(shipto, "ram", "ID", inv.we)
        if inv.object_name:
            sub(shipto, "ram", "Name", inv.object_name)
        elif billing_unit_name:
            sub(shipto, "ram", "Name", billing_unit_name)
        ship_addr = sub(shipto, "ram", "PostalTradeAddress")
        line_one = inv.object_street or inv.object_name or inv.buyer_street
        # Wichtig fuer Factur-X/ZUGFeRD EXTENDED: PostalTradeAddress hat eine feste XSD-Reihenfolge.
        # PostcodeCode darf nicht nach LineOne kommen, sonst meldet der Validator z.B.
        # "one of LineTwo, LineThree, CityName, CountryID".
        sub(ship_addr, "ram", "PostcodeCode", inv.object_postcode or inv.buyer_postcode)
        sub(ship_addr, "ram", "LineOne", line_one)
        sub(ship_addr, "ram", "CityName", inv.object_city or inv.buyer_city)
        sub(ship_addr, "ram", "CountryID", country_code(inv.object_country or inv.buyer_country))
    # Leistungserbringungsdatum ist laut ARP-Leitfaden Pflichtfeld (1..1).
    # Wenn kein Leistungszeitraum angegeben ist, gilt das Rechnungsdatum als Lieferdatum.
    actual = sub(header_delivery, "ram", "ActualDeliverySupplyChainEvent")
    occ = sub(actual, "ram", "OccurrenceDateTime")
    delivery_date = period_start if period_start is not None else issue_date
    sub(occ, "udt", "DateTimeString", delivery_date.strftime("%Y%m%d"), format="102")

    settlement = sub(tx, "ram", "ApplicableHeaderTradeSettlement")
    sub(settlement, "ram", "InvoiceCurrencyCode", "EUR")
    # Die Referenz auf die Ursprungsrechnung (BT-25) wird erst weiter unten
    # angehängt – hinter der Summation. Begründung dort.
    ref_invoice_no = clean_text(inv.reference_invoice_no)
    means = sub(settlement, "ram", "SpecifiedTradeSettlementPaymentMeans")
    sub(means, "ram", "TypeCode", "58")
    sub(means, "ram", "Information", "Zahlung per SEPA Überweisung.")
    acc = sub(means, "ram", "PayeePartyCreditorFinancialAccount")
    sub(acc, "ram", "IBANID", SELLER["iban"])
    sub(acc, "ram", "AccountName", SELLER["name"])
    bank = sub(means, "ram", "PayeeSpecifiedCreditorFinancialInstitution")
    sub(bank, "ram", "BICID", SELLER["bic"])

    htax = sub(settlement, "ram", "ApplicableTradeTax")
    sub(htax, "ram", "CalculatedAmount", money(inv.vat_amount))
    sub(htax, "ram", "TypeCode", "VAT")
    sub(htax, "ram", "BasisAmount", money(inv.net_total))
    sub(htax, "ram", "CategoryCode", "S")
    sub(htax, "ram", "RateApplicablePercent", money(inv.vat_rate))

    # Abrechnungs-/Leistungszeitraum nur ausgeben, wenn beide Werte befüllt sind.
    if period_start is not None and period_end is not None:
        period = sub(settlement, "ram", "BillingSpecifiedPeriod")
        ps = sub(period, "ram", "StartDateTime")
        sub(ps, "udt", "DateTimeString", period_start.strftime("%Y%m%d"), format="102")
        pe = sub(period, "ram", "EndDateTime")
        sub(pe, "udt", "DateTimeString", period_end.strftime("%Y%m%d"), format="102")

    terms = sub(settlement, "ram", "SpecifiedTradePaymentTerms")
    # Aareon/ARP übernimmt das Fälligkeitsdatum sicher, wenn in der Description
    # der Wert "Faelligkeitsdatum" enthalten ist.
    sub(terms, "ram", "Description", "Faelligkeitsdatum")
    due = sub(terms, "ram", "DueDateDateTime")
    sub(due, "udt", "DateTimeString", due_date.strftime("%Y%m%d"), format="102")

    sm = sub(settlement, "ram", "SpecifiedTradeSettlementHeaderMonetarySummation")
    sub(sm, "ram", "LineTotalAmount", money(inv.net_total))
    sub(sm, "ram", "ChargeTotalAmount", "0.00")
    sub(sm, "ram", "AllowanceTotalAmount", "0.00")
    sub(sm, "ram", "TaxBasisTotalAmount", money(inv.net_total))
    sub(sm, "ram", "TaxTotalAmount", money(inv.vat_amount), currencyID="EUR")
    sub(sm, "ram", "RoundingAmount", "0.00")
    sub(sm, "ram", "GrandTotalAmount", money(inv.gross))
    sub(sm, "ram", "DuePayableAmount", money(inv.gross))

    # Storno-/Korrekturrechnung: Verweis auf die Ursprungsrechnung (BT-25).
    #
    # Die Reihenfolge ist hier keine Geschmacksfrage. Das CII-D16B-Schema legt
    # für ram:ApplicableHeaderTradeSettlement eine feste xs:sequence fest, in
    # der ram:InvoiceReferencedDocument an Position 31 steht – NACH
    # ram:SpecifiedTradeSettlementHeaderMonetarySummation (Position 29). Stand
    # das Element weiter oben (direkt hinter InvoiceCurrencyCode), brach die
    # Validierung beim nächsten Geschwisterelement ab:
    #   Element '{...ram}SpecifiedTradeSettlementPaymentMeans': This element is
    #   not expected. Expected is one of ( ...ProFormaInvoiceReferencedDocument,
    #   ...LetterOfCreditReferencedDocument, ... )
    # Damit war JEDE Storno- (457) und Korrekturrechnung (384) als E-Rechnung
    # schemaungültig – genau die Belege, mit denen Aareon/GWG Buchungen
    # aufheben (Review-Fund F-04, gegen das Original-XSD nachgestellt).
    #
    # Dieselbe Falle steckt bei PostalTradeAddress (siehe Kommentar oben); dort
    # war sie schon bedacht. BT-26 (FormattedIssueDateTime der Ursprungs-
    # rechnung) ist optional und bewusst nicht gesetzt.
    if ref_invoice_no:
        refdoc = sub(settlement, "ram", "InvoiceReferencedDocument")
        sub(refdoc, "ram", "IssuerAssignedID", ref_invoice_no)

    return etree.tostring(root, pretty_print=True, xml_declaration=True, encoding="UTF-8")


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


def draw_wrapped(c, text: str, x: float, y: float, max_width: float, font: str = "Helvetica", size: int = 8.5, leading: float | None = None) -> float:
    if leading is None:
        leading = size + 3
    c.setFont(font, size)
    words = str(text).split()
    line = ""
    for word in words:
        test = f"{line} {word}".strip()
        if c.stringWidth(test, font, size) <= max_width:
            line = test
        else:
            if line:
                c.drawString(x, y, line)
                y -= leading
            line = word
    if line:
        c.drawString(x, y, line)
        y -= leading
    return y


# Schlichter PDF-Stil: optionaler Farbakzent (Dokumenttitel, Gesamtbetrag).
# None = komplett monochrom (gewählte Variante). Für einen dezenten Blau-Akzent
# auf (0.043, 0.361, 0.678) setzen. Synchron mit services/pdf_chrome.py halten.
PDF_ACCENT: tuple | None = None


@seller_document
def create_pdf(inv: InvoiceRow, out_pdf: Path, issue_date: datetime, period_start: datetime | None, period_end: datetime | None, due_date: datetime) -> None:
    """Erzeugt die sichtbare PDF-Rechnung im schlichten SEDO-Briefbogen-Stil.

    Reduziert: weisser Grund, feine Haarlinien, kleines Logo, viel Weissraum -
    keine Farbflaechen. Einziger Farbakzent (Titel, Gesamtbetrag) ueber
    ``PDF_ACCENT`` steuerbar. ENTWURF-Wasserzeichen bei inv.is_draft = True.
    """
    _validate_recipient(inv)
    # ── Farbwerte (RGB 0-1) ──────────────────────────────────────────────
    INK = (0.10, 0.11, 0.13)      # Fliesstext
    MUT = (0.42, 0.45, 0.50)      # gedaempfte Beschriftungen
    FAINT = (0.60, 0.63, 0.68)    # Kleingedrucktes
    HAIR = (0.82, 0.84, 0.87)     # Haarlinien
    ACCENT = PDF_ACCENT if PDF_ACCENT else INK

    c = canvas.Canvas(str(out_pdf), pagesize=A4)
    w, h = A4
    M = 22 * mm
    left = M
    right = w - M
    HEADER_H = 32 * mm            # Kopfzone inkl. Haarlinie
    FOOTER_H = 20 * mm            # Fusszone inkl. Haarlinie

    doc_type = inv.document_type_code or "380"
    doc_title = inv.document_title or "Rechnung"
    is_storno = doc_type == "457"
    is_draft = inv.is_draft
    logo_path = Path(__file__).resolve().parent.parent / "assets" / "sedo_logo.png"
    vat_pct = f"{float(inv.vat_rate):.10g}".replace(".", ",")

    def fc(*rgb): c.setFillColorRGB(*rgb)
    def sc(*rgb): c.setStrokeColorRGB(*rgb)

    def hairline(y_pos: float) -> None:
        sc(*HAIR); c.setLineWidth(0.5)
        c.line(left, y_pos, right, y_pos)
        c.setLineWidth(1)

    # ── Kopf: kleines Logo links, Absenderblock rechts, Haarlinie ────────
    def draw_header():
        top = h - 12 * mm
        if logo_path.exists():
            c.drawImage(str(logo_path), left, top - 10 * mm,
                        width=24 * mm, height=12 * mm,
                        preserveAspectRatio=True, anchor="sw", mask="auto")
        else:
            fc(*ACCENT); c.setFont("Helvetica-Bold", 15)
            c.drawString(left, top - 5 * mm, "SEDO")
        fc(*INK); c.setFont("Helvetica-Bold", 9.5)
        c.drawRightString(right, top - 1 * mm, SELLER["name"])
        fc(*MUT); c.setFont("Helvetica-Oblique", 7)
        c.drawRightString(right, top - 5 * mm, "Sauberes Handwerk · Rund um Ihr Objekt")
        c.setFont("Helvetica", 7)
        c.drawRightString(right, top - 9 * mm,
                          f"{SELLER['street']} · {SELLER['postcode']} {SELLER['city']} · "
                          f"Tel. {SELLER['phone']} · {SELLER['email']}")
        hairline(h - HEADER_H + 4 * mm)

    # ── Fuss: Haarlinie + drei Spalten Kleingedrucktes ───────────────────
    def draw_footer():
        hairline(FOOTER_H - 4 * mm)
        fc(*FAINT); c.setFont("Helvetica", 6.6)
        col2 = left + (right - left) * 0.40
        col3 = left + (right - left) * 0.72
        yy = FOOTER_H - 8 * mm
        for line in [SELLER["name"],
                     f"{SELLER['street']} · {SELLER['postcode']} {SELLER['city']}",
                     f"GF: {SELLER['ceo']} · {SELLER['register']}"]:
            c.drawString(left, yy, line); yy -= 3.1 * mm
        yy = FOOTER_H - 8 * mm
        for line in [SELLER.get('bank_name') or 'Bankverbindung',
                     f"IBAN {SELLER['iban']}",
                     f"BIC {SELLER['bic']}"]:
            c.drawString(col2, yy, line); yy -= 3.1 * mm
        yy = FOOTER_H - 8 * mm
        for line in [f"Tel. {SELLER['phone']}",
                     SELLER["email"],
                     f"St.-Nr. {SELLER['tax_no']} · USt-IdNr. {SELLER['vat_id']}"]:
            c.drawString(col3, yy, line); yy -= 3.1 * mm

    def draw_watermark(text: str = "ENTWURF"):
        c.saveState()
        c.translate(w / 2, h / 2); c.rotate(42)
        c.setFillColorRGB(0.90, 0.90, 0.90)
        c.setFont("Helvetica-Bold", 88)
        c.drawCentredString(0, 0, text)
        c.restoreState()

    # ── Tabellen-Spalten ─────────────────────────────────────────────────
    COL_QTY_R = left + 20 * mm
    COL_UNIT  = left + 23 * mm
    COL_DESC  = left + 44 * mm
    COL_EP_R  = right - 26 * mm

    def draw_table_header(y_pos: float) -> float:
        fc(*MUT); c.setFont("Helvetica-Bold", 7)
        c.drawString(left + 1 * mm, y_pos, "POS.")
        c.drawRightString(COL_QTY_R, y_pos, "MENGE")
        c.drawString(COL_UNIT, y_pos, "EINHEIT")
        c.drawString(COL_DESC, y_pos, "BEZEICHNUNG")
        c.drawRightString(COL_EP_R, y_pos, "EINZELPREIS")
        c.drawRightString(right, y_pos, "GESAMT")
        hairline(y_pos - 2.2 * mm)
        return y_pos - 7.5 * mm

    # ══ SEITE 1 ═══════════════════════════════════════════════════════════
    draw_header()
    if is_draft: draw_watermark()

    # Absenderzeile (klassisch ueber dem Empfaengerfeld)
    sender_y = h - HEADER_H - 4 * mm
    fc(*FAINT); c.setFont("Helvetica", 6.6)
    c.drawString(left, sender_y,
                 f"{SELLER['name']} · {SELLER['street']} · {SELLER['postcode']} {SELLER['city']}")

    # Empfaenger links
    ry = sender_y - 8 * mm
    fc(*INK); c.setFont("Helvetica-Bold", 10)
    c.drawString(left, ry, inv.buyer_name)
    c.setFont("Helvetica", 10)
    for line in [inv.buyer_name2, inv.buyer_street,
                 f"{inv.buyer_postcode} {inv.buyer_city}".strip()]:
        if line:
            ry -= 5 * mm; c.drawString(left, ry, str(line))

    # Eckdaten rechts - ohne Box, nur Typografie
    info_rows = [
        ("Rechnungsnr.",    inv.invoice_no),
        ("Rechnungsdatum",  issue_date.strftime("%d.%m.%Y")),
        ("Fällig bis",     due_date.strftime("%d.%m.%Y")),
        ("Kunden-Nr.",      inv.buyer_id or "—"),
    ]
    period_lbl = format_period_de(period_start, period_end)
    if period_lbl:
        info_rows.append(("Zeitraum", period_lbl))
    label_x = right - 62 * mm
    value_x = right - 34 * mm
    iy = sender_y - 2 * mm
    for lbl, val in info_rows:
        fc(*MUT); c.setFont("Helvetica", 8)
        c.drawString(label_x, iy, lbl)
        fc(*INK); c.setFont("Helvetica-Bold", 8)
        c.drawString(value_x, iy, str(val))
        iy -= 4.6 * mm

    # Dokumenttitel
    y = min(ry, iy) - 12 * mm
    fc(*ACCENT); c.setFont("Helvetica-Bold", 15)
    c.drawString(left, y, doc_title)
    y -= 9 * mm

    # Leistungsort als schlichte Zeile
    obj_parts: list[str] = []
    if clean_text(getattr(inv, "object_name", "")):
        obj_parts.append(clean_text(inv.object_name))
    adr = clean_text(getattr(inv, "object_street", ""))
    if adr and adr not in obj_parts:
        obj_parts.append(adr)
    city_s = " ".join([clean_text(getattr(inv, "object_postcode", "")),
                       clean_text(getattr(inv, "object_city", ""))]).strip()
    if city_s:
        obj_parts.append(city_s)
    if obj_parts:
        fc(*MUT); c.setFont("Helvetica-Bold", 8.5)
        c.drawString(left, y, "Leistungsort")
        fc(*INK); c.setFont("Helvetica", 9)
        c.drawString(left + 26 * mm, y, " · ".join(obj_parts))
        y -= 8 * mm

    # Anrede + Einleitung
    fc(*INK); c.setFont("Helvetica", 9.5)
    c.drawString(left, y, "Sehr geehrte Damen und Herren,")
    y -= 5.5 * mm
    intro = inv.intro_text or "wir erlauben uns, folgende Leistungen in Rechnung zu stellen:"
    if is_storno:
        intro = ("wir stornieren hiermit die u. g. Rechnung. "
                 "Der ausgewiesene Betrag wird buchhalterisch aufgehoben.")
    y = draw_wrapped(c, intro, left, y, right - left, "Helvetica", 9.5, 5.2 * mm)
    y -= 6 * mm

    # ── Positionstabelle ──────────────────────────────────────────────────
    y = draw_table_header(y)

    for row_idx, pos in enumerate(inv.effective_positions, 1):
        if y < FOOTER_H + 34 * mm:
            # Seitenumbruch
            draw_footer()
            c.showPage()
            fc(*FAINT); c.setFont("Helvetica", 7)
            c.drawString(left, h - 12 * mm,
                         f"{SELLER['name']} · {doc_title} {inv.invoice_no} · Fortsetzung")
            hairline(h - 14 * mm)
            if is_draft: draw_watermark()
            y = h - 22 * mm
            y = draw_table_header(y)

        fc(*INK); c.setFont("Helvetica", 9)
        c.drawString(left + 1 * mm, y, str(row_idx))
        c.drawRightString(COL_QTY_R, y, de_qty(pos.quantity))
        c.drawString(COL_UNIT, y, pos.unit or "Stk.")

        desc_start = y
        desc_bot = draw_wrapped(c, pos.description, COL_DESC, y,
                                COL_EP_R - COL_DESC - 5 * mm, "Helvetica", 9, 4.6 * mm)
        # Leistungsbeschreibung (optional) in kleinerer, grauer Schrift darunter
        _long = clean_text(getattr(pos, "long_text", "") or "")
        if _long:
            fc(*MUT)
            desc_bot = draw_wrapped(c, _long, COL_DESC, desc_bot - 2.6 * mm,
                                    COL_EP_R - COL_DESC - 5 * mm, "Helvetica", 8, 3.9 * mm)
            fc(*INK); c.setFont("Helvetica", 9)
        fc(*MUT); c.drawRightString(COL_EP_R, desc_start, de_money(pos.unit_price))
        fc(*INK); c.setFont("Helvetica-Bold", 9)
        c.drawRightString(right, desc_start, de_money(pos.line_total))
        c.setFont("Helvetica", 9)

        row_bot = min(desc_bot + 1 * mm, y - 4.2 * mm)
        hairline(row_bot + 0.8 * mm)
        y = row_bot - 3 * mm

    # ── Summen rechtsbuendig (ohne Box) ──────────────────────────────────
    y -= 4 * mm
    if y < FOOTER_H + 32 * mm:
        draw_footer()
        c.showPage()
        if is_draft: draw_watermark()
        y = h - 26 * mm
    sum_label_x = right - 60 * mm
    fc(*MUT); c.setFont("Helvetica", 9)
    c.drawString(sum_label_x, y, "Nettobetrag")
    fc(*INK); c.drawRightString(right, y, de_money(inv.net_total))
    y -= 5.4 * mm
    fc(*MUT); c.drawString(sum_label_x, y, f"zzgl. MwSt {vat_pct} %")
    fc(*INK); c.drawRightString(right, y, de_money(inv.vat_amount))
    y -= 3.4 * mm
    sc(*INK); c.setLineWidth(0.7)
    c.line(sum_label_x, y, right, y)
    c.setLineWidth(1)
    y -= 5.8 * mm
    fc(*ACCENT); c.setFont("Helvetica-Bold", 11.5)
    c.drawString(sum_label_x, y, "Gesamtbetrag")
    c.drawRightString(right, y, de_money(inv.gross))

    # ── Zahlungshinweis + GiroCode ────────────────────────────────────────
    y -= 15 * mm
    QR_SZ = 24 * mm
    qr_ok = False

    if not is_storno:
        try:
            import qrcode as _qrc
            iban_raw = SELLER["iban"].replace(" ", "")
            epc = "\n".join(["BCD", "002", "1", "SCT",
                             SELLER["bic"], SELLER["name"], iban_raw,
                             f"EUR{money(inv.gross)}", "", inv.invoice_no,
                             f"Rechnung {inv.invoice_no}"])
            qr_img = _qrc.make(epc, box_size=4, border=1)
            buf = io.BytesIO(); qr_img.save(buf, format="PNG"); buf.seek(0)
            c.drawImage(ImageReader(buf), left, y - QR_SZ,
                        width=QR_SZ, height=QR_SZ, mask="auto")
            fc(*FAINT); c.setFont("Helvetica", 6.6)
            c.drawString(left, y - QR_SZ - 3.2 * mm, "GiroCode (SEPA-Direktzahlung)")
            qr_ok = True
        except Exception:
            pass

    text_x = left + (QR_SZ + 6 * mm if qr_ok else 0)
    fc(*INK); c.setFont("Helvetica", 9.5)
    if is_storno:
        c.drawString(left, y - 4 * mm,
            "Diese Stornorechnung hebt die referenzierte Rechnung buchhalterisch auf.")
        c.drawString(left, y - 10 * mm,
            "Für Rückfragen stehen wir Ihnen gerne zur Verfügung.")
    else:
        c.drawString(text_x, y - 4 * mm,
            f"Bitte überweisen Sie {de_money(inv.gross)} bis zum "
            f"{due_date.strftime('%d.%m.%Y')}.")
        fc(*MUT); c.setFont("Helvetica", 9)
        c.drawString(text_x, y - 10 * mm, f"IBAN {SELLER['iban']} · BIC {SELLER['bic']}")
        c.drawString(text_x, y - 15 * mm, f"Verwendungszweck: {inv.invoice_no}")
        fc(*INK); c.setFont("Helvetica", 9.5)
        c.drawString(text_x, y - 21.5 * mm, "Wir bedanken uns für Ihren Auftrag!")

    # ── Fuss + Speichern ─────────────────────────────────────────────────
    draw_footer()
    c.save()


@seller_document
def embed_xml_into_pdf(pdf_path: Path, xml_bytes: bytes, output_path: Path) -> None:
    """Bettet die CII-XML als Factur-X/ZUGFeRD-Anhang in das PDF ein.

    Diese Funktion erzeugt eine echte Associated-File-Struktur (/AF) mit
    /AFRelationship /Data und dem Standard-Dateinamen factur-x.xml. Das ist
    deutlich näher an einer validierbaren ZUGFeRD-/Factur-X-Datei als ein
    einfaches PDF-Attachment.
    """
    from pypdf.generic import NameObject, ArrayObject, create_string_object

    reader = PdfReader(str(pdf_path))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    attachment = writer.add_attachment("factur-x.xml", xml_bytes)
    filespec = attachment.pdf_object
    filespec.update({
        NameObject("/AFRelationship"): NameObject("/Data"),
        NameObject("/Desc"): create_string_object("Factur-X/ZUGFeRD invoice XML"),
        NameObject("/UF"): create_string_object("factur-x.xml"),
    })
    writer._root_object.update({NameObject("/AF"): ArrayObject([filespec])})
    writer.add_metadata({
        "/Title": pdf_path.stem,
        "/Author": SELLER["name"],
        "/Subject": "ZUGFeRD/Factur-X Rechnung",
        "/Keywords": "ZUGFeRD, Factur-X, EN16931, CII",
    })
    with open(output_path, "wb") as f:
        writer.write(f)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_xlsx", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--issue-date", default=datetime.today().strftime("%Y-%m-%d"))
    parser.add_argument("--period-start", default="2026-05-01")
    parser.add_argument("--period-end", default="2026-05-31")
    parser.add_argument("--due-days", type=int, default=14)
    parser.add_argument("--net-override", default=None, help="Netto-Betrag für eine Standardposition überschreiben, z. B. 10,00")
    parser.add_argument("--auto-invoice-numbers", action="store_true", help="Rechnungsnummern aus Leistungsmonat neu vergeben: REYYYYMM0001")
    args = parser.parse_args()

    issue_date = datetime.strptime(args.issue_date, "%Y-%m-%d")
    period_start = datetime.strptime(args.period_start, "%Y-%m-%d")
    period_end = datetime.strptime(args.period_end, "%Y-%m-%d")
    due_date = issue_date + timedelta(days=args.due_days)

    out_xml = args.output_dir / "xml"
    out_pdf = args.output_dir / "pdf"
    out_zugferd = args.output_dir / "zugferd_pdf"
    for p in [out_xml, out_pdf, out_zugferd]:
        p.mkdir(parents=True, exist_ok=True)

    net_override = D(args.net_override) if args.net_override else None
    invoice_prefix = f"RE{period_start.strftime('%Y%m')}" if args.auto_invoice_numbers else None
    rows = load_rows(args.input_xlsx, args.limit, net_override=net_override, invoice_prefix=invoice_prefix)

    print(f"Fertig vorbereitet. Zeilen: {len(rows)}")


if __name__ == "__main__":
    main()
