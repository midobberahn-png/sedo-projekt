from datetime import datetime, time, timedelta
import uuid

from django import forms
from django.utils import timezone

from .models import CalendarCollection, Customer
from .services.calendar_data import ZONE


class CalendarEventForm(forms.Form):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    creation_key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)
    calendar = forms.ModelChoiceField(label='Kalender', queryset=CalendarCollection.objects.none(), required=False,
        empty_label='SEDO · nur lokal')
    title = forms.CharField(label='Termin', max_length=250)
    start_date = forms.DateField(label='Beginn am', widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))
    start_time = forms.TimeField(label='Uhrzeit', required=False, widget=forms.TimeInput(attrs={'type': 'time'}, format='%H:%M'))
    end_date = forms.DateField(label='Ende am', widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))
    end_time = forms.TimeField(label='Uhrzeit', required=False, widget=forms.TimeInput(attrs={'type': 'time'}, format='%H:%M'))
    all_day = forms.BooleanField(label='Ganztägig', required=False)
    location = forms.CharField(label='Ort', required=False, max_length=500)
    description = forms.CharField(label='Notiz', required=False, max_length=10000, widget=forms.Textarea(attrs={'rows': 5}))
    customer = forms.ModelChoiceField(label='Kunde (nur in SEDO sichtbar)', queryset=Customer.objects.all(), required=False)

    def __init__(self, *args, user, event=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['calendar'].queryset = CalendarCollection.objects.filter(user=user, selected=True,
            available=True, writable=True, connection__user=user)
        if not event:
            preferred = self.fields['calendar'].queryset.first()
            if preferred:
                self.initial['calendar'] = preferred.pk
        if event:
            # Moving an event between servers/calendars is a separate workflow.
            self.fields['calendar'].queryset = CalendarCollection.objects.filter(pk=event.calendar_id, user=user)
            self.fields['calendar'].disabled = True
            start, end = event.start.astimezone(ZONE), event.end.astimezone(ZONE)
            self.initial.update(revision=event.revision, calendar=event.calendar_id, title=event.title,
                start_date=start.date(), start_time=start.time(),
                end_date=(end.date() - timedelta(days=1)) if event.all_day else end.date(),
                end_time=end.time(), all_day=event.all_day, location=event.location,
                description=event.description, customer=event.customer_id)

    def clean(self):
        data = super().clean()
        if not data.get('start_date') or not data.get('end_date'):
            return data
        if not data.get('all_day') and (data.get('start_time') is None or data.get('end_time') is None):
            raise forms.ValidationError('Bitte Beginn und Ende mit Uhrzeit angeben oder „Ganztägig“ auswählen.')
        first, last = data['start_date'], data['end_date']
        if not 1900 <= first.year <= 2199 or not 1900 <= last.year <= 2199:
            raise forms.ValidationError('Bitte ein Datum zwischen 1900 und 2199 wählen.')
        first = datetime.combine(first, time.min if data.get('all_day') else data['start_time'])
        last = datetime.combine(last, time.min if data.get('all_day') else data['end_time'])
        if data.get('all_day'):
            last += timedelta(days=1)
        # Django rejects ambiguous and nonexistent local times at DST transitions.
        parser = forms.DateTimeField()
        with timezone.override(ZONE):
            data['start'], data['end'] = parser.clean(first), parser.clean(last)
        if data['end'] <= data['start'] or data['end'] - data['start'] > timedelta(days=366):
            raise forms.ValidationError('Das Ende muss nach dem Beginn liegen; ein Termin darf höchstens ein Jahr dauern.')
        return data


class CalendarConnectionForm(forms.Form):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    username = forms.EmailField(label='Apple-Account', max_length=254)
    password = forms.CharField(label='App-spezifisches Passwort', required=False, max_length=1000,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}))

    def __init__(self, *args, connection=None, **kwargs):
        self.connection = connection
        super().__init__(*args, **kwargs)
        self.initial['revision'] = connection.revision if connection else 0
        if connection:
            self.initial['username'] = connection.username
            self.fields['username'].disabled = True

    def clean(self):
        data = super().clean()
        if not data.get('password') and (not self.connection or not self.connection.password_encrypted):
            self.add_error('password', 'Bitte ein App-spezifisches Passwort eingeben.')
        return data
