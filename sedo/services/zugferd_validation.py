"""Verständliche, lokale Prüfung der von SEDO erzeugten ZUGFeRD-Belege.

Die Prüfung umfasst den XML-Aufbau und die EN-16931-Pflichtinhalte, rechnet
Positionen, Steuer und Summen nach und vergleicht die eingebettete XML mit dem
versiegelten XML-Original. Sie arbeitet offline auf den archivierten Bytes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO
from functools import lru_cache
from pathlib import Path

import elementpath
from lxml import etree
from pypdf import PdfReader

from .invoice_archive import lese_archivdatei

NS = {
    'rsm': 'urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100',
    'ram': 'urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100',
    'udt': 'urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100',
}


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    profile: str = ''
    official_rules_checked: int = 0

    @property
    def ok(self):
        return not self.errors


def _text(root, xpath):
    values = root.xpath(xpath, namespaces=NS)
    if not values:
        return ''
    value = values[0]
    return (value if isinstance(value, str) else value.text or '').strip()


def _decimal(root, xpath, label, result):
    value = _text(root, xpath)
    try:
        parsed = Decimal(value)
        if not parsed.is_finite() or abs(parsed) > Decimal('1e12'):
            raise ValueError('Zahl außerhalb des zulässigen Bereichs')
        return parsed
    except (InvalidOperation, ValueError):
        result.errors.append(f'{label} fehlt oder ist keine gültige Zahl.')
        return None


def _embedded_xml(pdf_bytes: bytes) -> bytes | None:
    reader = PdfReader(BytesIO(pdf_bytes), strict=False)
    attachments = getattr(reader, 'attachments', {}) or {}
    for name in ('factur-x.xml', 'zugferd-invoice.xml'):
        values = attachments.get(name)
        if values:
            value = values[0] if isinstance(values, list) else values
            return bytes(value)
    # Kompatibilität mit älteren pypdf-Versionen.
    root = reader.trailer.get('/Root', {})
    names = root.get('/Names', {}).get('/EmbeddedFiles', {}).get('/Names', [])
    for index in range(0, len(names), 2):
        if str(names[index]).lower() not in ('factur-x.xml', 'zugferd-invoice.xml'):
            continue
        spec = names[index + 1].get_object()
        stream = (spec.get('/EF') or {}).get('/F')
        if stream:
            return stream.get_object().get_data()
    return None


@lru_cache(maxsize=1)
def _official_rules():
    """Kompiliert die offiziellen EU/DIGITAL-Regeln einmal je Prozess."""
    source = (Path(__file__).resolve().parents[1] / 'validation' / 'en16931' /
              '1.3.16' / 'EN16931-CII-validation-preprocessed.sch')
    schema = etree.parse(str(source))
    namespaces = {
        node.get('prefix'): node.get('uri')
        for node in schema.xpath('//*[local-name()="ns"]')
    }
    rules = []
    for rule in schema.xpath('//*[local-name()="rule"]'):
        # Schematron-Kontexte sind Match-Muster: ein relativer Kontext wie
        # ram:CountryID gilt auf JEDER Ebene, nicht nur direkt am Dokumentknoten.
        # Der fest versionierte Regelsatz enthält auch eine Union zweier Muster.
        expression = ' | '.join(
            branch.strip() if branch.strip().startswith('/') else '//' + branch.strip()
            for branch in rule.get('context').split('|'))
        context = elementpath.Selector(
            expression, namespaces=namespaces, parser=elementpath.XPath2Parser)
        assertions = []
        for assertion in rule.xpath('./*[local-name()="assert"]'):
            assertions.append((
                assertion.get('id') or 'EN16931', assertion.get('flag') or 'fatal',
                ' '.join(''.join(assertion.itertext()).split()),
                elementpath.Selector(
                    'boolean(' + assertion.get('test') + ')', namespaces=namespaces,
                    parser=elementpath.XPath2Parser),
            ))
        rules.append((context, assertions))
    return rules


def _run_official_en16931(document, result):
    """Wendet alle 806 Regeln des offiziellen CII-Artefakts v1.3.16 an."""
    try:
        for context, assertions in _official_rules():
            for node in context.select(document):
                for rule_id, flag, message, assertion in assertions:
                    result.official_rules_checked += 1
                    if not bool(assertion.select(document, item=node)):
                        text = message.split(']-', 1)[-1].strip() if ']-' in message else message
                        target = result.warnings if flag in ('warning', 'info') else result.errors
                        target.append(f'EN 16931 {rule_id}: {text}')
    except Exception as exc:
        result.errors.append(f'Die offizielle EN-16931-Prüfung konnte nicht vollständig ausgeführt werden: {exc}')


@lru_cache(maxsize=2)
def _profile_schema(profile):
    base = Path(__file__).resolve().parents[1] / 'validation/facturx/1.09.2' / profile
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    return etree.XMLSchema(etree.parse(str(base / f'Factur-X_{profile.upper()}.xsd'), parser))


def _check_structure(root, result):
    result.profile = _text(root, '//ram:GuidelineSpecifiedDocumentContextParameter/ram:ID')
    profile = 'extended' if result.profile.endswith(':extended') else 'en16931'
    try:
        _profile_schema(profile).assertValid(root)
    except (etree.DocumentInvalid, etree.XMLSchemaParseError, OSError) as exc:
        result.errors.append(f'XML-Struktur entspricht nicht dem Profil {profile.upper()}: {exc}')
        return False
    result.checks.append(f'XML-Struktur nach Factur-X {profile.upper()} geprüft (Schemasatz 1.09.2).')
    return True


def validate_invoice_zugferd(invoice) -> ValidationResult:
    result = ValidationResult()
    try:
        xml_attachment = lese_archivdatei(invoice, 'xml_file')
        pdf_attachment = lese_archivdatei(invoice, 'zugferd_file')
    except Exception as exc:
        result.errors.append(str(exc))
        return result

    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=False)
    try:
        root = etree.fromstring(xml_attachment.data, parser)
    except (etree.XMLSyntaxError, ValueError) as exc:
        result.errors.append(f'XML ist nicht wohlgeformt: {exc}')
        return result
    if root.tag != f"{{{NS['rsm']}}}CrossIndustryInvoice":
        result.errors.append('XML-Wurzelelement oder Namespace entspricht nicht ZUGFeRD/CII.')
        return result
    if root.getroottree().docinfo.doctype:
        result.errors.append('DOCTYPE-Deklarationen sind in Rechnungs-XML nicht erlaubt.')
        return result
    if not _check_structure(root, result):
        return result
    result.checks.append('XML ist wohlgeformt und verwendet den CII-Namespace.')
    _run_official_en16931(etree.ElementTree(root), result)

    required = [
        ('//rsm:ExchangedDocument/ram:ID', 'Rechnungsnummer'),
        ('//rsm:ExchangedDocument/ram:TypeCode', 'Rechnungsart'),
        ('//rsm:ExchangedDocument/ram:IssueDateTime/udt:DateTimeString', 'Rechnungsdatum'),
        ('//ram:SellerTradeParty/ram:Name', 'Verkäufername'),
        ('//ram:BuyerTradeParty/ram:Name', 'Käufername'),
        ('//ram:SellerTradeParty/ram:PostalTradeAddress/ram:CountryID', 'Verkäuferland'),
        ('//ram:BuyerTradeParty/ram:PostalTradeAddress/ram:CountryID', 'Käuferland'),
        ('//ram:InvoiceCurrencyCode', 'Währung'),
        ('//ram:SpecifiedTradePaymentTerms/ram:DueDateDateTime/udt:DateTimeString', 'Fälligkeitsdatum'),
        ('//ram:PayeePartyCreditorFinancialAccount/ram:IBANID', 'IBAN'),
    ]
    for xpath, label in required:
        if not _text(root, xpath):
            result.errors.append(f'{label} fehlt.')
    number = _text(root, '//rsm:ExchangedDocument/ram:ID')
    if number and number != invoice.number:
        result.errors.append(f'Rechnungsnummer in XML ({number}) passt nicht zum Beleg ({invoice.number}).')
    if _text(root, '//ram:InvoiceCurrencyCode') not in ('', 'EUR'):
        result.errors.append('SEDO-Rechnungen müssen in EUR ausgestellt sein.')
    result.profile = _text(root, '//ram:GuidelineSpecifiedDocumentContextParameter/ram:ID')
    if result.profile not in ('urn:cen.eu:en16931:2017',
                              'urn:cen.eu:en16931:2017#compliant#urn:factur-x.eu:1p0:en16931',
                              'urn:cen.eu:en16931:2017#conformant#urn:factur-x.eu:1p0:extended'):
        result.errors.append('Das ausgewiesene Factur-X/ZUGFeRD-EN-16931-Profil fehlt oder ist unbekannt.')

    lines = root.xpath('//ram:IncludedSupplyChainTradeLineItem', namespaces=NS)
    if not lines:
        result.errors.append('Die Rechnung enthält keine Position.')
    line_sum = Decimal('0.00')
    for index, line in enumerate(lines, 1):
        name = _text(line, './/ram:SpecifiedTradeProduct/ram:Name')
        quantity = _decimal(line, './/ram:BilledQuantity', f'Menge in Position {index}', result)
        price = _decimal(line, './/ram:NetPriceProductTradePrice/ram:ChargeAmount', f'Preis in Position {index}', result)
        total = _decimal(line, './/ram:SpecifiedTradeSettlementLineMonetarySummation/ram:LineTotalAmount', f'Summe in Position {index}', result)
        if not name:
            result.errors.append(f'Bezeichnung in Position {index} fehlt.')
        if None not in (quantity, price, total):
            expected = (quantity * price).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            if expected != total.quantize(Decimal('0.01')):
                result.errors.append(f'Position {index}: Menge × Preis ergibt {expected}, ausgewiesen sind {total}.')
            line_sum += total

    net = _decimal(root, '//ram:SpecifiedTradeSettlementHeaderMonetarySummation/ram:TaxBasisTotalAmount', 'Nettosumme', result)
    tax = _decimal(root, '//ram:SpecifiedTradeSettlementHeaderMonetarySummation/ram:TaxTotalAmount', 'Steuerbetrag', result)
    gross = _decimal(root, '//ram:SpecifiedTradeSettlementHeaderMonetarySummation/ram:GrandTotalAmount', 'Bruttosumme', result)
    due = _decimal(root, '//ram:SpecifiedTradeSettlementHeaderMonetarySummation/ram:DuePayableAmount', 'Zahlbetrag', result)
    if net is not None and line_sum.quantize(Decimal('0.01')) != net.quantize(Decimal('0.01')):
        result.errors.append(f'Positionssummen ({line_sum}) stimmen nicht mit Netto ({net}) überein.')
    if None not in (net, tax, gross) and (net + tax).quantize(Decimal('0.01')) != gross.quantize(Decimal('0.01')):
        result.errors.append('Netto plus Steuer stimmt nicht mit der Bruttosumme überein.')
    if None not in (gross, due) and gross.quantize(Decimal('0.01')) != due.quantize(Decimal('0.01')):
        result.errors.append('Bruttosumme und Zahlbetrag stimmen nicht überein.')
    if gross is not None and gross.quantize(Decimal('0.01')) != Decimal(invoice.gross_total).quantize(Decimal('0.01')):
        result.errors.append('Bruttosumme in XML passt nicht zur gespeicherten Rechnung.')

    try:
        embedded = _embedded_xml(pdf_attachment.data)
    except Exception as exc:
        result.errors.append(f'ZUGFeRD-PDF kann nicht gelesen werden: {exc}')
    else:
        if embedded is None:
            result.errors.append('Im PDF ist keine factur-x.xml eingebettet.')
        elif embedded != xml_attachment.data:
            result.errors.append('Eingebettete XML und archivierte XML sind nicht bytegleich.')
        else:
            result.checks.append('PDF enthält die bytegleiche factur-x.xml.')

    if not result.errors:
        result.checks.extend([
            f'Offizieller EU/DIGITAL-Regelsatz EN 16931 CII 1.3.16 bestanden ({result.official_rules_checked} anwendbare Prüfungen).',
            'Pflichtfelder, Profil, Parteien, Zahlungsdaten und Währung sind vorhanden.',
            'Positionen, Netto, Steuer, Brutto und Zahlbetrag sind rechnerisch konsistent.',
            'Rechnungsnummer und Bruttobetrag stimmen mit dem gespeicherten Beleg überein.',
        ])
    result.warnings.append('Die PDF/A-3-Konformität des PDF-Containers wird hier nicht geprüft. '
                           'Dieses Ergebnis ist kein vollständiger ZUGFeRD-Konformitätsnachweis.')
    if result.profile.endswith(':extended'):
        result.warnings.append('Das Dokument verwendet EXTENDED. Die zusätzlichen EXTENDED-Profilregeln '
                               'sind vom EN-16931-Basisregelsatz nicht vollständig abgedeckt.')
    return result
