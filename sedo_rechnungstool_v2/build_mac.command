#!/bin/bash
# H2-Testpaket, kein signierter/notarisierter Produktivinstaller.
set -euo pipefail
cd "$(dirname "$0")"
OUTPUT="${TMPDIR:-/tmp}/SEDO-Testbuild-$(date +%Y%m%d-%H%M%S)-$$"
python3 tools/teststand.py build --output "$OUTPUT"
echo "Geprüftes Testpaket und Manifest: $OUTPUT"
echo "Keine Installation und keine Änderung vorhandener SEDO-Daten ausgeführt."
