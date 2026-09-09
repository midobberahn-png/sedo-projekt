"""Tests für die lokale Whisper-Transkription (Diktat-Endpoint).

faster-whisper ist in der Testumgebung normalerweise NICHT installiert – genau
das ist der wichtigste Pfad: Der Endpoint muss dann sauber mit 503 und dem
Code ``stt_unavailable`` antworten, damit die Oberfläche auf die
Browser-Spracherkennung zurückfallen kann.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from sedo.services.transcription import stt_status, TranscriptionUnavailable


def _audio(name='diktat.webm', content=b'x' * 2000):
    return SimpleUploadedFile(name, content, content_type='audio/webm')


class TranscriptionEndpointTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_user('u', 'u@x.de', 'pw12345')
        self.client = Client()
        self.client.login(username='u', password='pw12345')
        self.url = reverse('sedo:ai_transcribe')

    def test_get_verboten(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_login_erforderlich(self):
        resp = Client().post(self.url, {'audio': _audio()})
        # LoginRequiredMiddleware leitet zum Login um
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login/', resp['Location'])

    def test_ohne_audio_400(self):
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('kein Audio', resp.json()['error'])

    def test_ohne_faster_whisper_503_mit_code(self):
        """Ohne Installation: klarer 503 + Code für den Frontend-Fallback."""
        if stt_status()['available']:
            self.skipTest('faster-whisper ist hier installiert – Fallback-Pfad nicht testbar.')
        resp = self.client.post(self.url, {'audio': _audio()})
        self.assertEqual(resp.status_code, 503)
        data = resp.json()
        self.assertFalse(data['ok'])
        self.assertEqual(data.get('code'), 'stt_unavailable')

    def test_erfolgsfall_liefert_text(self):
        with patch('sedo.views.transcribe_audio', return_value='Treppenhausreinigung 350 € pauschal'):
            resp = self.client.post(self.url, {'audio': _audio()})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])
        self.assertIn('Treppenhausreinigung', data['text'])

    def test_leere_erkennung_422(self):
        with patch('sedo.views.transcribe_audio', return_value=''):
            resp = self.client.post(self.url, {'audio': _audio()})
        self.assertEqual(resp.status_code, 422)
        self.assertIn('keine Sprache', resp.json()['error'])

    def test_zu_grosses_audio_400(self):
        with patch('sedo.views.transcribe_audio', side_effect=ValueError('Die Aufnahme ist zu lang. Bitte in kürzeren Abschnitten diktieren.')):
            resp = self.client.post(self.url, {'audio': _audio()})
        self.assertEqual(resp.status_code, 400)
        self.assertIn('zu lang', resp.json()['error'])

    def test_unavailable_exception_503(self):
        with patch('sedo.views.transcribe_audio', side_effect=TranscriptionUnavailable('nicht installiert')):
            resp = self.client.post(self.url, {'audio': _audio()})
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json().get('code'), 'stt_unavailable')


class SttStatusTests(TestCase):
    def test_status_struktur(self):
        status = stt_status()
        self.assertIn('available', status)
        if not status['available']:
            self.assertIn('faster-whisper', status['reason'])

    def test_offer_form_erhaelt_stt_flag(self):
        user = get_user_model().objects.create_user('u2', 'u2@x.de', 'pw12345')
        c = Client()
        c.login(username='u2', password='pw12345')
        resp = c.get(reverse('sedo:offer_create'))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('sttAvailable', resp.content.decode())
