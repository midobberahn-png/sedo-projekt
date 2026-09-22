import io
from urllib.parse import urlencode
from unittest.mock import patch
from email.message import EmailMessage

from django.test import TestCase
from django.urls import reverse
from lxml import html
from PIL import Image

from sedo.models import IncomingInvoice, IncomingInvoiceSource
from sedo.services.incoming_detection import classify_document
from sedo.services.incoming_invoices import collect_message, reclassify_unreviewed
from sedo.services.mail_mime import store_message
from .document_fixtures import text_pdf
from . import test_incoming_invoices as fixtures
from .test_incoming_invoices import pdf_bytes


class IncomingDetectionTests(TestCase):
    setUp = fixtures.IncomingInvoiceTests.setUp
    message = fixtures.IncomingInvoiceTests.message
    invoice = fixtures.IncomingInvoiceTests.invoice

    def test_each_attachment_is_checked_even_when_email_says_invoice(self):
        message = EmailMessage()
        message['From'], message['To'], message['Subject'] = 'supplier@example.org', self.account.email, 'Ihre Rechnung mit Unterlagen'
        message.set_content('Anbei Rechnung, Angebot, Lieferschein und Termin für Zählerablesung.')
        for name, content in [('beleg.pdf', text_pdf()), ('Angebot.pdf', text_pdf('Angebot Nr. 11\nGesamt: 119,00 EUR')),
                              ('Lieferschein.pdf', text_pdf('Lieferschein Nr. 12')),
                              ('Termin.pdf', text_pdf('Termin zur Zählerablesung\nBitte am 30.09.2026 Zugang ermöglichen.')),
                              ('Dokument.pdf', text_pdf('Information zur Hausordnung'))]:
            message.add_attachment(content, maintype='application', subtype='pdf', filename=name)
        stored = store_message(self.account, message.as_bytes(), cache_only=True)
        self.assertEqual(collect_message(stored), 1)
        self.assertEqual(list(IncomingInvoice.objects.values_list('filename',flat=True)), ['beleg.pdf'])
        self.assertEqual(IncomingInvoiceSource.objects.count(), 1)
        self.assertEqual(collect_message(stored), 0)
        self.assertEqual(bytes(IncomingInvoice.objects.get().original), text_pdf())

    def test_offer_delivery_and_meter_letters_never_inherit_invoice_filename_or_subject(self):
        for title in ('Angebot Nr. 12', 'Lieferschein 2026-33', 'Termin für Zählerablesung',
                      'Zählerablesung am 30.09.2026', 'Kostenvoranschlag', 'Proforma-Rechnung', 'Gutschrift Nr. 12'):
            with self.subTest(title=title):
                result = classify_document(text_pdf(title+'\nBezug: Rechnungsnummer 12\nGesamt: 119,00 EUR'), 'Rechnung.pdf', 'Ihre Rechnung')
                self.assertEqual(result.kind, 'other')

    def test_invoice_with_offer_delivery_reference_and_meter_service_is_kept(self):
        for text in ('Rechnung Nr. 22\nRechnungsbetrag: 119,00 EUR\nBezug: Angebot 3 und Lieferschein 7',
                     'Rechnungsnummer: 123\nZählerablesung durchgeführt\nGesamtbetrag: 119,00 EUR'):
            self.assertEqual(classify_document(text_pdf(text), 'Dokument123.pdf', 'Unterlagen').kind, 'invoice')
            self.assertEqual(classify_document(text_pdf(text), 'Rechnung zum Angebot 123.pdf', 'Unterlagen').kind, 'invoice')

    def test_ordinary_or_unreadable_documents_are_not_invoices(self):
        for data,name,subject in [(pdf_bytes(),'Scan.pdf','Ihre Rechnung'),(text_pdf('Unser Treffen am Dienstag'),'Datei.pdf','Rechnung'),
                                  (b'<html>Rechnung</html>','Rechnung.pdf','Rechnung'),(b'<Invoice/>','Rechnung.xml','Rechnung')]:
            self.assertEqual(classify_document(data,name,subject).kind, 'unknown')

    def test_scan_needs_explicit_filename_and_subject_without_conflicting_document_kind(self):
        out=io.BytesIO(); Image.new('RGB',(20,20),'white').save(out,format='PNG')
        for data,name in [(pdf_bytes(),'Rechnung-123.pdf'),(out.getvalue(),'Rechnung-123.png')]:
            self.assertEqual(classify_document(data,name,'Ihre Rechnung 123').kind,'invoice')
            self.assertEqual(classify_document(data,name,'Unterlagen').kind,'unknown')
            self.assertEqual(classify_document(data,name,'Angebot und Rechnung').kind,'unknown')

    def test_structured_invoice_namespace_type_and_fields_are_required(self):
        xml=b'''<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
          xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
          xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2">
          <cbc:ID>R-123</cbc:ID><cbc:InvoiceTypeCode>380</cbc:InvoiceTypeCode>
          <cac:LegalMonetaryTotal><cbc:PayableAmount currencyID="EUR">119.00</cbc:PayableAmount></cac:LegalMonetaryTotal></Invoice>'''
        self.assertEqual(classify_document(xml,'Daten.xml').kind,'invoice')
        self.assertEqual(classify_document(xml.replace(b'>380<',b'>381<'),'Daten.xml').kind,'other')
        self.assertEqual(classify_document(xml.replace(b'>380<',b'>82<'),'Daten.xml').kind,'invoice')
        self.assertEqual(classify_document(xml.replace(b'>380<',b'>999<'),'Daten.xml').kind,'unknown')
        self.assertEqual(classify_document(xml.replace(b'>119.00<',b'>unknown<'),'Daten.xml').kind,'unknown')
        self.assertEqual(classify_document(xml.replace(b'Invoice-2',b'Invented-2'),'Daten.xml').kind,'unknown')
        hostile=b'<!DOCTYPE Invoice [<!ENTITY x SYSTEM "file:///etc/passwd">]>'+xml.replace(b'R-123',b'&x;')
        self.assertEqual(classify_document(hostile,'Rechnung.xml','Rechnung').kind,'unknown')

    def test_reclassification_only_separates_untouched_known_non_invoices(self):
        wrong=self.invoice(data=text_pdf('Angebot Nr. A-1\nGesamtbetrag: 119,00 EUR'),name='Angebot.pdf')
        real=self.invoice(data=text_pdf('Rechnung Nr. 2\nGesamtbetrag: 123,00 EUR'),name='Rechnung2.pdf')
        reviewed=self.invoice(data=text_pdf('Lieferschein 3'),name='Lieferschein.pdf',approved=True)
        edited=self.invoice(data=text_pdf('Angebot Nr. 4'),name='Angebot4.pdf')
        edited.revision=1; edited.save(update_fields=['revision'])
        unknown=self.invoice(data=pdf_bytes(),name='Scan.pdf')
        before=bytes(wrong.original)
        preview=reclassify_unreviewed()
        self.assertEqual([r['id'] for r in preview],[wrong.pk])
        wrong.refresh_from_db(); self.assertEqual(wrong.state,'review')
        result=reclassify_unreviewed(apply=True,user=self.user)
        self.assertEqual([r['id'] for r in result if r['applied']],[wrong.pk])
        wrong.refresh_from_db(); self.assertEqual(wrong.state,'ignored')
        self.assertEqual(bytes(wrong.original),before); self.assertEqual(wrong.sources.count(),1)
        for item,state in [(real,'review'),(reviewed,'approved'),(edited,'review'),(unknown,'review')]:
            item.refresh_from_db(); self.assertEqual(item.state,state)
        self.assertEqual(reclassify_unreviewed(apply=True,user=self.user),[])
        self.client.post(reverse('sedo:incoming_detail',args=[wrong.pk]),{'action':'reopen','revision':wrong.revision})
        wrong.refresh_from_db(); self.assertEqual(wrong.state,'review')
        self.assertEqual(reclassify_unreviewed(apply=True,user=self.user),[])

    def test_reclassification_rolls_back_if_audit_fails(self):
        item=self.invoice(data=text_pdf('Lieferschein 99'),name='Lieferschein99.pdf')
        with patch('sedo.services.incoming_invoices.record_action',side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError): reclassify_unreviewed(apply=True)
        item.refresh_from_db(); self.assertEqual(item.state,'review'); self.assertEqual(item.revision,0)

    def test_back_from_preview_and_review_keeps_list_filters_and_page(self):
        item=self.invoice()
        parent=reverse('sedo:incoming_index')+'?view=approved&q=Lieferant&page=3'
        detail=reverse('sedo:incoming_detail',args=[item.pk])+'?'+urlencode({'back':parent})
        response=self.client.get(detail)
        self.assertEqual(response.context['back_url'],parent)
        self.assertContains(response,'← Zurück')
        preview=response.context['preview_url']
        response=self.client.get(preview)
        self.assertEqual(response.context['preview_back_url'],detail)
        self.assertEqual(response.context['detail_url'],detail)
        direct=self.client.get(reverse('sedo:incoming_preview',args=[item.pk]),{'back':parent})
        self.assertEqual(direct.context['preview_back_url'],parent)
        invalid=self.client.post(detail,{'action':'approve','revision':0})
        self.assertEqual(invalid.context['back_url'],parent)
        saved=self.client.post(detail,{'action':'ignore','revision':0})
        self.assertEqual(saved.url,detail)

    def test_back_to_mail_and_untrusted_return_targets(self):
        item=self.invoice(); source=item.sources.get()
        parent=reverse('sedo:mail_message',args=[source.message_id])+'?section=archive&location=7'
        response=self.client.get(reverse('sedo:incoming_detail',args=[item.pk]),{'back':parent})
        self.assertEqual(response.context['back_url'],parent)
        for unsafe in ('https://evil.example/','//evil.example/','/einstellungen/','/\\evil.example/','/eingangsrechnungen/?q=\nspoof'):
            response=self.client.get(reverse('sedo:incoming_preview',args=[item.pk]),{'back':unsafe})
            self.assertEqual(response.context['preview_back_url'],reverse('sedo:incoming_index'))

    def test_list_links_preserve_context_and_ignored_documents_leave_archive(self):
        item=self.invoice()
        response=self.client.get(reverse('sedo:incoming_index'),{'view':'all','q':'Rechnung'})
        doc=html.fromstring(response.content)
        links=doc.xpath('//a[starts-with(@href,$path)]',path=reverse('sedo:incoming_preview',args=[item.pk]))
        self.assertIn('back=',links[0].get('href'))
        item.state='ignored'; item.save(update_fields=['state'])
        self.assertNotContains(self.client.get(reverse('sedo:incoming_index'),{'view':'all'}),item.filename)
        self.assertContains(self.client.get(reverse('sedo:incoming_index'),{'view':'ignored'}),item.filename)

    def test_cii_invoice_type_and_total_are_recognized(self):
        data=b'''<rsm:CrossIndustryInvoice xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100" xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100">
        <rsm:ExchangedDocument><ram:ID>R-42</ram:ID><ram:TypeCode>380</ram:TypeCode></rsm:ExchangedDocument>
        <rsm:SupplyChainTradeTransaction><ram:ApplicableHeaderTradeSettlement><ram:SpecifiedTradeSettlementHeaderMonetarySummation><ram:GrandTotalAmount>119.00</ram:GrandTotalAmount></ram:SpecifiedTradeSettlementHeaderMonetarySummation></ram:ApplicableHeaderTradeSettlement></rsm:SupplyChainTradeTransaction></rsm:CrossIndustryInvoice>'''
        self.assertEqual(classify_document(data,'factur-x.xml').kind,'invoice')
        self.assertEqual(classify_document(data.replace(b'>380<',b'>325<'),'factur-x.xml').kind,'other')
