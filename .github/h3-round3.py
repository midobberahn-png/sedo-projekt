from pathlib import Path


def replace(path, old, new, count=1):
    p = Path(path)
    s = p.read_text()
    actual = s.count(old)
    if actual != count:
        raise SystemExit(f'{path}: Anker {actual} statt {count}')
    p.write_text(s.replace(old, new, count))

# 1) Beleggraphmeldung vor generischem lifecycle-Hinweis.
p = Path('sedo_rechnungstool_v2/sedo/services/invoices.py')
s = p.read_text()
old = """    if original.lifecycle == 'cancelled':
        raise WorkflowError(f'Rechnung {original.number} ist bereits storniert.')
    existing = original.active_storno
    if existing:
        raise WorkflowError(
            f'Zu Rechnung {original.number} existiert bereits die Stornorechnung {existing.number}.')
    # Kreuzprüfung am Belegobjekt statt am Statusfeld.
    correction = original.active_correction
"""
new = """    existing = original.active_storno
    if existing:
        raise WorkflowError(
            f'Zu Rechnung {original.number} existiert bereits die Stornorechnung {existing.number}.')
    if original.lifecycle == 'cancelled':
        raise WorkflowError(f'Rechnung {original.number} ist bereits storniert.')
    # Kreuzprüfung am Belegobjekt statt am Statusfeld.
    correction = original.active_correction
"""
if s.count(old) != 1: raise SystemExit('storno guard anchor')
s = s.replace(old,new,1)
old = """    if original.lifecycle == 'cancelled':
        raise WorkflowError(f'Rechnung {original.number} ist storniert und kann nicht mehr korrigiert werden.')
    existing = original.active_correction
    if existing:
        raise WorkflowError(
            f'Zu Rechnung {original.number} existiert bereits die Korrekturrechnung {existing.number}.')
    # Kreuzprüfung am Belegobjekt statt am Statusfeld.
    storno = original.active_storno
    if storno:
        raise WorkflowError(
            f'Zu Rechnung {original.number} existiert bereits die Stornorechnung '
            f'{storno.number}. Eine stornierte Rechnung wird nicht mehr korrigiert.')
"""
new = """    # Der konkrete Folgebeleg ist informativer als nur der lifecycle-Cache.
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
"""
if s.count(old) != 1: raise SystemExit('correction guard anchor')
p.write_text(s.replace(old,new,1))

# 2) Storno-Audit alte Felder bewahren, lifecycle zusätzlich protokollieren.
p = Path('sedo_rechnungstool_v2/sedo/services/invoices.py')
s = p.read_text()
old = """        before = original.lifecycle
        guarded_lifecycle_update(original, expected='issued', target='cancelled',
                                 legacy_status='storniert')
        write_audit(request, 'update', original,
                    f'Rechnung {original.number}: ausgestellt → storniert', pflicht=True,
                    metadata={'von': before, 'nach': 'cancelled', 'anlass': 'storno'})
"""
new = """        before_lifecycle = original.lifecycle
        before_status = original.status
        guarded_lifecycle_update(original, expected='issued', target='cancelled',
                                 legacy_status='storniert')
        write_audit(request, 'update', original,
                    f'Rechnung {original.number}: ausgestellt → storniert', pflicht=True,
                    metadata={'von': before_status, 'nach': 'storniert',
                              'von_lifecycle': before_lifecycle, 'nach_lifecycle': 'cancelled',
                              'anlass': 'storno'})
"""
if s.count(old) != 1: raise SystemExit('storno audit anchor')
p.write_text(s.replace(old,new,1))

