"""Conservative, local attachment classification; no model or network calls.

A mail subject never labels every attachment. Document headings and structured
invoice types take precedence. Ambiguous material stays in the mailbox.
"""
import io
import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePath


@dataclass(frozen=True)
class Detection:
    kind: str  # invoice / other / unknown
    reason: str


def normalized(value):
    return unicodedata.normalize('NFKC', value).casefold().replace('_', ' ')


INVOICE = r'(?:rechnung(?:skopie|sduplikat)?|abschlagsrechnung|teilrechnung|schlussrechnung|stornorechnung|invoice|tax invoice)'
OTHER = r'(?:angebot|kostenvoranschlag|lieferschein|lieferavis|kommissionierschein|auftragsbestätigung|auftragsbestaetigung|bestellbestätigung|bestellbestaetigung|bestellung|mahnung|zahlungserinnerung|gutschrift|proforma(?:[ -]rechnung|[ -]invoice)?|quotation|quote|delivery note|credit note|termin(?:bestätigung|bestaetigung|ankündigung|ankuendigung|erinnerung)?|zählerablesung|zaehlerablesung|ablesetermin|ablesung)'
INVOICE_WORD = re.compile(r'(?<!\w)' + INVOICE + r'(?![a-zäöüß])')
OTHER_WORD = re.compile(r'(?<!\w)' + OTHER + r'(?![a-zäöüß])')
TITLE = re.compile(r'^\s*(?:betreff\s*:\s*)?(' + OTHER + '|' + INVOICE + r')(?=$|[\s:№#./\d-])')
NUMBER = re.compile(r'\b(?:rechnungs(?:nummer|[ -]?nr\.?)|invoice\s*(?:number|no\.?|#))\s*[:#.-]?\s*\S+')
# Every numeric candidate and separator run is bounded, and candidates cannot
# restart inside an overlong number. Cost is linear in the bounded text length.
_VALUE = r'(?<![\d.,])\d[\d .]{0,40}[,.]\d{2}(?![\d.,])'
AMOUNT = re.compile(
    _VALUE + r'\s{0,12}(?:eur\b|€|usd\b|gbp\b|chf\b)'
    + r'|(?:eur|€|usd|gbp|chf)\s{0,12}' + _VALUE
    + r'|\b(?:gesamtbetrag|rechnungsbetrag|zahlbetrag|zu zahlen|bruttobetrag|summe brutto|gesamt|total|amount due)'
      r'\s{0,12}[:a-z€ ]{0,32}' + _VALUE)


def classify_text(text):
    lines = normalized(text[:24000])[:24000].splitlines()
    title = None
    for line in lines[:100]:
        if NUMBER.match(line.strip()):
            title = 'rechnung'
            break
        match = TITLE.match(line)
        if match:
            title = match.group(1)
            break
    if title and OTHER_WORD.fullmatch(title):
        return Detection('other', 'Dokumenttyp: ' + title)
    content = '\n'.join(lines)
    if (title or NUMBER.search(content)) and AMOUNT.search(content):
        return Detection('invoice', 'Rechnungskennzeichen und Betrag im Dokument erkannt')
    return Detection('unknown', 'Kein eindeutiger Rechnungsinhalt erkannt')


# Invoice types from Peppol BIS Billing's UNCL1001 invoice subset. Requests for
# payment, debit notes and notifications do not establish an invoice here.
INVOICE_CODES = {'82', '326', '331', '380', '384', '386', '388', '389', '393',
                 '395', '575', '623', '780', '870', '875', '876', '877'}

def classify_xml(data):
    from lxml import etree
    try:
        parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
        root = etree.fromstring(data, parser)
        if root.getroottree().docinfo.doctype:
            return Detection('unknown', 'XML mit Dokumenttyp-Deklaration wird nicht automatisch übernommen')
        tag = etree.QName(root)
        if tag.namespace == 'urn:oasis:names:specification:ubl:schema:xsd:Invoice-2' and tag.localname == 'Invoice':
            ns = {'cbc':'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2',
                  'cac':'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2'}
            code = root.findtext('cbc:InvoiceTypeCode', namespaces=ns)
            number = root.findtext('cbc:ID', namespaces=ns)
            total = root.findtext('cac:LegalMonetaryTotal/cbc:PayableAmount', namespaces=ns)
        elif tag.namespace == 'urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100' and tag.localname == 'CrossIndustryInvoice':
            ns = {'rsm':tag.namespace, 'ram':'urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100'}
            code = root.findtext('rsm:ExchangedDocument/ram:TypeCode', namespaces=ns)
            number = root.findtext('rsm:ExchangedDocument/ram:ID', namespaces=ns)
            total = root.findtext('rsm:SupplyChainTradeTransaction/ram:ApplicableHeaderTradeSettlement/ram:SpecifiedTradeSettlementHeaderMonetarySummation/ram:GrandTotalAmount', namespaces=ns)
        else:
            if tag.localname in ('Order', 'Quotation', 'DespatchAdvice', 'CreditNote') and (tag.namespace or '').startswith('urn:oasis:names:specification:ubl:schema:xsd:'):
                return Detection('other', 'XML-Dokumenttyp: ' + tag.localname)
            return Detection('unknown', 'Kein unterstütztes Rechnungs-XML')
        if code and code.strip() not in INVOICE_CODES:
            return Detection('other' if code.strip() in ('325', '381') else 'unknown', 'XML-Belegart ist keine unterstützte Rechnung')
        if code and number and number.strip() and total and re.fullmatch(r'-?\d+(?:\.\d+)?', total.strip()):
            return Detection('invoice', 'Strukturiertes Rechnungs-XML erkannt')
    except (etree.XMLSyntaxError, ValueError, TypeError):
        pass
    return Detection('unknown', 'Rechnungs-XML nicht eindeutig lesbar')


