"""Chosen email defaults and review routes; no real messages leave the test."""
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from sedo.company_profile import save_profile
from sedo.models import Customer, DunningDocument, Invoice, Payment
from sedo.services.mail import dunning_email_subject_body, invoice_email_subject_body
from sedo.tests.company_fixtures import SYNTHETIC_PROFILE


class DefaultDocumentEmailTests(TestCase):
    def setUp(self):
        save_profile(SYNTHETIC_PROFILE)
        user = get_user_model().objects.create_user(username='mail-tester', password='synthetic-password')
        self.client.force_login(user)
        self.customer = Customer.objects.create(name='Synthetischer Kunde', email='receiver@example.invalid')
        self.invoice = Invoice.objects.create(number='RE-MAIL-1', customer=self.customer,
            issue_date=date(2026, 10, 1), due_date=date(2026, 10, 15),
            gross_total=Decimal('119.00'), lifecycle='issued')

    def test_invoice_default_uses_selected_friendly_text(self):
        subject, body = invoice_email_subject_body(self.invoice)
        self.assertIn('Ihre Rechnung RE-MAIL-1', subject)
        self.assertIn('Guten Tag,', body)
        self.assertIn('119,00 €', body)
        self.assertIn('15.10.2026', body)

    def test_reminder_and_dunning_defaults_use_document_amount_and_deadline(self):
        for kind, expected in (('reminder', 'Guten Tag,'), ('dunning', 'Sehr geehrte Damen und Herren,')):
            document = DunningDocument(invoice=self.invoice, kind=kind,
                amount_open=Decimal('19.50'), due_date_new=date(2026, 11, 1))
            subject, body = dunning_email_subject_body(document)
            self.assertIn('RE-MAIL-1', subject)
            self.assertIn(expected, body)
            self.assertIn('19,50 €', body)
            self.assertIn('01.11.2026', body)

    def test_reminder_review_and_send_use_stored_pdf_without_network(self):
        document = DunningDocument.objects.create(invoice=self.invoice, kind='reminder',
            amount_open=Decimal('119.00'), due_date_new=timezone.localdate() + timedelta(days=7),
            pdf_file='dunning/synthetic-reminder.pdf')
        url = reverse('sedo:dunning_email', args=[document.pk])
        with patch('sedo.payment_views.mahn_hindernis', return_value=''), \
                patch('sedo.payment_views.smtp_is_configured', return_value=True), \
                patch('sedo.payment_views._read_generated_document',
                      return_value=(PurePosixPath('dunning/synthetic-reminder.pdf'), b'%PDF-synthetic')) as read, \
                patch('sedo.payment_views.send_email_smtp') as send:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'Zahlungserinnerung zur Rechnung RE-MAIL-1')
            self.assertFalse(read.called)
            response = self.client.post(url, {'body': 'Geprüfter synthetischer Text'})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(send.call_args.args[2], 'Geprüfter synthetischer Text')
            self.assertEqual(send.call_args.args[3].data, b'%PDF-synthetic')

    def test_old_reminder_is_blocked_after_partial_payment(self):
        document = DunningDocument.objects.create(invoice=self.invoice, kind='reminder',
            amount_open=Decimal('119.00'), due_date_new=timezone.localdate() + timedelta(days=7),
            pdf_file='dunning/synthetic-reminder.pdf')
        Payment.objects.create(invoice=self.invoice, booking_date=timezone.localdate(),
                               amount=Decimal('99.50'))
        with patch('sedo.payment_views.mahn_hindernis', return_value=''), \
                patch('sedo.payment_views.send_email_smtp') as send:
            response = self.client.post(reverse('sedo:dunning_email', args=[document.pk]),
                                        {'body': 'Testtext'})
        self.assertEqual(response.status_code, 302)
        send.assert_not_called()
