from pathlib import Path
p = Path('sedo_rechnungstool_v2/sedo/tests/test_draft_workflow.py')
s = p.read_text()
old = """        create_storno(original)
        Invoice.objects.filter(pk=original.pk).update(status='ueberfaellig')
        with self.assertRaises(WorkflowError):
            finalisiere_entwurf(inv)
"""
new = """        create_storno(original)
        # Adversarial: Auch wenn der gespeicherte Belegzustand fälschlich wieder
        # 'issued' lautet, muss der tatsächlich ausgestellte Storno-Folgebeleg
        # die Korrekturfinalisierung weiterhin blockieren.
        Invoice.objects.filter(pk=original.pk).update(status='ueberfaellig', lifecycle='issued')
        with self.assertRaises(WorkflowError):
            finalisiere_entwurf(inv)
"""
if s.count(old) != 1:
    raise SystemExit(f'Graph-Testanker {s.count(old)} statt 1')
p.write_text(s.replace(old, new, 1))
