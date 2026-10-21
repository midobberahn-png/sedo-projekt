"""Tests für die Parallelitäts-Schranken der v3.23.1 (dritter externer Review).

Die ensure_*-Guards prüfen VOR der Transaktion – zwei gleichzeitige Requests
können sie beide passieren. Diese Tests simulieren genau das: Die Guards
werden per Patch außer Kraft gesetzt (so, als hätten beide Requests sie
gleichzeitig passiert) und es wird geprüft, dass die DB-Schranken
(Unique-Constraints, Guarded Updates) den zweiten Vorgang sauber stoppen –
mit WorkflowError statt Duplikat.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from .file_fixtures import isolate_files

from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from sedo.models import Customer, DunningDocument, Invoice, InvoiceItem, Offer, OfferItem
from sedo.services import (
    WorkflowError,
    create_invoice_from_offer,
    create_storno,
    generate_dunning_document,
    mark_invoice_corrected,
)


def _invoice(customer, number, **kwargs):
    defaults = dict(invoice_type='380', status='erstellt',
                    gross_total=Decimal('119.00'), net_total=Decimal('100.00'),
                    vat_amount=Decimal('19.00'))
    defaults.update(kwargs)
    inv = Invoice.objects.create(number=number, customer=customer, **defaults)
    InvoiceItem.objects.create(invoice=inv, position=1, description='Reinigung',
                               quantity=Decimal('1'), unit='pauschal',
                               unit_price=Decimal('100.00'))
    return inv


class ParallelStornoTests(TestCase):
    def setUp(self):
        isolate_files(self)
        self.customer = Customer.objects.create(name='Kunde', street='Weg 1',
                                                postcode='39104', city='Magdeburg')
        self.original = _invoice(self.customer, 'RE2026080401')

    def test_zweites_storno_scheitert_auch_ohne_guard(self):
        # Simuliert zwei parallele Requests, die beide den ensure-Guard
        # passiert haben: der Unique-Constraint stoppt den zweiten.
        create_storno(self.original)
        with patch('sedo.services.invoices.ensure_storno_allowed'):
            with self.assertRaises(WorkflowError):
                create_storno(self.original)
        self.assertEqual(
            Invoice.all_objects.filter(invoice_type='457',
                                       reference_invoice=self.original).count(), 1)

    def test_direkter_doppel_insert_verletzt_constraint(self):
        Invoice.objects.create(number='RE2026080402', customer=self.customer,
                               invoice_type='457', reference_invoice=self.original)
        with self.assertRaises(IntegrityError):
            Invoice.objects.create(number='RE2026080403', customer=self.customer,
                                   invoice_type='457', reference_invoice=self.original)


class ParallelCorrectionTests(TestCase):
    def setUp(self):
        isolate_files(self)
        self.customer = Customer.objects.create(name='Kunde')
        self.original = _invoice(self.customer, 'RE2026080410')

    def test_direkte_doppel_korrektur_verletzt_constraint(self):
        Invoice.objects.create(number='RE2026080411', customer=self.customer,
                               invoice_type='384', reference_invoice=self.original)
        with self.assertRaises(IntegrityError):
            Invoice.objects.create(number='RE2026080412', customer=self.customer,
                                   invoice_type='384', reference_invoice=self.original)

    def test_mark_invoice_corrected_verliert_das_rennen_sauber(self):
        # Erster „Request" hat das Original bereits markiert – der zweite
        # darf mit seinem Guarded UPDATE nicht mehr durchkommen.
        mark_invoice_corrected(self.original)
        with self.assertRaises(WorkflowError):
            mark_invoice_corrected(self.original)
        self.original.refresh_from_db()
        self.assertEqual(self.original.status, 'korrigiert')

    def test_soft_geloeschte_korrektur_blockiert_constraint_nicht(self):
        k = Invoice.objects.create(number='RE2026080413', customer=self.customer,
                                   invoice_type='384', reference_invoice=self.original)
        k.soft_delete()
        neu = Invoice.objects.create(number='RE2026080414', customer=self.customer,
                                     invoice_type='384', reference_invoice=self.original)
        self.assertIsNotNone(neu.pk)


class ParallelOfferInvoicingTests(TestCase):
    def setUp(self):
        isolate_files(self)
        self.customer = Customer.objects.create(name='Kunde', payment_days=14, street='Testweg 1', postcode='10115', city='Teststadt')
        self.offer = Offer.objects.create(number='AG2026080401', customer=self.customer,
                                          status='angenommen', vat_rate=Decimal('19.00'))
        OfferItem.objects.create(offer=self.offer, position=1, description='Leistung',
                                 quantity=Decimal('1'), unit='Stk.',
                                 unit_price=Decimal('100.00'))

    def test_zweite_rechnung_scheitert_auch_ohne_guard(self):
        create_invoice_from_offer(self.offer)
        before = Invoice.all_objects.count()
        with patch('sedo.services.invoices.ensure_offer_invoicing_allowed'):
            with self.assertRaises(WorkflowError):
                create_invoice_from_offer(self.offer)
        # Guarded UPDATE hat verloren → gesamte Transaktion (inkl. zweiter
        # Rechnung und Nummer) zurückgerollt.
        self.assertEqual(Invoice.all_objects.count(), before)
        self.offer.refresh_from_db()
        self.assertEqual(Invoice.all_objects.get(pk=self.offer.linked_invoice_id).number,
                         Invoice.objects.order_by('number').first().number)

    def test_inzwischen_abgelehntes_angebot_scheitert_vor_der_verknuepfung(self):
        # Die geladene Python-Instanz ist noch „angenommen“, die DB wurde
        # zwischenzeitlich abgelehnt. Der abschließende UPDATE muss neu prüfen.
        Offer.objects.filter(pk=self.offer.pk).update(status='abgelehnt')
        with patch('sedo.services.invoices.ensure_offer_invoicing_allowed'):
            with self.assertRaises(WorkflowError):
                create_invoice_from_offer(self.offer)
        self.assertFalse(Invoice.all_objects.exists())
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.status, 'abgelehnt')
        self.assertIsNone(self.offer.linked_invoice_id)


class ParallelDunningTests(TestCase):
    def setUp(self):
        isolate_files(self)
        self.customer = Customer.objects.create(name='Kunde', street='Weg 1',
                                                postcode='39104', city='Magdeburg')
        self.invoice = _invoice(self.customer, 'RE2026080420',
                                status='versendet',
                                # F-68: Parallelität an einer tatsächlich überfälligen Rechnung prüfen.
                                due_date=timezone.localdate() - timedelta(days=1))

    def test_doppelte_erinnerung_scheitert_auch_ohne_guard(self):
        generate_dunning_document(self.invoice, 'reminder')
        with patch('sedo.services.dunning.ensure_dunning_allowed'):
            with self.assertRaises(WorkflowError):
                generate_dunning_document(self.invoice, 'reminder')
        self.assertEqual(DunningDocument.objects.filter(invoice=self.invoice,
                                                        kind='reminder').count(), 1)

    def test_direkter_doppel_insert_verletzt_constraint(self):
        DunningDocument.objects.create(invoice=self.invoice, kind='dunning',
                                       document_date=timezone.localdate(),
                                       amount_open=Decimal('119.00'))
        with self.assertRaises(IntegrityError):
            DunningDocument.objects.create(invoice=self.invoice, kind='dunning',
                                           document_date=timezone.localdate(),
                                           amount_open=Decimal('119.00'))

    def test_erinnerung_und_mahnung_duerfen_nebeneinander_existieren(self):
        generate_dunning_document(self.invoice, 'reminder')
        generate_dunning_document(self.invoice, 'dunning')
        self.assertEqual(DunningDocument.objects.filter(invoice=self.invoice).count(), 2)
