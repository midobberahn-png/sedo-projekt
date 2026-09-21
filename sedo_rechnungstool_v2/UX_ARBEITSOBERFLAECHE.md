# Belegvorschau, Mailabruf und Navigation

## Bedienung

- Eingangsrechnungen: Dateiname und „Originalbeleg öffnen“ zeigen denselben unveränderten Beleg direkt in SEDO. PDF über die native PDF-Ansicht, Rasterbilder als Vorschau, XML als reiner Text. „Angaben prüfen“ öffnet die bisherige Prüfmaske. Herunterladen bleibt separat möglich.
- Aktive, eingerichtete Postfächer werden beim Programmstart und anschließend alle fünf Minuten abgerufen, solange die Desktop-App läuft. Ein eigener Dauerbetrieb bei geschlossener App ist nicht eingerichtet. Es handelt sich um regelmäßigen IMAP-Abruf.
- Nur der Mailbereich zeigt während des Abrufs eine kleine Anzeige „x/x Nachrichten geladen“. Die Zähler beziehen sich auf die bereits geladenen und insgesamt bekannten Nachrichten der gerade abgerufenen Postfächer; bei neuen Ordnerinventaren kann sich die Gesamtzahl ändern. Große Altbestände werden wie bisher in begrenzten Paketen nachgeladen.
- Neue Nachrichten erscheinen automatisch in der Liste. Eine laufende Auswahl bleibt erhalten; dann wartet die Aktualisierung bis die Auswahl aufgehoben wird. Entwürfe und die geöffnete Nachricht werden nicht neu geladen.
- Die linke Leiste zeigt im Ruhezustand Symbole, klappt beim Überfahren aus und beim Verlassen wieder ein. Tastaturnavigation hält sie während des Fokus offen. Auf Touch-/kleinen Bildschirmen öffnet der Menüknopf die Leiste.
- Tägliche Arbeit: Dashboard, E-Mail, Rechnungen, Angebote, Kalender, Eingangsrechnungen.
- Abrechnung: Neue Rechnung, GWG Monatsrechnung, Abo-Rechnung.
- Finanzen: Kassenbuch, Zahlungen, Offene Posten, Monatsabschluss, Auswertung.
- System: Kunden, Administration, Einstellungen. Startklar und Leistungskatalog sind über Einstellungen erreichbar. Der Menüpunkt Aufgaben entfällt; vorhandene Aufgaben bleiben gespeichert.
- Erklärungstexte und Info-Symbole entfallen. Die Texte stehen in BEDIENHINWEISE.md. Fehler, konkrete Zustände und erforderliche Bestätigungen bleiben sichtbar; routinemäßige Erfolgsmeldungen entfallen.

## Schutz und Prüfung

Original-Prüfsumme und Anmeldung werden auch für Vorschauen geprüft. Nur PDF und geprüfte Rasterformate werden eingebettet; XML wird escaped dargestellt. Rastervorschauen haben ein Pixel-Limit. Keine Nachricht wird durch den Abruf als gelesen markiert, und es erfolgt kein automatischer Versand. Die Fortschrittsanzeige enthält keine Zugangsdaten und wird bei Abschluss oder Abbruch entfernt. Bestehende IMAP-Leases, Abrufbudgets und Spam-/Belegregeln bleiben erhalten.

Die Desktop-App aktiviert den nativen Speicherdialog für explizite Downloads gemäß der offiziellen pywebview-API: https://pywebview.flowrl.com/examples/downloads .
