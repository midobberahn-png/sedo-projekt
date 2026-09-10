"""Etappe 7: Indexvertrag, echter SQL-Plan und reversible Migration ohne Datenänderung."""
import importlib
import re
from datetime import timedelta
from io import StringIO
from unittest import skipUnless

from django.core.management import call_command
from django.db import connection, migrations, models, transaction, IntegrityError
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from sedo.models import (AuditLog, Customer, CustomerObject, DunningDocument,
                         Invoice, InvoiceItem, NumberRange, Offer, Payment)
from sedo.services.dunning import get_dunning_candidates

OLD = ('sedo', '0012_concurrency_constraints')
NEW = ('sedo', '0013_invoice_query_indexes')
STATUS = 'invoice_status_due_idx'
DUE = 'invoice_active_due_status_idx'


def migration():
    return importlib.import_module('sedo.migrations.' + NEW[1]).Migration


def index_sql(name):
    with connection.cursor() as cursor:
        row = cursor.execute('SELECT sql FROM sqlite_master WHERE type=\'index\' AND name=%s',
                             [name]).fetchone()
    return row[0] if row else None


def snapshot():
    """Alle Anwendungstabellen; Django-Migrationsbuchführung ist ausdrücklich ausgenommen."""
    with connection.cursor() as cursor:
        names = connection.introspection.table_names(cursor)
        return {name: sorted(cursor.execute('SELECT * FROM ' + connection.ops.quote_name(name))
                             .fetchall(), key=repr)
                for name in names if name != 'django_migrations'}


class InvoiceIndexVertragTests(SimpleTestCase):
    def test_statusindex_modellvertrag(self):
        idx = next(i for i in Invoice._meta.indexes if i.name == STATUS)
        self.assertEqual(idx.fields, ['status', 'due_date'])
        self.assertIsNone(idx.condition)

    def test_faelligkeitsindex_modellvertrag(self):
        idx = next(i for i in Invoice._meta.indexes if i.name == DUE)
        self.assertEqual(idx.fields, ['due_date', 'status'])
        self.assertEqual(idx.condition, models.Q(deleted_at__isnull=True))

    def test_nur_zwei_addindex_operationen(self):
        ops = migration().operations
        self.assertEqual(len(ops), 2)
        self.assertTrue(all(type(op) is migrations.AddIndex for op in ops))
        self.assertEqual([op.model_name for op in ops], ['invoice', 'invoice'])
        self.assertEqual([op.index.deconstruct() for op in ops],
                         [idx.deconstruct() for idx in Invoice._meta.indexes])

    def test_vorgaenger_ist_0012(self):
        self.assertEqual(migration().dependencies, [OLD])
        self.assertTrue(migration().atomic)

    def test_keine_redundanten_praefixindizes(self):
        indexes = Invoice._meta.indexes
        self.assertEqual({i.name for i in indexes}, {STATUS, DUE})
        self.assertFalse(any(i.fields == ['status'] for i in indexes))
        self.assertTrue(all(len(i.name) <= 30 for i in indexes))


