"""Mail-Schutzregeln auf isolierten Quellkopien gezielt entfernen.

Nur echte fachliche Assertion-Fehler zählen als erkannte Gegenprobe.
"""
import gegenproben_entwurf as runner

runner.TEST = 'sedo.tests.'
runner.MUTATIONEN = [
    ('uidvalidity_guard', 'sedo/services/mail_imap.py',
     "if not location.folder.active or select(client, location.folder, readonly=False) != location.uidvalidity:",
     "if not location.folder.active or select(client, location.folder, readonly=False) < 0:",
     'MailProtocolTests.test_stale_uidvalidity_cannot_mark_or_move_other_message'),
    ('tls_hostname', 'sedo/services/smtp_transport.py',
     '    context.load_verify_locations(cafile=certifi.where())',
     '    context.load_verify_locations(cafile=certifi.where())\n    context.check_hostname = False',
     'MailProtocolTests.test_sync_is_idempotent_uses_peek_and_verified_tls'),
    ('send_idempotency', 'sedo/services/mail_send.py',
     "pk=draft.pk, author=author, state='draft', revision=revision",
     "pk=draft.pk, author=author",
     'MailSendTests.test_send_once_bcc_only_in_envelope_and_sent_copy_only_once'),
    ('unknown_send_guard', 'sedo/services/mail_send.py',
     "            state = 'unknown' if unknown else 'draft'",
     "            state = 'draft'",
     'MailSendTests.test_connection_loss_during_data_requires_manual_review_no_retry'),
    ('bcc_privacy', 'sedo/services/mail_send.py',
     "    message['Subject'] = draft.subject",
     "    message['Bcc'] = draft.bcc\n    message['Subject'] = draft.subject",
     'MailSendTests.test_send_once_bcc_only_in_envelope_and_sent_copy_only_once'),
    ('credential_account_binding', 'sedo/services/mail_secrets.py',
     "        if payload['email'] != account.email or payload['username'] != account.username:",
     '        if False:',
     'MailMimeAndSecurityTests.test_password_is_encrypted_bound_to_identity_and_private_on_disk'),
    ('business_source_archive', 'sedo/services/mail_storage.py',
     '        MailMessage.objects.filter(pk=message.pk, raw_cached=True).update(raw=raw, raw_cached=False)',
     '        pass',
     'MailMimeAndSecurityTests.test_business_source_survives_cache_loss_and_customer_link_pins_bytes'),
]

# Include targeted workspace guards in the same isolated mail counterprobe run.
runner.MUTATIONEN = [(*case[:-1], 'test_mail_client.' + case[-1]) for case in runner.MUTATIONEN]
runner.MUTATIONEN += [
    ('selected_folder_copy', 'sedo/mail_views.py',
     '    if location_id is not None:', '    if False:',
     'test_mail_workspace.MailWorkspaceTests.test_reader_uses_selected_copy_only_and_retains_page_and_filters'),
    ('copyuid_source_binding', 'sedo/services/mail_imap.py',
     ' or source_uid != location.uid', '',
     'test_mail_workspace.MailWorkspaceTests.test_bad_copyuid_never_invents_or_overwrites_destination'),
]

if __name__ == '__main__':
    raise SystemExit(runner.main())
