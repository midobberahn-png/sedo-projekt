"""H3: kanonischer Belegzustand getrennt von Zahlung/Mahnung/Anzeige."""
from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from sedo.models import Customer, DunningDocument, Invoice, Payment
from sedo.services.calc import offene_rechnungen, refresh_invoice_payment_status, zuordenbare_rechnungen
from sedo.services.invoice_state import (derived_display_status, derived_payment_state,
                                         guarded_lifecycle_update)
from sedo.services.invoices import (ensure_correction_allowed, ensure_storno_allowed,
                                    ensure_versand_erlaubt, ensure_zahlung_erlaubt,
                                    mark_invoice_corrected)
from sedo.services.workflow_errors import WorkflowError


class InvoiceLifecycleTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name='TEST H3 Kunde', payment_days=14)
        self.today = timezone.localdate()

    def inv(self, number, *, status='erstellt', lifecycle='issued', invoice_type='380', gross='119.00', due=None):
        return Invoice.objects.create(number=number, customer=self.customer, status=status,
            lifecycle=lifecycle, invoice_type=invoice_type, gross_total=gross,
            due_date=due if due is not None else self.today - timedelta(days=20))

    def test_alter_entwurf_beim_neuanlegen_wird_einmalig_in_lifecycle_uebersetzt(self):
        inv = Invoice.objects.create(number='TEST-H3-DRAFT', customer=self.customer, status='entwurf')
        self.assertEqual(inv.lifecycle, 'draft')

    def test_spaetere_statuskorruption_aendert_kanonischen_belegzustand_nicht(self):
        inv = self.inv('TEST-H3-CACHE')
        Invoice.objects.filter(pk=inv.pk).update(status='ueberfaellig')
        inv.refresh_from_db()
        self.assertEqual(inv.lifecycle, 'issued')
        self.assertFalse(inv.is_cancelled)
        ensure_zahlung_erlaubt(inv)

    def test_lifecycle_schutz_erlaubt_stalen_zahlcache_aber_keinen_falschen_belegcache(self):
        inv = self.inv('TEST-H3-CONSTRAINT')
        Invoice.objects.filter(pk=inv.pk).update(lifecycle='cancelled', status='ueberfaellig')
        inv.refresh_from_db()
        self.assertEqual((inv.lifecycle, inv.status), ('cancelled', 'ueberfaellig'))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Invoice.objects.filter(pk=inv.pk).update(lifecycle='issued', status='storniert')

    def test_zahlungszustand_kommt_aus_zahlungen_nicht_status(self):
        inv = self.inv('TEST-H3-PAY', status='bezahlt')
        self.assertEqual(derived_payment_state(inv), 'unpaid')
        Payment.objects.create(invoice=inv, booking_date=self.today, amount='20.00')
        self.assertEqual(derived_payment_state(inv), 'partial')
        Payment.objects.create(invoice=inv, booking_date=self.today, amount='99.00')
        self.assertEqual(derived_payment_state(inv), 'paid')

    def test_displaystatus_priorisiert_lifecycle_vor_zahlung(self):
        inv = self.inv('TEST-H3-CORR', status='korrigiert', lifecycle='corrected')
        Payment.objects.create(invoice=inv, booking_date=self.today, amount='119.00')
        self.assertEqual(derived_display_status(inv), 'korrigiert')

    def test_mahnung_ist_nachweis_und_keine_belegart(self):
        inv = self.inv('TEST-H3-DUN')
        DunningDocument.objects.create(invoice=inv, kind='reminder', document_date=self.today,
                                       due_date_new=self.today + timedelta(days=7), amount_open='119.00')
        self.assertEqual(inv.lifecycle, 'issued')
        self.assertEqual(derived_display_status(inv), 'gemahnt')

    def test_offene_forderungen_nur_ausgestellte_forderungsbelege(self):
        issued = self.inv('TEST-H3-OPEN')
        self.inv('TEST-H3-DRAFT2', status='entwurf', lifecycle='draft')
        self.inv('TEST-H3-CANCEL', status='storniert', lifecycle='cancelled')
        self.inv('TEST-H3-CORRECT', status='korrigiert', lifecycle='corrected')
        self.inv('TEST-H3-STORNO', invoice_type='457', gross='-119.00')
        self.assertEqual(list(offene_rechnungen().values_list('pk', flat=True)), [issued.pk])

    def test_zuordenbare_rechnung_nur_ausgestellte_forderung(self):
        issued = self.inv('TEST-H3-ASSIGN')
        self.inv('TEST-H3-NOASSIGN', status='korrigiert', lifecycle='corrected')
        self.assertEqual(list(zuordenbare_rechnungen().values_list('pk', flat=True)), [issued.pk])

    def test_refresh_zahlstatus_beruehrt_korrigierten_beleg_nicht(self):
        inv = self.inv('TEST-H3-REFRESH', status='korrigiert', lifecycle='corrected')
        Payment.objects.create(invoice=inv, booking_date=self.today, amount='119.00')
        refresh_invoice_payment_status(inv)
        inv.refresh_from_db()
        self.assertEqual((inv.lifecycle, inv.status), ('corrected', 'korrigiert'))

    def test_guarded_lifecycle_update_aendert_beide_kompatibilitaetswerte(self):
        inv = self.inv('TEST-H3-GUARD')
        guarded_lifecycle_update(inv, expected='issued', target='cancelled', legacy_status='storniert')
        self.assertEqual((inv.lifecycle, inv.status), ('cancelled', 'storniert'))

    def test_guarded_lifecycle_update_weist_veralteten_erwartungswert_ab(self):
        inv = self.inv('TEST-H3-RACE')
        guarded_lifecycle_update(inv, expected='issued', target='corrected', legacy_status='korrigiert')
        with self.assertRaises(WorkflowError):
            guarded_lifecycle_update(inv, expected='issued', target='cancelled', legacy_status='storniert')

    def test_fachguards_lesen_lifecycle_statt_legacy_cache(self):
        inv = self.inv('TEST-H3-GUARDS', status='ueberfaellig', lifecycle='cancelled')
        for fn in (ensure_zahlung_erlaubt, ensure_storno_allowed, ensure_correction_allowed):
            with self.subTest(fn=fn.__name__), self.assertRaises(WorkflowError):
                fn(inv)
        # Erneuter Versand eines stornierten Originals bleibt bewusst erlaubt.
        ensure_versand_erlaubt(inv)

    def test_korrekturwechsel_wird_zentral_gesetzt(self):
        inv = self.inv('TEST-H3-MARK')
        mark_invoice_corrected(inv)
        self.assertEqual((inv.lifecycle, inv.status), ('corrected', 'korrigiert'))
