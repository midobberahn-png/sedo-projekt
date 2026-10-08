@echo off
REM H2-Testpaket, kein signierter Produktivinstaller. Python 3.13.15 erforderlich.
setlocal
cd /d "%~dp0"
set "OUTPUT=%TEMP%\SEDO-Testbuild-%RANDOM%-%RANDOM%"
python tools\teststand.py build --output "%OUTPUT%"
if errorlevel 1 (
  echo TESTBUILD FEHLGESCHLAGEN. Keine Installation ausgefuehrt.
  pause
  exit /b 1
)
echo Geprueftes Testpaket und Manifest: %OUTPUT%
echo Kein Inno-Setup-Installer und keine vorhandenen SEDO-Daten veraendert.
pause
endlocal
