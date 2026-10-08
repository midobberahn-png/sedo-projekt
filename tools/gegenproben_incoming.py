"""Schutzregeln des Belegeingangs auf isolierten Quellkopien entfernen."""
import gegenproben_entwurf as runner

runner.TEST = 'sedo.tests.'
runner.MUTATIONEN = [
    ('package_attachment', 'sedo/services/mail_send.py',
     '    check_package_attachment(draft)', '    pass',
     'test_package_status_cannot_claim_delivery_when_attachment_removed_or_changed'),
    ('original_hash', 'sedo/services/incoming_invoices.py',
     'if len(data) != invoice.size or digest(data) != invoice.digest:', 'if False:',
     'test_tampered_original_is_blocked_before_approval_download_print_or_export'),
    ('review_guard', 'sedo/services/incoming_invoices.py',
     "if len(invoices) != len(ids) or any(item.state != 'approved' for item in invoices):",
     'if len(invoices) != len(ids):',
     'test_unreviewed_selection_cannot_print_export_or_mark_printed'),
    ('source_retention', 'sedo/services/incoming_invoices.py',
     '            pin_message(message)', '            pass',
     'test_auto_inbox_intake_pins_original_and_survives_cache_removal'),
    ('user_confirmation', 'sedo/incoming_forms.py',
     "if not data.get('confirm'):", 'if False:',
     'test_approval_requires_fields_confirmation_and_current_revision'),
    ('paused_intake', 'sedo/services/incoming_invoices.py',
     "if AppSetting.get('incoming_invoice_enabled', '1') != '1':", 'if False:',
     'test_sent_spam_trash_and_disabled_intake_not_collected'),
    ('xml_dtd_guard', 'sedo/document_probe.py',
     "        if root.getroottree().docinfo.doctype:\n            raise DocumentProbeError('invalid_xml')",
     "        if False:\n            raise DocumentProbeError('invalid_xml')",
     'test_xml_entities_are_not_read_or_sent_to_model'),
]

runner.MUTATIONEN = [(*case[:-1], 'test_incoming_invoices.IncomingInvoiceTests.' + case[-1]) for case in runner.MUTATIONEN]
runner.MUTATIONEN += [
    ('invoice_only_intake', 'sedo/services/incoming_invoices.py',
     "            if detection.kind != 'invoice':", '            if False:',
     'test_incoming_detection.IncomingDetectionTests.test_each_attachment_is_checked_even_when_email_says_invoice'),
    ('document_type_veto', 'sedo/document_probe.py',
     '    if title and _OTHER_WORD.fullmatch(title):', '    if False:',
     'test_incoming_detection.IncomingDetectionTests.test_offer_delivery_and_meter_letters_never_inherit_invoice_filename_or_subject'),
]

if __name__ == '__main__':
    raise SystemExit(runner.main())
