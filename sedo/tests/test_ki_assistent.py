"""
SEDO – Tests für den KI-Angebotsassistenten (v3.18.0, Angebots-Generator)

Alle Bonsai-Aufrufe sind gemockt – die Tests laufen ohne API-Key und ohne Netz.
Ausführen: python manage.py test sedo.tests
"""
import json
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from sedo.models import AppSetting, ServiceCatalogItem, Offer, OfferItem, Customer
from sedo.services import (
    AIError,
    generate_offer_from_text,
    normalize_ai_positions,
    apply_and_learn_service_catalog,
    _ai_to_decimal,
    _ai_normalize_unit,
    _service_catalog_prompt_block,
    get_ai_config,
    AI_DEFAULT_TEXT_MODEL,
    generate_offer_pdf,
)


def _pos(**kw):
    """Hilfsfunktion: vollständige Rohposition mit Defaults."""
    base = {'titel': 'Testleistung', 'leistungstext': 'Beschreibung der Testleistung.',
            'menge': 1, 'einheit': 'pauschal', 'einzelpreis': 100.0,
            'preis_art': 'genannt', 'katalog_name': 'Testleistung', 'katalog_neu': True,
            'artikel_nr': None}
    base.update(kw)
    return base


# ─────────────────────────────────────────────────────────────────────────
# Normalisierung / Validierung (reine Logik, kein Netz)
# ─────────────────────────────────────────────────────────────────────────
class NormalisierungTests(TestCase):

    def test_vollstaendiger_entwurf_wird_normalisiert(self):
        raw = {
            'positionen': [
                _pos(titel='Beräumung Reihenhaus', leistungstext='Komplette Beräumung aller Etagen.',
                     menge=1, einheit='pauschal', einzelpreis=900, preis_art='aufgeteilt',
                     katalog_name='Beräumung'),
                _pos(titel='Entsorgung', leistungstext='Fachgerechte Entsorgung inkl. Nachweis.',
                     menge=1, einheit='pauschal', einzelpreis=300, preis_art='aufgeteilt',
                     katalog_name='Entsorgung'),
            ],
            'annahmen': ['Übliche Wohnungsgröße angenommen'],
            'globale_hinweise': [],
        }
        result = normalize_ai_positions(raw)
        self.assertEqual(len(result['positionen']), 2)
        self.assertEqual(result['positionen'][0]['bezeichnung'], 'Beräumung Reihenhaus')
        self.assertEqual(result['positionen'][0]['leistungstext'], 'Komplette Beräumung aller Etagen.')
        self.assertEqual(result['positionen'][0]['einzelpreis'], '900.00')
        self.assertIn('Übliche Wohnungsgröße angenommen', result['annahmen'])
        # Aufteilungs-Hinweis mit Zeilensumme 1200 €
        self.assertTrue(any('aufgeteilt' in h and '1200.00' in h for h in result['hinweise']))

    def test_geschaetzter_preis_erzeugt_hinweis(self):
        raw = {'positionen': [_pos(titel='Hausmeisterdienst', einzelpreis=250, preis_art='geschaetzt')],
               'annahmen': [], 'globale_hinweise': []}
        result = normalize_ai_positions(raw)
        self.assertTrue(any('KI-Vorschlag' in h for h in result['hinweise']))
        self.assertEqual(result['positionen'][0]['preis_art'], 'geschaetzt')

    def test_offener_preis_leeres_feld_und_hinweis(self):
        raw = {'positionen': [_pos(einzelpreis=None, preis_art='offen')],
               'annahmen': [], 'globale_hinweise': []}
        result = normalize_ai_positions(raw)
        self.assertEqual(result['positionen'][0]['einzelpreis'], '')
        self.assertTrue(any('Preis offen' in h for h in result['hinweise']))

    def test_qm_preis_plausibilitaet(self):
        raw = {'positionen': [_pos(menge=100, einheit='m²', einzelpreis=45.0)],
               'annahmen': [], 'globale_hinweise': []}
        result = normalize_ai_positions(raw)
        self.assertTrue(any('je m² wirkt ungewöhnlich' in h for h in result['hinweise']))

    def test_fehlende_menge_wird_pauschale(self):
        raw = {'positionen': [_pos(menge=None, einheit=None)],
               'annahmen': [], 'globale_hinweise': []}
        result = normalize_ai_positions(raw)
        self.assertEqual(result['positionen'][0]['menge'], '1.00')
        self.assertEqual(result['positionen'][0]['einheit'], 'pauschal')

    def test_ungueltige_preis_art_wird_korrigiert(self):
        raw = {'positionen': [_pos(preis_art='quatsch')],
               'annahmen': [], 'globale_hinweise': []}
        result = normalize_ai_positions(raw)
        self.assertEqual(result['positionen'][0]['preis_art'], 'genannt')

    def test_langer_leistungstext_wird_gekuerzt(self):
        raw = {'positionen': [_pos(leistungstext='X' * 3000)],
               'annahmen': [], 'globale_hinweise': []}
        result = normalize_ai_positions(raw)
        self.assertLessEqual(len(result['positionen'][0]['leistungstext']), 1200)

    def test_ungueltiges_format_wirft_aierror(self):
        with self.assertRaises(AIError):
            normalize_ai_positions({'foo': 'bar'})
        with self.assertRaises(AIError):
            normalize_ai_positions({'positionen': 'kein-array'})

    def test_decimal_parser_deutsche_formate(self):
        self.assertEqual(_ai_to_decimal('0,45'), Decimal('0.45'))
        self.assertEqual(_ai_to_decimal('1.234,56'), Decimal('1234.56'))
        self.assertEqual(_ai_to_decimal('80 €'), Decimal('80.00'))
        self.assertIsNone(_ai_to_decimal('abc'))
        self.assertIsNone(_ai_to_decimal(True))

    def test_einheiten_normalisierung(self):
        self.assertEqual(_ai_normalize_unit('qm'), 'm²')
        self.assertEqual(_ai_normalize_unit('monatlich'), 'Monat')
        self.assertEqual(_ai_normalize_unit('Sonderfläche'), 'Sonderfläche')


