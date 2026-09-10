"""H3-Migration 0014: kontrollierte Übernahme des bisherigen Mischstatus."""
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


OLD = ('sedo', '0013_invoice_query_indexes')
NEW = ('sedo', '0014_invoice_lifecycle')


class InvoiceLifecycleMigrationTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.migrate(OLD)
        self.addCleanup(self.migrate, NEW)
        old_apps = MigrationExecutor(connection).loader.project_state([OLD]).apps
        Customer = old_apps.get_model('sedo', 'Customer')
        Invoice = old_apps.get_model('sedo', 'Invoice')
        customer = Customer.objects.create(name='TEST H3 Migration')
        self.ids = {}
        for n, status in enumerate(('entwurf','erstellt','versendet','ueberfaellig','teilbezahlt','gemahnt','bezahlt','storniert','korrigiert'), 1):
            obj = Invoice.objects.create(number=f'TEST-H3-MIG-{n}', customer=customer, status=status)
            self.ids[status] = obj.pk

    @staticmethod
    def migrate(target):
        MigrationExecutor(connection).migrate([target])

    def test_vorwaerts_ordnen_nur_belegzustaende_zu(self):
        self.migrate(NEW)
        apps = MigrationExecutor(connection).loader.project_state([NEW]).apps
        Invoice = apps.get_model('sedo', 'Invoice')
        expected = {
            'entwurf':'draft', 'storniert':'cancelled', 'korrigiert':'corrected',
            'erstellt':'issued', 'versendet':'issued', 'ueberfaellig':'issued',
            'teilbezahlt':'issued', 'gemahnt':'issued', 'bezahlt':'issued',
        }
        for status, lifecycle in expected.items():
            with self.subTest(status=status):
                inv = Invoice.objects.get(pk=self.ids[status])
                self.assertEqual((inv.status, inv.lifecycle), (status, lifecycle))

    def test_rueckweg_erhaelt_bisherige_statuswerte_bei_unveraendertem_lifecycle(self):
        self.migrate(NEW)
        self.migrate(OLD)
        apps = MigrationExecutor(connection).loader.project_state([OLD]).apps
        Invoice = apps.get_model('sedo', 'Invoice')
        self.assertEqual(set(Invoice.objects.values_list('status', flat=True)), set(self.ids))
        with connection.cursor() as cursor:
            self.assertEqual(cursor.execute('PRAGMA integrity_check').fetchone(), ('ok',))
            self.assertEqual(cursor.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_rueckweg_spiegelt_kanonische_belegzustaende_trotz_stalem_zahlcache(self):
        self.migrate(NEW)
        apps = MigrationExecutor(connection).loader.project_state([NEW]).apps
        Invoice = apps.get_model('sedo', 'Invoice')
        ids = [self.ids['erstellt'], self.ids['versendet'], self.ids['ueberfaellig']]
        Invoice.objects.filter(pk=ids[0]).update(lifecycle='draft', status='ueberfaellig')
        Invoice.objects.filter(pk=ids[1]).update(lifecycle='corrected', status='versendet')
        Invoice.objects.filter(pk=ids[2]).update(lifecycle='cancelled', status='gemahnt')

        self.migrate(OLD)
        old_apps = MigrationExecutor(connection).loader.project_state([OLD]).apps
        OldInvoice = old_apps.get_model('sedo', 'Invoice')
        self.assertEqual(
            list(OldInvoice.objects.filter(pk__in=ids).order_by('pk').values_list('status', flat=True)),
            ['entwurf', 'korrigiert', 'storniert'],
        )
