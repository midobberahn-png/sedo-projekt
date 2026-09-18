from __future__ import annotations
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, date, timedelta
from pathlib import Path
import csv
import json
import zipfile
from email.message import EmailMessage
import pandas as pd
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Sum, Q
from django.db.models.functions import ExtractYear
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.http import FileResponse, JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_POST

from .forms import CustomerForm, CustomerObjectForm, CustomerNoteForm, InvoiceForm, InvoiceItemFormSet, OfferForm, OfferItemFormSet, OfferFollowUpForm, BankJsonImportForm, ManualPaymentAssignForm, ManualPaymentForm, CashbookEntryForm, RecurringInvoiceTemplateForm, RecurringInvoiceItemFormSet, ServiceCatalogItemForm, WorkTaskForm
from .models import Customer, CustomerObject, CustomerNote, Invoice, InvoiceItem, Offer, Payment, CashbookEntry, AareonMapping, RecurringInvoiceTemplate, RecurringInvoiceItem, AppSetting, AuditLog, ServiceCatalogItem, WorkTask
from .audit import AUDIT_ERROR_AT_KEY, AUDIT_ERROR_KEY, AuditWriteError, write_audit
from .services import (
    generate_invoice_files, next_number, save_with_unique_number, create_numbered_invoice,
    recalc_invoice, recalc_offer, create_storno,
    WorkflowError, ensure_correction_allowed, ensure_offer_invoicing_allowed,
    mark_invoice_corrected, finalisiere_entwurf, entwurf_sperren,
    ensure_zahlung_erlaubt, ensure_versand_erlaubt, ausblenden_hindernis,
    offene_rechnungen,
    generate_offer_pdf, create_invoice_from_offer, create_email_draft, import_bank_json,
    refresh_invoice_payment_status, generate_dunning_document, next_cashbook_no,
    generate_cashbook_pdf, get_smtp_config, smtp_is_configured, send_email_smtp,
    get_dunning_candidates, send_bulk_dunning, ensure_dunning_allowed, mahn_hindernis,
    fetch_bank_transactions, get_banking_config, banking_is_configured, BankingError,
    get_ai_config, ai_is_configured, generate_offer_from_text, AIError,
    AI_DEFAULT_TEXT_MODEL,
    transcribe_audio, stt_status, TranscriptionUnavailable,
    polish_service_text, generate_cover_letter_text,
    ai_review_positions, check_positions_local,
)
from .services.invoice_files import datei_rueckweg
from .services.invoice_archive import lese_archivdatei, pruefe_archiv
from .services.workflow_errors import BelegArchivFehler
from .services.mail import invoice_email_subject_body
from .services.readiness import MANUAL_CHECKS, is_local_only_mode, readiness_report, transport_security_checks
from .services.customer_timeline import KINDS as TIMELINE_KINDS, build_customer_timeline
from .services.reports import build_reports
from .services.recurring_preview import build_recurring_preview
from .services.zugferd_validation import validate_invoice_zugferd
from .services.tax_export import create_tax_package, parse_month

# Interner Logger: breite Exception-Handler (Batch-Läufe, KI-Endpoints) melden
# dem Nutzer eine freundliche Meldung, protokollieren den echten Fehler aber hier.
logger = logging.getLogger('sedo')

# Standard-Zahlungsziel (Tage), wenn beim Kunden nichts hinterlegt ist.
DEFAULT_PAYMENT_DAYS = 14
# Obergrenzen für Listen-/Importansichten.
MAX_OPEN_ITEMS = 500
MAX_PAYMENTS_LIST = 300
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
def customer_data_api(request, pk):
    """JSON-Endpoint: Kundendaten + Objekte für AJAX-Autobefüllung im Rechnungsformular."""
    customer = get_object_or_404(Customer, pk=pk)
    return JsonResponse(_customer_form_data(customer))


def _customer_form_data(customer):
    """Lesende Kopfdaten für API und initiale Wizard-Zusammenfassung."""
    return {
        'id': customer.pk,
        'salutation': customer.salutation or '',
        'customer_no': customer.customer_no or '',
        'customer_type': customer.customer_type or 'einmalig',
        'name': customer.name or '',
        'name2': customer.name2 or '',
        'contact_person': customer.contact_person or '',
        'phone': customer.phone or '',
        'email': customer.email or '',
        'street': customer.street or '',
        'postcode': customer.postcode or '',
        'city': customer.city or '',
        'country': customer.country or 'DE',
        'payment_days': customer.payment_days,
        'objects': [
            {
                'id': obj.pk,
                'name': obj.name,
                'street': obj.street or '',
                'postcode': obj.postcode or '',
                'city': obj.city or '',
                'country': obj.country or 'DE',
            }
            for obj in customer.customer_objects.order_by('name')
        ],
    }


