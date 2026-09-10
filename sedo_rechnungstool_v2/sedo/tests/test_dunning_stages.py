"""Tests für das Mahnstufen-Modell der v3.22-Härtung.

Vorher galt ``dunning_documents__isnull=True``: Nach der ersten Erinnerung war
eine Rechnung für immer kein Mahnkandidat mehr – der Ablauf Erinnerung →
(Frist verstreicht) → Mahnung war unmöglich. Jetzt steuert die Art und das
Datum der letzten Mahnstufe die Kandidatenliste.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from sedo.models import Customer, DunningDocument, Invoice, Payment
from .file_fixtures import isolate_files
from sedo.services import (
    WorkflowError,
    ensure_dunning_allowed,
    get_dunning_candidates,
    next_dunning_kind,
    send_bulk_dunning,
)


def _overdue_invoice(customer, number, days_overdue=30):
    today = timezone.localdate()
    return Invoice.objects.create(
        number=number, customer=customer, invoice_type='380', status='versendet',
        issue_date=today - timedelta(days=days_overdue + 14),
        due_date=today - timedelta(days=days_overdue),
        net_total=Decimal('100.00'), vat_amount=Decimal('19.00'),
        gross_total=Decimal('119.00'),
    )


class DunningStageTests(TestCase):
    def setUp(self):
        isolate_files(self)
        self.customer = Customer.objects.create(name='Kunde', street='Weg 1',
                                                postcode='39104', city='Magdeburg')
        self.invoice = _overdue_invoice(self.customer, 'RE2026070001')

    def test_ohne_mahnung_ist_erinnerung_faellig(self):
        self.assertEqual(next_dunning_kind(self.invoice), 'reminder')
        candidates = get_dunning_candidates()
        self.assertEqual([c.pk for c in candidates], [self.invoice.pk])
        self.assertEqual(candidates[0].next_kind, 'reminder')

    def test_nach_erinnerung_mit_laufender_frist_kein_kandidat(self):
        DunningDocument.objects.create(
            invoice=self.invoice, kind='reminder',
            document_date=timezone.localdate(),
            due_date_new=timezone.localdate() + timedelta(days=7),
            amount_open=Decimal('119.00'))
        self.assertIsNone(next_dunning_kind(self.invoice))
        self.assertEqual(get_dunning_candidates(), [])

    def test_nach_abgelaufener_erinnerungsfrist_ist_mahnung_faellig(self):
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
        Payment.objects.create(invoice=self.invoice, amount=self.invoice.gross_total,
                               booking_date=timezone.localdate(), external_id='TEST-H3-STAGE-PAID')
        self.assertIsNone(next_dunning_kind(self.invoice), 'paid')
        Payment.objects.all().delete()
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
            document_date=timezone.localdate(),
            due_date_new=timezone.localdate() + timedelta(days=7),
            amount_open=Decimal('119.00'))
        with self.assertRaises(WorkflowError):
            ensure_dunning_allowed(self.invoice, 'reminder')
        # Mahnung als nächste Stufe bleibt zulässig (manuell jederzeit)
        ensure_dunning_allowed(self.invoice, 'dunning')

    def test_erinnerung_nach_mahnung_wird_blockiert(self):
        DunningDocument.objects.create(
            invoice=self.invoice, kind='dunning',
            document_date=timezone.localdate(),
            due_date_new=timezone.localdate() + timedelta(days=7),
            amount_open=Decimal('119.00'))
        with self.assertRaises(WorkflowError):
            ensure_dunning_allowed(self.invoice, 'reminder')

    def test_view_blockiert_doppelklick(self):
        get_user_model().objects.create_user('u', password='pw12345')
        self.client.login(username='u', password='pw12345')
        url = reverse('sedo:invoice_reminder', args=[self.invoice.pk, 'reminder'])
        self.client.post(url)
        self.client.post(url)
        self.assertEqual(DunningDocument.objects.filter(invoice=self.invoice,
                                                        kind='reminder').count(), 1)

    def test_bulk_auto_waehlt_je_rechnung_die_richtige_stufe(self):
        # Rechnung 1: noch nichts → Erinnerung. Rechnung 2: Erinnerungsfrist
        # abgelaufen → Mahnung. Rechnung 3: Mahnung existiert → übersprungen.
        inv2 = _overdue_invoice(self.customer, 'RE2026070003')
        inv3 = _overdue_invoice(self.customer, 'RE2026070004')
        DunningDocument.objects.create(
            invoice=inv2, kind='reminder',
            document_date=timezone.localdate() - timedelta(days=10),
            due_date_new=timezone.localdate() - timedelta(days=3),
            amount_open=Decimal('119.00'))
        DunningDocument.objects.create(
            invoice=inv3, kind='dunning',
            document_date=timezone.localdate() - timedelta(days=10),
            due_date_new=timezone.localdate() - timedelta(days=3),
            amount_open=Decimal('119.00'))
        result = send_bulk_dunning([self.invoice.pk, inv2.pk, inv3.pk], kind='auto')
        self.assertEqual(result, {'created': 2, 'skipped': 1})
        self.assertEqual(DunningDocument.objects.filter(invoice=self.invoice,
                                                        kind='reminder').count(), 1)
        self.assertEqual(DunningDocument.objects.filter(invoice=inv2,
                                                        kind='dunning').count(), 1)
        self.assertEqual(DunningDocument.objects.filter(invoice=inv3).count(), 1)

    def test_bulk_mit_expliziter_stufe_ueberspringt_duplikate(self):
        DunningDocument.objects.create(
            invoice=self.invoice, kind='reminder',
            document_date=timezone.localdate(),
            due_date_new=timezone.localdate() + timedelta(days=7),
            amount_open=Decimal('119.00'))
        result = send_bulk_dunning([self.invoice.pk], kind='reminder')
        self.assertEqual(result, {'created': 0, 'skipped': 1})