# 3) Belegketten: snapshot-Test darf lifecycle als beabsichtigtes Reparaturfeld ändern.
replace('sedo_rechnungstool_v2/sedo/tests/test_belegketten.py',
"""        for zeile in vorher['Invoice'] + nachher['Invoice']:
            zeile.pop('status')
            zeile.pop('updated_at')
""",
"""        for zeile in vorher['Invoice'] + nachher['Invoice']:
            zeile.pop('status')
            zeile.pop('lifecycle')
            zeile.pop('updated_at')
""")
# draft Folgebeleg explizit ausstellen
replace('sedo_rechnungstool_v2/sedo/tests/test_belegketten.py',
"""                folge.status = 'erstellt'
                folge.save(update_fields=['status'])
""",
"""                folge.status = 'erstellt'
                folge.lifecycle = 'issued'
                folge.save(update_fields=['status', 'lifecycle'])
""")
# Paralleltest schreibt aktuellen kanonischen Zustand mit.
replace('sedo_rechnungstool_v2/sedo/tests/test_belegketten.py',
"""                    schreiber.execute('UPDATE sedo_invoice SET status=? WHERE id=?', ('bezahlt', inv.pk))
                    schreiber.execute('UPDATE sedo_invoice SET status=? WHERE id=?', ('erstellt', folge.pk))
""",
"""                    schreiber.execute('UPDATE sedo_invoice SET status=?, lifecycle=? WHERE id=?', ('bezahlt', 'issued', inv.pk))
                    schreiber.execute('UPDATE sedo_invoice SET status=?, lifecycle=? WHERE id=?', ('erstellt', 'issued', folge.pk))
""")
replace('sedo_rechnungstool_v2/sedo/tests/test_belegketten.py',
"""                    self.assertEqual(schreiber.execute('SELECT status FROM sedo_invoice WHERE id=?',
                                                       (inv.pk,)).fetchone()[0], 'storniert')
""",
"""                    self.assertEqual(schreiber.execute('SELECT status, lifecycle FROM sedo_invoice WHERE id=?',
                                                       (inv.pk,)).fetchone(), ('storniert', 'cancelled'))
""")

# 4) Draft-Workflow künstliche Bestandslage konsistent setzen.
replace('sedo_rechnungstool_v2/sedo/tests/test_draft_workflow.py',
        "Invoice.objects.filter(pk=inv.pk).update(status='entwurf')",
        "Invoice.objects.filter(pk=inv.pk).update(status='entwurf', lifecycle='draft')")

# 5) Dunning guard: protected document states via lifecycle; paid via real Payment.
p=Path('sedo_rechnungstool_v2/sedo/tests/test_dunning_guard.py'); s=p.read_text()
if 'Payment' not in s.split('\n',40)[0:40].__str__():
    s=s.replace('from sedo.models import ', 'from sedo.models import ',1)
# add Payment into existing model import robustly
s=s.replace('Invoice, DunningDocument, AuditLog', 'Invoice, DunningDocument, AuditLog, Payment')
old="""    def test_geschuetzte_status_bleiben_auch_ohne_folgebeleg_gesperrt(self):
        for status in Invoice.GESPERRT_FUER_MAHNUNG:
            with self.subTest(status=status):
                self.assert_guarded(self.new_invoice(status=status))
"""
new="""    def test_geschuetzte_belegzustaende_und_echte_vollzahlung_sind_gesperrt(self):
        for status, lifecycle in (('entwurf','draft'), ('korrigiert','corrected'), ('storniert','cancelled')):
            with self.subTest(status=status):
                self.assert_guarded(self.new_invoice(status=status, lifecycle=lifecycle))
        paid = self.new_invoice(status='bezahlt', lifecycle='issued')
        Payment.objects.create(invoice=paid, amount=paid.gross_total,
                               booking_date=timezone.localdate(), external_id='TEST-H3-PAID-GUARD')
        self.assert_guarded(paid)
"""
if s.count(old)!=1: raise SystemExit('dunning protected test anchor')
s=s.replace(old,new,1)
s=s.replace("Invoice.objects.filter(pk=self.invoice.pk).update(status='entwurf')",
            "Invoice.objects.filter(pk=self.invoice.pk).update(status='entwurf', lifecycle='draft')",1)
p.write_text(s)