def _sum_decimal(queryset, field: str) -> Decimal:
    """Summe eines DecimalField als saubere Decimal mit 2 Nachkommastellen.

    SQLite liefert bei ``Sum()`` über ein DecimalField einen Float zurück
    (z. B. 5274.7000000000). Diese Umwandlung sorgt für eine korrekte Anzeige.
    """
    total = queryset.aggregate(s=Sum(field))['s'] or Decimal('0.00')
    return Decimal(str(total)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


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


def _customer_from_invoice_form(form):
    customer = form.cleaned_data.get('customer')
    if customer:
        return customer
    name = (form.cleaned_data.get('name') or '').strip()
    if not name:
        return None
    return Customer.objects.create(
        salutation=form.cleaned_data.get('salutation') or '',
        customer_no=form.cleaned_data.get('customer_no') or '',
        customer_type=form.cleaned_data.get('customer_type') or 'einmalig',
        name=name,
        name2=form.cleaned_data.get('name2') or '',
        contact_person=form.cleaned_data.get('contact_person') or '',
        phone=form.cleaned_data.get('phone') or '',
        email=form.cleaned_data.get('email') or '',
        street=form.cleaned_data.get('street') or '',
        postcode=form.cleaned_data.get('postcode') or '',
        city=form.cleaned_data.get('city') or '',
        country=form.cleaned_data.get('country') or 'DE',
        # F-63, zweiter Fundort: ein Kunde mit Zahlungsziel 0 bekam dauerhaft 14.
        payment_days=(14 if form.cleaned_data.get('payment_days') is None
                      else form.cleaned_data.get('payment_days')),
    )


def _object_from_invoice_form(form, customer):
    obj = form.cleaned_data.get('obj')
    if obj:
        return obj
    object_name = (form.cleaned_data.get('object_name') or '').strip()
    if not object_name or not customer:
        return None
    return CustomerObject.objects.create(
        customer=customer,
        name=object_name,
        street=form.cleaned_data.get('object_street') or '',
        postcode=form.cleaned_data.get('object_postcode') or '',
        city=form.cleaned_data.get('object_city') or '',
        country=form.cleaned_data.get('object_country') or 'DE',
    )


def _preview_from_forms(form, formset, title='Entwurfsansicht'):
    items = []
    net = Decimal('0.00')
    for item_form in formset.forms:
        if not item_form.cleaned_data or item_form.cleaned_data.get('DELETE'):
            continue
        desc = item_form.cleaned_data.get('description') or ''
        if not desc:
            continue
        qty = item_form.cleaned_data.get('quantity') or Decimal('0.00')
        price = item_form.cleaned_data.get('unit_price') or Decimal('0.00')
        line_total = (qty * price).quantize(Decimal('0.01'))
        net += line_total
        items.append({
            'description': desc,
            'long_text': item_form.cleaned_data.get('long_text') or '',
            'quantity': qty,
            'unit': item_form.cleaned_data.get('unit') or '',
            'unit_price': price,
            'line_total': line_total,
            'article_no': item_form.cleaned_data.get('article_no') or '',
        })
    vat_rate = form.cleaned_data.get('vat_rate') or Decimal('19.00')
    vat = (net * vat_rate / Decimal('100')).quantize(Decimal('0.01'))
    return {
        'title': title,
        'customer': form.cleaned_data.get('customer'),
        'manual_customer': {k: form.cleaned_data.get(k) for k in ['salutation','customer_no','name','name2','contact_person','phone','email','street','postcode','city','country']},
        'obj': form.cleaned_data.get('obj'),
        'manual_object': {k: form.cleaned_data.get(k) for k in ['object_name','object_street','object_postcode','object_city','object_country']},
        'issue_date': form.cleaned_data.get('issue_date'),
        'period_start': form.cleaned_data.get('period_start'),
        'period_end': form.cleaned_data.get('period_end'),
        'items': items,
        'net': net.quantize(Decimal('0.01')),
        'vat_rate': vat_rate,
        'vat': vat,
        'gross': (net + vat).quantize(Decimal('0.01')),
        'note': form.cleaned_data.get('note') if 'note' in form.cleaned_data else '',
    }


def _create_invoice_email_file(invoice: Invoice):
    """Erzeugt eine .eml-Datei zur Rechnung und gibt den Pfad zurück."""
    attachment = lese_archivdatei(invoice)
    recipient = invoice.customer.email or ''
    if not recipient:
        return None
    subject, body = invoice_email_subject_body(invoice)
    out_dir = Path(settings.MEDIA_ROOT) / 'emails' / invoice.number
    return create_email_draft(recipient, subject, body, attachment, out_dir, f'{invoice.number}_email')


def _normalize_item_positions(items) -> None:
    """Setzt fortlaufende Positionsnummern (1..n) und speichert nur Änderungen."""
    for idx, item in enumerate(items, start=1):
        if item.position != idx:
            item.position = idx
            item.save(update_fields=['position', 'updated_at'])


def _recurring_payment_days(payment_days: int | None, customer_payment_days: int | None) -> int:
    """Vorlage/Rechnung vor Kundenwert; nur None fehlt, 0 bedeutet sofort fällig (F-63)."""
    if payment_days is not None:
        return payment_days
    if customer_payment_days is not None:
        return customer_payment_days
    return DEFAULT_PAYMENT_DAYS


def _create_recurring_template_from_invoice(inv: Invoice, title: str | None = None):
    """Speichert eine erzeugte Rechnung als Abo-/Monatsrechnungsvorlage."""
    tmpl = RecurringInvoiceTemplate.objects.create(
        customer=inv.customer,
        obj=inv.obj,
        title=title or f'Monatsrechnung {inv.customer.name}' + (f' · {inv.obj.name}' if inv.obj else ''),
        status='active',
        vat_rate=inv.vat_rate,
        payment_days=_recurring_payment_days(
            (inv.due_date - inv.issue_date).days if inv.due_date is not None else None,
            inv.customer.payment_days,
        ),
        note=inv.note or '',
    )
    for idx, item in enumerate(inv.items.all(), start=1):
        RecurringInvoiceItem.objects.create(
            template=tmpl,
            position=idx,
            description=item.description,
            long_text=item.long_text,
            quantity=item.quantity,
            unit=item.unit,
            unit_price=item.unit_price,
            article_no=item.article_no,
        )
    return tmpl


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
@require_POST
def ai_generate_offer(request):
    """KI-Assistent: grobe Ansage in einen vollständigen Angebotsentwurf umwandeln.

    Datenschutz: Es wird ausschließlich der diktierte Text der Leistungs-
    beschreibung an Claude übertragen – keine Kunden- oder Objektdaten.
    Nebenwirkung: neue Leistungen werden in den Leistungskatalog übernommen.
    """
    if not ai_is_configured():
        return JsonResponse({'ok': False, 'error': 'Kein Claude-API-Key hinterlegt. Bitte unter Einstellungen → KI-Assistent eintragen.'}, status=400)
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
    installiert, antwortet der Endpoint mit 503 – die Oberfläche fällt dann
    automatisch auf die Browser-Spracherkennung zurück.
    """
    audio = request.FILES.get('audio')
    if not audio:
        return JsonResponse({'ok': False, 'error': 'Es wurde kein Audio übertragen.'}, status=400)
    suffix = Path(audio.name or 'diktat.webm').suffix or '.webm'
    try:
        text = transcribe_audio(audio.read(), suffix=suffix)
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
    System-Prompt weist Claude an, Namen/Adressen ersatzlos wegzulassen.
    """
    if not ai_is_configured():
        return JsonResponse({'ok': False, 'error': 'Kein Claude-API-Key hinterlegt. Bitte unter Einstellungen → KI-Assistent eintragen.'}, status=400)
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
        return JsonResponse({'ok': False, 'error': 'Kein Claude-API-Key hinterlegt. Bitte unter Einstellungen → KI-Assistent eintragen.'}, status=400)
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
    Kundennummer wird dafür NICHT an Claude übertragen. Der optionale KI-Teil
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
            ki_error = 'Kein Claude-API-Key hinterlegt. Bitte unter Einstellungen → KI-Assistent eintragen.'
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
def dashboard(request):
    today = timezone.localdate()
    invoices = Invoice.objects.all()
    dunning_candidates = get_dunning_candidates(days_overdue_threshold=14)
    open_tasks = WorkTask.objects.filter(status='open').select_related(
        'customer', 'offer', 'invoice').order_by('due_date', 'priority', 'pk')
    context = {
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
        'draft_invoices': invoices.filter(lifecycle='draft').count(),
        'today': today,
        'open_offers': Offer.objects.filter(status='offen').count(),
        'customers': Customer.objects.count(),
        'latest_invoices': Invoice.objects.select_related('customer','obj')[:8],
        'latest_offers': Offer.objects.select_related('customer','obj')[:5],
        'dunning_candidates_count': len(dunning_candidates),
        'smtp_configured': smtp_is_configured(),
        'ai_configured': ai_is_configured(),
        'ai_text_model': AppSetting.get('ai_text_model', ''),
        'ai_default_text_model': AI_DEFAULT_TEXT_MODEL,
        'open_tasks': open_tasks[:6],
        'open_tasks_count': open_tasks.count(),
        'overdue_tasks_count': open_tasks.filter(due_date__lt=today).count(),
    }
    return render(request, 'sedo/dashboard.html', context)


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
        name = file.name.lower()
        if name.endswith('.csv'):
            decoded = file.read().decode('utf-8-sig').splitlines()
            delimiter = ';' if (decoded and ';' in decoded[0]) else ','
            rows = list(csv.DictReader(decoded, delimiter=delimiter))
        else:
            df = pd.read_excel(file)
            rows = df.to_dict('records')
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
                return render(request, 'sedo/invoice_preview.html', {'preview': preview, 'back_post': request.POST})
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
                    with datei_rueckweg() as merken, transaction.atomic():
                        # Kollisionssichere Rechnungsnummer vergeben und speichern
                        save_with_unique_number(inv, 'EN' if action == 'draft' else 'RE')
                        formset.instance = inv
                        formset.save()
                        _normalize_item_positions(inv.items.all())
                        recalc_invoice(inv)
                        inv.save()
                        merken(inv)
                        generate_invoice_files(inv, erstmalig=action != 'draft', request=request)
                        if src_offer:
                            # Guarded UPDATE: nur wenn das Angebot noch nicht
                            # verrechnet ist – sonst rollt alles zurück.
                            updated = Offer.objects.filter(
                                pk=src_offer.pk, linked_invoice__isnull=True,
                            ).exclude(status='in_rechnung').update(
                                status='in_rechnung', linked_invoice=inv,
                                updated_at=timezone.now(),
                            )
                            if not updated:
                                raise WorkflowError(
                                    f'Aus Angebot {src_offer.number} wurde soeben bereits '
                                    f'eine Rechnung erzeugt (paralleler Vorgang).')
                        if src_invoice and action != 'draft':
                            # Ursprungsrechnung als korrigiert markieren – blockiert
                            # damit weitere Korrekturen und Stornos des Originals.
                            # Entwürfe ersetzen das Original noch nicht.
                            mark_invoice_corrected(src_invoice, request=request)
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
                    # Erst die Datei, dann der Status (F-115): vorher stand die
                    # Rechnung auf 'versendet', bevor überhaupt feststand, ob
                    # der Entwurf erzeugt werden konnte.
                    try:
                        eml_path = _create_invoice_email_file(inv)
                    except (WorkflowError, OSError) as exc:
                        messages.error(request, f'Rechnung erstellt, E-Mail-Entwurf abgebrochen: {exc}')
                        return redirect('sedo:invoice_detail', inv.pk)
                    if eml_path and Path(eml_path).exists() and inv.status == 'erstellt':
                        inv.status = 'versendet'
                        inv.save(update_fields=['status', 'updated_at'])
                    write_audit(request, 'create', inv, f'Rechnung {inv.number} erzeugt und E-Mail-Entwurf erstellt',
                                metadata={'vorgang': 'versand', 'weg': 'eml'})
                    messages.success(request, f'Rechnung {inv.number} wurde erzeugt und ein E-Mail-Entwurf wurde erstellt.')
                    if eml_path and Path(eml_path).exists():
                        return FileResponse(open(eml_path, 'rb'), as_attachment=True, filename=Path(eml_path).name)
                    return redirect('sedo:invoice_detail', inv.pk)
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
    if invoice.xml_file and invoice.zugferd_file:
        validation = validate_invoice_zugferd(invoice)
        if not validation.ok:
            messages.error(request, 'Versand gestoppt: Die ZUGFeRD-Prüfung hat Fehler gefunden. Bitte den Prüfbericht öffnen.')
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
            # Nur 'erstellt' wird zu 'versendet'. Vorher hob jeder erneute
            # Versand den Zustand auf: eine bezahlte Rechnung galt danach wieder
            # als offen, eine korrigierte verlor ihren Belegzustand (F-07 c).
            if invoice.status == 'erstellt':
                invoice.status = 'versendet'
                invoice.save(update_fields=['status', 'updated_at'])
            # metadata statt neuem AuditLog-Action-Wert: JSONField braucht keine
            # Migration. Der Eintrag ist der Versandnachweis für das Ausblenden.
            write_audit(request, 'generate', invoice,
                        f'Rechnung {invoice.number} direkt per SMTP versendet an {recipient}',
                        metadata={'vorgang': 'versand', 'weg': 'smtp'})
            messages.success(request, f'Rechnung {invoice.number} wurde direkt an {recipient} versendet.')
            return redirect('sedo:invoice_detail', pk)
        except Exception as e:
            logger.exception('SMTP-Rechnungsversand fehlgeschlagen')
            messages.error(request, f'E-Mail-Versand fehlgeschlagen: {e}')
            return redirect('sedo:invoice_detail', pk)
    else:
        # Fallback: .eml-Datei zum Download
        out_dir = Path(settings.MEDIA_ROOT) / 'emails' / invoice.number
        try:
            eml_path = create_email_draft(recipient, subject, body, attachment, out_dir, f'{invoice.number}_email')
        except OSError as exc:
            messages.error(request, f'E-Mail-Entwurf konnte nicht gespeichert werden: {exc}')
            return redirect('sedo:invoice_detail', pk)
        if not eml_path or not Path(eml_path).exists():
            # Erst prüfen, dann Status setzen: vorher stand die Rechnung schon
            # auf 'versendet', obwohl die Datei gar nicht entstanden war (F-115).
            messages.error(request, 'E-Mail-Entwurf konnte nicht erzeugt werden.')
            return redirect('sedo:invoice_detail', pk)
        if invoice.status == 'erstellt':
            invoice.status = 'versendet'
            invoice.save(update_fields=['status', 'updated_at'])
        write_audit(request, 'generate', invoice, 'E-Mail-Entwurf (.eml) zur Rechnung erzeugt',
                    metadata={'vorgang': 'versand', 'weg': 'eml'})
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
def offer_create(request):
    from .models import MailMessage
    from email.utils import parseaddr
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
                return render(request, 'sedo/invoice_preview.html', {'preview': preview, 'is_offer': True})
            if source_mail:
                from .services.mail_storage import pin_message
                from .services.mail_secrets import MailError
                try:
                    pin_message(source_mail)
                except MailError as exc:
                    form.add_error(None, str(exc))
                    return render(request, 'sedo/offer_form.html', {'form': form, 'formset': formset,
                        'source_mail': source_mail, 'stt_available': stt_status()['available']})
            customer = _customer_from_invoice_form(form)
            if not customer:
                form.add_error('name', 'Bitte einen gespeicherten Kunden auswählen oder die Kopfdaten mit Name/Firma ausfüllen.')
            else:
                obj = _object_from_invoice_form(form, customer)
                off = form.save(commit=False)
                off.source_mail = source_mail
                off.customer = customer
                off.obj = obj
                off.valid_until = off.issue_date + timedelta(days=form.cleaned_data.get('valid_days') or DEFAULT_PAYMENT_DAYS)
                off.status = 'offen'
                # Kollisionssichere Angebotsnummer vergeben und speichern
                save_with_unique_number(off, 'AG')
                formset.instance = off
                formset.save()
                # Positionsnummern normalisieren (sonst steht im PDF überall „1“)
                _normalize_item_positions(off.items.all())
                recalc_offer(off)
                off.save()
                generate_offer_pdf(off)
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
        # transkribiert wird oder die Browser-Spracherkennung nutzt.
        'stt_available': stt_status()['available'],
        'source_mail': source_mail,
    })


