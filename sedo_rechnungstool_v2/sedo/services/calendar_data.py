"""iCalendar conversion; remote data is never executed or fetched as a URL."""
import hashlib
from datetime import date, datetime, time, timedelta
from itertools import islice
from zoneinfo import ZoneInfo

from django.utils import timezone
from icalendar import Calendar, Event
import recurring_ical_events

MAX_ICAL = 512 * 1024
ZONE = ZoneInfo('Europe/Berlin')


class CalendarError(Exception):
    pass


def aware(value, zone=ZONE):
    if not isinstance(value, datetime):
        value = datetime.combine(value, time.min)
    return value.replace(tzinfo=zone) if timezone.is_naive(value) else value


def parsed(raw):
    if not raw or len(raw.encode('utf-8')) > MAX_ICAL:
        raise CalendarError('Der Termin ist leer oder zu groß.')
    try:
        cal = Calendar.from_ical(raw)
        events = cal.walk('VEVENT')
        if (cal.name != 'VCALENDAR' or not events or len(events) > 500
                or any(item.errors for item in cal.walk())
                or len({str(item.get('UID', '')) for item in events}) != 1):
            raise ValueError()
        uid = str(events[0].get('UID', ''))
        if not uid or len(uid) > 1024 or any(char in uid for char in '\r\n\x00'):
            raise ValueError()
        # Bound recurrence work before invoking the expansion library.
        for item in events:
            rule = item.get('RRULE')
            if rule:
                if isinstance(rule, list) or any(len(values) > 64 for values in rule.values()):
                    raise CalendarError('Diese komplexe Terminserie bitte in Apple Kalender ansehen.')
                if str(rule.get('FREQ', [''])[0]) not in ('DAILY', 'WEEKLY', 'MONTHLY', 'YEARLY'):
                    raise CalendarError('Diese Terminserie ist zu häufig. Bitte in Apple Kalender ansehen.')
                for key in ('BYHOUR', 'BYMINUTE', 'BYSECOND'):
                    if len(rule.get(key, [])) > 1:
                        raise CalendarError('Diese komplexe Terminserie bitte in Apple Kalender ansehen.')
                if int(rule.get('INTERVAL', [1])[0]) < 1:
                    raise CalendarError('Die Terminserie enthält einen ungültigen Abstand.')
        return cal, events
    except CalendarError:
        raise
    except Exception as exc:
        raise CalendarError('Die Kalenderdaten dieses Termins konnten nicht gelesen werden.') from exc


def fields(raw):
    cal, events = parsed(raw)
    master = next((item for item in events if 'RECURRENCE-ID' not in item), events[0])
    try:
        start = master.decoded('DTSTART')
        if not isinstance(start, date):
            raise ValueError()
        all_day = not isinstance(start, datetime)
        if isinstance(start, datetime) and timezone.is_naive(start) and master['DTSTART'].params.get('TZID'):
            raise CalendarError('Die Zeitzone des Termins ist unbekannt. Bitte in Apple Kalender prüfen.')
        end = master.decoded('DTEND', None)
        if end is None:
            end = start + master.decoded('DURATION', timedelta(days=1) if all_day else timedelta())
        if not isinstance(end, date) or isinstance(end, datetime) == all_day:
            raise ValueError()
        if not 1900 <= start.year <= 2200 or not 1900 <= end.year <= 2200:
            raise ValueError()
        zone = ZoneInfo(str(cal.get('X-WR-TIMEZONE', 'Europe/Berlin')))
        start, end = aware(start, zone), aware(end, zone)
        if end < start:
            raise ValueError()
        reason = ''
        recurring = any(key in master for key in ('RRULE', 'RDATE')) or len(events) > 1
        if cal.get('METHOD') or any('ATTENDEE' in item or 'ORGANIZER' in item for item in events):
            reason = 'Einladungen bitte in Apple Kalender bearbeiten.'
        elif recurring or 'RECURRENCE-ID' in master:
            reason = 'Diese Terminserie bitte in Apple Kalender bearbeiten.'
        if any(len(str(master.get(key, ''))) > length for key, length in
               (('SUMMARY', 250), ('LOCATION', 500), ('DESCRIPTION', 10000))):
            reason = 'Dieser Termin enthält umfangreiche Angaben. Bitte in Apple Kalender bearbeiten.'
        return dict(uid=str(master['UID']), title=str(master.get('SUMMARY', 'Ohne Titel'))[:250],
            location=str(master.get('LOCATION', ''))[:500], description=str(master.get('DESCRIPTION', ''))[:10000],
            start=start, end=end, all_day=all_day, timezone_name=str(getattr(start.tzinfo, 'key', 'Europe/Berlin')),
            recurring=recurring, read_only_reason=reason)
    except CalendarError:
        raise
    except Exception as exc:
        raise CalendarError('Beginn oder Ende des Termins ist ungültig.') from exc


