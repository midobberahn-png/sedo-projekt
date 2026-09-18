from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from sedo.models import AuditLog, Customer, CustomerNote, DunningDocument, Invoice, InvoiceItem, Offer, Payment


class CustomerTimelineTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('chronik')
        cls.customer = Customer.objects.create(name='Chronik Kunde')
        cls.other = Customer.objects.create(name='Fremdkunde')

    def setUp(self):
        self.client.force_login(self.user)
        self.url = reverse('sedo:customer_detail', args=[self.customer.pk])
        self.today = timezone.localdate()

    def test_chronik_buendelt_geschaeftsvorgaenge_und_bleibt_beim_kunden(self):
        invoice = Invoice.objects.create(number='RE-CHRONIK', customer=self.customer, issue_date=self.today, gross_total='119.00')
        foreign = Invoice.objects.create(number='RE-FREMD', customer=self.other, issue_date=self.today)
        Offer.objects.create(number='AG-CHRONIK', customer=self.customer, issue_date=self.today, gross_total='59.50')
        Payment.objects.create(invoice=invoice, booking_date=self.today, amount='20.00', purpose='Teilzahlung')
        DunningDocument.objects.create(invoice=invoice, kind='reminder', document_date=self.today, amount_open='99.00')
        Payment.objects.create(invoice=foreign, booking_date=self.today, amount='1.00', purpose='Fremdzahlung')
        AuditLog.objects.create(action='generate', object_type='Invoice', object_id=str(invoice.pk), object_repr=invoice.number, message='E-Mail-Entwurf (.eml) zur Rechnung erzeugt')
        CustomerNote.objects.create(customer=self.customer, author=self.user, text='Morgen zurückrufen')

        response = self.client.get(self.url, {'section': 'timeline'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'RE-CHRONIK')
        self.assertContains(response, 'AG-CHRONIK')
        self.assertContains(response, 'Teilzahlung')
        self.assertContains(response, 'Zahlungserinnerung')
        self.assertContains(response, 'E-Mail-Entwurf')
        self.assertContains(response, 'Morgen zurückrufen')
        self.assertNotContains(response, 'RE-FREMD')
        self.assertNotContains(response, 'Fremdzahlung')

    def test_chronik_filter_und_ungueltiger_filter(self):
        Invoice.objects.create(number='RE-FILTER', customer=self.customer, issue_date=self.today)
        CustomerNote.objects.create(customer=self.customer, author=self.user, text='Nur diese Notiz')
        filtered = self.client.get(self.url, {'section': 'timeline', 'kind': 'note'})
        self.assertContains(filtered, 'Nur diese Notiz')
        self.assertNotContains(filtered, 'RE-FILTER')
        invalid = self.client.get(self.url, {'section': 'timeline', 'kind': '<script>'})
        self.assertEqual(invalid.context['timeline_kind'], 'all')
        self.assertContains(invalid, 'RE-FILTER')

    def test_notiz_wird_zugeordnet_protokolliert_und_sicher_ausgegeben(self):
        note_url = reverse('sedo:customer_note_create', args=[self.customer.pk])
        self.assertEqual(self.client.get(note_url).status_code, 405)
        response = self.client.post(note_url, {'text': '<script>alert(1)</script> Rückruf'})
        self.assertRedirects(response, self.url + '?section=timeline')
        note = CustomerNote.objects.get()
        self.assertEqual(note.customer, self.customer)
        self.assertEqual(note.author, self.user)
        self.assertTrue(AuditLog.objects.filter(object_type='CustomerNote', object_id=str(note.pk)).exists())
        page = self.client.get(self.url, {'section': 'timeline'})
        self.assertContains(page, '&lt;script&gt;alert(1)&lt;/script&gt; Rückruf')
        self.assertNotContains(page, '<script>alert(1)</script>')

    def test_notiz_erfordert_anmeldung(self):
        self.client.logout()
        response = self.client.post(reverse('sedo:customer_note_create', args=[self.customer.pk]), {'text': 'Nein'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(CustomerNote.objects.count(), 0)


class ReportsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('berichte')
        cls.customer = Customer.objects.create(name='Bericht Kunde')

    def setUp(self):
        self.client.force_login(self.user)
        self.today = timezone.localdate()
        self.url = reverse('sedo:reports')

    def invoice(self, number, **kwargs):
        kwargs.setdefault('customer', self.customer)
        kwargs.setdefault('issue_date', self.today)
        kwargs.setdefault('gross_total', Decimal('100.00'))
        return Invoice.objects.create(number=number, **kwargs)

    def test_umsatz_nutzt_nur_ausgestellte_forderungsbelege(self):
        valid = self.invoice('RE-VALID', gross_total='119.00')
        InvoiceItem.objects.create(invoice=valid, description='Fensterreinigung', quantity='2', unit_price='50.00')
        self.invoice('RE-DRAFT', lifecycle='draft', gross_total='500.00')
        self.invoice('RE-CORRECTED', lifecycle='corrected', gross_total='500.00')
        self.invoice('RE-CANCELLED', lifecycle='cancelled', gross_total='500.00')
        self.invoice('RE-STORNO', invoice_type='457', gross_total='-119.00')

        response = self.client.get(self.url, {'year': self.today.year})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['revenue'], Decimal('119.00'))
        self.assertEqual(response.context['customer_rows'][0]['total'], Decimal('119.00'))
        self.assertEqual(response.context['service_rows'][0]['name'], 'Fensterreinigung')
        self.assertEqual(response.context['service_rows'][0]['total'], Decimal('100.00'))

    def test_forderungsalter_beruecksichtigt_aktive_zahlungen(self):
        not_due = self.invoice('RE-NOT-DUE', due_date=self.today + timedelta(days=3))
        old = self.invoice('RE-OLD', due_date=self.today - timedelta(days=95), gross_total='200.00')
        Payment.objects.create(invoice=old, booking_date=self.today, amount='50.00')
        paid = self.invoice('RE-PAID', due_date=self.today - timedelta(days=40))
        Payment.objects.create(invoice=paid, booking_date=self.today, amount='100.00')
        response = self.client.get(self.url)
        aging = {row['key']: row for row in response.context['aging']}
        self.assertEqual(aging['not_due']['total'], Decimal('100.00'))
        self.assertEqual(aging['over_90']['total'], Decimal('150.00'))
        self.assertEqual(aging['31_60']['count'], 0)
        self.assertEqual(response.context['open_total'], Decimal('250.00'))

    def test_angebotsquote_zaehlt_nur_entschiedene_angebote(self):
        for number, status in (
            ('AG-A', 'angenommen'), ('AG-I', 'in_rechnung'),
            ('AG-R', 'abgelehnt'), ('AG-O', 'offen'),
        ):
            Offer.objects.create(number=number, customer=self.customer, issue_date=self.today, status=status)
        response = self.client.get(self.url)
        self.assertEqual(response.context['offer_quote'], 67)
        self.assertEqual(response.context['accepted_offers'], 2)
        self.assertEqual(response.context['rejected_offers'], 1)
        self.assertEqual(response.context['open_offers'], 1)

    def test_bericht_ist_anmeldepflichtig_und_ungueltiges_jahr_ist_sicher(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.force_login(self.user)
        response = self.client.get(self.url, {'year': '<script>'})
        self.assertEqual(response.context['year'], self.today.year)
        self.assertNotContains(response, '<script>')
