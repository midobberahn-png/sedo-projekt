"""Regression tests use synthetic identities only; never installed user data."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase
from django.urls import reverse
from lxml import etree
from pypdf import PdfReader

from sedo.audit import AuditWriteError
from sedo.company_profile import (
    PROFILE_KEY, SELLER, SellerProfileError, load_profile, save_profile,
    seller_snapshot, validate_profile, initialize_profile,
)
from sedo.core.rechnungslauf_core import InvoiceRow, build_xml, create_pdf
from sedo.models import AppSetting, AuditLog, Customer, CustomerObject, Invoice, InvoiceItem, NumberRange, Offer, OfferItem
from sedo.security import READ_ONLY_GROUP
from sedo.services.invoices import finalisiere_entwurf
from sedo.tests.company_fixtures import SYNTHETIC_PROFILE


def synthetic_row():
    return InvoiceRow(
        invoice_no='TEST-001', object_name='Testobjekt', we='', ae='', nksl='',
        service='Testleistung', net=Decimal('100'), vat_rate=Decimal('19'),
        buyer_name='Synthetischer Testkunde', buyer_street='Kunden-Teststraße 2',
        buyer_postcode='10115', buyer_city='Berlin', buyer_country='DE',
    )


class CompanyProfileTests(TestCase):
    def setUp(self):
        AppSetting.objects.filter(key=PROFILE_KEY).delete()

    def test_missing_profile_does_not_create_pdf(self):
        with TemporaryDirectory() as directory:
            target = Path(directory) / 'test.pdf'
            with self.assertRaises(SellerProfileError):
                create_pdf(synthetic_row(), target, datetime(2026, 1, 1), None, None, datetime(2026, 1, 15))
            self.assertFalse(target.exists())
        self.assertFalse(AppSetting.objects.filter(key=PROFILE_KEY).exists())

    def test_save_roundtrip_normalizes_and_has_no_production_default(self):
        data = {**SYNTHETIC_PROFILE, 'country': 'de', 'iban': 'DE89 3704 0044 0532 0130 00'}
        save_profile(data)
        self.assertEqual(load_profile(), SYNTHETIC_PROFILE)
        AppSetting.objects.filter(key=PROFILE_KEY).delete()
        with self.assertRaises(SellerProfileError):
            _ = SELLER['name']

    def test_invalid_inputs_fail_without_storing_or_echoing_values(self):
        for changes in (
            {'name': ''}, {'name': 'x' * 121}, {'name': 'private\nvalue'},
            {'street': 'hidden\u202evalue'}, {'country': 'ZZ'},
            {'email': 'not-an-address'}, {'iban': 'DE00370400440532013000'},
            {'bic': 'INVALID'}, {'tax_no': '', 'vat_id': ''},
            {'vat_id': '', 'seller_id': ''},
            {'tax_no': '<script>'}, {'vat_id': 'invalid'}, {'unexpected': 'value'},
        ):
            with self.subTest(fields=tuple(changes)):
                with self.assertRaises(SellerProfileError) as raised:
                    save_profile({**SYNTHETIC_PROFILE, **changes})
                self.assertNotIn('private', str(raised.exception))
                self.assertFalse(AppSetting.objects.filter(key=PROFILE_KEY).exists())

    def test_malformed_database_profile_fails_closed(self):
        for raw in ('not-json', '[]', json.dumps({**SYNTHETIC_PROFILE, 'iban': 'invalid'})):
            AppSetting.set(PROFILE_KEY, raw)
            with self.assertRaises(SellerProfileError):
                load_profile()

    def test_xml_pdf_and_metadata_use_one_immutable_snapshot(self):
        save_profile(SYNTHETIC_PROFILE)
        issue, due = datetime(2026, 1, 1), datetime(2026, 1, 15)
        with seller_snapshot() as snapshot, TemporaryDirectory() as directory:
            with self.assertRaises(TypeError):
                snapshot['name'] = 'mutation'
            save_profile({**SYNTHETIC_PROFILE, 'name': 'Andere synthetische Testfirma'})
            xml = etree.fromstring(build_xml(synthetic_row(), issue, None, None, due))
            seller = xml.xpath('//*[local-name()="SellerTradeParty"]/*[local-name()="Name"]/text()')
            self.assertEqual(seller, [SYNTHETIC_PROFILE['name']])
            target = Path(directory) / 'test.pdf'
            create_pdf(synthetic_row(), target, issue, None, None, due)
            self.assertIn(SYNTHETIC_PROFILE['name'], PdfReader(target).pages[0].extract_text())
        self.assertEqual(SELLER['name'], 'Andere synthetische Testfirma')

    def test_thread_snapshots_do_not_leak(self):
        barrier = Barrier(2)
        def read_name(name):
            with seller_snapshot({**SYNTHETIC_PROFILE, 'name': name}):
                barrier.wait(timeout=5)
                return SELLER['name']
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(read_name, 'Synthetische Firma A')
            second = pool.submit(read_name, 'Synthetische Firma B')
            self.assertEqual(first.result(), 'Synthetische Firma A')
            self.assertEqual(second.result(), 'Synthetische Firma B')

    def test_missing_profile_prevents_finalization_without_mutation(self):
        customer = Customer.objects.create(name='Synthetischer Testkunde')
        invoice = Invoice.objects.create(number='TEST-DRAFT-001', customer=customer, lifecycle='draft')
        InvoiceItem.objects.create(invoice=invoice, position=1, description='Test', quantity=1, unit_price=10)
        with self.assertRaises(SellerProfileError):
            finalisiere_entwurf(invoice)
        invoice.refresh_from_db()
        self.assertEqual(invoice.lifecycle, 'draft')
        self.assertFalse(invoice.pdf_file)
        self.assertFalse(NumberRange.objects.exists())

    def test_optional_tax_identifiers_are_omitted_instead_of_empty_xml(self):
        issue, due = datetime(2026, 1, 1), datetime(2026, 1, 15)
        for values, expected_scheme in (
            (SYNTHETIC_PROFILE, 'FC'),
            ({**SYNTHETIC_PROFILE, 'tax_no': '', 'vat_id': 'DE000000000'}, 'VA'),
        ):
            with self.subTest(scheme=expected_scheme), seller_snapshot(values):
                xml = etree.fromstring(build_xml(synthetic_row(), issue, None, None, due))
                identifiers = xml.xpath('//*[local-name()="SellerTradeParty"]/*[local-name()="SpecifiedTaxRegistration"]/*[local-name()="ID"]')
                self.assertEqual([node.get('schemeID') for node in identifiers], [expected_scheme])
                self.assertTrue(all(node.text for node in identifiers))

    def test_missing_or_invalid_profile_blocks_document_post_before_any_write(self):
        user = get_user_model().objects.create_user('profile-writer', password='synthetic-test')
        self.client.force_login(user)
        data = {
            'action': 'create', 'name': 'Synthetischer Empfänger',
            'street': 'Testweg 2', 'postcode': '10115', 'city': 'Teststadt', 'country': 'DE',
            'customer_type': 'einmalig', 'issue_date': '2026-10-08',
            'valid_days': '14', 'payment_days': '14', 'vat_rate': '19.00',
            'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '1', 'items-MAX_NUM_FORMS': '1000',
            'items-0-description': 'Testleistung', 'items-0-quantity': '1',
            'items-0-unit': 'Stk.', 'items-0-unit_price': '10.00',
        }
        tables = (Customer, CustomerObject, Invoice, InvoiceItem, Offer, OfferItem, NumberRange, AuditLog)
        for raw in ('', json.dumps({**SYNTHETIC_PROFILE, 'iban': 'invalid'})):
            AppSetting.set(PROFILE_KEY, raw)
            for route in ('sedo:invoice_create', 'sedo:offer_create'):
                with self.subTest(profile=bool(raw), route=route):
                    before = [list(table.objects.order_by('pk').values()) for table in tables]
                    response = self.client.post(reverse(route), data)
                    self.assertRedirects(response, reverse('sedo:company_setup'))
                    self.assertEqual([list(table.objects.order_by('pk').values()) for table in tables], before)

    def test_unconfigured_installation_routes_to_company_setup_without_writing_documents(self):
        user = get_user_model().objects.create_user('profile-form', password='synthetic-test')
        self.client.force_login(user)
        for route in ('sedo:invoice_create', 'sedo:offer_create'):
            self.assertRedirects(self.client.get(reverse(route)), reverse('sedo:company_setup'))
        self.assertFalse(NumberRange.objects.exists())

    def test_first_run_requires_admin_and_valid_company_before_business_pages(self):
        admin = get_user_model().objects.create_user('setup-admin', password='synthetic-test', is_staff=True)
        employee = get_user_model().objects.create_user('setup-employee', password='synthetic-test')
        url = reverse('sedo:company_setup')
        self.client.force_login(employee)
        self.assertRedirects(self.client.get(reverse('sedo:dashboard')), url)
        self.assertContains(self.client.get(url), 'Administrationskonto')
        self.assertEqual(self.client.post(url, SYNTHETIC_PROFILE).status_code, 403)
        self.assertFalse(AppSetting.objects.filter(key=PROFILE_KEY).exists())
        self.client.force_login(admin)
        self.assertContains(self.client.get(url), 'Firmendaten einrichten')
        invalid = self.client.post(url, {**SYNTHETIC_PROFILE, 'iban': 'ungültig'})
        self.assertContains(invalid, 'IBAN: Format oder Prüfsumme ist ungültig.')
        self.assertIn('iban', invalid.context['company_form'].errors)
        self.assertFalse(AppSetting.objects.filter(key=PROFILE_KEY).exists())
        self.assertRedirects(self.client.post(url, SYNTHETIC_PROFILE), reverse('sedo:dashboard'))
        self.assertEqual(load_profile(), SYNTHETIC_PROFILE)
        self.assertRedirects(self.client.get(url), reverse('sedo:dashboard'))
        self.assertEqual(self.client.get(reverse('sedo:dashboard')).status_code, 200)

    def test_setup_is_blank_and_recovery_remains_accessible(self):
        user = get_user_model().objects.create_user('setup-recovery', password='synthetic-test', is_staff=True)
        self.client.force_login(user)
        response = self.client.get(reverse('sedo:company_setup'))
        for field in response.context['company_form']:
            self.assertFalse(field.value())
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(self.client.get(reverse('sedo:backup')).status_code, 200)
        self.assertEqual(self.client.get(reverse('sedo:readiness')).status_code, 200)

    def test_setup_cannot_overwrite_a_profile_saved_after_initial_check(self):
        user = get_user_model().objects.create_user('setup-race', password='synthetic-test', is_staff=True)
        self.client.force_login(user)
        existing = {**SYNTHETIC_PROFILE, 'name': 'Bereits eingerichtete Testfirma'}
        save_profile(existing)
        before = AuditLog.objects.count()
        with patch('sedo.views.load_profile', side_effect=SellerProfileError('Simulierter früherer Stand')):
            response = self.client.post(reverse('sedo:company_setup'), SYNTHETIC_PROFILE)
        self.assertRedirects(response, reverse('sedo:dashboard'))
        self.assertEqual(load_profile(), existing)
        self.assertEqual(AuditLog.objects.count(), before)

    def test_setup_audit_failure_rolls_back_profile_and_allows_retry(self):
        user = get_user_model().objects.create_user('setup-audit-retry', password='synthetic-test', is_staff=True)
        self.client.force_login(user)
        AppSetting.set('auto_weekly_backup_enabled', '0')
        url = reverse('sedo:company_setup')
        before = AuditLog.objects.count()
        with patch('sedo.views.write_audit', side_effect=AuditWriteError('Synthetischer Protokollfehler')) as audit:
            response = self.client.post(url, SYNTHETIC_PROFILE)
        self.assertTrue(audit.call_args.kwargs['pflicht'])
        self.assertContains(response, 'Die Einrichtung konnte nicht sicher protokolliert werden.')
        self.assertEqual(response.context['company_form']['name'].value(), SYNTHETIC_PROFILE['name'])
        self.assertFalse(AppSetting.objects.filter(key=PROFILE_KEY).exists())
        self.assertEqual(AuditLog.objects.count(), before)
        self.assertRedirects(self.client.get(reverse('sedo:dashboard')), url)
        self.assertRedirects(self.client.post(url, SYNTHETIC_PROFILE), reverse('sedo:dashboard'))
        self.assertEqual(load_profile(), SYNTHETIC_PROFILE)
        self.assertEqual(AuditLog.objects.count(), before + 1)

    def test_initialization_repairs_invalid_profile_but_never_replaces_valid_profile(self):
        AppSetting.set(PROFILE_KEY, 'not-json')
        self.assertTrue(initialize_profile(SYNTHETIC_PROFILE))
        self.assertFalse(initialize_profile({**SYNTHETIC_PROFILE, 'name': 'Andere Testfirma'}))
        self.assertEqual(load_profile(), SYNTHETIC_PROFILE)

    def test_read_only_user_can_see_setup_but_cannot_complete_it(self):
        user = get_user_model().objects.create_user('setup-readonly', password='synthetic-test', is_staff=True)
        user.groups.add(Group.objects.get_or_create(name=READ_ONLY_GROUP)[0])
        self.client.force_login(user)
        url = reverse('sedo:company_setup')
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.post(url, SYNTHETIC_PROFILE).status_code, 403)
        self.assertFalse(AppSetting.objects.filter(key=PROFILE_KEY).exists())

    def test_admin_can_save_profile_and_invalid_form_keeps_previous(self):
        user = get_user_model().objects.create_user('profile-admin', password='synthetic-test', is_staff=True)
        self.client.force_login(user)
        response = self.client.post(reverse('sedo:company_setup'), SYNTHETIC_PROFILE)
        self.assertRedirects(response, reverse('sedo:dashboard'))
        self.assertEqual(load_profile(), SYNTHETIC_PROFILE)
        response = self.client.post(reverse('sedo:settings'), {'action': 'save_company', **SYNTHETIC_PROFILE, 'iban': 'invalid'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'IBAN: Format oder Prüfsumme ist ungültig.')
        self.assertEqual(load_profile(), SYNTHETIC_PROFILE)

    def test_nonadmin_cannot_save_profile(self):
        user = get_user_model().objects.create_user('profile-user', password='synthetic-test')
        self.client.force_login(user)
        response = self.client.post(reverse('sedo:settings'), {'action': 'save_company', **SYNTHETIC_PROFILE})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(AppSetting.objects.filter(key=PROFILE_KEY).exists())

    def test_superuser_profile_form_matches_administrator_policy(self):
        user = get_user_model().objects.create_user('profile-superuser', password='synthetic-test',
                                                    is_superuser=True, is_staff=False)
        self.client.force_login(user)
        self.assertContains(self.client.get(reverse('sedo:company_setup')), 'Firmendaten speichern')
        response = self.client.post(reverse('sedo:company_setup'), SYNTHETIC_PROFILE)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(load_profile(), SYNTHETIC_PROFILE)

    def test_staff_with_read_only_role_cannot_read_or_change_profile(self):
        user = get_user_model().objects.create_user('profile-readonly', password='synthetic-test', is_staff=True)
        user.groups.add(Group.objects.get_or_create(name=READ_ONLY_GROUP)[0])
        self.client.force_login(user)
        url = reverse('sedo:settings')
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {'action': 'save_company', **SYNTHETIC_PROFILE}).status_code, 403)
        self.assertFalse(AppSetting.objects.filter(key=PROFILE_KEY).exists())

    def test_profile_save_requires_csrf_and_does_not_echo_into_audit(self):
        user = get_user_model().objects.create_user('profile-csrf', password='synthetic-test', is_staff=True)
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)
        url = reverse('sedo:company_setup')
        data = SYNTHETIC_PROFILE
        self.assertEqual(client.post(url, data).status_code, 403)
        self.assertFalse(AppSetting.objects.filter(key=PROFILE_KEY).exists())
        self.assertEqual(client.get(url).status_code, 200)
        response = client.post(url, data, HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(load_profile(), SYNTHETIC_PROFILE)
        audit = str(list(AuditLog.objects.values()))
        self.assertNotIn(SYNTHETIC_PROFILE['iban'], audit)
        self.assertNotIn(SYNTHETIC_PROFILE['name'], audit)

    def test_unhandled_profile_error_returns_helpful_400_not_500(self):
        user = get_user_model().objects.create_user('profile-reader', password='synthetic-test', is_staff=True)
        self.client.force_login(user)
        save_profile(SYNTHETIC_PROFILE)
        with patch('sedo.views.get_smtp_config', side_effect=SellerProfileError('Firmendaten fehlen.')):
            response = self.client.get(reverse('sedo:settings'))
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'Firmendaten einrichten', status_code=400)
        self.assertEqual(response['Cache-Control'], 'no-store')
