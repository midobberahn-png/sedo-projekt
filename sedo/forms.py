from decimal import Decimal
from django import forms
from django.forms import inlineformset_factory
from .models import Customer, CustomerObject, CustomerNote, Invoice, InvoiceItem, Offer, OfferItem, Payment, CashbookEntry, RecurringInvoiceTemplate, RecurringInvoiceItem, ServiceCatalogItem, WorkTask

CUSTOMER_WIDGET_CLASS = {'class': 'form-control form-control-sm'}
SELECT_WIDGET_CLASS = {'class': 'form-select form-select-sm'}


class CompanyProfileForm(forms.Form):
    """Only local administrator-entered identity; no built-in business values."""
    def __init__(self, *args, **kwargs):
        from .company_profile import PROFILE_FIELDS
        super().__init__(*args, **kwargs)
        for name, (label, max_length, required) in PROFILE_FIELDS.items():
            self.fields[name] = forms.CharField(
                label=label, max_length=max_length, required=required,
                strip=False, widget=forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS))

    def clean(self):
        from .company_profile import SellerProfileError, validate_profile
        values = super().clean()
        if self.errors:
            return values
        try:
            return validate_profile(values)
        except SellerProfileError as exc:
            raise forms.ValidationError(str(exc)) from None

class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = ['salutation','customer_no','customer_type','name','name2','contact_person','phone','email','street','postcode','city','country','payment_days','aareon_receiver_id']
        widgets = {f: forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS) for f in ['customer_no','name','name2','contact_person','phone','email','street','postcode','city','country','aareon_receiver_id']}
        widgets.update({'salutation': forms.Select(attrs=SELECT_WIDGET_CLASS), 'customer_type': forms.Select(attrs=SELECT_WIDGET_CLASS), 'payment_days': forms.NumberInput(attrs=CUSTOMER_WIDGET_CLASS)})

class CustomerObjectForm(forms.ModelForm):
    class Meta:
        model = CustomerObject
        fields = ['customer','name','street','postcode','city','country']
        widgets = {f: forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS) for f in ['name','street','postcode','city','country']}
        widgets['customer'] = forms.Select(attrs=SELECT_WIDGET_CLASS)

class InvoiceForm(forms.ModelForm):
    customer = forms.ModelChoiceField(
        label='Gespeicherten Kunden auswählen (optional)',
        queryset=Customer.objects.all(), required=False,
        widget=forms.Select(attrs=SELECT_WIDGET_CLASS)
    )
    obj = forms.ModelChoiceField(
        label='Gespeichertes Objekt auswählen (optional)',
        queryset=CustomerObject.objects.select_related('customer').all(), required=False,
        widget=forms.Select(attrs=SELECT_WIDGET_CLASS)
    )
    payment_days = forms.IntegerField(label='Zahlungsziel Tage', initial=14, min_value=0, widget=forms.NumberInput(attrs=CUSTOMER_WIDGET_CLASS))

    # Rechnungskopf / Empfänger manuell befüllbar
    salutation = forms.ChoiceField(label='Anrede', choices=Customer.SALUTATIONS, required=False, widget=forms.Select(attrs=SELECT_WIDGET_CLASS))
    customer_no = forms.CharField(label='Kunden-Nr.', required=False, widget=forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS))
    customer_type = forms.ChoiceField(label='Kundentyp', choices=Customer.CUSTOMER_TYPES, required=False, initial='einmalig', widget=forms.Select(attrs=SELECT_WIDGET_CLASS))
    name = forms.CharField(label='Name / Firma', required=False, widget=forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS))
    name2 = forms.CharField(label='Name Zusatz', required=False, widget=forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS))
    contact_person = forms.CharField(label='Ansprechpartner', required=False, widget=forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS))
    phone = forms.CharField(label='Telefon', required=False, widget=forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS))
    email = forms.EmailField(label='E-Mail', required=False, widget=forms.EmailInput(attrs=CUSTOMER_WIDGET_CLASS))
    street = forms.CharField(label='Straße', required=False, widget=forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS))
    postcode = forms.CharField(label='PLZ', required=False, widget=forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS))
    city = forms.CharField(label='Stadt', required=False, widget=forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS))
    country = forms.CharField(label='Land', required=False, initial='DE', widget=forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS))

    # Objekt / Leistungsort manuell befüllbar
    object_name = forms.CharField(label='Objekt / Leistungsort', required=False, widget=forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS))
    object_street = forms.CharField(label='Objekt Straße', required=False, widget=forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS))
    object_postcode = forms.CharField(label='Objekt PLZ', required=False, widget=forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS))
    object_city = forms.CharField(label='Objekt Stadt', required=False, widget=forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS))
    object_country = forms.CharField(label='Objekt Land', required=False, initial='DE', widget=forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS))

    class Meta:
        model = Invoice
        fields = ['customer','obj','issue_date','period_start','period_end','vat_rate','note']
        labels = {'customer': 'Gespeicherter Kunde', 'obj': 'Gespeichertes Objekt'}
        widgets = {
            'issue_date': forms.DateInput(attrs={'type':'date','class':'form-control form-control-sm'}, format='%Y-%m-%d'),
            'period_start': forms.DateInput(attrs={'type':'date','class':'form-control form-control-sm'}, format='%Y-%m-%d'),
            'period_end': forms.DateInput(attrs={'type':'date','class':'form-control form-control-sm'}, format='%Y-%m-%d'),
            'vat_rate': forms.NumberInput(attrs={'class':'form-control form-control-sm','step':'0.01'}),
            'note': forms.Textarea(attrs={'class':'form-control form-control-sm','rows':3}),
        }


