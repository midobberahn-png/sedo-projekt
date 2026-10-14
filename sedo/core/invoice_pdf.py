"""Sichtbare Rechnung und Factur-X-Einbettung im PDF."""
from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from sedo.company_profile import SELLER, seller_document
from .invoice_common import (
    InvoiceRow, clean_text, de_money, de_qty, money, format_period_de,
    _validate_recipient,
)


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


PDF_ACCENT: tuple[float, float, float] | None = None
_DEFAULT_ACCENT = object()


@dataclass(frozen=True)
class _PdfContext:
    """Layout, canvas and page callbacks for one invoice render."""
    c: canvas.Canvas
    w: float
    h: float
    left: float
    right: float
    HEADER_H: float
    FOOTER_H: float
    INK: tuple[float, float, float]
    MUT: tuple[float, float, float]
    FAINT: tuple[float, float, float]
    HAIR: tuple[float, float, float]
    ACCENT: tuple[float, float, float]
    COL_QTY_R: float
    COL_UNIT: float
    COL_DESC: float
    COL_EP_R: float
    fc: Callable[..., None]
    sc: Callable[..., None]
    hairline: Callable[[float], None]
    draw_header: Callable[[], None]
    draw_footer: Callable[[], None]
    draw_watermark: Callable[..., None]
    draw_table_header: Callable[[float], float]

def _pdf_context(out_pdf: Path, accent: tuple[float, float, float] | None) -> _PdfContext:
    """Prepare dimensions and repeatable page furniture."""
    # ── Farbwerte (RGB 0-1) ──────────────────────────────────────────────
    INK = (0.10, 0.11, 0.13)      # Fliesstext
    MUT = (0.42, 0.45, 0.50)      # gedaempfte Beschriftungen
    FAINT = (0.60, 0.63, 0.68)    # Kleingedrucktes
    HAIR = (0.82, 0.84, 0.87)     # Haarlinien
    ACCENT = accent if accent else INK

    c = canvas.Canvas(str(out_pdf), pagesize=A4)
    w, h = A4
    M = 22 * mm
    left = M
    right = w - M
    HEADER_H = 32 * mm            # Kopfzone inkl. Haarlinie
    FOOTER_H = 20 * mm            # Fusszone inkl. Haarlinie

    def fc(*rgb): c.setFillColorRGB(*rgb)
    def sc(*rgb): c.setStrokeColorRGB(*rgb)

    def hairline(y_pos: float) -> None:
        sc(*HAIR); c.setLineWidth(0.5)
        c.line(left, y_pos, right, y_pos)
        c.setLineWidth(1)

    # ── Kopf: Unternehmensdaten aus der lokalen Einrichtung ───────────
    def draw_header():
        top = h - 12 * mm
        fc(*INK); c.setFont("Helvetica-Bold", 9.5)
        c.drawRightString(right, top - 1 * mm, SELLER["name"])
        fc(*MUT); c.setFont("Helvetica", 7)
        c.drawRightString(right, top - 5 * mm,
                          f"{SELLER['street']} · {SELLER['postcode']} {SELLER['city']} · {SELLER['country']}")
        c.drawRightString(right, top - 9 * mm, f"Tel. {SELLER['phone']} · {SELLER['email']}")
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
                     f"Vertretung: {SELLER['ceo']}" + (f" · {SELLER['register']}" if SELLER['register'] else '')]:
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
    return _PdfContext(
        c=c, w=w, h=h, left=left, right=right, HEADER_H=HEADER_H, FOOTER_H=FOOTER_H,
        INK=INK, MUT=MUT, FAINT=FAINT, HAIR=HAIR, ACCENT=ACCENT,
        COL_QTY_R=COL_QTY_R, COL_UNIT=COL_UNIT, COL_DESC=COL_DESC, COL_EP_R=COL_EP_R,
        fc=fc, sc=sc, hairline=hairline, draw_header=draw_header,
        draw_footer=draw_footer, draw_watermark=draw_watermark,
        draw_table_header=draw_table_header,
    )


def _pdf_opening(ctx: _PdfContext, inv: InvoiceRow, issue_date: datetime,
                 period_start: datetime | None, period_end: datetime | None,
                 due_date: datetime, doc_title: str, is_storno: bool, is_draft: bool) -> float:
    """First-page recipient, document details, salutation and introduction."""
    c, h = ctx.c, ctx.h
    left, right, HEADER_H = ctx.left, ctx.right, ctx.HEADER_H
    INK, MUT, FAINT, ACCENT = ctx.INK, ctx.MUT, ctx.FAINT, ctx.ACCENT
    fc, draw_header, draw_watermark = ctx.fc, ctx.draw_header, ctx.draw_watermark
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
    return y


def _pdf_positions(ctx: _PdfContext, inv: InvoiceRow, y: float,
                   doc_title: str, is_draft: bool) -> float:
    """Position table with continuation pages and stable page breaks."""
    c, h = ctx.c, ctx.h
    left, right, FOOTER_H = ctx.left, ctx.right, ctx.FOOTER_H
    INK, MUT, FAINT = ctx.INK, ctx.MUT, ctx.FAINT
    COL_QTY_R, COL_UNIT, COL_DESC, COL_EP_R = ctx.COL_QTY_R, ctx.COL_UNIT, ctx.COL_DESC, ctx.COL_EP_R
    fc, hairline = ctx.fc, ctx.hairline
    draw_footer, draw_watermark, draw_table_header = ctx.draw_footer, ctx.draw_watermark, ctx.draw_table_header
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
    return y


def _pdf_totals(ctx: _PdfContext, inv: InvoiceRow, y: float,
                vat_pct: str, is_draft: bool) -> float:
    """Net, VAT and gross totals, including an optional new page."""
    c, h, right, FOOTER_H = ctx.c, ctx.h, ctx.right, ctx.FOOTER_H
    INK, MUT, ACCENT = ctx.INK, ctx.MUT, ctx.ACCENT
    fc, sc = ctx.fc, ctx.sc
    draw_footer, draw_watermark = ctx.draw_footer, ctx.draw_watermark
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
    return y


def _pdf_payment(ctx: _PdfContext, inv: InvoiceRow, y: float,
                 due_date: datetime, is_storno: bool) -> None:
    """Payment instruction, optional GiroCode and storno-specific wording."""
    c, left = ctx.c, ctx.left
    INK, MUT, FAINT = ctx.INK, ctx.MUT, ctx.FAINT
    fc = ctx.fc
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


@seller_document
def create_pdf(inv: InvoiceRow, out_pdf: Path, issue_date: datetime,
               period_start: datetime | None, period_end: datetime | None,
               due_date: datetime, *, accent=_DEFAULT_ACCENT) -> None:
    """Render the visible invoice from separate page, row and payment steps."""
    _validate_recipient(inv)
    ctx = _pdf_context(out_pdf, PDF_ACCENT if accent is _DEFAULT_ACCENT else accent)
    doc_type = inv.document_type_code or "380"
    doc_title = inv.document_title or "Rechnung"
    is_storno = doc_type == "457"
    is_draft = inv.is_draft
    vat_pct = f"{float(inv.vat_rate):.10g}".replace(".", ",")
    y = _pdf_opening(ctx, inv, issue_date, period_start, period_end, due_date,
                     doc_title, is_storno, is_draft)
    y = _pdf_positions(ctx, inv, y, doc_title, is_draft)
    y = _pdf_totals(ctx, inv, y, vat_pct, is_draft)
    _pdf_payment(ctx, inv, y, due_date, is_storno)
    ctx.draw_footer()
    ctx.c.save()


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
