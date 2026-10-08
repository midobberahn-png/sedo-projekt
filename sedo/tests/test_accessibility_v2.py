"""UX-09: gerenderte Formulare, No-JS-Zugänge und erhaltene POST-Verträge."""
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone
from django.template.loader import render_to_string
from lxml import html
from sedo.models import Customer, Invoice, Offer, Payment, RecurringInvoiceTemplate, ServiceCatalogItem
from .file_fixtures import isolate_files


class AccessibilityV2Tests(TestCase):
    def setUp(self):
        isolate_files(self)
        # This suite also renders administrative configuration/backup forms.
        self.user = get_user_model().objects.create_user('a11y-test', is_staff=True)
        self.client.force_login(self.user)
        self.customer = Customer.objects.create(name='TEST Kunde')
        self.inv = Invoice.objects.create(number='TEST-RE', customer=self.customer, gross_total=100)
        self.offer = Offer.objects.create(number='TEST-AG', customer=self.customer)
        self.payment = Payment.objects.create(booking_date=timezone.localdate(), amount=25)
        self.recurring = RecurringInvoiceTemplate.objects.create(customer=self.customer, title='TEST Abo')
        ServiceCatalogItem.objects.create(name='TEST Reinigung')

    def get(self, name, args=None):
        response = self.client.get(reverse('sedo:' + name, args=args))
        self.assertEqual(response.status_code, 200)
        return html.fromstring(response.content)

    def assert_fields_named(self, doc):
        for field in doc.xpath('//main//input[not(@type="hidden") and not(@type="submit")] | //main//select | //main//textarea'):
            if field.xpath('ancestor::template'):
                continue
            identifier = field.get('id')
            labels = doc.xpath('//label[@for=$id]', id=identifier) if identifier else []
            self.assertTrue(field.get('aria-label') or field.get('aria-labelledby') or labels or field.xpath('ancestor::label'),
                            f'Feld ohne Namen: {field.get("name")} / {identifier}')

    def test_restmodul_formulare_haben_feldnamen(self):
        for route in ('customer_create','object_create','service_catalog_create','recurring_create','settings',
                      'customer_import','aareon_monthly','recurring_monthly','payment_list','cashbook_list',
                      'month_close','audit_log','offer_create','customer_list','offer_list','service_catalog_list'):
            with self.subTest(route=route):
                self.assert_fields_named(self.get(route))

    def test_manuelle_zahlung_zuordnung_und_kasse_haben_labels(self):
        for route, args in [('invoice_add_payment',[self.inv.pk]),('payment_assign',[self.payment.pk]),('cashbook_create',['income'])]:
            with self.subTest(route=route):
                self.assert_fields_named(self.get(route,args))

    def test_formularfehler_sind_sichtbar_und_zugeordnet(self):
        for route in ('customer_create','object_create','service_catalog_create','recurring_create'):
            with self.subTest(route=route):
                response = self.client.post(reverse('sedo:'+route), {'name': ''})
                self.assertEqual(response.status_code, 200)
                doc = html.fromstring(response.content)
                invalid = doc.xpath('//main//*[@aria-invalid="true"]')
                self.assertTrue(invalid)
                for field in invalid:
                    errors = doc.xpath('//*[@id=$id]', id=field.get('id','')+'_error')
                    self.assertTrue(errors, field.get('name'))
                    self.assertFalse(errors[0].xpath('ancestor::*[@hidden]'))

    def test_shell_hat_sprunglink_und_eindeutige_ids(self):
        doc = self.get('dashboard')
        self.assertEqual(doc.xpath('//a[@class="skip-link"]/@href'), ['#main-content'])
        self.assertEqual(doc.xpath('//*[@id="main-content"]/@tabindex'), ['-1'])
        self.assertEqual(doc.xpath('//*[@id="sidebar-toggle"]/@aria-controls'), ['appSidebar'])
        self.assertFalse(doc.xpath('//*[@id="appSidebar"][@hidden or @inert]'))
        ids = doc.xpath('//*[@id]/@id')
        self.assertEqual(len(ids),len(set(ids)))
        self.assertNotIn('sidebar-cover',doc.xpath('//body/@class')[0])

    def test_tabellen_haben_nojs_scrollbereiche(self):
        for route in ('customer_list','offer_list','payment_list','cashbook_list','recurring_monthly',
                      'month_close','service_catalog_list','audit_log','security_status','settings'):
            with self.subTest(route=route):
                tables = self.get(route).xpath('//table[contains(@class,"responsive-records")]')
                self.assertTrue(tables)
                for table in tables:
                    self.assertEqual(table.getparent().get('role'),'region')
                    self.assertEqual(table.getparent().get('tabindex'),'0')
                    self.assertTrue(table.getparent().get('aria-label'))
                    self.assertTrue(table.xpath('.//th[@scope="col"]'))

    def test_meldung_ohne_js_sichtbar_und_html_escaped(self):
        request = RequestFactory().get('/')
        request.session = {}
        request._messages = FallbackStorage(request)
        messages.error(request, '<img src=x onerror=alert(1)> Fehler bleibt sichtbar')
        markup = render_to_string('sedo/base.html', {'messages': request._messages}, request=request)
        box = html.fromstring(markup).xpath('//*[@class="server-messages"]')[0]
        self.assertIn('<img src=x onerror=alert(1)>',box.text_content())
        self.assertFalse(box.xpath('.//img'))
        self.assertFalse(box.xpath('ancestor::*[@hidden]'))

    def test_schreibaktionen_behalten_post_csrf_und_auswahl(self):
        for route,args in [('settings',None),('payment_list',None),('recurring_monthly',None),('aareon_monthly',None),
                           ('month_close',None),('backup',None),('offer_detail',[self.offer.pk])]:
            with self.subTest(route=route):
                forms=self.get(route,args).xpath('//main//form[@method="post"]')
                self.assertTrue(forms)
                for form in forms:
                    self.assertTrue(form.xpath('.//input[@name="csrfmiddlewaretoken"]'))
        doc=self.get('recurring_monthly')
        self.assertEqual(doc.xpath('//form[@data-recurring-selection]/@action'),[reverse('sedo:recurring_generate')])
        self.assertEqual(doc.xpath('//input[@name="template_ids"]/@value'),[str(self.recurring.pk)])
        self.assertFalse(doc.xpath('//input[@name="template_ids"][@checked]'))

    def test_zahlungsloeschung_unter_weiteren_aktionen(self):
        links=self.get('payment_list').xpath('//details[@class="secondary-actions"]/a')
        self.assertEqual([link.get('href') for link in links],[reverse('sedo:payment_delete',args=[self.payment.pk])])

    def test_angebotspositionen_haben_labels_auch_in_neuer_zeile(self):
        doc=self.get('offer_create')
        for prefix in ('items-0','items-__prefix__'):
            for name in ('description','quantity','unit','unit_price','DELETE','long_text','article_no'):
                identifier='id_'+prefix+'-'+name
                self.assertTrue(doc.xpath('//label[@for=$id]', id=identifier), identifier)

    def test_login_hat_viewport_und_labels(self):
        self.client.logout()
        response=self.client.get(reverse('login'))
        doc=html.fromstring(response.content)
        self.assertTrue(doc.xpath('//meta[@name="viewport"]'))
        self.assert_fields_named(doc)
