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
AMOUNT = re.compile(r'\d[\d .,]*[,.]\d{2}\s*(?:eur\b|€|usd\b|gbp\b|chf\b)|(?:eur|€|usd|gbp|chf)\s*\d[\d .,]*[,.]\d{2}(?!\d)|\b(?:gesamtbetrag|rechnungsbetrag|zahlbetrag|zu zahlen|bruttobetrag|summe brutto|gesamt|total|amount due)\s*[:a-z€ ]*\d[\d .,]*[,.]\d{2}(?!\d)')


def classify_text(text):
    lines = normalized(text).splitlines()
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
