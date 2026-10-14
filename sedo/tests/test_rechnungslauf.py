"""
SEDO – Automatisierte Tests für Kern-Logik

Ausführen: python manage.py test sedo.tests
"""
from decimal import Decimal
from datetime import datetime
from types import SimpleNamespace
from .company_fixtures import SyntheticSellerTestCase as TestCase

from django.test import TestCase as DjangoTestCase
from lxml import etree

from sedo.core.rechnungslauf_core import (
    InvoicePosition,
    InvoiceRow,
    build_xml,
    de_money,
    money,
    D,
    clean_code,
)
from sedo.forms import InvoiceForm, OfferForm, RecurringInvoiceTemplateForm
from sedo.view_support import _preview_from_forms

NSMAP = {
    "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
    "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100",
}

ISSUE = datetime(2026, 6, 1)
DUE   = datetime(2026, 6, 15)
PS    = datetime(2026, 5, 1)
PE    = datetime(2026, 5, 31)


def _row(**kwargs) -> InvoiceRow:
    """Minimale InvoiceRow für Tests.

    ``invoice_type`` (380/384/457) wird als Convenience-Parameter akzeptiert
    und danach als ``document_type_code``/``document_title`` gesetzt –
    analog zu services.invoice_to_core_row().
    """
    invoice_type = kwargs.pop("invoice_type", "380")
    defaults = dict(
        invoice_no="TEST-001",
        object_name="Teststraße 1",
        we="1001", ae="Teststraße 1", nksl="4200",
        service="Reinigung",
        net=Decimal("100.00"),
        vat_rate=Decimal("19.00"),
        positions=[InvoicePosition("Reinigung", Decimal("1"), "Stk.", Decimal("100.00"), "", "Reinigung", "Reinigung")],
        object_street="Teststraße 1", object_postcode="39118", object_city="Magdeburg", object_country="DE",
        sap_mapping=True, buyer_id="42", buyer_name="Synthetischer Testkunde eG", buyer_name2="",
        buyer_street="Kunden-Teststraße 2", buyer_postcode="10115", buyer_city="Berlin", buyer_country="DE",
        ars_mapping=True,
    )
    defaults.update(kwargs)
    row = InvoiceRow(**defaults)
    _titles = {"380": "Rechnung", "384": "Korrekturrechnung", "457": "Stornorechnung"}
    row.document_type_code = invoice_type
    row.document_title = _titles.get(invoice_type, "Rechnung")
    return row


def _xml(row, issue=ISSUE, ps=None, pe=None, due=DUE):
    return etree.fromstring(build_xml(row, issue, ps, pe, due))


def xp(root, path):
    r = root.xpath(path, namespaces=NSMAP)
    return r[0].text.strip() if r and hasattr(r[0], "text") else (r[0] if r else None)


# ---------------------------------------------------------------------------
# Betragsberechnungen
# ---------------------------------------------------------------------------

class TestBetragsberechnungen(TestCase):
    def test_net_total_single_position(self):
        row = _row()
        self.assertEqual(row.net_total, Decimal("100.00"))

    def test_vat_amount(self):
        row = _row()
        self.assertEqual(row.vat_amount, Decimal("19.00"))

    def test_null_prozent_wird_vor_ungueltiger_erechnung_gestoppt(self):
        with self.assertRaisesRegex(ValueError, 'Steuerkategorie'):
            build_xml(_row(vat_rate=Decimal('0')), ISSUE, None, None, DUE)

    def test_gross(self):
        row = _row()
        self.assertEqual(row.gross, Decimal("119.00"))


    def test_mehrere_positionen(self):
        row = _row(positions=[
            InvoicePosition("Pos 1", Decimal("2"), "Stk.", Decimal("10.00")),
            InvoicePosition("Pos 2", Decimal("3"), "Stk.", Decimal("5.00")),
        ])
        self.assertEqual(row.net_total, Decimal("35.00"))

    def test_rundung_half_up(self):
        """0.005 muss zu 0.01 gerundet werden (ROUND_HALF_UP, nicht ROUND_HALF_EVEN)."""
        row = _row(positions=[
            InvoicePosition("Test", Decimal("1"), "Stk.", Decimal("0.005")),
        ])
        self.assertEqual(row.net_total, Decimal("0.01"))


