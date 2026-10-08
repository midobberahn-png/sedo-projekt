from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


APP = Path(settings.BASE_DIR)
BASE = APP / 'sedo/templates/sedo/base.html'
PARTIAL = APP / 'sedo/templates/sedo/_invoice_state_stack.html'
CSS = APP / 'sedo/static/sedo/state-system-v2.css'


class StateSystemV2Tests(SimpleTestCase):
    def test_state_css_wird_nach_app_shell_geladen(self):
        text = BASE.read_text(encoding='utf-8')
        shell = '/static/sedo/app-shell-v2.css?v=1'
        state = '/static/sedo/state-system-v2.css?v=1'
        self.assertIn(shell, text)
        self.assertIn(state, text)
        self.assertLess(text.index(shell), text.index(state))

    def test_partial_trennt_beleg_zahlung_und_faelligkeit(self):
        text = PARTIAL.read_text(encoding='utf-8')
        self.assertIn('invoice.get_lifecycle_display', text)
        self.assertIn("invoice.payment_state == 'paid'", text)
        self.assertIn("invoice.payment_state == 'partial'", text)
        self.assertIn('invoice.is_overdue', text)
        self.assertIn('invoice.days_overdue', text)
        self.assertIn("invoice.display_state == 'gemahnt'", text)
        self.assertNotIn('invoice.get_status_display', text)

    def test_lifecycle_und_payment_haben_getrennte_css_semantik(self):
        text = CSS.read_text(encoding='utf-8')
        for selector in (
            '.state-pill.lifecycle-draft', '.state-pill.lifecycle-issued',
            '.state-pill.lifecycle-corrected', '.state-pill.lifecycle-cancelled',
            '.state-pill.payment-paid', '.state-pill.payment-partial',
            '.state-pill.payment-open', '.state-context.context-danger',
            '.state-context.context-warning',
        ):
            with self.subTest(selector=selector):
                self.assertIn(selector, text)

    def test_zentrale_rechnungsseiten_verwenden_den_state_stack(self):
        for name in ('invoice_list.html', 'invoice_detail.html', 'open_items.html', 'dashboard.html'):
            text = (APP / 'sedo/templates/sedo' / name).read_text(encoding='utf-8')
            with self.subTest(name=name):
                self.assertIn("{% include 'sedo/_invoice_state_stack.html'", text)
