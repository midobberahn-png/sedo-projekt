@echo off
REM ─────────────────────────────────────────────────────────────────
REM SEDO Rechnungstool – Windows-Komplettpaket bauen (v2.2)
REM Auf einem Windows-Rechner (z. B. Parallels-VM) per Doppelklick
REM ausführen. Ergebnis: dist\SEDO Rechnungstool\SEDO Rechnungstool.exe
REM Voraussetzung: Python 3.12+ von python.org ("Add to PATH" anhaken).
REM ─────────────────────────────────────────────────────────────────
cd /d "%~dp0"
if not exist .venv_build (
  python -m venv .venv_build
)
call .venv_build\Scripts\activate.bat
python -m pip install --upgrade pip
pip install "Django>=5.2,<6.1" whitenoise pandas openpyxl reportlab pypdf lxml python-dateutil qrcode[pil] fints mt-940 faster-whisper
pip install -r requirements_desktop.txt
pyinstaller sedo_desktop.spec --noconfirm
if errorlevel 1 (
  echo BUILD FEHLGESCHLAGEN – Meldungen oben pruefen.
  pause
  exit /b 1
)
"dist\SEDO Rechnungstool\SEDO Rechnungstool.exe" --check

REM ── Installer bauen (wenn Inno Setup 6 installiert ist) ──────────
set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist %ISCC% (
  %ISCC% sedo_setup_windows.iss
  echo.
  echo Fertig: dist\SEDO_Rechnungstool_Setup_*.exe  ^(Installer = Update-Datei^)
) else (
  echo.
  echo HINWEIS: Inno Setup 6 nicht gefunden - Installer uebersprungen.
  echo Kostenlos laden: https://jrsoftware.org/isinfo.php - danach dieses
  echo Skript erneut ausfuehren, dann entsteht dist\SEDO_Rechnungstool_Setup.exe
  echo Alternativ den Ordner "dist\SEDO Rechnungstool" komplett kopieren.
)
pause