@login_required
def offer_detail(request, pk):
    offer = get_object_or_404(Offer.objects.select_related('customer','obj','linked_invoice'), pk=pk)
    follow_up_form = OfferFollowUpForm(instance=offer)
    return render(request, 'sedo/offer_detail.html', {'offer': offer, 'follow_up_form': follow_up_form})


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
        return render(request, 'sedo/offer_detail.html', {'offer': offer, 'follow_up_form': form}, status=400)
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
        offer.status = status
        if status != 'offen':
            offer.follow_up_date = None
        offer.save(update_fields=['status', 'follow_up_date', 'updated_at'])
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


@login_required
def open_items(request):
    today = timezone.localdate()
    status = request.GET.get('status', 'open')
    q = request.GET.get('q', '').strip()
    # Die Grundmenge schließt Stornobelege zusätzlich über den Typ aus – ein
    # Stornobeleg ist keine offene Forderung, egal welchen Status er trägt.
    # offene_rechnungen() darf hier NICHT die Wurzel sein: die Reiter „Bezahlt"
    # und „Alle ohne Storno" (open_items.html) liefen sonst leer, weil die
    # Funktion 'bezahlt' ausschließt.
    invoices = (Invoice.objects.select_related('customer', 'obj')
                .prefetch_related('payments', 'dunning_documents')
                .exclude(lifecycle='cancelled').exclude(invoice_type='457'))
    if status == 'overdue':
        invoices = offene_rechnungen(invoices).filter(due_date__lt=today)
    elif status == 'paid':
        invoices = invoices.filter(lifecycle='issued', status='bezahlt')
    elif status == 'all':
        # „Alle ohne Storno" zeigt weiterhin auch Bezahltes – nur Entwürfe
        # gehören nicht in eine Liste offener Posten.
        invoices = invoices.exclude(lifecycle='draft')
    else:
        invoices = offene_rechnungen(invoices)
    if q:
        invoices = invoices.filter(Q(number__icontains=q) | Q(customer__name__icontains=q) | Q(obj__name__icontains=q))
    invoice_list = list(invoices[:MAX_OPEN_ITEMS])
    for inv in invoice_list:
        refresh_invoice_payment_status(inv)
    # Auf dem Reiter „Alle" stehen auch ersetzte Originale und Entwürfe – die
    # gehören nicht in die Summe der offenen Forderungen.
    open_total = sum((i.open_amount for i in invoice_list
                      if i.lifecycle == 'issued' and i.invoice_type != '457'),
                     Decimal('0.00'))
    overdue_total = sum((i.open_amount for i in invoice_list if i.is_overdue), Decimal('0.00'))
    dunning_candidates = get_dunning_candidates()
    return render(request, 'sedo/open_items.html', {
        'invoices': invoice_list,
        'status': status,
        'q': q,
        'today': today,
        'open_total': open_total,
        'overdue_total': overdue_total,
        'overdue_count': len([i for i in invoice_list if i.is_overdue]),
        'dunning_candidates': dunning_candidates,
        'dunning_candidates_count': len(dunning_candidates),
    })


