"""HTTP-Abläufe für GWG- und Abo-Monatsrechnungen."""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import logging
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from .company_profile import load_profile, seller_post
from .forms import RecurringInvoiceTemplateForm, RecurringInvoiceItemFormSet
from .models import AareonMapping, Customer, Invoice, RecurringInvoiceTemplate, RecurringInvoiceItem
from .services.aareon_mapping import read_aareon_mapping_dataframe as _read_aareon_mapping_dataframe, perform_aareon_mapping_import as _perform_aareon_mapping_import
from .services.date_input import DatumUnlesbar, parse_date_for_view
from .services.item_positions import normalize_item_positions as _normalize_item_positions
from .services.monthly_delivery import monthly_zip_email as _monthly_zip_email
from .services.monthly_invoices import generate_gwg_monthly
from .services.recurring_generation import generate_recurring_invoice_from_template as _generate_recurring_invoice_from_template
from .services.recurring_preview import build_recurring_preview
from .services.recurring_templates import DEFAULT_PAYMENT_DAYS, recurring_payment_days as _recurring_payment_days
from .services.tax_export import parse_month

logger = logging.getLogger('sedo')


def _month_bounds(month_str):
    year, month = parse_month(month_str)
    start = date(year, month, 1)
    if month == 12:
        end = date(year, 12, 31)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    return start, end


def _parse_monthly_decimal(raw, feld, leistung, fehler):
    """Liest Menge/Preis aus dem Monatslauf-Formular – ohne stillen Fallback.

    Früher wurde ein leeres oder unlesbares Feld stillschweigend zu 10,00 €
    bzw. 1,00 (Review-Fund F-16). Bei einem Massenlauf über den GWG-Bestand
    entstanden so hunderte Rechnungen mit Phantasiepreis, korrigierbar nur
    über ebenso viele Stornos. Jetzt wird der Fehler gesammelt und der Lauf
    abgebrochen.
    """
    text = str(raw if raw is not None else '').strip()
    if not text:
        fehler.append(f'{leistung}: {feld} fehlt.')
        return None
    try:
        wert = Decimal(text.replace('.', '').replace(',', '.') if ',' in text else text).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError, ArithmeticError):
        fehler.append(f'{leistung}: „{text}" ist keine gültige Zahl für {feld}.')
        return None
    if wert <= 0:
        fehler.append(f'{leistung}: {feld} muss größer als 0 sein (gelesen: {wert}).')
        return None
    return wert


def _selected_monthly_services(request):
    """Liefert (Leistungen, Fehlerliste) für den Monatslauf.

    Ist die Fehlerliste nicht leer, darf der Lauf nicht starten (F-16).
    """
    services = []
    fehler = []
    for sid in ['Reinigung', 'Winterdienst', 'Heckenschnitt']:
        if request.POST.get(f'use_{sid}'):
            qty = _parse_monthly_decimal(request.POST.get(f'qty_{sid}'), 'Menge', sid, fehler)
            price = _parse_monthly_decimal(request.POST.get(f'price_{sid}'), 'Preis', sid, fehler)
            if qty is None or price is None:
                continue
            desc = request.POST.get(f'desc_{sid}') or sid
            unit = request.POST.get(f'unit_{sid}') or 'Stk.'
            services.append({'id': sid, 'description': desc, 'quantity': qty, 'unit': unit, 'unit_price': price})
    return services, fehler


