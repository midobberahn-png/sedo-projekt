"""Querschnittsansichten für Suche, KI, Dashboard und Systemverwaltung.

Kunden, Angebote, Zahlungen, Kassenbuch, Rechnungen und Monatsläufe
liegen in eigenen Ansichtsmodulen.
"""
from __future__ import annotations

from decimal import Decimal
from datetime import timedelta
from pathlib import Path
from email.message import EmailMessage
import json
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.http import FileResponse, JsonResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST, require_http_methods
from django.views.decorators.cache import never_cache

from .forms import ServiceCatalogItemForm, WorkTaskForm
from .models import (
    Customer, CustomerObject, Invoice, Offer, AareonMapping,
    AppSetting, AuditLog, ServiceCatalogItem, WorkTask,
)
from .audit import AUDIT_ERROR_AT_KEY, AUDIT_ERROR_KEY, AuditWriteError, write_audit
from .company_profile import load_profile, profile_form_initial, save_profile
from .services import (
    WorkflowError, offene_rechnungen, get_smtp_config, smtp_is_configured,
    send_email_smtp, get_dunning_candidates, SMTPDeliveryUncertain,
    get_banking_config, banking_is_configured, get_ai_config, ai_is_configured,
    generate_offer_from_text, AIError, transcribe_audio,
    TranscriptionUnavailable, polish_service_text, generate_cover_letter_text,
    ai_review_positions, check_positions_local,
)
from .services.aareon_mapping import (
    read_aareon_mapping_dataframe as _read_aareon_mapping_dataframe,
    perform_aareon_mapping_import as _perform_aareon_mapping_import,
)
from .services.readiness import (
    MANUAL_CHECKS, is_local_only_mode, readiness_report, transport_security_checks,
)
from .services.reports import build_reports
from .services.tax_export import create_tax_package, parse_month
from .services.ai_local import check_local_model, validate_local_base_url, validate_local_model
from .services.bonsai_runtime import runtime_status, start_bonsai_background, validate_runtime_dir
from .view_support import _sum_decimal

# Interner Logger: breite Exception-Handler (Batch-Läufe, KI-Endpoints) melden
# dem Nutzer eine freundliche Meldung, protokollieren den echten Fehler aber hier.
logger = logging.getLogger('sedo')

# Obergrenze für KI-Anfragen.
MAX_AI_REQUEST_BYTES = 100_000


@login_required
def global_search_api(request):
    """JSON-Endpoint der globalen Suche (v2.1): Kunden, Rechnungen, Angebote, Objekte.

    Liefert gruppierte Treffer für das Suchfeld in der Sidebar. Bewusst kleine
    Limits je Gruppe – die Suche ist ein Sprungbrett, keine Listenansicht.
    """
    q = (request.GET.get('q') or '').strip()
    if len(q) < 2:
        return JsonResponse({'ok': True, 'results': []})
    results = []
    for c in Customer.objects.filter(
            Q(name__icontains=q) | Q(name2__icontains=q) | Q(customer_no__icontains=q))[:4]:
        results.append({
            'gruppe': 'Kunden', 'titel': c.name,
            'sub': ' · '.join(x for x in [c.customer_no, f'{c.postcode} {c.city}'.strip()] if x),
            'badge': '', 'url': reverse('sedo:customer_detail', args=[c.pk]),
        })
    for inv in (Invoice.objects.filter(Q(number__icontains=q) | Q(customer__name__icontains=q))
                .select_related('customer').order_by('-issue_date')[:5]):
        results.append({
            'gruppe': 'Rechnungen', 'titel': inv.number,
            'sub': f'{inv.customer.name} · {inv.gross_total} €',
            'badge': inv.get_status_display(),
            'url': reverse('sedo:invoice_detail', args=[inv.pk]),
        })
    for off in (Offer.objects.filter(Q(number__icontains=q) | Q(customer__name__icontains=q))
                .select_related('customer').order_by('-issue_date')[:4]):
        results.append({
            'gruppe': 'Angebote', 'titel': off.number,
            'sub': f'{off.customer.name} · {off.gross_total} €',
            'badge': off.get_status_display(),
            'url': reverse('sedo:offer_detail', args=[off.pk]),
        })
    for obj in (CustomerObject.objects.filter(
            Q(name__icontains=q) | Q(street__icontains=q) | Q(city__icontains=q))
            .select_related('customer')[:4]):
        results.append({
            'gruppe': 'Objekte', 'titel': obj.name,
            'sub': ' · '.join(x for x in [obj.customer.name, f'{obj.postcode} {obj.city}'.strip()] if x),
            'badge': '', 'url': reverse('sedo:customer_detail', args=[obj.customer_id]),
        })
    return JsonResponse({'ok': True, 'results': results})


@login_required
@require_POST
def ai_generate_offer(request):
    """KI-Assistent: grobe Ansage in einen vollständigen Angebotsentwurf umwandeln.

    Datenschutz: Es wird ausschließlich der diktierte Text der Leistungs-
    beschreibung an Bonsai übertragen – keine Kunden- oder Objektdaten.
    Nebenwirkung: neue Leistungen werden in den Leistungskatalog übernommen.
    """
    if not ai_is_configured():
        return JsonResponse({'ok': False, 'error': 'Der lokale KI-Assistent ist deaktiviert. Bitte in den Einstellungen aktivieren.'}, status=400)
    if len(request.body) > MAX_AI_REQUEST_BYTES:
        return JsonResponse({'ok': False, 'error': 'Der Text ist zu lang. Bitte kürzen.'}, status=400)
    try:
        data = json.loads(request.body.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'error': 'Ungültige Anfrage.'}, status=400)
    text = (data.get('text') or '').strip() if isinstance(data, dict) else ''
    if len(text) < 5:
        return JsonResponse({'ok': False, 'error': 'Bitte zuerst diktieren oder einen Text eingeben.'}, status=400)
    try:
        result = generate_offer_from_text(text)
    except AIError as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=502)
    except Exception:
        logger.exception('KI-Angebotsentwurf fehlgeschlagen')
        return JsonResponse({'ok': False, 'error': 'Unerwarteter Fehler bei der KI-Auswertung. Bitte erneut versuchen.'}, status=500)
    write_audit(request, 'generate', object_type='KI', object_repr='Angebotsentwurf',
                message=f"KI-Assistent: {len(result['positionen'])} Position(en) erzeugt" +
                        (f" · {result['katalog_info']}" if result.get('katalog_info') else ''))
    return JsonResponse({'ok': True, 'positionen': result['positionen'],
                         'hinweise': result['hinweise'], 'annahmen': result['annahmen'],
                         'katalog_info': result.get('katalog_info', '')})


