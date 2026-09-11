"""Mahnwesen: Zahlungserinnerungen/Mahnungen erzeugen und Kandidaten finden."""
from __future__ import annotations

import logging
import os
import tempfile
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import Exists, F, OuterRef, Sum
from django.utils import timezone
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas

from ..core.rechnungslauf_core import SELLER
from ..audit import AuditWriteError, write_audit
from ..models import DunningDocument, Invoice
from .formatting import euro
from .workflow_errors import WorkflowError
from .invoice_files import quarantiniere_dateien
from . import pdf_chrome as chrome
from .pdf_chrome import HEADER_H, INK, MARGIN

# Neue Zahlungsfrist, die eine Mahnung/Erinnerung setzt.
DUNNING_GRACE_DAYS = 7
# Ab wie vielen Tagen Überfälligkeit eine Rechnung als Mahnkandidat gilt.
DEFAULT_DUNNING_THRESHOLD_DAYS = 14

# Rechnungen in diesen Status werden nie gemahnt.
# Inhaltlich unverändert, aber jetzt aus einer Quelle: Diese Datei war bisher
# die EINZIGE im Projekt, die alle vier Werte zusammen kannte – calc.py kannte
# nur 'storniert' und ließ sich deshalb den Belegzustand überschreiben (F-07).
_UNDUNNABLE_STATUS = Invoice.GESPERRT_FUER_MAHNUNG

_KIND_LABELS = {'reminder': 'Zahlungserinnerung', 'dunning': 'Mahnung'}
logger = logging.getLogger('sedo')


def _aktive_folgebelege():
    """Derselbe Belegbegriff wie active_storno/active_correction, nicht der Zahlstatus."""
    return Invoice.all_objects.filter(
        deleted_at__isnull=True, invoice_type__in=('384', '457'),
    ).exclude(lifecycle='draft')


def _aktuelle_rechnung(invoice: Invoice) -> Invoice | None:
    # Kein Vertrauen in ein altes Python-Objekt oder dessen Prefetch-Cache.
    if invoice.pk is None:
        return None
    return Invoice.all_objects.filter(pk=invoice.pk).first()


def _beleg_hindernis(invoice: Invoice | None) -> str:
    if invoice is None or invoice.deleted_at is not None:
        return 'Die Rechnung ist nicht mehr verfügbar und kann nicht gemahnt werden.'
    if invoice.invoice_type == '457':
        return f'{invoice.number} ist eine Stornorechnung und kann nicht gemahnt werden.'
    if invoice.lifecycle != 'issued':
        return (f'Rechnung {invoice.number} ({invoice.get_lifecycle_display()}) '
                'kann nicht gemahnt werden.')
    # Nicht die Rückwärtsrelation verwenden: deren Prefetch kann veraltet sein.
    folge = (_aktive_folgebelege().filter(reference_invoice_id=invoice.pk)
             .order_by('-invoice_type', 'pk').first())
    if folge is not None:
        art = 'Stornorechnung' if folge.invoice_type == '457' else 'Korrekturrechnung'
        return (f'Rechnung {invoice.number} wurde durch {art} {folge.number} ersetzt '
                'und kann nicht gemahnt werden.')
    return ''


def _forderungs_hindernis(invoice: Invoice, today=None) -> str:
    """F-68: Fälligkeit und echte Restforderung, auch bei manueller Stufenwahl.

    Der Fälligkeitstag ist noch Zahlungstag. Ohne Datum wird keine Frist
    erfunden; 0/negative Restbeträge sind unabhängig vom Status nicht mahnbar.
    """
    today = today if today is not None else timezone.localdate()
    if invoice.due_date is None:
        return f'Rechnung {invoice.number} hat kein Fälligkeitsdatum und kann nicht gemahnt werden.'
    if invoice.due_date >= today:
        return (f'Rechnung {invoice.number} ist noch nicht überfällig '
                'und kann nicht gemahnt werden.')
    if invoice.open_amount <= Decimal('0.00'):
        return (f'Rechnung {invoice.number} hat keinen positiven offenen Betrag '
                'und kann nicht gemahnt werden.')
    return ''


def mahn_hindernis(invoice: Invoice) -> str:
    """Aktueller Beleg-/Forderungssperrgrund; derselbe Vertrag wie im Generator."""
    current = _aktuelle_rechnung(invoice)
    return _beleg_hindernis(current) or _forderungs_hindernis(current)


def next_dunning_kind(invoice: Invoice, today=None) -> str | None:
    """Nächste Mahnstufe nach erneuter Belegprüfung, auch bei veralteter Python-Instanz."""
    current = _aktuelle_rechnung(invoice)
    if _beleg_hindernis(current) or _forderungs_hindernis(current, today):
        return None
    return _next_dunning_kind(current, today)


