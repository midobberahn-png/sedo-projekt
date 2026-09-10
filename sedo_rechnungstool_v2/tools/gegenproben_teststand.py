"""H2: vier Gegenproben an Quellkopien, ohne Paketinstallation/Django-Start."""
import argparse
import ast
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

APP = Path(__file__).resolve().parents[1]
MUTATIONS = [
    ('lock_hash', "    if sha(data) != meta['lock_sha256']:", '    if False:'),
    ('input_hash', '        if sha((app / name).read_bytes()) != digest:', '        if False:'),
    ('clean_source', "    if git('status', '--porcelain', '--untracked-files=no'):", '    if False:'),
    ('package_pin', "        if not re.match(r'^[A-Za-z0-9][A-Za-z0-9_.-]*==[A-Za-z0-9.!+_-]+(?:\\s|$)', entry):", '        if False:'),
]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    out=p.parse_args().output;out.mkdir(parents=True,exist_ok=True)
    results=[]
    with tempfile.TemporaryDirectory(prefix='sedo-build-gegenprobe-') as tmp:
        for name, old, new in [('basis','',''), *MUTATIONS]:
            root=Path(tmp)/name;root.mkdir()
            for item in ('requirements.txt','requirements_desktop.txt','requirements-release.in',
                         'requirements-release-constraints.txt','requirements-release.lock','release-lock.json',
                         'tools/teststand.py','sedo/tests/test_teststand.py'):
                dest=root/item;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(APP/item,dest)
            if name!='basis':
                source=root/'tools/teststand.py';text=source.read_text(encoding='utf-8')
                if text.count(old)!=1:raise RuntimeError('Uneindeutiger Anker: '+name)
                text=text.replace(old,new,1);ast.parse(text);source.write_text(text,encoding='utf-8')
            run=subprocess.run([sys.executable,'-m','unittest','discover','-s',str(root/'sedo/tests'),
                                '-p','test_teststand.py','-v'],capture_output=True,text=True,timeout=60)
            text=run.stdout+run.stderr;(out/(name+'.log')).write_text(text,encoding='utf-8')
            if name=='basis':
                if run.returncode:raise RuntimeError('Basis ist nicht grün.')
                continue
            ok=bool(run.returncode and 'AssertionError' in text and 'FAILED (failures=' in text and 'ERROR:' not in text)
            results.append({'schutz':name,'entfernen_macht_test_rot':ok})
    (out/'ergebnisse.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    return 0 if all(r['entfernen_macht_test_rot'] for r in results) else 1

if __name__=='__main__':raise SystemExit(main())
