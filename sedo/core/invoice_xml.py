"""CII/ZUGFeRD-XML-Erzeugung aus einem validierten Rechnungsdatensatz."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from lxml import etree
from sedo.company_profile import SELLER, seller_document
from .invoice_common import (
    NSMAP, InvoiceRow, D, clean_text, clean_code, country_code, money,
    qn, sub, unece_unit_code, format_period_de, _validate_recipient,
)


@seller_document
def build_xml(inv: InvoiceRow, issue_date: datetime, period_start: datetime | None, period_end: datetime | None, due_date: datetime) -> bytes:
    """Erzeugt die CII/ZUGFeRD-XML (EN 16931) als UTF-8-Bytes.

    Setzt u. a. BT-1 (Rechnungsnummer), BT-130 (Menge/Einheit), Leistungsart-
    Notes und den Leistungszeitraum. Eine fehlende Rechnungsnummer (BT-1 ist
    Pflicht) führt zu einer klaren ``ValueError`` statt einer strukturell
    ungültigen Rechnung.
    """
    if not clean_text(inv.invoice_no):
        raise ValueError("Rechnungsnummer fehlt (BT-1 ist Pflichtfeld).")
    if inv.vat_rate is None or D(inv.vat_rate) <= Decimal('0'):
        raise ValueError(
            '0 % oder negative Umsatzsteuer werden nicht unterstützt: Für die '
            'E-Rechnung fehlt eine passende Steuerkategorie; Kategorie S verlangt einen positiven Satz.')
    _validate_recipient(inv)
    root = etree.Element(qn("rsm", "CrossIndustryInvoice"), nsmap=NSMAP)

    tx = _xml_document_header(root, inv, issue_date, period_start, period_end)
    _xml_line_items(tx, inv)
    _xml_parties_and_delivery(tx, inv, issue_date, period_start)
    _xml_settlement(tx, inv, period_start, period_end, due_date)
    return etree.tostring(root, pretty_print=True, xml_declaration=True, encoding="UTF-8")


def _xml_document_header(root, inv: InvoiceRow, issue_date: datetime,
                         period_start: datetime | None, period_end: datetime | None):
    """Dokumentkontext, Belegart, Titel und Kopfnotizen in CII-Reihenfolge."""
    ctx = sub(root, "rsm", "ExchangedDocumentContext")
    bp = sub(ctx, "ram", "BusinessProcessSpecifiedDocumentContextParameter")
    sub(bp, "ram", "ID", "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0")
    gp = sub(ctx, "ram", "GuidelineSpecifiedDocumentContextParameter")
    sub(gp, "ram", "ID", "urn:cen.eu:en16931:2017#conformant#urn:factur-x.eu:1p0:extended")

    doc = sub(root, "rsm", "ExchangedDocument")
    sub(doc, "ram", "ID", inv.invoice_no)
    # ARP-Leitfaden S. 12: ExchangedDocument/Name = Rechnungstext.
    # Titel + Leistungsperiode, damit ARP den Vorgang klar benennen kann.
    # Rechnungstext verwendet den tatsächlichen Leistungsort.
    # Im Rechnungstext wird das OBJEKT gebraucht, an dem die Leistung erbracht
    # wurde – der Sachbearbeiter erkennt daran, worum es geht. Vorher stand dort
    # nur "Rechnung September 2026".
    #
    # Reihenfolge Belegart · Objekt · Zeitraum. Die Belegart bleibt bewusst vorn:
    # Ohne sie wäre einem Storno im Klartext nicht mehr anzusehen, dass es eines
    # ist – das stünde dann nur noch im TypeCode 457.
    # Ohne Objekt (Rechnung ohne Leistungsort) bleibt der Titel wie bisher.
    doc_title = inv.document_title or "Rechnung"
    objekt = clean_text(getattr(inv, "object_name", "") or "")
    if objekt:
        doc_title = f"{doc_title} {objekt}"
    period_label = format_period_de(period_start, period_end)
    if period_label:
        doc_title = f"{doc_title} · {period_label}"
    sub(doc, "ram", "Name", doc_title)
    sub(doc, "ram", "TypeCode", inv.document_type_code or "380")
    issue = sub(doc, "ram", "IssueDateTime")
    sub(issue, "udt", "DateTimeString", issue_date.strftime("%Y%m%d"), format="102")

    # Aareon/GWG-Anforderung: Leistungsart-ID in ZUGFeRD XML.
    # Wenn alle Positionen dieselbe Leistungsart haben, wird sie auf Kopfebene übertragen:
    # CrossIndustryInvoice/ExchangedDocument/IncludedNote/Content/Leistungsart:...
    # Wenn unterschiedliche Leistungsarten enthalten sind, werden sie positionsweise übertragen:
    # IncludedSupplyChainTradeLineItem/AssociatedDocumentLineDocument/IncludedNote/Content
    if inv.sap_mapping and inv.leistungsart_contents_for_positions and not inv.has_multiple_leistungsarten:
        note = sub(doc, "ram", "IncludedNote")
        sub(note, "ram", "Content", inv.leistungsart_contents_for_positions[0])

    note2 = sub(doc, "ram", "IncludedNote")
    legal_lines = [SELLER['name'], SELLER['street'], f"{SELLER['postcode']} {SELLER['city']}",
                   f"Vertretung: {SELLER['ceo']}"]
    if SELLER['register']:
        legal_lines.append(f"Register: {SELLER['register']}")
    sub(note2, "ram", "Content", '\n'.join(legal_lines))
    sub(note2, "ram", "SubjectCode", "REG")

    tx = sub(root, "rsm", "SupplyChainTradeTransaction")
    return tx


def _xml_line_items(tx, inv: InvoiceRow) -> None:
    """Positionsweise Leistungsarten, Mengen, Steuern und Beträge."""
    for idx, pos in enumerate(inv.effective_positions, start=1):
        line = sub(tx, "ram", "IncludedSupplyChainTradeLineItem")
        line_doc = sub(line, "ram", "AssociatedDocumentLineDocument")
        sub(line_doc, "ram", "LineID", str(idx))
        if inv.sap_mapping and inv.has_multiple_leistungsarten:
            line_note = sub(line_doc, "ram", "IncludedNote")
            sub(line_note, "ram", "Content", inv.leistungsart_content_for_position(pos))
        prod = sub(line, "ram", "SpecifiedTradeProduct")
        if inv.include_seller_assigned_id and pos.article_no:
            sub(prod, "ram", "SellerAssignedID", pos.article_no)
        sub(prod, "ram", "Name", pos.description)
        # BT-154: Leistungsbeschreibung, falls hinterlegt.
        _long = clean_text(getattr(pos, "long_text", "") or "")
        if _long:
            sub(prod, "ram", "Description", _long)
        # Leistungsart wird nach Kundenleitfaden als IncludedNote übertragen,
        # nicht als Produktbeschreibung.
        agr = sub(line, "ram", "SpecifiedLineTradeAgreement")
        price = sub(agr, "ram", "NetPriceProductTradePrice")
        sub(price, "ram", "ChargeAmount", money(pos.unit_price))
        delivery_line = sub(line, "ram", "SpecifiedLineTradeDelivery")
        sub(delivery_line, "ram", "BilledQuantity", money(pos.quantity), unitCode=unece_unit_code(pos.unit))
        settlement_line = sub(line, "ram", "SpecifiedLineTradeSettlement")
        tax = sub(settlement_line, "ram", "ApplicableTradeTax")
        sub(tax, "ram", "TypeCode", "VAT")
        sub(tax, "ram", "CategoryCode", "S")
        sub(tax, "ram", "RateApplicablePercent", money(inv.vat_rate))
        summ = sub(settlement_line, "ram", "SpecifiedTradeSettlementLineMonetarySummation")
        sub(summ, "ram", "LineTotalAmount", money(pos.line_total))


def _xml_parties_and_delivery(tx, inv: InvoiceRow, issue_date: datetime,
                              period_start: datetime | None) -> None:
    """Verkäufer, Empfänger und tatsächlichen Leistungsort schreiben."""
    header_agr = sub(tx, "ram", "ApplicableHeaderTradeAgreement")
    seller = sub(header_agr, "ram", "SellerTradeParty")
    # The business identifier is entered locally, never fabricated from tax
    # or bank data. An explicit recipient-specific Aareon ID takes priority.
    seller_identifier = getattr(inv, "seller_id", "") or SELLER['seller_id']
    if seller_identifier:
        sub(seller, "ram", "ID", seller_identifier)
    sub(seller, "ram", "Name", SELLER["name"])
    contact = sub(seller, "ram", "DefinedTradeContact")
    sub(contact, "ram", "PersonName", SELLER["ceo"])
    tel = sub(contact, "ram", "TelephoneUniversalCommunication")
    sub(tel, "ram", "CompleteNumber", SELLER["phone"])
    email = sub(contact, "ram", "EmailURIUniversalCommunication")
    sub(email, "ram", "URIID", SELLER["email"])
    seller_addr = sub(seller, "ram", "PostalTradeAddress")
    sub(seller_addr, "ram", "PostcodeCode", SELLER["postcode"])
    sub(seller_addr, "ram", "LineOne", SELLER["street"])
    sub(seller_addr, "ram", "CityName", SELLER["city"])
    sub(seller_addr, "ram", "CountryID", SELLER["country"])
    uri = sub(seller, "ram", "URIUniversalCommunication")
    sub(uri, "ram", "URIID", SELLER["email"], schemeID="EM")
    if SELLER["tax_no"]:
        reg = sub(seller, "ram", "SpecifiedTaxRegistration")
        sub(reg, "ram", "ID", SELLER["tax_no"], schemeID="FC")
    if SELLER["vat_id"]:
        reg2 = sub(seller, "ram", "SpecifiedTaxRegistration")
        sub(reg2, "ram", "ID", SELLER["vat_id"], schemeID="VA")

    buyer = sub(header_agr, "ram", "BuyerTradeParty")
    if inv.buyer_id:
        sub(buyer, "ram", "ID", inv.buyer_id)
    sub(buyer, "ram", "Name", inv.buyer_name)
    buyer_addr = sub(buyer, "ram", "PostalTradeAddress")
    sub(buyer_addr, "ram", "PostcodeCode", inv.buyer_postcode)
    sub(buyer_addr, "ram", "LineOne", inv.buyer_street)
    sub(buyer_addr, "ram", "CityName", inv.buyer_city)
    sub(buyer_addr, "ram", "CountryID", country_code(inv.buyer_country))

    # Leistungsort / Objektanschrift. Für den GWG/Excel-Massenlauf zusätzlich mit WE-ID.
    header_delivery = sub(tx, "ram", "ApplicableHeaderTradeDelivery")
    if inv.object_name or inv.object_street or inv.sap_mapping:
        shipto = sub(header_delivery, "ram", "ShipToTradeParty")
        # Alte SAP-Testlogik: WE als technische ShipTo-ID.
        # Aareon/GWG-Logik laut Screenshot/Support:
        #   ShipToTradeParty/ID   = ID der Abrechnungseinheit aus dem Mapping
        #                           (Spalte "Abrechnungseinheit" / ae_internal).
        #   ShipToTradeParty/Name = Objekt-/Abrechnungseinheitsname, z. B. "Ahornweg 1".
        #   PostalTradeAddress    = tatsächliche Objektadresse bzw. Objektname.
        billing_unit_name = clean_text(inv.object_name or inv.object_street)
        billing_unit_id = clean_code(getattr(inv, "ae", "")) or clean_code(getattr(inv, "we", ""))
        if getattr(inv, "ars_mapping", False):
            # GWG/Aareon: ID der Abrechnungseinheit kommt aus Spalte A
            # "Rechnungssteller Abrechnungseinheit". Deshalb ist inv.ae im Weblauf
            # bewusst auf diesen Wert gesetzt; im Excel-Massenlauf verwenden wir
            # notfalls object_name/billing_unit_name.
            sub(shipto, "ram", "ID", billing_unit_id or billing_unit_name)
        elif inv.sap_mapping and inv.we:
            sub(shipto, "ram", "ID", inv.we)
        if inv.object_name:
            sub(shipto, "ram", "Name", inv.object_name)
        elif billing_unit_name:
            sub(shipto, "ram", "Name", billing_unit_name)
        ship_addr = sub(shipto, "ram", "PostalTradeAddress")
        line_one = inv.object_street or inv.object_name or inv.buyer_street
        # Wichtig fuer Factur-X/ZUGFeRD EXTENDED: PostalTradeAddress hat eine feste XSD-Reihenfolge.
        # PostcodeCode darf nicht nach LineOne kommen, sonst meldet der Validator z.B.
        # "one of LineTwo, LineThree, CityName, CountryID".
        sub(ship_addr, "ram", "PostcodeCode", inv.object_postcode or inv.buyer_postcode)
        sub(ship_addr, "ram", "LineOne", line_one)
        sub(ship_addr, "ram", "CityName", inv.object_city or inv.buyer_city)
        sub(ship_addr, "ram", "CountryID", country_code(inv.object_country or inv.buyer_country))
    # Leistungserbringungsdatum ist laut ARP-Leitfaden Pflichtfeld (1..1).
    # Wenn kein Leistungszeitraum angegeben ist, gilt das Rechnungsdatum als Lieferdatum.
    actual = sub(header_delivery, "ram", "ActualDeliverySupplyChainEvent")
    occ = sub(actual, "ram", "OccurrenceDateTime")
    delivery_date = period_start if period_start is not None else issue_date
    sub(occ, "udt", "DateTimeString", delivery_date.strftime("%Y%m%d"), format="102")


def _xml_settlement(tx, inv: InvoiceRow, period_start: datetime | None,
                    period_end: datetime | None, due_date: datetime) -> None:
    """Zahlungsangaben und Summen; Referenzbeleg muss laut XSD zuletzt stehen."""
    settlement = sub(tx, "ram", "ApplicableHeaderTradeSettlement")
    sub(settlement, "ram", "InvoiceCurrencyCode", "EUR")
    # Die Referenz auf die Ursprungsrechnung (BT-25) wird erst weiter unten
    # angehängt – hinter der Summation. Begründung dort.
    ref_invoice_no = clean_text(inv.reference_invoice_no)
    means = sub(settlement, "ram", "SpecifiedTradeSettlementPaymentMeans")
    sub(means, "ram", "TypeCode", "58")
    sub(means, "ram", "Information", "Zahlung per SEPA Überweisung.")
    acc = sub(means, "ram", "PayeePartyCreditorFinancialAccount")
    sub(acc, "ram", "IBANID", SELLER["iban"])
    sub(acc, "ram", "AccountName", SELLER["name"])
    bank = sub(means, "ram", "PayeeSpecifiedCreditorFinancialInstitution")
    sub(bank, "ram", "BICID", SELLER["bic"])

    htax = sub(settlement, "ram", "ApplicableTradeTax")
    sub(htax, "ram", "CalculatedAmount", money(inv.vat_amount))
    sub(htax, "ram", "TypeCode", "VAT")
    sub(htax, "ram", "BasisAmount", money(inv.net_total))
    sub(htax, "ram", "CategoryCode", "S")
    sub(htax, "ram", "RateApplicablePercent", money(inv.vat_rate))

    # Abrechnungs-/Leistungszeitraum nur ausgeben, wenn beide Werte befüllt sind.
    if period_start is not None and period_end is not None:
        period = sub(settlement, "ram", "BillingSpecifiedPeriod")
        ps = sub(period, "ram", "StartDateTime")
        sub(ps, "udt", "DateTimeString", period_start.strftime("%Y%m%d"), format="102")
        pe = sub(period, "ram", "EndDateTime")
        sub(pe, "udt", "DateTimeString", period_end.strftime("%Y%m%d"), format="102")

    terms = sub(settlement, "ram", "SpecifiedTradePaymentTerms")
    # Aareon/ARP übernimmt das Fälligkeitsdatum sicher, wenn in der Description
    # der Wert "Faelligkeitsdatum" enthalten ist.
    sub(terms, "ram", "Description", "Faelligkeitsdatum")
    due = sub(terms, "ram", "DueDateDateTime")
    sub(due, "udt", "DateTimeString", due_date.strftime("%Y%m%d"), format="102")

    sm = sub(settlement, "ram", "SpecifiedTradeSettlementHeaderMonetarySummation")
    sub(sm, "ram", "LineTotalAmount", money(inv.net_total))
    sub(sm, "ram", "ChargeTotalAmount", "0.00")
    sub(sm, "ram", "AllowanceTotalAmount", "0.00")
    sub(sm, "ram", "TaxBasisTotalAmount", money(inv.net_total))
    sub(sm, "ram", "TaxTotalAmount", money(inv.vat_amount), currencyID="EUR")
    sub(sm, "ram", "RoundingAmount", "0.00")
    sub(sm, "ram", "GrandTotalAmount", money(inv.gross))
    sub(sm, "ram", "DuePayableAmount", money(inv.gross))

    # Storno-/Korrekturrechnung: Verweis auf die Ursprungsrechnung (BT-25).
    #
    # Die Reihenfolge ist hier keine Geschmacksfrage. Das CII-D16B-Schema legt
    # für ram:ApplicableHeaderTradeSettlement eine feste xs:sequence fest, in
    # der ram:InvoiceReferencedDocument an Position 31 steht – NACH
    # ram:SpecifiedTradeSettlementHeaderMonetarySummation (Position 29). Stand
    # das Element weiter oben (direkt hinter InvoiceCurrencyCode), brach die
    # Validierung beim nächsten Geschwisterelement ab:
    #   Element '{...ram}SpecifiedTradeSettlementPaymentMeans': This element is
    #   not expected. Expected is one of ( ...ProFormaInvoiceReferencedDocument,
    #   ...LetterOfCreditReferencedDocument, ... )
    # Damit war JEDE Storno- (457) und Korrekturrechnung (384) als E-Rechnung
    # schemaungültig – genau die Belege, mit denen Aareon/GWG Buchungen
    # aufheben (Review-Fund F-04, gegen das Original-XSD nachgestellt).
    #
    # Dieselbe Falle steckt bei PostalTradeAddress (siehe Kommentar oben); dort
    # war sie schon bedacht. BT-26 (FormattedIssueDateTime der Ursprungs-
    # rechnung) ist optional und bewusst nicht gesetzt.
    if ref_invoice_no:
        refdoc = sub(settlement, "ram", "InvoiceReferencedDocument")
        sub(refdoc, "ram", "IssuerAssignedID", ref_invoice_no)
