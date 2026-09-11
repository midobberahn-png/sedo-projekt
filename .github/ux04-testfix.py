from pathlib import Path
p=Path('sedo_rechnungstool_v2/sedo/tests/test_review_v2.py')
s=p.read_text(encoding='utf-8')
# Branch-Datei wurde bei einem API-Schreibfehler versehentlich ersetzt; stelle
# sie aus Git-parent wieder her, falls der Platzhalter vorliegt.
if s.strip() == '{% raw %}PLACEHOLDER{% endraw %}':
    import subprocess
    s=subprocess.check_output(['git','show','HEAD^:sedo_rechnungstool_v2/sedo/tests/test_review_v2.py'], text=True)
old="""        self.assertIn('Löschen nicht möglich', inhalt)
        self.assertNotIn(reverse('sedo:invoice_delete', args=[rechnung.pk]), inhalt)
"""
new="""        self.assertIn('Ausblenden nicht möglich', inhalt)
        self.assertIn('action-menu-disabled', inhalt)
        self.assertNotIn(reverse('sedo:invoice_delete', args=[rechnung.pk]), inhalt)
"""
if s.count(old)!=1:
    raise SystemExit(f'Testanker {s.count(old)} statt 1')
p.write_text(s.replace(old,new,1), encoding='utf-8')