# 6) Dunning stages: paid means real payment, protected document state means lifecycle.
p=Path('sedo_rechnungstool_v2/sedo/tests/test_dunning_stages.py'); s=p.read_text()
# ensure Payment import
s=s.replace('DunningDocument, Invoice', 'DunningDocument, Invoice, Payment')
old="""    def test_bezahlte_und_stornierte_rechnungen_sind_keine_kandidaten(self):
        for status in ('bezahlt', 'storniert', 'korrigiert', 'entwurf'):
            self.invoice.status = status
            self.invoice.save(update_fields=['status'])
            self.assertIsNone(next_dunning_kind(self.invoice), status)
"""
new="""    def test_bezahlte_und_gesperrte_belegzustaende_sind_keine_kandidaten(self):
        Payment.objects.create(invoice=self.invoice, amount=self.invoice.gross_total,
                               booking_date=timezone.localdate(), external_id='TEST-H3-STAGE-PAID')
        self.assertIsNone(next_dunning_kind(self.invoice), 'paid')
        Payment.objects.all().delete()
        for status, lifecycle in (('storniert','cancelled'), ('korrigiert','corrected'), ('entwurf','draft')):
            with self.subTest(status=status):
                Invoice.objects.filter(pk=self.invoice.pk).update(status=status, lifecycle=lifecycle)
                self.invoice.refresh_from_db()
                self.assertIsNone(next_dunning_kind(self.invoice), status)
"""
if s.count(old)!=1: raise SystemExit('dunning stages anchor')
p.write_text(s.replace(old,new,1))

# 7) Archive tests: protected legacy names always paired with lifecycle.
p=Path('sedo_rechnungstool_v2/sedo/tests/test_invoice_archive.py'); s=p.read_text()
old="""            with self.subTest(status=status):
                Invoice.objects.filter(pk=inv.pk).update(status=status)
                before = self.state(inv), self.files(), AuditLog.objects.count()
"""
new="""            with self.subTest(status=status):
                lifecycle = {'storniert':'cancelled', 'korrigiert':'corrected'}.get(status, 'issued')
                Invoice.objects.filter(pk=inv.pk).update(status=status, lifecycle=lifecycle)
                inv.refresh_from_db()
                before = self.state(inv), self.files(), AuditLog.objects.count()
"""
if s.count(old)!=1: raise SystemExit('archive all states anchor')
s=s.replace(old,new,1)
s=s.replace("Invoice.objects.filter(pk=inv.pk).update(status='korrigiert')",
            "Invoice.objects.filter(pk=inv.pk).update(status='korrigiert', lifecycle='corrected')",1)
old="""        Invoice.objects.filter(pk=inv.pk).update(status='entwurf')
        before = self.files()
"""
new="""        # Ein geschützter Legacy-Belegstatus kann lifecycle nicht mehr allein überschreiben.
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError), transaction.atomic():
            Invoice.objects.filter(pk=inv.pk).update(status='entwurf')
        Invoice.objects.filter(pk=inv.pk).update(status='ueberfaellig')
        before = self.files()
"""
if s.count(old)!=1: raise SystemExit('archive reset status anchor')
s=s.replace(old,new,1)
p.write_text(s)

# 8) lifecycle own test: non-protected stale cache, protected mismatch has separate constraint test.
replace('sedo_rechnungstool_v2/sedo/tests/test_invoice_lifecycle.py',
        "Invoice.objects.filter(pk=inv.pk).update(status='storniert')",
        "Invoice.objects.filter(pk=inv.pk).update(status='ueberfaellig')",1)
replace('sedo_rechnungstool_v2/sedo/tests/test_invoice_lifecycle.py',
        "self.assertEqual(inv.status, 'storniert')",
        "self.assertEqual(inv.status, 'ueberfaellig')",1)

# 9) workflow locks: synthetic state setup now writes both truths.
p=Path('sedo_rechnungstool_v2/sedo/tests/test_workflow_locks.py'); s=p.read_text()
s=s.replace("self.original.status = 'korrigiert'\n        self.original.save(update_fields=['status'])",
            "self.original.status = 'korrigiert'\n        self.original.lifecycle = 'corrected'\n        self.original.save(update_fields=['status', 'lifecycle'])",1)
s=s.replace("self.original.status = 'erstellt'\n        self.original.save(update_fields=['status'])\n        neu = create_storno(self.original)",
            "self.original.status = 'erstellt'\n        self.original.lifecycle = 'issued'\n        self.original.save(update_fields=['status', 'lifecycle'])\n        neu = create_storno(self.original)",1)