def render_event(event):
    if event.raw_ical:
        cal, events = parsed(event.raw_ical)
        if fields(event.raw_ical)['read_only_reason']:
            raise CalendarError('Diesen Termin bitte in Apple Kalender bearbeiten.')
        item = events[0]
    else:
        cal, item = Calendar(), Event()
        cal.add('VERSION', '2.0')
        cal.add('PRODID', '-//SEDO//Kalender//DE')
        cal.add_component(item)
        item.add('UID', event.uid)
    for key in ('SUMMARY', 'LOCATION', 'DESCRIPTION', 'DTSTART', 'DTEND', 'DURATION', 'DTSTAMP', 'LAST-MODIFIED', 'SEQUENCE'):
        item.pop(key, None)
    item.add('SUMMARY', event.title)
    item.add('LOCATION', event.location)
    item.add('DESCRIPTION', event.description)
    zone = ZoneInfo(event.timezone_name)
    start, end = event.start.astimezone(zone), event.end.astimezone(zone)
    item.add('DTSTART', start.date() if event.all_day else start.astimezone(ZoneInfo('UTC')))
    item.add('DTEND', end.date() if event.all_day else end.astimezone(ZoneInfo('UTC')))
    item.add('DTSTAMP', timezone.now())
    item.add('LAST-MODIFIED', timezone.now())
    item.add('SEQUENCE', event.revision)
    return cal.to_ical().decode('utf-8')


def fingerprint(raw):
    cal, _ = parsed(raw)
    return hashlib.sha256(cal.to_ical()).hexdigest()


def occurrences(events, start, end, limit=600):
    result, errors = [], []
    for event in events:
        try:
            cal, _ = parsed(event.raw_ical)
            if event.recurring:
                rows = list(islice(recurring_ical_events.of(cal).after(start), 501))
            else:
                rows = cal.walk('VEVENT')
            for item in rows:
                begins = aware(item.decoded('DTSTART'))
                default_duration = timedelta(days=1) if not isinstance(item.decoded('DTSTART'), datetime) else timedelta()
                finishes = aware(item.decoded('DTEND', None) or (
                    item.decoded('DTSTART') + item.decoded('DURATION', default_duration)))
                if begins >= end:
                    break
                if finishes < start or (finishes == start and begins != finishes) or str(item.get('STATUS', '')) == 'CANCELLED':
                    continue
                result.append(dict(event=event, title=str(item.get('SUMMARY', event.title))[:250],
                    start=begins, end=finishes, all_day=not isinstance(item.decoded('DTSTART'), datetime)))
                if len(result) >= 20000:
                    return sorted(result, key=lambda row: (row['start'], row['event'].pk))[:limit], ['Weitere Termine bitte im Kalender ansehen.']
        except Exception:
            errors.append(f'„{event.title}“ konnte nicht vollständig angezeigt werden.')
    return sorted(result, key=lambda row: (row['start'], row['event'].pk))[:limit], errors


def visible_events(user):
    from django.db.models import Q
    from sedo.models import CalendarEvent
    return CalendarEvent.objects.filter(calendar__user=user, calendar__selected=True).exclude(
        state__in=['deleted', 'deleting']).select_related('calendar').filter(
        Q(calendar__connection__isnull=True) | Q(calendar__connection__user=user))


def agenda(user, start, end, limit=600):
    from django.db.models import Q
    events = visible_events(user).filter(Q(recurring=True) | Q(start__lt=end, end__gte=start))
    selected = list(events[:2001])
    rows, errors = occurrences(selected[:2000], start, end, limit)
    if len(selected) > 2000:
        errors.append('Sehr viele Termine: Die Ansicht ist begrenzt. Bitte weniger iCloud-Kalender auswählen.')
    return rows, errors
