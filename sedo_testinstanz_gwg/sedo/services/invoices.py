"""Rechnungslogik: Kern-Mapping, Datei-Erzeugung, Storno, Angebot→Rechnung."""
from __future__ import annotations

import os
import tempfile
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from ..core.rechnungslauf_core import (
    InvoicePosition, InvoiceRow, build_xml, create_pdf, embed_xml_into_pdf,
)
from ..models import AareonMapping, AppSetting, Invoice, InvoiceItem, Offer
from .calc import recalc_invoice
from .numbering import create_numbered_invoice

# Rechnungsart-Code → Titel (XML-TypeCode + PDF-Titel/Texte).
DOC_TITLES = {'380': 'Rechnung', '384': 'Korrekturrechnung', '457': 'Stornorechnung'}
_TRUE_VALUES = ('1', 'true', 'True', 'ja', 'JA', 'yes')


class WorkflowError(ValueError):
    """Fachliche Sperre im Rechnungsworkflow.

    Wird von den Views abgefangen und dem Nutzer als verständliche
    Fehlermeldung angezeigt – es entsteht kein Datensatz.
    """


def ensure_storno_allowed(original: Invoice) -> None:
    """Blockiert Stornos, die fachlich nicht zulässig sind (GoBD-Workflow)."""
    if original.invoice_type == '457':
        raise WorkflowError(
            f'{original.number} ist selbst eine Stornorechnung und kann nicht erneut storniert werden.')
    if original.status == 'storniert':
        raise WorkflowError(f'Rechnung {original.number} ist bereits storniert.')
    existing = original.active_storno
    if existing:
        raise WorkflowError(
            f'Zu Rechnung {original.number} existiert bereits die Stornorechnung {existing.number}.')
    if original.status == 'korrigiert':
        correction = original.active_correction
        hint = f' Bitte ggf. die Korrekturrechnung {correction.number} stornieren.' if correction else ''
        raise WorkflowError(f'Rechnung {original.number} wurde bereits korrigiert.{hint}')


def ensure_correction_allowed(original: Invoice) -> None:
    """Blockiert Korrekturen, die fachlich nicht zulässig sind."""
    if original.invoice_type == '457':
        raise WorkflowError(f'{original.number} ist eine Stornorechnung und kann nicht korrigiert werden.')
    if original.status == 'storniert':
        raise WorkflowError(f'Rechnung {original.number} ist storniert und kann nicht mehr korrigiert werden.')
    existing = original.active_correction
    if existing:
        raise WorkflowError(
            f'Zu Rechnung {original.number} existiert bereits die Korrekturrechnung {existing.number}.')
    if original.status == 'korrigiert':
        raise WorkflowError(f'Rechnung {original.number} ist bereits als korrigiert markiert.')


def mark_invoice_corrected(original: Invoice) -> None:
    """Markiert die Ursprungsrechnung nach erfolgreicher Korrektur.

    Guarded UPDATE: Greift nur, wenn das Original noch nicht korrigiert oder
    storniert ist – zwei parallele Korrektur-Requests können den ensure-Guard
    beide passieren, aber nur einer gewinnt dieses Update. Der Verlierer
    bekommt einen WorkflowError und seine Transaktion rollt zurück.
    """
    updated = Invoice.objects.filter(pk=original.pk).exclude(
        status__in=('korrigiert', 'storniert'),
    ).update(status='korrigiert', updated_at=timezone.now())
    if not updated:
        raise WorkflowError(
            f'Rechnung {original.number} wurde parallel bereits korrigiert oder storniert.')
    original.refresh_from_db(fields=['status'])


def _is_ars_source(source: str) -> bool:
    """Erkennt Aareon/ARS/GWG-Rechnungen anhand der Quelle."""
    label = (source or '').lower().strip()
    return (
        label.startswith(('aareon', 'ars', 'gwg'))
        or 'gwg' in label or 'aareon' in label or 'ars' in label
    )


