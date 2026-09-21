"""Local-only transport and all four assistant contracts; no model/network needed."""
import copy
import io
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from sedo.models import AppSetting, ServiceCatalogItem
from sedo.services import ai
from sedo.services import ai_local as backend

CFG = {'enabled': True, 'base_url': 'http://127.0.0.1:8080/v1', 'text_model': ''}
MODEL = '/models/Ternary-Bonsai-2-27B-PQ2_0.gguf'
MODELS = {'data': [{'id': MODEL}]}
POSITION = {'titel': 'Glasreinigung', 'leistungstext': 'Reinigung der Fensterflächen.',
            'menge': 12, 'einheit': 'm²', 'einzelpreis': 4.5, 'preis_art': 'genannt',
            'katalog_name': 'Glasreinigung', 'katalog_neu': True, 'artikel_nr': None}
OFFER = {'positionen': [POSITION], 'annahmen': [], 'globale_hinweise': []}


def answer(raw, finish='stop'):
    return {'choices': [{'finish_reason': finish, 'message': {'content': json.dumps(raw)}}]}


class LocalConfigurationTests(TestCase):
    def test_default_local_without_key_and_ignores_legacy_cloud_config(self):
        AppSetting.set('ai_api_key', 'unused-secret')
        AppSetting.set('ai_text_model', 'old-cloud-model')
        with patch.dict(os.environ, {'SEDO_AI_API_KEY': 'unused-environment-secret',
                                    'SEDO_AI_LOCAL_BASE_URL': '', 'SEDO_AI_LOCAL_MODEL': ''}):
            cfg = backend.get_ai_config()
        self.assertTrue(cfg['enabled'])
        self.assertEqual(cfg['base_url'], backend.AI_DEFAULT_BASE_URL)
        self.assertEqual(cfg['text_model'], '')
        self.assertNotIn('api_key', cfg)

    def test_local_environment_overrides_settings(self):
        AppSetting.set('ai_local_base_url', 'http://127.0.0.1:8081/v1')
        with patch.dict(os.environ, {'SEDO_AI_LOCAL_BASE_URL': 'http://localhost:8082',
                                    'SEDO_AI_LOCAL_MODEL': 'bonsai-test'}):
            cfg = backend.get_ai_config()
        self.assertTrue(cfg['endpoint_from_env'])
        self.assertTrue(cfg['model_from_env'])
        self.assertEqual(cfg['base_url'], 'http://localhost:8082')
        self.assertEqual(cfg['text_model'], 'bonsai-test')

    def test_only_loopback_and_fixed_api_path(self):
        for url, expected in [('http://localhost:8080', 'http://127.0.0.1:8080/v1'),
                              ('http://[::1]:8080/v1/', 'http://[::1]:8080/v1'),
                              ('https://127.0.0.1:8080/v1', 'https://127.0.0.1:8080/v1')]:
            with self.subTest(url=url):
                self.assertEqual(backend.validate_local_base_url(url), expected)
        for url in ['https://api.anthropic.com/v1', 'http://192.168.1.1:8080',
                    'http://localhost.example/v1', 'http://127.0.0.1@external.example/v1',
                    'http://secret@localhost:8080/v1', 'file:///tmp/model',
                    'http://2130706433:8080', 'http://localhost:8080/elsewhere',
                    'http://localhost:8080/v1?redirect=bad', 'http://localhost:8080/v1#bad',
                    'http://localhost:99999', 'http://localhost:0', 'http://localhost:\n8080']:
            with self.subTest(url=url), self.assertRaises(backend.AIError):
                backend.validate_local_base_url(url)

    def test_disabled_never_contacts_server(self):
        with patch.object(backend, '_local_request') as request:
            with self.assertRaisesMessage(backend.AIError, 'deaktiviert'):
                backend.check_local_model({**CFG, 'enabled': False})
        request.assert_not_called()

    def test_model_selection_and_ambiguous_or_stale_model(self):
        with patch.object(backend, '_local_request', return_value=MODELS):
            self.assertEqual(backend.check_local_model(CFG), MODEL)
            self.assertEqual(backend.check_local_model({**CFG, 'text_model': MODEL}), MODEL)
            with self.assertRaises(backend.AIError):
                backend.check_local_model({**CFG, 'text_model': 'stale'})
        for data in [{'data': []}, {'data': None}, {'data': [{'id': 'a'}, {'id': 'b'}]}]:
            with self.subTest(data=data), patch.object(backend, '_local_request', return_value=data):
                with self.assertRaises(backend.AIError):
                    backend.check_local_model(CFG)