@skipUnless(connection.vendor == 'sqlite', 'SQLite-spezifische physische Indexprüfung')
class InvoiceIndexDatenbankTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.customer = Customer.objects.create(name='TEST-IDX synthetisch')
        today = timezone.localdate()
        cls.invoices = []
        for n, (status, _) in enumerate(Invoice.STATUS):
            cls.invoices.append(Invoice.objects.create(number=f'TEST-IDX-{n}',
                customer=cls.customer, status=status, due_date=today-timedelta(days=31+n),
                gross_total='119.00'))
        cls.future = Invoice.objects.create(number='TEST-IDX-FUTURE', customer=cls.customer,
                due_date=today+timedelta(days=1), gross_total='119.00')
        paid = next(i for i in cls.invoices if i.status == 'bezahlt')
        Payment.objects.create(invoice=paid, amount=paid.gross_total, booking_date=today,
                               external_id='TEST-IDX-PAID')
        cls.null = Invoice.objects.create(number='TEST-IDX-NULL', customer=cls.customer,
                due_date=None, gross_total='119.00')
        cls.hidden = Invoice.objects.create(number='TEST-IDX-HIDDEN', customer=cls.customer,
                due_date=today-timedelta(days=50), deleted_at=timezone.now(), gross_total='119.00')

    def test_statusindex_physisch(self):
        sql = index_sql(STATUS)
        self.assertIsNotNone(sql, 'Statusindex fehlt in der tatsächlichen Datenbank')
        self.assertIn('("status", "due_date")', sql)
        self.assertNotIn('WHERE', sql.upper())
        self.assertNotIn('UNIQUE', sql.upper())

    def test_faelligkeitsindex_physisch(self):
        sql = index_sql(DUE)
        self.assertIsNotNone(sql, 'Fälligkeitsindex fehlt in der tatsächlichen Datenbank')
        self.assertIn('("due_date", "status")', sql)
        self.assertIn('WHERE "deleted_at" IS NULL', sql)
        self.assertNotIn('UNIQUE', sql.upper())

    def test_statusfilter_nutzt_kombinierten_index(self):
        qs = Invoice.objects.filter(status='entwurf')
        self.assertIn(STATUS, qs.explain())
        self.assertEqual(list(qs.values_list('number', flat=True)), ['TEST-IDX-0'])

    def test_echte_mahnabfrage_nutzt_faelligkeitsindex(self):
        # Kein nachgebauter WHERE-Ausdruck: der erste SELECT stammt aus dem Service.
        with CaptureQueriesContext(connection) as captured:
            candidates = get_dunning_candidates(14)
        sql = next(q['sql'] for q in captured if q['sql'].lstrip().upper().startswith('SELECT'))
        with connection.cursor() as cursor:
            plan = ' '.join(row[3] for row in cursor.execute('EXPLAIN QUERY PLAN ' + sql).fetchall())
        self.assertIn(DUE, plan)
        self.assertNotIn('TEMP B-TREE FOR ORDER BY', plan)
        expected = {i.pk for i in self.invoices
                    if i.lifecycle == 'issued' and i.payment_state != 'paid' and i.invoice_type != '457'}
        self.assertEqual({i.pk for i in candidates}, expected)
        dates = [i.due_date for i in candidates]
        self.assertEqual(dates, sorted(dates))

    def test_null_zukunft_ausgeblendet_bleiben_unveraendert(self):
        before = snapshot()
        ids = {i.pk for i in get_dunning_candidates(14)}
        self.assertTrue(ids.isdisjoint({self.future.pk, self.null.pk, self.hidden.pk}))
        self.assertEqual(snapshot(), before)
        self.assertTrue(Invoice.all_objects.filter(pk=self.hidden.pk).exists())


