from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from sedo.models import Customer, DunningDocument, Invoice, Payment
from sedo.services.dunning import get_dunning_candidates, next_dunning_kind
from sedo.tests.file_fixtures import isolate_files


def _overdue_invoice(customer, number):
    return Invoice.objects.create(
        number=number,
        customer=customer,
        issue_date=timezone.localdate() - timedelta(days=45),
        due_date=timezone.localdate() - timedelta(days=31),
        gross_total=Decimal('119.00'),
        status='ueberfaellig',
    )


class DunningStageTests(TestCase):
    def setUp(self):
        isolate_files(self)
        self.customer = Customer.objects.create(name='Kunde', street='Weg 1',
                                                postcode='39104', city='Magdeburg')
        self.invoice = _overdue_invoice(self.customer, 'RE2026070001')

    def test_ohne_mahndokument_kommt_erinnerung(self):
        self.assertEqual(next_dunning_kind(self.invoice), 'reminder')
        candidates = get_dunning_candidates()
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].next_kind, 'reminder')

    def test_nach_erinnerung_kommt_mahnung(self):
        DunningDocument.objects.create(
            invoice=self.invoice, kind='reminder',
            document_date=timezone.localdate() - timedelta(days=10),
            due_date_new=timezone.localdate() - timedelta(days=3),
            amount_open=Decimal('119.00'))
        self.assertEqual(next_dunning_kind(self.invoice), 'dunning')
        candidates = get_dunning_candidates()
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].next_kind, 'dunning')

    def test_nach_mahnung_keine_weitere_automatische_stufe(self):
        DunningDocument.objects.create(
            invoice=self.invoice, kind='dunning',
            document_date=timezone.localdate() - timedelta(days=10),
            due_date_new=timezone.localdate() - timedelta(days=3),
            amount_open=Decimal('119.00'))
        self.assertIsNone(next_dunning_kind(self.invoice))
        self.assertEqual(get_dunning_candidates(), [])

    def test_bezahlte_und_gesperrte_belegzustaende_sind_keine_kandidaten(self):
        payment = Payment.objects.create(invoice=self.invoice, amount=self.invoice.gross_total,
                                         booking_date=timezone.localdate(),
                                         external_id='TEST-H3-STAGE-PAID')
        self.assertIsNone(next_dunning_kind(self.invoice), 'paid')
        payment.soft_delete()
        for status, lifecycle in (('storniert','cancelled'), ('korrigiert','corrected'), ('entwurf','draft')):
            with self.subTest(status=status):
                Invoice.objects.filter(pk=self.invoice.pk).update(status=status, lifecycle=lifecycle)
                self.invoice.refresh_from_db()
                self.assertIsNone(next_dunning_kind(self.invoice), status)


class DunningDuplicateLockTests(TestCase):
    def setUp(self):
        isolate_files(self)
        self.customer = Customer.objects.create(name='Kunde', street='Weg 1',
                                                postcode='39104', city='Magdeburg')
        self.invoice = _overdue_invoice(self.customer, 'RE2026070002')

    def test_gleiche_stufe_wird_blockiert(self):
        DunningDocument.objects.create(
            invoice=self.invoice, kind='reminder',
            document_date=timezone.localdate(), due_date_new=timezone.localdate() + timedelta(days=7),
            amount_open=Decimal('119.00'))
        self.assertNotEqual(next_dunning_kind(self.invoice), 'reminder')
