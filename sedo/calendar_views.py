import calendar as month_calendar
from contextlib import nullcontext
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import F
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST

from .calendar_forms import CalendarConnectionForm, CalendarEventForm
from .models import CalendarCollection, CalendarConnection, CalendarEvent
from .services.calendar_data import CalendarError, ZONE, agenda, aware, fields, render_event
from .services.calendar_sync import apply_remote, connection_lease, discover, encrypt, mark_deleted
from .services.calendar_worker import request_sync


def owned_event(user, pk):
    return get_object_or_404(CalendarEvent.objects.select_related('calendar', 'calendar__connection', 'customer'),
        pk=pk, calendar__user=user)


def revision(post):
    try:
        return int(post.get('revision', '-1'))
    except (ValueError, TypeError):
        return -1


def event_lock(event):
    return connection_lease(event.calendar.connection_id) if event.calendar.connection_id else nullcontext()


def require_editable(event):
    if (not event.calendar.selected or not event.calendar.available or not event.calendar.writable
            or event.read_only_reason or event.state in ('conflict', 'deleted', 'deleting') or event.pending_operation):
        raise CalendarError(event.read_only_reason or 'Dieser Termin kann momentan nicht bearbeitet werden. Bitte den Abgleich und den Kalenderstatus prüfen.')


@login_required
@never_cache
def index(request):
    today = timezone.localdate()
    try:
        chosen = date.fromisoformat(request.GET.get('month', today.strftime('%Y-%m')) + '-01')
        if not 1901 <= chosen.year <= 2198:
            raise ValueError()
    except ValueError:
        return HttpResponseBadRequest('Bitte einen gültigen Monat auswählen.')
    weeks = month_calendar.Calendar(firstweekday=0).monthdatescalendar(chosen.year, chosen.month)
    rows, errors = agenda(request.user, aware(weeks[0][0]), aware(weeks[-1][-1] + timedelta(days=1)))
    days = []
    for week in weeks:
        cells = []
        for day in week:
            start, end = aware(day), aware(day + timedelta(days=1))
            entries = [row for row in rows if row['start'] < end and
                (row['end'] > start or row['start'] == row['end'] == start)]
            cells.append(dict(date=day, current=day.month == chosen.month, today=day == today, entries=entries))
        days.append(cells)
    return render(request, 'sedo/calendar/index.html', {
        'month': chosen, 'previous': (chosen - timedelta(days=1)).strftime('%Y-%m'),
        'following': (chosen.replace(day=28) + timedelta(days=4)).strftime('%Y-%m'),
        'weeks': days, 'entries': rows, 'calendar_errors': errors,
        'connection': CalendarConnection.objects.filter(user=request.user).first(),
        'conflicts': CalendarEvent.objects.filter(calendar__user=request.user, state='conflict').select_related('calendar'),
        'deleted': CalendarEvent.objects.filter(calendar__user=request.user, state__in=['deleted', 'deleting']).order_by('-updated_at')[:30],
        'calendars': CalendarCollection.objects.filter(user=request.user, selected=True),
        'list_view': request.GET.get('view') == 'list',
    })


@login_required
@never_cache
def edit(request, pk=None):
    event = owned_event(request.user, pk) if pk else None
    now = timezone.localtime().replace(second=0, microsecond=0)
    initial = dict(revision=0, start_date=now.date(), end_date=(now + timedelta(hours=1)).date(),
        start_time=now.time(), end_time=(now + timedelta(hours=1)).time())
    if not event and request.GET.get('date'):
        try:
            initial['start_date'] = initial['end_date'] = date.fromisoformat(request.GET['date'])
        except ValueError:
            pass
    form = CalendarEventForm(request.POST or None, user=request.user, event=event, initial=initial)
    if request.method == 'POST' and form.is_valid():
        try:
            chosen = event.calendar if event else form.cleaned_data['calendar']
            connection_id = chosen.connection_id if chosen else None
            with (connection_lease(connection_id) if connection_id else nullcontext()), transaction.atomic():
                if event:
                    event = owned_event(request.user, event.pk)
                    require_editable(event)
                    if not CalendarEvent.objects.filter(pk=event.pk, revision=revision(request.POST)).update(revision=F('revision') + 1):
                        raise CalendarError('Der Termin wurde zwischenzeitlich geändert. Bitte neu öffnen.')
                    event.refresh_from_db()
                else:
                    if chosen:
                        chosen.refresh_from_db()
                        if not chosen.selected or not chosen.available or not chosen.writable:
                            raise CalendarError('Dieser Kalender ist momentan nicht beschreibbar.')
                    else:
                        chosen, _ = CalendarCollection.objects.get_or_create(user=request.user, remote_url='',
                            defaults={'name': 'SEDO · nur lokal', 'selected': True})
                    uid = f"{form.cleaned_data['creation_key']}@sedo.local"
                    if chosen.events.filter(uid=uid).exists():
                        raise CalendarError('Dieser Termin wurde bereits gespeichert. Bitte im Kalender öffnen.')
                    event = CalendarEvent(calendar=chosen, uid=uid)
                for key in ('title', 'location', 'description', 'all_day', 'start', 'end', 'customer'):
                    setattr(event, key, form.cleaned_data[key])
                event.timezone_name = 'Europe/Berlin'
                event.raw_ical = render_event(event)
                event.state = 'pending' if event.calendar.connection_id else 'clean'
                event.updated_at = timezone.now()
                event.save()
            if connection_id:
                request_sync(connection_id)
            messages.success(request, 'Termin gespeichert.')
            return redirect('sedo:calendar_detail', pk=event.pk)
        except CalendarError as exc:
            form.add_error(None, str(exc))
    return render(request, 'sedo/calendar/form.html', {'form': form, 'event': event})


