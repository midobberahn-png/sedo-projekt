"""Schlichter Briefbogen für die SEDO-Zusatz-PDFs (Angebot, Mahnung, Kassenbuch).

Reduzierter Stil: weißer Grund, feine Haarlinien, kleines Logo, viel Weißraum –
keine farbigen Flächen. Der einzige Farbakzent (SEDO-Blau) ist zentral über
``ACCENT`` steuerbar; auf ``None`` gesetzt sind die Dokumente komplett monochrom.

Die Rechnungs-PDF in ``core.rechnungslauf_core`` nutzt denselben Stil (dort
bewusst eigenständig gehalten, da der Kern Django-frei bleibt – Farbwerte
synchron halten!).
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm

from ..core.rechnungslauf_core import SELLER

# ── Farbwerte (RGB 0–1) – bewusst zurückhaltend ─────────────────────────────
INK = (0.10, 0.11, 0.13)      # Fließtext
MUT = (0.42, 0.45, 0.50)      # gedämpfte Beschriftungen
FAINT = (0.60, 0.63, 0.68)    # Fußzeile, Kleingedrucktes
HAIR = (0.82, 0.84, 0.87)     # Haarlinien
# Optionaler Farbakzent (Dokumenttitel, Gesamtbetrag). None = komplett
# monochrom (gewählte Variante); für einen dezenten Blau-Akzent auf
# (0.043, 0.361, 0.678) setzen. Synchron mit core.PDF_ACCENT halten.
ACCENT: tuple | None = None

MARGIN = 22 * mm
HEADER_H = 32 * mm            # Kopfzone inkl. Haarlinie
FOOTER_H = 20 * mm            # Fußzone inkl. Haarlinie

_LOGO = Path(__file__).resolve().parent.parent / 'assets' / 'sedo_logo.png'


def accent_color() -> tuple:
    """Akzentfarbe oder Tinte, falls monochrom gewünscht."""
    return ACCENT if ACCENT else INK


def draw_header(c) -> None:
    """Kleines Logo links, Absenderblock rechts, Haarlinie darunter."""
    w, h = A4
    left, right = MARGIN, w - MARGIN
    top = h - 12 * mm

    if _LOGO.exists():
        logo_w, logo_h = 24 * mm, 12 * mm
        c.drawImage(str(_LOGO), left, top - logo_h + 2 * mm,
                    width=logo_w, height=logo_h, preserveAspectRatio=True,
                    anchor='sw', mask='auto')
    else:
        c.setFillColorRGB(*accent_color())
        c.setFont('Helvetica-Bold', 15)
        c.drawString(left, top - 5 * mm, 'SEDO')

    c.setFillColorRGB(*INK)
    c.setFont('Helvetica-Bold', 9.5)
    c.drawRightString(right, top - 1 * mm, SELLER['name'])
    c.setFillColorRGB(*MUT)
    c.setFont('Helvetica-Oblique', 7)
    c.drawRightString(right, top - 5 * mm, 'Sauberes Handwerk · Rund um Ihr Objekt')
    c.setFont('Helvetica', 7)
    c.drawRightString(right, top - 9 * mm,
                      f"{SELLER['street']} · {SELLER['postcode']} {SELLER['city']} · "
                      f"Tel. {SELLER['phone']} · {SELLER['email']}")

    c.setStrokeColorRGB(*HAIR)
    c.setLineWidth(0.5)
    c.line(left, h - HEADER_H + 4 * mm, right, h - HEADER_H + 4 * mm)
    c.setLineWidth(1)


def draw_footer(c) -> None:
    """Haarlinie und drei schmale Spalten Kleingedrucktes."""
    w, _h = A4
    left, right = MARGIN, w - MARGIN
    c.setStrokeColorRGB(*HAIR)
    c.setLineWidth(0.5)
    c.line(left, FOOTER_H - 4 * mm, right, FOOTER_H - 4 * mm)
    c.setLineWidth(1)

    c.setFillColorRGB(*FAINT)
    c.setFont('Helvetica', 6.6)
    col2 = left + (right - left) * 0.40
    col3 = left + (right - left) * 0.72
    yy = FOOTER_H - 8 * mm
    for line in [SELLER['name'],
                 f"{SELLER['street']} · {SELLER['postcode']} {SELLER['city']}",
                 f"GF: {SELLER['ceo']} · {SELLER['register']}"]:
        c.drawString(left, yy, line)
        yy -= 3.1 * mm
    yy = FOOTER_H - 8 * mm
    for line in [SELLER.get('bank_name') or 'Bankverbindung',
                 f"IBAN {SELLER['iban']}",
                 f"BIC {SELLER['bic']}"]:
        c.drawString(col2, yy, line)
        yy -= 3.1 * mm
    yy = FOOTER_H - 8 * mm
    for line in [f"Tel. {SELLER['phone']}",
                 SELLER['email'],
                 f"St.-Nr. {SELLER['tax_no']} · USt-IdNr. {SELLER['vat_id']}"]:
        c.drawString(col3, yy, line)
        yy -= 3.1 * mm


def draw_sender_line(c, y: float) -> None:
    """Kleine Absenderzeile (klassisch über dem Empfängerfeld)."""
    c.setFillColorRGB(*FAINT)
    c.setFont('Helvetica', 6.6)
    c.drawString(MARGIN, y, f"{SELLER['name']} · {SELLER['street']} · {SELLER['postcode']} {SELLER['city']}")


def draw_title(c, y: float, text: str) -> None:
    """Schlichter Dokumenttitel im Akzentton."""
    c.setFillColorRGB(*accent_color())
    c.setFont('Helvetica-Bold', 15)
    c.drawString(MARGIN, y, text)


def draw_info_block(c, right_x: float, top_y: float, rows) -> float:
    """Beschriftung/Wert-Zeilen rechtsbündig – ohne Box, nur Typografie.

    ``rows`` ist eine Liste aus (Label, Wert). Gibt die y-Position unter der
    letzten Zeile zurück.
    """
    label_x = right_x - 62 * mm
    value_x = right_x - 34 * mm
    yy = top_y
    for lbl, val in rows:
        c.setFillColorRGB(*MUT)
        c.setFont('Helvetica', 8)
        c.drawString(label_x, yy, lbl)
        c.setFillColorRGB(*INK)
        c.setFont('Helvetica-Bold', 8)
        c.drawString(value_x, yy, str(val))
        yy -= 4.6 * mm
    return yy