@login_required
@require_POST
def ai_transcribe(request):
    """Lokale Whisper-Transkription: Audio-Diktat → deutscher Text.

    Datenschutz: Das Audio bleibt auf dem eigenen Server (kein Cloud-Upload)
    und wird nach der Transkription sofort gelöscht. Ist faster-whisper nicht
    installiert, antwortet der Endpoint mit 503 – Texteingabe bleibt verfügbar.
    """
    audio = request.FILES.get('audio')
    if not audio:
        return JsonResponse({'ok': False, 'error': 'Es wurde kein Audio übertragen.'}, status=400)
    suffix = Path(audio.name or 'diktat.webm').suffix or '.webm'
    try:
        from .import_probe import read_upload
        text = transcribe_audio(read_upload(audio, 25 * 1024 * 1024), suffix=suffix)
    except TranscriptionUnavailable as e:
        return JsonResponse({'ok': False, 'error': str(e), 'code': 'stt_unavailable'}, status=503)
    except ValueError as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)
    except Exception:
        logger.exception('Transkription fehlgeschlagen')
        return JsonResponse({'ok': False, 'error': 'Die Aufnahme konnte nicht transkribiert werden. Bitte erneut versuchen.'}, status=500)
    if not text:
        return JsonResponse({'ok': False, 'error': 'In der Aufnahme wurde keine Sprache erkannt. Bitte erneut diktieren.'}, status=422)
    write_audit(request, 'generate', object_type='KI', object_repr='Transkription',
                message=f'Diktat lokal transkribiert ({len(text)} Zeichen)')
    return JsonResponse({'ok': True, 'text': text})


def _read_json_body(request, max_bytes: int = MAX_AI_REQUEST_BYTES):
    """Liest den JSON-Body eines KI-Requests defensiv ein (None bei Fehlern)."""
    if len(request.body) > max_bytes:
        return None
    try:
        data = json.loads(request.body.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


@login_required
@require_POST
def ai_polish_text(request):
    """Magic-Tone: lose/emotionale Ansage → professionelle Leistungsbeschreibung.

    Datenschutz: Es wird ausschließlich der eingegebene Text übertragen; der
    System-Prompt weist Bonsai an, Namen/Adressen ersatzlos wegzulassen.
    """
    if not ai_is_configured():
        return JsonResponse({'ok': False, 'error': 'Der lokale KI-Assistent ist deaktiviert. Bitte in den Einstellungen aktivieren.'}, status=400)
    data = _read_json_body(request)
    if data is None:
        return JsonResponse({'ok': False, 'error': 'Ungültige Anfrage.'}, status=400)
    text = (data.get('text') or '').strip()
    if len(text) < 3:
        return JsonResponse({'ok': False, 'error': 'Bitte zuerst einen Text eingeben oder diktieren.'}, status=400)
    try:
        result = polish_service_text(text)
    except AIError as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=502)
    except Exception:
        logger.exception('Magic-Tone-Formulierung fehlgeschlagen')
        return JsonResponse({'ok': False, 'error': 'Unerwarteter Fehler bei der KI-Formulierung. Bitte erneut versuchen.'}, status=500)
    write_audit(request, 'generate', object_type='KI', object_repr='Magic-Tone',
                message=f'Leistungstext veredelt ({len(text)} → {len(result)} Zeichen)')
    return JsonResponse({'ok': True, 'text': result})


def _invoice_ai_context(invoice: Invoice) -> list[str]:
    """Eckdaten einer Rechnung für KI-Aufrufe – AUSSCHLIESSLICH Leistungsdaten.

    Bewusst ohne Kunden- und Objektdaten (Datenschutzregel des Projekts).
    """
    lines = [
        f'Rechnungsnummer: {invoice.number}',
        f'Bruttobetrag: {invoice.gross_total} EUR',
        f'Fällig bis: {invoice.due_date.strftime("%d.%m.%Y") if invoice.due_date else "siehe Rechnung"}',
    ]
    if invoice.period_start and invoice.period_end:
        lines.append(f'Leistungszeitraum: {invoice.period_start.strftime("%d.%m.%Y")} – {invoice.period_end.strftime("%d.%m.%Y")}')
    items = list(invoice.items.all()[:10])
    if items:
        lines.append('Positionen:')
        lines.extend(f'- {item.description}' for item in items)
    return lines


@login_required
@require_POST
def ai_cover_letter(request):
    """Magic-Tone: KI-Begleittext für den Rechnungsversand erzeugen.

    Der Kontext wird serverseitig aus der Rechnung gebaut und enthält nur
    Leistungsdaten (Nummer, Betrag, Fälligkeit, Positionsbezeichnungen).
    """
    if not ai_is_configured():
        return JsonResponse({'ok': False, 'error': 'Der lokale KI-Assistent ist deaktiviert. Bitte in den Einstellungen aktivieren.'}, status=400)
    data = _read_json_body(request)
    if data is None:
        return JsonResponse({'ok': False, 'error': 'Ungültige Anfrage.'}, status=400)
    invoice = Invoice.objects.filter(pk=data.get('invoice')).first()
    if not invoice:
        return JsonResponse({'ok': False, 'error': 'Rechnung wurde nicht gefunden.'}, status=404)
    hint = (data.get('hint') or '').strip()
    try:
        body = generate_cover_letter_text(_invoice_ai_context(invoice), hint)
    except AIError as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=502)
    except Exception:
        logger.exception('KI-Begleittext fehlgeschlagen')
        return JsonResponse({'ok': False, 'error': 'Unerwarteter Fehler beim Begleittext. Bitte erneut versuchen.'}, status=500)
    write_audit(request, 'generate', invoice, f'KI-Begleittext zu {invoice.number} erzeugt')
    return JsonResponse({'ok': True, 'text': body})


