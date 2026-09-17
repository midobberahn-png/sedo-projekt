"""UX-06: echte Listenaufrufe, Filter-/Seitenverhalten und sichere Einstiege."""
from datetime import date
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit
from lxml import html

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from sedo.models import Customer, CustomerObject, Invoice, Payment


class InvoiceListV2Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username='archive-test')
        cls.customer = Customer.objects.create(name='TEST Müller & Söhne')
        cls.obj = CustomerObject.objects.create(customer=cls.customer, name='TEST Büro')

    def setUp(self):
        self.client.force_login(self.user)
        self.url = reverse('sedo:invoice_list')

    def invoice(self, number='TEST-RE-001', **kwargs):
        return Invoice.objects.create(number=number, customer=self.customer,
                                      issue_date=date(2026, 9, 17), **kwargs)

    def test_anmeldung_erforderlich(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_lifecycle_ansichten_ignorieren_veralteten_statuscache(self):
        for state in ('draft', 'issued', 'corrected', 'cancelled'):
            self.invoice('TEST-' + state, lifecycle=state)
        for state in ('draft', 'issued', 'corrected', 'cancelled'):
            with self.subTest(state=state):
                response = self.client.get(self.url, {'view': state})
                self.assertEqual([i.lifecycle for i in response.context['invoices']], [state])
                self.assertContains(response, 'aria-current="page"')

    def test_suche_nach_nummer_kunde_und_objekt(self):
        inv = self.invoice(obj=self.obj)
        for query in ('RE-001', 'Müller', 'Büro'):
            with self.subTest(query=query):
                response = self.client.get(self.url, {'q': query})
                self.assertEqual(list(response.context['invoices']), [inv])

    def test_datum_und_lifecycle_kombinierbar(self):
        inv = self.invoice(lifecycle='draft')
        self.invoice('TEST-ANDERE', lifecycle='issued')
        response = self.client.get(self.url, {'year': '2026', 'month': '09', 'view': 'draft'})
        self.assertEqual(list(response.context['invoices']), [inv])
        self.assertEqual(response.context['month'], '9')
        empty = self.client.get(self.url, {'year': '2025'})
        self.assertEqual(empty.context['page_obj'].paginator.count, 0)
        self.assertContains(empty, '<option value="2025" selected>2025 (keine Belege)</option>', html=True)

    def test_seiten_sind_vollstaendig_stabil_und_summe_gilt_fuer_alle_treffer(self):
        for n in range(28):
            self.invoice(f'TEST-RE-{n:03}', gross_total=Decimal('10.00'))
        first = self.client.get(self.url)
        second = self.client.get(self.url, {'page': 2})
        one, two = list(first.context['invoices']), list(second.context['invoices'])
        self.assertEqual((len(one), len(two)), (25, 3))
        self.assertEqual(len({i.pk for i in one + two}), 28)
        self.assertEqual([i.number for i in one + two], sorted([i.number for i in one + two], reverse=True))
        self.assertEqual(second.context['gross_total'], Decimal('280.00'))
        self.assertContains(second, '26–28 von 28')

    def test_links_erhalten_suche_und_datum_ohne_alte_seite(self):
        query = 'Müller & Söhne <test>'
        response = self.client.get(self.url, {'q': query, 'year': '2026', 'month': '9', 'page': '8', 'view': 'draft'})
        for tab in response.context['archive_tabs']:
            params = parse_qs(urlsplit(tab['url']).query)
            self.assertEqual(params['q'], [query])
            self.assertEqual(params['year'], ['2026'])
            self.assertEqual(params['month'], ['9'])
            self.assertNotIn('page', params)
        params = parse_qs(response.context['page_query'])
        self.assertEqual(params['view'], ['draft'])
        self.assertEqual(params['q'], [query])
        self.assertContains(response, '&lt;test&gt;')
        self.assertNotContains(response, '<test>')

    def test_ungueltige_filter_und_seiten_geben_keinen_serverfehler(self):
        inv = self.invoice()
        for value in ('999999999999999999999', '²', '-1', '0', 'abc'):
            with self.subTest(value=value):
                response = self.client.get(self.url, {'year': value, 'month': value, 'page': value, 'view': value, 'status': value})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(list(response.context['invoices']), [inv])
                self.assertFalse(response.context['has_filters'])

    def test_ausgeblendete_belege_fehlen_in_liste_jahren_und_summe(self):
        self.invoice(gross_total='20.00')
        Invoice.objects.create(number='TEST-HIDDEN', customer=self.customer,
                               issue_date=date(2025, 1, 1), gross_total='100.00', deleted_at=timezone.now())
        response = self.client.get(self.url)
        self.assertEqual(response.context['page_obj'].paginator.count, 1)
        self.assertEqual(response.context['gross_total'], Decimal('20.00'))
        self.assertEqual(list(response.context['available_years']), [2026])
        self.assertNotContains(response, 'TEST-HIDDEN')

    def test_entwurf_zeigt_bearbeiten_und_keine_e_rechnungsdateien(self):
        inv = self.invoice(lifecycle='draft', pdf_file='test/draft.pdf', xml_file='test/stale.xml', zugferd_file='test/stale.pdf')
        response = self.client.get(self.url)
        self.assertContains(response, reverse('sedo:invoice_edit', args=[inv.pk]))
        self.assertContains(response, 'Entwurfs-PDF öffnen')
        self.assertNotContains(response, 'test/stale')
        self.assertNotContains(response, reverse('sedo:invoice_finalize', args=[inv.pk]))

    def test_liste_ist_lesend_und_bietet_keine_direkten_fachaktionen(self):
        inv = self.invoice(pdf_file='test/original.pdf', xml_file='test/original.xml', zugferd_file='test/zugferd.pdf')
        before = list(Invoice.all_objects.values())
        response = self.client.get(self.url)
        self.assertEqual(list(Invoice.all_objects.values()), before)
        self.assertContains(response, 'Dokumente')
        for filename in ('test/original.pdf', 'test/original.xml', 'test/zugferd.pdf'):
            self.assertContains(response, filename)
        for action in ('invoice_email', 'invoice_correction', 'invoice_storno', 'invoice_delete'):
            self.assertNotContains(response, reverse('sedo:' + action, args=[inv.pk]))
        archive = html.fromstring(response.content).xpath('//div[@class="archive-page"]')[0]
        self.assertEqual(archive.xpath('.//form[@method="post"]'), [])

    def test_zahlung_weiterhin_aus_aktiven_zahlungen_abgeleitet(self):
        inv = self.invoice(status='bezahlt', gross_total='100.00')
        Payment.objects.create(invoice=inv, booking_date=date(2026, 9, 17), amount='25.00')
        Payment.objects.create(invoice=inv, booking_date=date(2026, 9, 17), amount='75.00', deleted_at=timezone.now())
        response = self.client.get(self.url)
        self.assertContains(response, 'Teilbezahlt')
        self.assertContains(response, '75,00 € offen')
        self.assertNotContains(response, 'payment-paid')

    def test_alte_statuslinks_bleiben_nutzbar_und_filter_sichtbar(self):
        inv = self.invoice(lifecycle='draft')
        response = self.client.get(self.url, {'status': 'entwurf'})
        self.assertEqual(list(response.context['invoices']), [inv])
        self.assertContains(response, 'Übernommener Statusfilter:')
        self.assertEqual(parse_qs(response.context['page_query'])['status'], ['entwurf'])
        self.assertTrue(all('status=' not in tab['url'] for tab in response.context['archive_tabs']))

    def test_leeres_archiv_und_leere_suchergebnisse_unterscheiden_sich(self):
        self.assertContains(self.client.get(self.url), 'Erste Rechnung erstellen')
        self.assertContains(self.client.get(self.url, {'q': 'nicht vorhanden'}), 'Alle Belege anzeigen')

    def test_fehlende_dateien_werden_erklaert_ohne_neuerzeugung(self):
        self.invoice()
        response = self.client.get(self.url)
        self.assertContains(response, 'Keine Dokumente hinterlegt.')
        self.assertNotContains(response, 'neu erzeugen')
