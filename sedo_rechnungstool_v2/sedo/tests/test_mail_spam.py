"""Spam controls exercised through real sync, review routes and source workflows."""
from datetime import timedelta
from email import policy
from email.message import EmailMessage
from unittest.mock import patch

from django.test import Client
from django.urls import reverse
from django.utils import timezone
from lxml import html

from sedo.models import (Customer, DashboardPreference, IncomingInvoice, MailAccount, MailFolder,
                        MailLocation, MailMessage, MailSpamMove, MailSpamRule, Offer, WorkTask)
from sedo.services import mail_imap, mail_spam
from sedo.services.incoming_invoices import collect_account, collect_message
from sedo.services.mail_mime import store_message
from sedo.services.mail_secrets import MailError
from .test_mail_client import MailFixture


def message_bytes(sender='werbung@example.org', subject='Information', body='Guten Tag.', html_body=None, pdf=False, headers=None):
    msg = EmailMessage(policy=policy.SMTP)
    msg['From'], msg['To'], msg['Subject'] = sender, 'info@sedo-magdeburg.de', subject
    for name, value in (headers or {}).items():
        msg[name] = value
    msg.set_content(body)
    if html_body:
        msg.add_alternative(html_body, subtype='html')
    if pdf:
        msg.add_attachment(b'%PDF-test-original', maintype='application', subtype='pdf', filename='Rechnung.pdf')
    return msg.as_bytes()