@login_required
def bulk_dunning(request):
    """Batch-Mahnung: Mehrere Rechnungen gleichzeitig mahnen."""
    if request.method != 'POST':
        return redirect('sedo:open_items')
    # Mahnschreiben lassen sich nicht zurücknehmen. Deshalb fail-closed:
    # jede unklare Eingabe bricht ab, statt auf eine Massenaktion zu raten
    # (Review-Funde F-11 und F-66).
    kind = request.POST.get('kind', '')
    if kind not in ('reminder', 'dunning', 'auto'):
        messages.error(request, 'Mahnlauf abgebrochen: unklare Anforderung. '
                                'Bitte die Knöpfe auf der Seite „Offene Posten" verwenden.')
        return redirect('sedo:open_items')

    selected = request.POST.getlist('invoice_ids')
    if selected:
        result = send_bulk_dunning(selected, kind, request=request)
    elif kind == 'auto':
        # Nur der Mahnautomat darf ohne Auswahl arbeiten – er wählt je Rechnung
        # die fällige nächste Stufe und überspringt nichts.
        candidates = get_dunning_candidates()
        if not candidates:
            messages.success(request, 'Kein Mahnlauf nötig – derzeit gibt es keine Mahnkandidaten.')
            return redirect('sedo:open_items')
        result = send_bulk_dunning([str(inv.pk) for inv in candidates], kind, request=request)
    else:
        # Vorher wurde hier auf ALLE Kandidaten zurückgefallen – mit der vom
        # Knopf erzwungenen Stufe. Ein Klick ohne Häkchen erzeugte damit für
        # jede überfällige Rechnung sofort eine Mahnung, auch ohne vorherige
        # Zahlungserinnerung (F-66).
        messages.error(request, 'Bitte zuerst die Rechnungen anhaken, die gemahnt werden sollen. '
                                'Für alle fälligen Rechnungen auf einmal gibt es oben den Mahnautomaten.')
        return redirect('sedo:open_items')

    label = {'reminder': 'Zahlungserinnerungen', 'dunning': 'Mahnungen'}.get(kind, 'Mahnschreiben (Stufe automatisch)')
    write_audit(request, 'generate', object_type='Mahnung', object_repr='Batch',
                message=f'{result["created"]} {label} erstellt, {result["skipped"]} übersprungen')
    messages.success(request, f'{result["created"]} {label} erstellt. {result["skipped"]} übersprungen.')
    return redirect('sedo:open_items')


@login_required
def invoice_add_payment(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related('customer'), pk=pk)
    try:
        ensure_zahlung_erlaubt(invoice)
    except WorkflowError as fehler:
        messages.error(request, str(fehler))
        return redirect('sedo:invoice_detail', invoice.pk)
    if request.method == 'POST':
        form = ManualPaymentForm(request.POST)
        if form.is_valid():
            payment = form.save(commit=False)
            payment.invoice = invoice
            payment.matched_automatically = False
            if not payment.payer_name:
                payment.payer_name = invoice.customer.name
            payment.save()
            write_audit(request, 'payment', payment, f'Zahlung zu {invoice.number} erfasst')
            refresh_invoice_payment_status(invoice)
            messages.success(request, f'Zahlung über {payment.amount} € wurde zu {invoice.number} erfasst.')
            next_url = request.POST.get('next') or reverse('sedo:invoice_detail', args=[invoice.pk])
            return redirect(next_url)
    else:
        form = ManualPaymentForm(initial={
            'booking_date': timezone.localdate(),
            'amount': invoice.open_amount if invoice.open_amount > Decimal('0.00') else invoice.gross_total,
            'payer_name': invoice.customer.name,
            'purpose': f'Zahlung zu Rechnung {invoice.number}',
        })
    return render(request, 'sedo/payment_manual.html', {'form': form, 'invoice': invoice})


@login_required
def invoice_mark_paid(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related('customer'), pk=pk)
    if request.method != 'POST':
        return redirect('sedo:invoice_detail', invoice.pk)
    try:
        ensure_zahlung_erlaubt(invoice)
    except WorkflowError as fehler:
        messages.error(request, str(fehler))
        return redirect('sedo:invoice_detail', invoice.pk)
    amount = invoice.open_amount
    if amount <= Decimal('0.00'):
        messages.info(request, 'Diese Rechnung ist bereits vollständig ausgeglichen.')
    else:
        payment = Payment.objects.create(
            invoice=invoice,
            booking_date=timezone.localdate(),
            amount=amount,
            payer_name=invoice.customer.name,
            purpose=f'Manuell als bezahlt markiert · Rechnung {invoice.number}',
            matched_automatically=False,
        )
        write_audit(request, 'payment', payment, f'Rechnung {invoice.number} manuell als bezahlt markiert')
        refresh_invoice_payment_status(invoice)
        messages.success(request, f'Rechnung {invoice.number} wurde als bezahlt markiert.')
    next_url = request.POST.get('next') or reverse('sedo:invoice_detail', args=[invoice.pk])
    return redirect(next_url)


def _melde_abgewiesene_buchungsdaten(request, import_result):
    """Weist auf Bankzeilen ohne lesbares Buchungsdatum hin (F-48).

    Solche Zeilen wurden früher mit dem heutigen Datum gebucht. Jetzt werden
    sie abgewiesen – und einzeln aufgeführt, damit die Zahlung von Hand
    erfasst werden kann. Beim FinTS-Abruf gibt es keine Datei zum Nachbessern,
    deshalb stehen die Rohdaten in der Meldung.
    """
    fehler = (import_result or {}).get('date_errors') or []
    if not fehler:
        return
    zeilen = '; '.join(
        f"Zeile {f['zeile']}: Datum „{f['datum'] or 'leer'}“, {f['betrag']} € "
        f"von {f['name'] or 'unbekannt'}"
        for f in fehler[:5]
    )
    rest = f' … und {len(fehler) - 5} weitere' if len(fehler) > 5 else ''
    messages.warning(request, f'{len(fehler)} Zahlung(en) ohne lesbares Buchungsdatum wurden NICHT '
                              f'importiert und müssen von Hand erfasst werden: {zeilen}{rest}')
    write_audit(request, 'import', object_type='Bankimport', object_repr='Abgewiesene Zeilen',
                message=f'{len(fehler)} Zeilen ohne lesbares Buchungsdatum abgewiesen',
                metadata={'date_errors': str(fehler)[:500]})


