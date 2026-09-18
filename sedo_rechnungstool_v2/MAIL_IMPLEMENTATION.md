# Integriertes E-Mail-Modul

Ziel: United-Domains-Postfächer `info@sedo-magdeburg.de` und
`rechnung@sedo-magdeburg.de` in SEDO bedienen. Nr. 11 (neue KI-Funktionen) bleibt
zurückgestellt. Bestehende Rechnungs-/Angebotsregeln bleiben erhalten.

## Umsetzung und Abnahme

- IMAP über geprüftes TLS, Postfach-/Ordnerabgleich mit UIDVALIDITY und UIDs.
- Lesen, Suche, Anhänge, ungelesen/markiert, Verschieben, Papierkorb.
- Verfassen, Antworten/Allen antworten/Weiterleiten, lokale Entwürfe,
  SMTP über geprüftes TLS und Versandkopie auf dem Server.
- Kein automatischer Neuversand bei unklarem SMTP-Ergebnis.
- Geschützte lokale Zugangsdaten, keine Mail-Passwörter in Backups.
- Optionaler Angebots-/Aufgabenbezug mit erhaltener E-Mail-Quelle.
- Automatischer Abruf bei laufender Desktop-App, manueller Abruf und
  sichtbarer Fortschritt/Fehler. Keine Netzverbindung ohne eingerichtetes Konto.
- Tests mit simulierten Nachrichten/Servern; keine echten Mails versenden oder
  löschen. Reale Verbindung erst mit in der App hinterlegten Zugangsdaten prüfen.
- Vollständige Django-Regression, Gegenproben und native macOS-/Windows-Matrix,
  PR und Integration nur bei grünem Stand.

## Grenzen und Umstieg

IMAP übernimmt auf dem Server liegende Nachrichten und Ordner. Lokale Outlook-
Archive/PST-Dateien wären gesondert zu übernehmen. Der Nutzer hat bestätigt,
dass sämtliche E-Mails auf dem Mailserver liegen.
Kalender, Kontakte und Outlook-Regeln sind kein Teil dieses Mail-Auftrags.
Ein Live-Abnahmetest beider Postfächer und die Prüfung lokaler Archive gehören
zum endgültigen Outlook-Umstieg; automatisierte Tests ersetzen ihn nicht.
