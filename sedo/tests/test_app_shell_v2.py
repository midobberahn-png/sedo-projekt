from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


APP = Path(settings.BASE_DIR)
BASE = APP / 'sedo/templates/sedo/base.html'
SHELL = APP / 'sedo/static/sedo/app-shell-v2.css'
SEARCH = APP / 'sedo/static/sedo/global_search.js'


class AppShellV2Tests(SimpleTestCase):
    def test_app_shell_css_wird_nach_design_system_geladen(self):
        text = BASE.read_text(encoding='utf-8')
        design = '/static/sedo/design-system-v2.css?v=1'
        shell = '/static/sedo/app-shell-v2.css?v=1'
        self.assertIn(design, text)
        self.assertIn(shell, text)
        self.assertLess(text.index(design), text.index(shell))

    def test_navigation_has_requested_groups_and_order(self):
        from lxml import html
        from django.template.loader import render_to_string
        doc = html.fromstring(render_to_string('sedo/base.html'))
        nav = doc.xpath('//nav[@aria-label="Hauptnavigation"]')[0]
        self.assertEqual(nav.xpath('./div[@class="drawer-title"]/span/text()'),
                         ['Tägliche Arbeit', 'Abrechnung', 'Finanzen', 'System'])
        self.assertEqual(nav.xpath('./a/span[@class="nav-text"]/text()'), [
            'Dashboard', 'E-Mail', 'Rechnungen', 'Angebote', 'Kalender', 'Eingangsrechnungen',
            'Neue Rechnung', 'GWG Monatsrechnung', 'Abo-Rechnung', 'Kassenbuch', 'Zahlungen',
            'Offene Posten', 'Monatsabschluss', 'Auswertung', 'Kunden', 'Einstellungen'])
        self.assertFalse(nav.xpath('.//a[contains(@href,"aufgaben")]'))
        self.assertFalse(nav.xpath('.//a[contains(@href,"startklar") or contains(@href,"leistungskatalog")]'))
        self.assertEqual(nav.xpath('./a[@aria-label="Neue Rechnung"]/@href'), ['/rechnungen/neu/'])
        self.assertTrue(all(node.get('aria-label') for node in nav.xpath('./a')))

    def test_globale_suche_ist_einmalig_in_der_commandbar(self):
        text = BASE.read_text(encoding='utf-8')
        self.assertEqual(text.count('id="global-search"'), 1)
        self.assertEqual(text.count('id="global-search-results"'), 1)
        self.assertIn('class="app-commandbar"', text)
        self.assertIn('Suchen oder Aktion starten', text)
        self.assertIn('data-action-invoice=', text)
        self.assertIn('data-action-open-items=', text)

    def test_command_center_hat_tastatur_und_schnellaktionen(self):
        text = SEARCH.read_text(encoding='utf-8')
        self.assertIn("e.key.toLowerCase() === 'k'", text)
        self.assertIn("e.key === '/'", text)
        self.assertIn('Schnellaktionen', text)
        self.assertIn('Neue Rechnung', text)
        self.assertIn('Neues Angebot', text)
        self.assertIn('Neuen Kunden anlegen', text)
        self.assertIn('Offene Posten öffnen', text)

    def test_shell_hat_desktop_collapse_und_mobile_drawer(self):
        text = SHELL.read_text(encoding='utf-8')
        self.assertIn('body.sidebar-locked{--shell-sidebar-w:72px}', text)
        self.assertIn('@media(max-width:1050px)', text)
        self.assertIn('transform:translateX(-105%)', text)
        self.assertIn('body.sidebar-locked .sidebar{transform:translateX(0)}', text)