@login_required
@never_cache
def detail(request, pk):
    event = owned_event(request.user, pk)
    remote = None
    if event.remote_ical:
        try:
            remote = fields(event.remote_ical)
        except CalendarError:
            pass
    editable = True
    try:
        require_editable(event)
    except CalendarError:
        editable = False
    return render(request, 'sedo/calendar/detail.html', {'event': event, 'remote': remote, 'editable': editable,
        'last_day': event.end.astimezone(ZONE).date() - timedelta(days=1) if event.all_day else None})


@login_required
@require_POST
def event_action(request, pk):
    event = owned_event(request.user, pk)
    action = request.POST.get('action')
    try:
        if request.POST.get('confirm') != 'yes':
            raise CalendarError('Bitte die gewählte Änderung bestätigen.')
        with event_lock(event), transaction.atomic():
            event = owned_event(request.user, pk)
            if event.revision != revision(request.POST):
                raise CalendarError('Der Termin wurde zwischenzeitlich geändert. Bitte neu öffnen.')
            if action == 'delete':
                require_editable(event)
                event.deleted_locally = True
                event.state = 'deleting' if event.calendar.connection_id and event.remote_url else 'deleted'
            elif action in ('keep_local', 'keep_remote'):
                if event.state != 'conflict':
                    raise CalendarError('Es liegt kein Änderungskonflikt vor.')
                if action == 'keep_remote':
                    if event.remote_deleted:
                        mark_deleted(event)
                    else:
                        apply_remote(event, event.remote_ical, event.remote_etag)
                else:
                    remote_fields = fields(event.remote_ical) if event.remote_ical else {}
                    if (event.read_only_reason or remote_fields.get('read_only_reason') or not event.calendar.writable
                            or not event.calendar.selected or not event.calendar.available):
                        raise CalendarError('Diese Fassung kann nicht zu iCloud übertragen werden. Bitte in Apple Kalender bearbeiten.')
                    event.etag, event.base_ical = event.remote_etag, event.remote_ical
                    event.state = 'deleting' if event.deleted_locally else 'pending'
                    event.remote_ical = event.remote_etag = event.error = ''
                    event.remote_deleted = False
            else:
                raise CalendarError('Unbekannte Kalenderaktion.')
            event.revision += 1
            event.updated_at = timezone.now()
            event.save()
        if event.calendar.connection_id:
            request_sync(event.calendar.connection_id)
        messages.success(request, 'Kalenderänderung gespeichert.')
    except CalendarError as exc:
        messages.error(request, str(exc))
    return redirect('sedo:calendar_detail', pk=pk)