@login_required
def payment_list(request):
    import_result = None
    if request.method == 'POST' and request.POST.get('action') == 'fints_fetch':
        # Echte Kontoanbindung (v2.3): Umsätze direkt bei der Bank abrufen.
        try:
            days = int(request.POST.get('days') or 30)
        except (TypeError, ValueError):
            days = 30
        try:
            import_result = fetch_bank_transactions(days=days,
                                                    pin=request.POST.get('fints_pin_once', ''))
            write_audit(request, 'import', object_type='Bankimport', object_repr='FinTS-Abruf',
                        message=f"FinTS: {import_result['abgerufen']} Umsätze abgerufen, "
                                f"{import_result['imported']} importiert, "
                                f"{import_result['matched']} zugeordnet", metadata={
                            k: v for k, v in import_result.items() if k != 'unassigned'})
            messages.success(request, f"Bankabruf abgeschlossen: {import_result['abgerufen']} Umsätze, "
                                      f"{import_result['imported']} neu importiert, "
                                      f"{import_result['matched']} zugeordnet, "
                                      f"{import_result['unclear']} unklar.")
            _melde_abgewiesene_buchungsdaten(request, import_result)
        except BankingError as e:
            messages.error(request, str(e))
        except Exception as e:
            logger.exception('FinTS-Abruf fehlgeschlagen')
            messages.error(request, f'Bankabruf fehlgeschlagen: {e}')
    elif request.method == 'POST' and request.FILES.get('file'):
        try:
            payload = json.loads(request.FILES['file'].read().decode('utf-8-sig'))
            import_result = import_bank_json(payload)
            # 'unassigned' enthält Payment-Objekte und ist nicht JSON-fähig –
            # dieser Protokolleintrag scheiterte deshalb bei JEDEM Dateiimport
            # und wurde stumm verschluckt. Der FinTS-Zweig oben filtert korrekt.
            write_audit(request, 'import', object_type='Bankimport', object_repr='Bank-JSON',
                        message=f"Bankimport: {import_result['imported']} importiert, "
                                f"{import_result['matched']} zugeordnet",
                        metadata={k: v for k, v in import_result.items() if k != 'unassigned'})
            messages.success(request, f"Bankimport abgeschlossen: {import_result['imported']} importiert, {import_result['matched']} zugeordnet, {import_result['unclear']} unklar, {import_result['ignored']} ignoriert.")
            _melde_abgewiesene_buchungsdaten(request, import_result)
        except Exception as e:
            logger.exception('Bank-JSON-Import fehlgeschlagen')
            messages.error(request, f'Bank-JSON konnte nicht importiert werden: {e}')
    payments = Payment.objects.select_related('invoice', 'invoice__customer').all()[:MAX_PAYMENTS_LIST]
    # Zuordnen-Liste: keine Entwürfe, keine ersetzten Originale, keine Stornos.
    open_invoices = offene_rechnungen(
        Invoice.objects.select_related('customer', 'obj').prefetch_related('payments'))[:200]
    return render(request, 'sedo/payment_list.html', {
        'payments': payments,
        'open_invoices': open_invoices,
        'form': BankJsonImportForm(),
        'import_result': import_result,
        'banking_configured': banking_is_configured(),
        'banking_pin_stored': bool(AppSetting.get('fints_pin', '')),
    })


@login_required
def payment_assign(request, pk):
    payment = get_object_or_404(Payment, pk=pk)
    if request.method == 'POST':
        form = ManualPaymentAssignForm(request.POST)
        if form.is_valid():
            inv = form.cleaned_data['invoice']
            old_invoice = payment.invoice
            payment.invoice = inv
            payment.matched_automatically = False
            payment.save(update_fields=['invoice','matched_automatically','updated_at'])
            write_audit(request, 'payment', payment, f'Zahlung manuell {inv.number} zugeordnet')
            if old_invoice:
                refresh_invoice_payment_status(old_invoice)
            refresh_invoice_payment_status(inv)
            messages.success(request, f'Zahlung wurde {inv.number} zugeordnet.')
            return redirect('sedo:payment_list')
    else:
        form = ManualPaymentAssignForm()
    return render(request, 'sedo/payment_assign.html', {'payment': payment, 'form': form})


@login_required
def payment_delete(request, pk):
    payment = get_object_or_404(Payment, pk=pk)
    invoice = payment.invoice
    if request.method == 'POST':
        # pflicht=True – siehe invoice_delete.
        try:
            write_audit(request, 'delete', payment,
                        'Zahlung als gelöscht markiert (GoBD-Soft-Delete)', pflicht=True)
        except AuditWriteError:
            messages.error(request, 'Die Zahlung wurde NICHT ausgeblendet: Der Vorgang lässt sich '
                                    'gerade nicht protokollieren. Bitte später erneut versuchen.')
            return redirect('sedo:payment_list')
        # GoBD: Zahlungen werden nur ausgeblendet, nicht hart entfernt.
        payment.soft_delete()
        if invoice:
            refresh_invoice_payment_status(invoice)
        messages.success(request, 'Zahlung wurde ausgeblendet. Der Datensatz bleibt erhalten (GoBD).')
        return redirect('sedo:payment_list')
    return render(request, 'sedo/confirm_delete.html', {'title': 'Zahlung ausblenden', 'text': 'Diese Zahlung wirklich ausblenden? Der Datensatz bleibt GoBD-konform erhalten.', 'cancel_url': reverse('sedo:payment_list')})


@login_required
@require_POST
def invoice_reminder(request, pk, kind):
    invoice = get_object_or_404(Invoice, pk=pk)
    if kind not in ['reminder','dunning']:
        messages.error(request, 'Unbekannte Mahnstufe. Es wurde kein Mahndokument erzeugt.')
        return redirect('sedo:invoice_detail', invoice.pk)
    # Sperre gegen Mehrfach-Mahnung derselben Stufe – ensure_* fängt den
    # sequenziellen Fall, der Unique-Constraint (via WorkflowError aus
    # generate_dunning_document) den parallelen.
    try:
        ensure_dunning_allowed(invoice, kind)
        doc = generate_dunning_document(invoice, kind, request=request)
    except WorkflowError as e:
        messages.error(request, str(e))
        return redirect('sedo:invoice_detail', invoice.pk)
    # Der verpflichtende Einzelbeleg-Audit wurde bereits atomar im Service geschrieben.
    messages.success(request, f'{doc.get_kind_display()} wurde erzeugt.')
    return redirect('sedo:invoice_detail', invoice.pk)


@login_required
def cashbook_list(request):
    month = request.GET.get('month') or timezone.localdate().strftime('%Y-%m')
    entries = CashbookEntry.objects.filter(month=month).order_by('booking_no')
    income_total = _sum_decimal(entries.filter(kind='income'), 'amount')
    expense_total = _sum_decimal(entries.filter(kind__in=['expense', 'correction']), 'amount')
    return render(request, 'sedo/cashbook_list.html', {
        'month': month,
        'entries': entries,
        'income_total': income_total,
        'expense_total': expense_total,
        'saldo': income_total - expense_total,
    })


@login_required
def cashbook_create(request, kind):
    if kind not in ['income','expense']:
        kind = 'income'
    today = timezone.localdate()
    first_this_month = today.replace(day=1)
    last_prev = first_this_month - timedelta(days=1)
    while last_prev.weekday() >= 5:
        last_prev -= timedelta(days=1)
    if request.method == 'POST':
        form = CashbookEntryForm(request.POST)
        if form.is_valid():
            e = form.save(commit=False)
            e.kind = kind
            e.month = e.booking_date.strftime('%Y-%m')
            e.booking_no = next_cashbook_no(e.month)
            e.save()
            write_audit(request, 'create', e, 'Kassenbuchung gespeichert')
            messages.success(request, 'Kassenbuchung gespeichert.')
            return redirect(f"{reverse('sedo:cashbook_list')}?month={e.month}")
    else:
        month = last_prev.strftime('%Y-%m')
        form = CashbookEntryForm(initial={'booking_date': last_prev, 'booking_no': next_cashbook_no(month)})
    return render(request, 'sedo/cashbook_form.html', {'form': form, 'kind': kind, 'title': 'Neue Einnahme' if kind == 'income' else 'Neue Ausgabe'})