s=s.replace("self.original.status = 'storniert'\n        self.original.save(update_fields=['status'])",
            "self.original.status = 'storniert'\n        self.original.lifecycle = 'cancelled'\n        self.original.save(update_fields=['status', 'lifecycle'])",1)
p.write_text(s)

# 10) Index DB test: bezahlter Legacy-Cache braucht echte Payment-Zeile; expected truth lifecycle/payment.
p=Path('sedo_rechnungstool_v2/sedo/tests/test_invoice_indexes.py'); s=p.read_text()
old="""        cls.null = Invoice.objects.create(number='TEST-IDX-NULL', customer=cls.customer,
                due_date=None, gross_total='119.00')
        cls.hidden = Invoice.objects.create(number='TEST-IDX-HIDDEN', customer=cls.customer,
"""
new="""        paid = next(i for i in cls.invoices if i.status == 'bezahlt')
        Payment.objects.create(invoice=paid, amount=paid.gross_total, booking_date=today,
                               external_id='TEST-IDX-PAID')
        cls.null = Invoice.objects.create(number='TEST-IDX-NULL', customer=cls.customer,
                due_date=None, gross_total='119.00')
        cls.hidden = Invoice.objects.create(number='TEST-IDX-HIDDEN', customer=cls.customer,
"""
if s.count(old)!=1: raise SystemExit('index paid fixture anchor')
s=s.replace(old,new,1)
old="""        expected = {i.pk for i in self.invoices if i.status not in Invoice.GESPERRT_FUER_MAHNUNG}
"""
new="""        expected = {i.pk for i in self.invoices
                    if i.lifecycle == 'issued' and i.payment_state != 'paid' and i.invoice_type != '457'}
"""
if s.count(old)!=1: raise SystemExit('index expected anchor')
s=s.replace(old,new,1)

# Replace historical migration class tail entirely: use historical app models only.
marker="@skipUnless(connection.vendor == 'sqlite', 'SQLite-Migrationsvertrag')\nclass InvoiceIndexMigrationTests(TransactionTestCase):"
head, sep, tail=s.partition(marker)
if not sep: raise SystemExit('index migration class marker')
new_class=r'''@skipUnless(connection.vendor == 'sqlite', 'SQLite-Migrationsvertrag')
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
'''
p.write_text(head+new_class)

# 11) Review F07 paid fixture: old status alone is not payment truth.
p=Path('sedo_rechnungstool_v2/sedo/tests/test_review_v2.py'); s=p.read_text()
# Add real payment wherever this specific test creates status='bezahlt'. Do narrow replacement in test body.
marker='def test_offene_rechnungen_schliesst_belegzustaende_und_stornos_aus(self):'
pre, sep, rest=s.partition(marker)
if not sep: raise SystemExit('review f07 marker')
body, nextsep, after=rest.partition('\n    def ')
needle="Invoice.objects.create(number='RE2026080024'"
if needle in body and "Payment.objects.create" not in body:
    lines=body.splitlines()
    idx=next(i for i,l in enumerate(lines) if needle in l)
# robust generic inject after the paid fixture line block by replacing known status fragment via regex-free exact snippets below
old="""        bezahlt = Invoice.objects.create(number='RE2026080024', customer=self.customer,
            status='bezahlt', gross_total=Decimal('119.00'))
"""
new="""        bezahlt = Invoice.objects.create(number='RE2026080024', customer=self.customer,
            status='bezahlt', gross_total=Decimal('119.00'))
        Payment.objects.create(invoice=bezahlt, amount=Decimal('119.00'), booking_date=timezone.localdate(),
                               external_id='TEST-H3-REVIEW-PAID')
"""
if s.count(old)==1:
    s=s.replace(old,new,1)
else:
    # If numbering differs, locate in target function by status='bezahlt'.
    target=sep+rest
    if "status='bezahlt'" not in target: raise SystemExit('review paid fixture not found')
p.write_text(s)
