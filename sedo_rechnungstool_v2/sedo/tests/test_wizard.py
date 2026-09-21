"""
SEDO – Tests für den Schritt-Assistenten in Angebots- und Rechnungsmaske (v3.19.0)

Geprüft wird das serverseitig gelieferte Markup (Schritte, Reihenfolge,
Steuerelemente) sowie – besonders wichtig – dass Validierungsfehler im
richtigen Schritt landen und als .errorlist gerendert werden. Nur dann kann
wizard.js beim Neuladen automatisch zum fehlerhaften Schritt springen.

Ausführen: python manage.py test sedo.tests.test_wizard
"""
from datetime import date
from decimal import Decimal
from pathlib import Path

from .file_fixtures import isolate_files

from django.conf import settings as dj_settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from django.utils import timezone

from sedo.models import Customer, CustomerObject, Invoice, InvoiceItem, Offer, OfferItem
from sedo.services import (
    _AI_SYSTEM_PROMPT_BASE,
    create_invoice_from_offer,
    generate_invoice_files,
    recalc_invoice,
)


ANGEBOT_SCHRITTE = [
    ('step-kunde', 'Kunde'),
    ('step-objekt', 'Objekt'),
    ('step-angebotsdaten', 'Angebotsdaten'),
    ('step-ki', 'KI-Assistent'),
    ('step-positionen', 'Positionen'),
    ('step-pruefen', 'Prüfen'),
]

RECHNUNG_SCHRITTE = [
    ('step-kunde', 'Empfänger'),
    ('step-objekt', 'Leistungsort'),
    ('step-rechnungsdaten', 'Rechnungsdaten'),
    ('step-positionen', 'Positionen'),
    ('step-pruefen', 'Prüfen'),
]


def _angebot_post(**overrides):
    """Vollständiger, gültiger POST-Datensatz für die Angebotsmaske."""
    data = {
        'action': 'create',
        'customer': '', 'obj': '',
        'salutation': '', 'customer_no': '', 'customer_type': 'einmalig',
        'name': 'Musterkunde GmbH', 'name2': '', 'contact_person': '',
        'phone': '', 'email': '', 'street': 'Musterweg 1',
        'postcode': '12345', 'city': 'Musterstadt', 'country': 'DE',
        'object_name': 'Objekt Nord', 'object_street': 'Objektweg 2',
        'object_postcode': '12345', 'object_city': 'Musterstadt', 'object_country': 'DE',
        'issue_date': '2026-07-26', 'valid_days': '14', 'vat_rate': '19.00',
        'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '0',
        'items-MIN_NUM_FORMS': '1', 'items-MAX_NUM_FORMS': '1000',
        'items-0-description': 'Beräumung Reihenhaus',
        'items-0-long_text': 'Komplette Beräumung inklusive Entsorgung.',
        'items-0-quantity': '1.00', 'items-0-unit': 'pauschal',
        'items-0-unit_price': '1200.00', 'items-0-article_no': '',
    }
    data.update(overrides)
    return data


