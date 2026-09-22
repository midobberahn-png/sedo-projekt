from decimal import InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import F, Q
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.urls import Resolver404, resolve, reverse
from urllib.parse import urlencode, urlsplit
from django.views.decorators.cache import never_cache
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from .incoming_forms import IncomingInvoiceForm
from .models import AppSetting, AuditLog, IncomingInvoice, IncomingInvoicePackage, MailAccount, MailDraft, MailMessage
from .services.ai_local import AIError
from .services.incoming_invoices import (IncomingError, collect_message, create_package, digest, print_collection,
    record_action, selected_approved, suggest_fields, verified_original)
from .services.mail_mime import addresses
from .services.mail_secrets import MailError
from .services.mail_send import MAX_ATTACHMENTS_BYTES


def navigation(request, pk):
    parent = request.GET.get('back', '')
    try:
        parts = urlsplit(parent)
        match = resolve(parts.path)
        valid = (len(parent) <= 2000 and parent.startswith('/') and not parts.scheme and
                 not parts.netloc and not any(ord(char) < 32 or char == '\\' for char in parent) and
                 match.view_name in ('sedo:incoming_index', 'sedo:mail_inbox', 'sedo:mail_message'))
    except (ValueError, Resolver404):
        valid = False
    parent = parent if valid else reverse('sedo:incoming_index')
    query = urlencode({'back': parent})
    detail_url = reverse('sedo:incoming_detail', args=[pk]) + '?' + query
    return {'back_url': parent, 'detail_url': detail_url,
            'preview_url': reverse('sedo:incoming_preview', args=[pk]) + '?' + query + '&from=detail',
            'preview_back_url': detail_url if request.GET.get('from') == 'detail' else parent}


def download_bytes(data, name, content_type='application/octet-stream'):
    response = HttpResponse(data, content_type=content_type)
    response['Content-Disposition'] = content_disposition_header(True, name)
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@login_required
@never_cache
def index(request):
    view = request.GET.get('view', 'review')
    items = IncomingInvoice.objects.defer('original')
    if view == 'print':
        items = items.filter(state='approved', printed_at__isnull=True)
    elif view in ('review', 'approved', 'ignored'):
        items = items.filter(state=view)
    else:
        view = 'all'
        items = items.exclude(state='ignored')
    q = request.GET.get('q', '').strip()[:200]
    month = request.GET.get('month', timezone.localdate().strftime('%Y-%m'))
    if q:
        items = items.filter(Q(supplier__icontains=q) | Q(number__icontains=q) | Q(filename__icontains=q))
    if request.GET.get('month'):
        try:
            from .services.tax_export import parse_month
            year, number = parse_month(month)
            items = items.filter(document_date__year=year, document_date__month=number)
        except ValueError as exc:
            messages.error(request, str(exc))
            items = items.none()
    query = request.GET.copy()
    query.pop('page', None)
    return render(request, 'sedo/incoming/index.html', {
        'page_obj': Paginator(items, 30).get_page(request.GET.get('page')), 'view': view, 'q': q,
        'month': month, 'page_query': query.urlencode(), 'filtered_month': request.GET.get('month', ''),
        'review_count': IncomingInvoice.objects.filter(state='review').count(),
        'print_count': IncomingInvoice.objects.filter(state='approved', printed_at__isnull=True).count(),
        'scan_errors': MailMessage.objects.exclude(invoice_scan_error='').defer('raw', 'body')[:20],
        'packages': IncomingInvoicePackage.objects.select_related('draft').defer('data', 'draft__raw')[:15],
        'auto_enabled': AppSetting.get('incoming_invoice_enabled', '1') == '1',
        'tax_email': AppSetting.get('incoming_tax_email', ''),
        'return_url': request.get_full_path(),
    })