class CustomerNoteForm(forms.ModelForm):
    class Meta:
        model = CustomerNote
        fields = ['text']
        widgets = {
            'text': forms.Textarea(attrs={
                'class': 'form-control', 'rows': 3,
                'placeholder': 'Gespräch, Vereinbarung oder nächsten Schritt festhalten …',
                'maxlength': 4000,
            }),
        }


class WorkTaskForm(forms.ModelForm):
    class Meta:
        model = WorkTask
        fields = ['title', 'note', 'due_date', 'priority', 'customer', 'offer', 'invoice']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Was ist als Nächstes zu tun?'}),
            'note': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Optionaler Hintergrund oder Gesprächsnotiz'}),
            'due_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}, format='%Y-%m-%d'),
            'priority': forms.Select(attrs={'class': 'form-select'}),
            'customer': forms.Select(attrs={'class': 'form-select'}),
            'offer': forms.Select(attrs={'class': 'form-select'}),
            'invoice': forms.Select(attrs={'class': 'form-select'}),
        }

    def clean(self):
        cleaned = super().clean()
        refs = [cleaned.get('customer'), cleaned.get('offer'), cleaned.get('invoice')]
        if sum(value is not None for value in refs) > 1:
            raise forms.ValidationError('Bitte höchstens einen Bezug auswählen: Kunde, Angebot oder Rechnung.')
        return cleaned

class InvoiceItemForm(forms.ModelForm):
    class Meta:
        model = InvoiceItem
        fields = ['description','long_text','quantity','unit','unit_price','article_no','leistungsart_id','leistungsart_text']
        widgets = {
            'description': forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS),
            'long_text': forms.Textarea(attrs={'class':'form-control form-control-sm','rows':2,'placeholder':'Leistungsbeschreibung (erscheint im Rechnungs-PDF unter dem Titel)'}),
            'quantity': forms.NumberInput(attrs={'class':'form-control form-control-sm','step':'0.01'}),
            'unit': forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS),
            'unit_price': forms.NumberInput(attrs={'class':'form-control form-control-sm','step':'0.01'}),
            'article_no': forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS),
            'leistungsart_id': forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS),
            'leistungsart_text': forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS),
        }

InvoiceItemFormSet = inlineformset_factory(Invoice, InvoiceItem, form=InvoiceItemForm, extra=0, can_delete=True, min_num=1, validate_min=True)