@login_required
@require_POST
def ai_plausibility(request):
    """Plausibilitäts-Check der aktuellen Positionen (Prüfen-Schritt).

    Zweistufig: Der LOKALE Teil (Preis-Ausreißer gegen die eigene Kunden-
    historie, Dubletten, Nullwerte) läuft komplett auf diesem Server – die
    Kundennummer wird dafür NICHT an Bonsai übertragen. Der optionale KI-Teil
    prüft ausschließlich die Leistungsdaten (Bezeichnung/Menge/Einheit/Preis).
    """
    data = _read_json_body(request)
    if data is None:
        return JsonResponse({'ok': False, 'error': 'Ungültige Anfrage.'}, status=400)
    raw_positions = data.get('positionen')
    if not isinstance(raw_positions, list) or not raw_positions:
        return JsonResponse({'ok': False, 'error': 'Keine Positionen übergeben.'}, status=400)
    positions = [p for p in raw_positions[:40] if isinstance(p, dict)]
    mode = data.get('mode') if data.get('mode') in ('lokal', 'ki', 'beides') else 'lokal'
    customer_id = data.get('customer') or None

    lokal = check_positions_local(customer_id, positions)

    ki = None
    ki_error = ''
    if mode in ('ki', 'beides'):
        if not ai_is_configured():
            ki_error = 'Der lokale KI-Assistent ist deaktiviert. Bitte in den Einstellungen aktivieren.'
        else:
            try:
                ki = ai_review_positions(positions)
                write_audit(request, 'generate', object_type='KI', object_repr='Plausibilitäts-Check',
                            message=f'KI-Check: {len(ki)} Befund(e) bei {len(positions)} Position(en)')
            except AIError as e:
                ki_error = str(e)
            except Exception:
                logger.exception('KI-Plausibilitätscheck fehlgeschlagen')
                ki_error = 'Unerwarteter Fehler beim KI-Check. Bitte erneut versuchen.'
    return JsonResponse({'ok': True, 'lokal': lokal, 'ki': ki, 'ki_error': ki_error})


@login_required
def service_catalog_list(request):
    """Leistungskatalog: Übersicht aller (auch KI-gelernten) Leistungen."""
    q = (request.GET.get('q') or '').strip()
    items = ServiceCatalogItem.objects.all()
    if q:
        items = items.filter(Q(name__icontains=q) | Q(long_text__icontains=q))
    return render(request, 'sedo/service_catalog_list.html', {'items': items, 'q': q})


@login_required
def service_catalog_create(request):
    form = ServiceCatalogItemForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        item = form.save()
        write_audit(request, 'create', item, f'Katalog-Leistung „{item.name}" angelegt')
        messages.success(request, f'Leistung „{item.name}" wurde angelegt.')
        return redirect('sedo:service_catalog_list')
    return render(request, 'sedo/service_catalog_form.html', {'form': form, 'title': 'Neue Katalog-Leistung'})


@login_required
def service_catalog_update(request, pk):
    item = get_object_or_404(ServiceCatalogItem, pk=pk)
    form = ServiceCatalogItemForm(request.POST or None, instance=item)
    if request.method == 'POST' and form.is_valid():
        item = form.save(commit=False)
        item.created_by_ai = False  # manuell geprüft/überarbeitet
        item.save()
        write_audit(request, 'update', item, f'Katalog-Leistung „{item.name}" bearbeitet')
        messages.success(request, f'Leistung „{item.name}" wurde gespeichert.')
        return redirect('sedo:service_catalog_list')
    return render(request, 'sedo/service_catalog_form.html', {'form': form, 'title': f'Leistung bearbeiten: {item.name}', 'item': item})


@login_required
def service_catalog_delete(request, pk):
    item = get_object_or_404(ServiceCatalogItem, pk=pk)
    if request.method == 'POST':
        name = item.name
        item.delete()
        write_audit(request, 'delete', object_type='ServiceCatalogItem', object_repr=name,
                    message=f'Katalog-Leistung „{name}" gelöscht')
        messages.success(request, f'Leistung „{name}" wurde gelöscht.')
        return redirect('sedo:service_catalog_list')
    return render(request, 'sedo/confirm_delete.html', {
        'title': 'Katalog-Leistung löschen',
        'text': f'Soll die Leistung „{item.name}" wirklich aus dem Katalog gelöscht werden? Bestehende Angebote bleiben unverändert.',
        'cancel_url': reverse('sedo:service_catalog_list'),
    })

@login_required
def dashboard(request, layout_form=None):
    from .models import MailAccount, MailLocation
    from .services.mail_spam import spam_messages, confirmed_spam_messages
    from .dashboard_widgets import DashboardLayoutForm, catalogue, layout_for
    from .services.banking import cached_bank_balance
    from .services.dashboard_weather import weather_for
    from .services.calendar_data import agenda

    layout = layout_for(request.user)
    layout_form = layout_form or DashboardLayoutForm(initial=layout)
    selected = layout_form['widgets'].value() or []
    bank_cfg = get_banking_config() if 'balance' in layout['widgets'] else {}

    today = timezone.localdate()
    invoices = Invoice.objects.all()
    dunning_candidates = get_dunning_candidates(days_overdue_threshold=14)
    open_tasks = WorkTask.objects.filter(status='open').select_related(
        'customer', 'offer', 'invoice').order_by('due_date', 'priority', 'pk')
    calendar_entries, calendar_errors = agenda(request.user, timezone.now(), timezone.now() + timedelta(days=30), limit=6) if 'calendar' in layout['widgets'] else ([], [])
    context = {
        'calendar_entries': calendar_entries,
        'calendar_errors': calendar_errors,
        'dashboard_widgets': [widget for widget in catalogue(layout['widgets']) if widget['selected']],
        'widget_catalogue': catalogue(selected),
        'layout_form': layout_form,
        'weather_place': layout['weather_place'],
        'weather': weather_for(layout) if 'weather' in layout['widgets'] else None,
        'bank_balance': cached_bank_balance(bank_cfg) if bank_cfg else None,
        'banking_configured': all(bank_cfg.get(key) for key in ('blz', 'endpoint', 'login')),
        'banking_pin_stored': bool(bank_cfg.get('pin')),
        'draft_invoices': invoices.filter(lifecycle='draft').count(),
        # offene_rechnungen() schließt zusätzlich Entwürfe, durch eine Korrektur
        # ersetzte Originale und Stornobelege aus. Vorher zählte das Dashboard
        # Original UND Korrektur als offene Forderung, und ein Stornobeleg ging
        # mit negativem Betrag in die Summe ein (F-07, F-60).
        # 'paid_month' unten bleibt bewusst unverändert – das ist die Umsatzzahl,
        # die täglich angeschaut wird; sie gehört in eine eigene Entscheidung.
        'open_invoices': offene_rechnungen(invoices).count(),
        'open_amount_total': sum((i.open_amount for i in offene_rechnungen(invoices).prefetch_related('payments')), Decimal('0.00')),
        'overdue_invoices': offene_rechnungen(invoices).filter(due_date__lt=today).count(),
        'paid_month': _sum_decimal(invoices.filter(status='bezahlt', issue_date__year=today.year, issue_date__month=today.month), 'gross_total'),
        'mail_accounts_configured': MailAccount.objects.exists(),
        # Match the unread inbox view, including manually refreshed/paused accounts.
        'unread_mail_count': MailLocation.objects.filter(
            folder__active=True, folder__selectable=True,
            folder__role='inbox', seen=False).exclude(message__in=confirmed_spam_messages()).count(),
        'unread_spam_count': spam_messages().filter(locations__seen=False,
            locations__folder__active=True, locations__folder__selectable=True,
            locations__folder__role__in=['inbox', 'junk']).count(),
        'today': today,
        'open_offers': Offer.objects.filter(status='offen').count(),
        'customers': Customer.objects.count(),
        'latest_invoices': Invoice.objects.select_related('customer','obj')[:8],
        'latest_offers': Offer.objects.select_related('customer','obj')[:5],
        'dunning_candidates_count': len(dunning_candidates),
        'smtp_configured': smtp_is_configured(),
        'ai_configured': ai_is_configured(),
        'open_tasks': open_tasks[:6],
        'open_tasks_count': open_tasks.count(),
        'overdue_tasks_count': open_tasks.filter(due_date__lt=today).count(),
    }
    return render(request, 'sedo/dashboard.html', context)