class TestVatRateForms(DjangoTestCase):
    def test_formulare_lehnen_nicht_unterstuetzte_steuersaetze_ab(self):
        for form_class in (InvoiceForm, OfferForm, RecurringInvoiceTemplateForm):
            with self.subTest(form=form_class.__name__):
                form = form_class(data={'vat_rate': '0'})
                self.assertFalse(form.is_valid())
                self.assertIn('vat_rate', form.errors)

    def test_vorschau_ersetzt_null_prozent_nicht_still_durch_neunzehn(self):
        form = SimpleNamespace(cleaned_data={'vat_rate': Decimal('0')})
        formset = SimpleNamespace(forms=[])
        preview = _preview_from_forms(form, formset)
        self.assertEqual(preview['vat_rate'], Decimal('0'))


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

class TestHilfsfunktionen(TestCase):
    def test_de_money_ohne_tausender(self):
        self.assertEqual(de_money(Decimal("99.00")), "99,00 €")

    def test_de_money_mit_tausender(self):
        self.assertEqual(de_money(Decimal("1234.56")), "1.234,56 €")

    def test_de_money_grosser_betrag(self):
        self.assertEqual(de_money(Decimal("1234567.89")), "1.234.567,89 €")

    def test_money_format(self):
        self.assertEqual(money(Decimal("1234.56")), "1234.56")

    def test_D_deutsch(self):
        self.assertEqual(D("1.032,54"), Decimal("1032.54"))

    def test_D_englisch(self):
        self.assertEqual(D("1032.54"), Decimal("1032.54"))

    def test_D_euro_zeichen(self):
        self.assertEqual(D("99,00 €"), Decimal("99.00"))

    def test_clean_code_float(self):
        self.assertEqual(clean_code("4200.0"), "4200")

    def test_clean_code_unverändert(self):
        self.assertEqual(clean_code("Reinigung"), "Reinigung")


# ---------------------------------------------------------------------------
# XML-Struktur (build_xml)
# ---------------------------------------------------------------------------

class TestBuildXmlStruktur(TestCase):
    def setUp(self):
        self.root = _xml(_row())

    def test_root_element(self):
        self.assertIn("CrossIndustryInvoice", self.root.tag)

    def test_rechnungsnummer(self):
        self.assertEqual(xp(self.root, "//rsm:ExchangedDocument/ram:ID"), "TEST-001")

    def test_typecode_rechnung(self):
        self.assertEqual(xp(self.root, "//rsm:ExchangedDocument/ram:TypeCode"), "380")

    def test_ausgabedatum(self):
        self.assertEqual(xp(self.root, "//rsm:ExchangedDocument/ram:IssueDateTime/udt:DateTimeString"), "20260601")

    def test_document_name_gesetzt(self):
        """ExchangedDocument/Name muss immer gesetzt sein (ARP-Leitfaden S. 12)."""
        name = xp(self.root, "//rsm:ExchangedDocument/ram:Name")
        self.assertIsNotNone(name)
        self.assertNotEqual(name, "")

    def test_document_name_mit_periode(self):
        root = _xml(_row(), ps=PS, pe=PE)
        name = xp(root, "//rsm:ExchangedDocument/ram:Name")
        self.assertIn("Mai 2026", name)

    def test_seller_name(self):
        self.assertEqual("Synthetische Testfirma GmbH", xp(self.root, "//ram:SellerTradeParty/ram:Name"))

    def test_buyer_id(self):
        self.assertEqual(xp(self.root, "//ram:BuyerTradeParty/ram:ID"), "42")

    def test_buyer_name(self):
        self.assertEqual("Synthetischer Testkunde eG", xp(self.root, "//ram:BuyerTradeParty/ram:Name"))


class TestBuildXmlTypeCodes(TestCase):
    def test_typecode_storno(self):
        row = _row(invoice_type="457")
        root = _xml(row)
        self.assertEqual(xp(root, "//rsm:ExchangedDocument/ram:TypeCode"), "457")

    def test_typecode_korrektur(self):
        row = _row(invoice_type="384")
        root = _xml(row)
        self.assertEqual(xp(root, "//rsm:ExchangedDocument/ram:TypeCode"), "384")

    # Der Rechnungstext trägt seit 09.09.2026 zusätzlich das Objekt – Anforderung
    # aus einem Integrationstest: der Sachbearbeiter ordnet den Beleg
    # daran zu. Die Belegart steht weiterhin vorn, sonst wäre einem Storno im
    # Klartext nicht mehr anzusehen, dass es eines ist. _row() setzt
    # object_name="Teststraße 1", einen Leistungszeitraum gibt es hier nicht.
    def test_document_title_storno(self):
        row = _row(invoice_type="457")
        root = _xml(row)
        self.assertEqual(xp(root, "//rsm:ExchangedDocument/ram:Name"), "Stornorechnung Teststraße 1")

    def test_document_title_korrektur(self):
        row = _row(invoice_type="384")
        root = _xml(row)
        self.assertEqual(xp(root, "//rsm:ExchangedDocument/ram:Name"), "Korrekturrechnung Teststraße 1")


