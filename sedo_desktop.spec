# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller-Spec: SEDO als gebündeltes Desktop-Programm (Mac + Windows).

Build über tools/teststand.py; gesperrte Versionen in requirements-release.lock.
Das lokale Whisper-Diktat bleibt Bestandteil des Komplettpakets. Das Modell
wird erst beim ersten Diktat geladen, nicht bei Build oder Selbsttest.
"""
import sys
import plistlib
import runpy
from pathlib import Path
from importlib.metadata import distributions
from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

signing_identity = runpy.run_path(str(Path(SPECPATH) / 'tools/macos_signing.py'))['codesign_identity']()

django_datas, django_binaries, django_hidden = collect_all('django')

_whisper_packages = ['faster_whisper', 'ctranslate2', 'av', 'tokenizers', 'huggingface_hub', 'onnxruntime']
whisper_datas, whisper_binaries, whisper_hidden = [], [], []
for _pkg in _whisper_packages:
    _d, _b, _h = collect_all(_pkg)
    whisper_datas += _d
    whisper_binaries += _b
    whisper_hidden += _h

hiddenimports = (
    django_hidden
    + collect_submodules('sedo', filter=lambda name: not name.startswith('sedo.tests'))
    # pandas loads both Excel engines dynamically; exercise each in --check-strict.
    + collect_submodules('openpyxl')
    + collect_submodules('xlrd')
    + whisper_hidden
    + ['desktop', 'config.settings', 'config.urls', 'config.wsgi',
       'whitenoise', 'whitenoise.middleware', 'whitenoise.storage',
       'waitress', 'fints', 'mt940']
)

calendar_datas, calendar_hidden = [], []
for _pkg in ('icalendar', 'recurring_ical_events', 'x_wr_timezone', 'tzdata'):
    _d, _b, _h = collect_all(_pkg)
    calendar_datas += _d
    calendar_hidden += _h
hiddenimports += calendar_hidden

datas = django_datas + whisper_datas + calendar_datas + [
    ('sedo/templates', 'sedo/templates'),
    ('sedo/static', 'sedo/static'),
    ('sedo/validation', 'sedo/validation'),
]
# Keep operating instructions and the original dependency notices with the
# actual app, not only with the separately published source archive. This build
# environment contains exclusively the hash-locked release dependencies/tools.
for document in ('README.md', 'DEPLOYMENT_SECURITY_NOTES.md', 'BEDIENHINWEISE.md',
                 'BONSAI.md', 'E_MAIL.md', 'KALENDER.md', 'EINGANGSRECHNUNGEN.md',
                 'DASHBOARD.md', 'SPAMFILTER.md'):
    datas.append((document, 'docs'))
for distribution in distributions():
    datas += copy_metadata(distribution.metadata['Name'])
python_license = next((candidate for candidate in (
    Path(sys.base_prefix) / 'LICENSE.txt',
    Path(sys.base_prefix) / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'LICENSE.txt',
) if candidate.is_file()), None)
if python_license is None:
    raise RuntimeError('The bundled Python runtime license must accompany the release.')
datas.append((str(python_license), 'licenses/python'))

a = Analysis(
    ['tools/desktop_entry.py'],
    pathex=[SPECPATH],
    binaries=django_binaries + whisper_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'scipy', 'IPython', 'pytest'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SEDO Rechnungstool',
    debug=False,
    strip=False,
    upx=False,
    console=False,
    # PyInstaller carries this through COLLECT into BUNDLE, signing embedded
    # libraries as well as the main executable with the same distribution ID.
    # An unset value deliberately keeps this an ad-hoc signed test package.
    codesign_identity=signing_identity,
    # Hardened Runtime needs this narrow capability for the existing local
    # microphone dictation; no runtime/library-validation exceptions are used.
    entitlements_file=(str(Path(SPECPATH) / 'tools/macos-entitlements.plist')
                       if signing_identity else None),
    icon='SEDO.app/Contents/Resources/sedo.icns' if sys.platform == 'darwin' else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='SEDO Rechnungstool',
)

if sys.platform == 'darwin':
    # Keep the native package aligned with the source launcher metadata.
    source_info = plistlib.loads((Path(SPECPATH) / 'SEDO.app/Contents/Info.plist').read_bytes())
    app = BUNDLE(
        coll,
        name='SEDO Rechnungstool.app',
        icon='SEDO.app/Contents/Resources/sedo.icns',
        bundle_identifier='de.sedo-magdeburg.rechnungstool',
        info_plist={
            'CFBundleDisplayName': 'SEDO Rechnungstool',
            'CFBundleShortVersionString': source_info['CFBundleShortVersionString'],
            'CFBundleVersion': source_info['CFBundleVersion'],
            # The locked ARM64 av/numpy/onnxruntime wheels require macOS 14.
            # tools/macos_bundle.py checks every embedded Mach-O deployment target.
            'LSMinimumSystemVersion': '14.0',
            'LSArchitecturePriority': ['arm64'],
            'NSMicrophoneUsageDescription':
                'SEDO nutzt das Mikrofon für das lokale Diktat von Angebotspositionen. '
                'Die Aufnahme wird auf diesem Rechner transkribiert und danach gelöscht.',
            'NSHighResolutionCapable': True,
        },
    )
