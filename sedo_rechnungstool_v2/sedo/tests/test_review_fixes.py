"""Tests für die Nachbesserungen aus dem externen Static-Review (14.08.2026).

Abgedeckt: transaktionaler Aareon-Import (kein Datenverlust bei Importfehler),
atomare Rechnungserzeugung (keine Teilrechnungen bei PDF-Fehlern),
Env-Overrides für Secrets und der SECRET_KEY ohne Code-Fallback.
"""
import os
from decimal import Decimal
from unittest.mock import patch

import pandas as pd
from .file_fixtures import isolate_files

from django.contrib.auth import get_user_model
from django.test import TestCase

from config.settings import _get_secret_key
from sedo.models import AareonMapping, Customer, Invoice, Offer, OfferItem
from sedo.services import create_invoice_from_offer, get_ai_config
from sedo.services.mail import get_smtp_config


class AareonImportRollbackTests(TestCase):
    """Der wichtigste Review-Befund: Löschen vor Import darf bei Fehlern
    nicht den alten Bestand kosten."""

    def setUp(self):
        isolate_files(self)
        AareonMapping.objects.create(billing_unit_sender='Ahornweg 2', service_id='Reinigung',
                                     we_internal='WE1', nksl_internal='N1')
        AareonMapping.objects.create(billing_unit_sender='Ahornweg 2', service_id='Winterdienst',
                                     we_internal='WE1', nksl_internal='N2')

    def _df(self):
        return pd.DataFrame([
            {'Rechnungssteller Abrechnungseinheit': 'Birkenweg 1', 'Leistungsart-ID': 'Reinigung'},
            {'Rechnungssteller Abrechnungseinheit': 'Birkenweg 2', 'Leistungsart-ID': 'Reinigung'},
        ])

    def test_fehler_mitten_im_import_laesst_alten_bestand_intakt(self):
        from sedo import views

        # Seit v2.1 läuft das Schreiben als EIN bulk_create (Audit-Empfehlung);
        # schlägt es fehl, muss die Transaktion das Löschen mit zurückrollen.
        with patch.object(AareonMapping.objects, 'bulk_create',
                          side_effect=RuntimeError('Simulierter DB-Fehler mitten im Import')):
            with self.assertRaises(RuntimeError):
                views._perform_aareon_mapping_import(self._df(), request=None)

        # Transaktion zurückgerollt: der ALTE Bestand ist vollständig da,
        # nichts Halbes vom neuen Import übrig.
        remaining = set(AareonMapping.objects.values_list('billing_unit_sender', 'service_id'))
        self.assertEqual(remaining, {('Ahornweg 2', 'Reinigung'), ('Ahornweg 2', 'Winterdienst')})

    def test_erfolgreicher_import_ersetzt_bestand(self):
        from sedo import views
        with patch('sedo.views.messages'):  # Django-Messages brauchen einen echten Request
            views._perform_aareon_mapping_import(self._df(), request=None)
        remaining = set(AareonMapping.objects.values_list('billing_unit_sender', flat=True))
        self.assertEqual(remaining, {'Birkenweg 1', 'Birkenweg 2'})

    def test_fehlende_pflichtspalten_brechen_vor_dem_loeschen_ab(self):
        from sedo import views
        bad = pd.DataFrame([{'Falsche Spalte': 'x'}])
        with self.assertRaises(ValueError):
            views._perform_aareon_mapping_import(bad, request=None)
        self.assertEqual(AareonMapping.objects.count(), 2)  # unangetastet


class AtomicInvoiceCreationTests(TestCase):
    """Schlägt die Dokumenterzeugung fehl, darf keine Teilrechnung übrig bleiben."""

    def setUp(self):
        isolate_files(self)

    def test_offer_to_invoice_rollt_bei_pdf_fehler_komplett_zurueck(self):
        customer = Customer.objects.create(name='K')
        offer = Offer.objects.create(number='AG-RF-1', customer=customer, status='offen')
        OfferItem.objects.create(offer=offer, position=1, description='Leistung',
                                 quantity=Decimal('1'), unit='Stk.', unit_price=Decimal('100'))
        with patch('sedo.services.invoices.create_pdf', side_effect=RuntimeError('PDF kaputt')):
            with self.assertRaises(RuntimeError):
                create_invoice_from_offer(offer)
        self.assertEqual(Invoice.all_objects.count(), 0)  # keine Teilrechnung
        offer.refresh_from_db()
        self.assertEqual(offer.status, 'offen')           # Angebot unverändert


class SecretHandlingTests(TestCase):
    def test_alter_ai_key_wird_nicht_mehr_verwendet(self):
        os.environ['SEDO_AI_API_KEY'] = 'sk-aus-env'
        try:
            self.assertNotIn('api_key', get_ai_config())
        finally:
            os.environ.pop('SEDO_AI_API_KEY', None)

    def test_smtp_passwort_env_hat_vorrang(self):
        os.environ['SEDO_SMTP_PASSWORD'] = 'env-geheim'
        try:
            self.assertEqual(get_smtp_config()['password'], 'env-geheim')
        finally:
            os.environ.pop('SEDO_SMTP_PASSWORD', None)

    def test_secret_key_env_gewinnt_und_kein_code_fallback(self):
        os.environ['DJANGO_SECRET_KEY'] = 'env-schluessel-123'
        try:
            self.assertEqual(_get_secret_key(), 'env-schluessel-123')
        finally:
            os.environ.pop('DJANGO_SECRET_KEY', None)
        # Ohne Env: erzeugter/persistierter Schlüssel, niemals der alte Code-Fallback
        key = _get_secret_key()
        self.assertNotEqual(key, 'dev-only-change-me-sedo-rechnungstool')
        self.assertGreaterEqual(len(key), 32)
