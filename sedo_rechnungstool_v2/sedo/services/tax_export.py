"""Nachvollziehbares Monats-Paket für Steuerberatung und DATEV-Vorbereitung."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
import zipfile
import uuid

from django.conf import settings
from django.utils import timezone

from .invoice_archive import lese_archivdatei, pruefe_archiv
from .workflow_errors import BelegArchivFehler


def _de(value):
    return f'{Decimal(value):.2f}'.replace('.', ',')


def parse_month(month):
    if not isinstance(month, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}', month):
        raise ValueError('Bitte einen gültigen Monat im Format JJJJ-MM wählen.')
    year, number = map(int, month.split('-'))
    date(year, number, 1)
    if year == 9999:
        raise ValueError('Das Jahr liegt außerhalb des unterstützten Bereichs.')
    return year, number


def _csv_text(value):
    value = str(value or '')
    # Nutzereingaben bleiben Text, auch beim Öffnen in Excel/LibreOffice.
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')) else value


def create_tax_package(invoices, month: str, *, deleted_numbers=(), user=''):
    """Erstellt ein vollständiges ZIP oder bricht vor Veröffentlichung ab."""
    parse_month(month)
    deleted_numbers = list(deleted_numbers)
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
            'Belegdatum': invoice.issue_date.isoformat(),
            'Belegfeld 1': _csv_text(invoice.number),
            'Buchungstext': _csv_text(f'{invoice.customer.name} · {invoice.number}'[:60]),
            'Umsatz': _de(invoice.gross_total),
            'Netto': _de(invoice.net_total),
            'Umsatzsteuer': _de(invoice.vat_amount),
            'Steuersatz': _de(invoice.vat_rate),
            'Kunde': _csv_text(invoice.customer.name),
            'Kundennummer': _csv_text(invoice.customer.customer_no),
            'Rechnungsart': invoice.get_invoice_type_display(),
            'Beleglink': name,
        })
        total_net += invoice.net_total
        total_tax += invoice.vat_amount
        total_gross += invoice.gross_total

    buffer = io.StringIO(newline='')
    fields = list(rows[0].keys()) if rows else [
        'Belegdatum', 'Belegfeld 1', 'Buchungstext', 'Umsatz',
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

    export_id = uuid.uuid4().hex
    manifest['export_id'] = export_id
    manifest['dateien'].extend([
        {'datei': name, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
        for name, data in [('Buchungsdaten.csv', csv_bytes), ('Exportprotokoll.txt', protocol)]
    ])
    out_dir = Path(settings.MEDIA_ROOT) / 'month_close' / month / export_id
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
    manifest['paket_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest['paket'] = str(path.relative_to(settings.MEDIA_ROOT))
    return path, manifest
