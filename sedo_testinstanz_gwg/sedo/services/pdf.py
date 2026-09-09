"""PDF-Erzeugung für Angebote und Kassenbuch im schlichten SEDO-Briefbogen-Stil.

Nutzt den gemeinsamen minimalen Briefbogen aus ``pdf_chrome`` (Haarlinien,
kleines Logo, viel Weißraum) – identisch zum Stil der Rechnungs-PDF in
``core.rechnungslauf_core``.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from django.conf import settings
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas

from ..core.rechnungslauf_core import SELLER, de_qty
from .calc import recalc_offer
from .formatting import euro
from . import pdf_chrome as chrome
from .pdf_chrome import FAINT, FOOTER_H, HAIR, HEADER_H, INK, MARGIN, MUT


def _draw_wrapped(c, text, x, y, max_width, font='Helvetica', size=9, leading=4.8):
    """Zeichnet umbrochenen Text und gibt die neue y-Position zurück."""
    for line in simpleSplit(str(text), font, size, max_width):
        c.drawString(x, y, line)
        y -= leading * mm
    return y


def _hairline(c, left, right, y):
    c.setStrokeColorRGB(*HAIR)
    c.setLineWidth(0.5)
    c.line(left, y, right, y)
    c.setLineWidth(1)


def generate_offer_pdf(offer):
    """Erzeugt die Angebots-PDF im schlichten Briefbogen-Stil und verknüpft sie."""
    offer = recalc_offer(offer)
    offer.save()
    out_dir = Path(settings.MEDIA_ROOT) / 'generated' / offer.number
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / f'{offer.number}.pdf'

    c = canvas.Canvas(str(pdf_path), pagesize=A4)
    w, h = A4
    left, right = MARGIN, w - MARGIN
    vat_pct = f'{float(offer.vat_rate):.10g}'.replace('.', ',')

    # Spalten der Positionstabelle
    col_pos = left + 1 * mm
    col_desc = left + 12 * mm
    col_qty_r = right - 58 * mm
    col_ep_r = right - 28 * mm
    desc_w = col_qty_r - col_desc - 14 * mm

    def table_header(y):
        c.setFillColorRGB(*MUT)
        c.setFont('Helvetica-Bold', 7)
        c.drawString(col_pos, y, 'POS.')
        c.drawString(col_desc, y, 'BEZEICHNUNG')
        c.drawRightString(col_qty_r, y, 'MENGE')
        c.drawRightString(col_ep_r, y, 'EINZELPREIS')
        c.drawRightString(right, y, 'GESAMT')
        _hairline(c, left, right, y - 2.2 * mm)
        return y - 7.5 * mm

    # ── Seite 1 ──────────────────────────────────────────────────────────
    chrome.draw_header(c)
    sender_y = h - HEADER_H - 4 * mm
    chrome.draw_sender_line(c, sender_y)

    # Empfänger links
    ry = sender_y - 8 * mm
    c.setFillColorRGB(*INK)
    c.setFont('Helvetica-Bold', 10)
    c.drawString(left, ry, str(offer.customer.name))
    c.setFont('Helvetica', 10)
    for line in [offer.customer.name2, offer.customer.street,
                 f'{offer.customer.postcode} {offer.customer.city}'.strip()]:
        if (line or '').strip():
            ry -= 5 * mm
            c.drawString(left, ry, line)

    # Eckdaten rechts (ohne Box)
    info_rows = [
        ('Angebots-Nr.', offer.number),
        ('Datum', offer.issue_date.strftime('%d.%m.%Y')),
        ('Gültig bis', offer.valid_until.strftime('%d.%m.%Y') if offer.valid_until else '—'),
    ]
    iy = chrome.draw_info_block(c, right, sender_y - 2 * mm, info_rows)

    # Titel
    y = min(ry, iy) - 12 * mm
    chrome.draw_title(c, y, 'Angebot')
    y -= 9 * mm

    # Leistungsort als schlichte Zeile
    if offer.obj:
        parts = [offer.obj.name]
        if offer.obj.street and offer.obj.street != offer.obj.name:
            parts.append(offer.obj.street)
        city = f'{offer.obj.postcode} {offer.obj.city}'.strip()
        if city:
            parts.append(city)
        c.setFillColorRGB(*MUT)
        c.setFont('Helvetica-Bold', 8.5)
        c.drawString(left, y, 'Leistungsort')
        c.setFillColorRGB(*INK)
        c.setFont('Helvetica', 9)
        c.drawString(left + 26 * mm, y, ' · '.join(parts))
        y -= 8 * mm

    # Anrede + Einleitung (persönlich, wenn Anrede + Ansprechpartner bekannt)
    salutation = 'Sehr geehrte Damen und Herren,'
    contact = (offer.customer.contact_person or '').strip()
    if contact and offer.customer.salutation == 'Herr':
        salutation = f'Sehr geehrter Herr {contact.split()[-1]},'
    elif contact and offer.customer.salutation == 'Frau':
        salutation = f'Sehr geehrte Frau {contact.split()[-1]},'
    c.setFillColorRGB(*INK)
    c.setFont('Helvetica', 9.5)
    c.drawString(left, y, salutation)
    y -= 5.5 * mm
    y = _draw_wrapped(c, 'vielen Dank für Ihre Anfrage und das damit verbundene Vertrauen. '
                         'Gern unterbreiten wir Ihnen für die nachfolgend aufgeführten '
                         'Leistungen das folgende Angebot:',
                      left, y, right - left, 'Helvetica', 9.5, 5.2)
    y -= 6 * mm

    # Positionstabelle
    y = table_header(y)
    for item in offer.items.all():
        if y < FOOTER_H + 34 * mm:
            chrome.draw_footer(c)
            c.showPage()
            c.setFillColorRGB(*FAINT)
            c.setFont('Helvetica', 7)
            c.drawString(left, h - 12 * mm, f'Angebot {offer.number} · Fortsetzung')
            _hairline(c, left, right, h - 14 * mm)
            y = h - 22 * mm
            y = table_header(y)
        top = y
        c.setFillColorRGB(*INK)
        c.setFont('Helvetica', 9)
        c.drawString(col_pos, y, str(item.position))
        desc_bot = _draw_wrapped(c, item.description, col_desc, y, desc_w, 'Helvetica', 9, 4.6)
        long_text = (item.long_text or '').strip()
        if long_text:
            c.setFillColorRGB(*MUT)
            desc_bot = _draw_wrapped(c, long_text, col_desc, desc_bot - 2.6 * mm, desc_w, 'Helvetica', 8, 3.9)
            c.setFillColorRGB(*INK)
        c.setFont('Helvetica', 9)
        c.drawRightString(col_qty_r, top, f'{de_qty(item.quantity)} {item.unit}')
        c.setFillColorRGB(*MUT)
        c.drawRightString(col_ep_r, top, euro(item.unit_price))
        c.setFillColorRGB(*INK)
        c.setFont('Helvetica-Bold', 9)
        c.drawRightString(right, top, euro(item.quantity * item.unit_price))
        c.setFont('Helvetica', 9)
        row_bot = min(desc_bot + 1 * mm, top - 4.2 * mm)
        _hairline(c, left, right, row_bot + 0.8 * mm)
        y = row_bot - 3 * mm

    # Summen rechtsbündig (ohne Box)
    y -= 4 * mm
    if y < FOOTER_H + 32 * mm:
        chrome.draw_footer(c)
        c.showPage()
        y = h - 26 * mm
    sum_label_x = right - 60 * mm
    c.setFillColorRGB(*MUT)
    c.setFont('Helvetica', 9)
    c.drawString(sum_label_x, y, 'Nettobetrag')
    c.setFillColorRGB(*INK)
    c.drawRightString(right, y, euro(offer.net_total))
    y -= 5.4 * mm
    c.setFillColorRGB(*MUT)
    c.drawString(sum_label_x, y, f'zzgl. MwSt {vat_pct} %')
    c.setFillColorRGB(*INK)
    c.drawRightString(right, y, euro(offer.vat_amount))
    y -= 3.4 * mm
    c.setStrokeColorRGB(*INK)
    c.setLineWidth(0.7)
    c.line(sum_label_x, y, right, y)
    c.setLineWidth(1)
    y -= 5.8 * mm
    c.setFillColorRGB(*chrome.accent_color())
    c.setFont('Helvetica-Bold', 11.5)
    c.drawString(sum_label_x, y, 'Gesamtbetrag')
    c.drawRightString(right, y, euro(offer.gross_total))

    # Schlussteil: Gültigkeit, Gesprächsangebot, Grußformel
    y -= 12 * mm
    closing_lines = []
    if offer.valid_until:
        closing_lines.append('Dieses Angebot ist gültig bis zum '
                             f'{offer.valid_until.strftime("%d.%m.%Y")}.')
    closing_lines.append('Haben Sie Fragen oder wünschen Sie Anpassungen? Sprechen Sie uns '
                         'einfach an – wir passen das Angebot gern kurzfristig an.')
    closing_lines.append('Wir würden uns freuen, den Auftrag für Sie ausführen zu dürfen, '
                         'und sichern Ihnen eine sorgfältige und termingerechte Ausführung zu.')
    needed = len(closing_lines) * 6 * mm + 22 * mm
    if y - needed < FOOTER_H:
        chrome.draw_footer(c)
        c.showPage()
        y = h - 26 * mm
    c.setFillColorRGB(*INK)
    c.setFont('Helvetica', 9.5)
    for line in closing_lines:
        y = _draw_wrapped(c, line, left, y, right - left, 'Helvetica', 9.5, 5.2)
        y -= 2.6 * mm
    y -= 6 * mm
    c.drawString(left, y, 'Mit freundlichen Grüßen')
    y -= 5.5 * mm
    c.setFont('Helvetica-Bold', 9.5)
    c.drawString(left, y, SELLER['name'])

    chrome.draw_footer(c)
    c.save()

    offer.pdf_file.name = str(pdf_path.relative_to(settings.MEDIA_ROOT))
    offer.save(update_fields=['pdf_file', 'net_total', 'vat_amount', 'gross_total', 'updated_at'])
    return offer


def generate_cashbook_pdf(month: str) -> Path:
    """Erzeugt die Kassenbuch-PDF für einen Monat (YYYY-MM) im schlichten Stil."""
    from ..models import CashbookEntry

    entries = CashbookEntry.objects.filter(month=month).order_by('booking_no')
    out_dir = Path(settings.MEDIA_ROOT) / 'cashbook'
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / f'Kassenbuch_{month}.pdf'

    c = canvas.Canvas(str(pdf_path), pagesize=A4)
    w, h = A4
    left, right = MARGIN, w - MARGIN
    income_total = sum((e.amount for e in entries if e.kind == 'income'), Decimal('0.00'))
    expense_total = sum((e.amount for e in entries if e.kind in ['expense', 'correction']), Decimal('0.00'))

    cols = [left, left + 12 * mm, left + 32 * mm, left + 74 * mm]
    col_in_r = right - 30 * mm
    col_out_r = right

    def table_header(y):
        c.setFillColorRGB(*MUT)
        c.setFont('Helvetica-Bold', 7)
        c.drawString(cols[0], y, 'NR.')
        c.drawString(cols[1], y, 'DATUM')
        c.drawString(cols[2], y, 'NAME')
        c.drawString(cols[3], y, 'TEXT')
        c.drawRightString(col_in_r, y, 'EINNAHME')
        c.drawRightString(col_out_r, y, 'AUSGABE')
        _hairline(c, left, right, y - 2.2 * mm)
        return y - 7 * mm

    chrome.draw_header(c)
    y = h - HEADER_H - 8 * mm
    chrome.draw_title(c, y, f'Kassenbuch {month}')
    y -= 8 * mm

    # Zusammenfassung als schlichte Zeile
    c.setFillColorRGB(*MUT)
    c.setFont('Helvetica', 9)
    c.drawString(left, y, 'Einnahmen')
    c.drawString(left + 42 * mm, y, 'Ausgaben')
    c.drawString(left + 84 * mm, y, 'Saldo')
    y -= 4.6 * mm
    c.setFillColorRGB(*INK)
    c.setFont('Helvetica-Bold', 9.5)
    c.drawString(left, y, euro(income_total))
    c.drawString(left + 42 * mm, y, euro(expense_total))
    c.drawString(left + 84 * mm, y, euro(income_total - expense_total))
    y -= 8 * mm

    y = table_header(y)
    for e in entries:
        if y < FOOTER_H + 14 * mm:
            chrome.draw_footer(c)
            c.showPage()
            c.setFillColorRGB(*FAINT)
            c.setFont('Helvetica', 7)
            c.drawString(left, h - 12 * mm, f'Kassenbuch {month} · Fortsetzung')
            _hairline(c, left, right, h - 14 * mm)
            y = h - 22 * mm
            y = table_header(y)
        c.setFillColorRGB(*INK)
        c.setFont('Helvetica', 8)
        c.drawString(cols[0], y, str(e.booking_no))
        c.drawString(cols[1], y, e.booking_date.strftime('%d.%m.%Y'))
        c.drawString(cols[2], y, (e.name or '')[:24])
        c.drawString(cols[3], y, (e.text or '')[:48])
        if e.kind == 'income':
            c.drawRightString(col_in_r, y, euro(e.amount))
        else:
            c.drawRightString(col_out_r, y, euro(e.amount))
        _hairline(c, left, right, y - 2 * mm)
        y -= 6 * mm

    chrome.draw_footer(c)
    c.save()
    return pdf_path
