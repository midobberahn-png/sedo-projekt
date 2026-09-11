"""Rechnungslogik: Kern-Mapping, Datei-Erzeugung, Storno, Angebot→Rechnung."""
from __future__ import annotations

import os
import tempfile
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from ..core.rechnungslauf_core import (
    InvoicePosition, InvoiceRow, build_xml, create_pdf, embed_xml_into_pdf,
)
from ..audit import write_audit
from ..models import AareonMapping, AppSetting, Invoice, InvoiceItem, Offer
from .calc import recalc_invoice
from .numbering import create_numbered_invoice, save_with_unique_number
from .invoice_files import (datei_rueckweg, quarantiniere_dateien,
                            merke_veroeffentlichung, veroeffentliche_neue_datei)
from .workflow_errors import WorkflowError, BelegArchivFehler
from .invoice_state import guarded_lifecycle_update
from .invoice_archive import (ARCHIV_FELDER, archivnachweis, pruefe_archiv,
                              pruefe_schreibziele, versiegele_archiv, zielpfade)

# Rechnungsart-Code → Titel (XML-TypeCode + PDF-Titel/Texte).
DOC_TITLES = {'380': 'Rechnung', '384': 'Korrekturrechnung', '457': 'Stornorechnung'}
_TRUE_VALUES = ('1', 'true', 'True', 'ja', 'JA', 'yes')


def ensure_storno_allowed(original: Invoice) -> None:
    """Blockiert Stornos, die fachlich nicht zulässig sind (GoBD-Workflow).

    Die Prüfungen hängen bewusst nicht nur am Statusfeld: Vor Etappe 1 konnte
    ein einziger Blick auf „Offene Posten" ein korrigiertes Original auf
    'ueberfaellig' setzen – danach griff der Status-Guard nicht mehr, und der
    Kunde bekam Korrektur UND Storno (F-07). Die Kreuzprüfung auf den
    tatsächlichen Folgebeleg hält auch dann, wenn das Feld lügt – wichtig für
    Bestandsbelege, die diesen Schaden schon haben.
    """
    if original.invoice_type == '457':
        raise WorkflowError(
            f'{original.number} ist selbst eine Stornorechnung und kann nicht erneut storniert werden.')
    if original.lifecycle == 'draft':
        raise WorkflowError(
            f'{original.number} ist ein Entwurf und wurde nie ausgestellt. '
            'Bitte zuerst finalisieren oder verwerfen.')
    existing = original.active_storno
    if existing:
        raise WorkflowError(
            f'Zu Rechnung {original.number} existiert bereits die Stornorechnung {existing.number}.')
    if original.lifecycle == 'cancelled':
        raise WorkflowError(f'Rechnung {original.number} ist bereits storniert.')
    # Kreuzprüfung am Belegobjekt statt am Statusfeld.
    correction = original.active_correction
    if correction:
        raise WorkflowError(
            f'Zu Rechnung {original.number} existiert bereits die Korrekturrechnung '
            f'{correction.number}. Bitte ggf. diese stornieren.')
    if original.lifecycle == 'corrected':
        raise WorkflowError(f'Rechnung {original.number} wurde bereits korrigiert.')


def ensure_correction_allowed(original: Invoice) -> None:
    """Blockiert Korrekturen, die fachlich nicht zulässig sind.

    Spiegelbildlich zu ``ensure_storno_allowed`` – siehe dortige Begründung
    zur Kreuzprüfung.
    """
    if original.invoice_type == '457':
        raise WorkflowError(f'{original.number} ist eine Stornorechnung und kann nicht korrigiert werden.')
    if original.lifecycle == 'draft':
        raise WorkflowError(
            f'{original.number} ist ein Entwurf und wurde nie ausgestellt. '
            'Bitte zuerst finalisieren oder direkt bearbeiten.')
    # Der konkrete Folgebeleg ist informativer als nur der lifecycle-Cache.
    storno = original.active_storno
    if storno:
        raise WorkflowError(
            f'Zu Rechnung {original.number} existiert bereits die Stornorechnung '
            f'{storno.number}. Eine stornierte Rechnung wird nicht mehr korrigiert.')
    if original.lifecycle == 'cancelled':
        raise WorkflowError(f'Rechnung {original.number} ist storniert und kann nicht mehr korrigiert werden.')
    existing = original.active_correction
    if existing:
        raise WorkflowError(
            f'Zu Rechnung {original.number} existiert bereits die Korrekturrechnung {existing.number}.')
    if original.lifecycle == 'corrected':
        raise WorkflowError(f'Rechnung {original.number} ist bereits als korrigiert markiert.')