@login_required
def aareon_import(request):
    """Monatsrechnung/GWG: Mapping importieren und monatliche Rechnungen erzeugen."""
    stats = None
    run_result = None
    today = timezone.localdate()
    default_month = f"{today.year}-{today.month:02d}"
    generated_invoice_ids = request.session.get('last_monthly_invoice_ids', [])
    generated_invoices = Invoice.objects.filter(id__in=generated_invoice_ids).select_related('customer','obj').order_by('number') if generated_invoice_ids else []

    if request.method == 'POST':
        action = request.POST.get('action') or 'import'

        if action == 'import':
            if not request.FILES.get('file'):
                messages.error(request, 'Bitte eine Mapping-Datei auswählen.')
            else:
                file = request.FILES['file']
                try:
                    df = _read_aareon_mapping_dataframe(file)
                    stats = _perform_aareon_mapping_import(df, request)
                except Exception as e:
                    messages.error(request, f'Mapping-Import fehlgeschlagen: {e}')

        elif action == 'send_monthly_email':
            ids = request.POST.getlist('invoice_ids')
            month = request.POST.get('month') or default_month
            invoices = list(Invoice.objects.filter(id__in=ids).select_related('customer').order_by('number'))
            if not invoices:
                messages.error(request, 'Keine Monatsrechnungen für den Versand ausgewählt.')
            elif len({inv.customer_id for inv in invoices}) > 1:
                # Sicherheitsprüfung: Ein manipulierter/fehlerhafter POST darf
                # keine Rechnungen fremder Kunden in die ZIP eines Empfängers mischen.
                messages.error(request, 'Die ausgewählten Rechnungen gehören zu mehreren Kunden. '
                                        'Bitte nur Rechnungen EINES Empfängers für den Sammelversand auswählen.')
            else:
                customer = invoices[0].customer
                try:
                    eml_path = _monthly_zip_email(invoices, customer, month, request=request)
                    return FileResponse(open(eml_path, 'rb'), as_attachment=True, filename=eml_path.name, content_type='message/rfc822')
                except Exception as e:
                    messages.error(request, f'E-Mail-Entwurf konnte nicht erzeugt werden: {e}')

        elif action == 'generate':
            # Fail before customer/object creation or number reservation; each
            # document generator pins its own validated immutable snapshot.
            load_profile()
            customer_id = request.POST.get('customer')
            month = request.POST.get('month') or default_month
            # Alle Eingabefehler in EINEM Durchgang sammeln, damit der Nutzer
            # sie zusammen sieht statt einen nach dem anderen. Stille
            # Ersatzwerte gibt es hier nicht mehr: ein Massenlauf mit falschem
            # Rechnungsdatum oder falschem Zahlungsziel waere sonst nur ueber
            # ebenso viele Stornos zu korrigieren (F-16, NEU-2).
            lauf_fehler = []
            issue_date = None
            ps = pe = None
            try:
                ps, pe = _month_bounds(month)
            except ValueError as fehler:
                lauf_fehler.append(str(fehler))
            try:
                issue_date = parse_date_for_view(request.POST.get('issue_date'), 'Rechnungsdatum')
            except DatumUnlesbar as fehler:
                lauf_fehler.append(str(fehler))
            payment_days = DEFAULT_PAYMENT_DAYS
            payment_days_raw = str(request.POST.get('payment_days') or '').strip()
            if payment_days_raw:
                try:
                    payment_days = int(payment_days_raw)
                    if payment_days < 0:
                        raise ValueError
                except (TypeError, ValueError):
                    lauf_fehler.append(f'Zahlungsziel: „{payment_days_raw}" ist keine gültige Anzahl Tage.')
            services, service_fehler = _selected_monthly_services(request)
            lauf_fehler.extend(service_fehler)
            skip_existing = bool(request.POST.get('skip_existing'))
            limit_raw = str(request.POST.get('limit') or '').strip()
            limit = None
            if limit_raw:
                if limit_raw.isdigit() and int(limit_raw) > 0:
                    limit = int(limit_raw)
                else:
                    lauf_fehler.append(f'Testlimit: „{limit_raw}" ist keine gültige Anzahl.')

            if not customer_id:
                messages.error(request, 'Bitte einen Rechnungsempfänger/Kunden auswählen.')
            elif lauf_fehler:
                messages.error(request, 'Monatslauf abgebrochen – bitte die Eingaben prüfen: '
                                        + ' '.join(lauf_fehler))
            elif not services:
                messages.error(request, 'Bitte mindestens eine Leistung für den Monatslauf auswählen.')
            elif not AareonMapping.objects.exists():
                messages.error(request, 'Bitte zuerst die Mapping-Datei importieren.')
            else:
                customer = get_object_or_404(Customer, pk=customer_id)
                due = issue_date + timedelta(days=payment_days)
                run_result = generate_gwg_monthly(
                    customer=customer, month=month, issue_date=issue_date,
                    due_date=due, period_start=ps, period_end=pe,
                    services=services, skip_existing=skip_existing,
                    limit=limit, request=request,
                )
                generated_invoice_ids = run_result['invoice_ids']
                request.session['last_monthly_invoice_ids'] = generated_invoice_ids
                generated_invoices = Invoice.objects.filter(
                    id__in=generated_invoice_ids,
                ).select_related('customer', 'obj').order_by('number') if generated_invoice_ids else []
                created, skipped, missing = (run_result[key] for key in ('created', 'skipped', 'missing'))
                if created:
                    messages.success(request, f'{created} Monatsrechnungen erzeugt. {skipped} übersprungen. Fehlende Leistungszuordnungen: {missing}.')
                else:
                    messages.warning(request, f'Keine Monatsrechnungen erzeugt. {skipped} übersprungen. Fehlende Leistungszuordnungen: {missing}.')

    context = {
        'stats': stats,
        'run_result': run_result,
        'mappings': AareonMapping.objects.all()[:100],
        'mapping_count': AareonMapping.objects.count(),
        'unit_count': AareonMapping.objects.values('billing_unit_sender').distinct().count(),
        'customers': Customer.objects.all(),
        'default_month': default_month,
        'default_issue_date': today.isoformat(),
        'generated_invoices': generated_invoices,
        'recurring_templates': RecurringInvoiceTemplate.objects.select_related('customer','obj').prefetch_related('items').all(),
        'last_recurring_invoices': Invoice.objects.filter(id__in=request.session.get('last_recurring_invoice_ids', [])).select_related('customer','obj').order_by('number') if request.session.get('last_recurring_invoice_ids') else [],
    }
    return render(request, 'sedo/aareon_monthly.html', context)


