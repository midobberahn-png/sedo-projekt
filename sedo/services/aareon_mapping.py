"""Einlesen und transaktionales Ersetzen der Aareon-Mappingdaten."""
import pandas as pd
from django.contrib import messages
from django.db import transaction
from ..audit import write_audit
from ..models import AareonMapping


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


def read_aareon_mapping_dataframe(file):
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
    from ..import_probe import read_dataframe
    df = normalize_columns(read_dataframe(file, header=1))
    needed = {'Rechnungssteller Abrechnungseinheit', 'Leistungsart-ID'}
    if not needed.issubset(set(df.columns)):
        try:
            file.seek(0)
        except Exception:
            pass
        df0 = normalize_columns(read_dataframe(file, header=0))
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


def perform_aareon_mapping_import(df, request):
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