def invoice_to_core_row(invoice: Invoice) -> InvoiceRow:
    """Wandelt eine DB-Rechnung in die Kern-``InvoiceRow`` (XML/PDF)."""
    customer = invoice.customer
    obj = invoice.obj
    is_ars = _is_ars_source(invoice.source)

    positions = [
        InvoicePosition(
            description=item.description,
            long_text=item.long_text,
            quantity=item.quantity,
            unit=item.unit,
            unit_price=item.unit_price,
            article_no=item.article_no,
            leistungsart_id=item.leistungsart_id,
            leistungsart_text=item.leistungsart_text,
        )
        for item in invoice.items.all()
    ]
    object_name = obj.name if obj else ''

    # Aareon/GWG: ShipToTradeParty/ID kommt ausschließlich aus Spalte A der
    # Mappingdatei ("Rechnungssteller Abrechnungseinheit"), nicht aus der internen
    # GWG-Spalte "Abrechnungseinheit". Objektname/Adresse bleiben Name/Adresse.
    mapping_we = mapping_ae = mapping_nksl = ''
    if is_ars and object_name:
        first_item = invoice.items.order_by('position').first()
        qs = AareonMapping.objects.filter(billing_unit_sender=object_name)
        exact = qs.filter(service_id=first_item.leistungsart_id).first() if (first_item and first_item.leistungsart_id) else None
        mapping = exact or qs.first()
        if mapping:
            mapping_we = mapping.we_internal
            mapping_ae = mapping.billing_unit_sender
            mapping_nksl = mapping.nksl_internal

    seller_id = AppSetting.get('aareon_seller_id', '') if is_ars else ''
    include_flag = AppSetting.get('aareon_include_seller_assigned_id', '0') in _TRUE_VALUES
    # Bei ARS-Rechnungen nur senden, wenn ausdrücklich aktiviert; sonst immer.
    include_seller_assigned_id = (not is_ars) or include_flag

    buyer_identifier = customer.customer_no or str(customer.id)
    if is_ars and getattr(customer, 'aareon_receiver_id', ''):
        buyer_identifier = customer.aareon_receiver_id

    row = InvoiceRow(
        invoice_no=invoice.number,
        object_name=object_name,
        we=mapping_we, ae=mapping_ae, nksl=mapping_nksl,
        service=positions[0].description if positions else 'Leistung',
        net=invoice.net_total, vat_rate=invoice.vat_rate, positions=positions,
        object_street=obj.street if obj else '', object_postcode=obj.postcode if obj else '',
        object_city=obj.city if obj else '', object_country=obj.country if obj else 'DE',
        sap_mapping=is_ars, buyer_id=buyer_identifier, buyer_name=customer.name, buyer_name2=customer.name2,
        buyer_street=customer.street, buyer_postcode=customer.postcode, buyer_city=customer.city,
        buyer_country=customer.country, one_time=customer.customer_type == 'einmalig',
        ars_mapping=is_ars, seller_id=seller_id, include_seller_assigned_id=include_seller_assigned_id,
        document_type_code=invoice.invoice_type,
        document_title=DOC_TITLES.get(invoice.invoice_type, 'Rechnung'),
        is_draft=invoice.status == 'entwurf',
        reference_invoice_no=invoice.reference_invoice.number if invoice.reference_invoice_id else '',
    )
    return row


def generate_invoice_files(invoice: Invoice) -> Invoice:
    """Erzeugt PDF, XML und ZUGFeRD-PDF für eine Rechnung und verknüpft sie.

    Datei-atomar: Alle drei Dateien entstehen zuerst vollständig in einem
    Temp-Verzeichnis und werden erst nach Erfolg per ``os.replace`` in den
    Zielordner übernommen. Scheitert ein Schritt (z. B. die ZUGFeRD-Einbettung),
    bleiben im ``generated/``-Ordner keine halben Dateien zurück.
    """
    invoice = recalc_invoice(invoice)
    invoice.save()
    media_root = Path(settings.MEDIA_ROOT)
    media_root.mkdir(parents=True, exist_ok=True)
    out_dir = media_root / 'generated' / invoice.number
    pdf_path = out_dir / f'{invoice.number}.pdf'
    xml_path = out_dir / 'factur-x.xml'
    zf_path = out_dir / f'{invoice.number}_zugferd.pdf'
    due = invoice.due_date or (invoice.issue_date + timedelta(days=invoice.customer.payment_days))
    row = invoice_to_core_row(invoice)

    def _at_midnight(day):
        return timezone.datetime.combine(day, timezone.datetime.min.time()) if day else None

    issue_dt = _at_midnight(invoice.issue_date)
    due_dt = _at_midnight(due)
    ps_dt = _at_midnight(invoice.period_start)
    pe_dt = _at_midnight(invoice.period_end)

    # Temp-Verzeichnis auf demselben Dateisystem wie media/ – nur dann ist
    # os.replace ein atomares Rename statt einer Kopie.
    with tempfile.TemporaryDirectory(dir=media_root, prefix='.generating_') as tmp:
        tmp_dir = Path(tmp)
        tmp_pdf = tmp_dir / pdf_path.name
        tmp_xml = tmp_dir / xml_path.name
        tmp_zf = tmp_dir / zf_path.name
        create_pdf(row, tmp_pdf, issue_dt, ps_dt, pe_dt, due_dt)
        xml_bytes = build_xml(row, issue_dt, ps_dt, pe_dt, due_dt)
        tmp_xml.write_bytes(xml_bytes)
        embed_xml_into_pdf(tmp_pdf, xml_bytes, tmp_zf)
        # Erst jetzt – alles vollständig erzeugt – in den Zielordner übernehmen.
        out_dir.mkdir(parents=True, exist_ok=True)
        os.replace(tmp_pdf, pdf_path)
        os.replace(tmp_xml, xml_path)
        os.replace(tmp_zf, zf_path)
    invoice.pdf_file.name = str(pdf_path.relative_to(settings.MEDIA_ROOT))
    invoice.xml_file.name = str(xml_path.relative_to(settings.MEDIA_ROOT))
    invoice.zugferd_file.name = str(zf_path.relative_to(settings.MEDIA_ROOT))
    invoice.save(update_fields=['pdf_file', 'xml_file', 'zugferd_file',
                                'net_total', 'vat_amount', 'gross_total', 'updated_at'])
    return invoice


