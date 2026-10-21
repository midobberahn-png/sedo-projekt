"""Angebotsansichten mit klarer Grenze zu Rechnungsabläufen."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from .audit import AuditWriteError, write_audit
from .company_profile import load_profile, seller_post
from .forms import OfferForm, OfferItemFormSet, OfferFollowUpForm
from .models import Customer, Offer
from .services import (
    WorkflowError, next_number,
    generate_offer_pdf, create_invoice_from_offer, stt_status,
)
from .services.invoices import INVOICEABLE_OFFER_STATUSES
from .services.mail_secrets import MailError
from .services.offer_creation import save_new_offer
from .services.recurring_templates import DEFAULT_PAYMENT_DAYS
from .view_support import (
    _customer_from_invoice_form, _object_from_invoice_form, _preview_from_forms,
)

logger = logging.getLogger('sedo')


def _offer_detail_context(offer, follow_up_form):
    return {
        'offer': offer,
        'follow_up_form': follow_up_form,
        'can_invoice': not offer.linked_invoice_id and offer.status in INVOICEABLE_OFFER_STATUSES,
    }


def _offer_create_error(request, message, source_mail, web_ai_disabled):
    # Eine zurückgerollte Anlage hinterlässt PKs in den Formularinstanzen.
    # Neu binden bewahrt die POST-Eingaben ohne Verweise auf Phantom-Datensätze.
    offer = Offer(number=next_number('AG'), issue_date=timezone.localdate(), vat_rate=Decimal('19.00'))
    form = OfferForm(request.POST, instance=offer)
    formset = OfferItemFormSet(request.POST, instance=offer)
    form.is_valid()
    formset.is_valid()
    form.add_error(None, message)
    return render(request, 'sedo/offer_form.html', {
        'form': form, 'formset': formset, 'source_mail': source_mail,
        'stt_available': stt_status()['available'],
        'web_ai_disabled': web_ai_disabled,
    })


@login_required
def offer_list(request):
    q = request.GET.get('q','').strip()
    view = request.GET.get('view', 'open')
    today = timezone.localdate()
    offers = Offer.objects.select_related('customer','obj','linked_invoice').all()
    counts = {
        'follow_up': offers.filter(status='offen', follow_up_date__lte=today).count(),
        'open': offers.filter(status='offen').count(),
        'expired': offers.filter(status='offen', valid_until__lt=today).count(),
        'all': offers.count(),
    }
    if view == 'open':
        offers = offers.filter(status='offen')
    elif view == 'expired':
        offers = offers.filter(status='offen', valid_until__lt=today)
    elif view == 'decided':
        offers = offers.exclude(status='offen')
    elif view == 'follow_up':
        offers = offers.filter(status='offen', follow_up_date__lte=today)
    elif view != 'all':
        view = 'open'
        offers = offers.filter(status='offen')
    if q:
        offers = offers.filter(Q(number__icontains=q) | Q(customer__name__icontains=q) | Q(obj__name__icontains=q))
    return render(request, 'sedo/offer_list.html', {'offers': offers, 'q': q, 'view': view, 'counts': counts, 'today': today})


@login_required
@seller_post
def offer_create(request):
    from .models import MailMessage
    from email.utils import parseaddr
    web_ai_disabled = (getattr(settings, 'SEDO_WEB_MODE', False)
                       and not getattr(settings, 'SEDO_WEB_AI_ENABLED', False))
    source_id = request.POST.get('source_mail') if request.method == 'POST' else request.GET.get('source_mail')
    source_mail = get_object_or_404(MailMessage.objects.defer('raw'), pk=source_id) if source_id and str(source_id).isdigit() else None
    if source_id and source_mail is None:
        return HttpResponse('Ungültiger E-Mail-Bezug.', status=400)
    offer = Offer(number=next_number('AG'), issue_date=timezone.localdate(), vat_rate=Decimal('19.00'))
    if request.method == 'POST':
        form = OfferForm(request.POST, instance=offer)
        formset = OfferItemFormSet(request.POST, instance=offer)
        action = request.POST.get('action')
        if form.is_valid() and formset.is_valid():
            if action == 'preview':
                preview = _preview_from_forms(form, formset, title='Entwurfsansicht Angebot')
                return render(request, 'sedo/invoice_preview.html', {'preview': preview, 'is_offer': True, 'seller': load_profile()})
            if source_mail:
                from .services.mail_spam import require_business_clear
                try:
                    require_business_clear(source_mail)
                except MailError as exc:
                    return _offer_create_error(request, str(exc), source_mail, web_ai_disabled)
            if not form.cleaned_data.get('customer') and not (form.cleaned_data.get('name') or '').strip():
                form.add_error('name', 'Bitte einen gespeicherten Kunden auswählen oder die Kopfdaten mit Name/Firma ausfüllen.')
            else:
                off = form.save(commit=False)
                off.source_mail = source_mail
                off.status = 'offen'

                def prepare_offer():
                    customer = _customer_from_invoice_form(form)
                    off.customer = customer
                    off.obj = _object_from_invoice_form(form, customer)
                    off.valid_until = off.issue_date + timedelta(days=form.cleaned_data.get('valid_days') or DEFAULT_PAYMENT_DAYS)

                try:
                    save_new_offer(off, formset, prepare_offer=prepare_offer, source_mail=source_mail)
                except MailError as exc:
                    return _offer_create_error(request, str(exc), source_mail, web_ai_disabled)
                except Exception:
                    logger.exception('Angebotserstellung einschließlich PDF zurückgerollt')
                    return _offer_create_error(request, 'Angebot wurde nicht gespeichert: PDF oder Daten konnten nicht '
                                               'gespeichert werden. Bitte Eingaben prüfen oder erneut versuchen.',
                                               source_mail, web_ai_disabled)
                write_audit(request, 'create', off, f'Angebot {off.number} erstellt')
                messages.success(request, f'Angebot {off.number} gespeichert.')
                return redirect('sedo:offer_detail', off.pk)
    else:
        initial = {'valid_days':14, 'issue_date': timezone.localdate(), 'vat_rate': Decimal('19.00')}
        if source_mail:
            matches = Customer.objects.filter(email__iexact=source_mail.sender_email) if source_mail.sender_email else Customer.objects.none()
            customer = source_mail.customer or (matches.first() if matches.count() == 1 else None)
            initial.update({'email': source_mail.sender_email, 'name': parseaddr(source_mail.sender)[0]})
            if customer:
                initial['customer'] = customer
                initial.update({field: getattr(customer, field) for field in (
                    'salutation', 'name', 'name2', 'customer_no', 'customer_type',
                    'contact_person', 'phone', 'email', 'street', 'postcode', 'city', 'country',
                )})
        form = OfferForm(instance=offer, initial=initial)
        formset = OfferItemFormSet(instance=offer)
    return render(request, 'sedo/offer_form.html', {
        'form': form,
        'formset': formset,
        # Lokales Whisper verfügbar? Steuert, ob das Diktat über den Server
        # transkribiert werden kann. Sonst bleibt die Texteingabe.
        'stt_available': stt_status()['available'],
        'web_ai_disabled': web_ai_disabled,
        'source_mail': source_mail,
    })


@login_required
def offer_detail(request, pk):
    offer = get_object_or_404(Offer.objects.select_related('customer','obj','linked_invoice'), pk=pk)
    follow_up_form = OfferFollowUpForm(instance=offer)
    return render(request, 'sedo/offer_detail.html', _offer_detail_context(offer, follow_up_form))


@login_required
@require_POST
def offer_follow_up(request, pk):
    offer = get_object_or_404(Offer, pk=pk)
    if offer.status != 'offen':
        messages.error(request, 'Eine Wiedervorlage ist nur für offene Angebote möglich.')
        return redirect('sedo:offer_detail', pk)
    form = OfferFollowUpForm(request.POST, instance=offer)
    if not form.is_valid():
        messages.error(request, 'Bitte Datum und Notiz der Wiedervorlage prüfen.')
        return render(request, 'sedo/offer_detail.html', _offer_detail_context(offer, form), status=400)
    changed = form.save(commit=False)
    action = request.POST.get('action', 'save')
    if action == 'contacted':
        changed.last_follow_up_at = timezone.now()
    changed.save(update_fields=['follow_up_date', 'follow_up_note', 'last_follow_up_at', 'updated_at'])
    write_audit(request, 'update', changed, 'Angebots-Wiedervorlage aktualisiert' if action == 'save' else 'Nachfassen beim Angebot protokolliert')
    messages.success(request, 'Wiedervorlage gespeichert.' if action == 'save' else 'Nachfassen protokolliert und nächste Wiedervorlage gespeichert.')
    return redirect('sedo:offer_detail', pk)


@login_required
@require_POST
def offer_generate(request, pk):
    offer = get_object_or_404(Offer, pk=pk)
    generate_offer_pdf(offer)
    write_audit(request, 'generate', offer, 'Angebots-PDF neu erzeugt')
    messages.success(request, 'Angebots-PDF wurde neu erzeugt.')
    return redirect('sedo:offer_detail', pk)


@login_required
@require_POST
def offer_status(request, pk, status):
    offer = get_object_or_404(Offer, pk=pk)
    if status in ['offen', 'angenommen', 'abgelehnt']:
        updates = {'status': status, 'updated_at': timezone.now()}
        if status != 'offen':
            updates['follow_up_date'] = None
        updated = Offer.objects.filter(
            pk=offer.pk, linked_invoice__isnull=True,
        ).exclude(status='in_rechnung').update(**updates)
        if not updated:
            messages.error(request, 'Dieses Angebot ist bereits in Rechnung gestellt; der Status bleibt unverändert.')
            return redirect('sedo:offer_detail', pk)
        write_audit(request, 'update', offer, f'Angebotsstatus auf {status} gesetzt')
        messages.success(request, 'Angebotsstatus aktualisiert.')
    return redirect('sedo:offer_detail', pk)


@login_required
@require_POST
def offer_to_invoice(request, pk):
    offer = get_object_or_404(Offer, pk=pk)
    try:
        inv = create_invoice_from_offer(offer)
    except WorkflowError as e:
        messages.error(request, str(e))
        return redirect('sedo:offer_detail', offer.pk)
    write_audit(request, 'generate', inv, f'Rechnung aus Angebot {offer.number} erzeugt')
    messages.success(request, f'Rechnung {inv.number} aus Angebot {offer.number} erzeugt.')
    return redirect('sedo:invoice_detail', inv.pk)


@login_required
def offer_delete(request, pk):
    offer = get_object_or_404(Offer, pk=pk)
    if request.method == 'POST':
        no = offer.number
        # pflicht=True – siehe invoice_delete.
        try:
            write_audit(request, 'delete', offer,
                        f'Angebot {no} als gelöscht markiert (GoBD-Soft-Delete)', pflicht=True)
        except AuditWriteError:
            messages.error(request, f'Angebot {no} wurde NICHT ausgeblendet: Der Vorgang lässt '
                                    'sich gerade nicht protokollieren. Bitte später erneut versuchen.')
            return redirect('sedo:offer_detail', pk=offer.pk)
        # GoBD: Angebote sind Geschäftsbriefe – nur ausblenden, nicht hart löschen.
        offer.soft_delete()
        messages.success(request, f'Angebot {no} wurde ausgeblendet. Der Datensatz bleibt für die Nachvollziehbarkeit erhalten (GoBD).')
        return redirect('sedo:offer_list')
    return render(request, 'sedo/confirm_delete.html', {'title':'Angebot ausblenden', 'text':f'Angebot {offer.number} wirklich ausblenden? Der Datensatz bleibt GoBD-konform erhalten und wird nur nicht mehr angezeigt.', 'cancel_url': reverse('sedo:offer_detail', args=[pk])})
