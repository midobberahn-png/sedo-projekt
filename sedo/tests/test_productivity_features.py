from datetime import timedelta
from decimal import Decimal
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from sedo.company_profile import PROFILE_KEY, save_profile
from sedo.models import AppSetting, Customer, Invoice, Offer, RecurringInvoiceItem, RecurringInvoiceTemplate
from sedo.services.readiness import MANUAL_CHECKS, readiness_report, transport_security_checks
from sedo.tests.company_fixtures import SYNTHETIC_PROFILE


class ProductivityFeatureTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('produktivitaet', password='nur-test')
        cls.customer = Customer.objects.create(name='TEST Produktivität', payment_days=14)

    def setUp(self):
        self.client.force_login(self.user)
        self.today = timezone.localdate()

    def offer(self, **kwargs):
        defaults = {'number': f'AG-TEST-{Offer.all_objects.count() + 1}', 'customer': self.customer, 'status': 'offen', 'issue_date': self.today}
        defaults.update(kwargs)
        return Offer.objects.create(**defaults)

    def test_wiedervorlage_erscheint_in_faelliger_arbeitsliste(self):
        due = self.offer(follow_up_date=self.today, follow_up_note='Anrufen')
        later = self.offer(follow_up_date=self.today + timedelta(days=2))
        response = self.client.get(reverse('sedo:offer_list'), {'view': 'follow_up'})
        self.assertContains(response, due.number)
        self.assertNotContains(response, later.number)

    def test_nachfassen_protokolliert_zeitpunkt_und_neuen_termin(self):
        offer = self.offer()
        next_date = self.today + timedelta(days=7)
        response = self.client.post(reverse('sedo:offer_follow_up', args=[offer.pk]), {
            'action': 'contacted', 'follow_up_date': next_date.isoformat(), 'follow_up_note': 'Neue Unterlagen abwarten',
        })
        self.assertRedirects(response, reverse('sedo:offer_detail', args=[offer.pk]))
        offer.refresh_from_db()
        self.assertEqual(offer.follow_up_date, next_date)
        self.assertIsNotNone(offer.last_follow_up_at)

    def test_entschiedenes_angebot_kann_keine_wiedervorlage_erhalten(self):
        offer = self.offer(status='angenommen')
        self.client.post(reverse('sedo:offer_follow_up', args=[offer.pk]), {'follow_up_date': self.today.isoformat()})
        offer.refresh_from_db()
        self.assertIsNone(offer.follow_up_date)

    def test_abo_vorschau_ist_schreibfrei_und_erkennt_vorhandene_rechnung(self):
        template = RecurringInvoiceTemplate.objects.create(customer=self.customer, title='Pflege', vat_rate=Decimal('19'))
        RecurringInvoiceItem.objects.create(template=template, description='Monatspflege', quantity=2, unit_price=50)
        Invoice.objects.create(customer=self.customer, number='RE-TEST-VORHANDEN', source='Abo Monatsrechnung', period_start=self.today.replace(day=1), period_end=(self.today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1))
        before = (Invoice.objects.count(), Offer.objects.count(), RecurringInvoiceTemplate.objects.count())
        response = self.client.get(reverse('sedo:recurring_monthly'), {'preview_date': self.today.isoformat()})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Vorschaukalender')
        self.assertContains(response, 'vorhanden')
        self.assertEqual((Invoice.objects.count(), Offer.objects.count(), RecurringInvoiceTemplate.objects.count()), before)

    def test_startklar_abnahme_nur_per_post_und_ruecknehmbar(self):
        url = reverse('sedo:readiness_toggle', args=['windows_acceptance'])
        self.assertEqual(self.client.post(url, {'checked': '1'}).status_code, 403)
        self.assertEqual(AppSetting.get('readiness_windows_acceptance', '0'), '0')
        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.post(url, {'checked': '1'})
        self.assertEqual(AppSetting.get('readiness_windows_acceptance'), '1')
        self.client.post(url, {'checked': '0'})
        self.assertEqual(AppSetting.get('readiness_windows_acceptance'), '0')

    @override_settings(SEDO_LOCAL_ONLY=True, SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, SECURE_HSTS_SECONDS=0)
    @patch.dict('os.environ', {'SEDO_LISTEN_HOST': '127.0.0.1'})
    def test_lokaler_einzelplatzbetrieb_bewertet_https_schalter_korrekt(self):
        self.assertTrue(all(item['ok'] for item in transport_security_checks()))

    @override_settings(SEDO_LOCAL_ONLY=True, SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, SECURE_HSTS_SECONDS=0)
    @patch.dict('os.environ', {'SEDO_LISTEN_HOST': ''})
    def test_unbekannter_listener_ist_keine_bestaetigte_lokale_installation(self):
        self.assertFalse(any(item['ok'] for item in transport_security_checks()))

    @override_settings(SEDO_LOCAL_ONLY=True, SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, SECURE_HSTS_SECONDS=0)
    @patch.dict('os.environ', {'SEDO_LISTEN_HOST': '0.0.0.0'})
    def test_alle_schnittstellen_sind_kein_lokaler_listener(self):
        self.assertFalse(any(item['ok'] for item in transport_security_checks()))

    @override_settings(SEDO_LOCAL_ONLY=True, ALLOWED_HOSTS=['rechnung.example.de'], SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, SECURE_HSTS_SECONDS=0)
    def test_externer_host_kann_nicht_als_lokaler_betrieb_durchgehen(self):
        self.assertFalse(any(item['ok'] for item in transport_security_checks()))

    @override_settings(SEDO_LOCAL_ONLY=False, SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, SECURE_HSTS_SECONDS=0)
    def test_netzbetrieb_bleibt_ohne_https_rot(self):
        self.assertFalse(any(item['ok'] for item in transport_security_checks()))

    @override_settings(SEDO_LOCAL_ONLY=False, SECURE_SSL_REDIRECT=True, SESSION_COOKIE_SECURE=True, CSRF_COOKIE_SECURE=True, SECURE_HSTS_SECONDS=3600)
    def test_netzbetrieb_wird_erst_mit_vollstaendigem_https_gruen(self):
        self.assertTrue(all(item['ok'] for item in transport_security_checks()))


