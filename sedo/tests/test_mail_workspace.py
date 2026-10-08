"""Mailbox actions retain context; document badges reflect durable intake records."""
from datetime import timedelta
from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone
from lxml import html

from sedo.models import IncomingInvoiceSource, MailFolder, MailLocation, MailMessage, MailSpamMove
from sedo.services import mail_imap, mail_spam
from sedo.services.incoming_invoices import collect_message
from sedo.services.mail_secrets import MailError
from .test_mail_client import MailFixture, email_bytes
from .test_mail_spam import message_bytes


class MailWorkspaceTests(MailFixture):
    def setUp(self):
        super().setUp()
        self.server.folders['Spam'], self.server.validity['Spam'] = {}, 12
        self.server.list = lambda: ('OK', [b'(\\HasNoChildren) "/" "INBOX"', b'(\\Sent) "/" "Sent"',
            b'(\\Trash) "/" "Trash"', b'(\\Junk) "/" "Spam"'])
        for target in ('sedo.mail_views.request_sync', 'sedo.mail_spam_views.request_sync'):
            mocking = patch(target, return_value=True)
            mocking.start()
            self.addCleanup(mocking.stop)
        self.url = reverse('sedo:mail_inbox')
        self.batch_url = reverse('sedo:mail_spam_batch')

    def test_reader_uses_selected_copy_only_and_retains_page_and_filters(self):
        location = self.sync()
        trash = MailFolder.objects.get(role='trash')
        copy = MailLocation.objects.create(message=location.message, folder=trash, uid=88, uidvalidity=11)
        query = f'section=trash&account={self.account.pk}&page=2&q=Anfrage'
        response = self.client.get(reverse('sedo:mail_message', args=[location.message_id]) + '?' + query + f'&location={copy.pk}')
        self.assertEqual(response.context['current_location'].pk, copy.pk)
        self.assertEqual([item.pk for item in response.context['locations']], [copy.pk])
        self.assertContains(response, 'Endgültig löschen')
        self.assertEqual(response.context['mailbox_url'], self.url + '?' + response.context['return_query'])
        self.assertIn('page=2', response.context['return_query'])
        self.assertIn('q=Anfrage', response.context['return_query'])
        stale = self.client.get(reverse('sedo:mail_message', args=[location.message_id]), {'location':999999})
        self.assertIsNone(stale.context['current_location'])
        doc = html.fromstring(stale.content)
        self.assertFalse(doc.xpath('//article[contains(@class,"mail-reader")]//button[@name="action" and @value="trash"]'))

    def test_move_returns_to_selected_folder_and_search(self):
        location = self.sync()
        query = f'account={self.account.pk}&section=spam&q=Anfrage'
        response = self.client.post(reverse('sedo:mail_message_action', args=[location.message_id]),
            {'action':'trash', 'location':location.pk, 'return_query':query})
        self.assertIn('section=spam', response.url)
        self.assertIn('q=Anfrage', response.url)
        self.assertFalse(MailLocation.objects.filter(pk=location.pk).exists())
        self.assertEqual(MailSpamMove.objects.get().state, 'done')

    def test_redirect_accepts_mail_filters_only(self):
        location = self.sync()
        response = self.client.post(reverse('sedo:mail_message_action', args=[location.message_id]),
            {'action':'seen', 'location':location.pk, 'return_query':'next=https://evil.example&section=trash&account=-1&page=evil'})
        self.assertTrue(response.url.startswith(reverse('sedo:mail_message', args=[location.message_id])))
        self.assertNotIn('evil', response.url)
        self.assertNotIn('account=', response.url)
        self.assertIn('section=trash', response.url)

    def test_copyuid_places_confirmed_move_in_destination_immediately(self):
        location = self.sync()
        trash = MailFolder.objects.get(role='trash')
        before = self.server.response
        with patch.object(self.server, 'response', side_effect=lambda key: ('COPYUID',[b'11 11 78']) if key=='COPYUID' else before(key)):
            mail_imap.move_message(location, trash)
        moved = MailLocation.objects.get(folder=trash, uid=78)
        self.assertEqual(moved.message_id, location.message_id)
        self.assertEqual(moved.uidvalidity, 11)
        self.assertEqual(self.server.folders['Trash'][78][0], email_bytes())

    def test_bad_copyuid_never_invents_or_overwrites_destination(self):
        location = self.sync()
        trash = MailFolder.objects.get(role='trash')
        for value in (b'11 99 78', b'13 11 78', b'11 11:12 78:79', b'11 11 999999999999999999999999', b'bad'):
            with patch.object(self.server, 'response', return_value=('COPYUID',[value])):
                mail_imap.cache_move_target(self.server, location, trash)
            self.assertFalse(trash.locations.exists())
        from sedo.services.mail_mime import store_message
        other = store_message(self.account, email_bytes('Andere Nachricht'))
        occupied = MailLocation.objects.create(folder=trash, message=other, uid=78, uidvalidity=11)
        with patch.object(self.server, 'response', return_value=('COPYUID', [b'11 11 78'])):
            mail_imap.cache_move_target(self.server, location, trash)
        occupied.refresh_from_db()
        self.assertEqual(occupied.message_id, other.pk)

    def test_uncertain_trash_move_is_not_sent_twice_and_sync_reconciles(self):
        location = self.sync()
        trash = MailFolder.objects.get(role='trash')
        original = self.server.uid
        def lost_ack(command, *args):
            result = original(command, *args)
            if command == 'MOVE':
                raise OSError('lost reply')
            return result
        with patch.object(self.server, 'uid', side_effect=lost_ack):
            with self.assertRaises(MailError):
                mail_imap.move_message(location, trash)
        self.assertEqual(MailSpamMove.objects.get().state, 'unknown')
        with self.assertRaises(MailError):
            mail_imap.move_message(location, trash)
        self.assertEqual(sum(call[0]=='MOVE' for call in self.server.calls), 1)
        mail_imap.sync_account(self.account.pk)
        self.assertEqual(MailSpamMove.objects.get().state, 'done')
        self.assertTrue(MailLocation.objects.filter(folder=trash,message_id=location.message_id).exists())

    def test_background_yields_without_recording_a_sync_error(self):
        with patch.dict(mail_imap._foreground_waiters, {self.account.pk:1}):
            mail_imap.sync_account(self.account.pk)
        self.account.refresh_from_db()
        self.assertEqual(self.account.last_error, '')
        self.assertIsNone(self.account.lease_until)
        self.assertFalse(any(call[0] in ('MOVE','STORE','EXPUNGE') for call in self.server.calls))

    def test_foreground_waits_for_background_lease_and_never_retries_body(self):
        background = mail_imap.account_lease(self.account.pk)
        token = background.__enter__()
        released = False
        def handover(_):
            nonlocal released
            with self.assertRaises(mail_imap.MailSyncYield):
                mail_imap.renew(self.account, token)
            background.__exit__(None,None,None)
            released = True
        try:
            with patch('sedo.services.mail_imap.time.sleep', side_effect=handover) as wait:
                with self.assertRaisesMessage(MailError, 'body failed'):
                    with mail_imap.foreground_lease(self.account.pk):
                        raise MailError('body failed')
                self.assertEqual(wait.call_count, 1)
        finally:
            if not released:
                background.__exit__(None,None,None)
        self.assertNotIn(self.account.pk, mail_imap._foreground_waiters)
        self.account.refresh_from_db()
        self.assertIsNone(self.account.lease_until)

    def test_spam_confirmation_survives_harmless_background_reassessment(self):
        location = self.sync()
        response = self.client.post(self.batch_url, {'action':'spam', 'location':[location.pk], 'return_query':'view=unread'})
        ticket = response.context['ticket']
        mail_spam.assess(location.message)
        response = self.client.post(self.batch_url, {'ticket':ticket, 'confirm':'yes'})
        self.assertEqual(response.url, self.url+'?view=unread')
        self.assertEqual(MailSpamMove.objects.get().state, 'done')

    def test_changed_spam_decision_invalidates_confirmation(self):
        location = self.sync()
        response = self.client.post(self.batch_url, {'action':'spam', 'location':[location.pk]})
        MailMessage.objects.filter(pk=location.message_id).update(spam_manual='ham')
        self.client.post(self.batch_url, {'ticket':response.context['ticket'], 'confirm':'yes'})
        self.assertFalse(MailSpamMove.objects.exists())

    def test_bulk_trash_requires_confirmed_ticket_and_preserves_context(self):
        location = self.sync()
        query = f'account={self.account.pk}&view=unread'
        response = self.client.post(self.batch_url, {'action':'trash', 'location':[location.pk], 'return_query':query})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(MailSpamMove.objects.exists())
        ticket = response.context['ticket']
        self.client.post(self.batch_url, {'ticket':ticket})
        self.assertFalse(MailSpamMove.objects.exists())
        response = self.client.post(self.batch_url, {'ticket':ticket,'confirm':'yes'})
        self.assertEqual(response.url, self.url+'?'+query)
        self.assertEqual(MailSpamMove.objects.get().destination.role, 'trash')

    def test_document_badges_and_filters_use_actual_sources_not_scan_flag(self):
        self.server.folders['INBOX'][11] = [message_bytes(subject='Rechnung Testlieferant', pdf=True),set()]
        location = self.sync()
        message = location.message
        collect_message(message)
        response = self.client.get(self.url, {'view':'pending'})
        self.assertEqual(response.context['page_obj'].paginator.count, 1)
        self.assertContains(response, '1 Beleg übernommen · 1 zu prüfen')
        source = IncomingInvoiceSource.objects.get(message=message)
        source.invoice.state='approved'
        source.invoice.save(update_fields=['state'])
        response = self.client.get(self.url, {'view':'processed'})
        self.assertContains(response, 'Belege abgearbeitet')
        self.assertEqual(response.context['page_obj'].paginator.count, 1)
        self.assertEqual(self.client.get(self.url, {'view':'pending'}).context['page_obj'].paginator.count, 0)
        self.assertEqual(self.client.get(self.url, {'view':'unimported'}).context['page_obj'].paginator.count, 0)
        MailMessage.objects.filter(pk=message.pk).update(invoice_scan_error='Übernahme fehlgeschlagen')
        self.assertContains(self.client.get(self.url), 'Belegübernahme fehlgeschlagen')
        self.assertNotContains(self.client.get(self.url), '✓ Belege abgearbeitet</span>')

    def test_plain_mail_is_not_marked_done_just_because_scanned(self):
        location = self.sync()
        MailMessage.objects.filter(pk=location.message_id).update(invoice_scan_done=True)
        response = self.client.get(self.url, {'view':'unimported'})
        self.assertEqual(response.context['page_obj'].paginator.count, 1)
        self.assertNotContains(response, 'is-complete')

    def test_virtual_trash_and_sent_folders_keep_separate_messages(self):
        location = self.sync()
        self.server.folders['Sent'][18] = [email_bytes('Gesendete Testmail'),{b'\\Seen'}]
        self.server.folders['Trash'][78] = [email_bytes('Weggelegte Testmail'),set()]
        mail_imap.sync_account(self.account.pk)
        for section, subject in [('trash','Weggelegte Testmail'),('sent','Gesendete Testmail')]:
            response = self.client.get(self.url, {'section':section})
            self.assertContains(response, subject)
            self.assertNotContains(response, location.message.subject)

    def test_stale_action_is_a_visible_error_without_touching_another_copy(self):
        location = self.sync()
        url = reverse('sedo:mail_message_action', args=[location.message_id])
        pk = location.pk
        location.delete()
        response = self.client.post(url, {'action':'trash','location':pk,'return_query':'section=sent'}, follow=True)
        self.assertContains(response, 'inzwischen verschoben oder gelöscht')
        self.assertFalse(any(call[0]=='MOVE' for call in self.server.calls))

    def test_manual_document_intake_returns_to_same_mail_context(self):
        location = self.sync()
        response = self.client.post(reverse('sedo:incoming_collect', args=[location.message_id]),
            {'return_query':'section=archive&page=3','location':location.pk})
        self.assertTrue(response.url.startswith(reverse('sedo:mail_message', args=[location.message_id])))
        self.assertIn('section=archive', response.url)
        self.assertIn('page=3', response.url)
        self.assertIn('location='+str(location.pk), response.url)

    def test_intake_yields_before_document_work_and_sync_timestamp_follows_intake(self):
        location = self.sync()
        before = self.account.__class__.objects.get(pk=self.account.pk).last_sync
        observed = []
        def intake(account_id, heartbeat):
            observed.append(self.account.__class__.objects.get(pk=account_id).last_sync)
            heartbeat()
        with patch('sedo.services.incoming_invoices.collect_account', side_effect=intake):
            mail_imap.sync_account(self.account.pk)
        self.assertEqual(observed, [before])
        self.account.refresh_from_db()
        self.assertGreater(self.account.last_sync, before)
        from sedo.services.incoming_invoices import collect_account
        MailMessage.objects.filter(pk=location.message_id).update(invoice_scan_done=False)
        with patch('sedo.services.incoming_invoices.collect_message') as collect:
            with self.assertRaises(mail_imap.MailSyncYield):
                collect_account(self.account.pk, heartbeat=lambda: (_ for _ in ()).throw(mail_imap.MailSyncYield()))
            collect.assert_not_called()
