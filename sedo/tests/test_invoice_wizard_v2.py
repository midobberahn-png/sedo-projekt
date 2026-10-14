"""UX-07: kanonische Vorschau, Eingabeerhalt und unveränderte Speicherwege."""
import json
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from lxml import html

from sedo.models import Customer, CustomerObject, Invoice, Offer, OfferItem
from .file_fixtures import isolate_files
from .test_wizard import _angebot_post


class InvoiceWizardV2Tests(TestCase):
    def setUp(self):
        isolate_files(self)
        self.user = get_user_model().objects.create_user('wizard-v2')
        self.client.force_login(self.user)
        self.customer = Customer.objects.create(name='TEST Müller', street='Testweg 1',
            postcode='12345', city='Teststadt', email='test@example.invalid', payment_days=0)
        self.obj = CustomerObject.objects.create(customer=self.customer, name='TEST Büro', street='Büroweg 2')
        self.other = Customer.objects.create(name='TEST Andere Firma')
        CustomerObject.objects.create(customer=self.other, name='TEST Fremdobjekt')
        self.url = reverse('sedo:invoice_create')

    def data(self, **changes):
        data = _angebot_post(action='draft')
        data.pop('valid_days')
        data.update(payment_days='0', period_start='', period_end='', note='',
                    **{'items-0-leistungsart_id': '', 'items-0-leistungsart_text': ''})
        data.update(changes)
        return data

    def initial(self, response):
        doc = html.fromstring(response.content)
        return json.loads(doc.get_element_by_id('invoice-wizard-initial').text)

    def test_get_mit_kunde_zeigt_echte_stammdaten_und_nur_seine_objekte(self):
        response = self.client.get(self.url, {'customer': self.customer.pk})
        initial = self.initial(response)
        self.assertEqual(initial['customer']['street'], 'Testweg 1')
        self.assertEqual(initial['customer']['payment_days'], 0)
        self.assertEqual([o['id'] for o in initial['customer']['objects']], [self.obj.pk])
        self.assertEqual(Invoice.objects.count(), 0)
        self.assertEqual(Customer.objects.count(), 2)

    def test_ohne_auswahl_bleiben_manuelle_felder_ohne_js_zugaenglich(self):
        response = self.client.get(self.url)
        self.assertIsNone(self.initial(response)['customer'])
        doc = html.fromstring(response.content)
        for field in ('customer-fields', 'object-fields'):
            el = doc.get_element_by_id(field)
            self.assertNotIn('hidden', el.attrib)
            self.assertFalse(el.xpath('ancestor::*[@hidden]'))
        for step in doc.xpath('//*[@data-wizard-step]'):
            self.assertNotIn('hidden', step.attrib)
        self.assertNotContains(response, 'Ohne JavaScript:')

    def test_post_fehler_erhaelt_manuelle_daten_und_abo_auswahl(self):
        response = self.client.post(self.url, self.data(issue_date='', name='TEST Eingabe',
            save_as_recurring='1', recurring_title='TEST Monat', object_street='TEST Eingabeort'))
        self.assertEqual(response.status_code, 200)
        doc = html.fromstring(response.content)
        self.assertEqual(doc.get_element_by_id('id_name').value, 'TEST Eingabe')
        self.assertEqual(doc.get_element_by_id('id_object_street').value, 'TEST Eingabeort')
        self.assertEqual(doc.xpath('//input[@name="recurring_title"]')[0].value, 'TEST Monat')
        self.assertIn('checked', doc.xpath('//input[@name="save_as_recurring"]')[0].attrib)
        self.assertIn('open', doc.xpath('//details[contains(@class,"wizard-recurring")]')[0].attrib)
        self.assertEqual(Invoice.objects.count(), 0)

    def test_gebundene_kopffelder_bleiben_erhalten_snapshot_nutzt_stammdaten(self):
        response = self.client.post(self.url, self.data(customer=self.customer.pk,
            name='Nicht verwendeter manueller Name', email='ungueltig', issue_date=''))
        self.assertEqual(self.initial(response)['customer']['name'], 'TEST Müller')
        doc = html.fromstring(response.content)
        self.assertEqual(doc.get_element_by_id('id_name').value, 'Nicht verwendeter manueller Name')
        self.assertTrue(doc.get_element_by_id('customer-fields').xpath('.//*[@class="errorlist"]'))
        self.assertTrue(self.initial(response)['bound'])

    def test_ungueltige_kundenauswahl_zeigt_fehler_statt_serverfehler(self):
        for value in ('nicht-numerisch', '99999999999999999999999999'):
            response = self.client.post(self.url, self.data(customer=value))
            self.assertEqual(response.status_code, 200)
            self.assertIsNone(self.initial(response)['customer'])
            self.assertContains(response, 'id_customer_error')

    def test_snapshot_maskiert_html_und_script_in_stammdaten(self):
        self.customer.name = '</script><script>alert(1)</script>'
        self.customer.save()
        response = self.client.get(self.url, {'customer': self.customer.pk})
        self.assertEqual(self.initial(response)['customer']['name'], self.customer.name)
        self.assertNotContains(response, self.customer.name)
        self.assertContains(response, '\\u003C/script\\u003E')

    def test_angebotsbezug_und_positionen_bleiben_vorbelegt(self):
        offer = Offer.objects.create(number='TEST-AG-UX7', customer=self.customer, obj=self.obj)
        OfferItem.objects.create(offer=offer, description='TEST Reinigung', quantity=1, unit_price=50)
        response = self.client.get(self.url, {'from_offer': offer.pk})
        self.assertEqual(self.initial(response)['object_id'], str(self.obj.pk))
        self.assertContains(response, 'TEST Reinigung')
        self.assertContains(response, 'data-wizard-unlocked')
        self.assertContains(response, 'name="source_offer"')
        self.assertEqual(Invoice.objects.count(), 0)

    def test_korrekturbezug_bleibt_erhalten(self):
        inv = Invoice.objects.create(number='TEST-RE-UX7', customer=self.customer, obj=self.obj, gross_total=100)
        response = self.client.get(self.url, {'correction_of': inv.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.initial(response)['object_id'], str(self.obj.pk))
        self.assertContains(response, 'name="correction_of"')
        inv.refresh_from_db()
        self.assertEqual(inv.lifecycle, 'issued')

    def test_entwurf_speichert_weiterhin_manuellen_empfaenger_und_zahlungsziel_null(self):
        response = self.client.post(self.url, self.data())
        self.assertEqual(response.status_code, 302)
        inv = Invoice.objects.get()
        self.assertEqual(inv.lifecycle, 'draft')
        self.assertTrue(inv.number.startswith('EN'))
        self.assertEqual(inv.due_date, inv.issue_date)
        self.assertEqual(inv.customer.payment_days, 0)
        self.assertFalse(inv.xml_file)

    def test_erstellen_verwendet_gespeicherte_stammdaten(self):
        response = self.client.post(self.url, self.data(action='create', customer=self.customer.pk,
            obj=self.obj.pk, name='NICHT verwenden', object_name='NICHT verwenden'))
        self.assertEqual(response.status_code, 302)
        inv = Invoice.objects.get()
        self.assertEqual(inv.lifecycle, 'issued')
        self.assertEqual(inv.customer_id, self.customer.pk)
        self.assertEqual(inv.obj_id, self.obj.pk)
        self.assertEqual(Customer.objects.count(), 2)

    def test_email_aktion_liefert_datei_und_keinen_direktversand(self):
        with patch('sedo.views.send_email_smtp') as smtp:
            response = self.client.post(self.url, self.data(action='send', customer=self.customer.pk, obj=self.obj.pk))
        self.assertEqual(response.status_code, 200)
        self.assertIn('.eml', response['Content-Disposition'])
        self.assertIn(b'test@example.invalid', b''.join(response.streaming_content))
        smtp.assert_not_called()
        invoice = Invoice.objects.get()
        self.assertEqual(invoice.lifecycle, 'issued')
        self.assertEqual(invoice.status, 'erstellt')

    def test_abschluss_hat_eine_hauptaktion_und_korrekte_email_beschriftung(self):
        response = self.client.get(self.url)
        doc = html.fromstring(response.content)
        final = doc.xpath('//*[@data-wizard-final]')[0]
        primary = final.xpath('.//button[contains(concat(" ",@class," ")," primary ")]')
        self.assertEqual([el.get('value') for el in primary], ['create'])
        self.assertContains(response, 'Erstellen &amp; E-Mail vorbereiten')
        self.assertNotContains(response, 'Erstellen &amp; versenden')
        self.assertEqual(len(doc.xpath('//*[@data-review-goto]')), 4)