@login_required
def recurring_monthly(request):
    today = timezone.localdate()
    try:
        preview_date = date.fromisoformat(request.GET.get('preview_date', today.isoformat()))
        if preview_date.year >= 9999:
            raise ValueError('Kein ausreichender Zeitraum für eine Dreimonatsvorschau.')
    except ValueError:
        preview_date = today
    default_month = f"{preview_date.year}-{preview_date.month:02d}"
    context = {
        'default_month': default_month,
        'default_issue_date': preview_date.isoformat(),
        'preview_months': build_recurring_preview(preview_date),
        'recurring_templates': RecurringInvoiceTemplate.objects.select_related('customer','obj').prefetch_related('items').all(),
        'last_recurring_invoices': Invoice.objects.filter(id__in=request.session.get('last_recurring_invoice_ids', [])).select_related('customer','obj').order_by('number') if request.session.get('last_recurring_invoice_ids') else [],
    }
    return render(request, 'sedo/recurring_monthly.html', context)


@login_required
def aareon_legacy_redirect(request):
    return redirect('sedo:aareon_monthly')


@login_required
def recurring_template_create(request):
    instance = RecurringInvoiceTemplate()
    if request.method == 'POST':
        form = RecurringInvoiceTemplateForm(request.POST, instance=instance)
        formset = RecurringInvoiceItemFormSet(request.POST, instance=instance)
        if form.is_valid() and formset.is_valid():
            tmpl = form.save()
            formset.instance = tmpl
            formset.save()
            _normalize_item_positions(tmpl.items.all())
            messages.success(request, 'Abo-/Monatsrechnungsvorlage gespeichert.')
            return redirect('sedo:recurring_monthly')
    else:
        form = RecurringInvoiceTemplateForm(instance=instance)
        formset = RecurringInvoiceItemFormSet(instance=instance, queryset=RecurringInvoiceItem.objects.none())
    return render(request, 'sedo/recurring_template_form.html', {'form': form, 'formset': formset, 'title': 'Abo-/Monatsrechnung anlegen'})


@login_required
def recurring_template_update(request, pk):
    tmpl = get_object_or_404(RecurringInvoiceTemplate, pk=pk)
    if request.method == 'POST':
        form = RecurringInvoiceTemplateForm(request.POST, instance=tmpl)
        formset = RecurringInvoiceItemFormSet(request.POST, instance=tmpl)
        if form.is_valid() and formset.is_valid():
            form.save()
            formset.save()
            _normalize_item_positions(tmpl.items.all())
            messages.success(request, 'Abo-/Monatsrechnungsvorlage aktualisiert.')
            return redirect('sedo:recurring_monthly')
    else:
        form = RecurringInvoiceTemplateForm(instance=tmpl)
        formset = RecurringInvoiceItemFormSet(instance=tmpl)
    return render(request, 'sedo/recurring_template_form.html', {'form': form, 'formset': formset, 'title': 'Abo-/Monatsrechnung bearbeiten'})


