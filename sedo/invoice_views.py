"""HTTP-Abläufe für einzelne Rechnungen und ihren Versand."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.db.models.functions import ExtractYear
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .audit import AuditWriteError, write_audit
from .company_profile import load_profile, seller_post
from .forms import InvoiceForm, InvoiceItemFormSet
from .models import Customer, CustomerObject, Invoice, InvoiceItem, Offer
from .services import (
    WorkflowError, ausblenden_hindernis, create_email_draft, create_storno,
    ensure_correction_allowed, ensure_offer_invoicing_allowed,
    ensure_versand_erlaubt, entwurf_sperren, finalisiere_entwurf,
    generate_invoice_files, mahn_hindernis, next_number,
    send_email_smtp, smtp_is_configured, versand_lage, SMTPDeliveryUncertain,
)
from .services.invoice_archive import lese_archivdatei, pruefe_archiv
from .services.invoice_creation import save_new_invoice
from .services.invoice_delivery import ensure_invoice_zugferd_for_email as _ensure_invoice_zugferd_for_email
from .services.invoice_files import datei_rueckweg
from .services.item_positions import normalize_item_positions as _normalize_item_positions
from .services.mail import invoice_email_subject_body
from .services.recurring_templates import create_recurring_template_from_invoice as _create_recurring_template_from_invoice
from .services.zugferd_validation import validate_invoice_zugferd
from .view_support import (
    _customer_form_data, _customer_from_invoice_form, _object_from_invoice_form,
    _preview_from_forms, _sum_decimal,
)

logger = logging.getLogger('sedo')

def _fill_customer_initial(form, customer):
    if not customer:
        return
    for field in ['salutation','customer_no','customer_type','name','name2','contact_person','phone','email','street','postcode','city','country']:
        if field in form.fields:
            form.initial[field] = getattr(customer, field, '')

def _fill_object_initial(form, obj):
    if not obj:
        return
    mapping = {
        'object_name': 'name',
        'object_street': 'street',
        'object_postcode': 'postcode',
        'object_city': 'city',
        'object_country': 'country',
    }
    for field, attr in mapping.items():
        if field in form.fields:
            form.initial[field] = getattr(obj, attr, '')

def _create_invoice_email_file(invoice: Invoice):
    """Erzeugt eine .eml-Datei zur Rechnung und gibt den Pfad zurück."""
    _ensure_invoice_zugferd_for_email(invoice)
    attachment = lese_archivdatei(invoice)
    recipient = invoice.customer.email or ''
    if not recipient:
        return None
    subject, body = invoice_email_subject_body(invoice)
    out_dir = Path(settings.MEDIA_ROOT) / 'emails' / invoice.number
    return create_email_draft(recipient, subject, body, attachment, out_dir, f'{invoice.number}_email')

def _invoice_items_initial_from_invoice(source: Invoice):
    return [{
        'description': item.description,
        'long_text': item.long_text,
        'quantity': item.quantity,
        'unit': item.unit,
        'unit_price': item.unit_price,
        'article_no': item.article_no,
        'leistungsart_id': item.leistungsart_id,
        'leistungsart_text': item.leistungsart_text,
    } for item in source.items.all()]

@login_required
def invoice_list(request):
    q        = request.GET.get('q', '').strip()
    year_s   = request.GET.get('year', '').strip()
    month_s  = request.GET.get('month', '').strip()
    status_f = request.GET.get('status', '').strip()
    view = request.GET.get('view', 'all')
    views = [('all', 'Alle Belege'), ('draft', 'Entwürfe'),
             ('issued', 'Ausgestellt'), ('corrected', 'Ersetzt'),
             ('cancelled', 'Storniert')]
    if view not in dict(views):
        view = 'all'
    # Ungültige Datumswerte dürfen weder Fehler noch unsichtbare Filter erzeugen.
    year_s = (str(int(year_s)) if year_s.isascii() and year_s.isdigit()
              and len(year_s) <= 4 and 1 <= int(year_s) <= 9999 else '')
    month_s = (str(int(month_s)) if month_s.isascii() and month_s.isdigit()
               and len(month_s) <= 2 and 1 <= int(month_s) <= 12 else '')
    if status_f not in dict(Invoice.STATUS):
        status_f = ''

    invoices = Invoice.objects.select_related('customer', 'obj').all()

    if q:
        invoices = invoices.filter(
            Q(number__icontains=q) | Q(customer__name__icontains=q) | Q(obj__name__icontains=q)
        )
    if year_s.isdigit():
        invoices = invoices.filter(issue_date__year=int(year_s))
    if month_s.isdigit():
        invoices = invoices.filter(issue_date__month=int(month_s))
    # Neue Ansichten nutzen ausschließlich den kanonischen Belegzustand.
    if view != 'all':
        invoices = invoices.filter(lifecycle=view)
    if status_f:
        lifecycle_filter = {'entwurf': 'draft', 'korrigiert': 'corrected', 'storniert': 'cancelled'}.get(status_f)
        invoices = (invoices.filter(lifecycle=lifecycle_filter) if lifecycle_filter
                    else invoices.filter(status=status_f, lifecycle='issued'))

    # Verfügbare Jahre für das Filter-Dropdown
    available_years = list(
        Invoice.objects.annotate(y=ExtractYear('issue_date'))
        .values_list('y', flat=True).distinct().order_by('-y')
    )
    gross_total = _sum_decimal(invoices, 'gross_total')
    page = Paginator(invoices.order_by('-issue_date', '-number', '-pk'), 25).get_page(
        request.GET.get('page'))
    # Nur bekannte Filter übernehmen; Suche bleibt beim Blättern und Tabwechsel erhalten.
    params = request.GET.copy()
    params.clear()
    for key, value in [('q', q), ('year', year_s), ('month', month_s)]:
        if value:
            params[key] = value
    tabs = []
    for key, label in views:
        tab_params = params.copy()
        tab_params['view'] = key
        tabs.append({'key': key, 'label': label, 'url': '?' + tab_params.urlencode()})
    params['view'] = view
    if status_f:
        params['status'] = status_f

    return render(request, 'sedo/invoice_list.html', {
        'invoices': page.object_list,
        'page_obj': page,
        'page_query': params.urlencode(),
        'archive_tabs': tabs,
        'view': view,
        'view_label': dict(views)[view],
        'has_filters': bool(q or year_s or month_s or status_f or view != 'all'),
        'legacy_status_label': dict(Invoice.STATUS).get(status_f, ''),
        'q': q,
        'year': year_s,
        'month': month_s,
        'status': status_f,
        'available_years': available_years,
        'year_without_results': bool(year_s and int(year_s) not in available_years),
        'gross_total': gross_total,
    })

def _invoice_wizard_initial(form):
    """Nur gültige Auswahl-IDs lesen; gebundene Eingaben bleiben unverändert."""
    customer_id = str(form['customer'].value() or '')
    customer = None
    if customer_id.isascii() and customer_id.isdigit() and len(customer_id) < 19:
        customer = Customer.objects.filter(pk=int(customer_id)).first()
    return {
        'customer': _customer_form_data(customer) if customer else None,
        'object_id': str(form['obj'].value() or ''),
        'bound': form.is_bound,
    }

@login_required
@seller_post
def invoice_create(request):
    pre_customer = request.GET.get('customer')
    pre_offer = request.GET.get('from_offer')
    pre_correction = request.GET.get('correction_of')
    initial = {'payment_days': 14, 'issue_date': timezone.localdate(), 'vat_rate': Decimal('19.00')}
    invoice = Invoice(number=next_number('RE'), issue_date=timezone.localdate(), vat_rate=Decimal('19.00'))
    offer = None
    correction_source = None
    item_initial = None

    if pre_offer:
        offer = get_object_or_404(Offer.objects.select_related('customer','obj'), pk=pre_offer)
        try:
            ensure_offer_invoicing_allowed(offer)
        except WorkflowError as e:
            messages.error(request, str(e))
            return redirect('sedo:offer_detail', offer.pk)
        initial.update({'customer': offer.customer_id, 'vat_rate': offer.vat_rate, 'note': f'Diese Rechnung beruht auf Angebot {offer.number}.'})
        if offer.obj_id:
            initial['obj'] = offer.obj_id
        item_initial = [{
            'description': item.description,
            'long_text': item.long_text,
            'quantity': item.quantity,
            'unit': item.unit,
            'unit_price': item.unit_price,
            'article_no': item.article_no,
        } for item in offer.items.all()]
    elif pre_correction:
        correction_source = get_object_or_404(Invoice.objects.select_related('customer','obj'), pk=pre_correction)
        try:
            ensure_correction_allowed(correction_source)
        except WorkflowError as e:
            messages.error(request, str(e))
            return redirect('sedo:invoice_detail', correction_source.pk)
        initial.update({
            'customer': correction_source.customer_id,
            'obj': correction_source.obj_id,
            'vat_rate': correction_source.vat_rate,
            'period_start': correction_source.period_start,
            'period_end': correction_source.period_end,
            'note': f'Korrekturrechnung zu {correction_source.number}.',
        })
        item_initial = _invoice_items_initial_from_invoice(correction_source)
    elif pre_customer:
        customer = get_object_or_404(Customer, pk=pre_customer)
        initial['customer'] = customer.pk
        initial['payment_days'] = customer.payment_days

    # Für Angebote/Korrekturen genug leere/gefüllte Positionsformulare bereitstellen
    extra_count = max(1, len(item_initial or []))
    DynamicInvoiceItemFormSet = InvoiceItemFormSet
    if item_initial:
        from django.forms import inlineformset_factory
        from .forms import InvoiceItemForm
        DynamicInvoiceItemFormSet = inlineformset_factory(Invoice, InvoiceItem, form=InvoiceItemForm, extra=extra_count, can_delete=True, min_num=1, validate_min=True)

    if request.method == 'POST':
        form = InvoiceForm(request.POST, instance=invoice)
        formset = InvoiceItemFormSet(request.POST, instance=invoice)
        action = request.POST.get('action')
        if form.is_valid() and formset.is_valid():
            if action == 'preview':
                preview = _preview_from_forms(form, formset, title='Entwurfsansicht Rechnung')
                return render(request, 'sedo/invoice_preview.html', {'preview': preview, 'back_post': request.POST, 'seller': load_profile()})
            customer = _customer_from_invoice_form(form)
            if not customer:
                form.add_error('name', 'Bitte einen gespeicherten Kunden auswählen oder die Kopfdaten mit Name/Firma ausfüllen.')
            else:
                obj = _object_from_invoice_form(form, customer)
                inv = form.save(commit=False)
                inv.customer = customer
                inv.obj = obj
                # F-63: `or` machte aus einem Zahlungsziel von 0 Tagen still den
                # Kundenwert – dabei erlaubt das Formular 0 ausdrücklich
                # (min_value=0). „Zahlbar sofort" wurde so zu 14 Tagen.
                _zahlungsziel = form.cleaned_data.get('payment_days')
                if _zahlungsziel is None:
                    _zahlungsziel = customer.payment_days
                inv.due_date = inv.issue_date + timedelta(days=_zahlungsziel)
                inv.invoice_type = '380'
                inv.status = 'entwurf' if action == 'draft' else 'erstellt'
                inv.lifecycle = 'draft' if action == 'draft' else 'issued'
                src_offer = None
                src_invoice = None
                if request.POST.get('source_offer'):
                    src_offer = get_object_or_404(Offer, pk=request.POST.get('source_offer'))
                    # Serverseitige Sperre gegen doppelte Rechnungsstellung aus
                    # demselben Angebot (die UI versteckt nur den Button).
                    try:
                        ensure_offer_invoicing_allowed(src_offer)
                    except WorkflowError as e:
                        messages.error(request, str(e))
                        return redirect('sedo:offer_detail', src_offer.pk)
                    inv.source = 'Angebot'
                    inv.note = inv.note or f'Diese Rechnung beruht auf Angebot {src_offer.number}.'
                if request.POST.get('correction_of'):
                    src_invoice = get_object_or_404(Invoice, pk=request.POST.get('correction_of'))
                    try:
                        ensure_correction_allowed(src_invoice)
                    except WorkflowError as e:
                        messages.error(request, str(e))
                        return redirect('sedo:invoice_detail', src_invoice.pk)
                    inv.invoice_type = '384'
                    inv.source = 'Korrektur'
                    inv.reference_invoice = src_invoice
                    inv.note = inv.note or f'Korrekturrechnung zu {src_invoice.number}.'
                # Atomar: Rechnung, Positionen, Dateien und Verknüpfungen
                # entstehen ganz oder gar nicht (keine halben Rechnungen).
                # WorkflowError/Unique-Constraint decken dabei auch den Fall
                # ab, dass zwei parallele Requests die ensure-Guards passieren.
                try:
                    save_new_invoice(inv, formset, action=action, source_offer=src_offer,
                                     correction_source=src_invoice, request=request)
                except WorkflowError as e:
                    messages.error(request, str(e))
                    target = ('sedo:offer_detail', src_offer.pk) if src_offer \
                        else ('sedo:invoice_detail', src_invoice.pk) if src_invoice \
                        else ('sedo:invoice_list', None)
                    return redirect(target[0], target[1]) if target[1] else redirect(target[0])
                except IntegrityError as e:
                    # SQLite meldet die Spalte (reference_invoice_id) statt des
                    # Constraint-Namens; im Korrektur-Kontext ist das eindeutig.
                    if src_invoice and ('reference_invoice_id' in str(e)
                                        or 'invoice_unique_active_correction' in str(e)):
                        messages.error(request, f'Zu Rechnung {src_invoice.number} wurde soeben '
                                                f'bereits eine Korrekturrechnung erzeugt (paralleler Vorgang).')
                        return redirect('sedo:invoice_detail', src_invoice.pk)
                    raise
                except Exception:
                    logger.exception('Rechnungserstellung einschließlich Archiv zurückgerollt')
                    messages.error(request, 'Rechnung nicht erstellt: Dateien oder Pflichtprotokoll '
                                            'konnten nicht gespeichert werden.')
                    return redirect('sedo:invoice_list')
                if request.POST.get('save_as_recurring'):
                    tmpl_title = request.POST.get('recurring_title') or ''
                    tmpl = _create_recurring_template_from_invoice(inv, tmpl_title.strip() or None)
                    messages.success(request, f'Abo-/Monatsrechnungsvorlage „{tmpl.title}“ wurde gespeichert.')
                if action == 'send':
                    if not inv.customer.email:
                        messages.warning(request, f'Rechnung {inv.number} wurde erzeugt, aber es ist keine E-Mail-Adresse beim Kunden hinterlegt.')
                        return redirect('sedo:invoice_detail', inv.pk)
                    try:
                        eml_path = _create_invoice_email_file(inv)
                    except (WorkflowError, OSError) as exc:
                        messages.error(request, f'Rechnung erstellt, E-Mail-Entwurf abgebrochen: {exc}')
                        return redirect('sedo:invoice_detail', inv.pk)
                    if not eml_path or not Path(eml_path).is_file():
                        messages.error(request, 'Rechnung erstellt, E-Mail-Entwurf konnte nicht erzeugt werden.')
                        return redirect('sedo:invoice_detail', inv.pk)
                    write_audit(request, 'create', inv, f'Rechnung {inv.number} erzeugt und E-Mail-Entwurf erstellt',
                                metadata={'vorgang': 'eml_entwurf', 'weg': 'eml'})
                    messages.success(request, f'Rechnung {inv.number} wurde erzeugt und ein E-Mail-Entwurf wurde erstellt. '
                                              'Der tatsächliche Versand muss außerhalb von SEDO erfolgen.')
                    return FileResponse(open(eml_path, 'rb'), as_attachment=True, filename=Path(eml_path).name)
                if action == 'draft':
                    write_audit(request, 'create', inv, f'Rechnungsentwurf {inv.number} gespeichert')
                    messages.success(request, f'Entwurf {inv.number} gespeichert.')
                else:
                    write_audit(request, 'create', inv, f'Rechnung {inv.number} erzeugt')
                    messages.success(request, f'Rechnung {inv.number} erzeugt.')
                return redirect('sedo:invoice_detail', inv.pk)
    else:
        form = InvoiceForm(instance=invoice, initial=initial)
        if pre_customer:
            _fill_customer_initial(form, Customer.objects.get(pk=pre_customer))
        if offer:
            _fill_customer_initial(form, offer.customer)
            _fill_object_initial(form, offer.obj)
        if correction_source:
            _fill_customer_initial(form, correction_source.customer)
            _fill_object_initial(form, correction_source.obj)
        formset = DynamicInvoiceItemFormSet(instance=invoice, queryset=InvoiceItem.objects.none(), initial=item_initial or None)
    return render(request, 'sedo/invoice_form.html', {
        'form': form,
        'formset': formset,
        'title': 'Korrekturrechnung erstellen' if correction_source else 'Neue Rechnung',
        'source_offer': offer,
        'correction_source': correction_source,
        'smtp_configured': smtp_is_configured(),
        'wizard_initial': _invoice_wizard_initial(form),
    })

@login_required
@seller_post
def invoice_edit(request, pk):
    """Nur Entwürfe bearbeiten; niemals Stammdaten neu anlegen."""
    from django.forms import inlineformset_factory
    from .forms import InvoiceItemForm

    invoice = get_object_or_404(Invoice.objects.select_related('customer', 'obj'), pk=pk)
    if invoice.lifecycle != 'draft':
        messages.error(request, 'Nur ein Entwurf kann bearbeitet werden.')
        return redirect('sedo:invoice_detail', pk=pk)
    felder = ('customer', 'obj', 'issue_date', 'period_start', 'period_end', 'vat_rate', 'note')
    tage = ((invoice.due_date - invoice.issue_date).days if invoice.due_date
            else invoice.customer.payment_days)
    form = InvoiceForm(request.POST if request.method == 'POST' else None,
                       instance=invoice, initial={'payment_days': tage})
    # Keine manuellen Kopf-/Objektfelder: was nicht gespeichert wird, soll
    # auch nicht als bearbeitbar erscheinen. Die gespeicherten Stammdaten
    # werden bewusst weder geändert noch dupliziert.
    for name in list(form.fields):
        if name not in (*felder, 'payment_days'):
            form.fields.pop(name)
    form.fields['customer'].required = True
    form.fields['customer'].label = 'Gespeicherter Kunde'
    try:
        kunde_id = int(request.POST.get('customer', '')) if request.method == 'POST' else invoice.customer_id
    except (ValueError, TypeError):
        kunde_id = None
    form.fields['obj'].queryset = CustomerObject.objects.filter(customer_id=kunde_id)
    EditFormSet = inlineformset_factory(Invoice, InvoiceItem, form=InvoiceItemForm,
                                       extra=1, can_delete=True, min_num=1, validate_min=True)
    formset = EditFormSet(request.POST if request.method == 'POST' else None, instance=invoice)
    revision = request.POST.get('revision', '') if request.method == 'POST' else invoice.updated_at.isoformat()
    if request.method == 'POST':
        kopf_ok = form.is_valid()
        positionen_ok = formset.is_valid()
        if positionen_ok:
            eigene_ids = set(invoice.items.values_list('pk', flat=True))
            for positionsform in formset.initial_forms:
                item = positionsform.cleaned_data.get('id')
                if item is None or item.pk not in eigene_ids:
                    positionsform.add_error(None, 'Diese Position gehört nicht zu diesem Entwurf.')
                    positionen_ok = False
        if not revision:
            form.add_error(None, 'Bearbeitungsstand fehlt. Bitte die Seite neu laden.')
        elif kopf_ok and positionen_ok:
            werte = {name: form.cleaned_data[name] for name in felder}
            try:
                with datei_rueckweg() as merken, transaction.atomic():
                    entwurf_sperren(invoice, revision=revision)
                    for name, wert in werte.items():
                        setattr(invoice, name, wert)
                    invoice.due_date = invoice.issue_date + timedelta(days=form.cleaned_data['payment_days'])
                    invoice.save(update_fields=[*felder, 'due_date', 'updated_at'])
                    formset.instance = invoice
                    formset.save()
                    _normalize_item_positions(invoice.items.all())
                    write_audit(request, 'update', invoice, f'Entwurf {invoice.number} bearbeitet',
                                pflicht=True, metadata={'von': 'entwurf', 'nach': 'entwurf',
                                                       'anlass': 'bearbeiten'})
                    merken(invoice)
                    generate_invoice_files(invoice)
                messages.success(request, f'Entwurf {invoice.number} gespeichert.')
                return redirect('sedo:invoice_detail', pk=pk)
            except WorkflowError as fehler:
                messages.error(request, str(fehler))
            except Exception:
                logger.exception('Entwurfsbearbeitung zurückgerollt')
                messages.error(request, 'Der Entwurf wurde nicht geändert. Datei oder Protokoll '
                                        'konnte nicht gespeichert werden; bitte erneut versuchen.')
            return redirect('sedo:invoice_detail', pk=pk)
    return render(request, 'sedo/invoice_edit.html', {
        'invoice': invoice, 'form': form, 'formset': formset, 'revision': revision,
        'objekte': list(CustomerObject.objects.values('id', 'customer_id', 'name')),
    })

@login_required
@require_POST
def invoice_finalize(request, pk):
    """Bestätigung und Finalisierung; GET verändert grundsätzlich nichts."""
    invoice = get_object_or_404(Invoice.objects.select_related('customer', 'obj'), pk=pk)
    if invoice.lifecycle != 'draft':
        messages.error(request, 'Dieser Beleg ist kein Entwurf mehr.')
        return redirect('sedo:invoice_detail', pk=pk)
    if request.POST.get('confirm') != '1':
        heute = timezone.localdate()
        return render(request, 'sedo/invoice_finalize.html', {
            'invoice': invoice, 'revision': invoice.updated_at.isoformat(), 'heute': heute,
            'vormonat': (invoice.issue_date.year, invoice.issue_date.month) < (heute.year, heute.month),
        })
    try:
        revision = request.POST.get('revision')
        if not revision:
            raise WorkflowError('Bestätigungsstand fehlt. Bitte die Finalisierung erneut öffnen.')
        finalisiere_entwurf(invoice, request, datum_heute=request.POST.get('datum_heute') == '1',
                           datum_bestaetigt=True, revision=revision)
    except WorkflowError as fehler:
        messages.error(request, str(fehler))
    except Exception:
        logger.exception('Finalisierung zurückgerollt')
        messages.error(request, 'Der Entwurf wurde nicht finalisiert. Datei oder Protokoll '
                                'konnte nicht gespeichert werden; bitte erneut versuchen.')
    else:
        messages.success(request, f'Rechnung {invoice.number} wurde finalisiert. Sie wurde nicht versendet.')
    return redirect('sedo:invoice_detail', pk=pk)

@login_required
def invoice_detail(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related('customer','obj'), pk=pk)
    # Rückwärtsrelation läuft über all_objects; soft-gelöschte Zahlungen ausblenden.
    payments = invoice.payments.filter(deleted_at__isnull=True).order_by('-booking_date')
    return render(request, 'sedo/invoice_detail.html', {
        'invoice': invoice,
        'invoice_payments': payments,
        'archiv_status': pruefe_archiv(invoice) if invoice.lifecycle != 'draft' else None,
        # Leerer String = Ausblenden erlaubt; sonst der Grund im Klartext für
        # den deaktivierten Knopf (F-14).
        'ausblenden_hindernis': ausblenden_hindernis(invoice),
        'mahn_hindernis': mahn_hindernis(invoice),
        'external_send_unconfirmed': versand_lage(invoice) == 'unklar',
        'smtp_configured': smtp_is_configured(),
    })

@login_required
def invoice_zugferd_validation(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related('customer'), pk=pk)
    if invoice.lifecycle == 'draft':
        messages.error(request, 'Ein Entwurf besitzt noch keine versandfähige ZUGFeRD-Datei.')
        return redirect('sedo:invoice_detail', pk=pk)
    result = validate_invoice_zugferd(invoice)
    return render(request, 'sedo/invoice_zugferd_validation.html', {
        'invoice': invoice, 'validation': result,
        'smtp_configured': smtp_is_configured(),
    })

@login_required
@require_POST
def invoice_generate(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    try:
        generate_invoice_files(invoice, request=request)
    except WorkflowError as exc:
        messages.error(request, str(exc))
    except Exception:
        logger.exception('Dateierzeugung zurückgerollt')
        messages.error(request, 'Dateien konnten nicht gespeichert werden. Vorhandene Originale bleiben erhalten.')
    else:
        if invoice.lifecycle == 'draft':
            write_audit(request, 'generate', invoice, 'Entwurfs-PDF neu erzeugt')
            messages.success(request, 'Entwurfs-PDF wurde neu erzeugt.')
        else:
            messages.info(request, 'Ausgestellte Belegdateien bleiben unverändert. '
                                   'Für Änderungen bitte eine Korrekturrechnung erstellen.')
    return redirect('sedo:invoice_detail', pk)

@login_required
@require_POST
def invoice_confirm_external_send(request, pk):
    """Explizite Bestätigung nach geprüftem externem Versand oder SMTP-Abbruch."""
    invoice = get_object_or_404(Invoice.objects.select_related('customer'), pk=pk)
    if request.POST.get('confirmed') != '1':
        messages.error(request, 'Bitte den tatsächlichen Versand zuerst prüfen und bestätigen.')
        return redirect('sedo:invoice_detail', pk)
    try:
        with transaction.atomic():
            invoice = Invoice.objects.select_for_update().get(pk=pk)
            ensure_versand_erlaubt(invoice)
            if versand_lage(invoice) != 'unklar':
                raise WorkflowError('Für diese Rechnung liegt kein ungeklärter E-Mail-Versand vor.')
            write_audit(request, 'generate', invoice,
                        f'Externer Versand von Rechnung {invoice.number} durch Nutzer bestätigt',
                        metadata={'vorgang': 'versand', 'weg': 'extern_bestaetigt'},
                        pflicht=True)
            if invoice.status == 'erstellt':
                invoice.status = 'versendet'
                invoice.save(update_fields=['status', 'updated_at'])
    except (WorkflowError, AuditWriteError) as exc:
        messages.error(request, str(exc))
        return redirect('sedo:invoice_detail', pk)
    except Exception:
        logger.exception('Bestätigung des externen Rechnungsversands fehlgeschlagen')
        messages.error(request, 'Der Versand konnte nicht bestätigt werden. Bitte erneut prüfen.')
        return redirect('sedo:invoice_detail', pk)
    messages.success(request, f'Der Versand von Rechnung {invoice.number} wurde bestätigt und protokolliert.')
    return redirect('sedo:invoice_detail', pk)

@login_required
@require_POST
def invoice_email(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related('customer'), pk=pk)
    if not invoice.customer.email:
        messages.error(request, 'Beim Kunden ist keine E-Mail-Adresse hinterlegt.')
        return redirect('sedo:invoice_detail', pk)
    # Ein Entwurf darf nicht hinausgehen: sein PDF trägt zwar ein
    # ENTWURF-Wasserzeichen, die eingebettete XML war bisher aber eine
    # vollgültige E-Rechnung, die das Empfängersystem regulär verbucht (F-13).
    try:
        ensure_versand_erlaubt(invoice)
    except WorkflowError as fehler:
        messages.error(request, str(fehler))
        return redirect('sedo:invoice_detail', pk)

    # Ausgestellte SEDO-Belege besitzen beide Dateien. Alte Datensätze ohne
    # Dateiverweis laufen anschließend wie bisher in die harte Archivprüfung,
    # die den Versand ebenfalls stoppt. Diese Trennung hält Import-/Altbelege
    # verständlich und erlaubt keine Umgehung bei echten ZUGFeRD-Dateien.
    try:
        _ensure_invoice_zugferd_for_email(invoice)
    except WorkflowError as exc:
        messages.error(request, f'Versand gestoppt: {exc}')
        return redirect('sedo:invoice_zugferd_validation', pk=pk)

    # Nur gespeicherte Originalbytes; fehlendes Archiv ist kein Generierungsauftrag.
    try:
        attachment = lese_archivdatei(invoice)
    except WorkflowError as exc:
        messages.error(request, str(exc))
        return redirect('sedo:invoice_detail', pk)
    recipient = invoice.customer.email
    subject, body = invoice_email_subject_body(invoice)
    # Optionaler individueller Begleittext (z. B. per Magic-Tone erzeugt und geprüft)
    custom_body = (request.POST.get('body') or '').strip()
    if custom_body:
        body = custom_body

    # Wenn SMTP konfiguriert: direkt senden, sonst .eml zum Download
    if smtp_is_configured():
        try:
            send_email_smtp(recipient, subject, body, attachment)
        except SMTPDeliveryUncertain as exc:
            logger.warning('SMTP-Status für Rechnung %s unklar (%s)',
                           invoice.number, type(exc.__cause__ or exc).__name__)
            write_audit(request, 'generate', invoice,
                        f'SMTP-Versandstatus für Rechnung {invoice.number} unklar',
                        metadata={'vorgang': 'versand_ungeklaert', 'weg': 'smtp'})
            messages.warning(request, str(exc))
            return redirect('sedo:invoice_detail', pk)
        except Exception as e:
            logger.exception('SMTP-Rechnungsversand fehlgeschlagen')
            messages.error(request, f'E-Mail-Versand fehlgeschlagen: {e}')
            return redirect('sedo:invoice_detail', pk)
        # Die Mail ist bereits angenommen. Ein lokaler Datenbankfehler darf
        # jetzt nicht als fehlgeschlagene Zustellung erscheinen.
        status_error = False
        if invoice.status == 'erstellt':
            try:
                invoice.status = 'versendet'
                invoice.save(update_fields=['status', 'updated_at'])
            except Exception:
                logger.exception('SMTP-Mail angenommen, Rechnungsstatus konnte nicht gespeichert werden')
                status_error = True
        write_audit(request, 'generate', invoice,
                    f'Rechnung {invoice.number} direkt per SMTP versendet an {recipient}',
                    metadata={'vorgang': 'versand', 'weg': 'smtp'})
        if status_error:
            messages.warning(request, f'Rechnung {invoice.number} wurde per SMTP angenommen, '
                                      'aber der lokale Rechnungsstatus konnte nicht gespeichert werden. '
                                      'Bitte den Status prüfen; nicht blind erneut senden.')
        else:
            messages.success(request, f'Rechnung {invoice.number} wurde direkt an {recipient} versendet.')
        return redirect('sedo:invoice_detail', pk)
    else:
        # Fallback: .eml-Datei zum Download
        out_dir = Path(settings.MEDIA_ROOT) / 'emails' / invoice.number
        try:
            eml_path = create_email_draft(recipient, subject, body, attachment, out_dir, f'{invoice.number}_email')
        except OSError as exc:
            messages.error(request, f'E-Mail-Entwurf konnte nicht gespeichert werden: {exc}')
            return redirect('sedo:invoice_detail', pk)
        if not eml_path or not Path(eml_path).is_file():
            # Erst prüfen, dann Status setzen: vorher stand die Rechnung schon
            # auf 'versendet', obwohl die Datei gar nicht entstanden war (F-115).
            messages.error(request, 'E-Mail-Entwurf konnte nicht erzeugt werden.')
            return redirect('sedo:invoice_detail', pk)
        write_audit(request, 'generate', invoice, 'E-Mail-Entwurf (.eml) zur Rechnung erzeugt',
                    metadata={'vorgang': 'eml_entwurf', 'weg': 'eml'})
        return FileResponse(open(eml_path, 'rb'), as_attachment=True, filename=Path(eml_path).name)

@login_required
def invoice_correction(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    # Serverseitige Sperre: stornierte/bereits korrigierte Rechnungen und
    # Stornos dürfen nicht als Korrekturgrundlage geladen werden.
    try:
        ensure_correction_allowed(invoice)
    except WorkflowError as e:
        messages.error(request, str(e))
        return redirect('sedo:invoice_detail', invoice.pk)
    return redirect(f"{reverse('sedo:invoice_create')}?correction_of={invoice.pk}")

@login_required
@require_POST
def invoice_storno(request, pk):
    original = get_object_or_404(Invoice, pk=pk)
    try:
        storno = create_storno(original, request=request)
    except AuditWriteError:
        messages.error(request, 'Es wurde kein Storno erzeugt: Der Vorgang konnte nicht protokolliert werden.')
        return redirect('sedo:invoice_detail', original.pk)
    except WorkflowError as e:
        messages.error(request, str(e))
        return redirect('sedo:invoice_detail', original.pk)
    write_audit(request, 'generate', storno, f'Stornorechnung zu {original.number} erzeugt')
    messages.success(request, f'Stornorechnung {storno.number} erzeugt.')
    return redirect('sedo:invoice_detail', storno.pk)

@login_required
def invoice_delete(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    if request.method == 'POST':
        no = invoice.number
        try:
            with transaction.atomic():
                # Erster Schreibzugriff schützt vor parallelem Finalisieren.
                bestaetigter_stand = request.POST.get('invoice_revision')
                if bestaetigter_stand and bestaetigter_stand != invoice.updated_at.isoformat():
                    raise WorkflowError('Der Beleg wurde nach der Bestätigung geändert. Bitte erneut prüfen.')
                aktualisiert = Invoice.objects.filter(
                    pk=pk, status=invoice.status, updated_at=invoice.updated_at,
                ).update(updated_at=timezone.now())
                if not aktualisiert:
                    raise WorkflowError('Der Beleg wurde inzwischen geändert. Bitte die Seite neu laden.')
                invoice.refresh_from_db()
                hindernis = ausblenden_hindernis(invoice)
                if hindernis:
                    raise WorkflowError(hindernis)
                angebote = (list(Offer.all_objects.filter(linked_invoice_id=pk))
                            if invoice.lifecycle == 'draft' else [])
                bezug = ', '.join(angebot.number for angebot in angebote)
                write_audit(request, 'delete', invoice,
                            f'Rechnung {no} als gelöscht markiert (GoBD-Soft-Delete)'
                            + (f'; Angebote: {bezug}' if bezug else ''), pflicht=True,
                            metadata={'anlass': 'entwurf_verwerfen' if invoice.lifecycle == 'draft'
                                      else 'ausblenden', 'rechnungsnummer': no, 'angebote': bezug})
                for angebot in angebote:
                    vorher = angebot.status
                    if not Offer.all_objects.filter(pk=angebot.pk, linked_invoice_id=pk).update(
                            linked_invoice=None, status='angenommen', updated_at=timezone.now()):
                        raise WorkflowError('Die Angebotsverknüpfung wurde inzwischen geändert.')
                    angebot.refresh_from_db()
                    write_audit(request, 'update', angebot,
                                f'Angebot {angebot.number} nach Verwerfen von Entwurf {no} freigegeben',
                                pflicht=True, metadata={'von': vorher, 'nach': 'angenommen',
                                                       'anlass': 'entwurf_verwerfen',
                                                       'rechnungsnummer': no,
                                                       'angebotsnummer': angebot.number})
                invoice.soft_delete()
        except AuditWriteError:
            messages.error(request, f'Rechnung {no} wurde NICHT ausgeblendet: Der Vorgang lässt '
                                    'sich gerade nicht protokollieren. Bitte später erneut versuchen.')
            return redirect('sedo:invoice_detail', pk=pk)
        except WorkflowError as fehler:
            messages.error(request, str(fehler))
            return redirect('sedo:invoice_detail', pk=pk)
        messages.success(request, f'Rechnung {no} wurde ausgeblendet. Der Datensatz bleibt erhalten.')
        return redirect('sedo:invoice_list')
    return render(request, 'sedo/confirm_delete.html', {
        'title': 'Entwurf verwerfen' if invoice.lifecycle == 'draft' else 'Rechnung ausblenden',
        'text': f'Beleg {invoice.number} wirklich ausblenden? Der Datensatz bleibt erhalten.',
        'invoice_revision': invoice.updated_at.isoformat(),
        'cancel_url': reverse('sedo:invoice_detail', args=[pk]),
    })
