"""Tests für Datei-Atomarität und Monatslauf-Idempotenz (v3.22-Härtung).

Abgedeckt: keine halben PDF/XML/ZUGFeRD-Dateien im ``generated/``-Ordner, wenn
ein Erzeugungsschritt scheitert; der Unique-Constraint gegen doppelte
GWG-Monatsrechnungen; die Nummernvergabe wiederholt NUR bei echten
Nummernkollisionen.
"""
import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.conf import settings as dj_settings
from django.db import IntegrityError
from django.test import TestCase

from sedo.models import Customer, CustomerObject, Invoice, InvoiceItem
from sedo.services import create_numbered_invoice, generate_invoice_files


def _invoice(customer, number):
    inv = Invoice.objects.create(
        number=number, customer=customer, invoice_type='380', status='erstellt',
        gross_total=Decimal('119.00'), net_total=Decimal('100.00'),
        vat_amount=Decimal('19.00'))
    InvoiceItem.objects.create(invoice=inv, position=1, description='Reinigung',
                               quantity=Decimal('1'), unit='pauschal',
                               unit_price=Decimal('100.00'))
    return inv


class AtomicFileGenerationTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name='Kunde', street='Weg 1',
                                                postcode='39104', city='Magdeburg')
        self.invoice = _invoice(self.customer, 'RE2026080201')
        self.out_dir = Path(dj_settings.MEDIA_ROOT) / 'generated' / self.invoice.number
        # Das Test-MEDIA_ROOT überlebt zwischen Tests – Altbestand wegräumen.
        if self.out_dir.exists():
            shutil.rmtree(self.out_dir)

    def test_fehler_bei_zugferd_hinterlaesst_keine_halben_dateien(self):
        with patch('sedo.services.invoices.embed_xml_into_pdf',
                   side_effect=RuntimeError('Simulierter Einbettungsfehler')):
            with self.assertRaises(RuntimeError):
                generate_invoice_files(self.invoice, erstmalig=True)
        # Zielordner existiert nicht bzw. ist leer – PDF und XML wurden NICHT
        # übernommen, obwohl sie im Temp-Verzeichnis schon fertig waren.
        leftover = list(self.out_dir.glob('*')) if self.out_dir.exists() else []
        self.assertEqual(leftover, [])
        self.invoice.refresh_from_db()
        self.assertFalse(self.invoice.zugferd_file)

    def test_erfolgreiche_erzeugung_legt_alle_drei_dateien_ab(self):
        generate_invoice_files(self.invoice, erstmalig=True)
        self.assertTrue((self.out_dir / f'{self.invoice.number}.pdf').exists())
        self.assertTrue((self.out_dir / 'factur-x.xml').exists())
        self.assertTrue((self.out_dir / f'{self.invoice.number}_zugferd.pdf').exists())
        # Kein Temp-Verzeichnis übrig geblieben
        media_root = Path(dj_settings.MEDIA_ROOT)
        self.assertEqual(list(media_root.glob('.generating_*')), [])


class MonthlyRunIdempotencyTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name='GWG')
        self.obj = CustomerObject.objects.create(customer=self.customer, name='Asternweg 2/1')

    def _monthly(self, number, status='erstellt'):
        return Invoice.objects.create(
            number=number, customer=self.customer, obj=self.obj,
            source='GWG Monatsrechnung', status=status,
            period_start=date(2026, 8, 1), period_end=date(2026, 8, 31),
            gross_total=Decimal('119.00'))

    def test_doppelte_monatsrechnung_wird_vom_constraint_verhindert(self):
        self._monthly('RE2026080301')
        with self.assertRaises(IntegrityError):
            self._monthly('RE2026080302')

    def test_nach_storno_ist_neue_monatsrechnung_erlaubt(self):
        self._monthly('RE2026080303', status='storniert')
        neu = self._monthly('RE2026080304')
        self.assertIsNotNone(neu.pk)

    def test_andere_quelle_kollidiert_nicht(self):
        self._monthly('RE2026080305')
        manuell = Invoice.objects.create(
            number='RE2026080306', customer=self.customer, obj=self.obj,
            source='Manuell', period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 31), gross_total=Decimal('50.00'))
        self.assertIsNotNone(manuell.pk)


class NumberRetryScopeTests(TestCase):
    def test_monatslauf_unique_verletzung_wird_nicht_als_nummernkollision_wiederholt(self):
        customer = Customer.objects.create(name='GWG')
        obj = CustomerObject.objects.create(customer=customer, name='Birkenweg 1')
        create_numbered_invoice(
            invoice_type='380', customer=customer, obj=obj,
            source='GWG Monatsrechnung', status='erstellt',
            period_start=date(2026, 8, 1), period_end=date(2026, 8, 31))
        before = Invoice.all_objects.count()
        with self.assertRaises(IntegrityError):
            create_numbered_invoice(
                invoice_type='380', customer=customer, obj=obj,
                source='GWG Monatsrechnung', status='erstellt',
                period_start=date(2026, 8, 1), period_end=date(2026, 8, 31))
        self.assertEqual(Invoice.all_objects.count(), before)
