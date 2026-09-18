"""Nachvollziehbares Monats-Paket für Steuerberatung und DATEV-Vorbereitung."""
from __future__ import annotations

import csv
import hashlib
import io
import json
from decimal import Decimal
from pathlib import Path
import zipfile

from django.conf import settings
from django.utils import timezone

from .invoice_archive import lese_archivdatei, pruefe_archiv
from .workflow_errors import BelegArchivFehler


def _de(value):
    return f'{Decimal(value):.2f}'.replace('.', ',')


def create_tax_package(invoices, month: str, *, deleted_numbers=(), user=''):
    """Erstellt ein vollständiges ZIP oder bricht vor Veröffentlichung ab."""
    documents = []
    rows = []
    total_net = total_tax = total_gross = Decimal('0.00')
    for invoice in invoices:
        archive_report = pruefe_archiv(invoice)
        if archive_report['fehler']:
            raise BelegArchivFehler(archive_report['fehler'][0])
        attachment = lese_archivdatei(invoice, 'pdf_file')
        name = f'Belege/{invoice.number}.pdf'
        documents.append((name, attachment.data))
        rows.append({
            'Belegdatum': invoice.issue_date.strftime('%d%m'),
            'Belegfeld 1': invoice.number,
            'Buchungstext': f'{invoice.customer.name} · {invoice.number}'[:60],
            'Umsatz': _de(abs(invoice.gross_total)),
            'Soll/Haben-Kennzeichen': 'H' if invoice.gross_total >= 0 else 'S',
            'Netto': _de(invoice.net_total),
            'Umsatzsteuer': _de(invoice.vat_amount),
            'Steuersatz': _de(invoice.vat_rate),
            'Kunde': invoice.customer.name,
            'Kundennummer': invoice.customer.customer_no,
            'Rechnungsart': invoice.get_invoice_type_display(),
            'Beleglink': name,
        })
        total_net += invoice.net_total
        total_tax += invoice.vat_amount
        total_gross += invoice.gross_total

    buffer = io.StringIO(newline='')
    fields = list(rows[0].keys()) if rows else [
        'Belegdatum', 'Belegfeld 1', 'Buchungstext', 'Umsatz', 'Soll/Haben-Kennzeichen',
        'Netto', 'Umsatzsteuer', 'Steuersatz', 'Kunde', 'Kundennummer', 'Rechnungsart', 'Beleglink']
    writer = csv.DictWriter(buffer, fieldnames=fields, delimiter=';', lineterminator='\r\n')
    writer.writeheader()
    writer.writerows(rows)
    csv_bytes = ('\ufeff' + buffer.getvalue()).encode('utf-8')

    created = timezone.localtime().isoformat()
    hashes = [{'datei': name, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
              for name, data in documents]
    manifest = {
        'schema': 1, 'produkt': 'SEDO Rechnungstool', 'monat': month,
        'erstellt_am': created, 'erstellt_von': user,
        'rechnungen': len(rows), 'netto': str(total_net), 'umsatzsteuer': str(total_tax),
        'brutto': str(total_gross), 'ausgeblendete_rechnungsnummern': list(deleted_numbers),
        'dateien': hashes,
    }
    protocol = (
        f'SEDO Steuerberater-Export {month}\nErstellt: {created}\nBenutzer: {user or "—"}\n'
        f'Rechnungen: {len(rows)}\nNetto: {_de(total_net)} EUR\nUmsatzsteuer: {_de(total_tax)} EUR\n'
        f'Brutto: {_de(total_gross)} EUR\nAusgeblendete Nummern: {", ".join(deleted_numbers) or "keine"}\n\n'
        'Buchungsdaten.csv ist eine nachvollziehbare Arbeitsdatei für die Steuerberatung. '
        'Konten, Berater- und Mandantennummer werden bewusst nicht geraten; die endgültige '
        'DATEV-Zuordnung erfolgt durch die Steuerberatung. Die Prüfsummen stehen in manifest.json.\n'
    ).encode('utf-8')

    out_dir = Path(settings.MEDIA_ROOT) / 'month_close' / month
    out_dir.mkdir(parents=True, exist_ok=True)
    # Historischer Dateiname bleibt für bestehende Ablagen/Automationen stabil;
    # der Inhalt ist jetzt das vollständige Steuerberaterpaket.
    path = out_dir / f'SEDO_Rechnungen_{month}_Steuerberater_PDF.zip'
    temp = path.with_suffix('.zip.tmp')
    try:
        with zipfile.ZipFile(temp, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('Buchungsdaten.csv', csv_bytes)
            archive.writestr('Exportprotokoll.txt', protocol)
            archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8'))
            for name, data in documents:
                archive.writestr(name, data)
        temp.replace(path)
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    return path, manifest
