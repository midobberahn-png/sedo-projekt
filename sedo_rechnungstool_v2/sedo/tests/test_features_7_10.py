from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from sedo.models import Customer, Invoice, InvoiceItem, WorkTask
from sedo.restore_bootstrap import apply_pending_restore
from sedo.services import generate_invoice_files
from sedo.services.tax_export import create_tax_package
from sedo.services.zugferd_validation import validate_invoice_zugferd


class FeaturesSevenToTenTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='sedo_7_10_')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.media = self.home / 'media'
        config = override_settings(
            SEDO_HOME=self.home, MEDIA_ROOT=self.media,
            SEDO_BACKUP_DIR=self.home / 'backups')
        config.enable()
        self.addCleanup(config.disable)
        self.user = get_user_model().objects.create_user('arbeit', password='test-passwort')
        self.client.force_login(self.user)
        self.customer = Customer.objects.create(
            name='Prüfkunde GmbH', customer_no='K-710', email='kunde@example.invalid',
            street='Testweg 1', postcode='39100', city='Magdeburg')

    def invoice(self, number='RE-7-10-1'):
        invoice = Invoice.objects.create(
            number=number, customer=self.customer, issue_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=14))
        InvoiceItem.objects.create(
            invoice=invoice, description='Prüfleistung', quantity=1, unit_price=100)
        generate_invoice_files(invoice, erstmalig=True)
        invoice.refresh_from_db()
        return invoice

    def test_zugferd_prueft_xml_summen_und_einbettung(self):
        result = validate_invoice_zugferd(self.invoice())
        self.assertTrue(result.ok, result.errors)
        self.assertGreaterEqual(len(result.checks), 4)

    def test_zugferd_fehler_stoppt_versand(self):
        invoice = self.invoice()
        Path(invoice.xml_file.path).write_bytes(b'<kaputt>')
        response = self.client.post(reverse('sedo:invoice_email', args=[invoice.pk]))
        self.assertRedirects(response, reverse('sedo:invoice_zugferd_validation', args=[invoice.pk]))
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, 'erstellt')

    def test_aufgabe_frei_und_verknuepft_anlegen_und_erledigen(self):
        due = timezone.localdate().isoformat()
        response = self.client.post(reverse('sedo:task_list'), {
            'title': 'Kunden anrufen', 'note': 'Termin abstimmen', 'due_date': due,
            'priority': 'high', 'customer': self.customer.pk, 'offer': '', 'invoice': '',
        })
        self.assertRedirects(response, reverse('sedo:task_list'))
        task = WorkTask.objects.get()
        self.assertEqual(task.customer, self.customer)
        self.client.post(reverse('sedo:task_toggle', args=[task.pk]))
        task.refresh_from_db()
        self.assertEqual(task.status, 'done')
        self.assertIsNotNone(task.completed_at)

    def test_dashboard_zeigt_offene_aufgabe(self):
        WorkTask.objects.create(title='Heute prüfen', due_date=timezone.localdate(), created_by=self.user)
        response = self.client.get(reverse('sedo:dashboard'))
        self.assertContains(response, 'Heute prüfen')
        self.assertContains(response, 'Meine Wiedervorlagen')

    def test_steuerberaterpaket_enthaelt_beleg_buchungsdaten_und_protokoll(self):
        invoice = self.invoice()
        path, manifest = create_tax_package([invoice], timezone.localdate().strftime('%Y-%m'), user='arbeit')
        with ZipFile(path) as archive:
            names = set(archive.namelist())
            self.assertIn(f'Belege/{invoice.number}.pdf', names)
            self.assertIn('Buchungsdaten.csv', names)
            self.assertIn('Exportprotokoll.txt', names)
            self.assertIn('manifest.json', names)
            loaded = json.loads(archive.read('manifest.json'))
        self.assertEqual(manifest['rechnungen'], 1)
        self.assertEqual(loaded['brutto'], '119.00')
        self.assertEqual(len(loaded['dateien'][0]['sha256']), 64)

    def test_monatsabschluss_download_protokolliert_export(self):
        invoice = self.invoice()
        response = self.client.post(reverse('sedo:month_close'), {
            'month': invoice.issue_date.strftime('%Y-%m'), 'action': 'download_tax_package'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/zip')
        self.assertTrue(self.user.sedo_audit_logs.filter(object_type='SteuerberaterExport').exists())

    def test_backup_vorschau_prueft_datenbank_und_zaehlt_inhalt(self):
        from sedo.backup_utils import inspect_backup
        backup_dir = self.home / 'backups'
        backup_dir.mkdir()
        db = self.home / 'snapshot.sqlite3'
        with sqlite3.connect(db) as snapshot:
            snapshot.executescript('''
                CREATE TABLE django_migrations (app TEXT, name TEXT, applied TEXT);
                INSERT INTO django_migrations VALUES ('sedo', '0017_work_tasks', '2026-09-18');
                CREATE TABLE sedo_customer (id INTEGER PRIMARY KEY);
                INSERT INTO sedo_customer VALUES (1);
                CREATE TABLE sedo_invoice (id INTEGER PRIMARY KEY);
                CREATE TABLE sedo_offer (id INTEGER PRIMARY KEY);
                CREATE TABLE sedo_payment (id INTEGER PRIMARY KEY);
            ''')
        archive = backup_dir / 'SEDO_Backup_TEST.zip'
        with ZipFile(archive, 'w') as out:
            out.write(db, 'data/sedo_web.sqlite3')
            out.writestr('media/test.txt', b'ok')
        with patch('sedo.backup_utils.backup_dir', return_value=backup_dir):
            report = inspect_backup(archive.name)
        self.assertEqual(report['integrity'], 'ok')
        self.assertEqual(report['counts']['Kunden'], 1)
        self.assertEqual(report['media_files'], 1)

    def test_backup_vorschau_weist_pfadmanipulation_ab(self):
        from sedo.backup_utils import BackupError, inspect_backup
        backup_dir = self.home / 'backups'
        backup_dir.mkdir()
        archive = backup_dir / 'SEDO_Backup_UNSICHER.zip'
        with ZipFile(archive, 'w') as out:
            out.writestr('../ausbruch.txt', b'nein')
        with patch('sedo.backup_utils.backup_dir', return_value=backup_dir):
            with self.assertRaises(BackupError):
                inspect_backup(archive.name)

    def test_wiederherstellung_wird_erst_nach_zweiter_pruefung_vorgemerkt(self):
        from sedo.backup_utils import stage_restore
        backup_dir = self.home / 'backups'
        backup_dir.mkdir()
        db = self.home / 'snapshot.sqlite3'
        with sqlite3.connect(db) as snapshot:
            snapshot.executescript('''
                CREATE TABLE django_migrations (app TEXT, name TEXT, applied TEXT);
                INSERT INTO django_migrations VALUES ('sedo', '0017_work_tasks', '2026-09-18');
                CREATE TABLE sedo_customer (id INTEGER PRIMARY KEY);
                CREATE TABLE sedo_invoice (id INTEGER PRIMARY KEY);
            ''')
        archive = backup_dir / 'SEDO_Backup_STAGE.zip'
        with ZipFile(archive, 'w') as out:
            out.write(db, 'data/sedo_web.sqlite3')
            out.writestr('media/beleg.pdf', b'PDF')
        safety = backup_dir / 'SEDO_Backup_SICHERHEIT.zip'
        safety.write_bytes(b'safety')
        with patch('sedo.backup_utils.backup_dir', return_value=backup_dir), \
             patch('sedo.backup_utils.create_backup', return_value=safety):
            plan = stage_restore(archive.name)
        self.assertEqual(plan['backup_name'], archive.name)
        self.assertTrue((self.home / 'pending_restore.json').is_file())
        self.assertTrue((Path(plan['staging']) / 'media' / 'beleg.pdf').is_file())


class RestoreBootstrapTests(TestCase):
    def test_vorgemerkter_tausch_ersetzt_db_und_medien_mit_rueckweg(self):
        with tempfile.TemporaryDirectory(prefix='sedo_restore_boot_') as name:
            home = Path(name)
            live_db = home / 'data' / 'sedo_web.sqlite3'
            staged_db = home / 'restore-staging' / 'abc' / 'data' / 'sedo_web.sqlite3'
            live_media = home / 'media'
            staged_media = staged_db.parents[1] / 'media'
            live_db.parent.mkdir(parents=True)
            staged_db.parent.mkdir(parents=True)
            live_media.mkdir(); staged_media.mkdir()
            for path, marker in ((live_db, 'alt'), (staged_db, 'neu')):
                with sqlite3.connect(path) as db:
                    db.execute('CREATE TABLE marker (value TEXT)')
                    db.execute('INSERT INTO marker VALUES (?)', (marker,))
            (live_media / 'alt.txt').write_text('alt')
            (staged_media / 'neu.txt').write_text('neu')
            import hashlib
            digest = hashlib.sha256(staged_db.read_bytes()).hexdigest()
            (home / 'pending_restore.json').write_text(json.dumps({
                'staging': str(staged_db.parents[1]), 'database_sha256': digest,
                'backup_name': 'TEST.zip'}))
            result = apply_pending_restore(home)
            with sqlite3.connect(live_db) as db:
                self.assertEqual(db.execute('SELECT value FROM marker').fetchone()[0], 'neu')
            self.assertTrue((live_media / 'neu.txt').is_file())
            self.assertFalse((home / 'pending_restore.json').exists())
            self.assertTrue(Path(result['rollback']).is_dir())
