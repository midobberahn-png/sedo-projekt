#!/bin/bash
# SEDO Rechnungstool – Mac-Komplettpaket bauen (v2.2)
# Ergebnis: dist/SEDO Rechnungstool.app
set -e
cd "$(dirname "$0")"
if [ ! -d .venv_build ]; then python3 -m venv .venv_build; fi
source .venv_build/bin/activate
python -m pip install --upgrade pip >/dev/null
pip install 'Django>=5.2,<6.1' whitenoise pandas openpyxl reportlab pypdf lxml python-dateutil 'qrcode[pil]' fints mt-940 faster-whisper >/dev/null
pip install -r requirements_desktop.txt >/dev/null
pyinstaller sedo_desktop.spec --noconfirm
"dist/SEDO Rechnungstool.app/Contents/MacOS/SEDO Rechnungstool" --check
echo "Fertig: dist/SEDO Rechnungstool.app"
