import io
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib import messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse
from lxml import html
from PIL import Image

from sedo.services import mail_imap, mail_progress, mail_worker
from . import test_incoming_invoices as incoming_fixtures
from .test_mail_client import MailFixture, email_bytes


class DocumentPreviewTests(MailFixture):
    message = incoming_fixtures.IncomingInvoiceTests.message
    invoice = incoming_fixtures.IncomingInvoiceTests.invoice

    def test_original_button_and_filename_open_preview_without_downloading(self):
        item = self.invoice()
        preview = reverse('sedo:incoming_preview', args=[item.pk])
        for route in [reverse('sedo:incoming_index'), reverse('sedo:incoming_detail', args=[item.pk])]:
            doc = html.fromstring(self.client.get(route).content)
            links = doc.xpath('//a[@href=$url]', url=preview)
            self.assertTrue(links)
            self.assertFalse(any('download' in link.attrib for link in links))
        response = self.client.get(preview)
        self.assertContains(response, '<iframe')
        original = self.client.get(reverse('sedo:incoming_original', args=[item.pk]), {'inline': '1'})
        self.assertEqual(original.content, bytes(item.original))
        self.assertEqual(original['Content-Type'], 'application/pdf')
        self.assertTrue(original['Content-Disposition'].startswith('inline'))
        self.assertEqual(original['X-Frame-Options'], 'SAMEORIGIN')
        self.assertIn('no-store', original['Cache-Control'])
        item.refresh_from_db()
        self.assertEqual(item.state, 'review')

    def test_xml_is_escaped_and_never_loaded_as_active_document(self):
        item = self.invoice(data=b'<?xml version="1.0"?><script>alert(1)</script>', name='Rechnung.xml')
        response = self.client.get(reverse('sedo:incoming_preview', args=[item.pk]))
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertEqual(self.client.get(reverse('sedo:incoming_original', args=[item.pk]), {'inline':'1'}).status_code, 400)

    def test_tiff_preview_is_png_and_original_download_unchanged(self):
        data = io.BytesIO()
        Image.new('RGB', (30, 20), 'white').save(data, 'TIFF')
        item = self.invoice(data=data.getvalue(), name='Rechnung.tiff')
        url = reverse('sedo:incoming_original', args=[item.pk])
        preview = self.client.get(url, {'inline':'1'})
        self.assertEqual(preview['Content-Type'], 'image/png')
        self.assertTrue(preview.content.startswith(b'\x89PNG'))
        self.assertEqual(self.client.get(url).content, data.getvalue())

    def test_corrupt_original_and_disguised_html_do_not_render(self):
        item = self.invoice(data=b'<html><script>alert(1)</script></html>', name='Rechnung.pdf')
        preview = reverse('sedo:incoming_preview', args=[item.pk])
        self.assertEqual(self.client.get(preview).status_code, 409)
        item.original = b'changed'
        item.save(update_fields=['original'])
        self.assertContains(self.client.get(preview), 'Prüfsumme', status_code=409)
        self.assertEqual(self.client.get(reverse('sedo:incoming_original', args=[item.pk]), {'inline':'1'}).status_code, 409)

    def test_document_routes_require_login(self):
        item = self.invoice()
        self.client.logout()
        for route in ('incoming_preview', 'incoming_original'):
            self.assertEqual(self.client.get(reverse('sedo:'+route, args=[item.pk])).status_code, 302)


class MailProgressTests(MailFixture):
    def test_progress_is_updated_during_peek_and_cleared_after_sync(self):
        self.server.folders['INBOX'] = {uid:[email_bytes(f'Mail {uid}'), set()] for uid in range(1,4)}
        observed = []
        original = mail_progress.update
        def capture(*args):
            original(*args)
            observed.append(self.client.get(reverse('sedo:mail_status')).json()['accounts'][0])
        with patch('sedo.services.mail_progress.update', side_effect=capture):
            mail_imap.sync_account(self.account.pk)
        self.assertEqual([state['loaded'] for state in observed[:4]], [0,1,2,3])
        self.assertTrue(all(state['running'] and state['total']==3 for state in observed))
        self.assertIsNone(mail_progress.snapshot(self.account.pk))
        self.assertEqual(self.client.get(reverse('sedo:mail_status')).json()['accounts'][0]['loaded'], 3)

    def test_failed_sync_clears_progress_and_status_never_exposes_secrets(self):
        with patch('sedo.services.mail_imap.discover', side_effect=RuntimeError('private server text')):
            with self.assertRaises(RuntimeError): mail_imap.sync_account(self.account.pk)
        self.assertIsNone(mail_progress.snapshot(self.account.pk))
        response = self.client.get(reverse('sedo:mail_status'))
        self.assertNotContains(response, 'mail-test-secret')
        self.assertNotContains(response, 'password_encrypted')
        self.assertNotContains(response, 'lease_token')
        self.assertNotContains(response, 'private server text')

    def test_worker_waits_five_minutes_and_only_polls_enabled_configured_accounts(self):
        self.account.enabled = True
        self.account.save(update_fields=['enabled'])
        waits = []
        class StopLoop(BaseException): pass
        class Thread:
            def __init__(self, target, **kwargs): self.target = target
            def start(self): self.target()
        def wait(seconds):
            waits.append(seconds)
            raise StopLoop()
        with patch.object(mail_worker, '_worker', None), patch.object(mail_worker.threading, 'Thread', Thread), \
             patch.object(mail_worker._wake, 'wait', side_effect=wait), patch.object(mail_worker, 'request_sync') as sync:
            with self.assertRaises(StopLoop): mail_worker.start_mail_worker()
            sync.assert_called_once_with(self.account.pk)
        self.assertEqual(waits, [300])


class QuietInterfaceTests(SimpleTestCase):
    def test_explanations_live_in_documentation_not_tooltips(self):
        root = Path(settings.BASE_DIR)
        for template in (root/'sedo/templates/sedo').rglob('*.html'):
            self.assertNotIn('info-tip', template.read_text())
            self.assertNotIn('data-help', template.read_text())
        self.assertIn('Bonsai', (root/'BEDIENHINWEISE.md').read_text())

    def test_error_escaped_and_success_not_shown(self):
        request = RequestFactory().get('/')
        request.session = {}
        request._messages = FallbackStorage(request)
        messages.success(request, 'Unnecessary confirmation')
        messages.error(request, '<script>Visible error</script>')
        markup = render_to_string('sedo/base.html', {'messages':request._messages}, request=request)
        self.assertNotIn('Unnecessary confirmation', markup)
        self.assertIn('&lt;script&gt;Visible error&lt;/script&gt;', markup)
