import hashlib
import io
import json
import sqlite3
import tempfile
import zipfile
from datetime import date
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch
from contextlib import closing

from django.contrib.auth import get_user_model
from django.db.models.deletion import ProtectedError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from pypdf import PdfReader, PdfWriter

from sedo.models import (AppSetting, IncomingInvoice, IncomingInvoicePackage, IncomingInvoiceSource,
    MailAccount, MailDraft, MailFolder, MailLocation, MailMessage)
from sedo.services.incoming_invoices import (IncomingError, collect_account, collect_message, create_package,
    print_collection, selected_approved, suggest_fields, verified_original)
from sedo.services.mail_mime import store_message
from sedo.services.mail_storage import original_bytes


def pdf_bytes():
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


class IncomingInvoiceTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.override = override_settings(SEDO_HOME=Path(self.temp.name))
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.user = get_user_model().objects.create_user('reviewer', password='test-only', is_staff=True)
        self.client.force_login(self.user)
        self.account = MailAccount.objects.create(email='rechnung@sedo-magdeburg.de', username='rechnung')
        self.folder = MailFolder.objects.create(account=self.account, remote_name='INBOX', name='Posteingang', role='inbox')

    def message(self, data=None, name='Rechnung.pdf', subject='Ihre Rechnung', cache=True, account=None, role='inbox'):
        account = account or self.account
        email = EmailMessage()
        email['From'] = 'lieferant@example.org'
        email['To'] = account.email
        email['Subject'] = subject
        email.set_content('Rechnung anbei.')
        email.add_attachment(pdf_bytes() if data is None else data, maintype='application', subtype='octet-stream', filename=name)
        message = store_message(account, email.as_bytes(), cache_only=cache)
        folder, _ = MailFolder.objects.get_or_create(account=account, role=role, defaults={'remote_name': role, 'name': role})
        MailLocation.objects.create(folder=folder, message=message, uid=message.pk, uidvalidity=1)
        return message

    def invoice(self, approved=False, **kwargs):
        message = self.message(**kwargs)
        collect_message(message)
        item = IncomingInvoice.objects.get(sources__message=message)
        if approved:
            item.state = 'approved'
            item.supplier = 'Lieferant'
            item.number = 'ER-123'
            item.document_date = date(2026, 9, 1)
            item.gross = '119.00'
            item.save()
        return item

    def test_auto_inbox_intake_pins_original_and_survives_cache_removal(self):
        message = self.message()
        raw = original_bytes(message)
        self.assertEqual(collect_account(self.account.pk), 1)
        message.refresh_from_db()
        self.assertFalse(message.raw_cached)
        self.assertEqual(bytes(message.raw), raw)
        self.assertTrue(message.invoice_scan_done)
        self.assertEqual(IncomingInvoice.objects.get().state, 'review')
        self.assertEqual(collect_account(self.account.pk), 0)
        with self.assertRaises(ProtectedError):
            message.delete()

    def test_duplicates_across_accounts_have_two_sources_and_one_original(self):
        data = pdf_bytes()
        self.message(data=data)
        other = MailAccount.objects.create(email='info@sedo-magdeburg.de', username='info')
        self.message(data=data, account=other)
        collect_account(self.account.pk)
        collect_account(other.pk)
        self.assertEqual(IncomingInvoice.objects.count(), 1)
        self.assertEqual(IncomingInvoiceSource.objects.count(), 2)

    def test_sent_spam_trash_and_disabled_intake_not_collected(self):
        for role in ('sent', 'junk', 'trash'):
            self.message(subject=role, role=role)
        self.assertEqual(collect_account(self.account.pk), 0)
        self.message()
        AppSetting.set('incoming_invoice_enabled', '0')
        self.assertEqual(collect_account(self.account.pk), 0)

    def test_failed_intake_is_visible_and_does_not_drop_original_mail(self):
        message = self.message(data=b'')
        collect_account(self.account.pk)
        message.refresh_from_db()
        self.assertTrue(message.invoice_scan_error)
        self.assertEqual(IncomingInvoice.objects.count(), 0)
        self.assertTrue(original_bytes(message))
        self.assertContains(self.client.get(reverse('sedo:incoming_index')), 'Belegübernahme fehlgeschlagen')

    def test_unknown_documents_stay_review_candidates_and_executables_are_ignored(self):
        self.message(name='Bescheinigung.pdf', subject='Unterlagen')
        self.message(name='setup.exe', subject='Neue Software')
        collect_account(self.account.pk)
        self.assertEqual(IncomingInvoice.objects.count(), 1)
        self.assertIn('Belegart prüfen', IncomingInvoice.objects.get().reason)

    def test_approval_requires_fields_confirmation_and_current_revision(self):
        item = self.invoice()
        url = reverse('sedo:incoming_detail', args=[item.pk])
        data = {'action': 'approve', 'revision': '0', 'supplier': 'Lieferant', 'number': 'ER-123',
                'document_date': '2026-09-01', 'gross': '119.00', 'currency': 'EUR'}
        self.assertContains(self.client.post(url, data), 'Bitte zuerst das Original prüfen')
        item.refresh_from_db()
        self.assertEqual(item.revision, 0)
        self.assertEqual(item.state, 'review')
        self.client.post(url, {**data, 'confirm': 'on'})
        item.refresh_from_db()
        self.assertEqual(item.state, 'approved')
        self.assertEqual(item.reviewed_by, self.user)
        self.client.post(url, {**data, 'action': 'ignore'})
        item.refresh_from_db()
        self.assertEqual(item.state, 'approved')

    def test_original_cannot_be_changed_through_review_form(self):
        item = self.invoice()
        original = bytes(item.original)
        self.client.post(reverse('sedo:incoming_detail', args=[item.pk]), {
            'action': 'save', 'revision': 0, 'currency': 'EUR', 'original': 'injected', 'digest': '0' * 64})
        item.refresh_from_db()
        self.assertEqual(verified_original(item), original)

    def test_tampered_original_is_blocked_before_approval_download_print_or_export(self):
        item = self.invoice(approved=True)
        IncomingInvoice.objects.filter(pk=item.pk).update(original=b'changed')
        item.refresh_from_db()
        with self.assertRaises(IncomingError):
            print_collection([item])
        with self.assertRaises(IncomingError):
            create_package([item], '2026-09', self.user)
        self.assertEqual(self.client.get(reverse('sedo:incoming_original', args=[item.pk])).status_code, 409)

    def test_unreviewed_selection_cannot_print_export_or_mark_printed(self):
        item = self.invoice()
        for action in ('print', 'package', 'printed'):
            self.client.post(reverse('sedo:incoming_batch'), {'invoice': [item.pk], 'action': action, 'month': '2026-09', 'confirm_print': 'yes'})
        item.refresh_from_db()
        self.assertIsNone(item.printed_at)
        self.assertFalse(IncomingInvoicePackage.objects.exists())

    def test_print_download_does_not_claim_physical_print_and_confirmation_is_required(self):
        item = self.invoice(approved=True)
        url = reverse('sedo:incoming_batch')
        response = self.client.post(url, {'invoice': [item.pk], 'action': 'print'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(PdfReader(io.BytesIO(response.content)).pages), 1)
        item.refresh_from_db()
        self.assertIsNone(item.printed_at)
        self.client.post(url, {'invoice': [item.pk], 'action': 'printed'})
        item.refresh_from_db()
        self.assertIsNone(item.printed_at)
        self.client.post(url, {'invoice': [item.pk], 'action': 'printed', 'confirm_print': 'yes'})
        item.refresh_from_db()
        self.assertIsNotNone(item.printed_at)

    def test_xml_is_archived_but_not_silently_omitted_from_print(self):
        item = self.invoice(data=b'<Invoice/>', name='Rechnung.xml', approved=True)
        with self.assertRaises(IncomingError):
            print_collection([item])

    def test_package_contains_verified_originals_sources_and_safe_csv(self):
        item = self.invoice(approved=True)
        item.supplier = '=HYPERLINK("x")'
        item.save()
        package = create_package([item], '2026-09', self.user)
        with zipfile.ZipFile(io.BytesIO(bytes(package.data))) as z:
            manifest = json.loads(z.read('manifest.json'))
            self.assertEqual(manifest['belege'][0]['id'], item.pk)
            for file in manifest['dateien']:
                self.assertEqual(hashlib.sha256(z.read(file['pfad'])).hexdigest(), file['sha256'])
            self.assertIn("'=HYPERLINK", z.read('Uebersicht.csv').decode())
            self.assertTrue(any(name.endswith('.eml') for name in z.namelist()))
        with self.assertRaises(IncomingError):
            create_package([item], '2026-08', self.user)

    def test_package_email_is_draft_only_idempotent_and_private_to_author(self):
        package = create_package([self.invoice(approved=True)], '2026-09', self.user)
        url = reverse('sedo:incoming_package_email', args=[package.pk])
        with patch('sedo.services.mail_send.send_draft') as send:
            data = {'account': self.account.pk, 'recipient': 'steuer@example.org'}
            self.client.post(url, data)
            self.client.post(url, data)
            send.assert_not_called()
        self.assertEqual(MailDraft.objects.count(), 1)
        draft = MailDraft.objects.get()
        self.assertEqual(draft.state, 'draft')
        self.assertEqual(draft.attachments.count(), 1)
        self.assertEqual(bytes(draft.attachments.get().data), bytes(package.data))
        other = get_user_model().objects.create_user('other')
        self.client.force_login(other)
        self.client.post(url, data)
        self.assertEqual(MailDraft.objects.count(), 1)
        self.assertEqual(self.client.get(reverse('sedo:mail_review', args=[draft.pk])).status_code, 404)

    def test_invalid_recipient_or_tampered_package_never_creates_draft(self):
        package = create_package([self.invoice(approved=True)], '2026-09', self.user)
        url = reverse('sedo:incoming_package_email', args=[package.pk])
        self.client.post(url, {'account': self.account.pk, 'recipient': 'one@example.org,two@example.org'})
        self.assertFalse(MailDraft.objects.exists())
        IncomingInvoicePackage.objects.filter(pk=package.pk).update(data=b'tampered')
        self.client.post(url, {'account': self.account.pk, 'recipient': 'one@example.org'})
        self.assertFalse(MailDraft.objects.exists())
        self.assertEqual(self.client.get(reverse('sedo:incoming_package_download', args=[package.pk])).status_code, 409)

    def test_package_draft_can_be_deleted_and_recreated_without_losing_archive(self):
        package = create_package([self.invoice(approved=True)], '2026-09', self.user)
        self.client.post(reverse('sedo:incoming_package_email', args=[package.pk]), {'account': self.account.pk, 'recipient': 'tax@example.org'})
        package.refresh_from_db()
        self.client.post(reverse('sedo:mail_draft_action', args=[package.draft_id]), {'action': 'delete'})
        package.refresh_from_db()
        self.assertIsNone(package.draft_id)
        self.assertEqual(IncomingInvoice.objects.count(), 1)

    def test_package_status_cannot_claim_delivery_when_attachment_removed_or_changed(self):
        from sedo.services.mail_send import prepare
        from sedo.services.mail_secrets import MailError
        package = create_package([self.invoice(approved=True)], '2026-09', self.user)
        self.client.post(reverse('sedo:incoming_package_email', args=[package.pk]), {'account': self.account.pk, 'recipient': 'tax@example.org'})
        package.refresh_from_db()
        prepare(package.draft)
        package.draft.attachments.update(data=b'changed')
        with self.assertRaises(MailError):
            prepare(package.draft)
        package.draft.attachments.all().delete()
        with self.assertRaises(MailError):
            prepare(package.draft)

    def test_ai_fills_unsaved_form_only(self):
        item = self.invoice()
        with patch('sedo.incoming_views.suggest_fields', return_value={'supplier': 'Vorschlag', 'number': 'KI-1', 'document_date': '2026-09-01', 'gross': '123.45', 'currency': 'EUR'}):
            response = self.client.post(reverse('sedo:incoming_detail', args=[item.pk]), {'action': 'suggest'})
        self.assertContains(response, 'Vorschlag')
        item.refresh_from_db()
        self.assertEqual(item.supplier, '')
        self.assertEqual(item.state, 'review')

    def test_xml_entities_are_not_read_or_sent_to_model(self):
        item = self.invoice(data=b'<!DOCTYPE a [<!ENTITY x SYSTEM "file:///etc/passwd">]><Invoice>&x;</Invoice>', name='Rechnung.xml')
        with patch('sedo.services.ai_local.generate_structured') as ai:
            with self.assertRaises(IncomingError):
                suggest_fields(item)
            ai.assert_not_called()

    def test_empty_scan_pdf_has_manual_fallback_without_ai_call(self):
        item = self.invoice()
        with patch('sedo.services.ai_local.generate_structured') as ai:
            with self.assertRaises(IncomingError):
                suggest_fields(item)
            ai.assert_not_called()

    def test_settings_are_staff_only_and_mutations_require_csrf_post(self):
        url = reverse('sedo:incoming_settings')
        self.assertEqual(self.client.get(url).status_code, 405)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user)
        self.assertEqual(strict.post(url, {'enabled': 'on'}).status_code, 403)
        self.user.is_staff = False
        self.user.save()
        self.assertEqual(self.client.post(url, {'enabled': 'on'}).status_code, 403)
        self.client.logout()
        for name in ('incoming_index',):
            self.assertEqual(self.client.get(reverse('sedo:' + name)).status_code, 302)

    def test_detail_index_package_templates_escape_untrusted_names(self):
        item = self.invoice(approved=True, name='<script>alert(1)</script>.pdf')
        package = create_package([item], '2026-09', self.user)
        for url in (reverse('sedo:incoming_index'), reverse('sedo:incoming_detail', args=[item.pk]), reverse('sedo:incoming_package', args=[package.pk])):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, '<script>alert(1)</script>')

    def test_selection_cannot_be_empty_duplicate_or_too_large(self):
        for ids in ([], [1, 1], list(range(101))):
            with self.assertRaises(IncomingError):
                selected_approved(ids)

    def test_backup_keeps_incoming_original_package_and_pinned_source_bytes(self):
        from sedo.backup_utils import _sanitized_db_copy
        item = self.invoice(approved=True)
        package = create_package([item], '2026-09', self.user)
        raw = original_bytes(item.sources.get().message)
        source = Path(self.temp.name) / 'source.sqlite3'
        with closing(sqlite3.connect(source)) as db:
            db.executescript('CREATE TABLE sedo_incominginvoice (original BLOB); '
                'CREATE TABLE sedo_incominginvoicepackage (data BLOB); CREATE TABLE sedo_mailmessage (raw BLOB);')
            db.execute('INSERT INTO sedo_incominginvoice VALUES (?)', (bytes(item.original),))
            db.execute('INSERT INTO sedo_incominginvoicepackage VALUES (?)', (bytes(package.data),))
            db.execute('INSERT INTO sedo_mailmessage VALUES (?)', (raw,))
            db.commit()
        clean = _sanitized_db_copy(source)
        self.addCleanup(clean.unlink)
        with closing(sqlite3.connect(clean)) as db:
            self.assertEqual(db.execute('SELECT original FROM sedo_incominginvoice').fetchone()[0], bytes(item.original))
            self.assertEqual(db.execute('SELECT data FROM sedo_incominginvoicepackage').fetchone()[0], bytes(package.data))
            self.assertEqual(db.execute('SELECT raw FROM sedo_mailmessage').fetchone()[0], raw)

    def test_regular_imap_sync_triggers_intake_without_marking_mail_read(self):
        from sedo.tests.test_mail_client import FakeIMAP
        from sedo.services.mail_imap import sync_account
        message = self.message()
        peer = FakeIMAP()
        peer.folders['INBOX'][11] = [original_bytes(message), set()]
        with patch('sedo.services.mail_imap.connection') as connect:
            connect.return_value.__enter__.return_value = peer
            sync_account(self.account.pk)
        self.assertEqual(IncomingInvoice.objects.count(), 1)
        self.assertFalse(MailLocation.objects.filter(folder__role='inbox', seen=True).exists())

    def test_settings_remain_renderable_without_javascript_and_without_help(self):
        response = self.client.get(reverse('sedo:settings'))
        self.assertContains(response, 'data-settings-section', count=6)
        self.assertContains(response, 'name="smtp_host"')
        self.assertContains(response, 'quiet-ui.js')
        self.assertNotContains(response, 'data-help')
        self.assertNotContains(response, 'info-tip')
