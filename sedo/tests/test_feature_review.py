"""Gegenproben zu den im Review gefundenen Fehlerpfaden."""
from contextlib import closing
from datetime import date
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import csv
import io
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, SimpleTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from sedo.models import Customer, Invoice, InvoiceItem, WorkTask, RecurringInvoiceTemplate, RecurringInvoiceItem
from sedo.restore_bootstrap import apply_pending_restore
from sedo.services import generate_invoice_files
from sedo.services.customer_timeline import build_customer_timeline
from sedo.services.reports import build_reports
from sedo.services.tax_export import create_tax_package
from sedo.services.zugferd_validation import validate_invoice_zugferd


class RestoreFailureTests(SimpleTestCase):
    def fixture(self, home):
        live = home / 'data/sedo_web.sqlite3'
        staged = home / 'restore-staging/test/data/sedo_web.sqlite3'
        for path, marker in ((live, 'live'), (staged, 'backup')):
            path.parent.mkdir(parents=True)
            with closing(sqlite3.connect(path)) as con:
                con.execute('CREATE TABLE marker (value TEXT)')
                con.execute('INSERT INTO marker VALUES (?)', (marker,))
                con.commit()
        (home / 'media').mkdir()
        (home / 'media/original.pdf').write_bytes(b'original')
        (staged.parents[1] / 'media').mkdir()
        (staged.parents[1] / 'media/backup.pdf').write_bytes(b'backup')
        from sedo.restore_bootstrap import _tree_sha256
        (home / 'pending_restore.json').write_text(json.dumps({
            'schema': 1, 'staging': str(staged.parents[1]),
            'database_sha256': hashlib.sha256(staged.read_bytes()).hexdigest(),
            'staging_sha256': _tree_sha256(staged.parents[1]), 'backup_name': 'test.zip',
        }))
        return live

    def test_each_failed_file_swap_preserves_live_database_and_media(self):
        for failed_call in range(1, 7):
            with self.subTest(failed_call=failed_call), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                live = self.fixture(home)
                original_bytes = live.read_bytes()
                replace = os.replace
                count = 0
                def fail_once(src, dst):
                    nonlocal count
                    count += 1
                    if count == failed_call:
                        raise OSError('simulierter Datenträgerfehler')
                    return replace(src, dst)
                with patch('sedo.restore_bootstrap.os.replace', side_effect=fail_once):
                    with self.assertRaises(OSError):
                        apply_pending_restore(home)
                self.assertEqual(live.read_bytes(), original_bytes)
                self.assertEqual((home / 'media/original.pdf').read_bytes(), b'original')

    def test_success_cannot_reuse_old_sqlite_wal(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            live = self.fixture(home)
            Path(str(live) + '-wal').write_bytes(b'stale wal')
            Path(str(live) + '-shm').write_bytes(b'stale shared memory')
            apply_pending_restore(home)
            self.assertFalse(Path(str(live) + '-wal').exists())
            self.assertFalse(Path(str(live) + '-shm').exists())

    def test_finished_swap_recovers_after_result_write_failure(self):
        from sedo import restore_bootstrap
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            live = self.fixture(home)
            writer = restore_bootstrap._write_json
            def fail_result(path, data):
                if path.name == 'last_restore.json':
                    raise OSError('Ergebnisprotokoll vorübergehend blockiert')
                return writer(path, data)
            with patch.object(restore_bootstrap, '_write_json', side_effect=fail_result):
                with self.assertRaises(OSError):
                    apply_pending_restore(home)
            apply_pending_restore(home)
            self.assertFalse((home / 'pending_restore.json').exists())
            with closing(sqlite3.connect(live)) as con:
                self.assertEqual(con.execute('SELECT value FROM marker').fetchone()[0], 'backup')

    def test_second_desktop_process_cannot_open_same_data(self):
        from sedo.desktop_lock import acquire
        with tempfile.TemporaryDirectory() as tmp:
            first = acquire(tmp)
            try:
                with self.assertRaises(RuntimeError):
                    acquire(tmp)
            finally:
                first.close()
            acquire(tmp).close()


class FeatureReviewTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        settings = override_settings(SEDO_HOME=self.home, MEDIA_ROOT=self.home / 'media',
                                     SEDO_BACKUP_DIR=self.home / 'backups')
        settings.enable()
        self.addCleanup(settings.disable)
        self.user = get_user_model().objects.create_user('review')
        self.client.force_login(self.user)
        self.customer = Customer.objects.create(name='Gleicher Name', street='Weg 1',
                                                postcode='39100', city='Magdeburg')

    def invoice(self, number='RE-REVIEW', quantity='1.50', price='0.03'):
        inv = Invoice.objects.create(number=number, customer=self.customer,
                                     issue_date=timezone.localdate(), due_date=timezone.localdate())
        InvoiceItem.objects.create(invoice=inv, description='Leistung', quantity=quantity, unit_price=price)
        generate_invoice_files(inv, erstmalig=True)
        inv.refresh_from_db()
        return inv

    def test_valid_half_cent_invoice_passes_validator(self):
        result = validate_invoice_zugferd(self.invoice())
        self.assertTrue(result.ok, result.errors)

    def test_reports_keep_same_named_customers_separate(self):
        inv = self.invoice(quantity='1', price='100')
        other = Customer.objects.create(name=self.customer.name)
        Invoice.objects.create(number='RE-OTHER', customer=other, issue_date=inv.issue_date, gross_total='50')
        rows = build_reports(inv.issue_date.year)['customer_rows']
        self.assertEqual(len(rows), 2)

    def test_service_report_uses_rounded_line_amounts(self):
        inv = self.invoice()
        InvoiceItem.objects.create(invoice=inv, description='Leistung', quantity='1.50', unit_price='0.03')
        self.assertEqual(build_reports(inv.issue_date.year)['service_rows'][0]['total'], Decimal('0.10'))

    def test_customer_timeline_query_count_does_not_grow_per_invoice(self):
        self.invoice(quantity='1', price='100')
        with CaptureQueriesContext(connection) as one:
            build_customer_timeline(self.customer)
        for index in range(10):
            Invoice.objects.create(number=f'RE-N{index}', customer=self.customer,
                                   issue_date=timezone.localdate(), gross_total='100', due_date=timezone.localdate())
        with CaptureQueriesContext(connection) as many:
            build_customer_timeline(self.customer)
        self.assertLessEqual(len(many), len(one) + 1)
        self.assertLessEqual(len(many), 8)

    def test_document_paths_normalize_media_root_and_use_portable_separators(self):
        media = self.home / 'media'
        media.mkdir()
        with override_settings(MEDIA_ROOT=media / '..' / 'media'):
            inv = self.invoice(quantity='1', price='100')
            from sedo.services.invoice_archive import lese_archivdatei
            self.assertTrue(lese_archivdatei(inv).data)
            for field in ('pdf_file', 'xml_file', 'zugferd_file'):
                name = getattr(inv, field).name
                self.assertTrue(name.startswith('generated/'))
                self.assertNotIn(chr(92), name)

    def test_draft_archive_uses_matching_index_without_sorting(self):
        response = self.client.get(reverse('sedo:invoice_list') + '?view=draft')
        plan = response.context['page_obj'].paginator.object_list.explain()
        self.assertIn('invoice_draft_order_idx', plan)
        self.assertNotIn('TEMP B-TREE FOR ORDER BY', plan)

    def test_high_priority_tasks_come_first_on_same_day(self):
        today = timezone.localdate()
        WorkTask.objects.create(title='Normal', due_date=today, priority='normal')
        WorkTask.objects.create(title='Wichtig', due_date=today, priority='high')
        response = self.client.get(reverse('sedo:dashboard'))
        self.assertEqual(response.context['open_tasks'][0].title, 'Wichtig')

    def test_invalid_months_cannot_create_export_paths(self):
        for month in ('2026-09/../../outside', '2026-13', '0000-01', '999999999-01'):
            with self.subTest(month=month):
                with self.assertRaises(ValueError):
                    create_tax_package([], month)
                response = self.client.post(reverse('sedo:month_close'), {
                    'month': month, 'action': 'download_tax_package'})
                self.assertEqual(response.status_code, 400)

    def test_exports_are_immutable_per_run(self):
        inv = self.invoice(quantity='1', price='100')
        month = inv.issue_date.strftime('%Y-%m')
        first, _ = create_tax_package([inv], month, user='first')
        original = first.read_bytes()
        second, _ = create_tax_package([], month, user='second')
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), original)

    def test_corrupt_backup_gives_controlled_error(self):
        from sedo.backup_utils import BackupError, inspect_backup
        folder = self.home / 'backups'
        folder.mkdir()
        (folder / 'broken.zip').write_bytes(b'not a zip')
        with patch('sedo.backup_utils.backup_dir', return_value=folder):
            with self.assertRaises(BackupError):
                inspect_backup('broken.zip')

    def test_backup_includes_committed_wal_data(self):
        from sedo.backup_utils import _sanitized_db_copy
        live = self.home / 'wal.sqlite3'
        with closing(sqlite3.connect(live)) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE marker(value TEXT)')
            db.execute("INSERT INTO marker VALUES ('committed')")
            db.commit()
            copy = _sanitized_db_copy(live)
            self.addCleanup(lambda: copy.unlink(missing_ok=True))
            with closing(sqlite3.connect(copy)) as snapshot:
                self.assertEqual(snapshot.execute('SELECT value FROM marker').fetchone()[0], 'committed')

    def test_restore_simulation_rejects_incomplete_schema_without_touching_live_data(self):
        from sedo.backup_utils import inspect_backup, BackupError
        folder = self.home / 'backups'
        folder.mkdir()
        db = self.home / 'fake.sqlite3'
        with closing(sqlite3.connect(db)) as con:
            con.executescript('CREATE TABLE django_migrations(app TEXT,name TEXT,applied TEXT);'
                              'CREATE TABLE sedo_customer(id INTEGER); CREATE TABLE sedo_invoice(id INTEGER);')
        with ZipFile(folder / 'fake.zip', 'w') as archive:
            archive.write(db, 'data/sedo_web.sqlite3')
        with patch('sedo.backup_utils.backup_dir', return_value=folder), self.assertRaises(BackupError):
            inspect_backup('fake.zip')
        self.assertEqual(Customer.objects.get(pk=self.customer.pk).name, 'Gleicher Name')

    def test_duplicate_restore_paths_are_rejected(self):
        from sedo.backup_utils import inspect_backup, BackupError
        folder = self.home / 'backups'
        folder.mkdir()
        with ZipFile(folder / 'duplicate.zip', 'w') as archive:
            archive.writestr('media/BELEG.pdf', b'a')
            archive.writestr('media/beleg.pdf', b'b')
        with patch('sedo.backup_utils.backup_dir', return_value=folder), self.assertRaises(BackupError):
            inspect_backup('duplicate.zip')

    def test_export_csv_cannot_execute_customer_name_as_formula(self):
        self.customer.name = '=1+2'
        self.customer.save()
        inv = self.invoice(quantity='1', price='100')
        path, manifest = create_tax_package([inv], inv.issue_date.strftime('%Y-%m'))
        with ZipFile(path) as archive:
            row = next(csv.DictReader(io.StringIO(archive.read('Buchungsdaten.csv').decode('utf-8-sig')), delimiter=';'))
            self.assertEqual(row['Kunde'], "'=1+2")
            for entry in manifest['dateien']:
                self.assertEqual(hashlib.sha256(archive.read(entry['datei'])).hexdigest(), entry['sha256'])

    def test_xml_structure_and_non_finite_numbers_are_rejected(self):
        from lxml import etree
        from sedo.services.zugferd_validation import NS
        inv = self.invoice(quantity='1', price='100')
        original = Path(inv.xml_file.path).read_bytes()
        for kind in ('unknown_element', 'non_finite', 'invalid_country'):
            root = etree.fromstring(original)
            if kind == 'unknown_element':
                etree.SubElement(root, '{' + NS['ram'] + '}Unexpected')
            elif kind == 'non_finite':
                root.xpath('//ram:BilledQuantity', namespaces=NS)[0].text = 'NaN'
            else:
                root.xpath('//ram:SellerTradeParty/ram:PostalTradeAddress/ram:CountryID', namespaces=NS)[0].text = 'XX'
            xml = etree.tostring(root)
            with self.subTest(kind=kind), patch('sedo.services.zugferd_validation.lese_archivdatei',
                    side_effect=lambda _, field: SimpleNamespace(data=xml if field == 'xml_file' else b'pdf')), \
                    patch('sedo.services.zugferd_validation._embedded_xml', return_value=xml):
                result = validate_invoice_zugferd(inv)
                self.assertFalse(result.ok)

    def test_preview_uses_same_duplicate_rule_as_monthly_generation(self):
        from sedo.services.recurring_preview import build_recurring_preview
        for index in range(2):
            template = RecurringInvoiceTemplate.objects.create(customer=self.customer, title=f'Vorlage {index}')
            RecurringInvoiceItem.objects.create(template=template, description='Pflege', quantity='1', unit_price='100')
        month = build_recurring_preview(timezone.localdate(), months=1)[0]
        self.assertEqual(month['pending_count'], 1)
        self.assertEqual(month['total'], Decimal('119.00'))
        self.assertTrue(month['rows'][1]['duplicate'])


class AIReviewTests(SimpleTestCase):
    def test_non_finite_model_numbers_are_rejected(self):
        from sedo.services.ai import _ai_to_decimal
        for value in ('NaN', 'sNaN', 'Infinity', '-Infinity', float('nan'), float('inf')):
            with self.subTest(value=value):
                self.assertIsNone(_ai_to_decimal(value))