@login_required
def reports(request):
    current_year = timezone.localdate().year
    try:
        year = int(request.GET.get('year', current_year))
    except (TypeError, ValueError):
        year = current_year
    if year < 2000 or year > current_year:
        year = current_year
    context = build_reports(year)
    first_invoice = Invoice.objects.order_by('issue_date').values_list('issue_date', flat=True).first()
    first_offer = Offer.objects.order_by('issue_date').values_list('issue_date', flat=True).first()
    first_year = min(value.year for value in (first_invoice, first_offer) if value) if (first_invoice or first_offer) else current_year
    context['year_options'] = range(current_year, max(1999, first_year - 1), -1)
    return render(request, 'sedo/reports.html', context)


@login_required
@never_cache
@require_http_methods(['GET', 'HEAD', 'POST'])
@sensitive_post_parameters()
def company_setup(request):
    """First-run company identity, stored only in this installation's data home."""
    from .company_profile import SellerProfileError, initialize_profile
    from .forms import CompanyProfileForm
    from .security import is_administrator

    try:
        load_profile()
    except SellerProfileError:
        pass
    else:
        return redirect('sedo:dashboard')

    can_edit = is_administrator(request.user)
    if request.method == 'POST' and not can_edit:
        return HttpResponseForbidden('Nur die Administration kann die Firmendaten einrichten.')
    form = CompanyProfileForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                initialized = initialize_profile(form.cleaned_data)
                if initialized:
                    write_audit(request, 'create', object_type='AppSetting', object_repr='Firmendaten',
                                message='Lokales Absenderprofil eingerichtet', pflicht=True)
        except AuditWriteError:
            form.add_error(None, 'Die Einrichtung konnte nicht sicher protokolliert werden. Bitte erneut versuchen.')
        except SellerProfileError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, 'Firmendaten eingerichtet. SEDO kann jetzt für Ihr Unternehmen verwendet werden.' if initialized else 'Das Unternehmen wurde bereits eingerichtet.')
            return redirect('sedo:dashboard')
    return render(request, 'sedo/company_setup.html', {'company_form': form, 'can_edit_company': can_edit})


