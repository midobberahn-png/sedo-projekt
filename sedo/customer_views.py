"""Kunden- und Objektansichten einschließlich gesichertem Tabellenimport."""
from __future__ import annotations

from decimal import Decimal
import pandas as pd
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from .audit import write_audit
from .forms import CustomerForm, CustomerObjectForm, CustomerNoteForm
from .models import Customer, CustomerObject, Invoice, Offer
from .services import offene_rechnungen
from .services.customer_timeline import KINDS as TIMELINE_KINDS, build_customer_timeline
from .view_support import _customer_form_data


@login_required
def customer_data_api(request, pk):
    """JSON-Endpoint: Kundendaten + Objekte für AJAX-Autobefüllung im Rechnungsformular."""
    customer = get_object_or_404(Customer, pk=pk)
    return JsonResponse(_customer_form_data(customer))


def _norm(v):
    if v is None:
        return ''
    if isinstance(v, float) and pd.isna(v):
        return ''
    return str(v).strip()


def _pick(row, *names):
    lowered = {str(k).strip().lower(): v for k, v in row.items()}
    for name in names:
        key = name.lower()
        if key in lowered:
            return _norm(lowered[key])
    # fuzzy contains
    for key, val in lowered.items():
        for name in names:
            if name.lower() in key:
                return _norm(val)
    return ''


def _pick_customer_name(row):
    """Pick a customer name without accidentally using customer number columns."""
    lowered = {str(k).strip().lower(): v for k, v in row.items()}
    exact_candidates = ['name / firma', 'firma', 'kundenname', 'kunde name', 'name', 'suchname']
    for key in exact_candidates:
        if key in lowered:
            val = _norm(lowered[key])
            if val:
                return val
    for key, val in lowered.items():
        k = key.lower()
        if any(block in k for block in ['kundennr', 'kunden-nr', 'kundennummer', 'kunden nr', 'kd-nr', 'kdnr', 'nummer', 'nr.', 'id']):
            continue
        if any(token in k for token in ['firma', 'kunde', 'name', 'suchname']):
            val = _norm(val)
            if val:
                return val
    return ''


@login_required
def customer_list(request):
    q = request.GET.get('q','').strip()
    customers = Customer.objects.all()
    if q:
        customers = customers.filter(Q(name__icontains=q) | Q(customer_no__icontains=q) | Q(email__icontains=q) | Q(city__icontains=q))
    return render(request, 'sedo/customer_list.html', {'customers': customers, 'q': q})


@login_required
def customer_import(request):
    stats = None
    if request.method == 'POST' and request.FILES.get('file'):
        file = request.FILES['file']
        from .import_probe import read_dataframe
        try:
            rows = read_dataframe(file).to_dict('records')
        except (ValueError, OSError) as exc:
            messages.error(request, str(exc))
            return render(request, 'sedo/customer_import.html', {'stats': None}, status=400)
        created = updated = 0
        with transaction.atomic():
            for row in rows:
                customer_no = _pick(row, 'Kunden-Nr.', 'Kundennummer', 'Kunden Nr', 'Kdnr', 'KdNr')
                name_value = _pick_customer_name(row)
                if not name_value:
                    continue
                hausnummer = _pick(row, 'Hausnummer', 'Hausnr.')
                gelesen = {
                    'salutation': _pick(row, 'Anrede'),
                    'customer_type': _pick(row, 'Kundentyp'),
                    'name': name_value,
                    'name2': _pick(row, 'Name Zusatz', 'Zusatz', 'Adresszusatz'),
                    'contact_person': _pick(row, 'Ansprechpartner', 'Kontaktperson'),
                    'phone': _pick(row, 'Telefon', 'Tel', 'Telefonnummer', 'Mobil'),
                    'email': _pick(row, 'E-Mail', 'Email', 'Mail'),
                    'street': (_pick(row, 'Straße', 'Strasse', 'Adresse')
                               + (' ' + hausnummer if hausnummer else '')).strip(),
                    'postcode': _pick(row, 'PLZ', 'Postleitzahl'),
                    'city': _pick(row, 'Stadt', 'Ort'),
                    'country': _pick(row, 'Land'),
                }
                # F-12: Nur befüllte Felder schreiben. Fehlt eine Spalte in der Datei
                # oder ist die Zelle leer, liefert _pick '' – das wurde bisher in den
                # Bestandskunden geschrieben und löschte E-Mail, Telefon und Adresse.
                # Für Neuanlagen greifen die Model-Defaults (customer_type='einmalig',
                # country='DE'), deshalb müssen sie hier nicht gesetzt werden.
                defaults = {feld: wert for feld, wert in gelesen.items() if wert}
                if customer_no:
                    obj, was_created = Customer.objects.update_or_create(customer_no=customer_no, defaults=defaults)
                else:
                    obj, was_created = Customer.objects.update_or_create(name=name_value, defaults=defaults)
                created += 1 if was_created else 0
                updated += 0 if was_created else 1
        stats = {'created': created, 'updated': updated, 'total': created + updated}
        messages.success(request, f"Kundenimport abgeschlossen: {created} neu, {updated} aktualisiert. "
                                  f"Leere Felder in der Datei lassen vorhandene Stammdaten unverändert.")
    return render(request, 'sedo/customer_import.html', {'stats': stats})