class OfferForm(forms.ModelForm):
    customer = forms.ModelChoiceField(label='Gespeicherten Kunden auswählen (optional)', queryset=Customer.objects.all(), required=False, widget=forms.Select(attrs=SELECT_WIDGET_CLASS))
    obj = forms.ModelChoiceField(label='Gespeichertes Objekt auswählen (optional)', queryset=CustomerObject.objects.select_related('customer').all(), required=False, widget=forms.Select(attrs=SELECT_WIDGET_CLASS))
    valid_days = forms.IntegerField(label='Gültig in Tagen', initial=14, min_value=0, widget=forms.NumberInput(attrs=CUSTOMER_WIDGET_CLASS))

    salutation = forms.ChoiceField(label='Anrede', choices=Customer.SALUTATIONS, required=False, widget=forms.Select(attrs=SELECT_WIDGET_CLASS))
    customer_no = forms.CharField(label='Kunden-Nr.', required=False, widget=forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS))
    customer_type = forms.ChoiceField(label='Kundentyp', choices=Customer.CUSTOMER_TYPES, required=False, initial='einmalig', widget=forms.Select(attrs=SELECT_WIDGET_CLASS))
    name = forms.CharField(label='Name / Firma', required=False, widget=forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS))
    name2 = forms.CharField(label='Name Zusatz', required=False, widget=forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS))
    contact_person = forms.CharField(label='Ansprechpartner', required=False, widget=forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS))
    phone = forms.CharField(label='Telefon', required=False, widget=forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS))
    email = forms.EmailField(label='E-Mail', required=False, widget=forms.EmailInput(attrs=CUSTOMER_WIDGET_CLASS))
    street = forms.CharField(label='Straße', required=False, widget=forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS))
    postcode = forms.CharField(label='PLZ', required=False, widget=forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS))
    city = forms.CharField(label='Stadt', required=False, widget=forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS))
    country = forms.CharField(label='Land', required=False, initial='DE', widget=forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS))

    object_name = forms.CharField(label='Objekt / Leistungsort', required=False, widget=forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS))
    object_street = forms.CharField(label='Objekt Straße', required=False, widget=forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS))
    object_postcode = forms.CharField(label='Objekt PLZ', required=False, widget=forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS))
    object_city = forms.CharField(label='Objekt Stadt', required=False, widget=forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS))
    object_country = forms.CharField(label='Objekt Land', required=False, initial='DE', widget=forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS))

    class Meta:
        model = Offer
        fields = ['customer','obj','issue_date','vat_rate']
        widgets = {
            'issue_date': forms.DateInput(attrs={'type':'date','class':'form-control form-control-sm'}, format='%Y-%m-%d'),
            'vat_rate': forms.NumberInput(attrs={'class':'form-control form-control-sm','step':'0.01'}),
        }

class OfferItemForm(forms.ModelForm):
    class Meta:
        model = OfferItem
        fields = ['description','long_text','quantity','unit','unit_price','article_no']
        widgets = {
            'description': forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS),
            'long_text': forms.Textarea(attrs={'class':'form-control form-control-sm','rows':2,'placeholder':'Leistungsbeschreibung (erscheint im Angebots-PDF unter dem Titel)'}),
            'quantity': forms.NumberInput(attrs={'class':'form-control form-control-sm','step':'0.01'}),
            'unit': forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS),
            'unit_price': forms.NumberInput(attrs={'class':'form-control form-control-sm','step':'0.01'}),
            'article_no': forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS),
        }

OfferItemFormSet = inlineformset_factory(Offer, OfferItem, form=OfferItemForm, extra=0, can_delete=True, min_num=1, validate_min=True)

class OfferFollowUpForm(forms.ModelForm):
    class Meta:
        model = Offer
        fields = ['follow_up_date', 'follow_up_note']
        widgets = {
            'follow_up_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control form-control-sm'}, format='%Y-%m-%d'),
            'follow_up_note': forms.Textarea(attrs={'class': 'form-control form-control-sm', 'rows': 3, 'placeholder': 'z. B. Ansprechpartner anrufen, offene Frage klären'}),
        }

class BankJsonImportForm(forms.Form):
    file = forms.FileField(label='Bank-JSON-Datei', widget=forms.ClearableFileInput(attrs={'class':'form-control'}))