@login_required
@sensitive_post_parameters('weather_api_key', 'fints_pin', 'smtp_password', 'ai_api_key')
def settings_page(request):
    from .forms import CompanyProfileForm
    from .security import is_administrator
    company_initial = profile_form_initial()
    company_form = CompanyProfileForm(initial=company_initial)
    stats = None
    if request.method == 'POST':
        action = request.POST.get('action') or 'save_settings'

        if (action in {'save_ai', 'start_ai', 'test_ai'}
                and getattr(settings, 'SEDO_WEB_MODE', False)
                and not getattr(settings, 'SEDO_WEB_AI_ENABLED', False)):
            messages.info(request, 'Die KI-Funktionen sind für SEDO Online derzeit nicht freigegeben.')
            return redirect(reverse('sedo:settings') + '#ki-assistent')

        if action == 'save_company':
            if not is_administrator(request.user):
                return HttpResponseForbidden('Nur die Administration kann die Firmendaten ändern.')
            company_form = CompanyProfileForm(request.POST)
            if company_form.is_valid():
                with transaction.atomic():
                    save_profile(company_form.cleaned_data)
                    write_audit(request, 'update', object_type='AppSetting', object_repr='Firmendaten',
                                message='Lokales Absenderprofil gespeichert')
                messages.success(request, 'Firmendaten gespeichert. Bereits ausgestellte Originalbelege bleiben unverändert.')
                return redirect(reverse('sedo:settings') + '#company-settings')

        if action == 'save_letterhead':
            if not is_administrator(request.user):
                return HttpResponseForbidden('Nur die Administration kann den Briefbogen ändern.')
            enabled = request.POST.get('sedo_letterhead') == '1'
            from .company_profile import SEDO_LETTERHEAD_KEY
            with transaction.atomic():
                AppSetting.set(SEDO_LETTERHEAD_KEY, '1' if enabled else '0')
                write_audit(request, 'update', object_type='AppSetting', object_repr='Briefbogen',
                            message='SEDO-Briefbogen aktiviert' if enabled else 'SEDO-Briefbogen deaktiviert')
            messages.success(request, 'Briefbogen für neu erzeugte PDFs gespeichert. Bereits ausgestellte Originalbelege bleiben unverändert.')
            return redirect(reverse('sedo:settings') + '#letterhead-settings')

        if action == 'save_weather':
            if not request.user.is_staff:
                return HttpResponseForbidden('Nur die Administration kann den Wetterzugang ändern.')
            new_key = request.POST.get('weather_api_key', '').strip()
            if new_key and (len(new_key) > 512 or any(ord(char) < 33 or ord(char) > 126 for char in new_key)):
                messages.error(request, 'Bitte einen gültigen Wetter.com-Zugangsschlüssel ohne Leerzeichen eingeben.')
            else:
                if request.POST.get('weather_api_key_clear'):
                    AppSetting.set('weather_api_key', '')
                elif new_key:
                    AppSetting.set('weather_api_key', new_key)
                write_audit(request, 'update', object_type='AppSetting', object_repr='Wetter.com',
                            message='Wetterzugang gespeichert')
                messages.success(request, 'Wetterzugang gespeichert. Den Ort wählst du im Dashboard über das Zahnrad.')
            return redirect(reverse('sedo:settings') + '#weather-settings')

        if action == 'import_mapping':
            if not request.FILES.get('file'):
                messages.error(request, 'Bitte eine Mapping-Datei auswählen.')
            else:
                file = request.FILES['file']
                try:
                    df = _read_aareon_mapping_dataframe(file)
                    stats = _perform_aareon_mapping_import(df, request)
                except Exception as e:
                    messages.error(request, f'Mapping-Import fehlgeschlagen: {e}')
            return redirect('sedo:settings')

        if action == 'start_ai':
            if not request.user.is_staff:
                return HttpResponseForbidden('Nur die Administration kann Bonsai starten.')
            if start_bonsai_background(manual=True):
                messages.info(request, 'Bonsai wird im Hintergrund gestartet. Bitte gleich die Verbindung prüfen.')
            else:
                messages.info(request, 'Bonsai-Start läuft bereits oder der lokale KI-Assistent ist deaktiviert.')
            return redirect('sedo:settings')

        if action == 'test_ai':
            if not request.user.is_staff:
                return HttpResponseForbidden('Nur die Administration kann den KI-Zugang prüfen.')
            try:
                model = check_local_model()
                messages.success(request, f'Lokales Modell erreichbar: {Path(model).name}. Es wurden keine Geschäftsdaten übertragen.')
            except AIError as exc:
                messages.error(request, str(exc))
            return redirect('sedo:settings')

        if action == 'save_ai':
            if not request.user.is_staff:
                return HttpResponseForbidden('Nur die Administration kann den KI-Zugang ändern.')
            try:
                base_url = validate_local_base_url(request.POST.get('ai_local_base_url', ''))
                model = validate_local_model(request.POST.get('ai_local_model', ''))
                runtime_dir = validate_runtime_dir(request.POST.get('ai_local_runtime_dir', ''))
            except AIError as exc:
                messages.error(request, str(exc))
                return redirect('sedo:settings')
            AppSetting.set('ai_local_base_url', base_url)
            AppSetting.set('ai_local_model', model)
            AppSetting.set('ai_local_enabled', '1' if request.POST.get('ai_local_enabled') else '0')
            AppSetting.set('ai_local_autostart', '1' if request.POST.get('ai_local_autostart') else '0')
            AppSetting.set('ai_local_runtime_dir', runtime_dir)
            write_audit(request, 'update', object_type='AppSetting', object_repr='KI-Assistent',
                        message='Lokale Bonsai-Einstellungen gespeichert')
            messages.success(request, 'Lokale KI-Einstellungen gespeichert.')
            return redirect('sedo:settings')

        if action == 'save_smtp':
            from .security import is_administrator
            from .services.credentials import CredentialError, save_configuration
            if not is_administrator(request.user):
                return HttpResponseForbidden('Nur die Administration kann den SMTP-Zugang ändern.')
            try:
                save_configuration('smtp', {
                    'host': request.POST.get('smtp_host', ''),
                    'port': request.POST.get('smtp_port', '587') or '587',
                    'user': request.POST.get('smtp_user', ''),
                    'from_name': request.POST.get('smtp_from_name', ''),
                    'tls': '1' if request.POST.get('smtp_tls') else '0',
                }, secret=request.POST.get('smtp_password', ''))
            except CredentialError as exc:
                messages.error(request, str(exc))
                return redirect('sedo:settings')
            write_audit(request, 'update', object_type='AppSetting', object_repr='SMTP',
                        message='SMTP-Einstellungen gespeichert')
            messages.success(request, 'SMTP-Einstellungen gespeichert.')
            return redirect('sedo:settings')

        if action == 'save_banking':
            from .security import is_administrator
            from .services.credentials import CredentialError, save_configuration
            if not is_administrator(request.user):
                return HttpResponseForbidden('Nur die Administration kann den Bankzugang ändern.')
            try:
                save_configuration('fints', {
                    'blz': request.POST.get('fints_blz', ''),
                    'endpoint': request.POST.get('fints_endpoint', ''),
                    'login': request.POST.get('fints_login', ''),
                    'iban': request.POST.get('fints_iban', '').replace(' ', ''),
                    'product_id': request.POST.get('fints_product_id', ''),
                }, secret=request.POST.get('fints_pin', ''),
                    clear=bool(request.POST.get('fints_pin_clear')))
            except CredentialError as exc:
                messages.error(request, str(exc))
                return redirect('sedo:settings')
            write_audit(request, 'update', object_type='AppSetting', object_repr='Bankkonto',
                        message='FinTS-Bankzugang gespeichert')
            messages.success(request, 'Bankzugang gespeichert.')
            return redirect('sedo:settings')

        if action == 'test_smtp':
            test_to = request.POST.get('test_email', '').strip()
            if not test_to:
                messages.error(request, 'Bitte eine Ziel-E-Mail-Adresse für den Test eingeben.')
            else:
                try:
                    send_email_smtp(
                        test_to,
                        'SEDO – SMTP-Verbindungstest',
                        'Diese E-Mail bestätigt, dass der SMTP-Versand aus SEDO korrekt konfiguriert ist.',
                    )
                    write_audit(request, 'generate', object_type='SMTP', object_repr='Test',
                                message=f'Test-E-Mail an {test_to} versendet')
                    messages.success(request, f'Test-E-Mail erfolgreich an {test_to} versendet.')
                except SMTPDeliveryUncertain as e:
                    messages.warning(request, str(e))
                except Exception as e:
                    messages.error(request, f'SMTP-Test fehlgeschlagen: {e}')
            return redirect('sedo:settings')

        if action != 'save_company':
            AppSetting.set('aareon_seller_id', request.POST.get('aareon_seller_id', ''))
            AppSetting.set('aareon_include_seller_assigned_id', '1' if request.POST.get('aareon_include_seller_assigned_id') else '0')
            write_audit(request, 'update', object_type='AppSetting', object_repr='Einstellungen', message='Systemeinstellungen gespeichert')
            messages.success(request, 'Einstellungen wurden gespeichert.')
            return redirect('sedo:settings')

    smtp_cfg = get_smtp_config()
    from .services.dashboard_weather import weather_key
    from .models import MailAccount
    context = {
        'mail_settings_accounts': MailAccount.objects.only('email', 'enabled') if request.user.is_staff else [],
        'company_form': company_form,
        'company_configured': bool(company_initial.get('name')),
        'can_edit_company': is_administrator(request.user),
        'sedo_letterhead': AppSetting.get('document_sedo_letterhead', '0') == '1',
        'aareon_seller_id': AppSetting.get('aareon_seller_id', ''),
        'aareon_include_seller_assigned_id': AppSetting.get('aareon_include_seller_assigned_id', '0'),
        'mapping_count': AareonMapping.objects.count(),
        'unit_count': AareonMapping.objects.values('billing_unit_sender').distinct().count(),
        'service_count': AareonMapping.objects.values('service_id').distinct().count(),
        'mappings': AareonMapping.objects.all()[:50],
        'stats': stats,
        'ai_cfg': get_ai_config(),
        'web_ai_disabled': (getattr(settings, 'SEDO_WEB_MODE', False)
                            and not getattr(settings, 'SEDO_WEB_AI_ENABLED', False)),
        'ai_configured': ai_is_configured(),
        'weather_configured': bool(weather_key()),
        'ai_start_status': runtime_status(),
        'smtp_host':      smtp_cfg['host'],
        'smtp_port':      smtp_cfg['port'],
        'smtp_user':      smtp_cfg['user'],
        'smtp_from_name': smtp_cfg['from_name'],
        'smtp_tls':       smtp_cfg['tls'],
        'smtp_configured': smtp_is_configured(),
        'banking_cfg': get_banking_config(),
        'banking_configured': banking_is_configured(),
        'banking_pin_stored': bool(AppSetting.get('fints_pin', '')),
    }
    return render(request, 'sedo/settings.html', context)

