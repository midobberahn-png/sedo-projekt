"""Fixed widget catalogue and personal, validated dashboard preferences."""
import re

from django import forms

from .models import DashboardPreference


WIDGETS = (
    ('mail', 'Neue E-Mails', 'Ungelesene Nachrichten aus allen Posteingängen', 'i-mail', False),
    ('spam', 'Spam prüfen', 'Ungelesene Spam- und Verdachtsfälle', 'i-mail', False),
    ('balance', 'Kontostand', 'Zuletzt von der Bank abgerufener Saldo', 'i-payment', False),
    ('weather', 'Wetter', 'Wetter.com für deinen gewählten Ort', 'i-sun', False),
    ('drafts', 'Rechnungsentwürfe', 'Noch fertigzustellende Rechnungen', 'i-invoice', False),
    ('overdue', 'Überfällige Rechnungen', 'Zahlungseingänge und nächste Schritte', 'i-open', False),
    ('dunning', 'Mahnstufe prüfen', 'Kandidaten für den nächsten Mahnschritt', 'i-repeat', False),
    ('receivables', 'Offene Forderungen', 'Offener Gesamtbetrag und Rechnungsanzahl', 'i-open', False),
    ('paid', 'Bezahlte Rechnungen', 'Bezahlte Belege des aktuellen Rechnungsmonats', 'i-payment', False),
    ('offers', 'Offene Angebote', 'Angebote weiterverfolgen', 'i-offer', False),
    ('tasks', 'Meine Wiedervorlagen', 'Die nächsten sechs offenen Aufgaben', 'i-calendar', True),
    ('invoices', 'Letzte Rechnungen', 'Die acht neuesten Belege', 'i-invoice', True),
    ('customers', 'Kunden & Objekte', 'Stammdaten und Kundenakten', 'i-user', False),
    ('gwg', 'GWG-Abrechnung', 'Mapping und Monatslauf', 'i-building', False),
    ('backup', 'Backup & Sicherung', 'Datensicherung öffnen', 'i-backup', False),
)
WIDGET_KEYS = tuple(row[0] for row in WIDGETS)
DEFAULT_WIDGETS = tuple(key for key in WIDGET_KEYS if key not in ('balance', 'weather', 'drafts', 'spam'))


def layout_for(user):
    preference = DashboardPreference.objects.filter(user=user).first()
    saved = preference.widgets if preference else list(DEFAULT_WIDGETS)
    if not isinstance(saved, list):
        saved = list(DEFAULT_WIDGETS)
    return {
        'widgets': [key for key in WIDGET_KEYS if key in saved],
        'weather_country': preference.weather_country if preference else 'DE',
        'weather_postcode': preference.weather_postcode if preference else '39104',
        'weather_place': preference.weather_place if preference else 'Magdeburg',
    }


def catalogue(selected):
    return [dict(key=key, label=label, description=description, icon=icon, wide=wide,
                 selected=key in selected) for key, label, description, icon, wide in WIDGETS]


class DashboardLayoutForm(forms.Form):
    widgets = forms.MultipleChoiceField(required=False,
        choices=[(row[0], row[1]) for row in WIDGETS], widget=forms.CheckboxSelectMultiple)
    weather_country = forms.ChoiceField(label='Land', choices=(('DE', 'Deutschland'),
        ('AT', 'Österreich'), ('CH', 'Schweiz'), ('LI', 'Liechtenstein')))
    weather_postcode = forms.CharField(label='Postleitzahl', max_length=5)
    weather_place = forms.CharField(label='Ortsname', max_length=80)

    def clean_widgets(self):
        selected = self.cleaned_data['widgets']
        return [key for key in WIDGET_KEYS if key in selected]

    def clean(self):
        data = super().clean()
        country, postcode = data.get('weather_country'), data.get('weather_postcode')
        if country and postcode and not re.fullmatch(r'[0-9]{5}' if country == 'DE' else r'[0-9]{4}', postcode):
            self.add_error('weather_postcode', 'Bitte eine gültige Postleitzahl für das gewählte Land eingeben.')
        return data
