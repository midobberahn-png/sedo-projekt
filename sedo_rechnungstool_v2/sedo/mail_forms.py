from django import forms

from .models import MailAccount, MailDraft, WorkTask
from .services.mail_mime import addresses
from .services.mail_secrets import MailError


class MailAccountForm(forms.ModelForm):
    password = forms.CharField(label='Postfach-Passwort', required=False, max_length=1000,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
        help_text='Leer lassen, um das gespeicherte Passwort zu behalten. Nicht das SEDO-Anmeldepasswort verwenden.')

    class Meta:
        model = MailAccount
        fields = ['email', 'display_name', 'username', 'password', 'signature', 'enabled', 'smtp_port', 'smtp_security']
        labels = {'smtp_port': 'SMTP-Port', 'smtp_security': 'SMTP-Verschlüsselung'}
        widgets = {'signature': forms.Textarea(attrs={'rows': 5})}

    def clean(self):
        data = super().clean()
        email = data.get('email', '').lower()
        data['email'] = email
        if not data.get('password'):
            original = MailAccount.objects.filter(pk=self.instance.pk).first()
            if not original or not original.password_encrypted:
                self.add_error('password', 'Bitte das Passwort dieses Postfachs eingeben.')
            elif original.email != email or original.username != data.get('username'):
                self.add_error('password', 'Nach Änderung der Postfach-Adresse oder des Benutzernamens bitte das Passwort erneut eingeben.')
        if (data.get('smtp_port'), data.get('smtp_security')) not in ((465, 'ssl'), (587, 'starttls')):
            self.add_error('smtp_port', 'United Domains: 465 mit SSL/TLS oder 587 mit STARTTLS wählen.')
        for field in ('username', 'display_name'):
            if any(char in data.get(field, '') for char in '\r\n\x00'):
                self.add_error(field, 'Zeilenumbrüche sind hier nicht erlaubt.')
        return data


class MailDraftForm(forms.ModelForm):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    creation_key = forms.UUIDField(widget=forms.HiddenInput)

    class Meta:
        model = MailDraft
        fields = ['account', 'to', 'cc', 'bcc', 'subject', 'body']
        labels = {'account': 'Senden von'}
        widgets = {name: forms.TextInput() for name in ('to', 'cc', 'bcc')}
        widgets['body'] = forms.Textarea(attrs={'rows': 18, 'spellcheck': 'true'})

    def clean(self):
        data = super().clean()
        for field in ('to', 'cc', 'bcc'):
            try:
                addresses(data.get(field, ''))
            except MailError as exc:
                self.add_error(field, str(exc))
        if any(char in data.get('subject', '') for char in '\r\n\x00'):
            self.add_error('subject', 'Der Betreff darf keinen Zeilenumbruch enthalten.')
        return data


class MailTaskForm(forms.ModelForm):
    class Meta:
        model = WorkTask
        fields = ['title', 'note', 'due_date', 'priority']
        widgets = {'due_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
                   'note': forms.Textarea(attrs={'rows': 5})}
