#!/bin/bash
set -e
cd "$(dirname "$0")"
echo "SEDO Rechnungstool v1.0 beta · Django UI-Strang · Start"
if [ ! -d ".venv" ]; then
  echo "Erstelle lokale Python-Umgebung..."
  python3 -m venv .venv
fi
source .venv/bin/activate
HASHFILE=.requirements.hash
NEWHASH=$(shasum requirements.txt | awk '{print $1}')
OLDHASH=""
if [ -f "$HASHFILE" ]; then OLDHASH=$(cat "$HASHFILE"); fi
if [ "$NEWHASH" != "$OLDHASH" ]; then
  echo "Installiere/aktualisiere Pakete..."
  python -m pip install --upgrade pip
  pip install -r requirements.txt
  echo "$NEWHASH" > "$HASHFILE"
else
  echo "Pakete sind aktuell."
fi
mkdir -p data media logs
# Kein makemigrations auf Endnutzer-Rechnern (Review-Fund F-24): Es ist ein
# Entwicklerwerkzeug und erzeugt lokal eigene Migrationsdateien, sobald Modelle
# und Migrationen auseinanderlaufen. Beim naechsten Update trifft die lokale
# 00xx_auto_....py auf die offizielle Migration -> "Conflicting migrations
# detected", und die App startet nicht mehr. Nur anwenden, nie erzeugen.
python manage.py migrate --noinput
python manage.py ensure_admin
echo ""
echo "Öffne im Browser: http://127.0.0.1:8000"
echo "Login: admin – das Passwort wurde beim allerersten Start einmalig angezeigt."
echo "Passwort vergessen/ändern: python manage.py changepassword admin"
python manage.py runserver 0.0.0.0:8000