# ─────────────────────────────────────────────────────────────────────────
# Selbstlernender Leistungskatalog
# ─────────────────────────────────────────────────────────────────────────
class KatalogTests(TestCase):

    def test_neue_leistung_wird_angelegt(self):
        positionen = [{'katalog_name': 'Beräumung', 'leistungstext': 'Komplette Beräumung.',
                       'einheit': 'pauschal', 'katalog_neu': True}]
        info = apply_and_learn_service_catalog(positionen)
        item = ServiceCatalogItem.objects.get(name='Beräumung')
        self.assertTrue(item.created_by_ai)
        self.assertEqual(item.long_text, 'Komplette Beräumung.')
        self.assertEqual(item.default_unit, 'pauschal')
        self.assertIn('neue Leistung', info)

    def test_bestehender_katalogtext_wird_wiederverwendet(self):
        ServiceCatalogItem.objects.create(name='Beräumung', long_text='SEDO-Standardtext Beräumung.')
        positionen = [{'katalog_name': 'beräumung',  # case-insensitive Treffer
                       'leistungstext': 'Anderer, neu generierter Text.', 'einheit': 'pauschal'}]
        info = apply_and_learn_service_catalog(positionen)
        # Der gespeicherte Katalogtext ersetzt den generierten → Konsistenz
        self.assertEqual(positionen[0]['leistungstext'], 'SEDO-Standardtext Beräumung.')
        self.assertIn('aus dem Katalog übernommen', info)
        self.assertEqual(ServiceCatalogItem.objects.count(), 1)  # kein Duplikat

    def test_leerer_katalogeintrag_wird_befuellt_statt_dupliziert(self):
        ServiceCatalogItem.objects.create(name='Winterdienst', long_text='')
        positionen = [{'katalog_name': 'Winterdienst', 'leistungstext': 'Räum- und Streudienst.', 'einheit': 'Monat'}]
        apply_and_learn_service_catalog(positionen)
        item = ServiceCatalogItem.objects.get(name='Winterdienst')
        self.assertEqual(item.long_text, 'Räum- und Streudienst.')
        self.assertEqual(ServiceCatalogItem.objects.count(), 1)

    def test_katalog_prompt_block(self):
        self.assertIn('noch leer', _service_catalog_prompt_block())
        ServiceCatalogItem.objects.create(name='Glasreinigung', long_text='Beidseitige Reinigung.', default_unit='m²')
        block = _service_catalog_prompt_block()
        self.assertIn('Glasreinigung', block)
        self.assertIn('Beidseitige Reinigung.', block)