def classify_document(data, filename, subject=''):
    filename = normalized(PurePath(filename).name)
    named_other = OTHER_WORD.search(filename)
    ext = PurePath(filename).suffix
    if ext == '.xml':
        return classify_xml(data)
    text = ''
    if ext == '.pdf':
        try:
            from pypdf import PdfReader
            if not data.startswith(b'%PDF-'):
                return Detection('unknown', 'Keine lesbare PDF-Datei')
            reader = PdfReader(io.BytesIO(data), strict=False)
            if reader.is_encrypted or len(reader.pages) > 200:
                return Detection('unknown', 'PDF nicht automatisch lesbar')
            text = '\n'.join((page.extract_text() or '')[:12000] for page in reader.pages[:3])[:24000]
        except Exception:
            return Detection('unknown', 'PDF nicht automatisch lesbar')
        if text.strip():
            detected = classify_text(text)
            # Invoice filenames can mention an offer reference; actual content wins.
            if detected.kind != 'unknown' or not named_other:
                return detected
    elif ext in ('.png', '.jpg', '.jpeg', '.tif', '.tiff'):
        try:
            from PIL import Image
            with Image.open(io.BytesIO(data)) as picture:
                if picture.width * picture.height > 25_000_000:
                    return Detection('unknown', 'Bild nicht automatisch lesbar')
                picture.verify()
        except Exception:
            return Detection('unknown', 'Bild nicht automatisch lesbar')
    else:
        return Detection('unknown', 'Kein unterstütztes Rechnungsformat')
    if named_other:
        return Detection('other', 'Dateiname kennzeichnet ' + named_other.group())
    # Scans have no extractable text: require two independent explicit labels.
    if text.strip():
        return Detection('unknown', 'Kein eindeutiger Rechnungsinhalt erkannt')
    subject = normalized(subject)
    if INVOICE_WORD.search(filename) and INVOICE_WORD.search(subject) and not OTHER_WORD.search(subject):
        return Detection('invoice', 'Scan in Dateiname und Betreff ausdrücklich als Rechnung bezeichnet')
    return Detection('unknown', 'Scan nicht eindeutig als Rechnung bezeichnet')


MAX_DOCUMENT_BYTES = 20 * 1024 * 1024


def _resource_limits():
    """Set limits before importing PDF/image libraries. Fail closed on failure."""
    import os
    if os.name == 'posix':
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (3, 4))
        return
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]
        class Basic(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong), ('flags', wintypes.DWORD),
                        ('min_working_set', ctypes.c_size_t), ('max_working_set', ctypes.c_size_t), ('active', wintypes.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]
        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', IO), ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                        ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t)]
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        global _JOB_HANDLE
        _JOB_HANDLE = kernel.CreateJobObjectW(None, None)
        info = Extended()
        info.basic.flags = 0x100 | 0x8 | 0x2  # process memory, process count, CPU time
        info.basic.process_time = 3 * 10_000_000
        info.basic.active = 1
        info.process_memory = 768 * 1024 * 1024
        if (not _JOB_HANDLE or not kernel.SetInformationJobObject(_JOB_HANDLE, 9, ctypes.byref(info), ctypes.sizeof(info))
                or not kernel.AssignProcessToJobObject(_JOB_HANDLE, kernel.GetCurrentProcess())):
            raise OSError('Cannot enforce document process limits')
        return
    raise OSError('Unsupported resource isolation')


def worker_main(directory):
    """File-backed protocol: Windows windowed builds have no stdin/stdout."""
    import json
    import os
    from pathlib import Path
    import struct
    import sys
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        return 2
    result = Detection('unknown', 'Dokument konnte innerhalb der Sicherheitsgrenzen nicht geprüft werden')
    try:
        _resource_limits()
        # Third-party diagnostics must not depend on a console being attached.
        for name in ('stdout', 'stderr'):
            if getattr(sys, name) is None:
                setattr(sys, name, open(os.devnull, 'w'))
        request_path = directory / 'request.bin'
        if request_path.is_symlink():
            raise ValueError()
        with request_path.open('rb') as handle:
            prefix = handle.read(4)
            if len(prefix) != 4:
                raise ValueError()
            header_length = struct.unpack('!I', prefix)[0]
            if not 0 < header_length <= 8192:
                raise ValueError()
            header = json.loads(handle.read(header_length))
            data = handle.read(MAX_DOCUMENT_BYTES + 1)
        if len(data) > MAX_DOCUMENT_BYTES:
            raise ValueError()
        filename, subject = header['filename'], header['subject']
        if not isinstance(filename, str) or not isinstance(subject, str) or len(filename) > 240 or len(subject) > 2000:
            raise ValueError()
        result = classify_document(data, filename, subject)
    except Exception:
        pass
    try:
        output = json.dumps({'kind': result.kind, 'reason': result.reason[:500]}, ensure_ascii=True)
        with (directory / 'result.json').open('x', encoding='utf-8') as handle:
            os.chmod(directory / 'result.json', 0o600)
            handle.write(output)
    except OSError:
        return 2
    return 0


if __name__ == '__main__':
    import sys
    raise SystemExit(worker_main(sys.argv[1]) if len(sys.argv) == 2 else 2)