def create_storno(original: Invoice) -> Invoice:
    """Erzeugt eine Stornorechnung (TypeCode 457) zur Ursprungsrechnung.

    Atomar: Schlägt ein Schritt fehl (auch die PDF-/XML-Erzeugung), wird alles
    zurückgerollt – die Ursprungsrechnung bleibt dann unverändert.
    Blockiert Doppel-Stornos: pro Rechnung ist nur EIN aktives Storno zulässig,
    stornierte/korrigierte Rechnungen und Stornos selbst sind gesperrt.
    """
    ensure_storno_allowed(original)
    try:
        with transaction.atomic():
            return _create_storno_inner(original)
    except IntegrityError as exc:
        # Zwei parallele Requests können den ensure-Guard beide passieren –
        # der Unique-Constraint (max. EIN aktives Storno je Original) stoppt
        # den zweiten auf DB-Ebene; hier in eine Nutzermeldung übersetzen.
        # SQLite meldet die SPALTE (reference_invoice_id), nicht den
        # Constraint-Namen – in diesem Kontext ist das eindeutig das Storno.
        if 'reference_invoice_id' in str(exc) or 'invoice_unique_active_storno' in str(exc):
            raise WorkflowError(
                f'Zu Rechnung {original.number} wurde soeben bereits eine '
                f'Stornorechnung erzeugt (paralleler Vorgang).') from exc
        raise


def _create_storno_inner(original: Invoice) -> Invoice:
    storno = create_numbered_invoice(
        invoice_type='457',
        customer=original.customer,
        obj=original.obj,
        issue_date=timezone.localdate(),
        due_date=timezone.localdate(),
        vat_rate=original.vat_rate,
        status='erstellt',
        source='Storno',
        reference_invoice=original,
        note=f'Stornorechnung zu {original.number}',
    )
    for item in original.items.all():
        InvoiceItem.objects.create(
            invoice=storno,
            position=item.position,
            description=f'Storno {item.description}',
            long_text=item.long_text,
            quantity=-item.quantity,
            unit=item.unit,
            unit_price=item.unit_price,
            article_no=item.article_no,
            leistungsart_id=item.leistungsart_id,
            leistungsart_text=item.leistungsart_text,
        )
    generate_invoice_files(storno)
    original.status = 'storniert'
    original.save(update_fields=['status', 'updated_at'])
    return storno


def ensure_offer_invoicing_allowed(offer: Offer) -> None:
    """Blockiert die erneute Rechnungsstellung aus einem Angebot (idempotent).

    Die UI versteckt den Button zwar, aber der Server muss selbst prüfen –
    sonst erzeugt jeder weitere POST eine zusätzliche Rechnung.
    """
    if offer.linked_invoice_id:
        linked = offer.linked_invoice
        number = linked.number if linked else '(ausgeblendet)'
        raise WorkflowError(
            f'Aus Angebot {offer.number} wurde bereits Rechnung {number} erzeugt.')
    if offer.status == 'in_rechnung':
        raise WorkflowError(f'Angebot {offer.number} ist bereits in Rechnung gestellt.')


def create_invoice_from_offer(offer: Offer) -> Invoice:
    """Erzeugt eine Rechnung aus einem angenommenen Angebot (atomar, idempotent)."""
    ensure_offer_invoicing_allowed(offer)
    with transaction.atomic():
        return _create_invoice_from_offer_inner(offer)


def _create_invoice_from_offer_inner(offer: Offer) -> Invoice:
    inv = create_numbered_invoice(
        invoice_type='380',
        customer=offer.customer,
        obj=offer.obj,
        issue_date=timezone.localdate(),
        due_date=timezone.localdate() + timedelta(days=offer.customer.payment_days),
        vat_rate=offer.vat_rate,
        status='erstellt',
        source='Angebot',
        note=f'Diese Rechnung beruht auf Angebot {offer.number}.',
    )
    for item in offer.items.all():
        InvoiceItem.objects.create(
            invoice=inv,
            position=item.position,
            description=item.description,
            long_text=item.long_text,
            quantity=item.quantity,
            unit=item.unit,
            unit_price=item.unit_price,
            article_no=item.article_no,
        )
    generate_invoice_files(inv)
    # Guarded UPDATE statt save(): Nur wenn das Angebot noch nicht verrechnet
    # ist, wird verknüpft. Verliert dieser Request das Rennen gegen einen
    # parallelen, rollt die gesamte Transaktion (inkl. Rechnung + Nummer) zurück.
    updated = Offer.objects.filter(
        pk=offer.pk, linked_invoice__isnull=True,
    ).exclude(status='in_rechnung').update(
        status='in_rechnung', linked_invoice=inv, updated_at=timezone.now(),
    )
    if not updated:
        raise WorkflowError(
            f'Aus Angebot {offer.number} wurde soeben bereits eine Rechnung '
            f'erzeugt (paralleler Vorgang).')
    offer.refresh_from_db(fields=['status', 'linked_invoice'])
    return inv