@login_required
def cashbook_import_bank(request):
    if request.method == 'POST' and request.FILES.get('file'):
        try:
            payload = json.loads(request.FILES['file'].read().decode('utf-8-sig'))
            if isinstance(payload, dict):
                payload = payload.get('transactions') or payload.get('data') or []
            # Zweiphasig (NEU-2): erst alle Zeilen lesen und prüfen, dann
            # buchen. Vorher wurde ein unlesbares Buchungsdatum still durch
            # HEUTE ersetzt – in einer Kassenbuchung, die anschließend nicht
            # mehr korrigierbar ist. Lieber gar nichts buchen als falsch.
            zu_buchen = []
            datums_fehler = []
            for nummer, t in enumerate(payload, 1):
                amount = Decimal(str(t.get('betrag', t.get('amount', '0')))).quantize(Decimal('0.01'))
                purpose = str(t.get('verwendungszweck') or t.get('purpose') or '')
                text = (purpose + ' ' + str(t.get('typ',''))).lower()
                if not (amount < 0 and any(k in text for k in ['bargeld','geldautomat','atm','barabhebung','auszahlung'])):
                    continue
                booking_date = parse_date_optional(t.get('datum') or t.get('date'))
                if booking_date is None:
                    datums_fehler.append(f'Zeile {nummer} ({purpose[:40] or "ohne Verwendungszweck"}): '
                                         f'Buchungsdatum „{t.get("datum") or t.get("date") or "leer"}" unlesbar')
                    continue
                zu_buchen.append((booking_date, purpose, amount, t))

            if datums_fehler:
                write_audit(request, 'import', object_type='Kassenbuch', object_repr='Bankimport',
                            message='Abgebrochen: ' + ' · '.join(datums_fehler))
                messages.error(request, 'Kassenbuch-Bankimport abgebrochen, es wurde nichts gebucht. '
                                        + ' · '.join(datums_fehler))
                return redirect('sedo:cashbook_list')

            imported = 0
            with transaction.atomic():
                for booking_date, purpose, amount, t in zu_buchen:
                    month = booking_date.strftime('%Y-%m')
                    CashbookEntry.objects.create(month=month, booking_no=next_cashbook_no(month), receipt_no=str(t.get('belegnummer') or t.get('receipt_no') or ''), booking_date=booking_date, kind='income', name='Bargeldabhebung Bank', text=purpose or 'Bargeldabhebung', amount=abs(amount))
                    imported += 1
            write_audit(request, 'import', object_type='Kassenbuch', object_repr='Bankimport', message=f'{imported} Bargeldabhebungen übernommen')
            messages.success(request, f'{imported} Bargeldabhebungen ins Kassenbuch übernommen.')
        except Exception as e:
            logger.exception('Kassenbuch-Bankimport fehlgeschlagen')
            messages.error(request, f'Bankimport Kassenbuch fehlgeschlagen: {e}')
    return redirect('sedo:cashbook_list')


_DATE_FORMATS_FOR_VIEW = ['%Y-%m-%d', '%d.%m.%Y', '%Y%m%d']


def parse_date_optional(value):
    """Parst ein Datum; gibt None zurück, wenn es nicht lesbar ist."""
    # datetime VOR date prüfen – datetime ist eine Unterklasse von date.
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value or '').strip()
    for fmt in _DATE_FORMATS_FOR_VIEW:
        try:
            return datetime.strptime(s[:10] if fmt == '%Y-%m-%d' else s, fmt).date()
        except ValueError:
            continue
    return None


def parse_date_for_view(value, feld='Datum'):
    """Parst ein Pflichtdatum aus dem Request – ohne stillen Ersatzwert.

    Bisher wurde ein unlesbares Datum stumm durch HEUTE ersetzt. Der Wert
    speist das Rechnungsdatum des GWG- und des Abo-Monatslaufs sowie das
    Buchungsdatum des Kassenbuch-Bankimports: ein ganzer Massenlauf konnte
    lautlos falsch datiert werden. Das Rechnungsdatum ist steuerlich relevant
    und bestimmt über services/numbering.py zusätzlich den Nummernkreis-Monat.

    Wirft ``DatumUnlesbar``; die drei Aufrufer fangen das ab und brechen ab.
    """
    ergebnis = parse_date_optional(value)
    if ergebnis is None:
        raise DatumUnlesbar(f'{feld}: „{str(value or "").strip() or "leer"}" ist kein gültiges Datum.')
    return ergebnis


@login_required
def cashbook_pdf(request):
    month = request.GET.get('month') or timezone.localdate().strftime('%Y-%m')
    path = generate_cashbook_pdf(month)
    if not path.exists():
        messages.error(request, 'Die Kassenbuch-PDF konnte nicht erzeugt werden.')
        return redirect(f"{reverse('sedo:cashbook_list')}?month={month}")
    return FileResponse(open(path, 'rb'), content_type='application/pdf', filename=path.name)



def _clean_excel_value(value):
    if value is None:
        return ''
    try:
        if pd.isna(value):
            return ''
    except Exception:
        pass
    txt = str(value).strip()
    if txt.endswith('.0') and txt[:-2].isdigit():
        return txt[:-2]
    return txt



def _monthly_zip_email(invoices, customer, month):
    """Erzeugt einen E-Mail-Entwurf nur mit vollständiger Auswahl gespeicherter Originale."""
    attachments = [lese_archivdatei(inv) for inv in invoices]
    out_dir = Path(settings.MEDIA_ROOT) / 'monthly_emails'
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_month = str(month or timezone.localdate().strftime('%Y-%m')).replace('/', '-').replace(' ', '_')
    zip_path = out_dir / f'Monatsrechnungen_{safe_month}.zip'
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for attachment in attachments:
            zf.writestr(attachment.name, attachment.data)
    msg = EmailMessage()
    msg['To'] = customer.email or ''
    msg['From'] = 'info@sedo-magdeburg.de'
    msg['Subject'] = f'Monatsrechnungen {safe_month} - SEDO Objektdienstleistungen'
    body = (
        f'Sehr geehrte Damen und Herren,\n\n'
        f'anbei übersenden wir Ihnen die Monatsrechnungen für {safe_month} als ZIP-Datei.\n'
        f'Enthaltene Rechnungen: {len(invoices)}\n\n'
        f'Mit freundlichen Grüßen\nSEDO Objektdienstleistungen GmbH\n'
    )
    msg.set_content(body)
    if zip_path.exists():
        msg.add_attachment(zip_path.read_bytes(), maintype='application', subtype='zip', filename=zip_path.name)
    eml_path = out_dir / f'Monatsrechnungen_{safe_month}.eml'
    eml_path.write_bytes(msg.as_bytes())
    return eml_path

def _read_aareon_mapping_dataframe(file):
    """Liest die echte GWG-Mappingdatei robust ein.

    Die Kundendatei hat häufig zwei Kopfzeilen:
    Zeile 1: Objektfeld / Ausgangsfeld
    Zeile 2: Rechnungssteller Abrechnungseinheit / Leistungsart-ID / ...
    Darum wird zuerst header=1 versucht. Falls das nicht passt, fällt der Import auf header=0 zurück.
    """
    def normalize_columns(df):
        df = df.copy()
        df.columns = [str(c).strip() for c in df.columns]
        return df

    # Datei ggf. mehrfach lesbar machen
    try:
        file.seek(0)
    except Exception:
        pass
    df = normalize_columns(pd.read_excel(file, sheet_name=0, header=1, dtype=object))
    needed = {'Rechnungssteller Abrechnungseinheit', 'Leistungsart-ID'}
    if not needed.issubset(set(df.columns)):
        try:
            file.seek(0)
        except Exception:
            pass
        df0 = normalize_columns(pd.read_excel(file, sheet_name=0, header=0, dtype=object))
        if needed.issubset(set(df0.columns)):
            df = df0
        else:
            # Sonderfall: erste Datenzeile enthält die echten Header
            for i in range(min(5, len(df0))):
                possible = [_clean_excel_value(v) for v in df0.iloc[i].tolist()]
                if 'Rechnungssteller Abrechnungseinheit' in possible and 'Leistungsart-ID' in possible:
                    df = df0.iloc[i+1:].copy()
                    df.columns = possible
                    break
    return df


