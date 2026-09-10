"""Etappe 5: Belegketten lesen und Statusschäden nachvollziehbar reparieren.

Keine Nummernvergabe, Dateizugriffe, Belegerzeugung oder Löschung. Die
Reparatur ist für die SQLite-Einzelplatzinstanz bestimmt und als ganzer Lauf
atomar: Ohne jeden Pflicht-Audit wird KEINE Änderung dieses Laufs übernommen.
"""
from __future__ import annotations

from collections import defaultdict
from copy import copy
from datetime import timedelta
from uuid import uuid4

from django.db import connection, transaction
from django.db.models import F, Subquery
from django.utils import timezone

from ..audit import write_audit
from ..models import Invoice
from .calc import abgeleiteter_zahlstatus


class BelegkettenKonflikt(ValueError):
    """Die Reparatur passt nicht mehr zum geprüften Stand; alles zurückrollen."""


def _zahlstatus_nach_ruecksetzen(invoice: Invoice) -> str:
    """Wie refresh_invoice_payment_status, aber OHNE ungeschütztes save().

    Gruppe B geht fachlich über 'erstellt'. Ableitung und endgültiges UPDATE
    bleiben getrennt, damit genau der wirkliche Endzustand protokolliert wird.
    Ein Stornobeleg erhält auch bei negativem Brutto nie einen Zahlstatus.
    """
    probe = copy(invoice)
    probe.status = 'erstellt'
    probe.lifecycle = 'issued'
    if probe.invoice_type == '457':
        return 'erstellt'
    return abgeleiteter_zahlstatus(probe) or 'erstellt'


def _befund(invoice, gruppe, code, nach, hinweis, *, nach_lifecycle=None, storno=(), korrektur=(),
            bitte_pruefen=False, reparieren=True):
    """Nur skalare Belegangaben, keine Kunden-/Bankdaten oder Datei-Inhalte."""
    return {
        'gruppe': gruppe, 'code': code, 'invoice_id': invoice.pk,
        'nummer': invoice.number, 'von': invoice.status, 'nach': nach,
        'von_lifecycle': invoice.lifecycle, 'nach_lifecycle': nach_lifecycle,
        'revision': invoice.updated_at.isoformat(),
        'reparierbar': reparieren and (
            (nach is not None and nach != invoice.status)
            or (nach_lifecycle is not None and nach_lifecycle != invoice.lifecycle)
        ),
        'bitte_pruefen': bitte_pruefen, 'hinweis': hinweis,
        'storno_nummern': [i.number for i in storno],
        'korrektur_nummern': [i.number for i in korrektur],
    }