class ManualPaymentAssignForm(forms.Form):
    """Zuordnung einer Zahlung zu einer Rechnung.

    Das Queryset wird erst zur Laufzeit gesetzt, damit Entwürfe sowie ersetzte
    und stornierte Belege gar nicht erst zur Auswahl stehen (F-07). Bezahlte
    Rechnungen bleiben drin – sonst wäre keine Nach- oder Überzahlung buchbar.
    """

    invoice = forms.ModelChoiceField(label='Rechnung', queryset=Invoice.objects.none(),
                                     widget=forms.Select(attrs={'class': 'form-select'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .services.calc import zuordenbare_rechnungen
        self.fields['invoice'].queryset = zuordenbare_rechnungen().select_related('customer')


class ManualPaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ['booking_date', 'amount', 'payer_name', 'purpose']
        labels = {'booking_date': 'Zahlungsdatum', 'amount': 'Betrag', 'payer_name': 'Zahler / Name', 'purpose': 'Notiz / Verwendungszweck'}
        widgets = {
            'booking_date': forms.DateInput(attrs={'type':'date','class':'form-control form-control-sm'}, format='%Y-%m-%d'),
            'amount': forms.NumberInput(attrs={'class':'form-control form-control-sm','step':'0.01'}),
            'payer_name': forms.TextInput(attrs={'class':'form-control form-control-sm'}),
            'purpose': forms.Textarea(attrs={'class':'form-control form-control-sm','rows':2, 'placeholder':'z. B. manuell als bezahlt markiert / Überweisung / Barzahlung'}),
        }

class CashbookEntryForm(forms.ModelForm):
    booking_no = forms.IntegerField(label='Buchungsnummer', required=False, widget=forms.NumberInput(attrs={'class':'form-control form-control-sm', 'readonly':'readonly'}))
    class Meta:
        model = CashbookEntry
        fields = ['booking_date','booking_no','receipt_no','name','text','amount']
        widgets = {
            'booking_date': forms.DateInput(attrs={'type':'date','class':'form-control form-control-sm'}, format='%Y-%m-%d'),
            'receipt_no': forms.TextInput(attrs={'class':'form-control form-control-sm', 'placeholder':'z. B. Bon 12 / Quittung 03'}),
            'name': forms.TextInput(attrs={'class':'form-control form-control-sm'}),
            'text': forms.Textarea(attrs={'class':'form-control form-control-sm','rows':3}),
            'amount': forms.NumberInput(attrs={'class':'form-control form-control-sm','step':'0.01'}),
        }


class RecurringInvoiceTemplateForm(forms.ModelForm):
    class Meta:
        model = RecurringInvoiceTemplate
        fields = ['customer','obj','title','status','vat_rate','payment_days','note']
        widgets = {
            'customer': forms.Select(attrs=SELECT_WIDGET_CLASS),
            'obj': forms.Select(attrs=SELECT_WIDGET_CLASS),
            'title': forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS),
            'status': forms.Select(attrs=SELECT_WIDGET_CLASS),
            'vat_rate': forms.NumberInput(attrs={'class':'form-control form-control-sm','step':'0.01'}),
            'payment_days': forms.NumberInput(attrs=CUSTOMER_WIDGET_CLASS),
            'note': forms.Textarea(attrs={'class':'form-control form-control-sm','rows':3}),
        }

class RecurringInvoiceItemForm(forms.ModelForm):
    class Meta:
        model = RecurringInvoiceItem
        fields = ['description','long_text','quantity','unit','unit_price','article_no']
        widgets = {
            'description': forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS),
            'long_text': forms.Textarea(attrs={'class':'form-control form-control-sm','rows':2,'placeholder':'Leistungsbeschreibung (optional)'}),
            'quantity': forms.NumberInput(attrs={'class':'form-control form-control-sm','step':'0.01'}),
            'unit': forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS),
            'unit_price': forms.NumberInput(attrs={'class':'form-control form-control-sm','step':'0.01'}),
            'article_no': forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS),
        }

RecurringInvoiceItemFormSet = inlineformset_factory(RecurringInvoiceTemplate, RecurringInvoiceItem, form=RecurringInvoiceItemForm, extra=0, can_delete=True, min_num=1, validate_min=True)


class ServiceCatalogItemForm(forms.ModelForm):
    class Meta:
        model = ServiceCatalogItem
        fields = ['name', 'default_unit', 'long_text']
        widgets = {
            'name': forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS),
            'default_unit': forms.TextInput(attrs=CUSTOMER_WIDGET_CLASS),
            'long_text': forms.Textarea(attrs={'class':'form-control form-control-sm','rows':5}),
        }