@login_required
@never_cache
def detail(request, pk):
    invoice = get_object_or_404(IncomingInvoice, pk=pk)
    nav = navigation(request, pk)
    form = IncomingInvoiceForm(instance=invoice, initial={'revision': invoice.revision})
    if request.method == 'POST':
        action = request.POST.get('action')
        try:
            if action == 'suggest':
                suggestions = suggest_fields(invoice)
                # Suggestions fill an unsaved form only; never replace reviewed data.
                form = IncomingInvoiceForm(instance=invoice, initial={**suggestions, 'revision': invoice.revision})
                messages.warning(request, 'KI-Vorschlag – alle Angaben mit dem Original vergleichen. Noch nichts gespeichert oder freigegeben.')
            elif action in ('save', 'approve', 'ignore', 'reopen'):
                if action in ('save', 'approve'):
                    form = IncomingInvoiceForm(request.POST, instance=invoice)
                    if not form.is_valid():
                        return render(request, 'sedo/incoming/detail.html', {'invoice': invoice, 'form': form, **nav})
                with transaction.atomic():
                    # An atomic revision claim also serializes edits on SQLite.
                    revision = int(request.POST.get('revision', '-1'))
                    if not IncomingInvoice.objects.filter(pk=pk, revision=revision).update(revision=revision + 1):
                        raise IncomingError('Dieser Beleg wurde zwischenzeitlich geändert. Bitte neu öffnen.')
                    invoice.refresh_from_db()
                    if action in ('ignore', 'reopen'):
                        invoice.state = 'ignored' if action == 'ignore' else 'review'
                        invoice.reviewed_at = None
                        invoice.reviewed_by = None
                        invoice.save(update_fields=['state', 'reviewed_at', 'reviewed_by'])
                    else:
                        for field in form.Meta.fields:
                            setattr(invoice, field, form.cleaned_data[field])
                        verified_original(invoice)
                        invoice.state = 'approved' if action == 'approve' else 'review'
                        invoice.reviewed_at = timezone.now() if action == 'approve' else None
                        invoice.reviewed_by = request.user if action == 'approve' else None
                        invoice.save()
                    record_action(invoice, request.user, 'update', f'Belegstatus: {invoice.get_state_display()}')
                messages.success(request, 'Beleg gespeichert. Das Original bleibt unverändert.')
                return redirect(nav['detail_url'])
            else:
                raise IncomingError('Unbekannte Aktion.')
        except (IncomingError, AIError, ValueError, InvalidOperation) as exc:
            messages.error(request, str(exc) if isinstance(exc, (IncomingError, AIError)) else 'Bitte gültige Angaben verwenden.')
    duplicates = IncomingInvoice.objects.none()
    if invoice.supplier and invoice.number:
        duplicates = IncomingInvoice.objects.filter(supplier__iexact=invoice.supplier, number__iexact=invoice.number).exclude(pk=pk)
    return render(request, 'sedo/incoming/detail.html', {'invoice': invoice, 'form': form, 'duplicates': duplicates, **nav})


@login_required
@never_cache
def preview(request, pk):
    invoice = get_object_or_404(IncomingInvoice, pk=pk)
    context = {'invoice': invoice, **navigation(request, pk)}
    try:
        data = verified_original(invoice)
        kind = _preview_type(invoice, data)
        context['kind'] = kind
        if kind == 'text':
            if len(data) > 1024 * 1024:
                raise IncomingError('Diese XML-Datei ist für die Vorschau zu groß. Bitte das Original herunterladen.')
            encoding = 'utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
            context['document_text'] = data.decode(encoding, errors='replace')
    except IncomingError as exc:
        context['error'] = str(exc)
    return render(request, 'sedo/incoming/preview.html', context, status=409 if context.get('error') else 200)


def _preview_type(invoice, data):
    if invoice.media_type == 'application/pdf' and data.startswith(b'%PDF-'):
        return 'pdf'
    if invoice.media_type == 'application/xml':
        return 'text'  # Always escaped text: never execute an XML stylesheet or embedded markup.
    if invoice.media_type in ('image/png', 'image/jpeg', 'image/tiff'):
        import io
        from PIL import Image
        try:
            with Image.open(io.BytesIO(data)) as picture:
                if picture.format not in ('PNG', 'JPEG', 'TIFF'):
                    raise ValueError('Unsupported raster format')
                if picture.width * picture.height > 25_000_000:
                    raise ValueError('Preview exceeds pixel budget')
                picture.verify()
            return 'image'
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise IncomingError('Das Bild kann nicht angezeigt werden. Bitte das Original prüfen.') from exc
    raise IncomingError('Dieses Dokument kann nicht angezeigt werden. Bitte das Original herunterladen und prüfen.')


