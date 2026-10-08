"""Generated documents stay in the authenticated app; no cookie hand-off."""
from pathlib import Path
import os
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.http import Http404
from django.test import Client, TestCase
from django.urls import reverse
from lxml import html

from sedo.document_probe import DocumentProbeError
from sedo.document_views import _read_generated_document
from sedo.models import Customer, Invoice
from sedo.security import READ_ONLY_GROUP
from sedo.services.zugferd_validation import ValidationResult
from .document_fixtures import text_pdf
from .file_fixtures import isolate_files


class GeneratedDocumentNavigationTests(TestCase):
    def setUp(self):
        isolate_files(self)
        self.user = get_user_model().objects.create_user('document-navigation')
        self.client.force_login(self.user)
        self.relative = 'generated/RE-TEST/RE-TEST.pdf'
        self.path = Path(settings.MEDIA_ROOT) / self.relative
        self.path.parent.mkdir(parents=True)
        self.data = text_pdf('Synthetische Rechnung zur Bedienprüfung')
        self.path.write_bytes(self.data)
        self.url = reverse('sedo:document_preview', kwargs={'path': self.relative})

    def test_page_frame_and_download_all_require_login(self):
        for query in ('', '?inline=1', '?download=1'):
            with self.subTest(query=query):
                response = Client().get(self.url + query)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response['Location'].startswith('/login/?next='))
                self.assertNotIn(self.data, response.content)

    def test_internal_page_has_session_preserving_frame_back_and_download(self):
        response = self.client.get(self.url, {'back': '/rechnungen/?page=2'})
        self.assertEqual(response.status_code, 200)
        doc = html.fromstring(response.content)
        self.assertEqual(doc.xpath('//iframe/@src'), [self.url + '?inline=1'])
        self.assertTrue(doc.xpath('//iframe/@title')[0].startswith('PDF:'))
        self.assertEqual(doc.xpath('//a[contains(. ,"Zurück")]/@href'), ['/rechnungen/?page=2'])
        self.assertEqual(doc.xpath('//a[@download]/@href'), [self.url + '?download=1'])
        self.assertFalse(doc.xpath('//iframe[@src="http://example.invalid"]'))
        self.assertIn("frame-src 'self'", response['Content-Security-Policy'])
        self.assertEqual(response['X-Frame-Options'], 'DENY')
        self.assertIn('no-store', response['Cache-Control'])

    def test_inline_pdf_is_verified_and_returned_unchanged(self):
        response = self.client.get(self.url, {'inline': '1'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), self.data)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')
        self.assertTrue(response['Content-Disposition'].startswith('inline;'))
        self.assertIn("frame-ancestors 'self'", response['Content-Security-Policy'])

    def test_worker_failure_does_not_relax_pdf_validation(self):
        with patch('sedo.document_probe.validate_pdf', side_effect=DocumentProbeError('unsafe')):
            response = self.client.get(self.url, {'inline': '1'})
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, 'Original herunterladen', status_code=409)
        self.assertEqual(self.path.read_bytes(), self.data)

    def test_download_preserves_bytes_and_does_not_run_parent_pdf_parser(self):
        with patch('sedo.document_probe.validate_pdf', side_effect=AssertionError('download is not parsing')):
            response = self.client.get(self.url, {'download': '1'})
        self.assertEqual(b''.join(response.streaming_content), self.data)
        self.assertTrue(response['Content-Disposition'].startswith('attachment;'))
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertIn('no-store', response['Cache-Control'])

    def test_xml_is_escaped_text_never_an_active_document(self):
        raw = b'<?xml version="1.0"?><root><script>alert("synthetic")</script></root>'
        target = self.path.with_suffix('.xml')
        target.write_bytes(raw)
        url = reverse('sedo:document_preview', kwargs={'path': 'generated/RE-TEST/RE-TEST.xml'})
        with patch('lxml.etree.fromstring', side_effect=AssertionError('must not parse XML')):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '&lt;script&gt;')
        self.assertNotContains(response, '<script>alert(')
        self.assertFalse(html.fromstring(response.content).xpath('//iframe'))
        self.assertEqual(self.client.get(url, {'inline': '1'}).status_code, 404)
        download = self.client.get(url, {'download': '1'})
        self.assertEqual(b''.join(download.streaming_content), raw)

    def test_oversized_xml_preview_is_bounded_but_download_is_available(self):
        self.path.with_suffix('.xml').write_bytes(b'<root>synthetic</root>')
        url = reverse('sedo:document_preview', kwargs={'path': 'generated/RE-TEST/RE-TEST.xml'})
        with patch('sedo.document_views.MAX_XML_PREVIEW_BYTES', 8):
            response = self.client.get(url)
        self.assertContains(response, 'Textvorschau zu groß')
        self.assertContains(response, 'Original herunterladen')

    def test_unsafe_back_targets_are_never_links(self):
        for back in ('https://evil.example.invalid/', '//evil.example.invalid/',
                     '/\\evil.example.invalid/', 'javascript:alert(1)', '/safe\nunsafe'):
            with self.subTest(back=back):
                doc = html.fromstring(self.client.get(self.url, {'back': back}).content)
                self.assertEqual(doc.xpath('//a[contains(. ,"Zurück")]/@href'), [reverse('sedo:invoice_list')])

    def test_generated_roots_extensions_and_path_boundaries_are_enforced(self):
        for path in ('../secret.pdf', '/generated/RE-TEST/RE-TEST.pdf',
                     'generated/../../secret.pdf', 'generated\\secret.pdf',
                     'backups/secret.pdf', 'generated/unsafe.html', 'generated/a\x00.pdf'):
            with self.subTest(path=path), self.assertRaises(Http404):
                _read_generated_document(path)

    def test_symlinks_directories_and_size_overflow_are_rejected(self):
        symlink = self.path.parent / 'alias.pdf'
        symlink.symlink_to(self.path)
        with self.assertRaises(Http404):
            _read_generated_document('generated/RE-TEST/alias.pdf')
        link_dir = Path(settings.MEDIA_ROOT) / 'generated/linked'
        link_dir.symlink_to(self.path.parent, target_is_directory=True)
        with self.assertRaises(Http404):
            _read_generated_document('generated/linked/RE-TEST.pdf')
        self.path.parent.joinpath('directory.pdf').mkdir()
        with self.assertRaises(Http404):
            _read_generated_document('generated/RE-TEST/directory.pdf')
        with patch('sedo.document_views.MAX_DOCUMENT_BYTES', 4), self.assertRaises(Http404):
            _read_generated_document(self.relative)

    def test_readonly_can_view_but_no_post_is_accepted(self):
        self.user.groups.add(Group.objects.get_or_create(name=READ_ONLY_GROUP)[0])
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.post(self.url).status_code, 403)
        self.user.groups.clear()
        self.assertEqual(self.client.post(self.url).status_code, 405)
        self.assertEqual(self.path.read_bytes(), self.data)

    @skipUnless(hasattr(os, 'mkfifo'), 'FIFO files are unavailable on this platform')
    def test_special_file_is_rejected_without_blocking(self):
        os.mkfifo(self.path.parent / 'pipe.pdf')
        with self.assertRaises(Http404):
            _read_generated_document('generated/RE-TEST/pipe.pdf')

    def test_cashbook_generation_returns_to_internal_preview(self):
        response = self.client.get(reverse('sedo:cashbook_pdf'), {'month': '2026-10'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith('/dokumente/cashbook/Kassenbuch_2026-10.pdf?back='))
        preview = self.client.get(response['Location'])
        self.assertEqual(preview.status_code, 200)
        doc = html.fromstring(preview.content)
        self.assertEqual(doc.xpath('//a[contains(. ,"Zurück")]/@href'),
                         [reverse('sedo:cashbook_list') + '?month=2026-10'])

    def test_invoice_links_stay_internal_and_validation_has_return_navigation(self):
        customer = Customer.objects.create(name='Synthetischer Testkunde')
        invoice = Invoice.objects.create(number='RE-TEST', customer=customer,
                                         pdf_file=self.relative, xml_file='generated/RE-TEST/test.xml',
                                         zugferd_file='generated/RE-TEST/test_zugferd.pdf')
        detail = reverse('sedo:invoice_detail', args=[invoice.pk])
        for url in (detail, reverse('sedo:invoice_list')):
            doc = html.fromstring(self.client.get(url).content)
            links = doc.xpath('//a[starts-with(@href,"/dokumente/")]')
            self.assertTrue(links)
            self.assertTrue(all(not link.get('target') for link in links))
            self.assertFalse(doc.xpath('//a[starts-with(@href,"/media/")][@target="_blank"]'))
        with patch('sedo.views.validate_invoice_zugferd', return_value=ValidationResult()):
            response = self.client.get(reverse('sedo:invoice_zugferd_validation', args=[invoice.pk]))
        self.assertEqual(response.status_code, 200)
        doc = html.fromstring(response.content)
        self.assertEqual(doc.xpath('//a[normalize-space(.)="Zur Rechnung"]/@href'), [detail])

    def test_external_links_are_not_retargeted(self):
        source = Path(settings.BASE_DIR, 'sedo/templates/sedo/dashboard.html').read_text()
        self.assertIn('href="https://www.wetter.com/apps_und_mehr/website/api/" target="_blank"', source)