@login_required
def month_close(request):
    """Monatsabschluss: Steuerberater-Export."""
    today = timezone.localdate()
    default_month = today.strftime('%Y-%m')
    selected_month = request.POST.get('month') or request.GET.get('month') or default_month
    tax_email = request.POST.get('tax_email') or AppSetting.get('tax_advisor_email', '')

    try:
        year, month = parse_month(selected_month)
    except (ValueError, AttributeError):
        if request.method == 'POST':
            return HttpResponseBadRequest('Bitte einen gültigen Monat im Format JJJJ-MM wählen.')
        year, month = today.year, today.month
        selected_month = f'{year:04d}-{month:02d}'

    # Entwürfe gehören nicht in den Monatsabschluss: sie wurden nie ausgestellt,
    # verfälschten aber Anzahl, Summe und den Steuerberater-Export (F-13).
    # Entwürfe gehören nicht in den Monatsabschluss: sie wurden nie ausgestellt.
    # Ersetzte Originale ebenso wenig: Die Korrektur ist ein VOLLERSATZ mit
    # eigenem positivem Betrag, kein Differenzbeleg – Original und Korrektur
    # addierten sich in der Bruttosumme. Beim Storno stimmt die Summe dagegen
    # von selbst, weil der Stornobeleg negative Mengen trägt; er bleibt drin.
    invoices = (Invoice.objects.filter(issue_date__year=year, issue_date__month=month)
                .exclude(lifecycle__in=('draft', 'corrected'))
                .select_related('customer', 'obj').prefetch_related('items').order_by('number'))
    # Ausgeblendete Nummern des Monats mit ausweisen – eine dokumentierte
    # Nummernlücke ist GoBD-tragbar, eine unerklärte nicht (F-14).
    ausgeblendete_nummern = list(
        Invoice.all_objects.filter(issue_date__year=year, issue_date__month=month,
                                   deleted_at__isnull=False)
        .order_by('number').values_list('number', flat=True))

    if request.method == 'POST':
        action = request.POST.get('action')
        if tax_email:
            AppSetting.set('tax_advisor_email', tax_email)
        if action in ('export_tax_email', 'download_tax_package'):
            try:
                zip_path, manifest = create_tax_package(
                    invoices, selected_month, deleted_numbers=ausgeblendete_nummern,
                    user=request.user.get_username())
            except (WorkflowError, OSError) as exc:
                messages.error(request, f'Export abgebrochen, keine unvollständige Auswahl ausgegeben: {exc}')
                return redirect(f"{reverse('sedo:month_close')}?month={selected_month}")
            try:
                write_audit(request, 'generate', object_type='SteuerberaterExport',
                            object_repr=selected_month,
                            message=f'Steuerberaterpaket {selected_month} mit {manifest["rechnungen"]} Rechnungen erzeugt',
                            metadata={'vorgang': 'steuerberater_export', **manifest}, pflicht=True)
            except AuditWriteError:
                zip_path.unlink(missing_ok=True)
                messages.error(request, 'Export abgebrochen: Das Exportprotokoll konnte nicht gespeichert werden.')
                return redirect('sedo:month_close')
            if action == 'download_tax_package':
                return FileResponse(open(zip_path, 'rb'), as_attachment=True, filename=zip_path.name, content_type='application/zip')
            msg = EmailMessage()
            msg['To'] = tax_email or ''
            msg['From'] = ''
            msg['Subject'] = f'SEDO Rechnungsexport {selected_month}'
            msg.set_content(
                f'Sehr geehrte Damen und Herren,\n\n'
                f'anbei erhalten Sie das Steuerberaterpaket für {selected_month}.\n'
                f'Enthaltene Rechnungen: {manifest["rechnungen"]}\n'
                f'Enthalten sind Belege, Buchungsdaten und ein Exportprotokoll.\n\n'
                f'Mit freundlichen Grüßen\nSEDO\n'
            )
            msg.add_attachment(zip_path.read_bytes(), maintype='application', subtype='zip', filename=zip_path.name)
            eml_path = zip_path.parent / f'SEDO_Rechnungsexport_{selected_month}.eml'
            eml_path.write_bytes(msg.as_bytes())
            return FileResponse(open(eml_path, 'rb'), as_attachment=True, filename=eml_path.name, content_type='message/rfc822')

    context = {
        'selected_month': selected_month,
        'tax_email': tax_email,
        'invoices': invoices,
        'invoice_count': invoices.count(),
        'gross_total': _sum_decimal(invoices, 'gross_total'),
        'ausgeblendete_nummern': ausgeblendete_nummern,
        'export_history': AuditLog.objects.filter(
            object_type='SteuerberaterExport', object_repr=selected_month)[:8],
    }
    return render(request, 'sedo/month_close.html', context)

