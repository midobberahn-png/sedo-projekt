import os
import smtplib
import sqlite3
import ssl
import tempfile
import uuid
from contextlib import closing
from datetime import timedelta
from email import policy
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db.models.deletion import ProtectedError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from sedo.backup_utils import _sanitized_db_copy
from sedo.models import Customer, MailAccount, MailDraft, MailFolder, MailLocation, MailMessage, Offer, WorkTask
from sedo.services import mail_imap, mail_mime, mail_send
from sedo.services.mail_secrets import MailError, decrypt_password, encrypt_password
from sedo.services.mail_worker import recover_interrupted_sends
from sedo.services.mail_storage import cache_path, original_bytes, pin_message


def email_bytes(subject='Anfrage zur Reinigung', body='Bitte ein Angebot für das Treppenhaus.', html=None, attachment=False):
    msg = EmailMessage(policy=policy.SMTP)
    msg['From'] = 'Testkunde <kunde@example.org>'
    msg['To'] = 'info@seller.example.invalid'
    msg['Cc'] = 'rechnung@seller.example.invalid, kontakt@example.org'
    msg['Subject'] = subject
    msg['Message-ID'] = '<anfrage@example.org>'
    msg['Date'] = 'Fri, 18 Sep 2026 10:00:00 +0200'
    msg.set_content(body)
    if html is not None:
        msg.add_alternative(html, subtype='html')
    if attachment:
        msg.add_attachment(b'fake pdf content', maintype='application', subtype='pdf', filename='Plan.pdf')
    return msg.as_bytes()


class FakeIMAP:
    """Small stateful server model; sequence number deliberately differs from UID."""
    def __init__(self):
        self.folders = {'INBOX': {11: [email_bytes(), set()]}, 'Sent': {}, 'Trash': {77: [email_bytes('Andere gelöschte Mail'), {b'\\Deleted'}]}}
        self.validity = {'INBOX': 9, 'Sent': 10, 'Trash': 11}
        self.capabilities = (b'IMAP4REV1', b'MOVE', b'UIDPLUS')
        self.selected = None
        self.calls = []
        self.append_count = 0
        self.logout_count = 0

    def login(self, *_):
        return 'OK', [b'authenticated']

    def capability(self):
        return 'OK', [b' '.join(self.capabilities)]

    def list(self):
        return 'OK', [b'(\\HasNoChildren) "/" "INBOX"', b'(\\Sent) "/" "Sent"', b'(\\Trash) "/" "Trash"']

    def select(self, name, readonly=True):
        self.selected = name.strip('"')
        self.calls.append(('select', self.selected, readonly))
        return 'OK', [str(len(self.folders[self.selected])).encode()]

    def response(self, key):
        return key, [str(self.validity[self.selected]).encode()]

    def logout(self):
        self.logout_count += 1

    def append(self, folder, flags, when, raw):
        self.append_count += 1
        target = self.folders[folder.strip('"')]
        target[max(target, default=100) + 1] = [raw, {b'\\Seen'}]
        return 'OK', [b'appended']

    def uid(self, command, *args):
        self.calls.append((command, *args))
        folder = self.folders[self.selected]
        if command == 'SEARCH':
            if args[1] == 'UID':
                found = [int(args[2])] if int(args[2]) in folder else []
            elif args[1] == 'HEADER':
                found = [uid for uid, (raw, _) in folder.items() if args[3].strip('"').encode() in raw]
            else:
                found = [uid for uid, (_, flags) in folder.items() if b'\\Deleted' not in flags]
            return 'OK', [' '.join(map(str, found)).encode()]
        ids = [int(value) for value in args[0].split(',')]
        if command == 'FETCH':
            result = []
            for uid in ids:
                if uid not in folder:
                    continue
                raw, flags = folder[uid]
                if 'BODY.PEEK' in args[1]:
                    result.extend([(f'999 (UID {uid} BODY[] {{{len(raw)}}}'.encode(), raw), b')'])
                else:
                    result.append(f'999 (UID {uid} RFC822.SIZE {len(raw)} FLAGS ('.encode() + b' '.join(flags) + b'))')
            return 'OK', result
        if command == 'STORE':
            flag = args[2].strip('()').encode()
            if args[1].startswith('+'):
                folder[ids[0]][1].add(flag)
            else:
                folder[ids[0]][1].discard(flag)
        elif command == 'MOVE':
            target = self.folders[args[1].strip('"')]
            target[max(target, default=100) + 1] = folder.pop(ids[0])
        elif command == 'EXPUNGE':
            folder.pop(ids[0])
        return 'OK', [b'done']