# ─────────────────────────────────────────────────────────────────────────
# Generator Ende-zu-Ende (Bonsai-Tool-Use gemockt)
# ─────────────────────────────────────────────────────────────────────────
class GeneratorTests(TestCase):

    def setUp(self):
        AppSetting.set('ai_local_enabled', '1')

    def test_generierung_inkl_katalog_lernen(self):
        tool_input = {
            'positionen': [_pos(titel='Beräumung Reihenhaus', katalog_name='Beräumung')],
            'annahmen': ['Reihenhaus mit 3 Etagen angenommen'],
            'globale_hinweise': [],
        }
        with patch('sedo.services.ai._ai_generate_offer', return_value=tool_input):
            result = generate_offer_from_text('Beräumung Reihenhaus mit Entsorgung 1200 Euro')
        self.assertEqual(len(result['positionen']), 1)
        self.assertIn('Reihenhaus mit 3 Etagen angenommen', result['annahmen'])
        self.assertTrue(ServiceCatalogItem.objects.filter(name='Beräumung').exists())
        self.assertIn('neue Leistung', result['katalog_info'])

    def test_zweiter_lauf_nutzt_katalogtext(self):
        tool_input = {'positionen': [_pos(katalog_name='Beräumung', leistungstext='Erster Text.')],
                      'annahmen': [], 'globale_hinweise': []}
        with patch('sedo.services.ai._ai_generate_offer', return_value=tool_input):
            generate_offer_from_text('Beräumung bitte')
        tool_input2 = {'positionen': [_pos(katalog_name='Beräumung', leistungstext='Abweichender Text.')],
                       'annahmen': [], 'globale_hinweise': []}
        with patch('sedo.services.ai._ai_generate_offer', return_value=tool_input2):
            result = generate_offer_from_text('Nochmal Beräumung')
        self.assertEqual(result['positionen'][0]['leistungstext'], 'Erster Text.')

    def test_deaktiviert_wirft_aierror(self):
        AppSetting.set('ai_local_enabled', '0')
        with self.assertRaises(AIError):
            generate_offer_from_text('irgendeine Ansage')

    def test_zu_kurzer_text_wirft_aierror(self):
        with self.assertRaises(AIError):
            generate_offer_from_text('ab')

    def test_config_defaults(self):
        AppSetting.set('ai_local_model', '')
        self.assertEqual(get_ai_config()['text_model'], AI_DEFAULT_TEXT_MODEL)


