"""H2: hashgesperrter Testbuild, getrennt von Daten und Produktivinstallation.

source erzeugt ein deterministisches Quellpaket aus einem sauberen Git-Stand.
install legt eine NEUE Python-Umgebung an; build erzeugt das native Desktop-Paket.
Kein Rückfall auf ungebundene Pakete, keine automatische Lock-Aktualisierung.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import venv
import zipfile

APP = Path(__file__).resolve().parents[1]
PYTHON = (3, 13, 15)
LABEL = 'h2.1'
INPUTS = ('requirements.txt', 'requirements_desktop.txt',
          'requirements-release.in', 'requirements-release-constraints.txt')
ROOT_FILES = {*INPUTS, 'requirements-release.lock', 'release-lock.json', 'desktop.py',
              'manage.py', 'sedo_desktop.spec', 'build_mac.command', 'BUILD_WINDOWS.bat',
              'TESTSTAND.md', 'BEDIENHINWEISE.md', 'UX_ARBEITSOBERFLAECHE.md',
              'sedo_setup_windows.iss', 'WINDOWS_ERSTINSTALLATION.txt',
              'WINDOWS_UPDATES.md',
              'START_WINDOWS_TEST.bat',
              '.env.example', '.gitattributes', '.gitignore', 'README.md', 'DEPLOYMENT_SECURITY_NOTES.md',
              'LOCAL_CHECKS.md', 'BONSAI.md', 'E_MAIL.md', 'KALENDER.md',
              'RECHNUNGSREGELN.md',
              'DASHBOARD.md', 'SPAMFILTER.md', 'EINGANGSRECHNUNGEN.md',
              'START_HIER.command', 'START_WINDOWS.bat', 'SEDO_BEENDEN.command',
              'SEDO_BEENDEN.bat', 'SEDO.vbs', 'DESKTOP_VERKNUEPFUNG_ERSTELLEN.bat', 'SEDO.app/Contents/Info.plist',
              'SEDO.app/Contents/MacOS/sedo', 'tools/macos-entitlements.plist'}
SUFFIXES = {'.py', '.html', '.css', '.js', '.svg', '.png', '.jpg', '.jpeg',
            '.ico', '.icns', '.xsd', '.xsl', '.xslt', '.sch', '.json', '.md', '.txt'}


class BuildError(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def allowed(name: str) -> bool:
    p = Path(name)
    if p.is_absolute() or '..' in p.parts or '\\' in name:
        return False
    if name in ROOT_FILES or name == 'SEDO.app/Contents/Resources/sedo.icns':
        return True
    if any(part.startswith('.') or part in {'data', 'media', 'backups', 'logs', '__pycache__'} for part in p.parts):
        return False
    return bool(p.parts and p.parts[0] in {'sedo', 'config', 'tools'} and p.suffix in SUFFIXES)


def lock_entries(text: str) -> list[str]:
    """Nur fixierte PyPI-Anforderungen und SHA-256, keine URL/Index-Direktiven."""
    entries, pending = [], ''
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        pending += line.removesuffix('\\').rstrip() + ' '
        if line.endswith('\\'):
            continue
        entry = pending.strip()
        if not re.match(r'^[A-Za-z0-9][A-Za-z0-9_.-]*==[A-Za-z0-9.!+_-]+(?:\s|$)', entry):
            raise BuildError('Lock enthält keine exakt fixierte Paketversion.')
        hashes = re.findall(r'--hash=sha256:[0-9a-f]{64}(?=\s|$)', entry)
        remainder = re.sub(r'--hash=sha256:[0-9a-f]{64}(?=\s|$)', '', entry)
        if not hashes or '--' in remainder or '://' in remainder or '@' in remainder:
            raise BuildError('Lock enthält ungültige Hashes oder Paketquellen.')
        entries.append(entry)
        pending = ''
    if pending or not entries:
        raise BuildError('Unvollständiger Lock.')
    return entries


def validate_lock(app: Path = APP) -> list[str]:
    meta = json.loads((app / 'release-lock.json').read_text(encoding='utf-8'))
    if meta['python'] != '.'.join(map(str, PYTHON)) or set(meta['inputs']) != set(INPUTS):
        raise BuildError('Lock-Metadaten passen nicht zum Buildvertrag.')
    for name, digest in meta['inputs'].items():
        if sha((app / name).read_bytes()) != digest:
            raise BuildError(f'Lock veraltet: {name} wurde geändert. Explizit neu auflösen.')
    data = (app / 'requirements-release.lock').read_bytes()
    if sha(data) != meta['lock_sha256']:
        raise BuildError('Die Prüfsumme des Release-Locks stimmt nicht.')
    entries = lock_entries(data.decode('utf-8'))
    names = {e.split('==')[0].lower().replace('_', '-') for e in entries}
    if not {'django', 'faster-whisper', 'pywebview', 'pyinstaller', 'waitress', 'pip', 'wheel', 'setuptools', 'packaging'} <= names:
        raise BuildError('Im Lock fehlen Pflichtfunktionen oder Buildwerkzeuge.')
    return entries


def run(args: list[str], **kwargs) -> None:
    subprocess.run(args, check=True, **kwargs)


def install(target: Path, app: Path = APP) -> Path:
    if sys.version_info[:3] != PYTHON:
        raise BuildError('Der Testbuild benötigt genau Python ' + '.'.join(map(str, PYTHON)))
    entries = validate_lock(app)
    if target.exists():
        raise BuildError('Build-Umgebung existiert bereits; wird nicht überschrieben.')
    # Portable Python-Distributionen können beim Kopieren des Executables
    # ihren Basis-Pfad verlieren (sys.base_prefix zeigt dann auf /install).
    # Auf Unix auf den Original-Interpreter verweisen; Windows bleibt bei
    # Kopien, da Symlinks dort nicht für jeden Benutzer verfügbar sind.
    venv.EnvBuilder(with_pip=True, symlinks=os.name != 'nt').create(target)
    python = target / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    # Einige Desktop-Abhängigkeiten kommen als sdist. Auch ihr Backend ist
    # gesperrt: vorher aus Hash-Lock installieren, danach KEINE Build-Isolation.
    bootstrap = [e for e in entries if e.split('==')[0] in {'pip', 'setuptools', 'wheel', 'packaging'}]
    with tempfile.TemporaryDirectory(prefix='sedo-bootstrap-') as tmp:
        path = Path(tmp) / 'bootstrap.lock'
        path.write_text('\n'.join(bootstrap) + '\n', encoding='utf-8')
        run([str(python), '-m', 'pip', 'install', '--require-hashes', '--only-binary=:all:', '--no-deps', '-r', str(path)])
    run([str(python), '-m', 'pip', 'install', '--require-hashes', '--no-build-isolation',
         '-r', str(app / 'requirements-release.lock')])
    run([str(python), '-m', 'pip', 'check'])
    return python


def source(output: Path, app: Path = APP) -> Path:
    validate_lock(app)
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=app).decode('utf-8').strip()
    if git('status', '--porcelain', '--untracked-files=no'):
        raise BuildError('Quellpaket nur aus sauberem Git-Stand erstellen.')
    revision, tree = git('rev-parse', 'HEAD'), git('rev-parse', 'HEAD^{tree}')
    stamp = int(git('show', '-s', '--format=%ct', 'HEAD'))
    files = {}
    for name in git('ls-files', '-z', '--', '.').split('\0'):
        if not allowed(name):
            continue
        p = app / name
        if p.is_symlink() or not p.resolve().is_relative_to(app.resolve()):
            raise BuildError('Verknüpfung im Quellpaket: ' + name)
        if not p.is_file():
            raise BuildError('Unvollständiger Checkout: ' + name)
        files[name] = p.read_bytes()
    required = {'manage.py', 'desktop.py', 'sedo_desktop.spec', 'requirements-release.lock',
                'release-lock.json', 'tools/teststand.py', 'config/settings.py', 'sedo/models.py'}
    if not required <= files.keys():
        raise BuildError('Pflichtdateien fehlen im Quellpaket.')
    manifest = {'schema': 1, 'label': LABEL, 'commit': revision, 'tree': tree,
                'python': '.'.join(map(str, PYTHON)), 'kind': 'source-teststand',
                'files': {n: sha(v) for n, v in sorted(files.items())}}
    files['TESTSTAND_MANIFEST.json'] = (json.dumps(manifest, indent=2, ensure_ascii=False) + '\n').encode()
    output.mkdir(parents=True, exist_ok=True)
    dest = output / f'SEDO-{LABEL}-{revision[:12]}-source.zip'
    timestamp = datetime.fromtimestamp(max(stamp, 315532800), timezone.utc).timetuple()[:6]
    with zipfile.ZipFile(dest, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, timestamp)
            info.create_system = 3
            executable = name.endswith('.command') or name == 'SEDO.app/Contents/MacOS/sedo'
            info.external_attr = (0o100755 if executable else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    return dest


def stage_source(archive: Path, target: Path) -> dict:
    """Nur manifestierte Quellen in ein neues Build-Verzeichnis schreiben.

    PyInstaller sieht so keine unversionierten lokalen Module oder alte Artefakte.
    Das Manifest ist ein Integritätsnachweis, keine externe digitale Signatur.
    """
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        manifest = json.loads(z.read('TESTSTAND_MANIFEST.json'))
        files = manifest['files']
        if len(names) != len(set(names)) or set(names) != set(files) | {'TESTSTAND_MANIFEST.json'}:
            raise BuildError('Dateiauswahl des Quellpakets stimmt nicht mit dem Manifest überein.')
        content = {}
        for name, digest in files.items():
            if not allowed(name):
                raise BuildError('Nicht freigegebener Paketpfad: ' + name)
            data = z.read(name)
            if sha(data) != digest:
                raise BuildError('Quellpaket-Prüfsumme falsch: ' + name)
            content[name] = data
    target.mkdir(parents=True, exist_ok=False)
    for name, data in content.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return manifest


def frozen(output: Path, app: Path = APP) -> Path:
    validate_lock(app)
    output.mkdir(parents=True, exist_ok=False)
    source_zip = source(output, app)
    # FileProvider-backed output directories may recreate FinderInfo on apps.
    # Keep signing/verification on a private native filesystem until ZIP creation.
    with tempfile.TemporaryDirectory(prefix='sedo-native-build-',
                                     dir='/private/tmp' if sys.platform == 'darwin' else None) as tmp:
        staged = Path(tmp) / 'source'
        manifest = stage_source(source_zip, staged)
        envpython = install(Path(tmp) / 'venv', staged)
        # Unvollständiges lokales Diktat wird nicht länger als guter Build gewertet.
        build_env = {**os.environ, 'SEDO_HOME': str(Path(tmp) / 'home'), 'PYTHONUTF8': '1',
                     'DJANGO_SECRET_KEY': 'isolierter-buildtest-kein-betriebsschluessel'}
        (Path(tmp) / 'home/data').mkdir(parents=True)
        # Auch plattformspezifische Dateitausch-/Sperrfehler vor dem Paketbau prüfen.
        # A freshly staged Windows environment can exceed ten minutes for the
        # complete suite on a shared runner. Keep a finite but realistic limit.
        run([str(envpython), 'manage.py', 'test', 'sedo', '--noinput'],
            cwd=staged, env=build_env, timeout=1200)
        run([str(envpython), '-c', 'from faster_whisper import WhisperModel; import webview, waitress'], env=build_env)
        native_dist = Path(tmp) / 'desktop' if sys.platform == 'darwin' else output / 'desktop'
        run([str(envpython), '-m', 'PyInstaller', str(staged / 'sedo_desktop.spec'),
             '--noconfirm', '--workpath', str(Path(tmp) / 'work'), '--distpath', str(native_dist)], cwd=staged, env=build_env)
        if sys.platform == 'darwin':
            bundle = native_dist / 'SEDO Rechnungstool.app'
            exe = bundle / 'Contents/MacOS/SEDO Rechnungstool'
            run([str(envpython), str(staged / 'tools/macos_bundle.py'), str(bundle),
                 '--clean-build-metadata', '--report', str(output / 'bundle-check.json')])
        elif os.name == 'nt':
            bundle = output / 'desktop/SEDO Rechnungstool'
            exe = bundle / 'SEDO Rechnungstool.exe'
        else:
            raise BuildError('Native Desktop-Testpakete werden auf Windows/macOS gebaut.')
        report = output / 'self-check.json'
        env = {**os.environ, 'SEDO_CHECK_REPORT': str(report),
               'DJANGO_SECRET_KEY': 'isolierter-buildtest-kein-betriebsschluessel'}
        run([str(exe), '--check-strict'], env=env, timeout=120)
        check = json.loads(report.read_text(encoding='utf-8'))
        if not (check.get('ok') and check.get('whisper_available') and check.get('frozen')):
            raise BuildError('Der gebündelte Selbsttest ist nicht vollständig bestanden.')
        installed = subprocess.check_output([str(envpython), '-m', 'pip', 'freeze', '--all'], text=True)
        (output / 'installed.txt').write_text(installed, encoding='utf-8')
        name = f"SEDO-{LABEL}-{manifest['commit'][:12]}-{platform.system()}-{platform.machine()}"
        if sys.platform == 'darwin':
            archive = output / (name + '.zip')
            run(['ditto', '-c', '-k', '--norsrc', '--noextattr', '--keepParent', str(bundle), str(archive)])
            with zipfile.ZipFile(archive) as package:
                if package.testzip() is not None:
                    raise BuildError('Das native Mac-Archiv ist beschädigt.')
            extracted = Path(tmp) / 'archive-check'
            run(['ditto', '-x', '-k', str(archive), str(extracted)])
            # Never clean the extracted copy: the distributed archive itself
            # must survive strict verification, including all resource symlinks.
            run([str(envpython), str(staged / 'tools/macos_bundle.py'), str(extracted / bundle.name),
                 '--report', str(output / 'archive-check.json')])
            # This convenient workspace copy is for GUI checks, not signature
            # evidence: a local FileProvider may add Finder metadata immediately.
            run(['ditto', '-x', '-k', '--norsrc', '--noextattr', str(archive), str(output / 'desktop')])
        else:
            archive = Path(shutil.make_archive(str(output/name), 'zip', bundle.parent, bundle.name))
    manifest.update(kind='native-teststand', os=platform.system(), machine=platform.machine(),
                    lock_sha256=sha((app/'requirements-release.lock').read_bytes()),
                    self_check=check, target_pc_tested=False, signed_release=False)
    if sys.platform == 'darwin':
        manifest['macos_signature_evidence'] = 'private-build-and-freshly-extracted-archive'
        manifest['desktop_copy_purpose'] = 'local-gui-check'
        for label in ('bundle-check', 'archive-check'):
            evidence = json.loads((output / (label + '.json')).read_text(encoding='utf-8'))
            manifest[label] = {key: value for key, value in evidence.items() if key != 'binaries'}
    manifest['archive_sha256'] = sha(archive.read_bytes())
    (output / 'BUILD_MANIFEST.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['source', 'install', 'build', 'validate'])
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        if args.action == 'validate':
            print(f'Lock OK: {len(validate_lock())} plattformabhängige Pakete.')
        elif args.output is None:
            parser.error('--output ist erforderlich.')
        else:
            result = {'source': source, 'install': install, 'build': frozen}[args.action](args.output.resolve())
            print(result)
    except (BuildError, OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f'TESTBUILD ABGEBROCHEN: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