def ensure_zahlung_erlaubt(invoice: Invoice) -> None:
    """Blockiert Zahlungen auf Belege, die keine Forderung mehr darstellen.

    Eine Zahlung auf ein korrigiertes Original setzte dessen Status auf
    'bezahlt' oder 'teilbezahlt' und hob damit den Belegzustand auf (F-07).
    Die Meldung nennt den Beleg, auf den die Zahlung stattdessen gehört.
    """
    if invoice.lifecycle == 'draft':
        raise WorkflowError(
            f'{invoice.number} ist ein Entwurf. Auf einen Entwurf kann keine Zahlung gebucht '
            'werden – bitte zuerst finalisieren.')
    # Ein Stornobeleg fordert nichts, er hebt auf: sein Bruttobetrag ist negativ.
    # Eine Zahlung darauf ist immer ein Irrtum – geprüft wird die Belegart, nicht
    # der Status, denn ein Stornobeleg trägt selbst 'erstellt'.
    if invoice.invoice_type == '457':
        raise WorkflowError(
            f'{invoice.number} ist eine Stornorechnung und nimmt keine Zahlung auf. '
            'Zahlungen gehören auf die zugehörige Rechnung.')
    if invoice.lifecycle == 'corrected' or invoice.active_correction:
        korrektur = invoice.active_correction
        ziel = f' Bitte auf {korrektur.number} buchen.' if korrektur else ''
        raise WorkflowError(
            f'Rechnung {invoice.number} wurde durch eine Korrektur ersetzt.{ziel}')
    if invoice.lifecycle == 'cancelled' or invoice.active_storno:
        storno = invoice.active_storno
        hinweis = f' (Stornorechnung {storno.number})' if storno else ''
        raise WorkflowError(
            f'Rechnung {invoice.number} ist storniert{hinweis} und nimmt keine Zahlung mehr auf.')


def ensure_versand_erlaubt(invoice: Invoice) -> None:
    """Blockiert nur den Versand von Entwürfen.

    Korrigierte und stornierte Belege bleiben versendbar: „Schicken Sie mir
    das nochmal" ist ein normaler Kundenwunsch, und der Versand ändert ab
    Etappe 3 keinen Status mehr, kann also nichts mehr kaputtmachen.
    """
    if invoice.lifecycle == 'draft':
        raise WorkflowError(
            f'{invoice.number} ist ein Entwurf und darf nicht versendet werden. '
            'Bitte zuerst finalisieren.')


def versand_nachweis(invoice: Invoice) -> bool:
    """Gibt es einen Beleg dafür, dass die Rechnung das Haus verlassen hat?

    Bewusst NICHT nur über den Statuswert: ``refresh_invoice_payment_status``
    überschreibt 'versendet' mit 'ueberfaellig', sobald die Fälligkeit
    verstrichen ist. Im Bestand steht 'versendet' deshalb fast nirgends mehr –
    wer den Status als Beweis nimmt, hält versendete Rechnungen für
    unversendet und lässt sie ausblenden.
    """
    from django.conf import settings
    from django.db.models import Q

    from ..models import AuditLog

    if invoice.status == 'versendet':
        return True
    # .eml-Entwurf – wird beim Versandweg ohne SMTP geschrieben.
    eml_ordner = Path(settings.MEDIA_ROOT) / 'emails' / invoice.number
    if eml_ordner.exists() and any(eml_ordner.glob('*.eml')):
        return True
    # Protokolleinträge: strukturiert (neu) oder über den Text (Altbestand).
    eintraege = AuditLog.objects.filter(object_repr__icontains=invoice.number)
    if eintraege.filter(metadata__vorgang='versand').exists():
        return True
    return eintraege.filter(
        Q(message__icontains='versendet') | Q(message__icontains='E-Mail-Entwurf')).exists()


