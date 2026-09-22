"""Deterministic documents with real text for invoice-recognition tests."""
import io
from reportlab.pdfgen.canvas import Canvas


def text_pdf(text='Rechnung Nr. ER-2026-123\nRechnungsdatum: 22.09.2026\nRechnungsbetrag: 119,00 EUR'):
    out = io.BytesIO()
    canvas = Canvas(out, invariant=1)
    line = canvas.beginText(45, 790)
    for value in text.splitlines():
        line.textLine(value)
    canvas.drawText(line)
    canvas.save()
    return out.getvalue()