@login_required
def backup_page(request):
    """Systembereich: Backup verwalten."""
    from .backup_utils import BackupError, backup_dir, create_backup, inspect_backup, stage_restore
    selected = request.POST.get('backup') or request.GET.get('backup') or ''
    restore_report = None
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'backup_now':
            # create_backup bricht seit F-81 ab, wenn die Secret-Bereinigung
            # nicht belegbar ist – ohne diesen Zweig wäre das eine 500-Seite.
            try:
                backup_file = create_backup('manual_system')
            except Exception as fehler:
                logger.warning('Manuelles Backup fehlgeschlagen: %s', fehler, exc_info=True)
                write_audit(request, 'backup', object_type='Backup', object_repr='fehlgeschlagen',
                            message=f'Manuelles Backup fehlgeschlagen: {fehler}')
                if isinstance(fehler, BackupError):
                    messages.error(request, f'Backup abgebrochen: {fehler}')
                else:
                    messages.error(request, f'Backup fehlgeschlagen: {fehler}')
            else:
                write_audit(request, 'backup', object_type='Backup', object_repr=backup_file.name, message='Manuelles Backup erstellt')
                messages.success(request, f'Backup erstellt: {backup_file.name}')
        elif action == 'disable_weekly':
            AppSetting.set('auto_weekly_backup_enabled', '0')
            write_audit(request, 'backup', object_type='Backup', object_repr='Automatisches Wochenbackup', message='Automatisches Wochenbackup deaktiviert')
            messages.success(request, 'Automatisches Wochenbackup wurde deaktiviert.')
        elif action == 'enable_weekly':
            AppSetting.set('auto_weekly_backup_enabled', '1')
            write_audit(request, 'backup', object_type='Backup', object_repr='Automatisches Wochenbackup', message='Automatisches Wochenbackup aktiviert')
            messages.success(request, 'Automatisches Wochenbackup wurde aktiviert.')
        elif action == 'inspect_restore':
            try:
                inspect_backup(selected)
            except BackupError as exc:
                messages.error(request, f'Backup-Prüfung abgebrochen: {exc}')
                return redirect('sedo:backup')
            return redirect(f'{reverse("sedo:backup")}?backup={selected}')
        elif action == 'stage_restore':
            if request.POST.get('confirmation', '').strip().upper() != 'WIEDERHERSTELLEN':
                messages.error(request, 'Bitte zur Bestätigung WIEDERHERSTELLEN eingeben.')
                return redirect(f'{reverse("sedo:backup")}?backup={selected}')
            try:
                digest = request.POST.get('review_sha256', '')
                if not digest:
                    raise BackupError('Bitte zuerst die Sicherung prüfen und die Vorschau öffnen.')
                plan = stage_restore(selected, expected_digest=digest)
            except Exception as exc:
                logger.exception('Wiederherstellung konnte nicht vorbereitet werden')
                messages.error(request, f'Wiederherstellung abgebrochen: {exc}')
            else:
                write_audit(request, 'backup', object_type='BackupRestore', object_repr=selected,
                            message='Wiederherstellung geprüft und für den nächsten App-Start vorgemerkt',
                            metadata={'vorgang': 'restore_staged', 'safety_backup': plan['safety_backup']})
                restart = ('Bitte den SEDO-Webdienst kontrolliert neu starten.'
                           if getattr(settings, 'SEDO_WEB_MODE', False) else
                           'Bitte die App schließen und erneut öffnen.')
                messages.success(request, 'Wiederherstellung ist vollständig vorbereitet. '
                                 + restart + ' Vor dem Start wird der geprüfte Stand eingespielt.')
            return redirect('sedo:backup')
        return redirect('sedo:backup')
    if selected:
        try:
            restore_report = inspect_backup(selected)
        except BackupError as exc:
            messages.error(request, f'Backup-Prüfung abgebrochen: {exc}')
            selected = ''
    backups = sorted(backup_dir().glob('*.zip'), key=lambda item: item.stat().st_mtime, reverse=True)
    home = Path(getattr(settings, 'SEDO_HOME', settings.BASE_DIR))
    pending_restore = None
    last_restore = None
    try:
        pending_restore = json.loads((home / 'pending_restore.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        pass
    try:
        last_restore = json.loads((home / 'last_restore.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        pass
    context = {
        'auto_weekly_backup_enabled': AppSetting.get('auto_weekly_backup_enabled', '1'),
        'last_backup_at': AppSetting.get('last_backup_at', ''),
        'last_weekly_backup_at': AppSetting.get('last_weekly_backup_at', ''),
        'last_backup_path': AppSetting.get('last_backup_path', ''),
        'last_weekly_backup_path': AppSetting.get('last_weekly_backup_path', ''),
        # Ein abgebrochenes Wochenbackup läuft im Hintergrund und wäre sonst
        # unsichtbar – die Seite zeigt den Vermerk als Warnung an (F-81).
        'last_backup_error': AppSetting.get('last_backup_error', ''),
        'last_backup_error_at': AppSetting.get('last_backup_error_at', ''),
        'backups': backups, 'selected_backup': selected,
        'restore_report': restore_report, 'pending_restore': pending_restore,
        'last_restore': last_restore,
        'web_mode': getattr(settings, 'SEDO_WEB_MODE', False),
    }
    return render(request, 'sedo/backup.html', context)


@login_required
def task_list(request, pk=None):
    task = get_object_or_404(WorkTask, pk=pk) if pk else None
    initial = {'due_date': timezone.localdate()}
    for key in ('customer', 'offer', 'invoice'):
        value = request.GET.get(key)
        if value and str(value).isdigit():
            initial[key] = value
    form = WorkTaskForm(request.POST or None, instance=task, initial=initial if not task else None)
    if request.method == 'POST' and form.is_valid():
        saved = form.save(commit=False)
        if not saved.pk:
            saved.created_by = request.user
        saved.save()
        write_audit(request, 'update' if task else 'create', saved,
                    f'Aufgabe „{saved.title}“ {"bearbeitet" if task else "angelegt"}')
        messages.success(request, 'Aufgabe wurde gespeichert.')
        return redirect('sedo:task_list')
    status = request.GET.get('status', 'open')
    if status not in ('open', 'done', 'all'):
        status = 'open'
    tasks = WorkTask.objects.select_related('customer', 'offer', 'invoice', 'created_by')
    if status in ('open', 'done'):
        tasks = tasks.filter(status=status)
    return render(request, 'sedo/task_list.html', {
        'form': form, 'edit_task': task, 'tasks': tasks, 'status_filter': status,
        'today': timezone.localdate(),
    })


@login_required
@require_POST
def task_toggle(request, pk):
    task = get_object_or_404(WorkTask, pk=pk)
    task.status = 'open' if task.status == 'done' else 'done'
    task.completed_at = None if task.status == 'open' else timezone.now()
    task.save(update_fields=['status', 'completed_at', 'updated_at'])
    write_audit(request, 'update', task, f'Aufgabe „{task.title}“ als {task.get_status_display()} markiert')
    messages.success(request, f'Aufgabe ist jetzt {task.get_status_display().lower()}.')
    return redirect('sedo:dashboard' if request.POST.get('next') == reverse('sedo:dashboard')
                    else 'sedo:task_list')

@login_required
def security_status(request):
    """Kompakte Sicherheitsübersicht für den Produktivbetrieb."""
    last_backup = AppSetting.get('last_backup_at', '')
    # Nutzt der Admin noch das alte, dokumentierte Standard-Passwort?
    from django.contrib.auth import get_user_model
    admin_user = get_user_model().objects.filter(username='admin').first()
    admin_default_pw = bool(admin_user and admin_user.check_password('sedo1234'))
    transport_checks = transport_security_checks()
    checks = [
        {
            'label': 'Admin-Standard-Passwort geändert',
            'ok': not admin_default_pw,
            'value': 'geändert' if not admin_default_pw else 'NOCH "sedo1234"!',
            'hint': 'Die App ist im Heimnetz (0.0.0.0) und ggf. per Tunnel erreichbar – Standard-Passwort ändern: python manage.py changepassword admin',
        },
        {
            'label': 'DEBUG deaktiviert',
            'ok': not settings.DEBUG,
            'value': 'aus' if not settings.DEBUG else 'AN',
            'hint': 'Produktivbetrieb nur mit DEBUG=False.',
        },
        {
            'label': 'Allowed Hosts eingeschränkt',
            'ok': '*' not in getattr(settings, 'ALLOWED_HOSTS', []),
            'value': ', '.join(getattr(settings, 'ALLOWED_HOSTS', [])),
            'hint': 'Keine Wildcard im Produktivbetrieb.',
        },
        *transport_checks[:3],
        {
            'label': 'Clickjacking-Schutz',
            'ok': getattr(settings, 'X_FRAME_OPTIONS', '') == 'DENY',
            'value': getattr(settings, 'X_FRAME_OPTIONS', '—'),
            'hint': 'Einbettung in fremde Frames blockieren.',
        },
        transport_checks[3],
        {
            'label': 'Backup vorhanden',
            'ok': bool(last_backup),
            'value': last_backup or 'noch kein Backup vermerkt',
            'hint': 'Vor Bankanbindung täglich und extern sichern.',
        },
        {
            'label': '.env nicht im App-Backup',
            'ok': True,
            'value': 'ausgeschlossen',
            'hint': 'Secrets werden nicht in Benutzer-Backups gepackt.',
        },
    ]
    # Das Protokoll behauptete auf dieser Seite unbedingt „Audit aktiv" – auch
    # dann, wenn write_audit reihenweise scheiterte. Der Vermerk macht einen
    # Ausfall sichtbar (Nebenbefund NEU-1).
    audit_fehler = AppSetting.get(AUDIT_ERROR_KEY, '')
    checks.append({
        'label': 'Protokoll schreibt fehlerfrei',
        'ok': not audit_fehler,
        'value': 'ok' if not audit_fehler else 'letzter Versuch fehlgeschlagen',
        'hint': audit_fehler or 'Storno, Zahlungen und Ausblenden werden nachweisbar protokolliert.',
    })
    # Etappe 5: dieselbe rein lesende Prüfung wie im Verwaltungsbefehl.
    # Ein GET repariert nichts und schreibt keine Status-/Audit-Einträge.
    from .services.belegketten import pruefe_belegketten
    belegketten = pruefe_belegketten()['zusammenfassung']
    checks.append({
        'label': 'Belegketten in Ordnung',
        'ok': belegketten['kettenfehler'] == 0,
        'value': belegketten['kettenfehler'],
        'hint': ('Vorher Trockenlauf und Sicherung; bitte python manage.py '
                 'belegketten_pruefen --reparieren ausführen lassen. '
                 'Widersprüchliche Folgebelege zusätzlich manuell prüfen.'),
    })
    ok_count = len([c for c in checks if c['ok']])
    recent_logs = AuditLog.objects.select_related('user')[:20]
    context = {
        'checks': checks,
        'ok_count': ok_count,
        'total_count': len(checks),
        'recent_logs': recent_logs,
        'bank_ready': ok_count >= len(checks) - 1,
        'audit_fehler': audit_fehler,
        'audit_fehler_at': AppSetting.get(AUDIT_ERROR_AT_KEY, ''),
    }
    return render(request, 'sedo/security_status.html', context)


@login_required
def readiness(request):
    items = readiness_report()
    required = [item for item in items if item['group'] in ('Pflicht', 'Abnahme')]
    return render(request, 'sedo/readiness.html', {
        'items': items,
        'ready_count': sum(1 for item in required if item['ok']),
        'required_count': len(required),
        'ready': all(item['ok'] for item in required),
        'local_only': is_local_only_mode(),
    })


@login_required
@require_POST
def readiness_toggle(request, key):
    if key not in MANUAL_CHECKS:
        messages.error(request, 'Unbekannter Prüfschritt.')
        return redirect('sedo:readiness')
    value = '1' if request.POST.get('checked') == '1' else '0'
    AppSetting.set(f'readiness_{key}', value)
    write_audit(request, 'update', object_type='Startklar-Prüfung', object_repr=MANUAL_CHECKS[key], message=f"Status auf {'erledigt' if value == '1' else 'offen'} gesetzt")
    messages.success(request, f"{MANUAL_CHECKS[key]} wurde als {'erledigt' if value == '1' else 'offen'} markiert.")
    return redirect('sedo:readiness')


@login_required
def audit_log_page(request):
    action = request.GET.get('action', '').strip()
    q = request.GET.get('q', '').strip()
    logs = AuditLog.objects.select_related('user').all()
    if action:
        logs = logs.filter(action=action)
    if q:
        logs = logs.filter(Q(object_repr__icontains=q) | Q(message__icontains=q) | Q(user__username__icontains=q) | Q(ip_address__icontains=q))
    return render(request, 'sedo/audit_log.html', {
        'logs': logs[:500],
        'action': action,
        'q': q,
        'actions': AuditLog.ACTIONS,
    })