def _bericht(pruefzeit):
    # Eine gemeinsame Abfrage liefert Originale UND alle aktiven Folgebelege.
    # Kein 500er-Limit, kein Dateizugriff und kein N+1-Lesen des Beleggraphen.
    invoices = list(Invoice.objects.order_by('pk'))
    folgen = defaultdict(list)
    for folge in invoices:
        if folge.reference_invoice_id and folge.lifecycle != 'draft':
            folgen[folge.reference_invoice_id].append(folge)
    heute = timezone.localdate(pruefzeit)
    funde = []
    for invoice in invoices:
        kinder = folgen[invoice.pk]
        storno = tuple(i for i in kinder if i.invoice_type == '457')
        korrektur = tuple(i for i in kinder if i.invoice_type == '384')
        graph = {'storno': storno, 'korrektur': korrektur}
        nicht_ausgestellt = invoice.lifecycle == 'draft' or invoice.number.startswith('EN')
        if (storno or korrektur) and nicht_ausgestellt:
            funde.append(_befund(invoice, 'A', 'entwurf_mit_folgebeleg', None,
                'Entwurf/EN-Nummer mit ausgestelltem Folgebeleg: bitte manuell prüfen; '
                'die Reparatur darf keinen Entwurf ausstellen.', bitte_pruefen=True, **graph))
        elif storno:
            beide = bool(korrektur)
            if invoice.lifecycle != 'cancelled' or beide:
                funde.append(_befund(invoice, 'A', 'beide_folgebelege' if beide else 'storno_status_verloren',
                    'storniert', 'Storno hat Vorrang. Beide Folgebelege bitte prüfen.' if beide else
                    'Ausgestelltes, nicht ausgeblendetes Storno vorhanden.', nach_lifecycle='cancelled', bitte_pruefen=beide, **graph))
        elif korrektur:
            if invoice.lifecycle == 'cancelled':
                # Der Etappenplan erlaubt hier KEIN automatisches Entstornieren.
                funde.append(_befund(invoice, 'A', 'storniert_nur_mit_korrektur', None,
                    'Status storniert, aber nur eine Korrektur vorhanden: bitte manuell prüfen.',
                    bitte_pruefen=True, **graph))
            elif invoice.lifecycle != 'corrected':
                funde.append(_befund(invoice, 'A', 'korrektur_status_verloren', 'korrigiert',
                    'Ausgestellte, nicht ausgeblendete Korrektur vorhanden.', nach_lifecycle='corrected', **graph))
        elif invoice.lifecycle in ('cancelled', 'corrected'):
            funde.append(_befund(invoice, 'B', 'belegzustand_ohne_folgebeleg',
                None if nicht_ausgestellt else _zahlstatus_nach_ruecksetzen(invoice),
                'EN-Nummer mit widersprüchlichem Status: bitte manuell prüfen.' if nicht_ausgestellt else
                'Kein ausgestelltes Storno und keine ausgestellte Korrektur: Zahlstatus neu ableiten.',
                nach_lifecycle=None if nicht_ausgestellt else 'issued',
                bitte_pruefen=nicht_ausgestellt))
        elif invoice.invoice_type == '457' and invoice.status == 'bezahlt':
            funde.append(_befund(invoice, 'D', 'storno_faelschlich_bezahlt',
                None if nicht_ausgestellt else 'erstellt',
                'EN-Stornobeleg mit Zahlstatus: bitte manuell prüfen.' if nicht_ausgestellt else
                'Ein Stornobeleg erhält keinen Zahlstatus. Zahlungen selbst bleiben unverändert.',
                bitte_pruefen=nicht_ausgestellt))

        if invoice.lifecycle == 'draft':
            if invoice.xml_file.name or invoice.zugferd_file.name:
                funde.append(_befund(invoice, 'C', 'entwurf_mit_xml_verweis', None,
                    'XML-/ZUGFeRD-Dateiverweise vorhanden; physischer Inhalt nicht geprüft. '
                    'Über Bearbeiten/Neuerzeugen gezielt behandeln, keine automatische Dateireparatur.',
                    bitte_pruefen=True, reparieren=False))
            if timezone.localdate(invoice.created_at) < heute - timedelta(days=30):
                funde.append(_befund(invoice, 'D', 'entwurf_aelter_30_tage', None,
                    'Entwurf wurde vor mehr als 30 Kalendertagen angelegt; nur Hinweis, keine Änderung.',
                    reparieren=False))

    return {
        'funde': funde,
        'zusammenfassung': {
            'belege_geprueft': len(invoices), 'befunde': len(funde),
            'betroffene_belege': len({f['invoice_id'] for f in funde}),
            'gruppen': {g: sum(f['gruppe'] == g for f in funde) for g in 'ABCD'},
            'reparierbar': sum(f['reparierbar'] for f in funde),
            'kettenfehler': len({f['invoice_id'] for f in funde if f['gruppe'] in ('A', 'B')}),
            'manuell_pruefen': sum(f['bitte_pruefen'] for f in funde),
        },
    }


