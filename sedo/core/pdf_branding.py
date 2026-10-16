"""Optional SEDO letterhead mark shared by invoice and auxiliary PDFs."""
from pathlib import Path

from reportlab.lib.units import mm


LOGO = Path(__file__).resolve().parent.parent / 'assets' / 'sedo_logo.png'


def draw_sedo_mark(c, left, top, accent):
    if LOGO.is_file():
        c.drawImage(str(LOGO), left, top - 10 * mm,
                    width=24 * mm, height=12 * mm,
                    preserveAspectRatio=True, anchor='sw', mask='auto')
    else:
        c.setFillColorRGB(*accent)
        c.setFont('Helvetica-Bold', 15)
        c.drawString(left, top - 5 * mm, 'SEDO')
