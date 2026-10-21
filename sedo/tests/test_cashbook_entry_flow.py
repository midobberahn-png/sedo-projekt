"""Kassenbuch-Eingabe mit anschließendem Erfassen weiterer Buchungen."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from sedo.models import CashbookEntry


class CashbookEntryFlowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('cashbook-entry-test')
        self.client.force_login(self.user)

    def _data(self, name, *, action=None):
        data = {
            'booking_date': '2026-09-30',
            'receipt_no': 'TEST-1',
            'name': name,
            'text': 'Synthetische Testbuchung',
            'amount': '12.50',
        }
        if action:
            data['action'] = action
        return data

    def test_save_and_new_returns_to_empty_form_for_same_kind(self):
        for kind, label in (('income', 'Einnahme'), ('expense', 'Ausgabe')):
            with self.subTest(kind=kind):
                url = reverse('sedo:cashbook_create', args=[kind])
                form_page = self.client.get(url)
                self.assertContains(form_page, f'Speichern &amp; Neue {label}')
                self.assertContains(form_page, 'name="action" value="save_and_new"')

                response = self.client.post(url, self._data(f'Test {label}', action='save_and_new'))
                self.assertRedirects(response, url)
                entry = CashbookEntry.objects.get(name=f'Test {label}')
                self.assertEqual(entry.kind, kind)
                self.assertEqual(entry.month, '2026-09')

                next_page = self.client.get(url)
                self.assertFalse(next_page.context['form'].is_bound)
                self.assertNotIn('name', next_page.context['form'].initial)
                self.assertNotIn('amount', next_page.context['form'].initial)

    def test_two_successive_entries_are_booked_once_with_new_numbers(self):
        url = reverse('sedo:cashbook_create', args=['income'])
        for name in ('Erster Test', 'Zweiter Test'):
            response = self.client.post(url, self._data(name, action='save_and_new'))
            self.assertRedirects(response, url)
        self.assertEqual(
            list(CashbookEntry.objects.filter(month='2026-09').order_by('booking_no')
                 .values_list('booking_no', 'name')),
            [(1, 'Erster Test'), (2, 'Zweiter Test')],
        )
        self.client.get(url)
        self.assertEqual(CashbookEntry.objects.count(), 2)

    def test_plain_save_still_returns_to_booked_month(self):
        url = reverse('sedo:cashbook_create', args=['expense'])
        response = self.client.post(url, self._data('Testausgabe'))
        self.assertRedirects(response, reverse('sedo:cashbook_list') + '?month=2026-09')
        self.assertEqual(CashbookEntry.objects.get().kind, 'expense')

    def test_invalid_save_and_new_keeps_inputs_without_booking(self):
        url = reverse('sedo:cashbook_create', args=['expense'])
        data = self._data('Unvollständige Testausgabe', action='save_and_new')
        data['amount'] = ''
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Unvollständige Testausgabe')
        self.assertIn('amount', response.context['form'].errors)
        self.assertEqual(CashbookEntry.objects.count(), 0)