class AngebotsAssistentTests(TestCase):
    """Aufbau der Angebotsmaske als sechsstufiger Assistent."""

    def setUp(self):
        isolate_files(self)
        get_user_model().objects.create_user('tester', password='geheim123')
        self.client.login(username='tester', password='geheim123')
        self.resp = self.client.get(reverse('sedo:offer_create'))
        self.html = self.resp.content.decode('utf-8')

    def test_seite_laedt(self):
        self.assertEqual(self.resp.status_code, 200)

    def test_wizard_container_und_navigation(self):
        self.assertIn('data-wizard', self.html)
        self.assertIn('data-wizard-nav', self.html)
        self.assertIn('data-wizard-prev', self.html)
        self.assertIn('data-wizard-next', self.html)
        self.assertIn('data-wizard-final', self.html)
        self.assertIn('wizard.js', self.html)          # aus base.html eingebunden

    def test_alle_sechs_schritte_vorhanden(self):
        self.assertEqual(self.html.count('data-wizard-step'), 6)
        for step_id, label in ANGEBOT_SCHRITTE:
            self.assertIn('id="%s"' % step_id, self.html)
            self.assertIn('data-step-label="%s"' % label, self.html)

    def test_schrittreihenfolge_ki_nach_angebotsdaten(self):
        positionen = [self.html.index('id="%s"' % s) for s, _ in ANGEBOT_SCHRITTE]
        self.assertEqual(positionen, sorted(positionen),
                         'Die Schritte stehen nicht in der erwarteten Reihenfolge im HTML.')

    def test_ki_karte_liegt_in_ki_schritt(self):
        start = self.html.index('id="step-ki"')
        ende = self.html.index('id="step-positionen"')
        self.assertIn('ai-assist-card', self.html[start:ende])
        self.assertIn('ai-record-btn', self.html[start:ende])
        self.assertIn('ai-rec-indicator', self.html[start:ende])

    def test_positionen_liegen_in_positionsschritt(self):
        start = self.html.index('id="step-positionen"')
        ende = self.html.index('id="step-pruefen"')
        self.assertIn('id="position-list"', self.html[start:ende])
        self.assertIn('items-TOTAL_FORMS', self.html[start:ende])
        self.assertIn('id="live-summary"', self.html[start:ende])

    def test_abschlussaktionen_im_final_block(self):
        start = self.html.index('data-wizard-final')
        block = self.html[start:start + 600]
        self.assertIn('value="preview"', block)
        self.assertIn('value="create"', block)

    def test_zusammenfassung_im_letzten_schritt(self):
        start = self.html.index('id="step-pruefen"')
        block = self.html[start:]
        for feld in ('rc-customer', 'rc-object', 'rc-date', 'rc-valid', 'rc-count', 'rc-gross'):
            self.assertIn('id="%s"' % feld, block)

    def test_ohne_js_keine_versteckten_schritte(self):
        """Die Schritte dürfen serverseitig nicht per hidden ausgeliefert werden."""
        self.assertNotIn('class="wizard-step" hidden', self.html)
        self.assertNotIn('wizard-ready', self.html)


class RechnungsAssistentTests(TestCase):
    """Aufbau der Rechnungsmaske als fünfstufiger Assistent."""

    def setUp(self):
        isolate_files(self)
        get_user_model().objects.create_user('tester', password='geheim123')
        self.client.login(username='tester', password='geheim123')
        self.resp = self.client.get(reverse('sedo:invoice_create'))
        self.html = self.resp.content.decode('utf-8')

    def test_alle_fuenf_schritte_vorhanden(self):
        self.assertEqual(self.html.count('data-wizard-step'), 5)
        for step_id, label in RECHNUNG_SCHRITTE:
            self.assertIn('id="%s"' % step_id, self.html)
            self.assertIn('data-step-label="%s"' % label, self.html)

    def test_schrittreihenfolge(self):
        positionen = [self.html.index('id="%s"' % s) for s, _ in RECHNUNG_SCHRITTE]
        self.assertEqual(positionen, sorted(positionen))

    def test_bestehende_komfortfunktionen_bleiben(self):
        self.assertIn('customer-search', self.html)      # Kundenschnellsuche
        self.assertIn('id="due-preview"', self.html)     # Fälligkeitsvorschau
        self.assertIn('id="live-summary"', self.html)    # Live-Kalkulation
        self.assertIn('autofill-badge', self.html)       # Autofill-Kennzeichnung

    def test_alle_abschlussaktionen_vorhanden(self):
        start = self.html.index('data-wizard-final')
        block = self.html[start:start + 900]
        for action in ('preview', 'draft', 'create', 'send'):
            self.assertIn('value="%s"' % action, block)

    def test_neuanlage_ist_nicht_vorentsperrt(self):
        self.assertNotIn('data-wizard-unlocked', self.html)

    def test_rechnung_aus_angebot_ist_vorentsperrt(self):
        """Bei vorbefüllten Daten sollen alle Schritte sofort anspringbar sein."""
        customer = Customer.objects.create(name='Musterkunde GmbH')
        offer = Offer.objects.create(number='AG2026W001', customer=customer,
                                     issue_date=date(2026, 7, 26), vat_rate=Decimal('19.00'))
        resp = self.client.get(reverse('sedo:invoice_create'), {'from_offer': offer.pk})
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode('utf-8')
        self.assertIn('data-wizard-unlocked', html)
        self.assertIn('Angebotsbezug', html)


