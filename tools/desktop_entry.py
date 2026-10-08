"""H2: Desktop-Einstieg mit strengem, isoliertem Build-Selbsttest.

Normale Starts delegieren unverändert an desktop.main. Kein Audio-/Modell-Upload,
kein Abruf eines Whisper-Modells und keine vorhandene Anwendungsdatenbank.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import urllib.request

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _check_spreadsheet_workers():
    """Qualify optional bundled Excel engines using generated documents only."""
    import io
    import struct
    from openpyxl import Workbook
    from sedo.document_probe import run_operation

    modern = io.BytesIO()
    book = Workbook()
    book.active.append(['name', 'amount'])
    book.active.append(['synthetic', 12.5])
    book.save(modern)
    book.close()

    # A minimal BIFF2 worksheet needs no third-party writer or test fixtures.
    def record(kind, data):
        return struct.pack('<HH', kind, len(data)) + data
    legacy = record(0x0009, struct.pack('<HH', 7, 0x0010))
    legacy += record(0x0042, struct.pack('<H', 1252))
    for row, values in enumerate((('name', 'amount'), ('synthetic', '12.5'))):
        for column, value in enumerate(values):
            encoded = value.encode('ascii')
            legacy += record(0x0004, struct.pack('<HH3sB', row, column, b'\0\0\0', len(encoded)) + encoded)
    legacy += record(0x000A, b'')

    checks = {}
    for extension, data, amount in (
            ('.csv', b'name,amount\nsynthetic,12.5\n', '12.5'),
            ('.xlsx', modern.getvalue(), 12.5), ('.xls', legacy, '12.5')):
        result = run_operation('table_rows', [data], extension=extension)
        checks[extension[1:]] = (result.get('columns') == ['name', 'amount']
                                 and result.get('rows') == [['synthetic', amount]])
    return checks


def _check_invoice_workflow():
    """Exercise bundled document creation only in the freshly bootstrapped test DB."""
    from decimal import Decimal
    from sedo.company_profile import PROFILE_KEY, SellerProfileError, load_profile, save_profile
    from sedo.models import AppSetting, Customer, Invoice, InvoiceItem
    from sedo.services.invoices import finalisiere_entwurf
    from sedo.services.zugferd_validation import validate_invoice_zugferd
    if AppSetting.objects.filter(key=PROFILE_KEY).exists():
        raise RuntimeError('The invoice self-check requires a new synthetic database.')
    missing_profile_rejected = False
    try:
        load_profile()
    except SellerProfileError:
        missing_profile_rejected = True
    # Public example IBAN and reserved example.invalid addresses, never operator data.
    save_profile({
        'name': 'Synthetische Build-Testfirma', 'street': 'Teststraße 1',
        'postcode': '10115', 'city': 'Berlin', 'country': 'DE',
        'phone': '030 0000000', 'email': 'build@example.invalid',
        'tax_no': '00/000/00000', 'vat_id': '', 'seller_id': 'SYNTHETIC-RELEASE-ONLY',
        'iban': 'DE89370400440532013000',
        'bic': 'COBADEFFXXX', 'bank_name': 'Synthetische Testbank',
        'register': '', 'ceo': 'Synthetische Testperson',
    })
    customer = Customer.objects.create(name='Synthetischer Build-Testkunde',
                                      street='Teststraße 2', postcode='10115', city='Berlin', country='DE')
    invoice = Invoice.objects.create(number='EN-BUILD-001', customer=customer,
                                     lifecycle='draft', status='entwurf')
    InvoiceItem.objects.create(invoice=invoice, position=1, description='Synthetische Testleistung',
                               quantity=Decimal('1'), unit_price=Decimal('100'))
    finalisiere_entwurf(invoice)
    invoice.refresh_from_db()
    validation = validate_invoice_zugferd(invoice)
    if not validation.ok:
        raise RuntimeError('Synthetic invoice self-check failed: ' + '; '.join(validation.errors))
    return bool(missing_profile_rejected and invoice.lifecycle == 'issued'
                and invoice.number.startswith('RE') and invoice.gross_total == Decimal('119.00')
                and invoice.pdf_file and invoice.xml_file and invoice.zugferd_file and validation.ok)


def check() -> int:
    import desktop
    home = Path(tempfile.mkdtemp(prefix='sedo_build_check_'))
    os.environ['SEDO_HOME'] = str(home)
    app, _ = desktop._bootstrap()
    port = desktop._free_port()
    desktop._serve_in_thread(app, port)
    http = desktop._wait_until_up(port, timeout=30)
    assets = False
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/static/sedo/app.css', timeout=10) as response:
            assets = response.status == 200 and bool(response.read())
        for name in ('company-setup.css', 'dashboard-widgets.css', 'dashboard-widgets.js', 'calendar.css', 'quiet-ui.css', 'quiet-ui.js', 'mail.js', 'accessibility-v2.js', 'incoming-print.js'):
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/static/sedo/{name}', timeout=10) as response:
                assets = assets and response.status == 200 and bool(response.read())
    except OSError:
        assets = False
    from sedo.services import stt_status
    whisper = bool(stt_status().get('available'))
    from sedo.desktop_print import IncomingPrintAPI
    print_available = callable(IncomingPrintAPI.print_incoming_pdf)
    if sys.platform == 'darwin':
        from Quartz import PDFDocument
        print_available = print_available and hasattr(PDFDocument, 'printOperationForPrintInfo_scalingMode_autoRotate_')
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    executor = MigrationExecutor(connection)
    migrated = not executor.migration_plan(executor.loader.graph.leaf_nodes())
    # Prove the frozen package can encrypt credentials and load its CA bundle.
    # No real account and no connection to an external mailserver are involved.
    import ssl
    from sedo.models import MailAccount
    from sedo.services.mail_secrets import encrypt_password, decrypt_password
    from sedo.services.mail_imap import tls_context
    probe = MailAccount(email='build@example.invalid', username='build@example.invalid')
    probe.password_encrypted = encrypt_password(probe, 'isolierter-postfach-test')
    tls = tls_context()
    mail_available = (decrypt_password(probe) == 'isolierter-postfach-test'
                      and tls.check_hostname and tls.verify_mode == ssl.CERT_REQUIRED
                      and bool(tls.get_ca_certs()))
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/static/sedo/mail.css', timeout=10) as response:
            mail_available = mail_available and response.status == 200 and bool(response.read())
    except OSError:
        mail_available = False
    from datetime import date
    from types import SimpleNamespace
    from sedo.models import CalendarConnection
    from sedo.services.calendar_sync import encrypt, client_for
    from sedo.services.calendar_data import fields, occurrences, aware
    calendar_probe = CalendarConnection(user_id=1, username='calendar@example.invalid')
    calendar_probe.password_encrypted = encrypt(calendar_probe, 'isolierter-kalender-test')
    raw_calendar = ('BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:build-check\r\n'
                    'DTSTART:20260923T080000Z\r\nDTEND:20260923T090000Z\r\n'
                    'SUMMARY:Buildtest\r\nRRULE:FREQ=DAILY;COUNT=2\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n')
    calendar_values = fields(raw_calendar)
    calendar_rows, calendar_errors = occurrences(
        [SimpleNamespace(raw_ical=raw_calendar, recurring=True, title='Buildtest', pk=0)],
        aware(date(2026, 9, 23)), aware(date(2026, 9, 26)))
    calendar_available = bool(client_for(calendar_probe).authorization and calendar_values['recurring']
                              and len(calendar_rows) == 2 and not calendar_errors)
    # Die ausgelieferte App muss die Prüfschemata tatsächlich öffnen können.
    from sedo.services.zugferd_validation import _official_rules, _profile_schema
    validator = sum(len(assertions) for _, assertions in _official_rules()) == 806
    validator = validator and _profile_schema('extended') is not None and _profile_schema('en16931') is not None
    invoice_workflow = _check_invoice_workflow()
    from sedo.backup_utils import create_backup, inspect_backup
    backup = create_backup('build_check')
    restoration = inspect_backup(backup.name)['integrity'] == 'ok'
    hardlinks = False
    original = home/'hardlink-probe'
    original.write_bytes(b'isolierter Buildtest')
    try:
        linked = home/'hardlink-probe.link'
        os.link(original, linked)
        hardlinks = linked.samefile(original)
    except OSError:
        pass
    # Exercise the actual frozen child-process dispatch, not only its imports.
    import io
    from pypdf import PdfReader, PdfWriter
    from sedo.document_probe import (check_isolation, extract_xml_text, image_pdf,
                                     image_preview, merge_pdfs, validate_pdf)
    raw = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(raw)
    bounded_pdf = merge_pdfs([raw.getvalue()])
    from PIL import Image
    raster = io.BytesIO()
    Image.new('RGB', (16, 16), 'white').save(raster, 'PNG')
    bounded_image = image_preview(raster.getvalue())
    printed_image = image_pdf(raster.getvalue())
    spreadsheet_parsers = _check_spreadsheet_workers()
    isolation = check_isolation()
    document_probe = (len(PdfReader(io.BytesIO(bounded_pdf)).pages) == 1
                      and validate_pdf(raw.getvalue()) == 1
                      and bounded_image.startswith(b'\x89PNG') and printed_image.startswith(b'%PDF-')
                      and 'synthetic' in extract_xml_text(b'<Invoice>synthetic</Invoice>')
                      and all(spreadsheet_parsers.values())
                      and isolation['resource_limits_applied'])
    if sys.platform == 'darwin' or sys.platform.startswith('linux'):
        document_probe = document_probe and isolation['filesystem_network_sandbox']
    result = {'ok': bool(http and assets and whisper and migrated and hardlinks and validator and invoice_workflow and restoration and mail_available and calendar_available and print_available and document_probe),
              'http_login': http, 'static_assets': assets, 'whisper_available': whisper,
              'migrations_complete': migrated, 'hardlinks': hardlinks,
              'invoice_validator_available': validator, 'restore_simulation': restoration,
              'invoice_workflow': invoice_workflow,
              'mail_available': bool(mail_available),
              'calendar_available': calendar_available,
              'incoming_print_available': bool(print_available),
              'document_probe_isolated': document_probe,
              'document_probe_backend': isolation['backend'],
              'document_filesystem_network_sandbox': isolation['filesystem_network_sandbox'],
              'document_resource_limits': isolation['resource_limits_applied'],
              'spreadsheet_parsers': spreadsheet_parsers,
              'frozen': bool(getattr(sys, 'frozen', False)),
              'python': '.'.join(map(str, sys.version_info[:3]))}
    report = os.environ.get('SEDO_CHECK_REPORT')
    if report:
        Path(report).write_text(json.dumps(result, indent=2), encoding='utf-8')
    # Nur Testdaten im temporären Ordner; der Prozess endet samt Server-Thread.
    # Keine GUI-, Mikrofon- oder Zielrechnerabnahme daraus ableiten.
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--sedo-document-probe':
        from sedo.document_probe import worker_main
        raise SystemExit(worker_main(sys.argv[2]))
    if len(sys.argv) == 3 and sys.argv[1] == '--check-restore':
        from sedo.restore_check import main
        raise SystemExit(main(sys.argv[2]))
    if '--check-strict' in sys.argv:
        raise SystemExit(check())
    import desktop
    raise SystemExit(desktop.main())
