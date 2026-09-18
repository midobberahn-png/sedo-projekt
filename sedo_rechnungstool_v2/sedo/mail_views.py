"""Authenticated UI for the shared company mailboxes (private author drafts)."""
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Q, Sum
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.views.decorators.cache import never_cache
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from .mail_forms import MailAccountForm, MailDraftForm, MailTaskForm
from .models import Customer, MailAccount, MailDraft, MailFolder, MailLocation, MailMessage, Offer
from .services.mail_imap import (account_lease, create_folder, delete_permanently, move_message, set_flag)
from .services.mail_mime import attachment_bytes, body_and_attachments, parse, safe_html, safe_name
from .services.mail_secrets import MailError, encrypt_password
from .services.mail_send import (MAX_ATTACHMENTS_BYTES, forward_attachments, prepare, reply_initial, save_sent_copy, send_draft, test_account)
from .services.mail_worker import recover_interrupted_sends, request_sync
from .services.mail_storage import original_bytes, remove_cached


def _integer(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _shell(request):
    account_id = _integer(request.GET.get('account'))
    folder_id = _integer(request.GET.get('folder'))
    accounts = list(MailAccount.objects.defer('password_encrypted').all())
    folders = MailFolder.objects.filter(active=True, selectable=True).select_related('account').defer('account__password_encrypted').annotate(
        unread=Count('locations', filter=Q(locations__seen=False)))
    if account_id:
        folders = folders.filter(account_id=account_id)
    return {'accounts': accounts, 'folders': folders, 'account_id': account_id, 'folder_id': folder_id,
            'draft_count': MailDraft.objects.filter(author=request.user, state='draft').count()}


def _reader_context(message):
    matches = Customer.objects.filter(email__iexact=message.sender_email) if message.sender_email else Customer.objects.none()
    suggested = matches.first() if matches.count() == 1 else None
    return {'selected': message, 'locations': message.locations.select_related('folder__account'),
            'message_offers': message.offers.filter(deleted_at__isnull=True).select_related('linked_invoice'),
            'message_tasks': message.tasks.all(), 'suggested_customer': suggested,
            'customers': Customer.objects.order_by('name').only('id', 'name', 'customer_no'),
            'move_folders': MailFolder.objects.filter(account=message.account, active=True, selectable=True)}


@login_required
@never_cache
def inbox(request, pk=None):
    context = _shell(request)
    items = MailLocation.objects.filter(folder__active=True, folder__selectable=True)
    if context['account_id']:
        items = items.filter(folder__account_id=context['account_id'])
    if context['folder_id']:
        folder = get_object_or_404(context['folders'], pk=context['folder_id'])
        items = items.filter(folder=folder)
        context['current_folder'] = folder
    else:
        items = items.filter(folder__role='inbox')
    q = request.GET.get('q', '').strip()[:200]
    view = request.GET.get('view', '')
    if q:
        # Searching all locally loaded folders is explicit; pagination stays bounded.
        if request.GET.get('scope') == 'all':
            items = MailLocation.objects.filter(folder__active=True)
            if context['account_id']:
                items = items.filter(folder__account_id=context['account_id'])
        items = items.filter(Q(message__subject__icontains=q) | Q(message__sender__icontains=q) |
                             Q(message__recipients__icontains=q) | Q(message__body__icontains=q))
    if view == 'unread':
        items = items.filter(seen=False)
    elif view == 'flagged':
        items = items.filter(flagged=True)
    items = items.select_related('message', 'folder__account').defer('message__raw', 'message__body', 'folder__account__password_encrypted').order_by('-message__sent_at', '-pk')
    context.update({'page_obj': Paginator(items, 30).get_page(request.GET.get('page')), 'q': q, 'view': view,
                    'scope': request.GET.get('scope', ''), 'is_inbox': True})
    query = request.GET.copy()
    query.pop('page', None)
    context['page_query'] = query.urlencode()
    if pk is not None:
        context.update(_reader_context(get_object_or_404(MailMessage.objects.select_related('account', 'customer').defer('raw'), pk=pk)))
    return render(request, 'sedo/mail/inbox.html', context)


@login_required
@never_cache
def settings_page(request, pk=None):
    if not request.user.is_staff:
        return HttpResponseForbidden('Postfächer können nur mit einem Administrationskonto eingerichtet werden.')
    account = get_object_or_404(MailAccount, pk=pk) if pk else MailAccount()
    initial_email = request.GET.get('email', '')
    if initial_email not in ('info@sedo-magdeburg.de', 'rechnung@sedo-magdeburg.de'):
        initial_email = ''
    form = MailAccountForm(request.POST or None, instance=account,
        initial={'email': initial_email, 'username': initial_email, 'enabled': True} if not pk else None)
    if request.method == 'POST' and form.is_valid():
        changed = form.save(commit=False)
        try:
            if form.cleaned_data.get('password'):
                changed.password_encrypted = encrypt_password(changed, form.cleaned_data['password'])
            if pk:
                with account_lease(pk):
                    # Do not copy stale lease fields from the form instance.
                    changed.save(update_fields=['email', 'display_name', 'username', 'password_encrypted', 'signature', 'enabled', 'smtp_port', 'smtp_security'])
            else:
                changed.save()
            messages.success(request, 'Postfach gespeichert. Mit „Verbindung prüfen“ werden Anmeldung und Ordner getestet; dabei wird keine E-Mail versendet.')
            return redirect('sedo:mail_account', changed.pk)
        except MailError as exc:
            form.add_error(None, str(exc))
    context = _shell(request)
    context.update({'form': form, 'edited_account': account, 'account_folders': account.folders.filter(active=True) if pk else [],
                    'role_choices': MailFolder.ROLES})
    return render(request, 'sedo/mail/settings.html', context)


@login_required
@require_POST
def account_action(request, pk):
    account = get_object_or_404(MailAccount, pk=pk)
    action = request.POST.get('action')
    if action == 'sync':
        if not account.password_encrypted:
            messages.error(request, 'Bitte zuerst die Postfach-Zugangsdaten hinterlegen.')
        else:
            request_sync(pk)
            messages.success(request, 'Der Abruf läuft im Hintergrund. Die Nachrichtenliste lässt sich weiter bedienen.')
        return redirect('sedo:mail_inbox')
    if not request.user.is_staff:
        return HttpResponseForbidden('Administration erforderlich.')
    try:
        if action == 'test':
            test_account(account)
            messages.success(request, 'IMAP- und SMTP-Anmeldung erfolgreich. Es wurde keine Nachricht versendet.')
        elif action == 'folder':
            create_folder(account, request.POST.get('name', ''))
            messages.success(request, 'Ordner auf dem Mailserver angelegt.')
        elif action == 'roles':
            with account_lease(pk), transaction.atomic():
                assignments = []
                seen_roles = set()
                for folder in account.folders.filter(active=True, selectable=True):
                    role = request.POST.get(f'role_{folder.pk}', folder.role)
                    if role not in dict(MailFolder.ROLES) or (role != 'other' and role in seen_roles):
                        raise MailError('Jede besondere Ordnerrolle darf nur einmal vergeben werden.')
                    seen_roles.add(role)
                    folder.role, folder.role_manual = role, True
                    assignments.append(folder)
                MailFolder.objects.bulk_update(assignments, ['role', 'role_manual'])
            messages.success(request, 'Ordnerzuordnung gespeichert.')
        else:
            raise MailError('Unbekannte Postfachaktion.')
    except MailError as exc:
        messages.error(request, str(exc))
    return redirect('sedo:mail_account', pk)


@login_required
@never_cache
def status(request):
    return JsonResponse({'accounts': list(MailAccount.objects.values('id', 'email', 'last_sync', 'last_error', 'lease_until')),
                         'count': MailLocation.objects.filter(folder__active=True).count()})


@login_required
@never_cache
@xframe_options_sameorigin
def html_body(request, pk):
    message = get_object_or_404(MailMessage, pk=pk)
    try:
        _, body, _ = body_and_attachments(parse(original_bytes(message)))
        body = safe_html(body)
    except (MailError, ValueError, RecursionError):
        body = '<p>Die HTML-Ansicht ist für diese Nachricht nicht verfügbar.</p>'
    response = HttpResponse('<!doctype html><html lang="de"><meta charset="utf-8"><style>body{font:16px/1.5 system-ui;color:#24364b;overflow-wrap:anywhere;margin:20px}table{max-width:100%}pre{white-space:pre-wrap}a{color:#1761a2}</style><body>' + body + '</body></html>')
    response['Content-Security-Policy'] = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'; sandbox allow-popups allow-popups-to-escape-sandbox"
    response['Referrer-Policy'] = 'no-referrer'
    return response


def _download(data, name):
    response = HttpResponse(data, content_type='application/octet-stream')
    response['Content-Disposition'] = content_disposition_header(True, safe_name(name))
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
def download(request, pk, index=None):
    message = get_object_or_404(MailMessage, pk=pk)
    try:
        if index is None:
            return _download(original_bytes(message), 'Nachricht.eml')
        name, data = attachment_bytes(message, index)
        return _download(data, name)
    except MailError as exc:
        return HttpResponse(str(exc), status=400, content_type='text/plain; charset=utf-8')


@login_required
@require_POST
def message_action(request, pk):
    message = get_object_or_404(MailMessage, pk=pk)
    action = request.POST.get('action')
    try:
        if action == 'customer':
            customer_id = _integer(request.POST.get('customer'))
            message.customer = get_object_or_404(Customer, pk=customer_id) if customer_id else None
            message.save(update_fields=['customer'])
            messages.success(request, 'Kundenzuordnung gespeichert.')
        else:
            location = get_object_or_404(MailLocation.objects.select_related('folder__account'), pk=_integer(request.POST.get('location')), message=message)
            if action in ('seen', 'unseen', 'flag', 'unflag'):
                set_flag(location, 'seen' if action in ('seen', 'unseen') else 'flagged', action in ('seen', 'flag'))
            elif action in ('move', 'trash', 'archive'):
                if action == 'move':
                    destination = get_object_or_404(MailFolder, pk=_integer(request.POST.get('destination')), account=message.account)
                else:
                    destinations = message.account.folders.filter(role=action, active=True, selectable=True)
                    if destinations.count() != 1:
                        raise MailError('Bitte zunächst in den Postfach-Einstellungen genau einen passenden Zielordner zuordnen.')
                    destination = destinations.get()
                move_message(location, destination)
                request_sync(message.account_id)
                messages.success(request, 'Nachricht verschoben. Der Zielordner wird neu abgeglichen.')
                return redirect('sedo:mail_inbox')
            elif action == 'delete':
                if request.POST.get('confirm') != 'delete':
                    raise MailError('Bitte das endgültige Löschen im Papierkorb ausdrücklich bestätigen.')
                delete_permanently(location)
                # Retain a business source, otherwise remove the locally cached message too.
                if not message.customer_id and not message.locations.exists() and not message.offers.exists() and not message.tasks.exists() and not message.replies.exists() and not MailDraft.objects.filter(sent_message=message).exists():
                    remove_cached(message)
                    message.delete()
                messages.success(request, 'Die Nachricht wurde vom Server gelöscht. Verknüpfte Geschäftsquellen bleiben in SEDO erhalten.')
                return redirect('sedo:mail_inbox')
            else:
                raise MailError('Unbekannte Nachrichtenaktion.')
    except MailError as exc:
        messages.error(request, str(exc))
    return redirect('sedo:mail_message', pk)


@login_required
@never_cache
def compose(request, pk=None):
    draft = get_object_or_404(MailDraft.objects.select_related('account', 'source'), pk=pk, author=request.user) if pk else None
    if draft and draft.state != 'draft':
        return redirect('sedo:mail_review', draft.pk)
    if not MailAccount.objects.exists():
        messages.info(request, 'Bitte zuerst ein Postfach einrichten.')
        return redirect('sedo:mail_settings')
    source_id = _integer(request.POST.get('source') if request.method == 'POST' else request.GET.get('source'))
    source = draft.source if draft else (get_object_or_404(MailMessage.objects.select_related('account'), pk=source_id) if source_id else None)
    mode = draft.mode if draft else request.POST.get('mode', request.GET.get('mode', 'new'))
    if mode not in ('new', 'reply', 'reply_all', 'forward') or (mode != 'new' and not source):
        return HttpResponse('Ungültiger Nachrichtenbezug.', status=400)
    if source and source.content_error:
        messages.error(request, 'Diese Nachricht ist nicht vollständig geladen. Bitte das Original im Webmail prüfen.')
        return redirect('sedo:mail_message', source.pk)
    account = draft.account if draft else (source.account if source else MailAccount.objects.first())
    initial = {'account': account, 'body': '\n\n' + account.signature, 'revision': draft.revision if draft else 0,
               'creation_key': draft.creation_key if draft else uuid.uuid4()}
    if source and not draft:
        initial.update(reply_initial(source, mode, account))
    form = MailDraftForm(request.POST or None, instance=draft, initial=initial)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                is_new = draft is None
                if is_new:
                    if MailDraft.objects.filter(creation_key=form.cleaned_data['creation_key']).exists():
                        raise MailError('Dieser Entwurf wurde bereits gespeichert. Bitte die Entwurfsliste öffnen.')
                    draft = form.save(commit=False)
                    draft.author, draft.source, draft.mode = request.user, source, mode
                    draft.creation_key = form.cleaned_data['creation_key']
                    draft.save()
                    if source and mode == 'forward':
                        forward_attachments(draft, source)
                else:
                    changed = {key: form.cleaned_data[key] for key in ('account', 'to', 'cc', 'bcc', 'subject', 'body')}
                    if not MailDraft.objects.filter(pk=draft.pk, author=request.user, state='draft', revision=form.cleaned_data['revision']).update(
                            **changed, revision=F('revision') + 1, updated_at=timezone.now()):
                        raise MailError('Dieser Entwurf wurde in einem anderen Fenster geändert. Bitte den aktuellen Stand neu öffnen.')
                remove = [_integer(value) for value in request.POST.getlist('remove_attachment')]
                draft.attachments.filter(pk__in=[value for value in remove if value]).delete()
                total = draft.attachments.aggregate(total=Sum('size'))['total'] or 0
                files = request.FILES.getlist('attachments')
                if draft.attachments.count() + len(files) > 50:
                    raise MailError('Bitte höchstens 50 Anhänge je Nachricht verwenden.')
                for upload in files:
                    total += upload.size
                    if total > MAX_ATTACHMENTS_BYTES:
                        raise MailError('Anhänge dürfen zusammen höchstens 25 MB groß sein.')
                    draft.attachments.create(name=safe_name(upload.name), data=upload.read(), size=upload.size,
                        content_type=(upload.content_type or 'application/octet-stream')[:150])
            if request.POST.get('action') == 'review':
                return redirect('sedo:mail_review', draft.pk)
            messages.success(request, 'Entwurf in SEDO gespeichert. Er wurde noch nicht versendet.')
            return redirect('sedo:mail_compose', draft.pk)
        except (MailError, IntegrityError) as exc:
            form.add_error(None, str(exc) if isinstance(exc, MailError) else 'Dieser Entwurf wurde bereits gespeichert. Bitte die Entwurfsliste öffnen.')
            # A rolled-back INSERT must not look like a persisted draft in the template.
            if pk is None:
                draft = None
    context = _shell(request)
    context.update({'form': form, 'draft': draft, 'source': source, 'mode': mode,
                    'contacts': Customer.objects.exclude(email='').only('name', 'email')[:500]})
    return render(request, 'sedo/mail/compose.html', context)


@login_required
@never_cache
def drafts(request):
    recover_interrupted_sends()
    context = _shell(request)
    context['drafts'] = Paginator(MailDraft.objects.filter(author=request.user).select_related('account').defer('raw', 'body', 'account__password_encrypted'), 30).get_page(request.GET.get('page'))
    return render(request, 'sedo/mail/drafts.html', context)


@login_required
@never_cache
def review(request, pk):
    draft = get_object_or_404(MailDraft.objects.select_related('account', 'source').defer('raw'), pk=pk, author=request.user)
    context = _shell(request)
    ready = True
    if draft.state == 'draft':
        try:
            if not draft.account.password_encrypted:
                raise MailError('Für dieses Postfach fehlen noch die Zugangsdaten. Bitte zuerst das Postfach einrichten.')
            prepare(draft)
        except MailError as exc:
            ready = False
            messages.error(request, str(exc))
    context.update({'draft': draft, 'ready': ready})
    return render(request, 'sedo/mail/review.html', context)


@login_required
@require_POST
def draft_action(request, pk):
    draft = get_object_or_404(MailDraft.objects.select_related('account', 'source'), pk=pk, author=request.user)
    action = request.POST.get('action')
    try:
        if action == 'send':
            sent = send_draft(pk, request.user, _integer(request.POST.get('revision')))
            messages.success(request, 'Die E-Mail wurde vom Versandserver angenommen.' + (' Die Kopie liegt im Ordner „Gesendet“.' if sent.copy_saved else ' Die Ablage der Versandkopie steht noch aus.'))
            request_sync(draft.account_id)
        elif action == 'copy':
            with account_lease(draft.account_id):
                draft.refresh_from_db()
                save_sent_copy(draft)
            messages.success(request, 'Versandkopie auf dem Server gespeichert. Die Nachricht wurde nicht erneut versendet.')
            request_sync(draft.account_id)
        elif action == 'delete':
            deleted, _ = MailDraft.objects.filter(pk=pk, author=request.user, state='draft').delete()
            if not deleted:
                raise MailError('Nur unversendete Entwürfe können entfernt werden.')
            return redirect('sedo:mail_drafts')
        elif action in ('confirm_sent', 'confirm_not_sent'):
            if request.POST.get('confirmed') != '1':
                raise MailError('Bitte zuerst den Versandstatus im Webmail prüfen und die Prüfung bestätigen.')
            with account_lease(draft.account_id):
                draft.refresh_from_db()
                if draft.state != 'unknown':
                    raise MailError('Dieser Versandstatus wurde bereits geklärt.')
                if action == 'confirm_sent':
                    draft.state, draft.sent_at = 'sent', timezone.now()
                    from .services.mail_mime import store_message
                    draft.sent_message = store_message(draft.account, bytes(draft.raw))
                else:
                    draft.state, draft.raw, draft.message_id = 'draft', b'', ''
                    draft.revision += 1
                draft.error = ''
                draft.save()
            messages.success(request, 'Versandstatus nach deiner Prüfung aktualisiert. Es wurde keine E-Mail versendet.')
        else:
            raise MailError('Unbekannte Entwurfsaktion.')
    except MailError as exc:
        messages.error(request, str(exc))
    return redirect('sedo:mail_review', pk)


@login_required
@never_cache
def task_from_message(request, pk):
    message = get_object_or_404(MailMessage, pk=pk)
    form = MailTaskForm(request.POST or None, initial={'title': message.subject[:200], 'note': '', 'due_date': timezone.localdate()})
    if request.method == 'POST' and form.is_valid():
        task = form.save(commit=False)
        task.source_mail, task.customer, task.created_by = message, message.customer, request.user
        try:
            task.save()
            messages.success(request, 'Aufgabe zur E-Mail angelegt. Die Nachricht bleibt als Quelle verknüpft.')
            return redirect('sedo:task_update', task.pk)
        except MailError as exc:
            form.add_error(None, str(exc))
    return render(request, 'sedo/mail/task.html', {'form': form, 'selected': message})


@login_required
@require_POST
def offer_draft(request, pk):
    offer = get_object_or_404(Offer.objects.select_related('customer', 'source_mail__account'), pk=pk)
    existing = MailDraft.objects.filter(author=request.user, offer=offer, state='draft').first()
    if existing:
        messages.info(request, 'Der vorhandene E-Mail-Entwurf zu diesem Angebot wurde geöffnet.')
        return redirect('sedo:mail_compose', existing.pk)
    account = offer.source_mail.account if offer.source_mail_id else MailAccount.objects.first()
    if not account:
        messages.error(request, 'Bitte zuerst ein E-Mail-Postfach einrichten.')
        return redirect('sedo:offer_detail', pk)
    try:
        from django.conf import settings
        from pathlib import Path
        from .services.invoice_archive import sicherer_pfad
        if not offer.pdf_file:
            raise MailError('Bitte zunächst das Angebots-PDF erzeugen.')
        pdf = sicherer_pfad(Path(settings.MEDIA_ROOT), offer.pdf_file.name).read_bytes()
        if not pdf or len(pdf) > MAX_ATTACHMENTS_BYTES:
            raise MailError('Das Angebots-PDF fehlt oder ist größer als 25 MB.')
        with transaction.atomic():
            draft = MailDraft.objects.create(account=account, author=request.user, offer=offer,
                source=offer.source_mail, mode='reply' if offer.source_mail_id else 'new',
                to=offer.customer.email, subject=f'Ihr Angebot {offer.number}',
                body=f'Guten Tag,\n\nvielen Dank für Ihre Anfrage. Im Anhang finden Sie unser Angebot {offer.number}.\n\nBei Fragen sind wir gerne für Sie da.\n\n{account.signature}')
            draft.attachments.create(name=safe_name(offer.pdf_file.name), content_type='application/pdf', data=pdf, size=len(pdf))
        messages.success(request, 'Angebots-PDF in einen E-Mail-Entwurf übernommen. Bitte Empfänger, Text und Absender prüfen.')
        return redirect('sedo:mail_compose', draft.pk)
    except (MailError, OSError, ValueError):
        messages.error(request, 'Das Angebots-PDF konnte nicht übernommen werden. Bitte die Datei prüfen und gegebenenfalls neu erzeugen.')
        return redirect('sedo:offer_detail', pk)
