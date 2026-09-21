import re
from django import forms
from .models import IncomingInvoice


class IncomingInvoiceForm(forms.ModelForm):
    revision = forms.IntegerField(widget=forms.HiddenInput)
    confirm = forms.BooleanField(label='Original geprüft: Angaben stimmen und dieser Beleg ist eine Eingangsrechnung oder Gutschrift.', required=False)

    class Meta:
        model = IncomingInvoice
        fields = ['supplier', 'number', 'document_date', 'gross', 'currency', 'note']
        widgets = {'document_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
                   'note': forms.Textarea(attrs={'rows': 3})}

    def clean_currency(self):
        value = self.cleaned_data['currency'].upper()
        if not re.fullmatch('[A-Z]{3}', value):
            raise forms.ValidationError('Bitte einen dreistelligen Währungscode eingeben, z. B. EUR.')
        return value

    def clean(self):
        data = super().clean()
        if self.data.get('action') == 'approve':
            for key in ('supplier', 'number', 'document_date', 'gross'):
                if data.get(key) in ('', None):
                    self.add_error(key, 'Für die Freigabe erforderlich.')
            if not data.get('confirm'):
                self.add_error('confirm', 'Bitte zuerst das Original prüfen und die Angaben bestätigen.')
        return data
