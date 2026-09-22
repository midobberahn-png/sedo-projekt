"""Shared mailbox rules behind the settings gear; explicit batch previews."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from .mail_views import _integer, _shell, mailbox_url, return_query
from .models import AuditLog, MailAccount, MailLocation, MailSpamRule, MailSpamMove
from .services.mail_imap import account_lease, move_message
from .services.mail_secrets import MailError
from .services.mail_spam import assess, audit, correct, destination_for, evaluate, normalize_rule
from .services.mail_worker import request_sync


class SpamSettingsForm(forms.Form):
    revision = forms.IntegerField(widget=forms.HiddenInput(attrs={"id": "spam-settings-revision"}))
    mode = forms.ChoiceField(label='Spamfilter', choices=MailAccount._meta.get_field('spam_mode').choices)
    confirm_auto = forms.BooleanField(required=False, label='Neue Nachrichten dürfen nach meinen Sperrregeln in den Spamordner verschoben werden.')

    def clean(self):
        data = super().clean()
        if data.get('mode') == 'auto' and not data.get('confirm_auto'):
            self.add_error('confirm_auto', 'Bitte die automatische Sortierung ausdrücklich bestätigen.')
        return data


class SpamRuleForm(forms.Form):
    revision = forms.IntegerField(widget=forms.HiddenInput(attrs={"id": "spam-rule-revision"}))
    kind = forms.ChoiceField(label='Gilt für', choices=MailSpamRule._meta.get_field('kind').choices)
    value = forms.CharField(label='Adresse oder Domain', max_length=254)
    action = forms.ChoiceField(label='Behandlung', choices=MailSpamRule._meta.get_field('action').choices)
    confirm_domain = forms.BooleanField(required=False, label='Die Regel soll für alle Absender genau dieser Domain gelten.')

    def clean(self):
        data = super().clean()
        if data.get('kind') and data.get('value'):
            try:
                data['value'] = normalize_rule(data['kind'], data['value'])
            except MailError as exc:
                self.add_error('value', str(exc))
        if data.get('kind') == 'domain' and not data.get('confirm_domain'):
            self.add_error('confirm_domain', 'Bitte die Wirkung für alle Absender dieser Domain bestätigen.')
        return data


@login_required
@never_cache
def settings_page(request, pk):
    if not request.user.is_staff:
        return HttpResponseForbidden('Administration erforderlich.')
    account = get_object_or_404(MailAccount, pk=pk)
    action = request.POST.get('operation')
    setting_form = SpamSettingsForm(request.POST if action == 'settings' else None,
        initial={'revision': account.spam_revision, 'mode': account.spam_mode})
    rule_form = SpamRuleForm(request.POST if action == 'rule' else None,
        initial={'revision': account.spam_revision, 'kind': 'address', 'value': request.GET.get('sender', '')[:254]})
    form = setting_form if action == 'settings' else rule_form
    if request.method == 'POST':
        try:
            if action not in ('settings', 'rule', 'delete'):
                raise MailError('Unbekannte Filteraktion.')
            if action == 'delete' or form.is_valid():
                with account_lease(pk), transaction.atomic():
                    account.refresh_from_db()
                    if str(account.spam_revision) != request.POST.get('revision'):
                        raise MailError('Die Filtereinstellungen wurden inzwischen geändert. Bitte neu öffnen.')
                    if action == 'settings':
                        account.spam_mode = setting_form.cleaned_data['mode']
                    elif action == 'rule':
                        data = rule_form.cleaned_data
                        if account.spam_rules.filter(kind=data['kind'], value=data['value']).exists():
                            raise MailError('Für diese Adresse oder Domain besteht bereits eine Regel. Zum Ändern die vorhandene Regel zuerst entfernen.')
                        account.spam_rules.create(kind=data['kind'], value=data['value'], action=data['action'])
                    else:
                        get_object_or_404(MailSpamRule, pk=_integer(request.POST.get('rule')), account=account).delete()
                    account.spam_revision += 1
                    # Existing mail may be marked, but never moved by a new rule.
                    account.spam_auto_since = timezone.now() if account.spam_mode == 'auto' else None
                    account.save(update_fields=['spam_mode', 'spam_revision', 'spam_auto_since'])
                    AuditLog.objects.create(user=request.user, action='update', object_type='MailSpamRules',
                        object_id=str(pk), object_repr=account.email, message='Spamfilter-Einstellungen aktualisiert.',
                        metadata={'revision': account.spam_revision, 'operation': action, 'mode': account.spam_mode})
                messages.success(request, 'Filter gespeichert. Bestehende E-Mails werden dadurch nicht automatisch verschoben.')
                return redirect('sedo:mail_spam_settings', pk)
        except MailError as exc:
            messages.error(request, str(exc))
    context = _shell(request)
    context.update({'spam_account': account, 'setting_form': setting_form, 'rule_form': rule_form,
                    'rules': account.spam_rules.all()})
    return render(request, 'sedo/mail/spam_settings.html', context)


def selected_locations(values):
    try:
        ids = [int(value) for value in values]
    except (ValueError, TypeError):
        raise MailError('Ungültige Nachrichtenauswahl.')
    if not ids or len(ids) > 25 or len(set(ids)) != len(ids):
        raise MailError('Bitte 1 bis 25 verschiedene Nachrichten auswählen.')
    items = list(MailLocation.objects.filter(pk__in=ids, folder__active=True, folder__selectable=True)
                 .select_related('message__account', 'folder__account').order_by('folder__account_id', 'pk'))
    if len(items) != len(ids):
        raise MailError('Die Auswahl hat sich geändert. Bitte die Nachrichtenliste aktualisieren.')
    return items


def snapshot(items):
    return [[item.pk, item.message_id, item.folder_id, item.uidvalidity, item.uid,
             item.message.account.spam_revision, item.message.spam_manual,
             item.message.spam_state, item.message.spam_revision] for item in items]


@login_required
@require_POST
@never_cache
def batch(request):
    query = return_query(request)
    try:
        ticket = request.POST.get('ticket')
        if ticket:
            try:
                data = signing.loads(ticket, salt='mail-spam-review', max_age=600)
            except signing.BadSignature:
                raise MailError('Die Vorschau ist abgelaufen oder ungültig. Bitte erneut auswählen.')
            if data['user'] != request.user.pk or request.POST.get('confirm') != 'yes':
                raise MailError('Bitte die angezeigte Auswahl ausdrücklich bestätigen.')
            items = selected_locations([row[0] for row in data['rows']])
            if snapshot(items) != data['rows']:
                raise MailError('Nachrichten oder Regeln haben sich geändert. Bitte die Vorschau neu öffnen.')
            action = data['action']
            query = data.get('return_query', '')
            completed, failed, accounts = 0, [], set()
            for item in items:
                accounts.add(item.folder.account_id)
                try:
                    if action == 'check':
                        with account_lease(item.folder.account_id):
                            item.message.refresh_from_db()
                            item.message.account.refresh_from_db()
                            assess(item.message, review_old=True, user=request.user)
                    elif action == 'trash':
                        move_message(item, destination_for(item.folder.account, 'trash'))
                    else:
                        correct(item.pk, action, request.user)
                    completed += 1
                except (MailError, MailLocation.DoesNotExist) as exc:
                    failed.append((item, str(exc) if isinstance(exc, MailError) else 'Nachricht inzwischen verschoben.'))
            if action != 'check':
                for pk in accounts:
                    request_sync(pk)
            if failed:
                context = _shell(request)
                context.update({'completed': completed, 'failures': failed, 'mailbox_url': mailbox_url(query)})
                return render(request, 'sedo/mail/spam_result.html', context)
            messages.success(request, f'{completed} Nachricht(en) ' + ('geprüft und markiert.' if action == 'check' else 'bearbeitet. Der Serverstand wird neu abgerufen.'))
            return redirect(mailbox_url(query))
        action = request.POST.get('action')
        if action not in ('spam', 'ham', 'check', 'trash'):
            raise MailError('Unbekannte Spamaktion.')
        items = selected_locations(request.POST.getlist('location'))
        rows = []
        for item in items:
            state, reason, _ = evaluate(item.message, review_old=True)
            rows.append({'item': item, 'reason': reason, 'state': dict(item.message._meta.get_field('spam_state').choices)[state]})
        context = _shell(request)
        context.update({'selection': rows, 'action': action,
            'mailbox_url': mailbox_url(query),
            'ticket': signing.dumps({'user': request.user.pk, 'action': action, 'rows': snapshot(items), 'return_query': query}, salt='mail-spam-review')})
        return render(request, 'sedo/mail/spam_review.html', context)
    except MailError as exc:
        messages.error(request, str(exc))
        return redirect(mailbox_url(query))


@login_required
@require_POST
def resolve_move(request, pk):
    move = get_object_or_404(MailSpamMove.objects.select_related('message'), pk=pk)
    try:
        with account_lease(move.message.account_id):
            move.refresh_from_db()
            if move.state != 'unknown' or request.POST.get('confirm') != 'yes' or request.POST.get('state') not in ('done', 'failed'):
                raise MailError('Bitte eine offene Verschiebung erst im Webmail prüfen und das Ergebnis bestätigen.')
            move.state = request.POST['state']
            move.note = 'Serverstand vom Benutzer im Webmail geprüft: ' + ('verschoben.' if move.state == 'done' else 'nicht verschoben.')
            move.save(update_fields=['state', 'note'])
            audit(move.message, move.note, request.user)
        request_sync(move.message.account_id)
        messages.success(request, 'Prüfung gespeichert. Es wurde keine Nachricht verschoben; der Serverstand wird neu abgerufen.')
    except MailError as exc:
        messages.error(request, str(exc))
    return redirect('sedo:mail_message', move.message_id)
