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

    def test_navigation_hat_drei_klare_arbeitsgruppen(self):
        text = BASE.read_text(encoding='utf-8')
        daily = text.index('<span>Tägliche Arbeit</span>')
        billing = text.index('<span>Abrechnung</span>')
        system = text.index('<span>System</span>')
        self.assertLess(daily, billing)
        self.assertLess(billing, system)
        for label in ('Dashboard', 'Kunden', 'Rechnungen', 'Angebote', 'Offene Posten'):
            self.assertIn(f'<span class="nav-text">{label}</span>', text)
        for label in ('GWG', 'Abo-Rechnungen', 'Zahlungen', 'Kassenbuch', 'Monatsabschluss'):
            self.assertIn(f'<span class="nav-text">{label}</span>', text)
        self.assertIn('<span class="nav-text">Leistungskatalog</span>', text)
        self.assertIn('<span class="nav-text">Administration</span>', text)

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
