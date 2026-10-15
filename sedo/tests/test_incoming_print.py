import base64
import io
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, SimpleTestCase
from django.urls import reverse
from lxml import html
from PIL import Image
from pypdf import PdfReader, PdfWriter

from sedo.desktop_print import IncomingPrintAPI
from sedo.models import IncomingInvoice, AuditLog
from . import test_incoming_invoices as incoming_fixtures


def pdf(pages=2, password=None):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    if password:
        writer.encrypt(password)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class IncomingPrintTests(TestCase):
    setUp = incoming_fixtures.IncomingInvoiceTests.setUp
    message = incoming_fixtures.IncomingInvoiceTests.message
    invoice = incoming_fixtures.IncomingInvoiceTests.invoice

    def url(self, item):
        return reverse('sedo:incoming_print', args=[item.pk])

    def test_print_button_exists_in_list_assignment_and_document(self):
        item = self.invoice()
        for route in ['incoming_index', 'incoming_detail', 'incoming_preview']:
            url = reverse('sedo:'+route, args=[] if route=='incoming_index' else [item.pk])
            doc = html.fromstring(self.client.get(url).content)
            self.assertEqual(len(doc.xpath('//a[@data-incoming-print and @href=$url]', url=self.url(item))), 1)
            self.assertEqual(len(doc.xpath('//script[contains(@src,"incoming-print.js")]')), 1)
            self.assertFalse(doc.xpath('//title/script'))

    def test_all_pages_of_original_print_before_review_without_changing_workflow(self):
        original = pdf()
        item = self.invoice(data=original)
        for state in ['review', 'approved', 'ignored']:
            item.state = state
            item.save(update_fields=['state'])
            before = IncomingInvoice.objects.values().get(pk=item.pk)
            audit_count = AuditLog.objects.count()
            response = self.client.get(self.url(item))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, original)
            self.assertEqual(len(PdfReader(io.BytesIO(response.content)).pages), 2)
            self.assertEqual(response['Content-Type'], 'application/pdf')
            self.assertTrue(response['Content-Disposition'].startswith('inline'))
            self.assertIn('no-store', response['Cache-Control'])
            self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
            self.assertEqual(IncomingInvoice.objects.values().get(pk=item.pk), before)
            self.assertEqual(AuditLog.objects.count(), audit_count)

    def test_print_requires_login_and_is_get_only(self):
        item = self.invoice()
        self.assertEqual(self.client.post(self.url(item)).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.get(self.url(item)).status_code, 302)

    def test_changed_and_unreadable_pdfs_cannot_be_printed(self):
        item = self.invoice(data=pdf())
        IncomingInvoice.objects.filter(pk=item.pk).update(original=b'changed')
        self.assertContains(self.client.get(self.url(item)), 'Prüfsumme', status_code=409)
        for data in [b'<html>Not a PDF</html>', b'%PDF-broken', pdf(password='secret')]:
            item = self.invoice(data=data)
            self.assertEqual(self.client.get(self.url(item)).status_code, 409)

    def test_multipage_tiff_prints_every_page_without_changing_original(self):
        output = io.BytesIO()
        Image.new('RGB', (80, 40), 'red').save(output, 'TIFF', save_all=True,
            append_images=[Image.new('RGB', (40, 80), 'blue')])
        item = self.invoice(data=output.getvalue(), name='Rechnung.tiff')
        response = self.client.get(self.url(item))
        self.assertEqual(response.status_code, 200)
        pages = PdfReader(io.BytesIO(response.content)).pages
        self.assertEqual(len(pages), 2)
        for page in pages:
            self.assertEqual(len(page.images), 1)
            self.assertAlmostEqual(float(page.mediabox.width), 595.28, places=1)
        item.refresh_from_db()
        self.assertEqual(bytes(item.original), output.getvalue())
        self.assertIsNone(item.printed_at)

    def test_png_and_jpeg_are_printable(self):
        for format, ext in [('PNG', 'png'), ('JPEG', 'jpg')]:
            output = io.BytesIO()
            Image.new('RGB', (40, 20), 'white').save(output, format)
            response = self.client.get(self.url(self.invoice(data=output.getvalue(), name='Rechnung.'+ext)))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(PdfReader(io.BytesIO(response.content)).pages), 1)

    def test_xml_is_printed_as_literal_text_without_executing_markup(self):
        xml = '<?xml version="1.0"?><Invoice><script>alert(1)</script><Number>RE-123</Number></Invoice>'
        for data in [xml.encode(), xml.encode('utf-16')]:
            response = self.client.get(self.url(self.invoice(data=data, name='Rechnung.xml')))
            self.assertEqual(response.status_code, 200)
            text = ''.join(page.extract_text() for page in PdfReader(io.BytesIO(response.content)).pages)
            self.assertIn('XML-Original', text)
            self.assertIn('<script>alert(1)</script>', text)
            self.assertIn('RE-123', text)

    def test_document_size_and_page_budgets_fail_closed(self):
        item = self.invoice(data=pdf())
        with patch('sedo.services.incoming_print.MAX_PRINT_BYTES', 10):
            self.assertEqual(self.client.get(self.url(item)).status_code, 409)
        with patch('sedo.services.incoming_print.MAX_PRINT_PAGES', 1):
            self.assertEqual(self.client.get(self.url(item)).status_code, 409)

    def test_print_and_image_preview_routes_do_not_parse_in_parent(self):
        original = pdf()
        pdf_invoice = self.invoice(data=original)
        output = io.BytesIO()
        Image.new('RGB', (40, 20), 'white').save(output, 'PNG')
        image_invoice = self.invoice(data=output.getvalue(), name='Rechnung.png')
        with patch('pypdf.PdfReader', side_effect=AssertionError('parent PDF parser')), \
                patch('PIL.Image.open', side_effect=AssertionError('parent image parser')):
            self.assertEqual(self.client.get(self.url(pdf_invoice)).content, original)
            response = self.client.get(self.url(image_invoice))
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.content.startswith(b'%PDF-'))
            response = self.client.get(reverse('sedo:incoming_original', args=[image_invoice.pk]), {'inline': '1'})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.content.startswith(b'\x89PNG'))

    def test_xml_suggestion_route_uses_worker_parser(self):
        from sedo.services.incoming_invoices import suggest_fields
        invoice = self.invoice(data=b'<Invoice>synthetic invoice</Invoice>', name='Rechnung.xml')
        with patch('lxml.etree.fromstring', side_effect=AssertionError('parent XML parser')), \
                patch('sedo.services.ai_local.generate_structured', return_value={'number': 'synthetic'}) as suggest:
            self.assertEqual(suggest_fields(invoice), {'number': 'synthetic'})
        self.assertIn('synthetic invoice', suggest.call_args.args[1])

    def test_batch_size_is_checked_before_original_blobs_are_loaded(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from sedo.services.incoming_invoices import IncomingError, MAX_BATCH_BYTES, selected_approved
        invoice = self.invoice(data=pdf())
        IncomingInvoice.objects.filter(pk=invoice.pk).update(state='approved', size=MAX_BATCH_BYTES + 1)
        with CaptureQueriesContext(connection) as queries:
            with self.assertRaises(IncomingError):
                selected_approved([invoice.pk])
        self.assertEqual(len(queries), 1)
        self.assertNotIn('"sedo_incominginvoice"."original"', queries[0]['sql'])


class DesktopPrintBoundaryTests(SimpleTestCase):
    def setUp(self):
        self.api = IncomingPrintAPI('http://127.0.0.1:8765')
        self.api._window = SimpleNamespace(get_current_url=lambda: self.url)
        self.url = 'http://127.0.0.1:8765/eingangsrechnungen/'
        self.payload = base64.b64encode(pdf()).decode()

    @patch('sedo.desktop_print.sys', SimpleNamespace(platform='darwin'))
    @patch('sedo.desktop_print._mac_print', return_value={'native': True, 'ok': True})
    def test_only_local_invoice_pages_can_open_native_print(self, native):
        for path in ['/eingangsrechnungen/', '/eingangsrechnungen/beleg/1/', '/eingangsrechnungen/beleg/1/ansehen/']:
            self.url = 'http://127.0.0.1:8765'+path
            self.assertTrue(self.api.print_incoming_pdf(self.payload)['ok'])
        self.assertEqual(native.call_count, 3)
        for url in ['https://example.org/eingangsrechnungen/', 'http://127.0.0.1:8766/eingangsrechnungen/',
                    'http://127.0.0.1:8765/email/', 'file:///eingangsrechnungen/']:
            self.url = url
            self.assertFalse(self.api.print_incoming_pdf(self.payload)['ok'])
        self.assertEqual(native.call_count, 3)

    @patch('sedo.desktop_print.sys', SimpleNamespace(platform='darwin'))
    @patch('sedo.desktop_print._mac_print')
    def test_invalid_data_and_parallel_dialogs_are_blocked(self, native):
        for payload in [None, {}, 'not base64', base64.b64encode(b'<html>').decode()]:
            self.assertFalse(self.api.print_incoming_pdf(payload)['ok'])
        with patch('sedo.desktop_print.MAX_BYTES', 4):
            self.assertFalse(self.api.print_incoming_pdf(self.payload)['ok'])
        with self.api._printing:
            self.assertFalse(self.api.print_incoming_pdf(self.payload)['ok'])
        native.assert_not_called()

    @patch('sedo.desktop_print.sys', SimpleNamespace(platform='win32'))
    @patch('sedo.desktop_print._mac_print')
    def test_windows_uses_the_browser_pdf_print_dialog(self, native):
        self.assertEqual(self.api.print_incoming_pdf(self.payload), {'native': False})
        native.assert_not_called()
