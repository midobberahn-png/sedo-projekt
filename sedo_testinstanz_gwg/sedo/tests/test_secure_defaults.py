"""Tests für die Production-Defaults der v3.22-Härtung.

DEBUG ist standardmäßig AUS; statische Dateien kommen trotzdem über
WhiteNoise, Medien über die eigene Media-Route – die Desktop-Starter
funktionieren also ohne DEBUG.
"""
from pathlib import Path

from django.conf import settings as dj_settings
from django.contrib.auth import get_user_model
from django.test import TestCase


class SecureDefaultTests(TestCase):
    def test_debug_default_im_quelltext_ist_aus(self):
        source = (Path(dj_settings.BASE_DIR) / 'config' / 'settings.py').read_text(encoding='utf-8')
        self.assertIn("os.environ.get('DJANGO_DEBUG', '0')", source)
        self.assertNotIn("os.environ.get('DJANGO_DEBUG', '1')", source)

    def test_whitenoise_ist_aktiv(self):
        self.assertIn('whitenoise.middleware.WhiteNoiseMiddleware', dj_settings.MIDDLEWARE)

    def test_static_wird_ohne_debug_ausgeliefert(self):
        # Testlauf hat DEBUG=False – genau der Produktionsfall der Starter.
        self.assertFalse(dj_settings.DEBUG)
        resp = self.client.get('/static/sedo/app.css')
        self.assertEqual(resp.status_code, 200)

    def test_media_wird_ohne_debug_ausgeliefert(self):
        get_user_model().objects.create_user('u', password='pw12345')
        self.client.login(username='u', password='pw12345')
        media_root = Path(dj_settings.MEDIA_ROOT)
        target = media_root / 'generated' / 'TESTMEDIA'
        target.mkdir(parents=True, exist_ok=True)
        (target / 'probe.txt').write_text('ok', encoding='utf-8')
        resp = self.client.get('/media/generated/TESTMEDIA/probe.txt')
        self.assertEqual(resp.status_code, 200)

    def test_media_ohne_login_wird_zum_login_umgeleitet(self):
        # v2.0.1 (vierter Review): Rechnungs-PDFs enthalten Geschäftsdaten –
        # ohne Login gibt es keinen Dateiabruf, nur den Redirect zum Login.
        media_root = Path(dj_settings.MEDIA_ROOT)
        target = media_root / 'generated' / 'TESTMEDIA'
        target.mkdir(parents=True, exist_ok=True)
        (target / 'geheim.pdf').write_bytes(b'%PDF-1.4 test')
        resp = self.client.get('/media/generated/TESTMEDIA/geheim.pdf')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login/', resp.url)

    def test_static_bleibt_ohne_login_erreichbar(self):
        resp = self.client.get('/static/sedo/app.css')
        self.assertEqual(resp.status_code, 200)

    def test_backup_seite_verlangt_login(self):
        # Widerlegt den Audit-Befund vom 15.08. („backup_page ohne Auth“):
        # @login_required sitzt korrekt UND die Middleware schützt zusätzlich.
        from django.urls import reverse
        resp = self.client.get(reverse('sedo:backup'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login/', resp.url)
        resp = self.client.post(reverse('sedo:backup'), {})
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login/', resp.url)


class GlobalSearchTests(TestCase):
    """Globale Suche (v2.1): gruppierte Treffer, nur mit Login."""

    def setUp(self):
        from sedo.models import Customer, CustomerObject, Invoice, Offer
        from decimal import Decimal
        get_user_model().objects.create_user('u', password='pw12345')
        self.customer = Customer.objects.create(name='Müller Hausverwaltung GmbH',
                                                postcode='39104', city='Magdeburg')
        CustomerObject.objects.create(customer=self.customer, name='Asternweg 2/1',
                                      street='Asternweg 2/1', city='Magdeburg')
        Invoice.objects.create(number='RE2026080901', customer=self.customer,
                               status='versendet', gross_total=Decimal('119.00'))
        Offer.objects.create(number='AG2026080901', customer=self.customer,
                             status='offen', gross_total=Decimal('50.00'))

    def test_suche_verlangt_login(self):
        resp = self.client.get('/api/suche/?q=Müller')
        self.assertEqual(resp.status_code, 302)

    def test_suche_findet_alle_gruppen(self):
        self.client.login(username='u', password='pw12345')
        resp = self.client.get('/api/suche/?q=Müller')
        data = resp.json()
        gruppen = {r['gruppe'] for r in data['results']}
        self.assertIn('Kunden', gruppen)
        self.assertIn('Rechnungen', gruppen)   # über den Kundennamen gefunden
        self.assertIn('Angebote', gruppen)

    def test_suche_nach_rechnungsnummer(self):
        self.client.login(username='u', password='pw12345')
        resp = self.client.get('/api/suche/?q=RE20260809')
        titel = [r['titel'] for r in resp.json()['results']]
        self.assertIn('RE2026080901', titel)

    def test_suche_nach_objekt(self):
        self.client.login(username='u', password='pw12345')
        resp = self.client.get('/api/suche/?q=Asternweg')
        gruppen = {r['gruppe'] for r in resp.json()['results']}
        self.assertIn('Objekte', gruppen)

    def test_kurze_eingabe_liefert_nichts(self):
        self.client.login(username='u', password='pw12345')
        resp = self.client.get('/api/suche/?q=M')
        self.assertEqual(resp.json()['results'], [])
