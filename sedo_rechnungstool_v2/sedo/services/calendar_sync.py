"""Pull before push; ETags and stable resource URLs prevent lost updates."""
import hashlib
import json
import os
import tempfile
import uuid
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from cryptography.fernet import Fernet
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from sedo.models import CalendarCollection, CalendarConnection, CalendarEvent
from .calendar_caldav import ICloudDAV, checked_url
from .calendar_data import CalendarError, fields, fingerprint


def cipher(create=False):
    directory = Path(settings.SEDO_HOME) / 'data'
    path = directory / '.calendar_key'
    if create and not path.exists():
        directory.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.calendar-key-', dir=directory)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(Fernet.generate_key())
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(name, path)
            except FileExistsError:
                pass
        finally:
            Path(name).unlink(missing_ok=True)
    try:
        if path.is_symlink():
            raise ValueError()
        return Fernet(path.read_bytes())
    except Exception as exc:
        raise CalendarError('Der lokale Kalenderschlüssel fehlt oder ist beschädigt. Zugang bitte erneut einrichten.') from exc


def encrypt(connection, password):
    return cipher(True).encrypt(json.dumps({'user': connection.user_id,
        'username': connection.username, 'password': password}).encode()).decode()


def client_for(connection):
    try:
        value = json.loads(cipher().decrypt(connection.password_encrypted.encode()))
        if value['user'] != connection.user_id or value['username'] != connection.username:
            raise ValueError()
        return ICloudDAV(connection.username, value['password'])
    except Exception as exc:
        raise CalendarError('Bitte das App-spezifische iCloud-Passwort in den Kalendereinstellungen erneut eingeben.') from exc


@contextmanager
def connection_lease(pk):
    token = str(uuid.uuid4())
    def renew():
        if not CalendarConnection.objects.filter(pk=pk, lease_token=token).update(
                lease_until=timezone.now() + timedelta(minutes=3)):
            raise CalendarError('Der Kalenderabgleich wurde unterbrochen. Bitte erneut versuchen.')
    claimed = CalendarConnection.objects.filter(pk=pk).filter(
        Q(lease_until__isnull=True) | Q(lease_until__lt=timezone.now())).update(
            lease_token=token, lease_until=timezone.now() + timedelta(minutes=3))
    if not claimed:
        raise CalendarError('Der Kalender wird gerade abgeglichen. Bitte kurz warten.')
    try:
        yield renew
    finally:
        CalendarConnection.objects.filter(pk=pk, lease_token=token).update(lease_token='', lease_until=None)


def discover(connection, client=None):
    rows = (client or client_for(connection)).discover()
    with transaction.atomic():
        CalendarCollection.objects.filter(connection=connection).update(available=False)
        for row in rows:
            CalendarCollection.objects.update_or_create(user_id=connection.user_id,
                remote_url=row['remote_url'], defaults={**row, 'connection': connection, 'available': True})
    return len(rows)


def apply_remote(event, raw, etag):
    values = fields(raw)
    if event.pk and event.uid != values['uid']:
        raise CalendarError('Die Kennung eines iCloud-Termins hat sich unerwartet geändert.')
    for key, value in values.items():
        setattr(event, key, value)
    event.raw_ical = event.base_ical = raw
    event.etag, event.state = etag, 'clean'
    event.remote_ical = event.remote_etag = event.error = ''
    event.remote_deleted = False
    event.deleted_locally = False
    event.pending_ical = event.pending_operation = ''
    event.revision += 1
    event.updated_at = timezone.now()
    event.save()


def conflict(event, raw=None, etag=''):
    # Keep the local draft and the server version, including a remote deletion.
    if (event.state == 'conflict' and event.remote_ical == (raw or '') and event.remote_etag == etag
            and event.remote_deleted == (raw is None)):
        return
    event.state, event.remote_ical, event.remote_etag = 'conflict', raw or '', etag
    event.remote_deleted = raw is None
    event.pending_operation = event.pending_ical = ''
    event.error = 'Der Termin wurde auf beiden Seiten geändert. Bitte die Fassungen vergleichen.'
    event.revision += 1
    event.save()


def mark_deleted(event):
    event.state, event.error = 'deleted', ''
    event.pending_operation = event.pending_ical = ''
    event.revision += 1
    event.save()