@login_required
@never_cache
@sensitive_post_parameters('password')
def settings_page(request):
    connection = CalendarConnection.objects.filter(user=request.user).first()
    action = request.POST.get('action')
    form = CalendarConnectionForm(request.POST if action in ('credentials', 'save_discover') else None,
        connection=connection)
    if request.method == 'POST':
        credentials_saved = False
        discovery_attempted = False
        try:
            if action in ('credentials', 'save_discover') and form.is_valid():
                # Beim erneuten Laden ohne neue Eingabe den laufenden Abgleich
                # nicht pausieren. Ein eingetipptes Passwort wird dagegen vor
                # der Verbindung gespeichert und genau dafür verwendet.
                should_save = (action == 'credentials' or not connection
                               or bool(form.cleaned_data['password'])
                               or form.cleaned_data['username'] != connection.username)
                if should_save:
                    with (connection_lease(connection.pk) if connection else nullcontext()), transaction.atomic():
                        if connection:
                            connection.refresh_from_db()
                            if connection.revision != revision(request.POST):
                                raise CalendarError('Die Einstellungen wurden zwischenzeitlich geändert. Bitte neu öffnen.')
                            if form.cleaned_data['username'] != connection.username and connection.calendars.exists():
                                raise CalendarError('Der Apple-Account ist bereits mit Kalendern verknüpft und kann hier nicht gewechselt werden.')
                            connection.username = form.cleaned_data['username']
                        else:
                            connection = CalendarConnection(user=request.user, username=form.cleaned_data['username'])
                        if form.cleaned_data['password']:
                            connection.password_encrypted = encrypt(connection, form.cleaned_data['password'])
                        connection.enabled = False
                        connection.last_error = ''
                        connection.revision += 1
                        connection.save()
                    credentials_saved = True
                if action == 'save_discover':
                    expected_revision = connection.revision if credentials_saved else revision(request.POST)
                    with connection_lease(connection.pk):
                        connection.refresh_from_db()
                        if connection.revision != expected_revision:
                            raise CalendarError('Die Einstellungen wurden zwischenzeitlich geändert. Bitte neu öffnen.')
                        discovery_attempted = True
                        count = discover(connection)
                        CalendarConnection.objects.filter(pk=connection.pk).update(last_error='')
                    messages.success(request, f'{count} iCloud-Kalender gefunden. Bitte Kalender auswählen.')
                else:
                    messages.success(request, 'Zugang gespeichert. Jetzt Kalender laden und für den Abgleich auswählen.')
                return redirect('sedo:calendar_settings')
            elif action in ('discover', 'selection', 'pause'):
                if not connection:
                    raise CalendarError('Bitte zuerst den iCloud-Zugang einrichten.')
                with connection_lease(connection.pk):
                    connection.refresh_from_db()
                    if connection.revision != revision(request.POST):
                        raise CalendarError('Die Einstellungen wurden zwischenzeitlich geändert. Bitte neu öffnen.')
                    if action == 'discover':
                        discovery_attempted = True
                        count = discover(connection)
                        CalendarConnection.objects.filter(pk=connection.pk).update(last_error='')
                        messages.success(request, f'{count} iCloud-Kalender gefunden.')
                    elif action == 'selection':
                        if request.POST.get('confirm') != 'yes':
                            raise CalendarError('Bitte den beidseitigen Abgleich der ausgewählten Kalender bestätigen.')
                        ids = request.POST.getlist('calendar')
                        allowed = set(str(pk) for pk in connection.calendars.filter(user=request.user, available=True).values_list('pk', flat=True))
                        if not ids or not set(ids) <= allowed:
                            raise CalendarError('Bitte mindestens einen verfügbaren eigenen Kalender auswählen.')
                        if not connection.password_encrypted:
                            raise CalendarError('Bitte den iCloud-Zugang erneut einrichten.')
                        with transaction.atomic():
                            connection.calendars.update(selected=False)
                            connection.calendars.filter(pk__in=ids).update(selected=True)
                            connection.enabled = True
                            connection.revision += 1
                            connection.save(update_fields=['enabled', 'revision'])
                        messages.success(request, 'Kalenderauswahl gespeichert. Der beidseitige Abgleich ist aktiv.')
                    else:
                        connection.enabled = False
                        connection.revision += 1
                        connection.save(update_fields=['enabled', 'revision'])
                        messages.success(request, 'iCloud-Abgleich pausiert. Die gespeicherten Termine bleiben verfügbar.')
                if action == 'selection':
                    request_sync(connection.pk)
                return redirect('sedo:calendar_settings')
        except CalendarError as exc:
            if discovery_attempted and connection:
                CalendarConnection.objects.filter(pk=connection.pk).update(last_error=str(exc))
                connection.refresh_from_db()
            if credentials_saved:
                form = CalendarConnectionForm(connection=connection)
            messages.error(request, str(exc))
        except IntegrityError:
            messages.error(request, 'Die Einrichtung wurde bereits in einem anderen Fenster gespeichert. Bitte neu öffnen.')
    return render(request, 'sedo/calendar/settings.html', {'form': form, 'connection': connection,
        'calendars': connection.calendars.filter(user=request.user) if connection else []})


@login_required
@require_POST
def sync_now(request):
    connection = get_object_or_404(CalendarConnection, user=request.user)
    if not connection.enabled:
        messages.error(request, 'Bitte den iCloud-Abgleich zuerst in den Kalendereinstellungen aktivieren.')
    else:
        request_sync(connection.pk)
        messages.success(request, 'Der Kalenderabgleich läuft im Hintergrund. Zum Aktualisieren die Kalenderansicht erneut öffnen.')
    return redirect('sedo:calendar_index')