class SpamTests(MailFixture):
    def setUp(self):
        super().setUp()
        self.server.folders['Spam'], self.server.validity['Spam'] = {}, 12
        self.server.list = lambda: ('OK', [b'(\\HasNoChildren) "/" "INBOX"', b'(\\Sent) "/" "Sent"',
                                          b'(\\Trash) "/" "Trash"', b'(\\Junk) "/" "Spam"'])
        self.spam_queue = patch('sedo.mail_spam_views.request_sync', return_value=True)
        self.spam_queue.start()
        self.addCleanup(self.spam_queue.stop)
        self.batch_url = reverse('sedo:mail_spam_batch')
        self.settings_url = reverse('sedo:mail_spam_settings', args=[self.account.pk])

    def rule(self, value='werbung@example.org', kind='address', action='block', account=None):
        return MailSpamRule.objects.create(account=account or self.account, value=value, kind=kind, action=action)

    def load(self, raw=None, uid=20, folder='INBOX'):
        self.server.folders[folder][uid] = [raw or message_bytes(), set()]
        mail_imap.sync_account(self.account.pk)
        return MailMessage.objects.get(digest=mail_spam.hashlib.sha256(self.server.folders[folder][uid][0]).hexdigest())

    def ready_auto(self):
        self.account.spam_mode, self.account.spam_auto_since = 'auto', timezone.now()
        self.account.save()
        mail_imap.sync_account(self.account.pk)

    def submit(self, action, locations):
        response = self.client.post(self.batch_url, {'action': action, 'location': locations})
        self.assertEqual(response.status_code, 200)
        return self.client.post(self.batch_url, {'ticket': response.context['ticket'], 'confirm': 'yes'})

    def test_mark_default_never_moves_or_deletes(self):
        self.rule()
        message = self.load()
        self.assertEqual(message.spam_state, 'spam')
        self.assertIn(20, self.server.folders['INBOX'])
        self.assertFalse(MailSpamMove.objects.exists())
        self.assertFalse(any(row[0] in ('MOVE', 'EXPUNGE') for row in self.server.calls))

    def test_auto_only_moves_new_uid_after_baseline_by_exact_block_rule(self):
        self.rule()
        self.ready_auto()
        raw = message_bytes()
        self.server.folders['INBOX'][20] = [raw, set()]
        mail_imap.sync_account(self.account.pk)
        self.assertNotIn(20, self.server.folders['INBOX'])
        self.assertEqual(len(self.server.folders['Spam']), 1)
        self.assertEqual(MailSpamMove.objects.get().state, 'done')
        mail_imap.sync_account(self.account.pk)
        self.assertEqual(MailLocation.objects.filter(folder__role='junk').count(), 1)

    def test_backfilled_old_messages_and_new_rules_never_mass_move(self):
        self.rule()
        self.server.folders['INBOX'][200] = [message_bytes(), set()]
        with patch('sedo.services.mail_imap.SYNC_FOLDER_BATCH', 0):
            self.ready_auto()
        mail_imap.sync_account(self.account.pk)
        self.assertIn(200, self.server.folders['INBOX'])
        self.assertFalse(MailSpamMove.objects.exists())
        self.account.spam_revision += 1
        self.account.save()
        self.server.folders['INBOX'][201] = [message_bytes(subject='Vor Regeländerung'), set()]
        mail_imap.sync_account(self.account.pk)
        self.assertIn(201, self.server.folders['INBOX'])

    def test_uidvalidity_reset_establishes_new_baseline(self):
        self.rule()
        self.ready_auto()
        self.server.validity['INBOX'] += 1
        self.server.folders['INBOX'][200] = [message_bytes(), set()]
        mail_imap.sync_account(self.account.pk)
        self.assertIn(200, self.server.folders['INBOX'])
        self.assertFalse(MailSpamMove.objects.exists())

    def test_domain_rule_is_exact_and_address_rule_wins(self):
        self.rule('example.org', 'domain')
        self.rule('kunde@example.org', action='allow')
        for sender, expected in [('kunde@example.org', 'clear'), ('other@example.org', 'spam'),
                                 ('other@sub.example.org', 'clear'), ('other@example.org.evil.test', 'clear')]:
            message = store_message(self.account, message_bytes(sender=sender))
            self.assertEqual(mail_spam.evaluate(message)[0], expected, sender)

    def test_rules_are_scoped_to_account_and_no_implicit_rule_from_correction(self):
        other = MailAccount.objects.create(email='rechnung@sedo-magdeburg.de', username='rechnung')
        self.rule(account=other)
        message = self.load()
        self.assertEqual(message.spam_state, 'clear')
        mail_spam.correct(message.locations.get().pk, 'spam', self.user)
        self.assertEqual(self.account.spam_rules.count(), 0)
        self.assertEqual(other.spam_rules.count(), 1)

    def test_manual_ham_survives_rule_revision_and_repeated_sync(self):
        self.rule()
        message = self.load()
        mail_spam.correct(message.locations.get().pk, 'ham', self.user)
        self.account.spam_revision += 1
        self.account.spam_mode, self.account.spam_auto_since = 'auto', timezone.now() - timedelta(days=1)
        self.account.save()
        for _ in range(2):
            mail_imap.sync_account(self.account.pk)
        message.refresh_from_db()
        self.assertEqual((message.spam_manual, message.spam_state), ('ham', 'clear'))
        self.assertIn(20, self.server.folders['INBOX'])

    def test_spam_to_inbox_manual_correction_does_not_bounce(self):
        self.rule()
        self.ready_auto()
        message = self.load(folder='Spam')
        mail_spam.correct(message.locations.get().pk, 'ham', self.user)
        for _ in range(2):
            mail_imap.sync_account(self.account.pk)
        message.refresh_from_db()
        self.assertEqual(message.spam_state, 'clear')
        self.assertTrue(message.locations.filter(folder__role='inbox').exists())
        self.assertFalse(message.locations.filter(folder__role='junk').exists())

    def test_provider_folder_wins_over_allow_rule_without_silent_return(self):
        self.rule(action='allow')
        message = self.load(folder='Spam')
        self.assertEqual(message.spam_state, 'spam')
        self.assertTrue(mail_spam.blocked(message))
        self.assertEqual(message.spam_reason, 'Im Spamordner des Mailservers')
        self.assertEqual(len(self.server.folders['Spam']), 1)

    def test_forged_provider_headers_and_unknown_customer_are_not_spam_proof(self):
        message = self.load(message_bytes(subject='Rechnung und Angebot', headers={
            'X-Spam-Flag': 'YES', 'X-Spam-Score': '999', 'Authentication-Results': 'trusted.example; dmarc=fail'}))
        self.assertEqual(message.spam_state, 'clear')
        self.assertFalse(MailSpamMove.objects.exists())

    def test_two_suspicious_signals_only_mark_and_never_contact_link(self):
        self.ready_auto()
        with patch('requests.get') as requests_get, patch('urllib.request.urlopen') as urlopen:
            message = self.load(message_bytes(body='Bitte sofort anmelden.',
                html_body='<a href="https://evil.example/login">https://bank.example/login</a>'))
        self.assertEqual(message.spam_state, 'suspect')
        self.assertIn('Linktext', message.spam_reason)
        self.assertIn(20, self.server.folders['INBOX'])
        self.assertFalse(MailSpamMove.objects.exists())
        requests_get.assert_not_called()
        urlopen.assert_not_called()

    def test_one_weak_indicator_is_not_enough(self):
        message = self.load(message_bytes(body='Bitte sofort anmelden.'))
        self.assertEqual(message.spam_state, 'clear')

    def test_multiple_from_addresses_do_not_match_allow_rule(self):
        self.rule('kunde@example.org', action='allow')
        message = self.load(message_bytes(sender='kunde@example.org, angreifer@evil.test'))
        self.assertEqual(message.spam_state, 'suspect')

    def test_missing_and_corrupt_originals_are_review_cases(self):
        message = self.load()
        from sedo.services.mail_storage import cache_path
        cache_path(message.account_id, message.digest).write_bytes(b'changed')
        self.assertEqual(mail_spam.evaluate(message)[0], 'suspect')
        self.assertEqual(mail_spam.evaluate(message)[0], 'suspect')

    def test_manual_decision_and_business_original_survive_failed_move(self):
        message = self.load()
        customer = Customer.objects.create(name='Quelle')
        offer = Offer.objects.create(number='AG-SPAM-QUELLE', customer=customer, source_mail=message)
        self.server.capabilities = (b'IMAP4REV1',)
        with self.assertRaises(MailError):
            mail_spam.correct(message.locations.get().pk, 'spam', self.user)
        message.refresh_from_db()
        self.assertEqual(message.spam_manual, 'spam')
        self.assertTrue(bytes(message.raw))
        self.assertTrue(Offer.objects.filter(pk=offer.pk, source_mail=message).exists())
        self.assertIn(20, self.server.folders['INBOX'])

    def test_business_sources_are_never_automatically_moved(self):
        self.rule()
        self.ready_auto()
        raw = message_bytes()
        message = store_message(self.account, raw)
        message.customer = Customer.objects.create(name='Verknüpft')
        message.save()
        self.server.folders['INBOX'][20] = [raw, set()]
        mail_imap.sync_account(self.account.pk)
        self.assertIn(20, self.server.folders['INBOX'])
        self.assertFalse(MailSpamMove.objects.exists())

    def test_lost_move_ack_is_reconciled_without_second_move(self):
        self.rule()
        self.ready_auto()
        self.server.folders['INBOX'][20] = [message_bytes(), set()]
        original = self.server.uid
        def lose_ack(command, *args):
            result = original(command, *args)
            if command == 'MOVE':
                raise OSError('secret server text')
            return result
        with patch.object(self.server, 'uid', side_effect=lose_ack):
            with self.assertRaises(MailError) as caught:
                mail_imap.sync_account(self.account.pk)
        self.assertNotIn('secret', str(caught.exception))
        self.assertEqual(MailSpamMove.objects.get().state, 'unknown')
        mail_imap.sync_account(self.account.pk)
        self.assertEqual(MailSpamMove.objects.get().state, 'done')
        self.assertEqual(len([call for call in self.server.calls if call[0] == 'MOVE']), 1)

    def test_failure_before_move_is_not_automatically_retried_after_sync(self):
        self.rule()
        self.ready_auto()
        self.server.folders['INBOX'][20] = [message_bytes(), set()]
        original = self.server.uid
        def disconnect(command, *args):
            if command == 'MOVE':
                raise OSError('closed')
            return original(command, *args)
        with patch.object(self.server, 'uid', side_effect=disconnect):
            with self.assertRaises(MailError):
                mail_imap.sync_account(self.account.pk)
        mail_imap.sync_account(self.account.pk)
        self.assertEqual(MailSpamMove.objects.count(), 1)
        self.assertEqual(MailSpamMove.objects.get().state, 'failed')
        self.assertIn(20, self.server.folders['INBOX'])
        self.assertFalse(any(row[0] == 'MOVE' for row in self.server.calls))

    def test_unknown_move_cannot_be_repeated_manually_or_after_uidvalidity_reset(self):
        message = self.load()
        location = message.locations.get()
        junk = MailFolder.objects.get(role='junk')
        attempt = MailSpamMove.objects.create(message=message, source=location.folder, destination=junk,
            uid=location.uid, uidvalidity=location.uidvalidity)
        with self.assertRaises(MailError):
            mail_spam.correct(location.pk, 'spam', self.user)
        self.server.validity['INBOX'] += 1
        mail_imap.sync_account(self.account.pk)
        attempt.refresh_from_db()
        self.assertEqual(attempt.state, 'unknown')
        self.assertFalse(any(row[0] == 'MOVE' for row in self.server.calls))

    def test_missing_or_ambiguous_spam_folder_keeps_message(self):
        message = self.load()
        MailFolder.objects.create(account=self.account, remote_name='Junk2', name='Junk2', role='junk')
        with self.assertRaises(MailError):
            mail_spam.correct(message.locations.get().pk, 'spam', self.user)
        self.assertIn(20, self.server.folders['INBOX'])

    def test_stale_remote_uid_never_operated_on(self):
        message = self.load()
        del self.server.folders['INBOX'][20]
        with self.assertRaises(MailError):
            mail_spam.correct(message.locations.get().pk, 'spam', self.user)
        self.assertFalse(MailSpamMove.objects.exists())

    def test_spam_never_enters_invoice_intake_until_explicit_release(self):
        self.rule()
        message = self.load(message_bytes(pdf=True))
        self.assertFalse(IncomingInvoice.objects.exists())
        self.assertFalse(message.invoice_scan_done)
        with self.assertRaises(MailError):
            collect_message(message)
        mail_spam.correct(message.locations.get().pk, 'ham', self.user)
        self.assertEqual(collect_account(self.account.pk), 1)
        self.assertEqual(IncomingInvoice.objects.count(), 1)

    def test_suspect_or_server_spam_duplicate_blocks_inbox_intake(self):
        raw = message_bytes(pdf=True, body='Bitte sofort anmelden.',
            html_body='<a href="https://evil.example/">https://bank.example/</a>')
        message = self.load(raw)
        self.assertEqual(message.spam_state, 'suspect')
        self.assertFalse(IncomingInvoice.objects.exists())
        message.spam_state, message.spam_manual = 'clear', 'ham'
        message.save()
        MailLocation.objects.create(message=message, folder=MailFolder.objects.get(role='junk'), uid=45, uidvalidity=12)
        with self.assertRaises(MailError):
            collect_message(message)

    def test_new_tasks_blocked_but_archived_invoice_kept_after_spam_mark(self):
        message = self.load(message_bytes(pdf=True))
        invoice = IncomingInvoice.objects.get()
        raw = bytes(invoice.original)
        mail_spam.correct(message.locations.get().pk, 'spam', self.user)
        response = self.client.post(reverse('sedo:mail_task', args=[message.pk]), {
            'title': 'Aus Spam', 'due_date': '2026-09-21', 'priority': 'normal', 'note': ''})
        self.assertContains(response, 'Kein Spam')
        self.assertFalse(WorkTask.objects.filter(source_mail=message).exists())
        invoice.refresh_from_db()
        self.assertEqual(bytes(invoice.original), raw)

    def test_batch_preview_and_confirmation_are_required_and_do_not_create_rules(self):
        message = self.load()
        response = self.client.post(self.batch_url, {'action': 'spam', 'location': [message.locations.get().pk]})
        self.assertContains(response, '1 Nachricht(en)')
        self.assertFalse(MailSpamMove.objects.exists())
        ticket = response.context['ticket']
        self.client.post(self.batch_url, {'ticket': ticket})
        self.assertFalse(MailSpamMove.objects.exists())
        self.client.post(self.batch_url, {'ticket': ticket, 'confirm': 'yes'})
        self.assertEqual(MailSpamMove.objects.get().state, 'done')
        self.assertFalse(MailSpamRule.objects.exists())

    def test_preview_is_bound_to_user_selection_and_current_rules(self):
        message = self.load()
        response = self.client.post(self.batch_url, {'action': 'spam', 'location': [message.locations.get().pk]})
        ticket = response.context['ticket']
        self.client.force_login(self.other)
        self.client.post(self.batch_url, {'ticket': ticket, 'confirm': 'yes'})
        self.client.force_login(self.user)
        self.account.spam_revision += 1
        self.account.save()
        self.client.post(self.batch_url, {'ticket': ticket, 'confirm': 'yes'})
        self.client.post(self.batch_url, {'ticket': ticket + 'changed', 'confirm': 'yes'})
        self.assertFalse(MailSpamMove.objects.exists())

    def test_batch_limit_duplicates_and_bad_ids_are_rejected(self):
        location = self.load().locations.get().pk
        for values in ([], ['no'], [location, location], list(range(1, 27)), [99999]):
            self.assertEqual(self.client.post(self.batch_url, {'action': 'spam', 'location': values}).status_code, 302)
        self.assertFalse(MailSpamMove.objects.exists())

    def test_old_mail_review_only_marks_without_moving(self):
        self.rule()
        message = self.load()
        MailMessage.objects.filter(pk=message.pk).update(created_at=timezone.now() - timedelta(days=10), spam_state='clear')
        response = self.submit('check', [message.locations.get().pk])
        self.assertEqual(response.status_code, 302)
        message.refresh_from_db()
        self.assertEqual(message.spam_state, 'spam')
        self.assertFalse(MailSpamMove.objects.exists())

    def test_partial_batch_failure_reports_each_message_without_undoing_success(self):
        one = self.load()
        two = self.load(message_bytes(subject='zweite'), uid=21)
        original = mail_spam.correct
        def fail_second(pk, action, user):
            if pk == two.locations.get().pk:
                raise MailError('Postfach beschäftigt')
            return original(pk, action, user)
        with patch('sedo.mail_spam_views.correct', side_effect=fail_second):
            response = self.submit('spam', [one.locations.get().pk, two.locations.get().pk])
        self.assertContains(response, '1 Nachricht(en) vollständig')
        self.assertContains(response, 'Postfach beschäftigt')
        self.assertEqual(MailSpamMove.objects.count(), 1)

    def test_settings_staff_csrf_revision_and_auto_confirmation(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.settings_url).status_code, 403)
        self.assertEqual(self.client.post(self.settings_url, {}).status_code, 403)
        self.client.force_login(self.user)
        self.client.post(self.settings_url, {'operation': 'settings', 'revision': 1, 'mode': 'auto'})
        self.account.refresh_from_db()
        self.assertEqual(self.account.spam_mode, 'mark')
        self.client.post(self.settings_url, {'operation': 'settings', 'revision': 1, 'mode': 'auto', 'confirm_auto': 'on'})
        self.account.refresh_from_db()
        self.assertEqual(self.account.spam_mode, 'auto')
        self.assertIsNotNone(self.account.spam_auto_since)
        self.client.post(self.settings_url, {'operation': 'settings', 'revision': 1, 'mode': 'off'})
        self.account.refresh_from_db()
        self.assertEqual(self.account.spam_mode, 'auto')
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.user)
        self.assertEqual(csrf.post(self.settings_url, {}).status_code, 403)
        self.assertEqual(csrf.post(self.batch_url, {}).status_code, 403)
        self.assertEqual(Client().post(self.batch_url, {}).status_code, 302)
        self.assertEqual(self.client.get(self.batch_url).status_code, 405)

    def test_rule_normalization_broad_domain_confirmation_and_conflict(self):
        data = {'operation': 'rule', 'revision': 1, 'kind': 'domain', 'value': ' EXAMPLE.ORG ', 'action': 'block'}
        self.client.post(self.settings_url, data)
        self.assertFalse(MailSpamRule.objects.exists())
        self.client.post(self.settings_url, {**data, 'confirm_domain': 'on'})
        self.assertEqual(MailSpamRule.objects.get().value, 'example.org')
        self.client.post(self.settings_url, {**data, 'revision': 2, 'action': 'allow', 'confirm_domain': 'on'})
        self.assertEqual(MailSpamRule.objects.get().action, 'block')
        for bad in ('*.example.org', 'https://example.org', 'x@y.example', 'example.org\nmalicious'):
            with self.assertRaises(MailError):
                mail_spam.normalize_rule('domain', bad)

    def test_off_mode_disables_local_rules_but_not_manual_or_server_spam(self):
        self.rule()
        self.account.spam_mode = 'off'
        self.account.save()
        message = self.load()
        self.assertEqual(message.spam_state, 'clear')
        mail_spam.correct(message.locations.get().pk, 'spam', self.user)
        mail_imap.sync_account(self.account.pk)
        message.refresh_from_db()
        self.assertEqual(message.spam_state, 'spam')

    def test_dashboard_optional_spam_tile_and_count_excludes_confirmed_spam(self):
        self.rule()
        self.load()
        DashboardPreference.objects.create(user=self.user, widgets=['mail', 'spam'])
        response = self.client.get(reverse('sedo:dashboard'))
        self.assertEqual(response.context['unread_mail_count'], 1)
        self.assertEqual(response.context['unread_spam_count'], 1)
        self.assertContains(response, 'data-widget="spam"')
        response = self.client.get(reverse('sedo:mail_inbox') + '?section=spam')
        self.assertEqual(response.context['page_obj'].paginator.count, 1)

    def test_reader_escapes_subject_and_uses_gear_and_help_icons(self):
        message = self.load(message_bytes(subject='<script>bad()</script>'))
        response = self.client.get(reverse('sedo:mail_message', args=[message.pk]))
        self.assertNotContains(response, '<script>bad()')
        root = html.fromstring(response.content)
        self.assertTrue(root.xpath('//a[contains(@class,"settings-icon") and contains(@href,"spam/einstellungen")]'))
        self.assertTrue(root.xpath('//input[@form="spam-selection"]'))

    def test_leased_account_prevents_correction_and_settings_mutation(self):
        message = self.load()
        with mail_imap.account_lease(self.account.pk):
            with self.assertRaises(mail_imap.MailBusy):
                mail_spam.correct(message.locations.get().pk, 'spam', self.user)
            self.client.post(self.settings_url, {'operation': 'settings', 'revision': 1, 'mode': 'off'})
        message.refresh_from_db()
        self.account.refresh_from_db()
        self.assertEqual(message.spam_manual, '')
        self.assertEqual(self.account.spam_mode, 'mark')

    def test_unresolved_move_requires_explicit_confirmation_and_never_sends_move(self):
        message = self.load()
        location = message.locations.get()
        attempt = MailSpamMove.objects.create(message=message, source=location.folder,
            destination=MailFolder.objects.get(role='junk'), uid=location.uid, uidvalidity=location.uidvalidity)
        url = reverse('sedo:mail_spam_resolve', args=[attempt.pk])
        self.client.post(url, {'state': 'failed'})
        attempt.refresh_from_db()
        self.assertEqual(attempt.state, 'unknown')
        self.client.post(url, {'state': 'failed', 'confirm': 'yes'})
        attempt.refresh_from_db()
        self.assertEqual(attempt.state, 'failed')
        self.assertFalse(any(row[0] == 'MOVE' for row in self.server.calls))

    def test_server_junk_copy_is_excluded_from_normal_mail_count_and_unread_view(self):
        message = self.load()
        MailLocation.objects.create(message=message, folder=MailFolder.objects.get(role='junk'), uid=80, uidvalidity=12)
        response = self.client.get(reverse('sedo:dashboard'))
        self.assertEqual(response.context['unread_mail_count'], 1)
        response = self.client.get(reverse('sedo:mail_inbox') + '?view=unread')
        self.assertEqual(response.context['page_obj'].paginator.count, 1)

    def test_removed_mail_from_spam_by_other_client_is_reclassified_on_sync(self):
        message = self.load(folder='Spam')
        self.server.folders['INBOX'][30] = self.server.folders['Spam'].pop(20)
        mail_imap.sync_account(self.account.pk)
        message.refresh_from_db()
        self.assertEqual(message.spam_state, 'clear')

    def test_empty_or_foreign_rule_delete_cannot_change_settings(self):
        other = MailAccount.objects.create(email='other@example.org', username='other')
        rule = self.rule(account=other)
        for value in ('bad', '', rule.pk):
            response = self.client.post(self.settings_url, {'operation': 'delete', 'revision': 1, 'rule': value})
            self.assertEqual(response.status_code, 404)
        self.assertTrue(MailSpamRule.objects.filter(pk=rule.pk).exists())

    def test_backup_retains_filter_rules_decisions_and_uncertain_attempts(self):
        import sqlite3
        from contextlib import closing
        from pathlib import Path
        from sedo.backup_utils import _sanitized_db_copy
        path = Path(self.temp.name) / 'spam-backup.sqlite3'
        with closing(sqlite3.connect(path)) as db:
            db.executescript('CREATE TABLE sedo_mailaccount (spam_mode TEXT, spam_revision INTEGER, password_encrypted TEXT, enabled INTEGER, lease_token TEXT, lease_until TEXT);'
                'CREATE TABLE sedo_mailspamrule (value TEXT, action TEXT);'
                'CREATE TABLE sedo_mailmessage (spam_manual TEXT, spam_state TEXT);'
                'CREATE TABLE sedo_mailspammove (state TEXT, uid INTEGER, uidvalidity INTEGER);')
            db.execute("INSERT INTO sedo_mailaccount VALUES ('mark', 5, 'secret', 1, 'lease', 'later')")
            db.execute("INSERT INTO sedo_mailspamrule VALUES ('blocked@example.org', 'block')")
            db.execute("INSERT INTO sedo_mailmessage VALUES ('ham', 'clear')")
            db.execute("INSERT INTO sedo_mailspammove VALUES ('unknown', 20, 9)")
            db.commit()
        clean = _sanitized_db_copy(path)
        self.addCleanup(clean.unlink)
        with closing(sqlite3.connect(clean)) as db:
            self.assertEqual(db.execute('SELECT * FROM sedo_mailaccount').fetchone(), ('mark', 5, '', 0, '', None))
            self.assertEqual(db.execute('SELECT * FROM sedo_mailspamrule').fetchone(), ('blocked@example.org', 'block'))
            self.assertEqual(db.execute('SELECT * FROM sedo_mailmessage').fetchone(), ('ham', 'clear'))
            self.assertEqual(db.execute('SELECT * FROM sedo_mailspammove').fetchone(), ('unknown', 20, 9))

    def test_unknown_move_holds_intake_despite_later_ham_correction(self):
        message = self.load()
        location = message.locations.get()
        MailSpamMove.objects.create(message=message, source=location.folder,
            destination=MailFolder.objects.get(role='junk'), uid=location.uid, uidvalidity=location.uidvalidity)
        mail_spam.correct(location.pk, 'ham', self.user)
        with self.assertRaises(MailError):
            mail_spam.require_business_clear(message)

    def test_held_spam_does_not_starve_later_legitimate_invoice(self):
        self.sync()
        inbox = MailFolder.objects.get(role='inbox')
        for index in range(30):
            message = store_message(self.account, message_bytes(subject=f'Spam {index}', pdf=True))
            message.spam_manual, message.spam_state = 'spam', 'spam'
            message.save()
            MailLocation.objects.create(message=message, folder=inbox, uid=index + 100, uidvalidity=9)
        good = store_message(self.account, message_bytes(sender='supplier@example.org', pdf=True))
        MailLocation.objects.create(message=good, folder=inbox, uid=200, uidvalidity=9)
        self.assertEqual(collect_account(self.account.pk), 1)
        self.assertEqual(IncomingInvoice.objects.get().sources.get().message_id, good.pk)