@override_settings(DEBUG=False, SEDO_LOCAL_ONLY=True, ALLOWED_HOSTS=['testserver', '127.0.0.1'])
class CompanyReadinessTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user('readiness-admin', password='synthetic-test', is_staff=True)
        self.client.force_login(user)
        AppSetting.set('last_backup_at', timezone.now().isoformat())
        for key in MANUAL_CHECKS:
            AppSetting.set(f'readiness_{key}', '1')

    def company_check(self):
        return next(item for item in readiness_report() if item['key'] == 'company')

    @patch.dict('os.environ', {'SEDO_LISTEN_HOST': '127.0.0.1'})
    def test_missing_profile_is_required_and_prevents_green_readiness(self):
        AppSetting.objects.filter(key=PROFILE_KEY).delete()
        check = self.company_check()
        self.assertFalse(check['ok'])
        self.assertEqual(check['group'], 'Pflicht')
        self.assertEqual(check['href'], reverse('sedo:settings') + '#company-settings')
        response = self.client.get(reverse('sedo:readiness'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['ready'])
        self.assertEqual(response.context['ready_count'], response.context['required_count'] - 1)
        self.assertContains(response, check['href'])
        self.assertNotContains(response, 'Bereit für den Alltag')

    @patch.dict('os.environ', {'SEDO_LISTEN_HOST': '127.0.0.1'})
    def test_malformed_or_invalid_profile_prevents_green_readiness(self):
        for raw in ('not-json', '[]', json.dumps({**SYNTHETIC_PROFILE, 'iban': 'invalid'})):
            with self.subTest(profile_format=raw[:8]):
                AppSetting.set(PROFILE_KEY, raw)
                self.assertFalse(self.company_check()['ok'])
                response = self.client.get(reverse('sedo:readiness'))
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.context['ready'])
                self.assertNotContains(response, SYNTHETIC_PROFILE['iban'])

    @patch.dict('os.environ', {'SEDO_LISTEN_HOST': '127.0.0.1'})
    def test_valid_profile_satisfies_required_check_without_exposing_values(self):
        save_profile(SYNTHETIC_PROFILE)
        self.assertTrue(self.company_check()['ok'])
        response = self.client.get(reverse('sedo:readiness'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['ready'])
        self.assertEqual(response.context['ready_count'], response.context['required_count'])
        self.assertContains(response, 'Bereit für den Alltag')
        self.assertNotContains(response, SYNTHETIC_PROFILE['iban'])
        self.assertNotContains(response, SYNTHETIC_PROFILE['name'])