def reconcile(event, remote):
    if remote is None:
        if event.state == 'deleting' or event.pending_operation == 'delete' or event.state in ('clean', 'deleted'):
            mark_deleted(event)
        elif event.etag:
            conflict(event)
        return
    raw, etag = remote
    if event.pending_operation == 'put' and fingerprint(raw) == fingerprint(event.pending_ical):
        apply_remote(event, raw, etag)
    elif etag == event.etag and event.state != 'conflict':
        return
    elif event.state in ('pending', 'deleting', 'conflict') or event.pending_operation:
        conflict(event, raw, etag)
    else:
        apply_remote(event, raw, etag)


def sync_calendar(calendar, client, renew):
    renew()
    inventory = client.inventory(calendar.remote_url)
    known = {event.remote_url: event for event in calendar.events.exclude(remote_url='')}
    loaded = 0
    for url, etag in inventory.items():
        event = known.get(url)
        if event and event.etag == etag and not event.pending_operation and event.state != 'conflict':
            continue
        if loaded >= 100:
            # Full inventory is still known, but no outgoing writes until pull catches up.
            raise CalendarError('Viele Termine: ein weiterer Abgleich lädt den nächsten Abschnitt.')
        loaded += 1
        renew()
        remote = client.get(url)
        if remote is None:
            # Resource vanished after inventory; handled on next complete inventory.
            continue
        raw, remote_etag = remote
        values = fields(raw)
        if event is None:
            event = calendar.events.filter(uid=values['uid']).first()
            if event:
                if event.remote_url and event.remote_url != url:
                    raise CalendarError('Ein Termin erscheint unter mehreren iCloud-Adressen. Abgleich gestoppt.')
                event.remote_url = url
                conflict(event, raw, remote_etag)
            else:
                event = CalendarEvent(calendar=calendar, remote_url=url)
                apply_remote(event, raw, remote_etag)
        else:
            reconcile(event, remote)
    # Absence only counts after a successfully parsed, complete inventory.
    for url, event in known.items():
        if url not in inventory and event.state != 'deleted':
            reconcile(event, None)
    if not calendar.writable:
        return
    pending = calendar.events.filter(state__in=['pending', 'deleting']).order_by('pk')[:25]
    for event in pending:
        if event.read_only_reason:
            continue
        renew()
        if not event.remote_url:
            name = hashlib.sha256(event.uid.encode()).hexdigest() + '.ics'
            event.remote_url = checked_url(name, calendar.remote_url, within=calendar.remote_url)
        # Always refresh before a retry and before a write. ETag condition closes the remaining race.
        remote = client.get(event.remote_url)
        if remote is not None or event.etag:
            reconcile(event, remote)
        if event.state not in ('pending', 'deleting'):
            continue
        if event.state == 'deleting' and not event.etag:
            mark_deleted(event)
            continue
        event.pending_operation = 'delete' if event.state == 'deleting' else 'put'
        event.pending_ical = event.raw_ical if event.pending_operation == 'put' else ''
        event.save(update_fields=['remote_url', 'pending_operation', 'pending_ical'])
        status = (client.delete(event.remote_url, event.etag) if event.pending_operation == 'delete'
                  else client.put(event.remote_url, event.pending_ical, event.etag))
        if status in (409, 412) or (status == 404 and event.pending_operation == 'put'):
            remote = client.get(event.remote_url)
            conflict(event, *(remote or (None, '')))
            continue
        if event.pending_operation == 'delete' and status in (200, 204, 404):
            mark_deleted(event)
        elif event.pending_operation == 'put' and status in (200, 201, 204):
            remote = client.get(event.remote_url)
            if remote is None:
                raise CalendarError('iCloud hat gespeichert, der Termin ist noch nicht lesbar. Nächsten Abgleich abwarten.')
            apply_remote(event, *remote)
        else:
            raise CalendarError('iCloud hat die Änderung nicht bestätigt. Der nächste Abgleich prüft zuerst den Serverstand.')


def sync_connection(pk, client=None):
    with connection_lease(pk) as renew:
        connection = CalendarConnection.objects.get(pk=pk)
        if not connection.enabled:
            return
        try:
            client = client or client_for(connection)
            for calendar in connection.calendars.filter(user_id=connection.user_id, selected=True, available=True):
                sync_calendar(calendar, client, renew)
            CalendarConnection.objects.filter(pk=pk).update(last_sync=timezone.now(), last_error='')
        except CalendarError as exc:
            CalendarConnection.objects.filter(pk=pk).update(last_error=str(exc))
            raise
