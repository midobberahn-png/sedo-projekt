# Deployment- und Sicherheitsnotizen

## Betriebsmodi (seit v2.0.1)

SEDO kennt zwei grundverschiedene Betriebsarten – NICHT mischen:

**LOKAL (Einzelplatz, Standard):**
- `DJANGO_DEBUG` bleibt aus (Default), HTTP auf 127.0.0.1/Heimnetz ist okay.
- Medien (Rechnungs-PDFs) sind seit v2.0.1 grundsätzlich NUR mit Login
  abrufbar – auch lokal (kostet nichts, die Browser-Session bleibt bestehen).

**NETZWERK / CLOUDFLARE-TUNNEL (extern erreichbar):**
Vor dem Freischalten eines Tunnels zwingend in der `.env` setzen:

```
DJANGO_SECURE_SSL_REDIRECT=1
DJANGO_SESSION_COOKIE_SECURE=1
DJANGO_CSRF_COOKIE_SECURE=1
DJANGO_ALLOWED_HOSTS=<nur die echten Hostnamen, kein Wildcard>
DJANGO_CSRF_TRUSTED_ORIGINS=https://<hostname>
```

Der Security-Status in den Einstellungen zeigt an, ob diese Punkte aktiv
sind. Ohne sie gehört die Anwendung nicht ins Internet – Rechnungs-PDFs
enthalten personenbezogene Geschäftsdaten.

## Update auf bestehendem Hetzner-Server

1. ZIP nach `/opt/` hochladen.
2. Bestehenden App-Ordner vorher sichern.
3. Neue Version entpacken bzw. Dateien übernehmen.
4. Im App-Ordner ausführen:

```bash
cd /opt/sedo-rechnung
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl restart sedo-rechnung
sudo systemctl status sedo-rechnung --no-pager
```

## Serverstatus prüfen

```bash
sudo ufw status
sudo systemctl status nginx --no-pager
sudo systemctl status sedo-rechnung --no-pager
sudo fail2ban-client status sshd
sudo certbot renew --dry-run
```

## Bankanbindung erst nach diesen Punkten

- SSH nur per Key
- Root-Login deaktiviert
- Passwort-Login deaktiviert
- HTTPS aktiv
- Audit-Log aktiv
- Externe, verschlüsselte Backups geplant
- `.env` und Bank-Zugangsdaten nicht in App-Backups
- Rollen/Rechte für Admin/Buchhaltung/Mitarbeiter getrennt
