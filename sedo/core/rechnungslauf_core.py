#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stabiler Importpfad für ZUGFeRD-/Factur-X-Rechnungen und Excel-Massenläufe.

XML, PDF und gemeinsame Rechnungsdaten liegen in getrennten Nachbarmodulen.
Der Absender wird über einen validierten Snapshot der lokalen Konfiguration
bezogen. Bestehende Aufrufer können weiter diese Namen importieren:

- ``build_xml``          – CII/ZUGFeRD-XML (EN 16931) aus einer ``InvoiceRow``
- ``create_pdf``         – sichtbare PDF-Rechnung im SEDO-Design
- ``embed_xml_into_pdf`` – bettet die XML als Factur-X-Anhang in das PDF ein
- ``load_rows``          – liest einen Excel-Massenlauf ein

Die Web-App (``sedo/services``) nutzt diese Funktionen über den bisherigen
Importpfad; ``main()`` ist nur der eigenständige CLI-Einstieg.

Hinweis: Die Einbettung nutzt eine ZUGFeRD-/Factur-X-konforme Attachment-
Struktur. Externe Schema-/Schematron-Validierung bleibt empfohlen.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
import pandas as pd

# Public imports keep existing callers on the stable core API. XML, PDF and
# shared invoice data now evolve in separate modules without circular imports.
from .invoice_common import (
    NSMAP, RSM, RAM, UDT, SELLER, BUYER, MONTHS_DE, _UNIT_CODE_MAP,
    unece_unit_code, D, money, de_money, de_qty, clean_text, clean_code,
    country_code, safe_filename, qn, sub, InvoicePosition, InvoiceRow,
    default_positions, clone_position, normalize_ars_service, service_key,
    _validate_recipient, format_period_de,
)
from .invoice_xml import build_xml
from .invoice_pdf import PDF_ACCENT, draw_wrapped, create_pdf as _create_pdf, embed_xml_into_pdf


def create_pdf(inv: InvoiceRow, out_pdf: Path, issue_date: datetime,
               period_start: datetime | None, period_end: datetime | None,
               due_date: datetime) -> None:
    """Keep the original import path and its configurable accent."""
    _create_pdf(inv, out_pdf, issue_date, period_start, period_end, due_date,
                accent=PDF_ACCENT)


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


# Schlichter PDF-Stil: optionaler Farbakzent (Dokumenttitel, Gesamtbetrag).
# None = komplett monochrom (gewählte Variante). Für einen dezenten Blau-Akzent
# auf (0.043, 0.361, 0.678) setzen. Synchron mit services/pdf_chrome.py halten.


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