def _sperre_reparaturlauf():
    """SQLite-Schreibsperre VOR dem Lesen, ohne einen Feldwert zu verändern.

    select_for_update() ist auf SQLite wirkungslos. Das erste UPDATE nimmt
    die Schreibsperre; erst danach werden Beleggraph und Zahlungen gelesen.
    Auch bei leerer Tabelle beginnt die Schreibtransaktion. Kein stiller
    Rückfall auf ungesicherte Reparaturen bei einem anderen Datenbanktyp.
    """
    if connection.vendor != 'sqlite':
        raise BelegkettenKonflikt('Automatische Reparatur ist nur für SQLite freigegeben; '
                                 'der lesende Prüflauf bleibt verfügbar.')
    erster = Invoice.objects.order_by('pk').values('pk')[:1]
    Invoice.objects.filter(pk=Subquery(erster)).update(updated_at=F('updated_at'))


def _repariere_befund(fund, lauf_id):
    """Ein guarded UPDATE und ein Pflicht-Audit in derselben Transaktion."""
    if not connection.in_atomic_block:
        raise BelegkettenKonflikt('Eine Reparatur benötigt eine umgebende Transaktion.')
    if not fund['reparierbar'] or fund['gruppe'] not in ('A', 'B', 'D'):
        raise BelegkettenKonflikt('Dieser Befund ist nicht automatisch reparierbar.')
    values = {'updated_at': timezone.now()}
    if fund['nach'] is not None:
        values['status'] = fund['nach']
    if fund['nach_lifecycle'] is not None:
        values['lifecycle'] = fund['nach_lifecycle']
    updated = Invoice.objects.filter(
        pk=fund['invoice_id'], status=fund['von'], lifecycle=fund['von_lifecycle'],
        updated_at=fund['revision'],
    ).update(**values)
    if updated != 1:
        raise BelegkettenKonflikt(f"Beleg {fund['nummer']} wurde inzwischen geändert. Bitte neu prüfen.")
    write_audit(None, 'update', object_type='Invoice', object_id=str(fund['invoice_id']),
        object_repr=fund['nummer'], message=f"Belegketten-Reparatur {fund['nummer']}: "
        f"{fund['von']} → {fund['nach']} ({fund['code']}).",
        pflicht=True, metadata={
            'von': fund['von'], 'nach': fund['nach'], 'von_lifecycle': fund['von_lifecycle'],
            'nach_lifecycle': fund['nach_lifecycle'], 'anlass': 'reparatur',
            'gruppe': fund['gruppe'], 'befund': fund['code'], 'lauf_id': lauf_id,
            'storno_nummern': ', '.join(fund['storno_nummern']),
            'korrektur_nummern': ', '.join(fund['korrektur_nummern']),
        })
    return {k: fund[k] for k in ('invoice_id', 'nummer', 'gruppe', 'code', 'von', 'nach', 'von_lifecycle', 'nach_lifecycle')}


def pruefe_belegketten(*, reparieren=False):
    """Standard: nur lesen. Reparatur: alle Änderungen oder gar keine.

    JSON-tauglicher Bericht mit Vorbefunden, tatsächlich übernommenen
    Änderungen und Nachprüfung. Historische Belegnummern und Dateien bleiben
    unberührt. Gruppe C und alte Entwürfe (D) sind ausschließlich Hinweise.
    """
    pruefzeit = timezone.now()
    lauf_id = str(uuid4()) if reparieren else None
    with transaction.atomic():
        if reparieren:
            _sperre_reparaturlauf()
        vorher = _bericht(pruefzeit)
        aenderungen = []
        if reparieren:
            for fund in vorher['funde']:
                if fund['reparierbar']:
                    aenderungen.append(_repariere_befund(fund, lauf_id))
        nachher = _bericht(pruefzeit) if reparieren else None
    return {
        'schema_version': 1, 'modus': 'reparatur' if reparieren else 'trockenlauf',
        'pruefzeit': pruefzeit.isoformat(), 'lauf_id': lauf_id,
        **vorher, 'aenderungen': aenderungen, 'nachpruefung': nachher,
    }