@login_required
def recurring_template_delete(request, pk):
    tmpl = get_object_or_404(RecurringInvoiceTemplate, pk=pk)
    if request.method == 'POST':
        tmpl.delete()
        messages.success(request, 'Abo-/Monatsrechnungsvorlage gelöscht.')
        return redirect('sedo:recurring_monthly')
    return render(request, 'sedo/confirm_delete.html', {'title':'Abo-/Monatsrechnung löschen', 'text':f'Vorlage „{tmpl.title}“ wirklich löschen?', 'cancel_url': reverse('sedo:recurring_monthly')})


@login_required
@seller_post
def recurring_monthly_generate(request):
    if request.method != 'POST':
        return redirect('sedo:recurring_monthly')
    month = request.POST.get('month') or f"{timezone.localdate().year}-{timezone.localdate().month:02d}"
    # Vor dem Vorlagen-Queryset prüfen: ein unlesbares Rechnungsdatum würde
    # sonst still auf HEUTE gesetzt und alle Abo-Rechnungen falsch datieren
    # (NEU-2). Das Rechnungsdatum bestimmt auch den Nummernkreis-Monat.
    try:
        issue_date = parse_date_for_view(request.POST.get('issue_date'), 'Rechnungsdatum')
    except DatumUnlesbar as fehler:
        logger.warning('Abo-Monatslauf abgebrochen: %s', fehler)
        messages.error(request, f'Abo-Monatslauf abgebrochen – {fehler}')
        return redirect('sedo:recurring_monthly')
    skip_existing = bool(request.POST.get('skip_existing'))
    selected_ids = request.POST.getlist('template_ids')
    # Keine implizite Gesamtauswahl: auch ohne JavaScript oder bei manipuliertem
    # POST darf eine leere Auswahl niemals alle aktiven Vorlagen abrechnen.
    if not selected_ids:
        messages.error(request, 'Bitte mindestens eine aktive Abo-Vorlage auswählen. Es wurde keine Rechnung erzeugt.')
        return redirect('sedo:recurring_monthly')
    try:
        selected_ids = {int(value) for value in selected_ids}
        if any(pk <= 0 or pk > 9223372036854775807 for pk in selected_ids):
            raise ValueError('Ungültige Vorlagen-ID')
    except (TypeError, ValueError):
        messages.error(request, 'Die Abo-Auswahl ist ungültig. Bitte die Vorlagen erneut auswählen.')
        return redirect('sedo:recurring_monthly')
    templates = list(RecurringInvoiceTemplate.objects.filter(
        id__in=selected_ids, status='active',
    ).select_related('customer', 'obj'))
    # Auch eine gemischte, inzwischen veraltete Auswahl wird VOR der ersten
    # Rechnung abgewiesen; kein unbemerkt verkleinerter oder erweiterter Lauf.
    if {tmpl.pk for tmpl in templates} != selected_ids:
        messages.error(request, 'Die Abo-Auswahl enthält nicht mehr verfügbare oder pausierte Vorlagen. '
                       'Es wurde keine Rechnung erzeugt. Bitte die Auswahl prüfen.')
        return redirect('sedo:recurring_monthly')
    ps, pe = _month_bounds(month)
    created_ids = []
    skipped = 0
    failed = []
    for tmpl in templates:
        if skip_existing and Invoice.objects.filter(customer=tmpl.customer, obj=tmpl.obj, source='Abo Monatsrechnung', period_start=ps, period_end=pe).exclude(lifecycle='cancelled').exists():
            skipped += 1
            continue
        due = issue_date + timedelta(days=_recurring_payment_days(
            tmpl.payment_days, tmpl.customer.payment_days,
        ))
        try:
            inv = _generate_recurring_invoice_from_template(tmpl, issue_date, ps, pe, due)
            created_ids.append(inv.id)
        except Exception:
            # Eine fehlgeschlagene Vorlage bricht nicht den ganzen Lauf ab;
            # die Rechnung wurde durch die Transaktion vollständig zurückgerollt.
            logger.exception('Abo-Monatslauf: Vorlage „%s“ fehlgeschlagen', tmpl.title)
            failed.append(tmpl.title)
    request.session['last_recurring_invoice_ids'] = created_ids
    if created_ids:
        messages.success(request, f'{len(created_ids)} normale Monatsrechnungen erzeugt. {skipped} übersprungen.')
    else:
        messages.warning(request, f'Keine normalen Monatsrechnungen erzeugt. {skipped} übersprungen.')
    if failed:
        messages.error(request, 'Fehlgeschlagen (vollständig zurückgerollt): ' + ', '.join(failed[:5]))
    return redirect('sedo:recurring_monthly')