class FakeSMTP:
    def __init__(self, refusal=None, data_error=None):
        self.refusal, self.data_error = refusal, data_error
        self.deliveries, self.recipients, self.resets = [], [], 0

    def mail(self, address):
        self.sender = address
        return 250, b'ok'

    def rcpt(self, address):
        self.recipients.append(address)
        return (550, b'no') if address == self.refusal else (250, b'ok')

    def rset(self):
        self.resets += 1

    def data(self, raw):
        self.deliveries.append(raw)
        if self.data_error:
            raise self.data_error
        return 250, b'accepted'

    def close(self):
        pass


class MailFixture(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.override = override_settings(SEDO_HOME=Path(self.temp.name), MEDIA_ROOT=Path(self.temp.name) / 'media')
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.user = get_user_model().objects.create_user('mail-admin', is_staff=True)
        self.other = get_user_model().objects.create_user('mail-other')
        self.client.force_login(self.user)
        self.account = MailAccount.objects.create(email='info@seller.example.invalid', username='info@seller.example.invalid')
        self.account.password_encrypted = encrypt_password(self.account, 'mail-test-secret-never-log')
        self.account.save()
        self.server = FakeIMAP()
        self.network = patch('sedo.services.mail_imap.imaplib.IMAP4_SSL', return_value=self.server)
        self.imap_factory = self.network.start()
        self.addCleanup(self.network.stop)
        self.queue = patch('sedo.mail_views.request_sync', return_value=True)
        self.queue.start()
        self.addCleanup(self.queue.stop)

    def sync(self):
        mail_imap.sync_account(self.account.pk)
        return MailLocation.objects.select_related('message', 'folder__account').get(folder__role='inbox')

    def draft(self, **kwargs):
        data = {'account': self.account, 'author': self.user, 'to': 'kunde@example.org', 'subject': 'Ihr Angebot', 'body': 'Guten Tag, vielen Dank.'}
        data.update(kwargs)
        return MailDraft.objects.create(**data)


class MailProtocolTests(MailFixture):
    def test_real_imaplib_parses_wire_responses_and_sends_valid_uid_commands(self):
        import imaplib
        import shlex
        import subprocess
        import sys
        command = [sys.executable, str(Path(__file__).with_name('mail_protocol_peer.py'))]
        command = subprocess.list2cmdline(command) if os.name == 'nt' else shlex.join(command)
        with patch('sedo.services.mail_imap.imaplib.IMAP4_SSL', side_effect=lambda *args, **kwargs: imaplib.IMAP4_stream(command)):
            mail_imap.sync_account(self.account.pk)
            location = MailLocation.objects.select_related('folder__account').get(folder__role='inbox')
            self.assertEqual(location.uid, 41)
            self.assertTrue(location.seen)
            self.assertEqual(location.message.subject, 'Echte IMAP-Protokollprobe')
            mail_imap.set_flag(location, 'flagged', True)
            mail_imap.append_sent(self.account, original_bytes(location.message))
            mail_imap.move_message(location, MailFolder.objects.get(role='sent'))

    def test_sync_errors_never_expose_server_echoed_credentials(self):
        import imaplib
        with patch.object(self.server, 'login', side_effect=imaplib.IMAP4.error('mail-test-secret-never-log')):
            with self.assertRaises(MailError) as caught:
                mail_imap.sync_account(self.account.pk)
        self.account.refresh_from_db()
        self.assertNotIn('mail-test-secret', str(caught.exception))
        self.assertNotIn('mail-test-secret', self.account.last_error)
        self.assertIsNone(self.account.lease_until)

    def test_missing_remote_uid_is_never_operated_on(self):
        location = self.sync()
        del self.server.folders['INBOX'][11]
        with self.assertRaises(MailError):
            mail_imap.set_flag(location, 'seen', True)
        self.assertFalse(any(call[0] == 'STORE' for call in self.server.calls))

    def test_sync_is_idempotent_uses_peek_and_verified_tls(self):
        self.sync()
        self.sync()
        self.assertEqual(MailLocation.objects.count(), 1)
        self.assertEqual(MailMessage.objects.count(), 1)
        self.assertFalse(MailLocation.objects.get().seen)
        context = self.imap_factory.call_args.kwargs['ssl_context']
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        bodies = [call for call in self.server.calls if call[0] == 'FETCH' and 'BODY' in call[2]]
        self.assertEqual(len(bodies), 1)
        self.assertIn('BODY.PEEK[]', bodies[0][2])
        self.assertEqual(bodies[0][1], '11')

    def test_remote_flags_and_removal_are_reflected_without_deleting_business_source(self):
        location = self.sync()
        customer = Customer.objects.create(name='Kunde')
        offer = Offer.objects.create(number='AG-MAIL', customer=customer, source_mail=location.message)
        self.server.folders['INBOX'][11][1] = {b'\\Seen', b'\\Flagged'}
        self.sync()
        location.refresh_from_db()
        self.assertTrue(location.seen and location.flagged)
        del self.server.folders['INBOX'][11]
        mail_imap.sync_account(self.account.pk)
        self.assertFalse(MailLocation.objects.exists())
        self.assertTrue(MailMessage.objects.filter(pk=offer.source_mail_id).exists())

    def test_uidvalidity_change_does_not_reuse_old_uid_content(self):
        old = self.sync().message
        self.server.validity['INBOX'] = 50
        self.server.folders['INBOX'][11] = [email_bytes('Neue Nachricht mit gleicher UID'), set()]
        new = self.sync()
        self.assertEqual(new.uidvalidity, 50)
        self.assertNotEqual(old.pk, new.message_id)
        self.assertEqual(new.message.subject, 'Neue Nachricht mit gleicher UID')

    def test_stale_uidvalidity_cannot_mark_or_move_other_message(self):
        location = self.sync()
        self.server.validity['INBOX'] = 51
        with self.assertRaises(MailError):
            mail_imap.set_flag(location, 'seen', True)
        self.assertFalse(any(call[0] == 'STORE' for call in self.server.calls))

    def test_mark_read_and_star_update_server_and_local_copy(self):
        location = self.sync()
        mail_imap.set_flag(location, 'seen', True)
        mail_imap.set_flag(location, 'flagged', True)
        location.refresh_from_db()
        self.assertTrue(location.seen and location.flagged)
        self.assertIn(b'\\Seen', self.server.folders['INBOX'][11][1])
        self.assertIn(b'\\Flagged', self.server.folders['INBOX'][11][1])

    def test_move_targets_uid_and_never_expunge_other_deleted_mail(self):
        location = self.sync()
        trash = MailFolder.objects.get(role='trash')
        original_pk = location.message_id
        mail_imap.move_message(location, trash)
        self.assertNotIn(11, self.server.folders['INBOX'])
        self.assertIn(77, self.server.folders['Trash'])
        mail_imap.sync_account(self.account.pk)
        moved = MailLocation.objects.get(folder=trash)
        self.assertEqual(moved.message_id, original_pk)
        self.assertNotEqual(moved.uid, 11)
        mail_imap.delete_permanently(MailLocation.objects.select_related('folder__account').get(pk=moved.pk))
        self.assertIn(77, self.server.folders['Trash'])
        self.assertFalse(any(call[0] == 'EXPUNGE' and len(call) == 1 for call in self.server.calls))

    def test_server_without_move_is_left_unchanged(self):
        location = self.sync()
        self.server.capabilities = (b'IMAP4REV1', b'UIDPLUS')
        with self.assertRaisesMessage(MailError, 'IMAP MOVE'):
            mail_imap.move_message(location, MailFolder.objects.get(role='trash'))
        self.assertIn(11, self.server.folders['INBOX'])
        self.assertFalse(any(call[0] in ('STORE', 'COPY') for call in self.server.calls))

    def test_delete_requires_trash_and_uidplus(self):
        location = self.sync()
        with self.assertRaises(MailError):
            mail_imap.delete_permanently(location)
        mail_imap.move_message(location, MailFolder.objects.get(role='trash'))
        mail_imap.sync_account(self.account.pk)
        self.server.capabilities = (b'IMAP4REV1',)
        with self.assertRaises(MailError):
            mail_imap.delete_permanently(MailLocation.objects.select_related('folder__account').get())

    def test_account_lease_excludes_concurrent_work_and_releases_on_error(self):
        with self.assertRaises(MailError):
            with mail_imap.account_lease(self.account.pk):
                with mail_imap.account_lease(self.account.pk):
                    self.fail('second lease must not succeed')
        self.account.refresh_from_db()
        self.assertIsNone(self.account.lease_until)
        with mail_imap.account_lease(self.account.pk):
            pass

    def test_folder_encoding_and_literal_names(self):
        name = 'Kunden/Größe & Räume'
        encoded = mail_imap.encode_folder(name)
        self.assertEqual(mail_imap.decode_folder(encoded), name)
        remote, flags = mail_imap._folder_entry((b'(\\HasNoChildren) "/" {19}', encoded.encode()))
        self.assertEqual(remote, encoded)
        self.assertIn(b'\\hasnochildren', flags)
        self.assertEqual(mail_imap.quoted('A"B\\C'), '"A\\"B\\\\C"')
        with self.assertRaises(MailError):
            mail_imap.quoted('INBOX\r\nLOGOUT')

    def test_partial_initial_sync_is_visible_and_continues(self):
        self.server.folders['INBOX'] = {uid: [email_bytes(f'Anfrage {uid}'), set()] for uid in range(1, 14)}
        with patch('sedo.services.mail_imap.SYNC_FOLDER_BATCH', 10):
            mail_imap.sync_account(self.account.pk)
        folder = MailFolder.objects.get(role='inbox')
        self.assertEqual(folder.total_count, 13)
        self.assertEqual(folder.cached_count, 10)
        self.assertEqual(min(folder.locations.values_list('uid', flat=True)), 4)
        response = self.client.get(reverse('sedo:mail_inbox'), {'folder': folder.pk})
        state = self.client.get(reverse('sedo:mail_status')).json()['accounts'][0]
        self.assertEqual((state['loaded'], state['total']), (10, 13))
        self.assertFalse(state['running'])
        self.assertNotContains(response, 'Jetzt abrufen')
        mail_imap.sync_account(self.account.pk)
        self.assertEqual(folder.locations.count(), 13)


class MailMimeAndSecurityTests(MailFixture):
    def test_received_mime_uses_private_cache_and_missing_copy_is_refetched(self):
        message = self.sync().message
        self.assertTrue(message.raw_cached)
        self.assertEqual(bytes(message.raw), b'')
        path = cache_path(message.account_id, message.digest)
        self.assertEqual(path.read_bytes(), email_bytes())
        path.unlink()
        with self.assertRaisesMessage(MailError, 'erneut abrufen'):
            original_bytes(message)
        self.sync()
        self.assertEqual(original_bytes(message), email_bytes())
        self.assertEqual(MailMessage.objects.count(), 1)

    def test_business_source_survives_cache_loss_and_customer_link_pins_bytes(self):
        message = self.sync().message
        customer = Customer.objects.create(name='TEST Geschäftsquelle')
        message.customer = customer
        message.save(update_fields=['customer'])
        cache_path(message.account_id, message.digest).unlink()
        message.refresh_from_db()
        self.assertFalse(message.raw_cached)
        self.assertEqual(original_bytes(message), email_bytes())

    def test_corrupt_cache_is_never_archived_as_business_source(self):
        message = self.sync().message
        path = cache_path(message.account_id, message.digest)
        path.write_bytes(b'corrupted')
        with self.assertRaisesMessage(MailError, 'beschädigt'):
            pin_message(message)
        message.refresh_from_db()
        self.assertTrue(message.raw_cached)
        self.assertEqual(bytes(message.raw), b'')
        self.sync()
        self.assertEqual(original_bytes(message), email_bytes())

    def test_offer_cannot_be_saved_with_missing_source_original(self):
        from .test_wizard import _angebot_post
        message = self.sync().message
        cache_path(message.account_id, message.digest).unlink()
        response = self.client.post(reverse('sedo:offer_create'), _angebot_post(source_mail=message.pk))
        self.assertContains(response, 'erneut abrufen')
        self.assertFalse(Offer.objects.exists())
        self.assertFalse(Customer.objects.exists())

    def test_smtp_tls_paths_verify_certificates_and_fail_before_login(self):
        from unittest.mock import MagicMock
        transport = MagicMock()
        for security, port, factory in [('ssl', 465, 'SMTP_SSL'), ('starttls', 587, 'SMTP')]:
            self.account.smtp_security, self.account.smtp_port = security, port
            with self.subTest(security=security), patch('sedo.services.mail_send.smtplib.' + factory, return_value=transport):
                mail_send.smtp_connection(self.account)
                if security == 'starttls':
                    context = transport.starttls.call_args.kwargs['context']
                    self.assertTrue(context.check_hostname)
                    transport.login.reset_mock()
                    transport.starttls.side_effect = ssl.SSLCertVerificationError('invalid')
                    with self.assertRaises(ssl.SSLCertVerificationError):
                        mail_send.smtp_connection(self.account)
                    transport.login.assert_not_called()

    def test_mailbox_settings_never_echo_password_and_preserve_it_when_blank(self):
        data = {'email': self.account.email, 'username': self.account.username, 'display_name': 'SEDO', 'signature': '', 'password': '', 'smtp_port': 465, 'smtp_security': 'ssl'}
        encrypted = self.account.password_encrypted
        response = self.client.post(reverse('sedo:mail_account', args=[self.account.pk]), data)
        self.assertEqual(response.status_code, 302)
        self.account.refresh_from_db()
        self.assertEqual(self.account.password_encrypted, encrypted)
        data.update(username='different-login', password='')
        response = self.client.post(reverse('sedo:mail_account', args=[self.account.pk]), data)
        self.assertContains(response, 'Passwort erneut eingeben')
        self.account.refresh_from_db()
        self.assertEqual(self.account.username, 'info@seller.example.invalid')
        self.assertNotContains(response, encrypted)

    def test_too_many_mime_parts_are_bounded_and_original_remains_downloadable(self):
        raw = email_bytes(attachment=True)
        with patch('sedo.services.mail_mime.MAX_PARTS', 1):
            message = mail_mime.store_message(self.account, raw)
        self.assertTrue(message.content_error)
        self.assertEqual(bytes(message.raw), raw)

    def test_mime_parser_keeps_attachments_and_safe_filename(self):
        message = mail_mime.store_message(self.account, email_bytes(attachment=True))
        self.assertIn('Treppenhaus', message.body)
        name, content = mail_mime.attachment_bytes(message, message.attachments[0]['index'])
        self.assertEqual((name, content), ('Plan.pdf', b'fake pdf content'))
        self.assertEqual(mail_mime.safe_name('../../secret\r\n.txt'), 'secret.txt')
        self.assertEqual(mail_mime.safe_name('C:\\Windows\\file.txt'), 'file.txt')
        with self.assertRaises(MailError):
            mail_mime.attachment_bytes(message, 9999)

    def test_hostile_html_is_passive_and_original_is_preserved(self):
        hostile = '<script>alert(document.cookie)</script><img src="https://tracker.example/pixel"><form action="/bad"><input></form><a href="javascript:alert(1)">bad</a><a href="https://example.org">Link</a><svg onload="boom()"></svg><p style="background:url(https://tracker.example)">Hallo</p>'
        raw = email_bytes(html=hostile)
        message = mail_mime.store_message(self.account, raw)
        response = self.client.get(reverse('sedo:mail_html', args=[message.pk]))
        self.assertNotContains(response, '<script')
        self.assertNotContains(response, '<img')
        self.assertNotContains(response, '<form')
        self.assertNotContains(response, 'javascript:')
        self.assertNotContains(response, 'tracker.example')
        self.assertContains(response, 'rel="noopener noreferrer"')
        self.assertIn("default-src 'none'", response['Content-Security-Policy'])
        self.assertIn('sandbox allow-popups', response['Content-Security-Policy'])
        self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')
        self.assertEqual(bytes(message.raw), raw)

    def test_html_only_mail_gets_searchable_text_and_bad_charset_falls_back(self):
        raw = b'From: kunde@example.org\r\nSubject: Test\r\nContent-Type: text/html; charset=does-not-exist\r\n\r\n<p>Nur HTML</p>'
        message = mail_mime.store_message(self.account, raw)
        self.assertIn('Nur HTML', message.body)

    def test_recipient_validation_blocks_header_injection_and_invalid_partial_list(self):
        for value in ('one@example.org\r\nBcc: bad@example.org', 'one@example.org, invalid', 'Name <bad>'):
            with self.subTest(value=value), self.assertRaises(MailError):
                mail_mime.addresses(value)
        self.assertEqual(mail_mime.addresses('Name <one@example.org>; other@example.org'), ['one@example.org', 'other@example.org'])

    def test_password_is_encrypted_bound_to_identity_and_private_on_disk(self):
        self.assertNotIn('mail-test-secret', self.account.password_encrypted)
        self.assertEqual(decrypt_password(self.account), 'mail-test-secret-never-log')
        if os.name != 'nt':
            self.assertEqual((Path(self.temp.name) / 'data/.mail_key').stat().st_mode & 0o777, 0o600)
        self.account.email = 'other@example.org'
        with self.assertRaises(MailError):
            decrypt_password(self.account)

    def test_backup_strips_mail_credentials_disables_sync_and_keeps_live_copy(self):
        path = Path(self.temp.name) / 'source.sqlite3'
        with closing(sqlite3.connect(path)) as db:
            db.execute('CREATE TABLE sedo_mailaccount (password_encrypted text, enabled integer, lease_token text, lease_until text)')
            db.execute('INSERT INTO sedo_mailaccount VALUES (?, 1, ?, ?)', (self.account.password_encrypted, 'lease', '2026-09-18'))
            db.commit()
        clean = _sanitized_db_copy(path)
        self.addCleanup(clean.unlink)
        with closing(sqlite3.connect(clean)) as db:
            self.assertEqual(db.execute('SELECT * FROM sedo_mailaccount').fetchone(), ('', 0, '', None))
        self.assertNotIn(self.account.password_encrypted.encode(), clean.read_bytes())
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute('SELECT enabled FROM sedo_mailaccount').fetchone()[0], 1)

    def test_all_mail_content_and_actions_require_login(self):
        location = self.sync()
        self.client.logout()
        for name, args in [('mail_inbox', []), ('mail_html', [location.message_id]), ('mail_original', [location.message_id]), ('mail_status', [])]:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse('sedo:' + name, args=args)).status_code, 302)
        self.assertEqual(self.client.post(reverse('sedo:mail_message_action', args=[location.message_id]), {'action': 'seen', 'location': location.pk}).status_code, 302)

    def test_mutations_require_post_csrf_and_staff_for_account_changes(self):
        location = self.sync()
        url = reverse('sedo:mail_message_action', args=[location.message_id])
        self.assertEqual(self.client.get(url).status_code, 405)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user)
        self.assertEqual(strict.post(url, {'action': 'seen', 'location': location.pk}).status_code, 403)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('sedo:mail_account', args=[self.account.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse('sedo:mail_account_action', args=[self.account.pk]), {'action': 'test'}).status_code, 403)

    def test_download_is_attachment_never_inline_html(self):
        location = self.sync()
        response = self.client.get(reverse('sedo:mail_original', args=[location.message_id]))
        self.assertEqual(response['Content-Type'], 'application/octet-stream')
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertIn('no-store', response['Cache-Control'])


