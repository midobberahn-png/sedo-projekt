"""Offline postcode suggestions and form syntax with synthetic addresses."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from sedo.forms import CustomerForm
from sedo.services.postcodes import cities_for_postcode


class PostcodeLookupTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='postal-tester', password='synthetic-password')
        self.client.force_login(self.user)

    def test_known_unknown_and_multiple_localities(self):
        self.assertEqual(cities_for_postcode('10115'), ('Berlin',))
        self.assertGreater(len(cities_for_postcode('06449')), 1)
        self.assertEqual(cities_for_postcode('00000'), ())
        self.assertEqual(cities_for_postcode('ABCDE'), ())

    def test_local_api_and_invalid_input(self):
        url = reverse('sedo:postcode_cities_api')
        self.assertEqual(self.client.get(url, {'postcode': '10115'}).json()['cities'], ['Berlin'])
        self.assertGreater(len(self.client.get(url, {'postcode': '06449'}).json()['cities']), 1)
        self.assertEqual(self.client.get(url, {'postcode': '00000'}).json()['cities'], [])
        self.assertEqual(self.client.get(url, {'postcode': '1234x'}).status_code, 400)

    def test_de_format_is_rejected_but_special_postcode_can_be_checked_manually(self):
        data = {'name': 'Synthetischer Kunde', 'customer_type': 'einmalig',
                'payment_days': 14, 'country': 'DE', 'postcode': '12X45', 'city': 'Berlin'}
        form = CustomerForm(data)
        self.assertFalse(form.is_valid())
        self.assertIn('postcode', form.errors)
        data['postcode'] = '12345'
        self.assertTrue(CustomerForm(data).is_valid())
        data['country'], data['postcode'] = 'FR', '75001'
        self.assertTrue(CustomerForm(data).is_valid())