def ausblenden_hindernis(invoice: Invoice) -> str:
    """Grund, warum eine Rechnung nicht ausgeblendet werden darf – oder ''.

    Ausblenden bleibt für das erlaubt, was nachweislich nie das Haus verlassen
    hat (Entscheidung des Inhabers: mildes Ausblenden). Vorher stand der
    Löschen-Knopf bedingungslos bei jeder Rechnung, auch bei versendeten und
    bezahlten – der Beleg fehlte danach im Steuerberater-Export, während die
    Zahlung an einem unsichtbaren Datensatz hing (F-14).

    Rückgabe ist der Klartext für die Oberfläche, kein bloßes True/False: Für
    zwei Nutzer ohne Support ist der Grund die halbe Lösung.
    """
    # Ein Entwurf wurde nie ausgestellt – auch dann nicht, wenn er als Korrektur
    # oder Storno angelegt wurde. Er muss verworfen werden können, sonst sitzt
    # er als toter Folgebeleg fest und niemand kommt an ihn heran.
    if invoice.lifecycle != 'draft' and (invoice.invoice_type in ('384', '457')
                                        or invoice.reference_invoice_id):
        return (f'{invoice.number} ist ein Folgebeleg zu einer anderen Rechnung. '
                'Folgebelege werden nicht ausgeblendet – sonst bliebe das Original dauerhaft blockiert.')
    if invoice.lifecycle in ('cancelled', 'corrected'):
        return (f'Rechnung {invoice.number} hat bereits einen Folgebeleg und kann nicht '
                'ausgeblendet werden.')
    if invoice.active_storno or invoice.active_correction:
        folge = invoice.active_storno or invoice.active_correction
        return (f'Zu Rechnung {invoice.number} existiert der Folgebeleg {folge.number}. '
                'Bitte zuerst dort ansetzen.')
    if invoice.payments.filter(deleted_at__isnull=True).exists():
        return (f'Auf Rechnung {invoice.number} ist bereits eine Zahlung gebucht. '
                'Vorgesehen ist eine Stornorechnung.')
    if invoice.dunning_documents.exists():
        return (f'Zu Rechnung {invoice.number} wurde bereits gemahnt. '
                'Vorgesehen ist eine Stornorechnung.')
    if versand_nachweis(invoice):
        return (f'Rechnung {invoice.number} wurde bereits versendet und kann nicht ausgeblendet '
                'werden. Vorgesehen ist eine Stornorechnung.')
    return ''


def mark_invoice_corrected(original: Invoice, request=None) -> None:
    """Geschützter Belegzustandswechsel mit verpflichtendem Nachweis."""
    vorher_lifecycle = original.lifecycle
    vorher_status = original.status
    with transaction.atomic():
        guarded_lifecycle_update(original, expected='issued', target='corrected',
                                 legacy_status='korrigiert')
        write_audit(request, 'update', original,
                    f'Rechnung {original.number}: ausgestellt → korrigiert', pflicht=True,
                    metadata={'von': vorher_status, 'nach': 'korrigiert',
                              'von_lifecycle': vorher_lifecycle, 'nach_lifecycle': 'corrected',
                              'anlass': 'korrektur'})


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
        is_draft=invoice.lifecycle == 'draft',
        reference_invoice_no=invoice.reference_invoice.number if invoice.reference_invoice_id else '',
    )
    return row


