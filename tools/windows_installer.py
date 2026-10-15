"""Create a first-installation ZIP from a successful, hash-verified Windows gate.

No operating data, passwords, model downloads or GitHub runners are involved.
The target computer still needs its own first-start acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import subprocess
import tempfile
import zipfile
from urllib.parse import urlsplit

APP = Path(__file__).resolve().parents[1]
PACKAGING_FILES = {'sedo_setup_windows.iss', 'WINDOWS_ERSTINSTALLATION.txt',
                   'START_WINDOWS_TEST.bat',
                   'tools/windows_installer.py', 'sedo/tests/test_windows_installer.py', 'TESTSTAND.md'}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verified_build(checks: Path):
    report = json.loads((checks / 'report.json').read_text(encoding='utf-8'))
    native = json.loads((checks / 'native/BUILD_MANIFEST.json').read_text(encoding='utf-8'))
    steps = report.get('steps', [])
    regression_steps = ('install', 'dependencies', 'environment', 'tests', 'sqlite',
                        'index-50000', 'migrationen')
    probes = ('entwurf', 'belegketten', 'indizes', 'belegarchiv', 'mahnwaechter',
              'abo_zahlungsziel', 'bedienrisiken', 'teststand', 'mail', 'incoming', 'spam', 'calendar')
    required = {'source-a', 'source-b', 'native-build'}
    required.update(f'django-{series}-{step}' for series in ('5.2', '6.0')
                    for step in (*regression_steps, *('gegenproben-' + p for p in probes)))
    if (report.get('status') != 'passed' or not report.get('development_ready')
            or report.get('host', {}).get('os') != 'Windows'
            or not required <= {s['name'] for s in steps}
            or any(s.get('status') != 'passed' for s in steps)):
        raise ValueError('Vollständige grüne Windows-Prüfung fehlt.')
    if (native.get('os') != 'Windows' or native.get('kind') != 'native-teststand'
            or any(native.get(k) != report.get(k) for k in ('commit', 'tree'))
            or not all(native.get('self_check', {}).get(k) for k in ('ok', 'frozen', 'whisper_available'))):
        raise ValueError('Windows-Build stimmt nicht mit dem Prüfbericht überein.')
    archives = [p for p in (checks / 'native').glob('*.zip')
                if digest(p) == native.get('archive_sha256')]
    if len(archives) != 1:
        raise ValueError('Geprüftes Windows-Paket fehlt oder wurde verändert.')
    return report, native, archives[0]


def unpack(archive: Path, target: Path):
    with zipfile.ZipFile(archive) as z:
        for entry in z.infolist():
            path = PurePosixPath(entry.filename)
            if (not path.parts or path.parts[0] != 'SEDO Rechnungstool'
                    or '..' in path.parts or '\\' in entry.filename or ':' in entry.filename
                    or any(p in {'.env', '.secret_key'} or p.endswith(('.sqlite3', '.sqlite')) for p in path.parts)
                    or (entry.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError('Unerlaubte Datei im Windows-Paket: ' + entry.filename)
        z.extractall(target)
    bundle = target / 'SEDO Rechnungstool'
    exe = bundle / 'SEDO Rechnungstool.exe'
    with exe.open('rb') as f:
        header = f.read(64)
        if header[:2] != b'MZ':
            raise ValueError('Windows-Programm fehlt.')
        f.seek(struct.unpack_from('<I', header, 60)[0])
        if f.read(6) != b'PE\x00\x00\x64\x86':
            raise ValueError('Installer benötigt das x64-Windows-Programm.')
    return bundle


def verify_app_sources(current: dict, tested: dict):
    current = {k: v for k, v in current.items() if k not in PACKAGING_FILES}
    tested = {k: v for k, v in tested.items() if k not in PACKAGING_FILES}
    if current != tested:
        raise ValueError('Anwendungsquellen weichen vom vollständig geprüften Stand ab.')


def recipe_provenance(native: dict):
    from teststand import allowed
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=APP).decode('utf-8').strip()
    if git('status', '--porcelain', '--untracked-files=no'):
        raise ValueError('Installer nur aus sauberem Git-Stand bauen.')
    current = {n: digest(APP / n) for n in git('ls-files', '-z', '--', '.').split('\0') if allowed(n)}
    verify_app_sources(current, native['files'])
    return {'commit': git('rev-parse', 'HEAD'),
            'files': {n: current[n] for n in sorted(PACKAGING_FILES)}}


def checked_runtime_url(url: str):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.netloc != 'download.visualstudio.microsoft.com'
            or not re.fullmatch(r'/download/pr/[A-Za-z0-9/._-]+/VC_redist\.x64\.exe', parsed.path)
            or parsed.query or parsed.fragment):
        raise ValueError('Feste offizielle Microsoft-Downloadadresse erforderlich.')
    return url


def microsoft_signature(path: Path):
    env = {**os.environ, 'SEDO_WEBVIEW_BOOTSTRAPPER': str(path)}
    command = ("$s=Get-AuthenticodeSignature $env:SEDO_WEBVIEW_BOOTSTRAPPER; "
               "if ($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch 'CN=Microsoft Corporation,') {exit 1}; "
               "(Get-Item -LiteralPath $env:SEDO_WEBVIEW_BOOTSTRAPPER).VersionInfo.FileVersion")
    version = subprocess.check_output(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                                     env=env, timeout=120).decode('utf-8').strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+\.\d+', version):
        raise ValueError('Ungültige Microsoft-Laufzeitversion.')
    return version


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checks', required=True, type=Path)
    parser.add_argument('--iscc', required=True, type=Path)
    parser.add_argument('--webview-bootstrapper', required=True, type=Path)
    parser.add_argument('--vc-runtime', required=True, type=Path)
    parser.add_argument('--vc-runtime-url', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--version', default='2026.9.22')
    args = parser.parse_args()
    if os.name != 'nt' or not re.fullmatch(r'\d+\.\d+\.\d+(?:\.\d+)?', args.version):
        parser.error('Auf Windows mit numerischer Versionsnummer ausführen.')
    report, native, archive = verified_build(args.checks.resolve())
    # Installer-only revisions carry separate provenance and a separate installation smoke test.
    # EVERY application source and application test must still match the full regression gate.
    recipe = recipe_provenance(native)
    bootstrapper = args.webview_bootstrapper.resolve()
    microsoft_signature(bootstrapper)
    vc_runtime = args.vc_runtime.resolve()
    vc_version = microsoft_signature(vc_runtime)
    vc_url = checked_runtime_url(args.vc_runtime_url)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='sedo-installer-') as tmp:
        bundle = unpack(archive, Path(tmp))
        subprocess.run([str(args.iscc.resolve()), '/Qp', '/DBundleDir=' + str(bundle),
                        '/DInstallerOutputDir=' + str(output), '/DAppVersion=' + args.version,
                        '/DWebViewInstaller=' + str(bootstrapper), '/DVCRuntimeURL=' + vc_url,
                        '/DVCRuntimeHash=' + digest(vc_runtime), '/DVCRuntimeVersion=' + vc_version,
                        str(APP / 'sedo_setup_windows.iss')], check=True)
    setup = output / f'SEDO_Rechnungstool_Setup_{args.version}.exe'
    shutil.copyfile(APP / 'WINDOWS_ERSTINSTALLATION.txt', output / 'START_HIER.txt')
    shutil.copyfile(APP / 'START_WINDOWS_TEST.bat', output / 'START_WINDOWS_TEST.bat')
    evidence = {'version': args.version, 'commit': native['commit'], 'tree': native['tree'],
                'installer_recipe': recipe,
                'setup_sha256': digest(setup), 'native_archive_sha256': native['archive_sha256'],
                'windows_checks': len(report['steps']), 'windows_host': report['host'],
                'webview_bootstrapper_sha256': digest(bootstrapper),
                'vc_runtime': {'url': vc_url, 'version': vc_version, 'sha256': digest(vc_runtime)},
                'signed_release': False, 'target_pc_tested': False}
    (output / 'BUILD_INFO.json').write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
    package = output / f'SEDO_Windows_Installer_{args.version}.zip'
    with zipfile.ZipFile(package, 'x', compression=zipfile.ZIP_STORED) as z:
        for path in (setup, output / 'START_HIER.txt', output / 'START_WINDOWS_TEST.bat',
                     output / 'BUILD_INFO.json'):
            z.write(path, path.name)
    (output / 'SHA256.txt').write_text(digest(package) + '  ' + package.name + '\n', encoding='ascii')
    print(package)


if __name__ == '__main__':
    main()
