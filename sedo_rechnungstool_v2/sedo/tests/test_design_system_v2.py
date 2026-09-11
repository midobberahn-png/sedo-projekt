from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


APP = Path(settings.BASE_DIR)
BASE = APP / 'sedo/templates/sedo/base.html'
CSS = APP / 'sedo/static/sedo/design-system-v2.css'


class DesignSystemV2Tests(SimpleTestCase):
    def test_design_system_wird_nach_legacy_css_geladen(self):
        text = BASE.read_text(encoding='utf-8')
        legacy = '/static/sedo/app.css?v=3_24'
        design = '/static/sedo/design-system-v2.css?v=1'
        self.assertIn(legacy, text)
        self.assertIn(design, text)
        self.assertLess(text.index(legacy), text.index(design))

    def test_kern_tokens_und_semantische_komponenten_existieren(self):
        text = CSS.read_text(encoding='utf-8')
        for token in (
            '--ds-primary:', '--ds-danger:', '--ds-warning:', '--ds-success:',
            '--ds-space-4:', '--ds-radius-lg:', '--ds-focus:',
        ):
            with self.subTest(token=token):
                self.assertIn(token, text)
        for selector in (
            '.btn.primary', '.btn.danger', '.badge.green', '.table th',
            '.empty-state', '.ds-selection-bar', '.ds-tabs',
        ):
            with self.subTest(selector=selector):
                self.assertIn(selector, text)

    def test_tabellarische_zahlen_und_tastaturfokus_sind_definiert(self):
        text = CSS.read_text(encoding='utf-8')
        self.assertIn('font-variant-numeric:tabular-nums lining-nums', text)
        self.assertIn(':focus-visible', text)
        self.assertIn('prefers-reduced-motion:reduce', text)

    def test_normale_karten_werden_nicht_mehr_als_klickbar_angehoben(self):
        text = CSS.read_text(encoding='utf-8')
        self.assertIn('.card:hover{box-shadow:var(--ds-shadow-xs)}', text)
        self.assertIn('a.card:hover,.action-card:hover,.clickable-card:hover', text)
