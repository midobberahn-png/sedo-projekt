"""Personal calendars and durable synchronization state, separate from billing."""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


def event_uid():
    return f'{uuid.uuid4()}@sedo.local'


class CalendarConnection(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    username = models.EmailField('Apple-Account')
    password_encrypted = models.TextField(blank=True, editable=False)
    enabled = models.BooleanField(default=False)
    revision = models.PositiveIntegerField(default=1)
    last_sync = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True)
    lease_token = models.CharField(max_length=36, blank=True, editable=False)
    lease_until = models.DateTimeField(null=True, editable=False)


class CalendarCollection(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    connection = models.ForeignKey(CalendarConnection, null=True, blank=True,
                                   on_delete=models.PROTECT, related_name='calendars')
    name = models.CharField('Kalender', max_length=180)
    remote_url = models.CharField(max_length=1500, blank=True)
    selected = models.BooleanField(default=False)
    available = models.BooleanField(default=True)
    writable = models.BooleanField(default=True)

    class Meta:
        ordering = ['name', 'pk']
        constraints = [models.UniqueConstraint(fields=['user', 'remote_url'], name='calendar_owner_url_unique')]

    def __str__(self):
        return self.name


class CalendarEvent(models.Model):
    calendar = models.ForeignKey(CalendarCollection, on_delete=models.PROTECT, related_name='events')
    uid = models.CharField(max_length=1024, default=event_uid)
    title = models.CharField('Termin', max_length=250)
    location = models.CharField('Ort', max_length=500, blank=True)
    description = models.TextField('Notiz', max_length=10000, blank=True)
    start = models.DateTimeField('Beginn')
    end = models.DateTimeField('Ende')
    all_day = models.BooleanField('Ganztägig', default=False)
    timezone_name = models.CharField(max_length=100, default='Europe/Berlin')
    recurring = models.BooleanField(default=False)
    read_only_reason = models.CharField(max_length=240, blank=True)
    customer = models.ForeignKey('sedo.Customer', null=True, blank=True, on_delete=models.SET_NULL)
    raw_ical = models.TextField(blank=True, editable=False)
    remote_url = models.CharField(max_length=1500, blank=True, editable=False)
    etag = models.CharField(max_length=500, blank=True, editable=False)
    base_ical = models.TextField(blank=True, editable=False)
    state = models.CharField(max_length=12, default='clean', choices=[
        ('clean', 'Gespeichert'), ('pending', 'Wartet auf Abgleich'),
        ('deleting', 'Löschung vorgemerkt'), ('conflict', 'Änderungen vergleichen'),
        ('deleted', 'Gelöscht')])
    revision = models.PositiveIntegerField(default=1)
    remote_ical = models.TextField(blank=True, editable=False)
    remote_etag = models.CharField(max_length=500, blank=True, editable=False)
    remote_deleted = models.BooleanField(default=False, editable=False)
    deleted_locally = models.BooleanField(default=False, editable=False)
    pending_ical = models.TextField(blank=True, editable=False)
    pending_operation = models.CharField(max_length=6, blank=True, editable=False)
    error = models.CharField(max_length=300, blank=True)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['start', 'pk']
        indexes = [models.Index(fields=['calendar', 'state', 'start'], name='calendar_state_start_idx')]
        constraints = [
            models.UniqueConstraint(fields=['calendar', 'uid'], name='calendar_event_uid_unique'),
            models.CheckConstraint(condition=models.Q(end__gte=models.F('start')), name='calendar_event_date_order'),
        ]

    def __str__(self):
        return self.title