def _next_dunning_kind(invoice: Invoice, today=None) -> str | None:
    """Stufenwahl für einen bereits geprüften Beleg/Snapshot der Kandidatenabfrage.

    Keine Erinnerung -> Erinnerung; deren Frist abgelaufen -> Mahnung.
    Die Schreibfunktion prüft unabhängig davon unter einer Datenbanksperre.
    """
    docs = list(invoice.dunning_documents.all())
    if any(d.kind == 'dunning' for d in docs):
        return None
    reminders = [d for d in docs if d.kind == 'reminder']
    if not reminders:
        return 'reminder'
    last = max(reminders, key=lambda d: (d.document_date, d.id))
    today = today or timezone.localdate()
    if last.due_date_new and last.due_date_new < today:
        return 'dunning'
    return None  # Frist der Erinnerung läuft noch


def ensure_dunning_allowed(invoice: Invoice, kind: str) -> None:
    """Beleggraph, Restforderung, Fälligkeit und Duplikate frisch prüfen; noch keine Sperre."""
    if kind not in _KIND_LABELS:
        raise WorkflowError('Unbekannte Mahnstufe. Bitte Erinnerung oder Mahnung wählen.')
    invoice = _aktuelle_rechnung(invoice)
    hindernis = _beleg_hindernis(invoice) or _forderungs_hindernis(invoice)
    if hindernis:
        raise WorkflowError(hindernis)
    docs = list(invoice.dunning_documents.all())
    same = [d for d in docs if d.kind == kind]
    if same:
        last = max(same, key=lambda d: (d.document_date, d.id))
        raise WorkflowError(
            f'{_KIND_LABELS[kind]} zu {invoice.number} wurde bereits am '
            f'{last.document_date.strftime("%d.%m.%Y")} erzeugt.')
    if kind == 'reminder' and any(d.kind == 'dunning' for d in docs):
        raise WorkflowError(
            f'Zu {invoice.number} existiert bereits eine Mahnung – '
            f'eine Zahlungserinnerung wäre ein Rückschritt in der Mahnstufe.')


def _mahndatei_ziel(invoice: Invoice, kind: str) -> Path:
    """Nur den eigenen Medienpfad verwenden, keine Symlinks/Traverse oder Altdatei ersetzen."""
    number = invoice.number
    if not number or number in ('.', '..') or any(c in number for c in ('/', '\\', '\x00')):
        raise WorkflowError('Ungültige Rechnungsnummer für ein Mahndokument.')
    root = Path(settings.MEDIA_ROOT).resolve()
    target = root / 'dunning' / number / f'{number}_{kind}.pdf'
    for part in (root / 'dunning', target.parent, target):
        if part.is_symlink():
            raise WorkflowError('Der Mahndateipfad enthält eine symbolische Verknüpfung.')
    if target.exists():
        raise WorkflowError('Die Mahndatei existiert bereits. Sie wird nicht überschrieben; '
                            'bitte den vorhandenen Beleg prüfen.')
    return target