class LocalTransportTests(TestCase):
    def test_request_has_no_credentials_and_disables_proxy_and_redirects(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"data": []}'
        opener = MagicMock()
        opener.open.return_value = response
        with patch.object(urllib.request, 'build_opener', return_value=opener) as build:
            self.assertEqual(backend._local_request(CFG, '/models'), {'data': []})
        handlers = build.call_args.args
        self.assertEqual(next(h.proxies for h in handlers if isinstance(h, urllib.request.ProxyHandler)), {})
        redirect = next(h for h in handlers if isinstance(h, backend._NoRedirect))
        self.assertIsNone(redirect.redirect_request(None, None, 307, '', {}, 'https://external.example'))
        req = opener.open.call_args.args[0]
        self.assertEqual(req.full_url, 'http://127.0.0.1:8080/v1/models')
        self.assertNotIn('Authorization', req.headers)
        self.assertNotIn('X-api-key', req.headers)

    def test_external_destination_rejected_before_open(self):
        with patch.object(urllib.request, 'build_opener') as build:
            with self.assertRaises(backend.AIError):
                backend._local_request({**CFG, 'base_url': 'https://cloud.example/v1'}, '/models')
        build.assert_not_called()

    def test_server_errors_are_safe_without_retry_or_fallback(self):
        for failure in [urllib.error.URLError('private-details'), TimeoutError('private-details'),
                        urllib.error.HTTPError('secret', 503, 'private-details', {}, io.BytesIO(b'private-details')),
                        urllib.error.HTTPError('secret', 307, 'private-details', {}, io.BytesIO(b'private-details')),
                        urllib.error.HTTPError('secret', 400, 'private-details', {}, io.BytesIO(b'private-details'))]:
            opener = MagicMock()
            opener.open.side_effect = failure
            with self.subTest(failure=failure), patch.object(urllib.request, 'build_opener', return_value=opener):
                with self.assertRaises(backend.AIError) as caught:
                    backend._local_request(CFG, '/models')
                self.assertNotIn('private-details', str(caught.exception))
                self.assertEqual(opener.open.call_count, 1)

    def test_malformed_oversized_or_nonfinite_response_is_rejected(self):
        for body in [b'not json', b'[]', b'{"x":NaN}', b'\xff', b'x'*(backend.AI_MAX_RESPONSE_BYTES+1)]:
            opener = MagicMock()
            opener.open.return_value.__enter__.return_value.read.return_value = body
            with self.subTest(length=len(body)), patch.object(urllib.request, 'build_opener', return_value=opener):
                with self.assertRaises(backend.AIError):
                    backend._local_request(CFG, '/models')

    def test_schema_request_and_loaded_model_used(self):
        with patch.object(backend, '_local_request', side_effect=[MODELS, answer(OFFER)]) as request:
            result = backend.generate_structured('system', 'user', ai._AI_OFFER_INPUT_SCHEMA, cfg=CFG)
        self.assertEqual(result, OFFER)
        cfg, path, payload = request.call_args.args
        self.assertEqual(path, '/chat/completions')
        self.assertEqual(payload['model'], MODEL)
        self.assertEqual(payload['response_format']['schema'], ai._AI_OFFER_INPUT_SCHEMA)
        self.assertEqual(payload['reasoning_effort'], 'none')
        self.assertNotIn('tools', payload)

    def test_invalid_or_truncated_output_never_reaches_catalog(self):
        invalid = []
        for key, value in [('menge', True), ('einzelpreis', float('inf')), ('preis_art', 'invented')]:
            raw=copy.deepcopy(OFFER); raw['positionen'][0][key]=value; invalid.append(answer(raw))
        raw=copy.deepcopy(OFFER); del raw['positionen'][0]['titel']; invalid.append(answer(raw))
        invalid += [answer(OFFER, 'length'), answer(OFFER, 'tool_calls'), {'choices': []},
                    answer({'positionen': 'wrong'}), answer({'text': 'wrong'}),
                    {'choices': [{'finish_reason': 'stop', 'message': {'content': '{'}}]}]
        for data in invalid:
            with self.subTest(data=data), patch.object(backend, '_local_request', side_effect=[MODELS, data]):
                with self.assertRaises(backend.AIError):
                    ai.generate_offer_from_text('Glasreinigung 12 Quadratmeter für je 4,50 Euro')
        self.assertFalse(ServiceCatalogItem.objects.exists())


