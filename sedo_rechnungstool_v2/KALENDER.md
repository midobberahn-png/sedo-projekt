# Kalender und iCloud

## Nutzung

- **Kalender** in der Hauptnavigation: Monatsansicht und Terminliste. Auf kleinen
  Bildschirmen wird die Terminliste angezeigt. Termine können lokal angelegt,
  bearbeitet und nach ausdrücklicher Bestätigung gelöscht werden.
- **Dashboard → Zahnrad → Kalender** fügt die Kachel mit den nächsten sechs
  Terminen der kommenden 30 Tage hinzu. Die bestehende Auswahl bleibt erhalten.
- **Kalender → Zahnrad** öffnet die Einrichtung. Jeder SEDO-Benutzer verbindet
  seinen eigenen Apple-Account; andere SEDO-Benutzer sehen diese Termine nicht.
- Im Apple-Account ein App-spezifisches Passwort erzeugen und direkt in SEDO
  eintragen. Anschließend **Kalender laden**, gewünschte Kalender auswählen und
  den beidseitigen Abgleich bestätigen. Keine öffentliche Kalenderfreigabe nötig.
- SEDO übernimmt neue und geänderte Termine sowie bestätigte Löschungen in beide
  Richtungen. Apple Kalender auf Mac/iPhone muss denselben iCloud-Account nutzen.
  Der Desktop-Abgleich läuft alle zwei Minuten, solange SEDO geöffnet ist;
  **Abgleichen** startet ihn bei Bedarf. Die Ansicht zeigt den letzten Stand.
- Lokale Termine werden nicht nachträglich automatisch in iCloud hochgeladen.
  Beim Anlegen lässt sich „SEDO · nur lokal“ oder ein ausgewählter beschreibbarer
  iCloud-Kalender wählen. Ohne iCloud-Verbindung funktioniert der lokale Kalender.
- Zeitangaben in der Eingabe gelten für Europe/Berlin einschließlich Sommerzeit.
  Bei ganztägigen Terminen ist der gewählte letzte Tag eingeschlossen.
- Eine optionale Kundenzuordnung bleibt ausschließlich in SEDO; sie wird nicht
  in die iCloud-Terminbeschreibung eingefügt.

## Erste Ausbaustufe und Grenzen

Normale Einzeltermine werden beidseitig erstellt, geändert und gelöscht.
Terminserien einschließlich Ausnahmen sowie Einladungen werden angezeigt;
deren Bearbeitung bleibt in dieser Ausbaustufe in Apple Kalender. Es werden
keine Teilnehmer eingeladen oder Einladungsantworten versendet. Kalender mit
Leseberechtigung bleiben auch in SEDO schreibgeschützt.

Bei gleichzeitigen Änderungen werden beide Fassungen einschließlich einer
eventuellen Löschung angezeigt. Erst eine bewusste Auswahl löst den Konflikt.
Gelöschte Termine bleiben lokal zur Nachvollziehbarkeit einsehbar.
SEDO sendet keine eigenen Betriebssystem-Erinnerungen; bestehende iCloud-Alarme
bleiben erhalten. Aufgaben/Wiedervorlagen werden nicht automatisch exportiert.
Ein Wechsel des verbundenen Apple-Accounts ist noch kein eigener UI-Workflow.

Der echte iCloud-Abgleich benötigt den Zugang des Nutzers und eine gesonderte
Abnahme mit seinem Kalender. Automatische Tests verwenden ausschließlich
synthetische Termine und einen simulierten CalDAV-Server. Eine Windows-Abnahme
wird durch die lokale Mac-Prüfung nicht ersetzt.

## Technik und Schutz

- CalDAV-Ermittlung über current-user-principal und calendar-home-set;
  Freigaben werden anhand der gemeldeten Berechtigungen bestimmt.
- HTTPS mit Zertifikatsprüfung; Zugangsdaten nur an caldav.icloud.com und
  p<Nummer>-caldav.icloud.com, keine Umgebungs-Proxys. Begrenzte und geprüfte
  Weiterleitungen nur bei Lesezugriffen. Keine externen Anhänge oder URLs geladen.
- Persönliche Kalenderdaten in drei eigenen Tabellen (Migration 0024).
  Fachmodelle für Rechnungen, Zahlungen und Aufgaben bleiben unverändert.
- Abruf vor Übertragung, vollständiges Ressourcenverzeichnis vor Erkennen von
  Löschungen, überprüfte ETags, If-Match/If-None-Match bei allen Schreibzugriffen.
  Fehlende/fehlerhafte Listen sind niemals ein Beleg für entfernte Termine.
- Dauerhafter Übertragungsstand vor PUT/DELETE; nach einem Verbindungsabbruch
  wird zuerst der Serverstand gelesen. Stabile Ressourcenadressen und UIDs
  verhindern Duplikate. Unklare abweichende Fassungen gehen in die Konfliktansicht.
- Verbindungs-Lease serialisiert Abgleich, Einrichtung und Benutzeränderungen.
  Revisionsprüfung schützt Formulare und Konfliktauswahl vor veraltetem Stand.
- XML ohne DTD/Entitäten, begrenzte Antwortgrößen, keine Ausführung von Inhalten,
  Vorlagen-Escaping. Sehr häufige/übermäßig komplexe Serien werden zurückgewiesen.
- App-Passwort mit lokalem Fernet-Schlüssel verschlüsselt und an Benutzer und
  Apple-Account gebunden. Schlüssel und verschlüsselte Zugangsdaten sind nicht
  in Backups. Wiederhergestellte ausstehende Änderungen werden angehalten;
  erneute Zugangseingabe, Kalenderauswahl und Abgleich sind erforderlich.
- iCalendar/Serienbibliotheken sind im Release-Lock und nativen Paket enthalten.
  Der eingefrorene Selbsttest prüft Verschlüsselung, Serienauflösung und Assets.

## Quellen

- [Apple: App-spezifische Passwörter](https://support.apple.com/de-de/102654)
- [CalDAV, RFC 4791](https://www.rfc-editor.org/rfc/rfc4791)
- [iCloud-Endpunkte: Python-CalDAV-Dokumentation](https://caldav.readthedocs.io/stable/about.html)
- [iCalendar-Bibliothek](https://icalendar.readthedocs.io/en/latest/how-to/usage.html)
- [Serien und Ausnahmen](https://recurring-ical-events.readthedocs.io/en/latest/user-guide/examples.html)