class MailSendTests(MailFixture):
    def setUp(self):
        super().setUp()
        self.sync()
        self.smtp = FakeSMTP()
        self.transport = patch('sedo.services.mail_send.smtp_connection', return_value=self.smtp)
        self.transport.start()
        self.addCleanup(self.transport.stop)

    def test_send_once_bcc_only_in_envelope_and_sent_copy_only_once(self):
        draft = self.draft(bcc='hidden@example.org')
        sent = mail_send.send_draft(draft.pk, self.user, 0)
        self.assertEqual(sent.state, 'sent')
        self.assertTrue(sent.copy_saved)
        self.assertEqual(len(self.smtp.deliveries), 1)
        self.assertIn('hidden@example.org', self.smtp.recipients)
        self.assertNotIn(b'hidden@example.org', self.smtp.deliveries[0])
        self.assertNotIn(b'Bcc:', self.smtp.deliveries[0])
        with self.assertRaises(MailError):
            mail_send.send_draft(draft.pk, self.user, 0)
        self.assertEqual(len(self.smtp.deliveries), 1)
        mail_imap.append_sent(self.account, bytes(sent.raw))
        self.assertEqual(self.server.append_count, 1)

    def test_one_refused_recipient_aborts_before_data_for_everyone(self):
        self.smtp.refusal = 'no@example.org'
        draft = self.draft(to='yes@example.org, no@example.org')
        with self.assertRaisesMessage(MailError, 'an niemanden'):
            mail_send.send_draft(draft.pk, self.user, 0)
        self.assertEqual(self.smtp.deliveries, [])
        draft.refresh_from_db()
        self.assertEqual(draft.state, 'draft')
        self.assertEqual(self.smtp.resets, 1)

    def test_connection_loss_during_data_requires_manual_review_no_retry(self):
        self.smtp.data_error = smtplib.SMTPServerDisconnected('lost')
        draft = self.draft()
        with self.assertRaisesMessage(MailError, 'bereits versendet'):
            mail_send.send_draft(draft.pk, self.user, 0)
        draft.refresh_from_db()
        self.assertEqual(draft.state, 'unknown')
        with self.assertRaises(MailError):
            mail_send.send_draft(draft.pk, self.user, draft.revision)
        self.assertEqual(len(self.smtp.deliveries), 1)

    def test_explicit_data_rejection_can_return_to_draft(self):
        self.smtp.data_error = smtplib.SMTPDataError(552, b'too big')
        draft = self.draft()
        with self.assertRaises(MailError):
            mail_send.send_draft(draft.pk, self.user, 0)
        draft.refresh_from_db()
        self.assertEqual(draft.state, 'draft')

    def test_sent_copy_failure_never_turns_accepted_message_back_to_draft(self):
        draft = self.draft()
        with patch('sedo.services.mail_send.append_sent', side_effect=MailError('offline')):
            sent = mail_send.send_draft(draft.pk, self.user, 0)
        self.assertEqual(sent.state, 'sent')
        self.assertFalse(sent.copy_saved)
        with mail_imap.account_lease(self.account.pk):
            mail_send.save_sent_copy(sent)
        self.assertEqual(len(self.smtp.deliveries), 1)
        sent.refresh_from_db()
        self.assertTrue(sent.copy_saved)

    def test_crash_recovery_marks_old_sending_unknown(self):
        draft = self.draft(state='sending')
        MailDraft.objects.filter(pk=draft.pk).update(updated_at=timezone.now() - timedelta(minutes=10))
        recover_interrupted_sends()
        draft.refresh_from_db()
        self.assertEqual(draft.state, 'unknown')

    def test_reply_all_excludes_both_own_addresses_and_preserves_thread(self):
        MailAccount.objects.create(email='rechnung@seller.example.invalid', username='rechnung')
        source = MailMessage.objects.get()
        initial = mail_send.reply_initial(source, 'reply_all', self.account)
        self.assertIn('kunde@example.org', initial['to'])
        self.assertEqual(initial['cc'], 'kontakt@example.org')
        draft = self.draft(source=source, mode='reply_all', **initial)
        raw, _, _ = mail_send.prepare(draft)
        msg = mail_mime.parse(raw)
        self.assertEqual(str(msg['In-Reply-To']), '<anfrage@example.org>')
        self.assertIn('<anfrage@example.org>', str(msg['References']))

    def test_forward_preserves_attachment_bytes(self):
        source = mail_mime.store_message(self.account, email_bytes(attachment=True))
        draft = self.draft(source=source, mode='forward')
        mail_send.forward_attachments(draft, source)
        self.assertEqual(bytes(draft.attachments.get().data), b'fake pdf content')
        raw, _, _ = mail_send.prepare(draft)
        self.assertIn('Plan.pdf', str(mail_mime.parse(raw)))

    def test_stale_review_cannot_send_changed_draft(self):
        draft = self.draft(revision=3)
        with self.assertRaises(MailError):
            mail_send.send_draft(draft.pk, self.user, 2)
        self.assertEqual(self.smtp.deliveries, [])