def generate_invoice_files(invoice: Invoice, *, erstmalig=False, request=None) -> Invoice:
    """Entwurf aktualisieren oder unveränderte Originale wiederverwenden.

    `erstmalig` ist ausschließlich für interne Erstellungs-/Finalisierungswege,
    kein HTTP-Parameter und kein Reparaturmodus. Fehlende Originale sind auch
    ohne Dateiverweise KEINE Erlaubnis zur Neuerzeugung aus heutigen Stammdaten.
    """
    invoice.refresh_from_db()
    if invoice.deleted_at is not None:
        raise WorkflowError('Die Rechnung ist nicht mehr verfügbar.')
    if invoice.lifecycle != 'draft' and not erstmalig:
        report = pruefe_archiv(invoice)
        if report['fehler']:
            raise BelegArchivFehler(report['fehler'][0])
        return invoice  # F-18: kein Recalc, keine Datei-/DB-Schreiboperation.
    with datei_rueckweg() as merken, transaction.atomic():
        # SQLite: Schreibsperre ohne Änderung des Revisionswerts; nach dem
        # Warten DB-Zustand frisch lesen, nie der alten Python-Instanz vertrauen.
        if not Invoice.objects.filter(pk=invoice.pk).update(updated_at=F('updated_at')):
            raise WorkflowError('Die Rechnung ist nicht mehr verfügbar.')
        invoice.refresh_from_db()
        if invoice.lifecycle != 'draft' and not erstmalig:
            report = pruefe_archiv(invoice)
            if report['fehler']:
                raise BelegArchivFehler(report['fehler'][0])
            return invoice
        pruefe_schreibziele(invoice, erstmalig=erstmalig)
        merken(invoice)
        result = _generate_invoice_files_inner(invoice)
        if invoice.lifecycle != 'draft':
            versiegele_archiv(invoice, request=request)
        return result


def _generate_invoice_files_inner(invoice: Invoice) -> Invoice:
    recalc_invoice(invoice)
    media_root = Path(settings.MEDIA_ROOT)
    media_root.mkdir(parents=True, exist_ok=True)
    targets = zielpfade(invoice)
    pdf_path, xml_path, zf_path = (targets[f] for f in ARCHIV_FELDER)
    out_dir = pdf_path.parent
    due = invoice.due_date or (invoice.issue_date + timedelta(days=invoice.customer.payment_days))
    row = invoice_to_core_row(invoice)
    is_draft = invoice.lifecycle == 'draft'

    def _at_midnight(day):
        return timezone.datetime.combine(day, timezone.datetime.min.time()) if day else None

    issue_dt = _at_midnight(invoice.issue_date)
    due_dt = _at_midnight(due)
    ps_dt = _at_midnight(invoice.period_start)
    pe_dt = _at_midnight(invoice.period_end)

    with tempfile.TemporaryDirectory(dir=media_root, prefix='.generating_') as tmp:
        tmp_dir = Path(tmp)
        tmp_pdf = tmp_dir / pdf_path.name
        create_pdf(row, tmp_pdf, issue_dt, ps_dt, pe_dt, due_dt)
        if not is_draft:
            xml_bytes = build_xml(row, issue_dt, ps_dt, pe_dt, due_dt)
            tmp_xml = tmp_dir / xml_path.name
            tmp_zf = tmp_dir / zf_path.name
            tmp_xml.write_bytes(xml_bytes)
            embed_xml_into_pdf(tmp_pdf, xml_bytes, tmp_zf)
        out_dir.mkdir(parents=True, exist_ok=True)
        if is_draft:
            os.replace(tmp_pdf, pdf_path)
            merke_veroeffentlichung(pdf_path)
            # Nur bei diesem ausdrücklich bearbeiteten Entwurf: alte XMLs
            # nicht bloß aus der UI verstecken, sondern aus MEDIA_ROOT nehmen.
            alt = [xml_path, zf_path]
            alt += [Path(f.path) for f in (invoice.xml_file, invoice.zugferd_file) if f]
            quarantiniere_dateien(alt)
            invoice.xml_file.name = ''
            invoice.zugferd_file.name = ''
        else:
            veroeffentliche_neue_datei(tmp_pdf, pdf_path)
            veroeffentliche_neue_datei(tmp_xml, xml_path)
            veroeffentliche_neue_datei(tmp_zf, zf_path)
            invoice.xml_file.name = str(xml_path.relative_to(settings.MEDIA_ROOT))
            invoice.zugferd_file.name = str(zf_path.relative_to(settings.MEDIA_ROOT))
    invoice.pdf_file.name = str(pdf_path.relative_to(settings.MEDIA_ROOT))
    invoice.save(update_fields=['pdf_file', 'xml_file', 'zugferd_file',
                                'net_total', 'vat_amount', 'gross_total', 'updated_at'])
    return invoice


