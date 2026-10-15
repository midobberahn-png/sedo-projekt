@echo off
setlocal
set "SEDO_HOME=%APPDATA%\SEDO-Test-3.21.0.2"
set "SEDO_PROGRAM=%LOCALAPPDATA%\Programs\SEDO Rechnungstool\SEDO Rechnungstool.exe"
if not exist "%SEDO_PROGRAM%" (
  echo SEDO wurde am Standardort nicht gefunden. Bitte Setup mit den Voreinstellungen installieren.
  pause
  exit /b 1
)
if not exist "%SEDO_HOME%" mkdir "%SEDO_HOME%"
if not exist "%SEDO_HOME%" (
  echo Der getrennte Testdatenordner konnte nicht angelegt werden.
  pause
  exit /b 1
)
start "" "%SEDO_PROGRAM%"
if errorlevel 1 (
  echo SEDO konnte nicht gestartet werden.
  pause
  exit /b 1
)
