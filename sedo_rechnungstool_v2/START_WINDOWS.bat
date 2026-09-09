@echo off
cd /d %~dp0
if not exist .venv python -m venv .venv
call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
if not exist data mkdir data
if not exist media mkdir media
REM Kein makemigrations auf Endnutzer-Rechnern (Review-Fund F-24): Es ist ein
REM Entwicklerwerkzeug und erzeugt lokal eigene Migrationsdateien, sobald Modelle
REM und Migrationen auseinanderlaufen. Beim naechsten Update trifft die lokale
REM 00xx_auto_....py auf die offizielle Migration, der Start bricht mit
REM "Conflicting migrations detected" ab. Nur anwenden, nie erzeugen.
python manage.py migrate --noinput
python manage.py ensure_admin
echo Öffne im Browser: http://127.0.0.1:8000
echo Login: admin (Passwort wurde beim ersten Start einmalig angezeigt)
echo Passwort aendern: python manage.py changepassword admin
python manage.py runserver 0.0.0.0:8000
pause