@login_required
def customer_create(request):
    if request.method == 'POST':
        form = CustomerForm(request.POST)
        if form.is_valid():
            c = form.save()
            messages.success(request, 'Kunde gespeichert.')
            return redirect('sedo:customer_detail', c.pk)
    else:
        form = CustomerForm()
    return render(request, 'sedo/customer_form.html', {'form': form, 'title': 'Neuen Kunden anlegen'})


@login_required
def customer_update(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    if request.method == 'POST':
        form = CustomerForm(request.POST, instance=customer)
        if form.is_valid():
            form.save()
            messages.success(request, 'Kunde aktualisiert.')
            return redirect('sedo:customer_detail', customer.pk)
    else:
        form = CustomerForm(instance=customer)
    return render(request, 'sedo/customer_form.html', {'form': form, 'title': 'Kunden bearbeiten'})


@login_required
def customer_detail(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    # Standardmanager statt Rückwärtsrelation: ausgeblendete Belege bleiben unsichtbar.
    invoices = Invoice.objects.filter(customer=customer).select_related('obj').order_by('-issue_date', '-number', '-pk')
    offers = Offer.objects.filter(customer=customer).select_related('obj').order_by('-issue_date', '-number', '-pk')
    objects = customer.customer_objects.order_by('name', 'pk')
    open_invoices = offene_rechnungen(invoices)
    overdue = open_invoices.filter(due_date__lt=timezone.localdate())
    sections = [('overview', 'Übersicht'), ('timeline', 'Chronik'), ('invoices', 'Rechnungen'),
                ('offers', 'Angebote'), ('objects', 'Objekte'), ('details', 'Stammdaten')]
    section = request.GET.get('section', 'overview')
    if section not in dict(sections):
        section = 'overview'
    q = request.GET.get('q', '').strip() if section in ('invoices', 'offers', 'objects') else ''
    invoice_filter = request.GET.get('filter', 'all') if section == 'invoices' else 'all'
    if invoice_filter not in ('all', 'draft', 'open', 'overdue'):
        invoice_filter = 'all'
    page = None
    timeline_kind = request.GET.get('kind', 'all') if section == 'timeline' else 'all'
    if timeline_kind not in dict(TIMELINE_KINDS):
        timeline_kind = 'all'
    timeline_entries = []
    if section in ('invoices', 'offers', 'objects'):
        records = {'invoices': invoices, 'offers': offers, 'objects': objects}[section]
        if section == 'invoices':
            if invoice_filter == 'draft':
                records = records.filter(lifecycle='draft')
            elif invoice_filter in ('open', 'overdue'):
                records = open_invoices if invoice_filter == 'open' else overdue
        if q:
            if section == 'objects':
                records = records.filter(Q(name__icontains=q) | Q(street__icontains=q) | Q(city__icontains=q))
            else:
                records = records.filter(Q(number__icontains=q) | Q(obj__name__icontains=q))
        page = Paginator(records, 20).get_page(request.GET.get('page'))
    elif section == 'timeline':
        page = Paginator(build_customer_timeline(customer, timeline_kind), 30).get_page(request.GET.get('page'))
        timeline_entries = page.object_list
    elif section == 'overview':
        timeline_entries = build_customer_timeline(customer)[:5]
    # Nur die gewählte Liste laden; Übersicht begrenzen, Kennzahlen bleiben vollständig.
    params = request.GET.copy()
    params.clear()
    params['section'] = section
    if q:
        params['q'] = q
    if invoice_filter != 'all':
        params['filter'] = invoice_filter
    if timeline_kind != 'all':
        params['kind'] = timeline_kind
    return render(request, 'sedo/customer_detail.html', {
        'customer': customer,
        'section': section,
        'section_label': dict(sections)[section],
        'customer_tabs': [{'key': key, 'label': label} for key, label in sections],
        'q': q,
        'invoice_filter': invoice_filter,
        'timeline_kind': timeline_kind,
        'timeline_kinds': TIMELINE_KINDS,
        'timeline_entries': timeline_entries,
        'note_form': CustomerNoteForm(),
        'page_obj': page,
        'page_query': params.urlencode(),
        'invoice_count': invoices.count(),
        'offer_count': offers.count(),
        'object_count': objects.count(),
        'draft_count': invoices.filter(lifecycle='draft').count(),
        'open_count': open_invoices.count(),
        'overdue_count': overdue.count(),
        # Die aktive Zahlungssumme stammt bereits aus offene_rechnungen();
        # keine zusätzliche Zahlungsabfrage für jeden Kundenbeleg.
        'open_total': sum(((gross - paid).quantize(Decimal('0.01'))
                           for gross, paid in open_invoices.values_list('gross_total', '_active_paid')),
                          Decimal('0.00')) if section == 'overview' else None,
        'customer_invoices': page.object_list if section == 'invoices' else invoices[:5] if section == 'overview' else [],
        'customer_offers': page.object_list if section == 'offers' else offers[:3] if section == 'overview' else [],
        'customer_objects': page.object_list if section == 'objects' else [],
    })


@login_required
@require_POST
def customer_note_create(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    form = CustomerNoteForm(request.POST)
    if form.is_valid():
        note = form.save(commit=False)
        note.customer = customer
        note.author = request.user
        note.save()
        write_audit(request, 'create', note, f'Notiz in Kundenakte {customer.name} angelegt')
        messages.success(request, 'Notiz zur Kunden-Chronik hinzugefügt.')
    else:
        messages.error(request, 'Die Notiz ist leer oder zu lang.')
    return redirect(f"{reverse('sedo:customer_detail', args=[customer.pk])}?section=timeline")


@login_required
def object_create(request):
    if request.method == 'POST':
        form = CustomerObjectForm(request.POST)
        if form.is_valid():
            obj = form.save()
            messages.success(request, 'Objekt gespeichert.')
            return redirect('sedo:customer_detail', obj.customer_id)
    else:
        form = CustomerObjectForm(initial={'customer': request.GET.get('customer')})
    return render(request, 'sedo/object_form.html', {'form': form, 'title':'Objekt anlegen'})


@login_required
def object_update(request, pk):
    obj = get_object_or_404(CustomerObject, pk=pk)
    if request.method == 'POST':
        form = CustomerObjectForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, 'Objekt aktualisiert.')
            return redirect('sedo:customer_detail', obj.customer_id)
    else:
        form = CustomerObjectForm(instance=obj)
    return render(request, 'sedo/object_form.html', {'form': form, 'title':'Objekt bearbeiten'})


@login_required
def object_delete(request, pk):
    obj = get_object_or_404(CustomerObject, pk=pk)
    customer_id = obj.customer_id
    if request.method == 'POST':
        obj.delete()
        messages.success(request, 'Objekt gelöscht.')
        return redirect('sedo:customer_detail', customer_id)
    return render(request, 'sedo/confirm_delete.html', {'title':'Objekt löschen', 'text':f'Objekt „{obj}“ wirklich löschen?', 'cancel_url': reverse('sedo:customer_detail', args=[customer_id])})