class AssistantIntegrationTests(TestCase):
    def test_offer_uses_bonsai_and_preserves_normalization_and_catalog(self):
        with patch.object(backend, '_local_request', side_effect=[MODELS, answer(OFFER)]):
            result = ai.generate_offer_from_text('Glasreinigung 12 Quadratmeter für je 4,50 Euro')
        self.assertEqual(result['positionen'][0]['einzelpreis'], '4.50')
        self.assertEqual(result['positionen'][0]['menge'], '12.00')
        self.assertTrue(ServiceCatalogItem.objects.get(name='Glasreinigung').created_by_ai)

    def test_polish_and_cover_letter_use_local_json(self):
        for call in [lambda: ai.polish_service_text('Fenster ordentlich putzen.'),
                     lambda: ai.generate_cover_letter_text(['Rechnung TEST-42', 'Bruttobetrag 100 EUR'])]:
            with self.subTest(call=call), patch.object(backend, '_local_request', side_effect=[MODELS, answer({'text': 'Fertiger Testtext.'})]):
                self.assertEqual(call(), 'Fertiger Testtext.')

    def test_review_uses_local_model_and_preserves_position_numbers(self):
        raw={'befunde': [{'position': 2, 'schwere': 'hinweis', 'text': 'Bitte Menge prüfen.'}]}
        with patch.object(backend, '_local_request', side_effect=[MODELS, answer(raw)]):
            result=ai.ai_review_positions([{}, {'bezeichnung': 'Glasreinigung', 'menge': '12'}])
        self.assertEqual(result[0]['position'], 2)

    def test_polish_empty_text_rejected(self):
        with patch.object(backend, '_local_request', side_effect=[MODELS, answer({'text': '   '})]):
            with self.assertRaises(backend.AIError):
                ai.polish_service_text('Fenster reinigen')


class SettingsTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('bonsai-admin', password='test', is_staff=True)
        self.client.force_login(self.user)
        self.url=reverse('sedo:settings')

    def test_admin_can_save_and_test_loaded_model(self):
        self.client.post(self.url, {'action': 'save_ai', 'ai_local_enabled': 'on',
                                   'ai_local_base_url': 'http://localhost:8080', 'ai_local_model': ''})
        self.assertEqual(AppSetting.get('ai_local_base_url'), CFG['base_url'])
        with patch.object(backend, '_local_request', return_value=MODELS) as request:
            response=self.client.post(self.url, {'action':'test_ai'}, follow=True)
        self.assertContains(response, 'Lokales Modell erreichbar')
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[1], '/models')

    def test_invalid_settings_not_saved(self):
        response=self.client.post(self.url, {'action':'save_ai','ai_local_base_url':'https://external.example'},follow=True)
        self.assertContains(response, 'nur localhost')
        self.assertEqual(AppSetting.get('ai_local_base_url',''), '')

    def test_nonstaff_cannot_change_or_probe_shared_endpoint(self):
        self.user.is_staff=False; self.user.save()
        for action in ['save_ai','test_ai']:
            with self.subTest(action=action):
                self.assertEqual(self.client.post(self.url, {'action':action}).status_code,403)

    def test_ui_has_local_configuration_and_no_cloud_key_input(self):
        with patch.object(backend, '_local_request') as request:
            response=self.client.get(self.url)
        self.assertContains(response, 'KI-Assistent (Bonsai · lokal)')
        self.assertContains(response, 'Gespeicherte Verbindung prüfen')
        self.assertNotContains(response, 'name="ai_api_key"')
        request.assert_not_called()

    def test_speech_has_no_browser_cloud_fallback(self):
        script=(Path(__file__).parents[1]/'static/sedo/offer_ai.js').read_text()
        self.assertNotIn('SpeechRecognition',script)
        self.assertNotIn('startWebSpeech',script)
        self.assertIn("urls.transcribe",script)
        self.assertIn('Lokales Diktat nicht verfügbar',script)