@skipUnless(connection.vendor == 'sqlite', 'SQLite-Migrationsvertrag')
class InvoiceIndexMigrationTests(TransactionTestCase):
    def apps_for(self, target):
        return MigrationExecutor(connection).loader.project_state([target]).apps

    def setUp(self):
        self.migrate(OLD)
        self.addCleanup(self.migrate, ('sedo', '0014_invoice_lifecycle'))
        apps = self.apps_for(OLD)
        CustomerH = apps.get_model('sedo', 'Customer')
        ObjectH = apps.get_model('sedo', 'CustomerObject')
        InvoiceH = apps.get_model('sedo', 'Invoice')
        ItemH = apps.get_model('sedo', 'InvoiceItem')
        PaymentH = apps.get_model('sedo', 'Payment')
        OfferH = apps.get_model('sedo', 'Offer')
        DunningH = apps.get_model('sedo', 'DunningDocument')
        RangeH = apps.get_model('sedo', 'NumberRange')
        AuditH = apps.get_model('sedo', 'AuditLog')
        self.customer = CustomerH.objects.create(name='TEST-IDX Migration')
        self.obj = ObjectH.objects.create(customer=self.customer, name='TEST-IDX Objekt')
        self.original = InvoiceH.objects.create(number='TEST-IDX-ORIGINAL', customer=self.customer,
            obj=self.obj, status='korrigiert', due_date=timezone.localdate(), gross_total='119.00',
            pdf_file='unveraendert/original.pdf', xml_file='unveraendert/factur-x.xml')
        self.correction = InvoiceH.objects.create(number='TEST-IDX-KORREKTUR', customer=self.customer,
            reference_invoice=self.original, invoice_type='384', status='erstellt')
        InvoiceH.objects.create(number='TEST-IDX-ENTWURF', customer=self.customer, status='entwurf')
        InvoiceH.objects.create(number='TEST-IDX-AUSGEBLENDET', customer=self.customer,
            deleted_at=timezone.now(), due_date=None)
        ItemH.objects.create(invoice=self.original, description='TEST-IDX Leistung', unit_price='100')
        PaymentH.objects.create(invoice=self.correction, amount='10', booking_date=timezone.localdate(),
            external_id='TEST-IDX-ZAHLUNG')
        OfferH.objects.create(number='TEST-IDX-ANGEBOT', customer=self.customer, linked_invoice=self.original)
        DunningH.objects.create(invoice=self.correction, kind='reminder')
        RangeH.objects.create(prefix='EN', year=2099, month=1, next_number=17)
        AuditH.objects.create(action='create', object_repr='TEST-IDX alter Nachweis')
        self.customer_id, self.original_id = self.customer.pk, self.original.pk

    @staticmethod
    def migrate(target):
        MigrationExecutor(connection).migrate([target])

    def test_vorwaerts_rueckwaerts_erneut_ohne_datenverlust(self):
        before = snapshot()
        with connection.cursor() as c:
            table = c.execute("SELECT rootpage, sql FROM sqlite_master WHERE type='table' AND name='sedo_invoice'").fetchone()
        self.assertIsNone(index_sql(STATUS)); self.assertIsNone(index_sql(DUE))
        for target, present in [(NEW, True), (OLD, False), (NEW, True)]:
            with self.subTest(target=target):
                self.migrate(target)
                self.assertEqual(index_sql(STATUS) is not None, present)
                self.assertEqual(index_sql(DUE) is not None, present)
                self.assertEqual(snapshot(), before)
                with connection.cursor() as c:
                    self.assertEqual(c.execute("SELECT rootpage, sql FROM sqlite_master WHERE type='table' AND name='sedo_invoice'").fetchone(), table)
                    self.assertEqual(c.execute('PRAGMA integrity_check').fetchall(), [('ok',)])
                    self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_sql_besteht_nur_aus_indexoperationen(self):
        for backwards, verb in [(False, 'CREATE INDEX'), (True, 'DROP INDEX')]:
            out = StringIO(); call_command('sqlmigrate', 'sedo', NEW[1], backwards=backwards, stdout=out)
            statements = [x.strip() for x in re.sub(r'--[^\n]*', '', out.getvalue()).split(';') if x.strip()]
            self.assertEqual(len([x for x in statements if x.startswith(verb)]), 2)
            self.assertTrue(all(x in ('BEGIN','COMMIT') or x.startswith(verb) for x in statements))

    def test_echte_migration_baut_keine_tabelle_neu(self):
        with CaptureQueriesContext(connection) as captured: self.migrate(NEW)
        sql='\n'.join(q['sql'] for q in captured)
        self.assertEqual(len(re.findall(r'CREATE INDEX', sql, re.I)), 2)
        self.assertNotRegex(sql.upper(), r'(?:CREATE|ALTER|DROP) TABLE|(?:INSERT INTO|UPDATE|DELETE FROM) "SEDO_')

    def test_statusauswahl_vor_nach_gleich(self):
        def selection(target):
            InvoiceH=self.apps_for(target).get_model('sedo','Invoice')
            return {status:list(InvoiceH.objects.filter(status=status).values_list('pk',flat=True))
                    for status,_ in InvoiceH._meta.get_field('status').choices}
        before=selection(OLD); self.migrate(NEW); self.assertEqual(selection(NEW),before)
        self.migrate(OLD); self.assertEqual(selection(OLD),before)

    def test_eindeutigkeitssperren_bleiben_erhalten(self):
        for target in (NEW, OLD):
            self.migrate(target); InvoiceH=self.apps_for(target).get_model('sedo','Invoice')
            with self.assertRaises(IntegrityError), transaction.atomic():
                InvoiceH.objects.create(number='TEST-IDX-ORIGINAL', customer_id=self.customer_id)
            with self.assertRaises(IntegrityError), transaction.atomic():
                InvoiceH.objects.create(number='TEST-IDX-KORREKTUR-2', customer_id=self.customer_id,
                    reference_invoice_id=self.original_id, invoice_type='384')
            with connection.cursor() as c: constraints=connection.introspection.get_constraints(c,'sedo_invoice')
            for name in ('invoice_unique_active_storno','invoice_unique_active_correction','invoice_unique_gwg_monthly_run'):
                self.assertTrue(constraints[name]['unique'])
