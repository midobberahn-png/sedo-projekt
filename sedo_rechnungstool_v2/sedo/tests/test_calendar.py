import sqlite3
import tempfile
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from sedo.backup_utils import _sanitized_db_copy
from sedo.models import CalendarCollection, CalendarConnection, CalendarEvent, DashboardPreference
from sedo.services import calendar_caldav as dav, calendar_sync as sync
from sedo.services.calendar_data import CalendarError, ZONE, agenda, aware, fields, render_event


URL = 'https://p12-caldav.icloud.com/123/calendars/work/'


def raw_event(uid='one@calendar.test', title='Besichtigung', start=None, end=None, extra=''):
    start = start or '20260923T080000Z'
    end = end or '20260923T090000Z'
    return ('BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Tests//DE\r\nBEGIN:VEVENT\r\n'
        f'UID:{uid}\r\nDTSTART:{start}\r\nDTEND:{end}\r\nSUMMARY:{title}\r\n{extra}'
        'END:VEVENT\r\nEND:VCALENDAR\r\n')


class CalendarPeer:
    def __init__(self):
        self.items, self.calls, self.counter = {}, [], 0
        self.fail_inventory = False
        self.lose_put_response = self.lose_delete_response = False
        self.race_raw = None

    def store(self, url, raw):
        self.counter += 1
        self.items[url] = (raw, f'"{self.counter}"')

    def discover(self):
        return [dict(name='Arbeit', remote_url=URL, writable=True)]

    def inventory(self, url):
        self.calls.append(('PROPFIND', url))
        if self.fail_inventory:
            raise CalendarError('Unvollständige Liste.')
        return {key:value[1] for key, value in self.items.items() if key.startswith(url)}

    def get(self, url):
        self.calls.append(('GET', url))
        return self.items.get(url)

    def put(self, url, raw, etag):
        self.calls.append(('PUT', url, etag))
        if self.race_raw:
            self.store(url, self.race_raw)
            self.race_raw = None
        if (etag and (url not in self.items or self.items[url][1] != etag)) or (not etag and url in self.items):
            return 412
        self.store(url, raw)
        if self.lose_put_response:
            self.lose_put_response = False
            raise CalendarError('Antwort verloren.')
        return 201

    def delete(self, url, etag):
        self.calls.append(('DELETE', url, etag))
        if url not in self.items:
            return 404
        if self.items[url][1] != etag:
            return 412
        del self.items[url]
        if self.lose_delete_response:
            self.lose_delete_response = False
            raise CalendarError('Antwort verloren.')
        return 204


class CalendarTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('calendar-user')
        cls.other = get_user_model().objects.create_user('calendar-other')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='sedo-calendar-tests-')
        self.addCleanup(self.temp.cleanup)
        setting = override_settings(SEDO_HOME=Path(self.temp.name))
        setting.enable()
        self.addCleanup(setting.disable)
        self.worker = patch('sedo.calendar_views.request_sync', return_value=True)
        self.worker.start()
        self.addCleanup(self.worker.stop)
        self.client.force_login(self.user)
        self.connection = CalendarConnection.objects.create(user=self.user, username='owner@example.test', enabled=True)
        self.calendar = CalendarCollection.objects.create(user=self.user, connection=self.connection,
            remote_url=URL, name='Arbeit', selected=True)
        self.peer = CalendarPeer()

    def run_sync(self):
        sync.sync_connection(self.connection.pk, self.peer)

    def load(self, raw=None, name='one.ics'):
        self.peer.store(URL + name, raw or raw_event())
        self.run_sync()
        return CalendarEvent.objects.get(remote_url=URL + name)

    def draft(self, title='Vor Ort'):
        event = CalendarEvent(calendar=self.calendar, title=title,
            start=aware(datetime(2026, 9, 23, 10)), end=aware(datetime(2026, 9, 23, 11)), state='pending')
        event.raw_ical = render_event(event)
        event.save()
        return event

    def data(self, event=None, **changes):
        return dict(revision=event.revision if event else 0, creation_key=str(uuid.uuid4()),
            calendar=self.calendar.pk, title='Ortstermin', start_date='2026-09-23', end_date='2026-09-23',
            start_time='10:00', end_time='11:00', **changes)

    def local_edit(self, event, title='Lokal geändert'):
        event.title, event.state = title, 'pending'
        event.raw_ical = render_event(event)
        event.save()

    def test_pull_and_local_create_update_delete_both_directions(self):
        remote = self.load()
        self.assertEqual(remote.title, 'Besichtigung')
        created = self.draft()
        self.run_sync()
        created.refresh_from_db()
        self.assertEqual(created.state, 'clean')
        self.assertEqual(len(self.peer.items), 2)
        self.local_edit(remote)
        self.run_sync()
        remote.refresh_from_db()
        self.assertEqual(remote.state, 'clean')
        self.assertIn('Lokal geändert', self.peer.items[remote.remote_url][0])
        remote.state = 'deleting'
        remote.save()
        self.run_sync()
        self.assertNotIn(remote.remote_url, self.peer.items)
        remote.refresh_from_db()
        self.assertEqual(remote.state, 'deleted')
        del self.peer.items[created.remote_url]
        self.run_sync()
        created.refresh_from_db()
        self.assertEqual(created.state, 'deleted')

    def test_two_sided_changes_preserve_both_versions(self):
        event = self.load()
        self.local_edit(event)
        self.peer.store(event.remote_url, raw_event(title='Apple geändert'))
        self.run_sync()
        event.refresh_from_db()
        self.assertEqual(event.state, 'conflict')
        self.assertIn('Apple geändert', event.remote_ical)
        self.assertIn('Lokal geändert', event.raw_ical)
        self.assertFalse(any(row[0] == 'PUT' for row in self.peer.calls))

    def test_remote_deletion_with_pending_local_edit_is_conflict(self):
        event = self.load()
        self.local_edit(event)
        del self.peer.items[event.remote_url]
        self.run_sync()
        event.refresh_from_db()
        self.assertEqual(event.state, 'conflict')
        self.assertTrue(event.remote_deleted)
        self.assertFalse(self.peer.items)

    def test_incomplete_inventory_never_deletes(self):
        event = self.load()
        self.peer.items.clear()
        self.peer.fail_inventory = True
        with self.assertRaises(CalendarError):
            self.run_sync()
        event.refresh_from_db()
        self.assertEqual(event.state, 'clean')
        self.assertTrue(event.raw_ical)

    def test_conditional_write_detects_race_after_pull(self):
        event = self.load()
        self.local_edit(event)
        self.peer.race_raw = raw_event(title='Noch später in Apple geändert')
        self.run_sync()
        event.refresh_from_db()
        self.assertEqual(event.state, 'conflict')
        self.assertIn('Noch später', self.peer.items[event.remote_url][0])

    def test_lost_create_response_does_not_duplicate_or_replay(self):
        event = self.draft()
        self.peer.lose_put_response = True
        with self.assertRaises(CalendarError):
            self.run_sync()
        event.refresh_from_db()
        self.assertEqual(event.pending_operation, 'put')
        self.run_sync()
        event.refresh_from_db()
        self.assertEqual(event.state, 'clean')
        self.assertEqual(len(self.peer.items), 1)
        self.assertEqual(sum(row[0] == 'PUT' for row in self.peer.calls), 1)

    def test_lost_update_response_and_subsequent_remote_edit_conflicts(self):
        event = self.load()
        self.local_edit(event)
        self.peer.lose_put_response = True
        with self.assertRaises(CalendarError):
            self.run_sync()
        self.peer.store(event.remote_url, raw_event(title='Danach in Apple'))
        self.run_sync()
        event.refresh_from_db()
        self.assertEqual(event.state, 'conflict')
        self.assertIn('Danach in Apple', event.remote_ical)

    def test_lost_delete_response_is_reconciled_without_repeat(self):
        event = self.load()
        event.state = 'deleting'
        event.save()
        self.peer.lose_delete_response = True
        with self.assertRaises(CalendarError):
            self.run_sync()
        self.run_sync()
        event.refresh_from_db()
        self.assertEqual(event.state, 'deleted')
        self.assertEqual(sum(row[0] == 'DELETE' for row in self.peer.calls), 1)

    def test_pending_operation_blocks_edit(self):
        event = self.draft()
        event.pending_operation = 'put'
        event.pending_ical = event.raw_ical
        event.save()
        response = self.client.post(reverse('sedo:calendar_edit', args=[event.pk]), self.data(event))
        self.assertContains(response, 'momentan nicht bearbeitet')
        event.refresh_from_db()
        self.assertEqual(event.title, 'Vor Ort')

    def test_another_users_calendar_is_not_visible_or_editable(self):
        event = self.load()
        self.client.force_login(self.other)
        for name in ('calendar_detail', 'calendar_edit'):
            self.assertEqual(self.client.get(reverse('sedo:' + name, args=[event.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('sedo:calendar_action', args=[event.pk]),
            {'action': 'delete', 'confirm': 'yes', 'revision': event.revision}).status_code, 404)
        response = self.client.post(reverse('sedo:calendar_create'), self.data())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(CalendarEvent.objects.count(), 1)
        self.assertNotContains(self.client.get(reverse('sedo:calendar_index')), 'Besichtigung')

    def test_event_creation_and_duplicate_submission(self):
        data = self.data()
        response = self.client.post(reverse('sedo:calendar_create'), data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(CalendarEvent.objects.get().state, 'pending')
        response = self.client.post(reverse('sedo:calendar_create'), data)
        self.assertContains(response, 'bereits gespeichert')
        self.assertEqual(CalendarEvent.objects.count(), 1)

    def test_local_calendar_does_not_upload_to_icloud(self):
        data = self.data()
        data['calendar'] = ''
        self.assertEqual(self.client.post(reverse('sedo:calendar_create'), data).status_code, 302)
        event = CalendarEvent.objects.get()
        self.assertIsNone(event.calendar.connection_id)
        self.assertEqual(event.state, 'clean')
        self.run_sync()
        self.assertFalse(self.peer.items)

    def test_stale_edit_does_not_overwrite(self):
        event = self.load()
        data = self.data(event)
        CalendarEvent.objects.filter(pk=event.pk).update(revision=event.revision + 1, title='Neuer Stand')
        response = self.client.post(reverse('sedo:calendar_edit', args=[event.pk]), data)
        self.assertContains(response, 'zwischenzeitlich geändert')
        event.refresh_from_db()
        self.assertEqual(event.title, 'Neuer Stand')

    def test_delete_requires_confirmation_and_matching_revision(self):
        event = self.load()
        url = reverse('sedo:calendar_action', args=[event.pk])
        self.client.post(url, {'action': 'delete', 'revision': event.revision})
        event.refresh_from_db()
        self.assertEqual(event.state, 'clean')
        self.client.post(url, {'action': 'delete', 'revision': 0, 'confirm': 'yes'})
        event.refresh_from_db()
        self.assertEqual(event.state, 'clean')
        self.client.post(url, {'action': 'delete', 'revision': event.revision, 'confirm': 'yes'})
        event.refresh_from_db()
        self.assertEqual(event.state, 'deleting')

    def test_get_and_csrf_cannot_mutate(self):
        event = self.load()
        url = reverse('sedo:calendar_action', args=[event.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.user)
        self.assertEqual(protected.post(url, {'action': 'delete', 'confirm': 'yes',
            'revision': event.revision}).status_code, 403)

    def test_all_day_inclusive_end_and_dst_validation(self):
        data = self.data()
        data.update(all_day='on', start_date='2026-10-24', end_date='2026-10-25', start_time='', end_time='')
        self.assertEqual(self.client.post(reverse('sedo:calendar_create'), data).status_code, 302)
        event = CalendarEvent.objects.get()
        self.assertEqual((event.end.astimezone(ZONE).date() - event.start.astimezone(ZONE).date()).days, 2)
        self.assertIn('DTEND;VALUE=DATE:20261026', event.raw_ical)
        for day in ('2026-03-29', '2026-10-25'):
            data = self.data()
            data.update(start_date=day, end_date=day, start_time='02:30', end_time='04:30')
            self.assertEqual(self.client.post(reverse('sedo:calendar_create'), data).status_code, 200)
        self.assertEqual(CalendarEvent.objects.count(), 1)

    def test_series_exceptions_and_invitation_are_visible_but_not_rewritten(self):
        raw = raw_event(extra='RRULE:FREQ=DAILY;COUNT=3\r\nEXDATE:20260924T080000Z\r\n')
        event = self.load(raw)
        rows, errors = agenda(self.user, aware(date(2026, 9, 23)), aware(date(2026, 9, 27)))
        self.assertEqual([row['start'].day for row in rows], [23, 25])
        self.assertFalse(errors)
        response = self.client.post(reverse('sedo:calendar_edit', args=[event.pk]), self.data(event))
        self.assertContains(response, 'Apple Kalender bearbeiten')
        event.refresh_from_db()
        self.assertEqual(event.raw_ical, raw)
        invite = self.load(raw_event(uid='invite', extra='ORGANIZER:mailto:host@example.test\r\nATTENDEE:mailto:guest@example.test\r\n'), 'invite.ics')
        self.assertIn('Einladungen', invite.read_only_reason)

    def test_unknown_properties_and_alarms_survive_edit(self):
        event = self.load(raw_event(extra='X-APPLE-CUSTOM:keep-me\r\nBEGIN:VALARM\r\nACTION:DISPLAY\r\nTRIGGER:-PT15M\r\nDESCRIPTION:Termin\r\nEND:VALARM\r\n'))
        self.local_edit(event)
        self.assertIn('X-APPLE-CUSTOM:keep-me', event.raw_ical)
        self.assertIn('BEGIN:VALARM', event.raw_ical)

    def test_dashboard_is_optional_personal_and_chronological(self):
        self.load(raw_event(extra='RRULE:FREQ=DAILY;COUNT=20\r\n'))
        self.load(raw_event(uid='soon', title='Dazwischen', start='20260923T120000Z', end='20260923T130000Z'), 'soon.ics')
        DashboardPreference.objects.create(user=self.user, widgets=['calendar'])
        now = aware(datetime(2026, 9, 23, 7))
        with patch('sedo.views.timezone.now', return_value=now):
            response = self.client.get(reverse('sedo:dashboard'))
        self.assertContains(response, 'data-widget="calendar"')
        self.assertEqual(response.context['calendar_entries'][1]['title'], 'Dazwischen')
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(reverse('sedo:dashboard')), 'data-widget="calendar"')

    def test_conflict_resolution_is_reviewed_and_etag_guarded(self):
        event = self.load()
        self.local_edit(event)
        self.peer.store(event.remote_url, raw_event(title='Apple neu'))
        self.run_sync()
        event.refresh_from_db()
        url = reverse('sedo:calendar_action', args=[event.pk])
        self.client.post(url, {'action': 'keep_local', 'revision': event.revision})
        event.refresh_from_db()
        self.assertEqual(event.state, 'conflict')
        self.client.post(url, {'action': 'keep_local', 'revision': event.revision, 'confirm': 'yes'})
        event.refresh_from_db()
        self.assertEqual(event.state, 'pending')
        self.peer.store(event.remote_url, raw_event(title='Erneut Apple geändert'))
        self.run_sync()
        event.refresh_from_db()
        self.assertEqual(event.state, 'conflict')
        self.client.post(url, {'action': 'keep_remote', 'revision': event.revision, 'confirm': 'yes'})
        event.refresh_from_db()
        self.assertEqual(event.title, 'Erneut Apple geändert')
        self.assertEqual(event.state, 'clean')

    def test_read_only_or_unselected_calendar_never_uploads(self):
        self.draft()
        self.calendar.writable = False
        self.calendar.save()
        self.run_sync()
        self.assertFalse(self.peer.items)
        self.calendar.writable, self.calendar.selected = True, False
        self.calendar.save()
        self.run_sync()
        self.assertFalse(self.peer.items)

    def test_lease_blocks_concurrent_changes(self):
        self.connection.lease_token = 'another-worker'
        self.connection.lease_until = timezone.now() + timedelta(minutes=1)
        self.connection.save()
        with self.assertRaises(CalendarError):
            self.run_sync()
        self.assertFalse(self.peer.calls)
        response = self.client.post(reverse('sedo:calendar_create'), self.data())
        self.assertContains(response, 'gerade abgeglichen')

    def test_credentials_encrypted_identity_bound_and_not_rendered(self):
        password = 'synthetic-calendar-app-password'
        self.connection.password_encrypted = sync.encrypt(self.connection, password)
        self.connection.save()
        self.assertNotIn(password, self.connection.password_encrypted)
        client = sync.client_for(self.connection)
        self.assertTrue(client.authorization.startswith('Basic '))
        response = self.client.get(reverse('sedo:calendar_settings'))
        self.assertNotContains(response, password)
        self.connection.user = self.other
        with self.assertRaises(CalendarError):
            sync.client_for(self.connection)

    def test_discovery_never_selects_calendars_or_starts_sync(self):
        self.calendar.delete()
        self.assertEqual(sync.discover(self.connection, self.peer), 1)
        self.assertFalse(CalendarCollection.objects.get().selected)
        self.worker.stop()

    def test_selection_requires_confirmation_and_own_calendars(self):
        self.connection.enabled = False
        self.connection.password_encrypted = sync.encrypt(self.connection, 'synthetic-password')
        self.connection.save()
        url = reverse('sedo:calendar_settings')
        self.client.post(url, {'action': 'selection', 'revision': self.connection.revision, 'calendar': self.calendar.pk})
        self.connection.refresh_from_db()
        self.assertFalse(self.connection.enabled)
        self.client.post(url, {'action': 'selection', 'revision': self.connection.revision, 'calendar': 999999, 'confirm': 'yes'})
        self.connection.refresh_from_db()
        self.assertFalse(self.connection.enabled)
        self.client.post(url, {'action': 'selection', 'revision': self.connection.revision, 'calendar': self.calendar.pk, 'confirm': 'yes'})
        self.connection.refresh_from_db()
        self.assertTrue(self.connection.enabled)

    def test_paused_connection_makes_no_network_calls(self):
        self.connection.enabled = False
        self.connection.save()
        self.run_sync()
        self.assertFalse(self.peer.calls)

    def test_backup_removes_credentials_and_holds_pending_writes(self):
        path = Path(self.temp.name) / 'sample.sqlite3'
        db = sqlite3.connect(path)
        db.executescript("CREATE TABLE sedo_calendarconnection(password_encrypted TEXT, enabled INTEGER, lease_token TEXT, lease_until TEXT);"
            "CREATE TABLE sedo_calendarevent(state TEXT,pending_operation TEXT,pending_ical TEXT,error TEXT);")
        secret = 'synthetic-ciphertext-never-in-backup'
        db.execute('INSERT INTO sedo_calendarconnection VALUES (?,1,?,?)', (secret, 'worker', 'later'))
        db.execute("INSERT INTO sedo_calendarevent VALUES ('pending','put','draft','')")
        db.commit()
        db.close()
        clean = _sanitized_db_copy(path)
        self.addCleanup(clean.unlink, missing_ok=True)
        with sqlite3.connect(clean) as copy_db:
            self.assertEqual(copy_db.execute('SELECT password_encrypted,enabled,lease_token,lease_until FROM sedo_calendarconnection').fetchone(), ('', 0, '', None))
            self.assertEqual(copy_db.execute('SELECT state,pending_operation FROM sedo_calendarevent').fetchone(), ('conflict', ''))
        self.assertNotIn(secret.encode(), clean.read_bytes())

    def test_calendar_pages_render_without_network(self):
        self.load()
        with patch.object(dav.ICloudDAV, 'request', side_effect=AssertionError('network from GET')):
            for name in ('calendar_index', 'calendar_create', 'calendar_settings'):
                self.assertEqual(self.client.get(reverse('sedo:' + name)).status_code, 200)
            self.assertEqual(self.client.get(reverse('sedo:calendar_index') + '?view=list').status_code, 200)
            self.assertEqual(self.client.get(reverse('sedo:calendar_index') + '?month=invalid').status_code, 400)

    def test_cloud_content_is_escaped(self):
        event = self.load(raw_event(title='<script>alert(1)</script>'))
        response = self.client.get(reverse('sedo:calendar_detail', args=[event.pk]))
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertContains(response, '&lt;script&gt;')

    def test_caldav_only_allows_apple_calendar_hosts(self):
        for url in ('http://caldav.icloud.com/', 'https://evil.test/', 'https://caldav.icloud.com.evil.test/',
                    'https://user:password@caldav.icloud.com/', 'https://p12-caldav.icloud.com:8443/',
                    'https://caldav.icloud.com/?token=x', 'https://p12-caldav.icloud.com/a/%2e%2e/'):
            with self.assertRaises(CalendarError, msg=url):
                dav.checked_url(url)
        self.assertEqual(dav.checked_url('one.ics', URL, within=URL), URL + 'one.ics')
        with self.assertRaises(CalendarError):
            dav.checked_url('../other/event.ics', URL, within=URL)

    def test_caldav_conditional_headers_and_weak_etags(self):
        client = dav.ICloudDAV('test@example.test', 'synthetic')
        with patch.object(client, 'request', return_value=(201, {}, b'')) as request:
            client.put(URL + 'x.ics', raw_event(), '')
            self.assertEqual(request.call_args.args[3]['If-None-Match'], '*')
            client.put(URL + 'x.ics', raw_event(), '"etag"')
            self.assertEqual(request.call_args.args[3]['If-Match'], '"etag"')
            client.delete(URL + 'x.ics', '"etag"')
            self.assertEqual(request.call_args.kwargs['headers']['If-Match'], '"etag"')
            for etag in ('', 'W/"weak"', '"bad\r\nheader"'):
                with self.assertRaises(CalendarError):
                    client.delete(URL + 'x.ics', etag)

    def test_caldav_entity_and_partial_inventory_rejected(self):
        with self.assertRaises(CalendarError):
            dav.multistatus(b'<!DOCTYPE x [<!ENTITY y SYSTEM "file:///tmp/no">]><x/>')
        body = b'<d:multistatus xmlns:d="DAV:"><d:response><d:href>/123/calendars/work/x.ics</d:href><d:propstat><d:prop/><d:status>HTTP/1.1 403 Forbidden</d:status></d:propstat></d:response></d:multistatus>'
        client = dav.ICloudDAV('test@example.test', 'synthetic')
        with patch.object(client, 'request', return_value=(207, {}, body)):
            with self.assertRaises(CalendarError):
                client.inventory(URL)

    def test_caldav_redirect_cannot_exfiltrate_credentials(self):
        response = MagicMock(status=302)
        response.read.return_value = b''
        response.getheaders.return_value = [('Location', 'https://evil.test/')]
        response.getheader.return_value = 'https://evil.test/'
        connection = MagicMock()
        connection.getresponse.return_value = response
        with patch('sedo.services.calendar_caldav.http.client.HTTPSConnection', return_value=connection) as factory:
            with self.assertRaises(CalendarError):
                dav.ICloudDAV('test@example.test', 'synthetic').request('PROPFIND', dav.ROOT)
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(factory.call_args.args[0], 'caldav.icloud.com')

    def test_malformed_ical_is_held(self):
        for raw in ('not a calendar', raw_event(extra='RRULE:FREQ=SECONDLY\r\n')):
            with self.assertRaises(CalendarError):
                fields(raw)

    def test_empty_multistatus_is_not_evidence_of_deleted_events(self):
        client = dav.ICloudDAV('test@example.test', 'synthetic')
        with patch.object(client, 'request', return_value=(207, {}, b'<d:multistatus xmlns:d="DAV:"/>')):
            with self.assertRaises(CalendarError):
                client.inventory(URL)

    def test_discovery_and_inventory_parse_real_dav_shapes(self):
        def response(href, content):
            return ('<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
                f'<d:response><d:href>{href}</d:href><d:propstat><d:prop>{content}</d:prop>'
                '<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>').encode()
        responses = [
            response('/', '<d:current-user-principal><d:href>/123/principal/</d:href></d:current-user-principal>'),
            response('/123/principal/', '<c:calendar-home-set><d:href>https://p12-caldav.icloud.com/123/calendars/</d:href></c:calendar-home-set>'),
            response(URL, '<d:resourcetype><d:collection/><c:calendar/></d:resourcetype>'
                '<d:displayname>Arbeit &amp; Termine</d:displayname>'
                '<c:supported-calendar-component-set><c:comp name="VEVENT"/></c:supported-calendar-component-set>'
                '<d:current-user-privilege-set><d:privilege><d:write/></d:privilege></d:current-user-privilege-set>'),
        ]
        client = dav.ICloudDAV('test@example.test', 'synthetic')
        with patch.object(client, 'request', side_effect=[(207, {}, item) for item in responses]) as request:
            result = client.discover()
            self.assertEqual(result, [dict(name='Arbeit & Termine', remote_url=URL, writable=True)])
            self.assertEqual(request.call_args.args[3]['Depth'], '1')
        own = response(URL, '<d:resourcetype><d:collection/><c:calendar/></d:resourcetype>')
        child = response(URL + 'one.ics', '<d:resourcetype/><d:getetag>&quot;abc&quot;</d:getetag>')
        from lxml import etree
        tree = etree.fromstring(own)
        tree.append(etree.fromstring(child)[0])
        with patch.object(client, 'request', return_value=(207, {}, etree.tostring(tree))):
            self.assertEqual(client.inventory(URL), {URL + 'one.ics': '"abc"'})

    def test_calendars_returned_for_another_collection_are_rejected(self):
        body = b'<d:multistatus xmlns:d="DAV:"><d:response><d:href>https://p12-caldav.icloud.com/other/x.ics</d:href><d:propstat><d:prop><d:getetag>"abc"</d:getetag></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>'
        with patch.object(dav.ICloudDAV, 'request', return_value=(207, {}, body)):
            with self.assertRaises(CalendarError):
                dav.ICloudDAV('test@example.test', 'synthetic').inventory(URL)

    def test_large_remote_body_is_rejected_before_parsing(self):
        response = MagicMock(status=200)
        response.read.return_value = b'x' * (dav.MAX_ICAL + 1)
        response.getheaders.return_value = []
        connection = MagicMock()
        connection.getresponse.return_value = response
        with patch('sedo.services.calendar_caldav.http.client.HTTPSConnection', return_value=connection):
            with self.assertRaises(CalendarError):
                dav.ICloudDAV('test@example.test', 'synthetic').get(URL + 'one.ics')
        self.assertEqual(response.read.call_args.args[0], dav.MAX_ICAL + 1)

    def test_invalid_times_and_unresolved_timezone_are_not_guessed(self):
        for begin, end in (('11:00', '10:00'), ('10:00', '10:00')):
            data = self.data()
            data.update(start_time=begin, end_time=end)
            self.assertEqual(self.client.post(reverse('sedo:calendar_create'), data).status_code, 200)
        self.assertFalse(CalendarEvent.objects.exists())
        bad = raw_event().replace('DTSTART:20260923T080000Z', 'DTSTART;TZID=Unbekannt/Zeitzone:20260923T080000')
        with self.assertRaises(CalendarError):
            fields(bad)

    def test_selection_is_private_even_when_other_user_enables_widget(self):
        self.load()
        DashboardPreference.objects.create(user=self.other, widgets=['calendar'])
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(reverse('sedo:dashboard')), 'Besichtigung')

    def test_local_delete_and_remote_edit_remains_a_delete_conflict(self):
        event = self.load()
        event.state, event.deleted_locally = 'deleting', True
        event.save()
        self.peer.store(event.remote_url, raw_event(title='Apple inzwischen geändert'))
        self.run_sync()
        event.refresh_from_db()
        self.assertEqual(event.state, 'conflict')
        self.assertTrue(event.deleted_locally)
        self.client.post(reverse('sedo:calendar_action', args=[event.pk]),
            {'action': 'keep_local', 'confirm': 'yes', 'revision': event.revision})
        event.refresh_from_db()
        self.assertEqual(event.state, 'deleting')
        self.run_sync()
        self.assertNotIn(event.remote_url, self.peer.items)

    def test_remote_conflict_changes_invalidate_old_review(self):
        event = self.load()
        self.local_edit(event)
        self.peer.store(event.remote_url, raw_event(title='Apple zuerst'))
        self.run_sync()
        event.refresh_from_db()
        old_revision = event.revision
        self.peer.store(event.remote_url, raw_event(title='Apple danach'))
        self.run_sync()
        self.client.post(reverse('sedo:calendar_action', args=[event.pk]),
            {'action': 'keep_local', 'confirm': 'yes', 'revision': old_revision})
        event.refresh_from_db()
        self.assertEqual(event.state, 'conflict')
        self.assertIn('Apple danach', event.remote_ical)

    def test_unchanged_conflict_does_not_invalidate_review(self):
        event = self.load()
        self.local_edit(event)
        self.peer.store(event.remote_url, raw_event(title='Apple'))
        self.run_sync()
        event.refresh_from_db()
        revision = event.revision
        self.run_sync()
        event.refresh_from_db()
        self.assertEqual(event.revision, revision)

    def test_failed_discovery_does_not_hide_existing_calendars(self):
        with patch.object(self.peer, 'discover', side_effect=CalendarError('Nicht erreichbar.')):
            with self.assertRaises(CalendarError):
                sync.discover(self.connection, self.peer)
        self.calendar.refresh_from_db()
        self.assertTrue(self.calendar.available)
        self.assertTrue(self.calendar.selected)

    def test_remote_modified_series_exception_appears_once(self):
        raw = raw_event(extra='RRULE:FREQ=DAILY;COUNT=3\r\n')
        exception = ('BEGIN:VEVENT\r\nUID:one@calendar.test\r\nRECURRENCE-ID:20260924T080000Z\r\n'
            'DTSTART:20260924T100000Z\r\nDTEND:20260924T110000Z\r\nSUMMARY:Verschobener Termin\r\nEND:VEVENT\r\n')
        self.load(raw.replace('END:VCALENDAR', exception + 'END:VCALENDAR'))
        rows, errors = agenda(self.user, aware(date(2026,9,23)), aware(date(2026,9,27)))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1]['title'], 'Verschobener Termin')
        self.assertEqual(rows[1]['start'].hour, 10)
        self.assertFalse(errors)

    def test_offline_pending_create_is_visible_and_preserved(self):
        event = self.draft()
        with patch.object(self.peer, 'inventory', side_effect=CalendarError('Offline.')):
            with self.assertRaises(CalendarError):
                self.run_sync()
        event.refresh_from_db()
        self.assertEqual(event.state, 'pending')
        rows, errors = agenda(self.user, aware(date(2026,9,23)), aware(date(2026,9,24)))
        self.assertEqual(rows[0]['title'], event.title)
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.last_error, 'Offline.')