@login_required
@never_cache
@xframe_options_sameorigin
def original(request, pk):
    invoice = get_object_or_404(IncomingInvoice, pk=pk)
    try:
        data = verified_original(invoice)
        if request.GET.get('inline') != '1':
            return download_bytes(data, invoice.filename, invoice.media_type)
        kind = _preview_type(invoice, data)
        if kind not in ('pdf', 'image'):
            return HttpResponse('Für dieses Dokument ist keine eingebettete Ansicht verfügbar.', status=400)
        content_type = invoice.media_type
        if kind == 'image':
            import io
            from PIL import Image
            # Re-encode raster previews, including TIFF, for consistent browser support.
            output = io.BytesIO()
            try:
                with Image.open(io.BytesIO(data)) as picture:
                    picture.thumbnail((2400, 2400))
                    picture.convert('RGB').save(output, format='PNG')
            except (OSError, ValueError, Image.DecompressionBombError) as exc:
                raise IncomingError('Das Bild kann nicht angezeigt werden. Bitte das Original prüfen.') from exc
            data, content_type = output.getvalue(), 'image/png'
        response = download_bytes(data, invoice.filename, content_type)
        response['Content-Disposition'] = content_disposition_header(False, invoice.filename)
        response['Content-Security-Policy'] = "default-src 'none'; object-src 'self'; frame-ancestors 'self'"
        return response
    except IncomingError as exc:
        return HttpResponse(str(exc), status=409)


@login_required
@require_POST
def batch(request):
    try:
        ids = [int(value) for value in request.POST.getlist('invoice')]
        with transaction.atomic():
            # Acquires SQLite write lock before reading reviewed snapshots.
            IncomingInvoice.objects.filter(pk__in=ids).update(revision=F('revision'))
            invoices = selected_approved(ids)
            action = request.POST.get('action')
            if action == 'print':
                return download_bytes(print_collection(invoices), 'SEDO_Drucksammlung.pdf', 'application/pdf')
            if action == 'printed':
                if request.POST.get('confirm_print') != 'yes':
                    raise IncomingError('Bitte bestätigen, dass die ausgewählten Belege tatsächlich gedruckt wurden.')
                for item in invoices:
                    item.printed_at = timezone.now()
                    item.save(update_fields=['printed_at'])
                    record_action(item, request.user, 'update', 'Druck vom Benutzer bestätigt.')
                messages.success(request, 'Druckstatus gespeichert.')
            elif action == 'package':
                package = create_package(invoices, request.POST.get('month', ''), request.user)
                AuditLog.objects.create(user=request.user, action='generate', object_type='IncomingInvoicePackage',
                    object_id=str(package.pk), object_repr=package.month, message='Steuerbüro-Paket erstellt.', metadata={'sha256': package.digest})
                return redirect('sedo:incoming_package', package.pk)
            else:
                raise IncomingError('Unbekannte Sammelaktion.')
    except (IncomingError, ValueError) as exc:
        messages.error(request, str(exc) if isinstance(exc, IncomingError) else 'Bitte eine gültige Belegauswahl und einen gültigen Monat verwenden.')
    return redirect('sedo:incoming_index')


@login_required
@never_cache
def package_detail(request, pk):
    package = get_object_or_404(IncomingInvoicePackage.objects.select_related('draft'), pk=pk)
    return render(request, 'sedo/incoming/package.html', {'package': package,
        'accounts': MailAccount.objects.all(), 'tax_email': AppSetting.get('incoming_tax_email', ''),
        'can_email': len(package.data) <= MAX_ATTACHMENTS_BYTES,
        'owns_draft': package.draft_id and package.draft.author_id == request.user.pk})