class TestBuildXmlActualDelivery(TestCase):
    def test_actual_delivery_mit_periode(self):
        """Mit Leistungszeitraum: period_start als Lieferdatum."""
        root = _xml(_row(), ps=PS, pe=PE)
        dt = xp(root, "//ram:ActualDeliverySupplyChainEvent/ram:OccurrenceDateTime/udt:DateTimeString")
        self.assertEqual(dt, "20260501")

    def test_actual_delivery_ohne_periode_fallback_issuedate(self):
        """Ohne Leistungszeitraum: Pflichtfeld muss trotzdem gesetzt sein (= issue_date)."""
        root = _xml(_row(), ps=None, pe=None)
        dt = xp(root, "//ram:ActualDeliverySupplyChainEvent/ram:OccurrenceDateTime/udt:DateTimeString")
        self.assertIsNotNone(dt)
        self.assertEqual(dt, "20260601")  # ISSUE = 2026-06-01


class TestBuildXmlShipTo(TestCase):
    def test_shiptotradeparty_id(self):
        root = _xml(_row())
        self.assertEqual(xp(root, "//ram:ShipToTradeParty/ram:ID"), "Teststraße 1")

    def test_shiptotradeparty_name(self):
        root = _xml(_row())
        self.assertIn("Teststraße", xp(root, "//ram:ShipToTradeParty/ram:Name"))


class TestBuildXmlLeistungsart(TestCase):
    def test_einzelne_leistungsart_auf_kopfebene(self):
        """Eine Leistungsart → IncludedNote auf Kopfebene (ExchangedDocument)."""
        root = _xml(_row())
        notes = root.xpath(
            "//rsm:ExchangedDocument/ram:IncludedNote/ram:Content/text()", namespaces=NSMAP
        )
        leistungsart_notes = [n for n in notes if "LeistungsartID" in n]
        self.assertEqual(len(leistungsart_notes), 1)

    def test_leistungsart_format(self):
        """Format: 'Leistungsart: LeistungsartID:X;LeistungsartTEXT:Y'"""
        root = _xml(_row())
        notes = root.xpath("//ram:IncludedNote/ram:Content/text()", namespaces=NSMAP)
        la_notes = [n for n in notes if "LeistungsartID" in n]
        self.assertTrue(la_notes[0].startswith("Leistungsart: LeistungsartID:"))
        self.assertIn(";LeistungsartTEXT:", la_notes[0])

    def test_mehrere_leistungsarten_auf_positionsebene(self):
        """Zwei verschiedene Leistungsarten → pro Position, nicht auf Kopfebene."""
        row = _row(positions=[
            InvoicePosition("Reinigung",   Decimal("1"), "Stk.", Decimal("50.00"), "", "Reinigung",   "Reinigung"),
            InvoicePosition("Winterdienst",Decimal("1"), "Stk.", Decimal("50.00"), "", "Winterdienst","Winterdienst"),
        ])
        root = _xml(row)
        # Auf Kopfebene kein LeistungsartID
        header_notes = root.xpath(
            "//rsm:ExchangedDocument/ram:IncludedNote/ram:Content/text()", namespaces=NSMAP
        )
        self.assertFalse(any("LeistungsartID" in n for n in header_notes))
        # Auf Positionsebene vorhanden
        pos_notes = root.xpath(
            "//ram:AssociatedDocumentLineDocument/ram:IncludedNote/ram:Content/text()", namespaces=NSMAP
        )
        self.assertEqual(len(pos_notes), 2)


class TestBuildXmlBetraege(TestCase):
    def test_nettogesamtbetrag(self):
        root = _xml(_row())
        sm = "//ram:SpecifiedTradeSettlementHeaderMonetarySummation"
        self.assertEqual(xp(root, sm + "/ram:TaxBasisTotalAmount"), "100.00")

    def test_bruttogesamtbetrag(self):
        root = _xml(_row())
        sm = "//ram:SpecifiedTradeSettlementHeaderMonetarySummation"
        self.assertEqual(xp(root, sm + "/ram:GrandTotalAmount"), "119.00")

    def test_umsatzsteuer(self):
        root = _xml(_row())
        self.assertEqual(xp(root, "//ram:ApplicableTradeTax/ram:CalculatedAmount"), "19.00")

    def test_faelligkeitsdatum_description(self):
        """ARP-Pflicht: Description muss 'Faelligkeitsdatum' enthalten."""
        root = _xml(_row())
        self.assertEqual(xp(root, "//ram:SpecifiedTradePaymentTerms/ram:Description"), "Faelligkeitsdatum")
