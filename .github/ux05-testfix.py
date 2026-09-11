from pathlib import Path

p = Path('sedo_rechnungstool_v2/sedo/tests/test_review_v2.py')
s = p.read_text(encoding='utf-8')
old = '''    def test_bezahlt_knopf_zeigt_auf_die_zahlungsroute(self):
        antwort = self.client.get(reverse('sedo:open_items'))
        inhalt = antwort.content.decode('utf-8')
        ziel = reverse('sedo:invoice_mark_paid', args=[self.rechnungen[0].pk])
        self.assertIn(f'action="{ziel}"', inhalt)
'''
new = '''    def test_arbeitswarteschlange_hat_keinen_mark_paid_post_mehr(self):
        antwort = self.client.get(reverse('sedo:open_items'))
        inhalt = antwort.content.decode('utf-8')
        mark_paid = reverse('sedo:invoice_mark_paid', args=[self.rechnungen[0].pk])
        zahlung = reverse('sedo:invoice_add_payment', args=[self.rechnungen[0].pk])
        self.assertNotIn(f'action="{mark_paid}"', inhalt,
                         'UX-05 entfernt den direkten Mark-paid-POST aus der OP-Zeile.')
        self.assertIn(zahlung, inhalt,
                      'Der sichere Zahlungsweg muss aus der Arbeitswarteschlange erreichbar bleiben.')
        self.assertEqual(inhalt.count('id="batch-form"'), 1,
                         'Das Mahn-Batchformular muss weiterhin genau einmal und isoliert vorkommen.')
'''
if s.count(old) != 1:
    raise SystemExit(f'F-11-Anker {s.count(old)} statt 1')
p.write_text(s.replace(old, new, 1), encoding='utf-8')
