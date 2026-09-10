"""Tests für Magic-Tone: Profi-Text-Veredelung + KI-Begleittext.

Der Claude-Aufruf selbst wird gemockt – geprüft werden Endpoint-Verhalten,
Fehlerpfade und vor allem die Datenschutzregel: Der serverseitig gebaute
Begleittext-Kontext darf KEINE Kunden-/Objektdaten enthalten.
"""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from sedo.models import AppSetting, Customer, CustomerObject, Invoice, InvoiceItem
from sedo.services import AIError
from sedo.services.mail import EmailAttachment
from sedo.views import _invoice_ai_context


class _Base(TestCase):
    def setUp(self):
        get_user_model().objects.create_user('u', 'u@x.de', 'pw12345')
        self.client = Client()
        self.client.login(username='u', password='pw12345')
        AppSetting.set('ai_api_key', 'test-key')


class PolishEndpointTests(_Base):
    def setUp(self):
        super().setUp()
        self.url = reverse('sedo:ai_polish_text')

    def test_get_verboten(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_ohne_api_key_400(self):
        AppSetting.set('ai_api_key', '')
        resp = self.client.post(self.url, {'text': 'Server kaputt, extra Stunden'},
                                content_type='application/json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('API-Key', resp.json()['error'])

    def test_zu_kurzer_text_400(self):
        resp = self.client.post(self.url, '{"text": "ab"}', content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    def test_erfolg(self):
        with patch('sedo.views.polish_service_text',
                   return_value='Zusätzlicher Aufwand: Notfall-Systemanalyse nach Systemausfall.'):
            resp = self.client.post(self.url, '{"text": "Mensch, die 2 Stunden extra wegen dem Absturz!"}',
                                    content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Notfall-Systemanalyse', resp.json()['text'])

    def test_ai_fehler_502(self):
        with patch('sedo.views.polish_service_text', side_effect=AIError('Claude ist ausgelastet.')):
            resp = self.client.post(self.url, '{"text": "Treppenhaus sauber machen"}',
                                    content_type='application/json')
        self.assertEqual(resp.status_code, 502)
        self.assertIn('ausgelastet', resp.json()['error'])


class CoverLetterTests(_Base):
    def setUp(self):
        super().setUp()
        self.url = reverse('sedo:ai_cover_letter')
        self.customer = Customer.objects.create(
            name='Streng Geheim GmbH', street='Verborgene Straße 9',
            postcode='39118', city='Magdeburg', email='geheim@kunde.de',
        )
        self.obj = CustomerObject.objects.create(customer=self.customer, name='Geheimobjekt West')
        self.invoice = Invoice.objects.create(
            number='RE-MT-1', customer=self.customer, obj=self.obj,
            gross_total=Decimal('1428.00'), due_date=date(2026, 8, 28),
            period_start=date(2026, 8, 1), period_end=date(2026, 8, 31),
        )
        InvoiceItem.objects.create(invoice=self.invoice, position=1,
                                   description='Unterhaltsreinigung Treppenhaus',
                                   quantity=Decimal('1'), unit='Monat', unit_price=Decimal('1200.00'))

    def test_kontext_enthaelt_keine_kundendaten(self):
        """Datenschutzregel: nur Leistungsdaten in den KI-Kontext."""
        context = '\n'.join(_invoice_ai_context(self.invoice))
        self.assertIn('RE-MT-1', context)
        self.assertIn('Unterhaltsreinigung', context)
        self.assertNotIn('Geheim', context)            # Kundenname + Objektname
        self.assertNotIn('Verborgene', context)        # Adresse
        self.assertNotIn('geheim@kunde.de', context)   # E-Mail

    def test_unbekannte_rechnung_404(self):
        resp = self.client.post(self.url, '{"invoice": 99999}', content_type='application/json')
        self.assertEqual(resp.status_code, 404)

    def test_erfolg(self):
        with patch('sedo.views.generate_cover_letter_text',
                   return_value='Sehr geehrte Damen und Herren, anbei unsere Rechnung RE-MT-1.'):
            resp = self.client.post(self.url, f'{{"invoice": {self.invoice.pk}, "hint": "freundlich"}}',
                                    content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('RE-MT-1', resp.json()['text'])


class EmailBodyOverrideTests(_Base):
    def test_individueller_begleittext_wird_verwendet(self):
        customer = Customer.objects.create(name='K', email='k@x.de')
        invoice = Invoice.objects.create(number='RE-MT-2', customer=customer,
                                         gross_total=Decimal('100.00'))
        captured = {}

        def fake_send(recipient, subject, body, attachment=None):
            captured.update(recipient=recipient, subject=subject, body=body)

        with patch('sedo.views.lese_archivdatei', return_value=EmailAttachment('TEST.pdf', b'TEST Original')), \
             patch('sedo.views.smtp_is_configured', return_value=True), \
             patch('sedo.views.send_email_smtp', side_effect=fake_send):
            resp = self.client.post(reverse('sedo:invoice_email', args=[invoice.pk]),
                                    {'body': 'Individueller KI-Begleittext.'})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(captured['body'], 'Individueller KI-Begleittext.')
