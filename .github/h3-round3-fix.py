from pathlib import Path
p = Path('.github/h3-round3.py')
s = p.read_text()
old = """        write_audit(request, 'update', original,
                    f'Rechnung {original.number}: ausgestellt → storniert', pflicht=True,
                    metadata={'von': before, 'nach': 'cancelled', 'anlass': 'storno'})
"""
new = """        write_audit(request, 'update', original,
                    f'Rechnung {original.number}: ausgestellt → storniert ({storno.number})',
                    pflicht=True, metadata={'von': before, 'nach': 'cancelled', 'anlass': 'storno'})
"""
if s.count(old) != 1:
    raise SystemExit(f'Auditanker im Patchskript: {s.count(old)} statt 1')
p.write_text(s.replace(old, new, 1))
