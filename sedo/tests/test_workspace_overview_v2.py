"""UX-08: aufgabenorientierte Einstiege, Kundenabgrenzung und lesende Ansichten."""
from datetime import timedelta
from decimal import Decimal
from urllib.parse import parse_qs

from lxml import html
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from sedo.models import Customer, CustomerObject, Invoice, Offer, Payment, MailAccount, MailFolder, MailLocation, MailMessage


class WorkspaceOverviewV2Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('workspace-test')
        cls.customer = Customer.objects.create(name='TEST Müller & Sohn', payment_days=0)
        cls.other = Customer.objects.create(name='TEST Fremdkunde')
        cls.obj = CustomerObject.objects.create(customer=cls.customer, name='TEST Büro Nord')

    def setUp(self):
        self.client.force_login(self.user)
        self.url = reverse('sedo:customer_detail', args=[self.customer.pk])
        self.today = timezone.localdate()

    def invoice(self, number='TEST-RE-001', **kwargs):
        kwargs.setdefault('customer', self.customer)
        kwargs.setdefault('gross_total', Decimal('100.00'))
        return Invoice.objects.create(number=number, issue_date=self.today, **kwargs)

    def offer(self, number='TEST-AG-001', **kwargs):
        kwargs.setdefault('customer', self.customer)
        return Offer.objects.create(number=number, issue_date=self.today, **kwargs)

    def page(self, **kwargs):
        return self.client.get(self.url, kwargs)

    def test_ansichten_benoetigen_anmeldung(self):
        self.client.logout()
        for url in (self.url, reverse('sedo:dashboard')):
            self.assertEqual(self.client.get(url).status_code, 302)

    def test_dashboard_mahnlauf_fuehrt_zur_pruefung_ohne_sammelpost(self):
        self.invoice(due_date=self.today - timedelta(days=30))
        response = self.client.get(reverse('sedo:dashboard'))
        self.assertEqual(response.context['dunning_candidates_count'], 1)
        self.assertContains(response, 'Mahnlauf prüfen')
        self.assertNotContains(response, reverse('sedo:bulk_dunning'))
        root = html.fromstring(response.content).xpath('//div[contains(concat(" ", normalize-space(@class), " "), " work-page ")]')[0]
        self.assertEqual([form.get('action') for form in root.xpath('.//form[@method="post"]')],
                         [reverse('sedo:dashboard_layout')])
        link = root.xpath('.//a[.//h3[text()="Mahnstufe prüfen"]]')[0]
        self.assertEqual(link.get('href'), reverse('sedo:open_items'))

    def test_dashboard_entwuerfe_und_eindeutige_monatskennzahl(self):
        draft = self.invoice(lifecycle='draft', status='erstellt')
        self.invoice('TEST-HIDDEN', lifecycle='draft', deleted_at=timezone.now())
        response = self.client.get(reverse('sedo:dashboard'))
        self.assertNotContains(response, 'Entwürfe fertigstellen')
        self.assertContains(response, reverse('sedo:invoice_edit', args=[draft.pk]))
        self.assertContains(response, 'Rechnungsmonat')
        self.assertNotContains(response, 'kein Zahlungseingangsbericht')
        self.assertNotContains(response, 'TEST-HIDDEN')

    def test_dashboard_mail_count_matches_unread_inboxes_and_tracks_read_status(self):
        for email in ('info@seller.example.invalid', 'rechnung@seller.example.invalid'):
            account = MailAccount.objects.create(email=email, username=email, enabled=False)
            for name, role, active, selectable, seen in (
                ('INBOX', 'inbox', True, True, False),
                ('Archive', 'archive', True, True, False),
                ('Trash', 'trash', True, True, False),
                ('Junk', 'junk', True, True, False),
                ('Sent', 'sent', True, True, False),
                ('Old Inbox', 'inbox', False, True, False),
                ('Container', 'inbox', True, False, False),
            ):
                # Distinct messages: a copy of the same message in Junk now
                # correctly excludes that message from the normal mail count.
                import hashlib
                message = MailMessage.objects.create(account=account, digest=hashlib.sha256(name.encode()).hexdigest())
                folder = MailFolder.objects.create(account=account, remote_name=name,
                    name=name, role=role, active=active, selectable=selectable, uidvalidity=1)
                MailLocation.objects.create(folder=folder, message=message, uid=1, uidvalidity=1, seen=seen)
                if name == 'INBOX':
                    MailLocation.objects.create(folder=folder, message=message, uid=2, uidvalidity=1, seen=True)
        response = self.client.get(reverse('sedo:dashboard'))
        self.assertEqual(response.context['unread_mail_count'], 2)
        card = html.fromstring(response.content).xpath('//a[.//h3[text()="Neue E-Mails"]]')[0]
        self.assertEqual(card.get('href'), reverse('sedo:mail_inbox') + '?view=unread')
        inbox = self.client.get(card.get('href'))
        self.assertEqual(inbox.context['page_obj'].paginator.count, 2)
        MailLocation.objects.filter(folder__role='inbox', folder__active=True, folder__selectable=True).update(seen=True)
        response = self.client.get(reverse('sedo:dashboard'))
        self.assertEqual(response.context['unread_mail_count'], 0)
        card = html.fromstring(response.content).xpath('//a[.//h3[text()="Neue E-Mails"]]')[0]
        self.assertEqual(card.xpath('.//span[@class="work-count"]/text()'), ['0'])

    def test_dashboard_without_mailboxes_offers_setup_only_to_staff(self):
        for staff in (False, True):
            self.user.is_staff = staff
            self.user.save(update_fields=['is_staff'])
            response = self.client.get(reverse('sedo:dashboard'))
            self.assertContains(response, 'Noch kein Postfach eingerichtet')
            self.assertNotContains(response, 'keine ungelesenen E-Mails')
            card = html.fromstring(response.content).xpath('//a[.//h3[text()="Neue E-Mails"]]')[0]
            self.assertEqual(card.get('href'), reverse('sedo:mail_settings' if staff else 'sedo:mail_inbox'))

    def test_kundenkennzahlen_folgen_aktiven_forderungen_und_zahlungen(self):
        inv = self.invoice(status='bezahlt', due_date=self.today - timedelta(days=1))
        Payment.objects.create(invoice=inv, booking_date=self.today, amount='25.00')
        Payment.objects.create(invoice=inv, booking_date=self.today, amount='75.00', deleted_at=timezone.now())
        self.invoice('TEST-DRAFT', lifecycle='draft')
        self.invoice('TEST-CORRECTED', lifecycle='corrected')
        self.invoice('TEST-CANCELLED', lifecycle='cancelled')
        self.invoice('TEST-STORNO', invoice_type='457', gross_total='-100.00')
        self.invoice('TEST-HIDDEN', deleted_at=timezone.now())
        self.invoice('TEST-OTHER', customer=self.other)
        paid = self.invoice('TEST-PAID')
        Payment.objects.create(invoice=paid, booking_date=self.today, amount='100.00')
        response = self.page()
        self.assertEqual(response.context['open_total'], Decimal('75.00'))
        self.assertEqual(response.context['open_count'], 1)
        self.assertEqual(response.context['overdue_count'], 1)
        self.assertEqual(response.context['draft_count'], 1)
        self.assertEqual(response.context['invoice_count'], 6)
        self.assertNotContains(response, 'TEST-HIDDEN')
        self.assertNotContains(response, 'TEST-OTHER')

    def test_rechnungsfilter_nutzen_lifecycle_und_aktive_zahlungen(self):
        draft = self.invoice('TEST-DRAFT', lifecycle='draft')
        overdue = self.invoice('TEST-OVERDUE', due_date=self.today - timedelta(days=1))
        current = self.invoice('TEST-CURRENT', due_date=self.today)
        self.invoice('TEST-REPLACED', lifecycle='corrected')
        paid = self.invoice('TEST-PAID')
        Payment.objects.create(invoice=paid, booking_date=self.today, amount='100.00')
        for key, expected in [('draft', {draft.pk}), ('open', {overdue.pk, current.pk}), ('overdue', {overdue.pk})]:
            with self.subTest(key=key):
                response = self.page(section='invoices', filter=key)
                self.assertEqual({i.pk for i in response.context['customer_invoices']}, expected)

    def test_alle_bereiche_bleiben_auf_kunden_begrenzt(self):
        self.invoice()
        self.invoice('TEST-FREMD-RE', customer=self.other)
        self.offer()
        self.offer('TEST-FREMD-AG', customer=self.other)
        CustomerObject.objects.create(customer=self.other, name='TEST-FREMD-OBJEKT')
        for section in ('overview', 'invoices', 'offers', 'objects', 'details'):
            response = self.page(section=section)
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, 'TEST-FREMD')
            root = html.fromstring(response.content)
            active = root.xpath('//nav[@class="work-tabs"]/a[@aria-current="page"]')
            self.assertEqual(len(active), 1)
            self.assertEqual(active[0].get('href'), '?section=' + section)

    def test_ausgeblendete_angebote_fehlen_auch_in_zaehlern(self):
        self.offer('TEST-VISIBLE')
        self.offer('TEST-HIDDEN', deleted_at=timezone.now())
        for section in ('overview', 'offers'):
            response = self.page(section=section)
            self.assertEqual(response.context['offer_count'], 1)
            self.assertContains(response, 'TEST-VISIBLE')
            self.assertNotContains(response, 'TEST-HIDDEN')

    def test_uebersicht_begrenzt_listen_aber_nicht_kennzahlen(self):
        for n in range(8):
            self.invoice(f'TEST-RE-{n:03}')
            self.offer(f'TEST-AG-{n:03}')
        response = self.page()
        self.assertEqual(len(response.context['customer_invoices']), 5)
        self.assertEqual(len(response.context['customer_offers']), 3)
        self.assertEqual(response.context['invoice_count'], 8)
        self.assertEqual(response.context['offer_count'], 8)
        self.assertEqual(response.context['open_total'], Decimal('800.00'))

    def test_seiten_erhalten_filter_und_stabile_reihenfolge(self):
        for n in range(23):
            self.invoice(f'TEST-RE-{n:03}', lifecycle='draft', obj=self.obj)
        one = self.page(section='invoices', filter='draft', q='Büro Nord')
        two = self.page(section='invoices', filter='draft', q='Büro Nord', page=2)
        invoices = list(one.context['customer_invoices']) + list(two.context['customer_invoices'])
        self.assertEqual(len(one.context['customer_invoices']), 20)
        self.assertEqual(len(two.context['customer_invoices']), 3)
        self.assertEqual(len({i.pk for i in invoices}), 23)
        self.assertEqual([i.number for i in invoices], sorted([i.number for i in invoices], reverse=True))
        self.assertEqual(parse_qs(two.context['page_query']), {'section': ['invoices'], 'filter': ['draft'], 'q': ['Büro Nord']})
        self.assertContains(two, '21–23 von 23')

    def test_angebote_und_objekte_sind_durchsuchbar_und_paginiert(self):
        for n in range(23):
            self.offer(f'TEST-AG-{n:03}', obj=self.obj)
            CustomerObject.objects.create(customer=self.customer, name=f'TEST-Lager-{n:03}')
        offers = self.page(section='offers', q='Büro', page=2)
        objects = self.page(section='objects', q='Lager', page=2)
        self.assertEqual(len(offers.context['customer_offers']), 3)
        self.assertEqual(len(objects.context['customer_objects']), 3)
        self.assertEqual(offers.context['page_obj'].paginator.count, 23)
        self.assertEqual(objects.context['page_obj'].paginator.count, 23)

    def test_ungueltige_parameter_und_leere_suche(self):
        self.invoice()
        for value in ('unknown', '-1', '999999999999999999999', '²'):
            with self.subTest(value=value):
                response = self.page(section='invoices', filter=value, page=value)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context['invoice_filter'], 'all')
        response = self.page(section='unknown', q='ignored', page=99)
        self.assertEqual(response.context['section'], 'overview')
        self.assertEqual(response.context['q'], '')
        empty = self.page(section='invoices', q='<script>alert(1)</script> & X')
        self.assertContains(empty, 'Keine passenden Rechnungen')
        self.assertNotContains(empty, '<script>alert(1)</script>')
        self.assertEqual(parse_qs(empty.context['page_query'])['q'], ['<script>alert(1)</script> & X'])

    def test_zustaende_getrennt_und_entwurf_bearbeitbar(self):
        draft = self.invoice('TEST-DRAFT', lifecycle='draft')
        inv = self.invoice('TEST-PARTIAL', status='bezahlt')
        Payment.objects.create(invoice=inv, booking_date=self.today, amount='25.00')
        response = self.page(section='invoices')
        self.assertContains(response, 'lifecycle-issued')
        self.assertContains(response, 'Teilbezahlt')
        self.assertContains(response, '75,00 € offen')
        self.assertContains(response, reverse('sedo:invoice_edit', args=[draft.pk]))
        self.assertNotContains(response, reverse('sedo:invoice_finalize', args=[draft.pk]))

    def test_stammdaten_und_neuanlage_behalten_kundenbezug_und_nulltage(self):
        response = self.page(section='details')
        self.assertContains(response, '0 Tage')
        self.assertContains(response, reverse('sedo:customer_update', args=[self.customer.pk]))
        for route in ('invoice_create', 'offer_create'):
            self.assertContains(response, reverse('sedo:' + route) + '?customer=' + str(self.customer.pk))
        objects = self.page(section='objects')
        self.assertContains(objects, reverse('sedo:object_create') + '?customer=' + str(self.customer.pk))
        root = html.fromstring(objects.content)
        delete = root.xpath('//details[@class="work-object-actions"]/a')[0]
        self.assertEqual(delete.get('href'), reverse('sedo:object_delete', args=[self.obj.pk]))

    def test_get_ansichten_mutieren_keine_belege_oder_zahlungen(self):
        inv = self.invoice(status='bezahlt', due_date=self.today - timedelta(days=30))
        self.offer()
        before = (list(Invoice.all_objects.values()), list(Offer.all_objects.values()), list(Payment.all_objects.values()))
        for section in ('overview', 'invoices', 'offers', 'objects', 'details'):
            response = self.page(section=section)
            root = html.fromstring(response.content).xpath('//div[contains(@class,"customer-file")]')[0]
            self.assertFalse(root.xpath('.//form[@method="post"]'))
            for action in ('invoice_email', 'invoice_correction', 'invoice_storno', 'invoice_add_payment'):
                self.assertNotContains(response, reverse('sedo:' + action, args=[inv.pk]))
        self.client.get(reverse('sedo:dashboard'))
        self.assertEqual(before, (list(Invoice.all_objects.values()), list(Offer.all_objects.values()), list(Payment.all_objects.values())))