def _render_dunning_pdf(invoice, kind, open_amount, new_due, pdf_path):
    """Bestehende PDF-Gestaltung; nur der Zielpfad liegt jetzt im Temp-Verzeichnis."""
    label = _KIND_LABELS[kind]
    c = canvas.Canvas(str(pdf_path), pagesize=A4)
    w, h = A4
    left, right = MARGIN, w - MARGIN
    chrome.draw_header(c)
    sender_y = h - HEADER_H - 4 * mm
    chrome.draw_sender_line(c, sender_y)

    # Empfänger links
    ry = sender_y - 8 * mm
    c.setFillColorRGB(*INK)
    c.setFont('Helvetica-Bold', 10)
    c.drawString(left, ry, invoice.customer.name)
    c.setFont('Helvetica', 10)
    for line in [invoice.customer.name2, invoice.customer.street,
                 f'{invoice.customer.postcode} {invoice.customer.city}'.strip()]:
        if (line or '').strip():
            ry -= 5 * mm
            c.drawString(left, ry, line)

    # Eckdaten rechts (ohne Box)
    info_rows = [
        ('Datum', timezone.localdate().strftime('%d.%m.%Y')),
        ('Rechnung', invoice.number),
        ('Fällig war', invoice.due_date.strftime('%d.%m.%Y') if invoice.due_date else '—'),
        ('Neue Frist', new_due.strftime('%d.%m.%Y')),
        ('Offener Betrag', euro(open_amount)),
    ]
    iy = chrome.draw_info_block(c, right, sender_y - 2 * mm, info_rows)

    # Titel + Fließtext
    y = min(ry, iy) - 12 * mm
    chrome.draw_title(c, y, label)
    y -= 10 * mm
    issue = invoice.issue_date.strftime('%d.%m.%Y')
    if kind == 'reminder':
        intro = (f'zu unserer Rechnung {invoice.number} vom {issue} konnten wir noch keinen '
                 f'vollständigen Zahlungseingang feststellen.')
    else:
        intro = (f'trotz Fälligkeit ist unsere Rechnung {invoice.number} vom {issue} noch nicht '
                 f'vollständig ausgeglichen.')
    c.setFillColorRGB(*INK)
    c.setFont('Helvetica', 10)
    c.drawString(left, y, 'Sehr geehrte Damen und Herren,')
    y -= 7 * mm
    paragraphs = [
        intro,
        (f'Bitte überweisen Sie den offenen Betrag von {euro(open_amount)} bis spätestens '
         f'{new_due.strftime("%d.%m.%Y")} unter Angabe der Rechnungsnummer {invoice.number}.'),
        'Sollten Sie den Betrag bereits überwiesen haben, betrachten Sie dieses Schreiben bitte als gegenstandslos.',
    ]
    for part in paragraphs:
        for line in simpleSplit(part, 'Helvetica', 10, right - left):
            c.drawString(left, y, line)
            y -= 5 * mm
        y -= 2.5 * mm
    y -= 3 * mm
    c.drawString(left, y, 'Mit freundlichen Grüßen')
    y -= 5 * mm
    c.setFont('Helvetica-Bold', 10)
    c.drawString(left, y, SELLER['name'])

    chrome.draw_footer(c)
    c.save()


def generate_dunning_document(invoice: Invoice, kind: str = 'reminder', *, request=None) -> DunningDocument:
    """Verbindlicher Mahnwächter: sperren, frisch prüfen, PDF/Status/Pflichtaudit erzeugen.

    Das erste SQL innerhalb von atomic ist ein UPDATE ohne Wertänderung. So wird
    auf SQLite vor dem Lesen serialisiert (SELECT FOR UPDATE wäre wirkungslos).
    Alle Stufen prüfen Fälligkeit und Restbetrag innerhalb der Sperre. Die
    manuelle Stufenwahl bleibt erhalten; 'auto' beachtet zusätzlich Schwelle und
    Erinnerungsfrist. Ein vorheriger UI-/Service-Check reicht nicht.
    """
    if kind not in (*_KIND_LABELS, 'auto'):
        raise WorkflowError('Unbekannte Mahnstufe. Es wurde kein Mahndokument erzeugt.')
    target = identity = None
    try:
        try:
            with transaction.atomic():
                if not Invoice.objects.filter(pk=invoice.pk).update(updated_at=F('updated_at')):
                    raise WorkflowError('Die Rechnung ist nicht mehr verfügbar.')
                current = Invoice.objects.select_related('customer').get(pk=invoice.pk)
                use_kind = kind
                if kind == 'auto':
                    cutoff = timezone.localdate() - timedelta(days=DEFAULT_DUNNING_THRESHOLD_DAYS)
                    if current.due_date is None or current.due_date > cutoff or current.open_amount <= 0:
                        raise WorkflowError('Für diese Rechnung ist derzeit kein automatischer Mahnlauf fällig.')
                    use_kind = _next_dunning_kind(current)
                    if use_kind is None:
                        raise WorkflowError('Für diese Rechnung ist keine nächste Mahnstufe fällig.')
                ensure_dunning_allowed(current, use_kind)
                target = _mahndatei_ziel(current, use_kind)
                paid = (current.payments.filter(deleted_at__isnull=True)
                        .aggregate(s=Sum('amount'))['s'] or Decimal('0.00'))
                open_amount = (current.gross_total - paid).quantize(Decimal('0.01'))
                new_due = timezone.localdate() + timedelta(days=DUNNING_GRACE_DAYS)
                before = current.status
                target.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(dir=Path(settings.MEDIA_ROOT), prefix='.dunning_') as tmp:
                    temp_pdf = Path(tmp) / target.name
                    _render_dunning_pdf(current, use_kind, open_amount, new_due, temp_pdf)
                    doc = DunningDocument.objects.create(
                        invoice=current, kind=use_kind, document_date=timezone.localdate(),
                        due_date_new=new_due, amount_open=open_amount,
                        pdf_file=str(target.relative_to(Path(settings.MEDIA_ROOT).resolve())),
                    )
                    # Zusätzliche Schranke direkt am Status-UPDATE; niemals einen
                    # geschützten Zustand durch 'gemahnt' ersetzen.
                    changed_at = timezone.now()
                    updated = (Invoice.objects.filter(pk=current.pk, status=before, lifecycle='issued')
                               .exclude(invoice_type='457')
                               .filter(~Exists(_aktive_folgebelege().filter(reference_invoice_id=OuterRef('pk'))))
                               .update(status='gemahnt', updated_at=changed_at))
                    if not updated:
                        raise WorkflowError('Die Rechnung wurde inzwischen ersetzt, storniert oder geändert.')
                    write_audit(request, 'generate', doc,
                                f'{doc.get_kind_display()} zu {current.number} erzeugt', pflicht=True,
                                metadata={'von': before, 'nach': 'gemahnt', 'anlass': 'mahnung',
                                          'nummer': current.number, 'mahnstufe': use_kind})
                    # Exklusiv veröffentlichen. Eine zwischenzeitlich belegte Datei
                    # bleibt auch im Fehler-Rückweg fremd und wird nicht verschoben.
                    stat = temp_pdf.stat()
                    os.link(temp_pdf, target)
                    identity = (stat.st_dev, stat.st_ino)
            # Erst nach erfolgreichem Abschluss den Zustand des Aufruferobjekts ändern.
            invoice.status = 'gemahnt'
            invoice.updated_at = changed_at
            doc.invoice = invoice
            return doc
        except Exception:
            if identity is not None and target is not None and target.is_file():
                stat = target.stat()
                if identity == (stat.st_dev, stat.st_ino):
                    quarantiniere_dateien([target])
            raise
    except IntegrityError as exc:
        raise WorkflowError('Diese Mahnstufe wurde soeben bereits erzeugt (paralleler Vorgang).') from exc
    except OperationalError as exc:
        if 'locked' in str(exc).lower() or 'busy' in str(exc).lower():
            raise WorkflowError('Die Rechnung wird gerade bearbeitet. Bitte den Mahnvorgang erneut prüfen.') from exc
        raise
    except (OSError, AuditWriteError) as exc:
        logger.exception('Mahndokument wegen Datei-/Protokollfehler zurückgerollt')
        raise WorkflowError('Das Mahndokument wurde nicht erzeugt: PDF oder Protokoll konnte nicht '
                            'gespeichert werden. Bitte erneut versuchen.') from exc


