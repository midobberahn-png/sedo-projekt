"""Calendar synchronization must fail safely when a guard is removed."""
import gegenproben_entwurf as runner

runner.TEST = 'sedo.tests.test_calendar.CalendarTests.'
runner.MUTATIONEN = [
    ('two_sided_conflict', 'sedo/services/calendar_sync.py',
     "elif event.state in ('pending', 'deleting', 'conflict') or event.pending_operation:",
     "elif event.state == 'conflict' or event.pending_operation:",
     'test_two_sided_changes_preserve_both_versions'),
    ('strong_delete_etag', 'sedo/services/calendar_caldav.py',
     'if not strong_etag(etag):', 'if False:',
     'test_caldav_conditional_headers_and_weak_etags'),
    ('create_if_none_match', 'sedo/services/calendar_caldav.py',
     "headers['If-Match' if etag else 'If-None-Match'] = etag or '*'",
     "headers['If-Match' if etag else 'If-None-Match'] = etag or ''",
     'test_caldav_conditional_headers_and_weak_etags'),
    ('owner_boundary', 'sedo/calendar_views.py',
     'pk=pk, calendar__user=user)', 'pk=pk)',
     'test_another_users_calendar_is_not_visible_or_editable'),
    ('delete_confirmation', 'sedo/calendar_views.py',
     "if request.POST.get('confirm') != 'yes':\n            raise CalendarError('Bitte die gewählte Änderung bestätigen.')",
     "if False:\n            raise CalendarError('Bitte die gewählte Änderung bestätigen.')",
     'test_delete_requires_confirmation_and_matching_revision'),
    ('paused_connection', 'sedo/services/calendar_sync.py',
     'if not connection.enabled:', 'if False:',
     'test_paused_connection_makes_no_network_calls'),
    ('restore_no_replay', 'sedo/backup_utils.py',
     "SET state='conflict', pending_operation='', pending_ical='', error='Wiederhergestellte",
     "SET state=state, pending_operation='', pending_ical='', error='Wiederhergestellte",
     'test_backup_removes_credentials_and_holds_pending_writes'),
]

if __name__ == '__main__':
    raise SystemExit(runner.main())