def entwurf_sperren(invoice: Invoice, *, revision=None, finalisieren=False) -> None:
    """Erstes SQL-Statement der Transaktion: Entwurf per guarded UPDATE sperren.

    SQLite ignoriert SELECT FOR UPDATE. Dieser Schreibzugriff serialisiert
    Bearbeiten/Finalisieren/Verwerfen; eine Formularrevision verhindert zudem
    das Überschreiben von Änderungen aus einem zweiten Browserfenster.
    """
    qs = Invoice.objects.filter(pk=invoice.pk, lifecycle='draft')
    if revision is not None:
        from django.utils.dateparse import parse_datetime
        try:
            zeitpunkt = parse_datetime(revision)
        except (ValueError, TypeError):
            zeitpunkt = None
        if zeitpunkt is None or timezone.is_naive(zeitpunkt):
            raise WorkflowError('Ungültiger Bearbeitungsstand. Bitte die Seite neu laden.')
        qs = qs.filter(updated_at=zeitpunkt)
    values = {'updated_at': timezone.now()}
    if finalisieren:
        values.update(lifecycle='issued', status='erstellt')
    if not qs.update(**values):
        raise WorkflowError('Der Entwurf wurde inzwischen geändert, finalisiert oder verworfen. '
                            'Bitte die Seite neu laden.')
    # Nicht nur status: eine ältere Python-Instanz darf keine veralteten
    # Kopfdaten, Nummern oder Dateiverweise zurückschreiben.
    invoice.refresh_from_db()


def finalisiere_entwurf(invoice: Invoice, request=None, *, datum_heute=False,
                       datum_bestaetigt=False, revision=None) -> Invoice:
    """Finalisieren ohne Mailversand; Nummer, Belegkette und Audit atomar."""
    try:
        with datei_rueckweg() as merken, transaction.atomic():
            entwurf_sperren(invoice, revision=revision, finalisieren=True)
            pruefe_schreibziele(invoice, erstmalig=False)
            heute = timezone.localdate()
            altes_datum = invoice.issue_date
            alte_nummer = invoice.number
            if (altes_datum.year, altes_datum.month) < (heute.year, heute.month):
                if not datum_bestaetigt:
                    raise WorkflowError('Das Rechnungsdatum liegt in einem Vormonat. '
                                        'Bitte das Datum vor dem Finalisieren ausdrücklich bestätigen.')
            if invoice.invoice_type not in ('380', '384'):
                raise WorkflowError('Dieser Entwurfstyp kann nicht als Rechnung finalisiert werden.')
            if not invoice.items.exists():
                raise WorkflowError('Der Entwurf benötigt mindestens eine Position.')
            if invoice.obj_id and invoice.obj.customer_id != invoice.customer_id:
                raise WorkflowError('Das gespeicherte Objekt gehört zu einem anderen Kunden.')
            original = None
            if invoice.invoice_type == '384':
                original = Invoice.objects.filter(pk=invoice.reference_invoice_id).first()
                if original is None:
                    raise WorkflowError('Die Ursprungsrechnung der Korrektur ist nicht mehr verfügbar.')
                # ensure_correction_allowed sieht nach dem Statuswechsel die
                # Korrektur selbst. Deshalb explizit nur FREMDE Folgebelege prüfen.
                if (original.lifecycle != 'issued'
                        or original.invoice_type == '457'
                        or original.followups.filter(deleted_at__isnull=True,
                                                     invoice_type__in=('384', '457'))
                        .exclude(pk=invoice.pk).exclude(lifecycle='draft').exists()):
                    raise WorkflowError('Die Ursprungsrechnung wurde inzwischen ersetzt oder storniert.')
            if datum_heute:
                tage = ((invoice.due_date - altes_datum).days if invoice.due_date
                        else invoice.customer.payment_days)
                if tage < 0:
                    raise WorkflowError('Ungültiges Zahlungsziel. Bitte den Entwurf zuerst bearbeiten.')
                invoice.issue_date = heute
                invoice.due_date = heute + timedelta(days=tage)
                invoice.save(update_fields=['issue_date', 'due_date', 'updated_at'])
            if invoice.number.startswith('EN'):
                save_with_unique_number(invoice, 'RE')
            if original is not None:
                mark_invoice_corrected(original, request=request)
            write_audit(request, 'update', invoice,
                        f'Entwurf {alte_nummer} als Rechnung {invoice.number} finalisiert',
                        pflicht=True, metadata={
                            'von': 'entwurf', 'nach': 'erstellt', 'anlass': 'finalisieren',
                            'alte_nummer': alte_nummer, 'neue_nummer': invoice.number,
                            'altes_rechnungsdatum': altes_datum.isoformat(),
                            'neues_rechnungsdatum': invoice.issue_date.isoformat(),
                        })
            merken(invoice)
            # EN-Vorschauen bleiben erhalten. Nur ein historischer RE-Entwurf
            # verwendet dieselben Zielpfade: seine Vorschau vorab aufbewahren.
            if not alte_nummer.startswith('EN'):
                quarantiniere_dateien(zielpfade(invoice).values())
            for field in ARCHIV_FELDER:
                getattr(invoice, field).name = ''
            invoice.save(update_fields=[*ARCHIV_FELDER, 'updated_at'])
            generate_invoice_files(invoice, erstmalig=True, request=request)
        return invoice
    except Exception:
        # Django setzt Python-Objektfelder beim Rollback nicht zurück.
        invoice.refresh_from_db()
        raise


