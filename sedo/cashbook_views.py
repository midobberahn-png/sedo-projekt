"""Kassenbuchansichten und Abhebungsimport."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode
import json
import logging
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from .audit import write_audit
from .forms import CashbookEntryForm
from .models import CashbookEntry
from .services import next_cashbook_no, generate_cashbook_pdf
from .services.date_input import parse_date_optional
from .view_support import _sum_decimal

logger = logging.getLogger('sedo')


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
            if request.POST.get('action') == 'save_and_new':
                return redirect('sedo:cashbook_create', kind=kind)
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


@login_required
def cashbook_pdf(request):
    month = request.GET.get('month') or timezone.localdate().strftime('%Y-%m')
    path = generate_cashbook_pdf(month)
    if not path.exists():
        messages.error(request, 'Die Kassenbuch-PDF konnte nicht erzeugt werden.')
        return redirect(f"{reverse('sedo:cashbook_list')}?month={month}")
    document_path = path.resolve().relative_to(Path(settings.MEDIA_ROOT).resolve()).as_posix()
    return redirect(reverse('sedo:document_preview', kwargs={'path': document_path})
                    + '?' + urlencode({
                        'back': reverse('sedo:cashbook_list') + '?month=' + month}))