class FehlerImRichtigenSchrittTests(TestCase):
    """Kernabsicherung: Fehler müssen sichtbar und dem Schritt zuordenbar sein."""

    def setUp(self):
        isolate_files(self)
        get_user_model().objects.create_user('tester', password='geheim123')
        self.client.login(username='tester', password='geheim123')

    def _block(self, html, step_id, next_step_id=None):
        start = html.index('id="%s"' % step_id)
        ende = html.index('id="%s"' % next_step_id) if next_step_id else len(html)
        return html[start:ende]

    def test_fehlender_kunde_erzeugt_fehler_in_schritt_kunde(self):
        resp = self.client.post(reverse('sedo:offer_create'), _angebot_post(name=''))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode('utf-8')
        self.assertIn('errorlist', html)
        self.assertIn('errorlist', self._block(html, 'step-kunde', 'step-objekt'))

    def test_fehlendes_datum_erzeugt_fehler_in_schritt_angebotsdaten(self):
        resp = self.client.post(reverse('sedo:offer_create'), _angebot_post(issue_date=''))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode('utf-8')
        self.assertIn('errorlist', self._block(html, 'step-angebotsdaten', 'step-ki'))

    def test_fehlende_position_erzeugt_fehler_in_schritt_positionen(self):
        daten = _angebot_post()
        daten['items-TOTAL_FORMS'] = '0'
        for key in list(daten):
            if key.startswith('items-0-'):
                daten.pop(key)
        resp = self.client.post(reverse('sedo:offer_create'), daten)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode('utf-8')
        self.assertIn('errorlist', self._block(html, 'step-positionen', 'step-pruefen'))

    def test_fehlerhafte_position_erzeugt_fehler_in_schritt_positionen(self):
        resp = self.client.post(reverse('sedo:offer_create'),
                                _angebot_post(**{'items-0-quantity': 'keine Zahl'}))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode('utf-8')
        self.assertIn('errorlist', self._block(html, 'step-positionen', 'step-pruefen'))

    def test_gueltiges_angebot_wird_weiterhin_gespeichert(self):
        """Regression: Der Umbau darf den Speicherweg nicht verändern."""
        resp = self.client.post(reverse('sedo:offer_create'), _angebot_post(), follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Offer.objects.count(), 1)
        offer = Offer.objects.first()
        self.assertEqual(offer.customer.name, 'Musterkunde GmbH')
        self.assertEqual(offer.items.count(), 1)
        self.assertEqual(offer.items.first().long_text, 'Komplette Beräumung inklusive Entsorgung.')
        self.assertEqual(offer.net_total, Decimal('1200.00'))

    def test_positionsnummern_werden_durchnummeriert(self):
        """Regression: im PDF stand vorher bei jeder Position eine „1“."""
        daten = _angebot_post()
        daten.update({
            'items-TOTAL_FORMS': '3',
            'items-1-description': 'Entsorgung', 'items-1-long_text': '',
            'items-1-quantity': '1.00', 'items-1-unit': 'pauschal',
            'items-1-unit_price': '300.00', 'items-1-article_no': '',
            'items-2-description': 'Endreinigung', 'items-2-long_text': '',
            'items-2-quantity': '1.00', 'items-2-unit': 'pauschal',
            'items-2-unit_price': '150.00', 'items-2-article_no': '',
        })
        self.client.post(reverse('sedo:offer_create'), daten)
        offer = Offer.objects.first()
        self.assertIsNotNone(offer)
        self.assertEqual([i.position for i in offer.items.all()], [1, 2, 3])

    def test_vorschau_funktioniert_weiterhin(self):
        resp = self.client.post(reverse('sedo:offer_create'), _angebot_post(action='preview'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Beräumung Reihenhaus')
        self.assertEqual(Offer.objects.count(), 0)


class StatischeDateienTests(TestCase):
    """Die Assistenten-Dateien müssen tatsächlich ausgeliefert werden können."""

    def test_wizard_js_existiert(self):
        pfad = Path(dj_settings.BASE_DIR) / 'sedo' / 'static' / 'sedo' / 'wizard.js'
        self.assertTrue(pfad.exists(), 'wizard.js fehlt im static-Verzeichnis')
        inhalt = pfad.read_text(encoding='utf-8')
        self.assertIn('data-wizard-step', inhalt)
        self.assertIn('wizard-ready', inhalt)
        self.assertIn('errorlist', inhalt)     # Sprung zum ersten Fehler

    def test_css_enthaelt_wizard_regeln(self):
        pfad = Path(dj_settings.BASE_DIR) / 'sedo' / 'static' / 'sedo' / 'app.css'
        inhalt = pfad.read_text(encoding='utf-8')
        self.assertIn('.wizard-ready .wizard-step', inhalt)
        self.assertIn('.wizard-pill', inhalt)
        self.assertIn('[hidden]{display:none !important}', inhalt)

    def test_offer_ai_springt_zu_positionen(self):
        pfad = Path(dj_settings.BASE_DIR) / 'sedo' / 'static' / 'sedo' / 'offer_ai.js'
        inhalt = pfad.read_text(encoding='utf-8')
        self.assertIn("goTo('step-positionen'", inhalt)


# ─────────────────────────────────────────────────────────────────────────
# v3.20.0 – Autofill, Datum, Leistungsbeschreibung in Rechnungen, Kalkulation
# ─────────────────────────────────────────────────────────────────────────
class KundenAutofillTests(TestCase):
    """Angebotsmaske soll bei Kundenauswahl alle Felder sichtbar befüllen."""

    def setUp(self):
        isolate_files(self)
        get_user_model().objects.create_user('tester', password='geheim123')
        self.client.login(username='tester', password='geheim123')
        self.html = self.client.get(reverse('sedo:offer_create')).content.decode('utf-8')

    def test_bedienelemente_vorhanden(self):
        for marker in ('customer-search', 'customer-pill', 'autofill-badge',
                       'section-customer', 'section-object', 'obj-hint', 'clear-customer'):
            self.assertIn(marker, self.html, 'Autofill-Element fehlt: %s' % marker)

    def test_ruft_kundendaten_api_auf(self):
        self.assertIn('/api/kunden/', self.html)

    def test_api_liefert_kunde_mit_objekten(self):
        customer = Customer.objects.create(
            name='GWG Musterstadt eG', street='Hauptstr. 1', postcode='12345',
            city='Musterstadt', country='DE', payment_days=21)
        CustomerObject.objects.create(customer=customer, name='WE 4711',
                                      street='Musterweg 5', postcode='12345', city='Musterstadt')
        resp = self.client.get(reverse('sedo:customer_data_api', args=[customer.pk]))
        self.assertEqual(resp.status_code, 200)
        daten = resp.json()
        self.assertEqual(daten['name'], 'GWG Musterstadt eG')
        self.assertEqual(daten['postcode'], '12345')
        self.assertEqual(len(daten['objects']), 1)
        self.assertEqual(daten['objects'][0]['name'], 'WE 4711')


class DatumsfeldTests(TestCase):
    """Regression: type=date braucht ISO-Format, sonst bleibt das Feld leer."""

    def setUp(self):
        isolate_files(self)
        get_user_model().objects.create_user('tester', password='geheim123')
        self.client.login(username='tester', password='geheim123')

    def test_angebotsdatum_ist_heute_und_iso(self):
        html = self.client.get(reverse('sedo:offer_create')).content.decode('utf-8')
        heute = timezone.localdate().isoformat()
        self.assertIn('name="issue_date" value="%s"' % heute, html)
        self.assertNotIn('name="issue_date" value="%s"' % timezone.localdate().strftime('%d.%m.%Y'), html)

    def test_rechnungsdatum_ist_heute_und_iso(self):
        html = self.client.get(reverse('sedo:invoice_create')).content.decode('utf-8')
        self.assertIn('name="issue_date" value="%s"' % timezone.localdate().isoformat(), html)

    def test_datum_bleibt_nach_fehlerhaftem_absenden_erhalten(self):
        resp = self.client.post(reverse('sedo:offer_create'), _angebot_post(name=''))
        html = resp.content.decode('utf-8')
        self.assertIn('name="issue_date" value="2026-07-26"', html)


def _produktblock(xml_text):
    """Schneidet den SpecifiedTradeProduct-Block der ersten Position heraus."""
    start = xml_text.index('<ram:SpecifiedTradeProduct>')
    ende = xml_text.index('</ram:SpecifiedTradeProduct>') + len('</ram:SpecifiedTradeProduct>')
    return xml_text[start:ende]


class RechnungLeistungsbeschreibungTests(TestCase):
    """Leistungsbeschreibung in Rechnungen: Maske, Speichern, PDF, XML (BT-154)."""

    def setUp(self):
        isolate_files(self)
        get_user_model().objects.create_user('tester', password='geheim123')
        self.client.login(username='tester', password='geheim123')

    def test_feld_in_der_rechnungsmaske(self):
        html = self.client.get(reverse('sedo:invoice_create')).content.decode('utf-8')
        self.assertIn('items-0-long_text', html)
        self.assertIn('items-__prefix__-long_text', html)   # auch im Leer-Template

    def test_langtext_wird_gespeichert(self):
        daten = {
            'action': 'draft', 'customer': '', 'obj': '', 'customer_type': 'einmalig',
            'name': 'Musterkunde GmbH', 'country': 'DE', 'object_country': 'DE',
            'object_name': 'Objekt Nord',
            'issue_date': '2026-07-26', 'payment_days': '14',
            'period_start': '', 'period_end': '', 'vat_rate': '19.00', 'note': '',
            'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '1', 'items-MAX_NUM_FORMS': '1000',
            'items-0-description': 'Unterhaltsreinigung Treppenhaus',
            'items-0-long_text': 'Wöchentliche Reinigung aller Treppenhausflächen inklusive Geländer.',
            'items-0-quantity': '1.00', 'items-0-unit': 'pauschal',
            'items-0-unit_price': '250.00', 'items-0-article_no': '',
            'items-0-leistungsart_id': '', 'items-0-leistungsart_text': '',
        }
        self.client.post(reverse('sedo:invoice_create'), daten)
        inv = Invoice.objects.first()
        self.assertIsNotNone(inv)
        self.assertEqual(inv.items.first().long_text,
                         'Wöchentliche Reinigung aller Treppenhausflächen inklusive Geländer.')

    def test_langtext_landet_in_pdf_und_xml(self):
        customer = Customer.objects.create(name='Musterkunde GmbH', street='Hauptstr. 1',
                                           postcode='12345', city='Musterstadt')
        inv = Invoice.objects.create(number='RE2026TEST01', customer=customer,
                                     issue_date=date(2026, 7, 26),
                                     due_date=date(2026, 8, 9), vat_rate=Decimal('19.00'))
        InvoiceItem.objects.create(
            invoice=inv, position=1, description='Unterhaltsreinigung Treppenhaus',
            long_text='Woechentliche Reinigung aller Treppenhausflaechen inklusive Gelaender und Fenstersimsen.',
            quantity=Decimal('1.00'), unit='pauschal', unit_price=Decimal('250.00'))
        recalc_invoice(inv); inv.save()
        generate_invoice_files(inv, erstmalig=True)

        basis = Path(dj_settings.MEDIA_ROOT) / 'generated' / inv.number
        pdf = basis / ('%s.pdf' % inv.number)
        self.assertTrue(pdf.exists())
        self.assertGreater(pdf.stat().st_size, 1000)

        xml = basis / 'factur-x.xml'
        self.assertTrue(xml.exists(), 'ZUGFeRD-XML wurde nicht erzeugt')
        inhalt = xml.read_text(encoding='utf-8')
        # BT-154 gehört in den Produktblock, nicht irgendwo in die XML
        produkt = _produktblock(inhalt)
        self.assertIn('<ram:Description>', produkt)
        self.assertIn('Woechentliche Reinigung aller Treppenhausflaechen', produkt)

    def test_ohne_langtext_keine_leere_description_im_xml(self):
        customer = Customer.objects.create(name='Musterkunde GmbH', street='Hauptstr. 1',
                                           postcode='12345', city='Musterstadt')
        inv = Invoice.objects.create(number='RE2026TEST02', customer=customer,
                                     issue_date=date(2026, 7, 26),
                                     due_date=date(2026, 8, 9), vat_rate=Decimal('19.00'))
        InvoiceItem.objects.create(invoice=inv, position=1, description='Reinigung',
                                   quantity=Decimal('1.00'), unit='pauschal',
                                   unit_price=Decimal('100.00'))
        recalc_invoice(inv); inv.save()
        generate_invoice_files(inv, erstmalig=True)
        inhalt = (Path(dj_settings.MEDIA_ROOT) / 'generated' / inv.number / 'factur-x.xml').read_text(encoding='utf-8')
        self.assertNotIn('<ram:Description>', _produktblock(inhalt))

    def test_uebernahme_aus_angebot(self):
        customer = Customer.objects.create(name='Musterkunde GmbH', payment_days=14)
        offer = Offer.objects.create(number='AG2026T001', customer=customer,
                                     issue_date=date(2026, 7, 26), vat_rate=Decimal('19.00'))
        OfferItem.objects.create(offer=offer, position=1, description='Beräumung',
                                 long_text='Komplette Beräumung inklusive Entsorgung.',
                                 quantity=Decimal('1.00'), unit='pauschal',
                                 unit_price=Decimal('1200.00'))
        inv = create_invoice_from_offer(offer)
        self.assertEqual(inv.items.first().long_text, 'Komplette Beräumung inklusive Entsorgung.')

    def test_vorbefuellte_maske_zeigt_langtext(self):
        customer = Customer.objects.create(name='Musterkunde GmbH', payment_days=14)
        offer = Offer.objects.create(number='AG2026T002', customer=customer,
                                     issue_date=date(2026, 7, 26), vat_rate=Decimal('19.00'))
        OfferItem.objects.create(offer=offer, position=1, description='Beräumung',
                                 long_text='Komplette Beräumung inklusive Entsorgung.',
                                 quantity=Decimal('1.00'), unit='pauschal',
                                 unit_price=Decimal('1200.00'))
        html = self.client.get(reverse('sedo:invoice_create'),
                               {'from_offer': offer.pk}).content.decode('utf-8')
        self.assertIn('Komplette Beräumung inklusive Entsorgung.', html)


class KalkulationsangabenTests(TestCase):
    """Arbeiter/Stunden dienen nur der Preisfindung und dürfen nicht auftauchen."""

    def test_prompt_verbietet_ausweisung(self):
        prompt = _AI_SYSTEM_PROMPT_BASE
        self.assertIn('KALKULATIONSANGABEN NICHT AUSWEISEN', prompt)
        for begriff in ('Arbeiter', 'Arbeitsstunden', 'Stundensätze', 'Leistungstext'):
            self.assertIn(begriff, prompt)
        # Ausnahme für echte Stundenabrechnung muss erhalten bleiben
        self.assertIn('Stundenbasis', prompt)

    def test_hinweis_in_der_maske(self):
        get_user_model().objects.create_user('tester2', password='geheim123')
        self.client.login(username='tester2', password='geheim123')
        html = self.client.get(reverse('sedo:offer_create')).content.decode('utf-8')
        from pathlib import Path
        from django.conf import settings
        self.assertNotIn('Kalkulationsangaben', html)
        self.assertIn('Kalkulationsangaben', (Path(settings.BASE_DIR) / 'BEDIENHINWEISE.md').read_text())