class MailWorkflowTests(MailFixture):
    def test_mail_ids_above_thousand_are_not_german_formatted(self):
        from lxml import html
        message = MailMessage.objects.create(pk=1234, account=self.account, digest='a' * 64,
                                            subject='Große Kennung', sender_email='kunde@example.org', raw=email_bytes())
        folder = MailFolder.objects.create(pk=1234, account=self.account, remote_name='INBOX', name='INBOX', role='inbox')
        location = MailLocation.objects.create(pk=1234, folder=folder, message=message, uid=1234, uidvalidity=1)
        response = self.client.get(reverse('sedo:mail_message', args=[message.pk]))
        doc = html.fromstring(response.content)
        self.assertEqual(set(doc.xpath('//input[@name="location"]/@value')), {'1234'})
        self.assertTrue(any('?folder=1234&account=' in value for value in doc.xpath('//a/@href')))
        self.assertNotIn('?folder=1.234', response.content.decode())
        response = self.client.get(reverse('sedo:mail_new'), {'source': message.pk, 'mode': 'reply'})
        self.assertEqual(html.fromstring(response.content).xpath('//input[@name="source"]/@value'), ['1234'])
        response = self.client.get(reverse('sedo:offer_create'), {'source_mail': message.pk})
        self.assertEqual(html.fromstring(response.content).xpath('//input[@name="source_mail"]/@value'), ['1234'])

    def test_inbox_is_paginated_searchable_and_omits_raw_mime_from_list_query(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from sedo.models import AppSetting
        # Measure the list, not the intentionally separate weekly backup task.
        AppSetting.set('auto_weekly_backup_enabled', '0')
        self.sync()
        folder = MailFolder.objects.get(role='inbox')
        for uid in range(100, 135):
            msg = mail_mime.store_message(self.account, email_bytes(f'Zusatznachricht {uid}'))
            MailLocation.objects.create(folder=folder, message=msg, uid=uid, uidvalidity=9, flagged=uid == 120)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse('sedo:mail_inbox'))
        self.assertEqual(len(response.context['page_obj']), 30)
        list_queries = [entry['sql'] for entry in queries if 'LIMIT 30' in entry['sql']]
        self.assertEqual(len(list_queries), 1)
        self.assertNotIn('"raw"', list_queries[0])
        self.assertLess(len(queries), 12)
        response = self.client.get(reverse('sedo:mail_inbox'), {'q': 'Zusatznachricht 120', 'view': 'flagged'})
        self.assertEqual(len(response.context['page_obj']), 1)

    def test_email_offer_save_and_pdf_reply_keep_source_without_direct_send(self):
        from .test_wizard import _angebot_post
        source = self.sync().message
        customer = Customer.objects.create(name='TEST Mail-Kunde', email=source.sender_email, street='Testweg 1', postcode='12345', city='Teststadt')
        response = self.client.post(reverse('sedo:offer_create'), _angebot_post(customer=customer.pk, source_mail=source.pk))
        self.assertEqual(response.status_code, 302)
        offer = Offer.objects.get()
        self.assertEqual(offer.source_mail_id, source.pk)
        self.assertEqual(MailDraft.objects.count(), 0)
        response = self.client.post(reverse('sedo:mail_offer', args=[offer.pk]))
        self.assertEqual(response.status_code, 302)
        draft = MailDraft.objects.get()
        self.assertEqual(draft.source, source)
        self.assertEqual(draft.offer, offer)
        self.assertEqual(draft.to, customer.email)
        self.assertEqual(draft.state, 'draft')
        self.assertTrue(bytes(draft.attachments.get().data).startswith(b'%PDF'))
        self.client.post(reverse('sedo:mail_offer', args=[offer.pk]))
        self.assertEqual(MailDraft.objects.count(), 1)

    def test_source_offer_post_error_retains_email_reference(self):
        from .test_wizard import _angebot_post
        source = self.sync().message
        response = self.client.post(reverse('sedo:offer_create'), _angebot_post(source_mail=source.pk, issue_date=''))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'name="source_mail" value="{source.pk}"')
        self.assertEqual(Offer.objects.count(), 0)

    def test_mail_pages_render_without_network_on_get(self):
        location = self.sync()
        draft = self.draft()
        before = len(self.server.calls)
        for name, args in [('mail_inbox', []), ('mail_message', [location.message_id]), ('mail_settings', []),
                           ('mail_account', [self.account.pk]), ('mail_new', []), ('mail_compose', [draft.pk]),
                           ('mail_review', [draft.pk]), ('mail_drafts', []), ('mail_task', [location.message_id])]:
            with self.subTest(name=name):
                response = self.client.get(reverse('sedo:' + name, args=args))
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, 'mail-test-secret-never-log')
        self.assertEqual(before, len(self.server.calls))

    def test_drafts_are_private_to_author(self):
        draft = self.draft(subject='Privater Entwurf')
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(reverse('sedo:mail_drafts')), 'Privater Entwurf')
        for name in ('mail_compose', 'mail_review'):
            self.assertEqual(self.client.get(reverse('sedo:' + name, args=[draft.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('sedo:mail_draft_action', args=[draft.pk]), {'action': 'delete'}).status_code, 404)

    def test_draft_save_deduplicates_repeated_create_and_blocks_stale_edits(self):
        key = uuid.uuid4()
        data = {'account': self.account.pk, 'to': 'kunde@example.org', 'subject': 'Entwurf', 'body': 'Original', 'revision': 0, 'creation_key': str(key), 'action': 'save', 'mode': 'new'}
        self.assertEqual(self.client.post(reverse('sedo:mail_new'), data).status_code, 302)
        self.client.post(reverse('sedo:mail_new'), data)
        self.assertEqual(MailDraft.objects.count(), 1)
        draft = MailDraft.objects.get()
        url = reverse('sedo:mail_compose', args=[draft.pk])
        data['body'] = 'Neue Fassung'
        self.assertEqual(self.client.post(url, data).status_code, 302)
        data['body'] = 'Veraltete Fassung'
        response = self.client.post(url, data)
        self.assertContains(response, 'anderen Fenster geändert')
        draft.refresh_from_db()
        self.assertEqual(draft.body, 'Neue Fassung')

    def test_offer_get_prefills_customer_without_creating_offer_or_sending_mail(self):
        source = self.sync().message
        customer = Customer.objects.create(name='Kundenfirma', email=source.sender_email,
            street='Firmenweg 12', postcode='39104', city='Magdeburg', phone='0391 123456')
        response = self.client.get(reverse('sedo:offer_create'), {'source_mail': source.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['form'].initial['customer'], customer)
        for field in ('name', 'email', 'street', 'postcode', 'city', 'phone'):
            self.assertEqual(response.context['form'][field].value(), getattr(customer, field))
        self.assertContains(response, 'name="source_mail"')
        self.assertEqual(Offer.objects.count(), 0)
        self.assertEqual(MailDraft.objects.count(), 0)

    def test_task_preserves_source_and_customer_chronology_links_mail(self):
        source = self.sync().message
        customer = Customer.objects.create(name='Chronik Kunde')
        source.customer = customer
        source.save(update_fields=['customer'])
        response = self.client.post(reverse('sedo:mail_task', args=[source.pk]), {'title': 'Auftrag prüfen', 'due_date': timezone.localdate(), 'priority': 'normal', 'note': 'Bitte sichten'})
        self.assertEqual(response.status_code, 302)
        task = WorkTask.objects.get()
        self.assertEqual(task.source_mail, source)
        self.assertEqual(task.customer, customer)
        with self.assertRaises(ProtectedError):
            source.delete()
        page = self.client.get(reverse('sedo:customer_detail', args=[customer.pk]), {'section': 'timeline', 'kind': 'email'})
        self.assertContains(page, reverse('sedo:mail_message', args=[source.pk]))

    def test_message_action_cannot_operate_on_unrelated_location(self):
        location = self.sync()
        other = mail_mime.store_message(self.account, email_bytes('Andere Mail'))
        response = self.client.post(reverse('sedo:mail_message_action', args=[other.pk]), {'action': 'seen', 'location': location.pk})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(any(call[0] == 'STORE' for call in self.server.calls))

    def test_confirm_unknown_requires_explicit_checked_box_and_no_send(self):
        draft = self.draft(state='unknown')
        url = reverse('sedo:mail_draft_action', args=[draft.pk])
        self.client.post(url, {'action': 'confirm_not_sent'})
        draft.refresh_from_db()
        self.assertEqual(draft.state, 'unknown')
        self.client.post(url, {'action': 'confirm_not_sent', 'confirmed': '1'})
        draft.refresh_from_db()
        self.assertEqual(draft.state, 'draft')