def _month_bounds(month_str):
    year, month = [int(x) for x in month_str.split('-')]
    start = date(year, month, 1)
    if month == 12:
        end = date(year, 12, 31)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    return start, end


class DatumUnlesbar(ValueError):
    """Ein Pflichtdatum ließ sich nicht lesen.

    Bewusst eine eigene Ausnahme statt eines Ersatzwerts: ein still auf HEUTE
    gesetztes Rechnungs- oder Buchungsdatum landet unbemerkt in einem
    aufbewahrungspflichtigen Beleg (Nebenbefund NEU-2).
    """


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

def _perform_aareon_mapping_import(df, request):
    """Importiert einen bereits gelesenen DataFrame als AareonMapping-Einträge.

    Ersetzt den Mapping-Bestand **transaktional**: Löschen und Neuanlage laufen
    in EINER Transaktion – schlägt irgendetwas mitten im Import fehl, wird alles
    zurückgerollt und der alte Bestand bleibt vollständig erhalten (vorher
    konnte ein Fehler nach dem Löschen den kompletten Bestand kosten).
    Wirft bei ungültigen Spalten eine ValueError-Exception (vor dem Löschen).
    """
    required = ['Rechnungssteller Abrechnungseinheit', 'Leistungsart-ID']
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError('Pflichtspalten fehlen: ' + ', '.join(missing) + '. Erwartet wird die GWG-Mappingdatei mit der Fachkopfzeile in Zeile 2.')
    # Zeilen erst im Speicher sammeln (letzter Eintrag je Einheit+Leistung
    # gewinnt – wie vorher bei update_or_create), dann EIN bulk_create statt
    # hunderter Einzel-Queries (Audit-Empfehlung 15.08.). Der Bestand wurde
    # zuvor gelöscht, Kollisionen mit Altdaten gibt es daher nicht.
    rows: dict[tuple, AareonMapping] = {}
    for _, row in df.iterrows():
        bu = _clean_excel_value(row.get('Rechnungssteller Abrechnungseinheit'))
        sid = _clean_excel_value(row.get('Leistungsart-ID'))
        if not bu or not sid:
            continue
        valid_from = None
        raw_date = row.get('gültig ab') if 'gültig ab' in df.columns else row.get('gueltig ab') if 'gueltig ab' in df.columns else None
        try:
            if hasattr(raw_date, 'date'):
                valid_from = raw_date.date()
            elif raw_date:
                valid_from = pd.to_datetime(raw_date).date()
        except (ValueError, TypeError):
            valid_from = None
        rows[(bu, sid)] = AareonMapping(
            billing_unit_sender=bu,
            service_id=sid,
            company_code=_clean_excel_value(row.get('Buchungskreis')),
            erp_contract_no=_clean_excel_value(row.get('ERP-Vertragsnummer')),
            we_internal=_clean_excel_value(row.get('Wirtschaftseinheit')),
            ae_internal=_clean_excel_value(row.get('Abrechnungseinheit')),
            nksl_internal=_clean_excel_value(row.get('Nebenkostenschlüssel')),
            account=_clean_excel_value(row.get('Sachkonto')),
            valid_from=valid_from,
        )
    # Schutz vor Totalverlust (Review-Fund F-17): Enthält die Datei zwar die
    # richtige Kopfzeile, aber keine einzige verwertbare Zeile (falsches
    # Tabellenblatt, gefilterter Export, leere Vorlage), dann wurde der
    # komplette Mapping-Bestand gelöscht und durch nichts ersetzt. Vor der
    # Transaktion abbrechen – der Aufrufer meldet die Ausnahme als Fehler.
    if not rows:
        raise ValueError('Keine gültigen Mapping-Zeilen gefunden (jede Zeile braucht '
                         '„Rechnungssteller Abrechnungseinheit" und „Leistungsart-ID"). '
                         'Der bisherige Mapping-Bestand bleibt unverändert.')
    with transaction.atomic():
        AareonMapping.objects.all().delete()
        AareonMapping.objects.bulk_create(rows.values())
    count = len(rows)
    stats = {
        'count': count,
        'units': AareonMapping.objects.values('billing_unit_sender').distinct().count(),
        'services': AareonMapping.objects.values('service_id').distinct().count(),
    }
    # count ist hier immer > 0 – der Fall „0 Zeilen" bricht oben ab (F-17).
    write_audit(request, 'import', object_type='AareonMapping', object_repr='GWG Mapping', message=f'{count} Mapping-Zeilen importiert')
    messages.success(request, f'{count} Mapping-Zeilen importiert. Daraus können jetzt GWG-Monatsrechnungen erzeugt werden.')
    return stats


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
                    eml_path = _monthly_zip_email(invoices, customer, month)
                    return FileResponse(open(eml_path, 'rb'), as_attachment=True, filename=eml_path.name, content_type='message/rfc822')
                except Exception as e:
                    messages.error(request, f'E-Mail-Entwurf konnte nicht erzeugt werden: {e}')

        elif action == 'generate':
            customer_id = request.POST.get('customer')
            month = request.POST.get('month') or default_month
            # Alle Eingabefehler in EINEM Durchgang sammeln, damit der Nutzer
            # sie zusammen sieht statt einen nach dem anderen. Stille
            # Ersatzwerte gibt es hier nicht mehr: ein Massenlauf mit falschem
            # Rechnungsdatum oder falschem Zahlungsziel waere sonst nur ueber
            # ebenso viele Stornos zu korrigieren (F-16, NEU-2).
            lauf_fehler = []
            issue_date = None
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
                ps, pe = _month_bounds(month)
                due = issue_date + timedelta(days=payment_days)
                created = skipped = missing = 0
                errors = []
                created_ids = []
                units = list(AareonMapping.objects.values_list('billing_unit_sender', flat=True).distinct().order_by('billing_unit_sender'))
                if limit:
                    units = units[:limit]
                # Mapping einmalig indexieren statt pro Einheit × Leistung neu abzufragen (kein N+1).
                mapping_index = {
                    (m.billing_unit_sender, m.service_id): m
                    for m in AareonMapping.objects.all()
                }
                for unit_name in units:
                    if skip_existing and Invoice.objects.filter(customer=customer, obj__name=unit_name, source='GWG Monatsrechnung', period_start=ps, period_end=pe).exclude(lifecycle='cancelled').exists():
                        skipped += 1
                        continue
                    # Erst prüfen, welche Leistungen gemappt sind – erst dann eine Nummer
                    # vergeben, damit keine Rechnungsnummern „verbrannt“ werden.
                    matched = []
                    for svc in services:
                        mapping = mapping_index.get((unit_name, svc['id']))
                        if mapping:
                            matched.append((svc, mapping))
                        else:
                            missing += 1
                    if not matched:
                        continue
                    # Jede Einheit läuft in EINER Transaktion: schlägt irgendetwas
                    # (auch die PDF-/XML-Erzeugung) fehl, wird die komplette
                    # Rechnung zurückgerollt – keine Teilrechnungen in der DB.
                    try:
                        with datei_rueckweg() as merken, transaction.atomic():
                            obj, _ = CustomerObject.objects.get_or_create(
                                customer=customer,
                                name=unit_name,
                                defaults={'street': unit_name, 'postcode': customer.postcode, 'city': customer.city, 'country': customer.country or 'DE'}
                            )
                            inv = create_numbered_invoice(
                                invoice_type='380',
                                customer=customer,
                                obj=obj,
                                issue_date=issue_date,
                                due_date=due,
                                period_start=ps,
                                period_end=pe,
                                vat_rate=Decimal('19.00'),
                                status='erstellt',
                                source='GWG Monatsrechnung',
                                note=f'Monatsrechnung {month} auf Basis GWG-Mapping.',
                            )
                            for pos_no, (svc, mapping) in enumerate(matched, start=1):
                                InvoiceItem.objects.create(
                                    invoice=inv,
                                    position=pos_no,
                                    description=svc['description'],
                                    quantity=svc['quantity'],
                                    unit=svc['unit'],
                                    unit_price=svc['unit_price'],
                                    article_no=mapping.nksl_internal,
                                    leistungsart_id=mapping.service_id,
                                    leistungsart_text=mapping.service_id,
                                )
                            recalc_invoice(inv)
                            inv.save()
                            merken(inv)
                            generate_invoice_files(inv, erstmalig=True, request=request)
                        created += 1
                        created_ids.append(inv.id)
                    except IntegrityError:
                        # Idempotenz-Schutz (Unique-Constraint) hat gegriffen:
                        # dieselbe Monatsrechnung existiert bereits – z. B. bei
                        # doppelt/parallel gestartetem Lauf. Kein Fehler.
                        logger.info('GWG-Monatslauf: Einheit %s existiert bereits (übersprungen)', unit_name)
                        skipped += 1
                    except Exception as e:
                        logger.exception('GWG-Monatslauf: Einheit %s fehlgeschlagen', unit_name)
                        errors.append(f'{unit_name}: {e}')
                request.session['last_monthly_invoice_ids'] = created_ids
                generated_invoice_ids = created_ids
                generated_invoices = Invoice.objects.filter(id__in=created_ids).select_related('customer','obj').order_by('number') if created_ids else []
                run_result = {'created': created, 'skipped': skipped, 'missing': missing, 'errors': errors[:20], 'month': month, 'invoice_ids': created_ids}
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


