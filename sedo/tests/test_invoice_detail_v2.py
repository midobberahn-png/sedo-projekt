from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


APP = Path(settings.BASE_DIR)
BASE = APP / 'sedo/templates/sedo/base.html'
DETAIL = APP / 'sedo/templates/sedo/invoice_detail.html'
ACTIONS = APP / 'sedo/templates/sedo/_invoice_actions.html'
CSS = APP / 'sedo/static/sedo/invoice-detail-v2.css'


class InvoiceDetailV2Tests(SimpleTestCase):
    def test_detail_css_wird_nach_state_system_geladen(self):
        text = BASE.read_text(encoding='utf-8')
        state = '/static/sedo/state-system-v2.css?v=1'
        detail = '/static/sedo/invoice-detail-v2.css?v=1'
        self.assertIn(state, text)
        self.assertIn(detail, text)
        self.assertLess(text.index(state), text.index(detail))

    def test_rechnungsdetail_verwendet_ein_zentrales_aktionspartial(self):
        text = DETAIL.read_text(encoding='utf-8')
        self.assertEqual(text.count("{% include 'sedo/_invoice_actions.html' %}"), 1)
        self.assertNotIn('class="toolbar detail-actions"', text)

    def test_genau_eine_zustandsabhaengige_hauptaktion_ist_definiert(self):
        text = ACTIONS.read_text(encoding='utf-8')
        self.assertIn("invoice.lifecycle == 'draft'", text)
        self.assertIn('Rechnung finalisieren', text)
        self.assertIn("invoice.lifecycle == 'issued'", text)
        self.assertIn("invoice.payment_state != 'paid'", text)
        self.assertIn('Zahlung erfassen', text)
        self.assertIn('Originalbeleg öffnen', text)
        self.assertGreaterEqual(text.count('detail-primary-action'), 4)

    def test_seltene_und_destructive_aktionen_liegen_im_mehr_menu(self):
        text = ACTIONS.read_text(encoding='utf-8')
        self.assertIn('Weitere Aktionen', text)
        for label in (
            'Korrekturrechnung erstellen', 'Zahlungserinnerung erstellen',
            'Mahnung erstellen', 'Stornorechnung erzeugen',
            'Entwurf verwerfen', 'Rechnung ausblenden',
        ):
            with self.subTest(label=label):
                self.assertIn(label, text)
        self.assertIn('action-menu-item danger', text)

    def test_entwurf_hat_keine_doppelte_finalisieren_toolbar_mehr(self):
        text = DETAIL.read_text(encoding='utf-8')
        self.assertEqual(text.count('Rechnung finalisieren'), 0)
        self.assertIn('draft-guidance-actions', text)

    def test_action_menu_hat_mobile_und_desktop_regeln(self):
        text = CSS.read_text(encoding='utf-8')
        self.assertIn('.action-menu-panel', text)
        self.assertIn('@media(max-width:900px)', text)
        self.assertIn('@media(max-width:640px)', text)
        self.assertIn('.detail-primary-action', text)