def get_dunning_candidates(days_overdue_threshold: int = DEFAULT_DUNNING_THRESHOLD_DAYS):
    """Überfällige Rechnungen mit ihrer jeweils nächsten fälligen Mahnstufe.

    Gibt eine Liste von Rechnungen zurück; jede trägt ``next_kind``
    ('reminder'/'dunning') und ``next_kind_label`` für die Anzeige.
    """
    today = timezone.localdate()
    cutoff = today - timedelta(days=days_overdue_threshold)
    queryset = (
        Invoice.objects
        .filter(lifecycle='issued')
        .exclude(invoice_type='457')
        .filter(~Exists(_aktive_folgebelege().filter(reference_invoice_id=OuterRef('pk'))))
        .filter(due_date__lte=cutoff, due_date__lt=today)
        .select_related('customer', 'obj')
        .prefetch_related('dunning_documents', 'payments')
        .order_by('due_date')
    )
    candidates = []
    for inv in queryset:
        kind = _next_dunning_kind(inv, today)
        if not kind:
            continue
        if inv.open_amount <= Decimal('0.00'):
            continue  # bereits ausgeglichen, nur der Status hinkt nach
        inv.next_kind = kind
        inv.next_kind_label = _KIND_LABELS[kind]
        candidates.append(inv)
    return candidates


def send_bulk_dunning(invoice_ids: list, kind: str = 'reminder', *, request=None) -> dict:
    """Pro Rechnung neu und gesperrt prüfen, auch bei alten Auswahlen; kein Mailversand.

    'auto' entscheidet erst im Generator innerhalb der Schreibsperre. Ein
    gesperrter oder fehlgeschlagener Beleg verhindert nicht die übrigen Belege.
    Der Rückgabevertrag {'created': n, 'skipped': n} bleibt unverändert.
    """
    if kind not in (*_KIND_LABELS, 'auto'):
        raise WorkflowError('Unbekannte Mahnstufe. Der Mahnlauf wurde nicht gestartet.')
    created = skipped = 0
    for pk in invoice_ids:
        try:
            inv = Invoice.objects.filter(pk=pk).first()
        except (TypeError, ValueError, OverflowError):
            inv = None
        if inv is None:
            skipped += 1
            continue
        try:
            generate_dunning_document(inv, kind, request=request)
        except WorkflowError:
            skipped += 1
            continue
        created += 1
    return {'created': created, 'skipped': skipped}