def _generate_recurring_invoice_from_template(tmpl, issue_date, period_start, period_end, due_date):
    """Erzeugt eine Abo-Monatsrechnung – atomar: Fehler (auch bei PDF/XML)
    rollen die komplette Rechnung zurück, keine Teilrechnungen."""
    with datei_rueckweg() as merken, transaction.atomic():
        return _generate_recurring_invoice_inner(tmpl, issue_date, period_start, period_end, due_date, merken)


def _generate_recurring_invoice_inner(tmpl, issue_date, period_start, period_end, due_date, merken):
    inv = create_numbered_invoice(
        invoice_type='380',
        customer=tmpl.customer,
        obj=tmpl.obj,
        issue_date=issue_date,
        due_date=due_date,
        period_start=period_start,
        period_end=period_end,
        vat_rate=tmpl.vat_rate,
        status='erstellt',
        source='Abo Monatsrechnung',
        note=tmpl.note or f'Monatsrechnung auf Basis der Vorlage „{tmpl.title}“.',
    )
    for idx, item in enumerate(tmpl.items.all(), start=1):
        InvoiceItem.objects.create(
            invoice=inv,
            position=idx,
            description=item.description,
            long_text=item.long_text,
            quantity=item.quantity,
            unit=item.unit,
            unit_price=item.unit_price,
            article_no=item.article_no,
        )
    recalc_invoice(inv)
    inv.save()
    merken(inv)
    generate_invoice_files(inv, erstmalig=True)
    return inv


@login_required
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

@login_required
def settings_page(request):
    stats = None
    if request.method == 'POST':
        action = request.POST.get('action') or 'save_settings'

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

        if action == 'save_ai':
            # API-Key nur speichern, wenn neu eingegeben (leer = unverändert)
            new_key = request.POST.get('ai_api_key', '').strip()
            if request.POST.get('ai_api_key_clear'):
                AppSetting.set('ai_api_key', '')
            elif new_key:
                AppSetting.set('ai_api_key', new_key)
            AppSetting.set('ai_text_model', request.POST.get('ai_text_model', '').strip())
            write_audit(request, 'update', object_type='AppSetting', object_repr='KI-Assistent',
                        message='KI-Einstellungen gespeichert')
            messages.success(request, 'KI-Einstellungen gespeichert.')
            return redirect('sedo:settings')

        if action == 'save_smtp':
            AppSetting.set('smtp_host',      request.POST.get('smtp_host', '').strip())
            AppSetting.set('smtp_port',      request.POST.get('smtp_port', '587').strip() or '587')
            AppSetting.set('smtp_user',      request.POST.get('smtp_user', '').strip())
            AppSetting.set('smtp_from_name', request.POST.get('smtp_from_name', '').strip())
            AppSetting.set('smtp_tls',       '1' if request.POST.get('smtp_tls') else '0')
            # Passwort nur speichern wenn neu eingegeben (leeres Feld = unverändert lassen)
            new_pw = request.POST.get('smtp_password', '')
            if new_pw:
                AppSetting.set('smtp_password', new_pw)
            write_audit(request, 'update', object_type='AppSetting', object_repr='SMTP',
                        message='SMTP-Einstellungen gespeichert')
            messages.success(request, 'SMTP-Einstellungen gespeichert.')
            return redirect('sedo:settings')

        if action == 'save_banking':
            AppSetting.set('fints_blz',        request.POST.get('fints_blz', '').strip())
            AppSetting.set('fints_endpoint',   request.POST.get('fints_endpoint', '').strip())
            AppSetting.set('fints_login',      request.POST.get('fints_login', '').strip())
            AppSetting.set('fints_iban',       request.POST.get('fints_iban', '').replace(' ', '').strip())
            AppSetting.set('fints_product_id', request.POST.get('fints_product_id', '').strip())
            # PIN nur speichern, wenn neu eingegeben; Löschen per Checkbox.
            # (Wie ai_api_key/smtp_password: aus Backups bereinigt, Env hat Vorrang.)
            if request.POST.get('fints_pin_clear'):
                AppSetting.set('fints_pin', '')
            elif request.POST.get('fints_pin', ''):
                AppSetting.set('fints_pin', request.POST.get('fints_pin', ''))
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
                except Exception as e:
                    messages.error(request, f'SMTP-Test fehlgeschlagen: {e}')
            return redirect('sedo:settings')

        AppSetting.set('aareon_seller_id', request.POST.get('aareon_seller_id', ''))
        AppSetting.set('aareon_include_seller_assigned_id', '1' if request.POST.get('aareon_include_seller_assigned_id') else '0')
        write_audit(request, 'update', object_type='AppSetting', object_repr='Einstellungen', message='Systemeinstellungen gespeichert')
        messages.success(request, 'Einstellungen wurden gespeichert.')
        return redirect('sedo:settings')

    smtp_cfg = get_smtp_config()
    context = {
        'aareon_seller_id': AppSetting.get('aareon_seller_id', ''),
        'aareon_include_seller_assigned_id': AppSetting.get('aareon_include_seller_assigned_id', '0'),
        'mapping_count': AareonMapping.objects.count(),
        'unit_count': AareonMapping.objects.values('billing_unit_sender').distinct().count(),
        'service_count': AareonMapping.objects.values('service_id').distinct().count(),
        'mappings': AareonMapping.objects.all()[:50],
        'stats': stats,
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
            msg['From'] = 'info@sedo-magdeburg.de'
            msg['Subject'] = f'SEDO Rechnungsexport {selected_month}'
            msg.set_content(
                f'Sehr geehrte Damen und Herren,\n\n'
                f'anbei erhalten Sie das Steuerberaterpaket für {selected_month}.\n'
                f'Enthaltene Rechnungen: {manifest["rechnungen"]}\n'
                f'Enthalten sind Belege, Buchungsdaten und ein Exportprotokoll.\n\n'
                f'Mit freundlichen Grüßen\nSEDO Objektdienstleistungen GmbH\n'
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
                messages.success(request, 'Wiederherstellung ist vollständig vorbereitet. Bitte die App schließen und erneut öffnen; vor dem Start wird der geprüfte Stand eingespielt.')
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
