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
- Automatischer Abruf alle fünf Minuten bei laufender Desktop-App mit
  sichtbarem Fortschritt/Fehler ausschließlich im Mailbereich. Keine Netzverbindung ohne eingerichtetes Konto.
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

## Überarbeitung Postfach vom 22.09.2026

- Aktionskontext enthält ausschließlich freigegebene Filterparameter;
  Nachrichtenlinks tragen die konkrete MailLocation mit UID/UIDVALIDITY.
  Eine veraltete Auswahl weicht nicht auf eine andere Ordnerkopie aus.
- Manuelle MOVE-Aktionen verwenden wie Spam-Aktionen das dauerhafte
  Verschiebejournal. Unklare Antworten sperren Wiederholungen bis zum Abgleich.
- Ein exaktes COPYUID-Tupel nach bestätigtem MOVE macht die Zielkopie sofort
  sichtbar. Quell-UID, UIDVALIDITY, Wertebereich und bestehende Cacheeinträge
  werden geprüft; ohne belastbare Zuordnung übernimmt der nächste Abruf.
  Grundlagen: https://www.rfc-editor.org/rfc/rfc6851 und
  https://www.rfc-editor.org/rfc/rfc4315.
- Die Datenbanksperre bleibt führend. Prozessinterne Vordergrundanforderungen
  lassen den Worker an sicheren Heartbeats pausieren, auch zwischen kompletten
  Belegübernahmen. Benutzeraktionen warten begrenzt; ihre Operation wird nie
  automatisch wiederholt. Ein erfolgreicher Abrufzeitpunkt wird erst nach
  der Belegübernahme veröffentlicht.
- Spam-Bestätigungen binden Nutzer, Auswahl, UIDVALIDITY, Regelrevision und
  manuelle Entscheidung sowie Klassifikation. Ein bloß erneuerter Prüfzeitpunkt
  macht eine unveränderte Auswahl nicht ungültig.
- Belegmarkierungen beruhen auf IncomingInvoiceSource und dem aktuellen
  Prüfzustand, nicht auf invoice_scan_done. Keine Datenmodelländerung.
- Lokale Testabnahme: vollständige Django-5.2-/6.0-Matrix, Gegenproben,
  SQLite-Parallelität, Indexvergleich und nativer macOS-Build. Die kostenpflichtigen
  GitHub-Workflows bleiben während der Entwicklung ausgesetzt. Windows/Linux
  benötigen weiterhin eine eigene Abnahme des jeweiligen Standes.
