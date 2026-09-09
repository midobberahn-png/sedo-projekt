# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller-Spec: SEDO als gebündeltes Desktop-Programm (Mac + Windows).

Build:  pyinstaller sedo_desktop.spec   (im Projektordner, venv mit
        requirements_desktop.txt). Ergebnis: dist/SEDO Rechnungstool[.app/.exe]

Seit v2.3 MIT faster-whisper (Mireks Vorgabe: „das Diktat sollte drin bleiben“):
ctranslate2/PyAV & Co. werden mitgebündelt (~+180 MB), das Whisper-Modell lädt
weiterhin beim ersten Diktat in den SEDO-Datenordner. Die App bringt die
Mikrofon-Berechtigung mit (NSMicrophoneUsageDescription).
"""
import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules

django_datas, django_binaries, django_hidden = collect_all('django')

# Lokales Whisper-Diktat (faster-whisper + native Abhängigkeiten)
_whisper_packages = ['faster_whisper', 'ctranslate2', 'av', 'tokenizers', 'huggingface_hub', 'onnxruntime']
whisper_datas, whisper_binaries, whisper_hidden = [], [], []
for _pkg in _whisper_packages:
    try:
        _d, _b, _h = collect_all(_pkg)
        whisper_datas += _d; whisper_binaries += _b; whisper_hidden += _h
    except Exception:
        pass  # Paket nicht installiert → Diktat fällt im Bundle weg (stt_unavailable)

hiddenimports = (
    django_hidden
    + collect_submodules('sedo')          # inkl. Migrationen + management.commands
    + whisper_hidden
    + ['config.settings', 'config.urls', 'config.wsgi',
       'whitenoise', 'whitenoise.middleware', 'whitenoise.storage',
       'waitress', 'fints', 'mt940']
)

datas = django_datas + whisper_datas + [
    ('sedo/templates', 'sedo/templates'),
    ('sedo/static', 'sedo/static'),
]

a = Analysis(
    ['desktop.py'],
    pathex=[],
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
    console=False,                 # echtes Fensterprogramm, kein Terminal
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
    app = BUNDLE(
        coll,
        name='SEDO Rechnungstool.app',
        icon='SEDO.app/Contents/Resources/sedo.icns',
        bundle_identifier='de.sedo-magdeburg.rechnungstool',
        info_plist={
            'CFBundleDisplayName': 'SEDO Rechnungstool',
            'CFBundleShortVersionString': '2.3.0',
            'NSMicrophoneUsageDescription':
                'SEDO nutzt das Mikrofon für das lokale Diktat von Angebotspositionen. '
                'Die Aufnahme wird auf diesem Rechner transkribiert und danach gelöscht.',
            'NSHighResolutionCapable': True,
        },
    )
