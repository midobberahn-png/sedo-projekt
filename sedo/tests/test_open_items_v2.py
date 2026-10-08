from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


APP = Path(settings.BASE_DIR)
TEMPLATE = APP / 'sedo/templates/sedo/open_items.html'
CSS = APP / 'sedo/static/sedo/open-items-v2.css'


class OpenItemsV2ContractTests(SimpleTestCase):
    def test_statusfilter_sind_als_arbeitstabs_ausgebildet(self):
        text = TEMPLATE.read_text(encoding='utf-8')
        self.assertIn('class="op-tabs"', text)
        for status in ('open', 'overdue', 'paid', 'all'):
            self.assertIn(f'?status={status}', text)

    def test_rechnungszeile_hat_einen_priorisierten_naechsten_schritt(self):
        text = TEMPLATE.read_text(encoding='utf-8')
        self.assertIn('Nächster Schritt', text)
        self.assertIn('Mahnschritt prüfen', text)
        self.assertIn('Zahlung abwarten / erfassen', text)
        self.assertIn('Abgeschlossen', text)

    def test_keine_direkten_mahnbuttons_mehr_pro_zeile(self):
        text = TEMPLATE.read_text(encoding='utf-8')
        # Einzelne Mahn-/Erinnerungs-POSTs gehören nicht mehr in jede Tabellenzeile.
        self.assertNotIn("invoice_reminder' i.pk 'reminder'", text)
        self.assertNotIn("invoice_reminder' i.pk 'dunning'", text)

    def test_mahnautomat_hat_expliziten_review_schritt(self):
        text = TEMPLATE.read_text(encoding='utf-8')
        self.assertIn('Mahnlauf prüfen', text)
        self.assertIn('auto-review-confirm', text)
        self.assertIn('auto-dunning-submit', text)
        self.assertIn('disabled>Nächste Stufen ausführen', text)

    def test_batchaktionen_erscheinen_erst_nach_auswahl(self):
        template = TEMPLATE.read_text(encoding='utf-8')
        css = CSS.read_text(encoding='utf-8')
        self.assertIn('id="batch-bar"', template)
        self.assertIn("bar.classList.toggle('visible',n>0)", template)
        self.assertIn('.op-batchbar{', css)
        self.assertIn('display:none', css)
        self.assertIn('.op-batchbar.visible{display:flex}', css)

    def test_batch_mahnung_hat_bestaetigungsdialog(self):
        text = TEMPLATE.read_text(encoding='utf-8')
        self.assertIn('window.confirm', text)
        self.assertIn("'Mahnungen'", text)
        self.assertIn("'Zahlungserinnerungen'", text)

    def test_responsive_arbeitswarteschlange_ist_definiert(self):
        text = CSS.read_text(encoding='utf-8')
        self.assertIn('@media(max-width:1050px)', text)
        self.assertIn('@media(max-width:680px)', text)
        self.assertIn('grid-template-areas', text)
