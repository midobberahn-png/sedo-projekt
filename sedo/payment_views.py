"""Offene Posten, Zahlungserfassung und Mahnansichten."""
from __future__ import annotations

from decimal import Decimal
import json
import logging
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.db.models import Q
from django.views.decorators.http import require_POST
from .audit import AuditWriteError, write_audit
from .forms import BankJsonImportForm, ManualPaymentAssignForm, ManualPaymentForm
from .models import AppSetting, Invoice, Payment
from .services import (
    WorkflowError, ensure_zahlung_erlaubt, offene_rechnungen,
    refresh_invoice_payment_status, get_dunning_candidates,
    send_bulk_dunning, generate_dunning_document, ensure_dunning_allowed,
    import_bank_json, fetch_bank_transactions, banking_is_configured, BankingError,
)

logger = logging.getLogger('sedo')
MAX_OPEN_ITEMS = 500
MAX_PAYMENTS_LIST = 300


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


def _melde_mehrdeutige_buchungen(request, import_result):
    """Nicht sicher unterscheidbare Bankbuchungen zur manuellen Klärung melden."""
    rows = (import_result or {}).get('ambiguous') or []
    if not rows:
        return
    examples = '; '.join(
        f"Zeile {row['zeile']}: {row['datum']}, {row['betrag']} € von {row['name'] or 'unbekannt'}"
        for row in rows[:5]
    )
    rest = f' … und {len(rows) - 5} weitere' if len(rows) > 5 else ''
    messages.warning(request, f'{len(rows)} Bankbuchung(en) wurden nicht automatisch übernommen, '
                              f'weil sie sich nicht sicher von vorhandenen Buchungen unterscheiden '
                              f'lassen oder eine Bankreferenz mit anderen Buchungsdaten erneut vorkommt. '
                              f'Bitte mit dem Kontoauszug vergleichen und fehlende Zahlungen '
                              f'manuell erfassen: {examples}{rest}')
    write_audit(request, 'import', object_type='Bankimport', object_repr='Mehrdeutige Buchungen',
                message=f'{len(rows)} mehrdeutige Bankbuchungen zur Klärung',
                metadata={'anzahl': len(rows), 'beispiele': rows[:5]})


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
            _melde_mehrdeutige_buchungen(request, import_result)
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
            _melde_mehrdeutige_buchungen(request, import_result)
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
