from pathlib import Path
p = Path('.github/h3-round3.py')
s = p.read_text()
old = """replace('sedo_rechnungstool_v2/sedo/tests/test_invoice_lifecycle.py',
        \"self.assertEqual(inv.status, 'storniert')\",
        \"self.assertEqual(inv.status, 'ueberfaellig')\",1)
"""
if s.count(old) != 1:
    raise SystemExit(f'Lifecycle-Zweitanker: {s.count(old)} statt 1')
p.write_text(s.replace(old, '', 1))
