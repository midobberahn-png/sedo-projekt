"""Local spam protections must fail their regression test when removed."""
import gegenproben_entwurf as runner

runner.TEST = 'sedo.tests.test_mail_spam.SpamTests.'
runner.MUTATIONEN = [
    ('manual_release', 'sedo/services/mail_spam.py',
     "if message.spam_manual == 'ham':", 'if False:',
     'test_manual_ham_survives_rule_revision_and_repeated_sync'),
    ('address_priority', 'sedo/services/mail_spam.py',
     "return rules.filter(kind='address').first() or rules.filter(kind='domain').first()",
     "return rules.filter(kind='domain').first() or rules.filter(kind='address').first()",
     'test_domain_rule_is_exact_and_address_rule_wins'),
    ('old_mail_baseline', 'sedo/services/mail_spam.py',
     "uid__gt=F('folder__spam_baseline_uid'),", 'uid__gte=0,',
     'test_backfilled_old_messages_and_new_rules_never_mass_move'),
    ('multiple_indicators', 'sedo/services/mail_spam.py',
     'if len(signals) >= 2:', 'if len(signals) >= 1:',
     'test_one_weak_indicator_is_not_enough'),
    ('unknown_move', 'sedo/services/mail_spam.py',
     "if location.message.spam_moves.filter(state='unknown').exists():", 'if False:',
     'test_unknown_move_cannot_be_repeated_manually_or_after_uidvalidity_reset'),
    ('no_automatic_retry', 'sedo/services/mail_spam.py',
     "message__created_at__gte=account.spam_auto_since, message__spam_moves__isnull=True",
     "message__created_at__gte=account.spam_auto_since",
     'test_failure_before_move_is_not_automatically_retried_after_sync'),
    ('invoice_hold', 'sedo/services/mail_spam.py',
     '    if blocked(message):', '    if False:',
     'test_spam_never_enters_invoice_intake_until_explicit_release'),
    ('review_confirmation', 'sedo/mail_spam_views.py',
     "if data['user'] != request.user.pk or request.POST.get('confirm') != 'yes':",
     "if data['user'] != request.user.pk:",
     'test_batch_preview_and_confirmation_are_required_and_do_not_create_rules'),
]

if __name__ == '__main__':
    raise SystemExit(runner.main())