# ─────────────────────────────────────────────────────────────────────────
# View-/Endpoint-Tests
# ─────────────────────────────────────────────────────────────────────────
class KiEndpointTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user('tester', password='geheim123')
        self.url = reverse('sedo:ai_generate_offer')

    def _login(self):
        self.client.login(username='tester', password='geheim123')

    def test_anonym_wird_umgeleitet(self):
        resp = self.client.post(self.url, data='{}', content_type='application/json')
        self.assertEqual(resp.status_code, 302)

    def test_get_nicht_erlaubt(self):
        self._login()
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_deaktiviert_400(self):
        AppSetting.set('ai_local_enabled', '0')
        self._login()
        resp = self.client.post(self.url, data=json.dumps({'text': 'Beräumung Reihenhaus'}),
                                content_type='application/json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('deaktiviert', resp.json()['error'])

    def test_erfolgreiche_generierung_ueber_endpoint(self):
        self._login()
        AppSetting.set('ai_local_enabled', '1')
        tool_input = {
            'positionen': [_pos(titel='Beräumung Reihenhaus', leistungstext='Beräumung aller Räume.',
                                einzelpreis=1200, katalog_name='Beräumung')],
            'annahmen': ['3 Etagen angenommen'],
            'globale_hinweise': [],
        }
        with patch('sedo.services.ai._ai_generate_offer', return_value=tool_input):
            resp = self.client.post(self.url, data=json.dumps({'text': 'Beräumung Reihenhaus 1200 Euro'}),
                                    content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['positionen'][0]['bezeichnung'], 'Beräumung Reihenhaus')
        self.assertEqual(data['positionen'][0]['leistungstext'], 'Beräumung aller Räume.')
        self.assertEqual(data['annahmen'], ['3 Etagen angenommen'])
        self.assertIn('katalog_info', data)

    def test_aierror_wird_als_502_gemeldet(self):
        self._login()
        AppSetting.set('ai_local_enabled', '1')
        with patch('sedo.views.generate_offer_from_text', side_effect=AIError('Testfehler')):
            resp = self.client.post(self.url, data=json.dumps({'text': 'Beräumung Reihenhaus'}),
                                    content_type='application/json')
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(resp.json()['error'], 'Testfehler')


# ─────────────────────────────────────────────────────────────────────────
# Leistungskatalog-Seiten (CRUD)
# ─────────────────────────────────────────────────────────────────────────
class KatalogSeitenTests(TestCase):

    def setUp(self):
        User = get_user_model()
        User.objects.create_user('tester', password='geheim123')
        self.client.login(username='tester', password='geheim123')

    def test_liste_und_leerzustand(self):
        resp = self.client.get(reverse('sedo:service_catalog_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Leistungskatalog')
        self.assertContains(resp, 'Noch keine Leistungen')

    def test_anlegen_bearbeiten_loeschen(self):
        resp = self.client.post(reverse('sedo:service_catalog_create'),
                                {'name': 'Glasreinigung', 'default_unit': 'm²', 'long_text': 'Beidseitig.'})
        self.assertEqual(resp.status_code, 302)
        item = ServiceCatalogItem.objects.get(name='Glasreinigung')
        # Bearbeiten setzt created_by_ai zurück (gilt als geprüft)
        item.created_by_ai = True
        item.save()
        self.client.post(reverse('sedo:service_catalog_update', args=[item.pk]),
                         {'name': 'Glasreinigung', 'default_unit': 'm²', 'long_text': 'Beidseitig, streifenfrei.'})
        item.refresh_from_db()
        self.assertFalse(item.created_by_ai)
        self.assertEqual(item.long_text, 'Beidseitig, streifenfrei.')
        self.client.post(reverse('sedo:service_catalog_delete', args=[item.pk]))
        self.assertFalse(ServiceCatalogItem.objects.filter(pk=item.pk).exists())

    def test_suche(self):
        ServiceCatalogItem.objects.create(name='Winterdienst', long_text='Räumen und Streuen.')
        ServiceCatalogItem.objects.create(name='Glasreinigung', long_text='Beidseitig.')
        resp = self.client.get(reverse('sedo:service_catalog_list'), {'q': 'Winter'})
        self.assertContains(resp, 'Winterdienst')
        self.assertNotContains(resp, 'Glasreinigung')


# ─────────────────────────────────────────────────────────────────────────
# Seiten- und PDF-Tests
# ─────────────────────────────────────────────────────────────────────────
class SeitenTests(TestCase):

    def setUp(self):
        User = get_user_model()
        User.objects.create_user('tester', password='geheim123')
        self.client.login(username='tester', password='geheim123')

    def test_angebotsmaske_enthaelt_generator_panel(self):
        resp = self.client.get(reverse('sedo:offer_create'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'ai-assist-card')         # KI-Karte (eigener Schritt)
        self.assertContains(resp, 'ai-rec-indicator')       # Aufnahme-Anzeige
        self.assertContains(resp, 'long_text')              # Leistungstext-Feld im Formset
        self.assertContains(resp, 'offer_ai.js')

    def test_einstellungen_enthalten_ki_karte(self):
        get_user_model().objects.filter(username='tester').update(is_staff=True)
        resp = self.client.get(reverse('sedo:settings'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'KI-Assistent (Bonsai · lokal)')

    def test_catalogue_is_reachable_through_settings(self):
        get_user_model().objects.filter(username='tester').update(is_staff=True)
        resp = self.client.get(reverse('sedo:dashboard'))
        self.assertContains(resp, 'Einstellungen')
        self.assertNotContains(resp, 'Leistungskatalog')
        self.assertContains(self.client.get(reverse('sedo:settings')), reverse('sedo:service_catalog_list'))

    def test_angebots_pdf_mit_leistungstext(self):
        customer = Customer.objects.create(name='Testkunde GmbH')
        offer = Offer.objects.create(number='AG2026TEST01', customer=customer)
        OfferItem.objects.create(offer=offer, position=1, description='Beräumung Reihenhaus',
                                 long_text='Komplette Beräumung aller Etagen inklusive Keller. '
                                           'Abtransport und fachgerechte Entsorgung sind enthalten. ' * 3,
                                 quantity=Decimal('1.00'), unit='pauschal', unit_price=Decimal('1200.00'))
        generate_offer_pdf(offer)
        offer.refresh_from_db()
        from pathlib import Path
        from django.conf import settings as dj_settings
        pdf_path = Path(dj_settings.MEDIA_ROOT) / 'generated' / offer.number / f'{offer.number}.pdf'
        self.assertTrue(pdf_path.exists())
        self.assertGreater(pdf_path.stat().st_size, 1000)
