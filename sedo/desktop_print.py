"""Narrow macOS PDF print bridge; other platforms use the browser PDF dialog."""
import base64
import binascii
import re
import sys
import threading
from urllib.parse import urlsplit

MAX_BYTES = 32 * 1024 * 1024


class IncomingPrintAPI:
    def __init__(self, origin):
        self._origin = origin
        self._window = None
        self._printing = threading.Lock()

    def print_incoming_pdf(self, encoded):
        if sys.platform != 'darwin':
            return {'native': False}
        try:
            current = urlsplit(self._window.get_current_url())
            if (f'{current.scheme}://{current.netloc}' != self._origin or
                    not re.fullmatch(r'/eingangsrechnungen/(?:beleg/[0-9]+/(?:ansehen/)?)?', current.path)):
                raise ValueError('Not an incoming-invoice page')
            if not isinstance(encoded, str) or len(encoded) > ((MAX_BYTES + 2) // 3) * 4:
                raise ValueError('Invalid print payload')
            data = base64.b64decode(encoded, validate=True)
            if len(data) > MAX_BYTES or not data.startswith(b'%PDF-'):
                raise ValueError('Invalid PDF')
        except (AttributeError, ValueError, binascii.Error):
            return {'native': True, 'ok': False, 'error': 'Der Druckbeleg ist ungültig. Bitte erneut öffnen.'}
        if not self._printing.acquire(blocking=False):
            return {'native': True, 'ok': False, 'error': 'Es ist bereits ein Druckdialog geöffnet.'}
        try:
            # The JS bridge is an independent entry point: validate even when
            # the normal print HTTP route has already checked this document.
            from .document_probe import validate_pdf
            validate_pdf(data, max_pages=500)
            return _mac_print(data)
        except Exception:
            return {'native': True, 'ok': False, 'error': 'Der Druckdialog konnte nicht geöffnet werden.'}
        finally:
            self._printing.release()


def _mac_print(data):
    # macOS's user-controlled print UI uses the system PDF renderer. The worker
    # validates structure and budgets first; this UI is not the parser sandbox
    # and still relies on a supported, patched operating system.
    from PyObjCTools import AppHelper
    complete = threading.Event()
    result = {'native': True, 'ok': False, 'error': 'Der Druckdialog konnte nicht geöffnet werden.'}

    def show():
        try:
            import AppKit
            from Foundation import NSData
            from Quartz import PDFDocument, kPDFPrintPageScaleToFit
            document = PDFDocument.alloc().initWithData_(NSData.dataWithBytes_length_(data, len(data)))
            if document is None or document.isLocked() or not 0 < document.pageCount() <= 500:
                raise ValueError('Unreadable PDF')
            operation = document.printOperationForPrintInfo_scalingMode_autoRotate_(
                AppKit.NSPrintInfo.sharedPrintInfo().copy(), kPDFPrintPageScaleToFit, True)
            if operation is None:
                raise ValueError('No print operation')
            operation.setShowsPrintPanel_(True)
            operation.setShowsProgressPanel_(True)
            operation.runOperation()  # User chooses printer/pages or cancels; never silent printing.
            result.clear()
            result.update(native=True, ok=True)
        except Exception:
            pass
        finally:
            complete.set()

    AppHelper.callAfter(show)
    complete.wait()
    return result
