"""Printable copies; viewing/printing never approves or marks a document printed."""
import io
import textwrap

from .incoming_invoices import IncomingError, verified_original
from ..document_probe import DocumentProbeError, image_pdf, validate_pdf

MAX_PRINT_BYTES = 32 * 1024 * 1024
MAX_PRINT_PAGES = 500


def printable_pdf(invoice):
    data = verified_original(invoice)
    if len(data) > MAX_PRINT_BYTES:
        raise IncomingError('Der Beleg ist zum direkten Drucken zu groß. Bitte das Original herunterladen.')
    try:
        if invoice.media_type == 'application/pdf' and data.startswith(b'%PDF-'):
            validate_pdf(data, max_pages=MAX_PRINT_PAGES)
            return data  # Preserve all original pages, form appearances and annotations.
        if invoice.media_type in ('image/png', 'image/jpeg', 'image/tiff'):
            return image_pdf(data)
        if invoice.media_type == 'application/xml':
            return _xml_pdf(data, invoice.filename)
    except IncomingError:
        raise
    except DocumentProbeError as exc:
        raise IncomingError('Der Beleg konnte nicht innerhalb der Sicherheitsgrenzen zum Drucken geöffnet werden. Bitte das Original prüfen.') from exc
    except Exception as exc:
        raise IncomingError('Der Beleg konnte nicht zum Drucken geöffnet werden. Bitte das Original prüfen.') from exc
    raise IncomingError('Dieses Dateiformat kann nicht direkt gedruckt werden.')


def _xml_pdf(data, filename):
    # Raw text only: no XML entities, remote stylesheets or embedded markup executed.
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen.canvas import Canvas
    if len(data) > 1024 * 1024:
        raise IncomingError('Die XML-Datei ist zum direkten Drucken zu groß.')
    encoding = 'utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
    text = data.decode(encoding, errors='replace')
    lines = [part for line in text.splitlines() for part in (textwrap.wrap(
        line.expandtabs(4), width=110, replace_whitespace=False, drop_whitespace=False) or [''])]
    if len(lines) > MAX_PRINT_PAGES * 65:
        raise IncomingError('Die XML-Textansicht überschreitet 500 Druckseiten.')
    output = io.BytesIO()
    canvas = Canvas(output, pagesize=A4)
    for start in range(0, max(len(lines), 1), 65):
        canvas.setFont('Helvetica', 10)
        canvas.drawString(28, 814, 'XML-Original · Textansicht')
        canvas.setFont('Helvetica', 8)
        canvas.drawString(28, 800, filename[:110])
        canvas.setFont('Courier', 8)
        for index, line in enumerate(lines[start:start + 65]):
            canvas.drawString(28, 780 - index * 11, line)
        canvas.showPage()
    canvas.save()
    return output.getvalue()
