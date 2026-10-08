"""Outgoing artifacts use the locally configured company, with synthetic data."""
from datetime import datetime
from email.header import decode_header, make_header
from email.utils import parseaddr
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import TestCase, override_settings
from lxml import etree
from pypdf import PdfReader
from reportlab.pdfgen import canvas

from sedo.company_profile import PROFILE_KEY, save_profile
from sedo.core.rechnungslauf_core import build_xml, create_pdf
from sedo.models import AppSetting, Customer, Offer, OfferItem
from sedo.services.mail import get_smtp_config, send_email_smtp
from sedo.services.pdf import generate_cashbook_pdf, generate_offer_pdf
from sedo.tests.company_fixtures import SYNTHETIC_PROFILE
from sedo.tests.test_company_profile import synthetic_row


class CompanyBrandingTests(TestCase):
    def setUp(self):
        save_profile(SYNTHETIC_PROFILE)

    def test_invoice_offer_and_cashbook_use_own_identity_without_supplier_branding(self):
        image_paths = []
        draw_image = canvas.Canvas.drawImage

        def capture_image(target, image, *args, **kwargs):
            if isinstance(image, (str, Path)):
                image_paths.append(str(image))
            return draw_image(target, image, *args, **kwargs)

        issue, due = datetime(2026, 1, 1), datetime(2026, 1, 15)
        with TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=Path(directory)), \
                patch.object(canvas.Canvas, 'drawImage', capture_image):
            invoice_path = Path(directory) / 'invoice.pdf'
            create_pdf(synthetic_row(), invoice_path, issue, None, None, due)
            customer = Customer.objects.create(name='Synthetischer Kunde')
            offer = Offer.objects.create(number='SYNTHETIC-OFFER', customer=customer)
            OfferItem.objects.create(offer=offer, position=1, description='Testleistung', quantity=1, unit_price=10)
            generate_offer_pdf(offer)
            paths = [invoice_path, Path(directory) / offer.pdf_file.name, generate_cashbook_pdf('2026-01')]
            for path in paths:
                with self.subTest(document=path.name):
                    text = '\n'.join(page.extract_text() for page in PdfReader(path).pages)
                    self.assertIn(SYNTHETIC_PROFILE['name'], text)
                    self.assertIn(SYNTHETIC_PROFILE['email'], text)
                    self.assertNotIn('Sauberes Handwerk', text)
                    self.assertNotIn('SEDO', text)
                    self.assertIn('Vertretung:', text)
                    self.assertNotIn('GF:', text)
        self.assertFalse(any('sedo_logo' in path for path in image_paths))

    def test_xml_representation_note_does_not_assume_company_legal_form(self):
        xml = etree.fromstring(build_xml(synthetic_row(), datetime(2026, 1, 1), None, None, datetime(2026, 1, 15)))
        notes = xml.xpath('//*[local-name()="IncludedNote"]/*[local-name()="Content"]/text()')
        company_note = next(note for note in notes if SYNTHETIC_PROFILE['name'] in note)
        self.assertIn('Vertretung: ' + SYNTHETIC_PROFILE['ceo'], company_note)
        self.assertNotIn('Geschäftsführung:', company_note)
        self.assertNotIn('Register:', company_note)

    def test_smtp_uses_company_when_display_name_is_missing_or_blank(self):
        self.assertEqual(get_smtp_config()['from_name'], SYNTHETIC_PROFILE['name'])
        AppSetting.set('smtp_from_name', '   ')
        self.assertEqual(get_smtp_config()['from_name'], SYNTHETIC_PROFILE['name'])
        self.assertEqual(AppSetting.get('smtp_from_name'), '   ')
        save_profile({**SYNTHETIC_PROFILE, 'name': 'Andere synthetische Firma'})
        self.assertEqual(get_smtp_config()['from_name'], 'Andere synthetische Firma')
        AppSetting.set('smtp_from_name', 'Bewusst eingerichteter Versandname')
        self.assertEqual(get_smtp_config()['from_name'], 'Bewusst eingerichteter Versandname')

    def test_smtp_settings_are_readable_without_company_profile(self):
        AppSetting.objects.filter(key=PROFILE_KEY).delete()
        self.assertEqual(get_smtp_config()['from_name'], '')

    def test_smtp_from_header_preserves_company_punctuation_and_unicode(self):
        company_name = 'Muster, Büro & Service'
        save_profile({**SYNTHETIC_PROFILE, 'name': company_name})
        for key, value in {'host': 'mail.example.invalid', 'user': 'sender@example.invalid'}.items():
            AppSetting.set('smtp_' + key, value)
        with patch('sedo.services.mail.secret_for', return_value='synthetic-secret'), \
                patch('sedo.services.mail.smtplib.SMTP') as smtp:
            send_email_smtp('receiver@example.invalid', 'Test', 'Synthetischer Test')
        message = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
        name, address = parseaddr(message['From'])
        self.assertEqual(str(make_header(decode_header(name))), company_name)
        self.assertEqual(address, 'sender@example.invalid')