@login_required
def package_download(request, pk):
    package = get_object_or_404(IncomingInvoicePackage, pk=pk)
    if digest(bytes(package.data)) != package.digest:
        return HttpResponse('Paket-Prüfsumme stimmt nicht.', status=409)
    return download_bytes(bytes(package.data), f'SEDO_Eingangsrechnungen_{package.month}.zip', 'application/zip')


@login_required
@require_POST
def package_email(request, pk):
    try:
        with transaction.atomic():
            IncomingInvoicePackage.objects.filter(pk=pk).update(month=F('month'))
            package = get_object_or_404(IncomingInvoicePackage, pk=pk)
            if package.draft_id:
                if package.draft.author_id != request.user.pk:
                    raise IncomingError('Der bestehende Entwurf gehört einem anderen Benutzer.')
                return redirect('sedo:mail_review', package.draft_id)
            account = get_object_or_404(MailAccount, pk=request.POST.get('account'))
            recipients = addresses(request.POST.get('recipient', ''))
            if len(recipients) != 1:
                raise IncomingError('Bitte genau eine Steuerbüro-Adresse angeben.')
            data = bytes(package.data)
            if digest(data) != package.digest:
                raise IncomingError('Die Paket-Prüfsumme stimmt nicht.')
            if len(data) > MAX_ATTACHMENTS_BYTES:
                raise IncomingError('Das Paket überschreitet das Mail-Limit von 25 MB. Bitte kleinere Pakete bilden.')
            draft = MailDraft.objects.create(account=account, author=request.user, to=recipients[0],
                subject=f'Eingangsrechnungen {package.month} · SEDO',
                body=f'Guten Tag,\n\nanbei unsere geprüften Eingangsbelege für {package.month} mit Originalen, Quell-E-Mails und Übersicht.\n\nMit freundlichen Grüßen\n' + account.signature)
            draft.attachments.create(name=f'SEDO_Eingangsrechnungen_{package.month}.zip', data=data, size=len(data), content_type='application/zip')
            package.draft = draft
            package.save(update_fields=['draft'])
        return redirect('sedo:mail_review', draft.pk)
    except (IncomingError, MailError, ValueError):
        messages.error(request, 'Mailentwurf konnte nicht erstellt werden. Bitte Absender, Empfänger, Paketgröße und Paket-Prüfsumme prüfen.')
        return redirect('sedo:incoming_package', pk)


@login_required
@require_POST
def settings_save(request):
    if not request.user.is_staff:
        return HttpResponseForbidden('Administration erforderlich.')
    try:
        recipient = request.POST.get('tax_email', '').strip()
        if recipient and len(addresses(recipient)) != 1:
            raise MailError('Bitte genau eine Steuerbüro-Adresse angeben.')
        with transaction.atomic():
            AppSetting.set('incoming_tax_email', recipient)
            AppSetting.set('incoming_invoice_enabled', '1' if request.POST.get('enabled') else '0')
        messages.success(request, 'Einstellungen gespeichert.')
    except MailError as exc:
        messages.error(request, str(exc))
    return redirect('sedo:incoming_index')


@login_required
@require_POST
def collect(request, pk):
    message = get_object_or_404(MailMessage, pk=pk)
    try:
        MailMessage.objects.filter(pk=pk).update(invoice_scan_done=False)
        count = collect_message(message)
        if not count and not message.incoming_sources.exists():
            messages.error(request, 'Keine eindeutige Rechnung erkannt. Die Dokumente bleiben im Postfach.')
        else:
            messages.success(request, f'{count} neue Belege übernommen. Bereits gespeicherte Originale werden nicht doppelt angelegt.')
    except (IncomingError, MailError) as exc:
        messages.error(request, str(exc))
    if 'return_query' in request.POST:
        from .mail_views import _integer, return_query
        from django.http import QueryDict
        from django.urls import reverse
        params = QueryDict(return_query(request)).copy()
        location_id = _integer(request.POST.get('location'))
        if location_id:
            params['location'] = location_id
        return redirect(reverse('sedo:mail_message', args=[pk]) + ('?' + params.urlencode() if params else ''))
    return redirect('sedo:incoming_index')