def create_storno(original: Invoice, request=None) -> Invoice:
    """Erzeugt eine Stornorechnung (TypeCode 457) zur Ursprungsrechnung.

    Atomar: Schlägt ein Schritt fehl (auch die PDF-/XML-Erzeugung), wird alles
    zurückgerollt – die Ursprungsrechnung bleibt dann unverändert.
    Blockiert Doppel-Stornos: pro Rechnung ist nur EIN aktives Storno zulässig,
    stornierte/korrigierte Rechnungen und Stornos selbst sind gesperrt.
    """
    ensure_storno_allowed(original)
    try:
        with datei_rueckweg() as merken, transaction.atomic():
            return _create_storno_inner(original, request=request, merken=merken)
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


def _create_storno_inner(original: Invoice, request=None, merken=None) -> Invoice:
    storno = create_numbered_invoice(
        invoice_type='457',
        customer=original.customer,
        obj=original.obj,
        issue_date=timezone.localdate(),
        due_date=timezone.localdate(),
        vat_rate=original.vat_rate,
        status='erstellt',
        lifecycle='issued',
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
    vorher_lifecycle = original.lifecycle
    vorher_status = original.status
    guarded_lifecycle_update(original, expected='issued', target='cancelled',
                             legacy_status='storniert')
    write_audit(request, 'update', original,
                f'Rechnung {original.number}: ausgestellt → storniert ({storno.number})',
                pflicht=True, metadata={'von': vorher_status, 'nach': 'storniert',
                                        'von_lifecycle': vorher_lifecycle,
                                        'nach_lifecycle': 'cancelled', 'anlass': 'storno'})
    merken(storno)
    generate_invoice_files(storno, erstmalig=True, request=request)
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
    with datei_rueckweg() as merken, transaction.atomic():
        return _create_invoice_from_offer_inner(offer, merken)


def _create_invoice_from_offer_inner(offer: Offer, merken) -> Invoice:
    inv = create_numbered_invoice(
        invoice_type='380',
        customer=offer.customer,
        obj=offer.obj,
        issue_date=timezone.localdate(),
        due_date=timezone.localdate() + timedelta(days=offer.customer.payment_days),
        vat_rate=offer.vat_rate,
        status='erstellt',
        lifecycle='issued',
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
    merken(inv)
    generate_invoice_files(inv, erstmalig=True)
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
