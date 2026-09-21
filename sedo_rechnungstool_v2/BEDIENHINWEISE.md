# SEDO – Bedienung und Hintergrundinformationen

Die Erklärungstexte werden ausschließlich in dieser Dokumentation geführt. Im Programm
bleiben Feldbezeichnungen, konkrete Statuswerte, Fehlermeldungen und erforderliche
Aktionsbestätigungen sichtbar. Die folgenden Abschnitte sind nach Bildschirm geordnet.
Platzhalter in doppelten Klammern stehen für die jeweiligen Daten eines Vorgangs.

### aareon_import.html

GWG-Mapping importieren, Leistungen wählen und Monatsrechnungen erzeugen.

Hauptprozess: Entweder GWG-Abrechnung mit Mapping oder normale Abo-/Monatsrechnungen für andere Kunden erzeugen.

Erwartet wird Mapping SEDO GWG.xlsx . Die echte Fachkopfzeile wird automatisch erkannt.

Dieser Kunde erscheint im Rechnungskopf. Die Abrechnungseinheit kommt aus der Mapping-Datei.

Eine, mehrere oder alle Leistungen möglich. Pro Abrechnungseinheit wird eine Rechnung erzeugt; ausgewählte Leistungen werden als Positionen zusammengefasst.

Für Kunden ohne GWG-Mapping. Vorlagen entstehen entweder über „Neue Rechnung“ mit Haken „als Abo-/Monatsrechnungsvorlage speichern“ oder direkt hier über „Vorlage anlegen“.

### aareon_monthly.html

Mapping importieren, Leistungen wählen und Monatsrechnungen erzeugen.

GWG-Monatsrechnung: Mapping importieren, Leistungstexte und Preise prüfen, Lauf erzeugen und anschließend Dateien/Entwürfe kontrollieren.

Dieser Kunde erscheint im Rechnungskopf. Die Abrechnungseinheit kommt aus der Mapping-Datei.

Eine, mehrere oder alle Leistungen möglich. Pro Abrechnungseinheit wird eine Rechnung erzeugt; ausgewählte Leistungen werden als Positionen zusammengefasst.

### audit_log.html

Protokoll kritischer Aktionen wie Login, Backup, Import, Zahlung, Mahnung und Löschung.

### backup.html

Automatische Sicherung – ohne API-Key, SMTP-Passwort, Bank-PIN und aktive Anmelde-Sitzungen.

Die App prüft beim Start/Seitenaufruf, ob das letzte automatische Backup älter als 7 Tage ist. Falls ja, wird automatisch ein ZIP-Backup erzeugt.

Datenbank mit Kunden, Rechnungen, Angeboten, Zahlungen und Kassenbuch Medienordner mit erzeugten PDFs, XML-Dateien, ZUGFeRD-Dateien und E-Mail-Entwürfen Backup-Infodatei mit Zeitpunkt und Grund der Sicherung Nicht enthalten: .env, Secret Key, alte KI-API-Schlüssel, SMTP-Passwort, Bank-PIN und aktive Anmelde-Sitzungen. Die Datenbankkopie wird neu aufgebaut, damit auch keine Rest-Bytes übrig bleiben. Enthalten und schützenswert: alle Kunden-, Rechnungs- und Zahlungsdaten sowie die Bank-Stammdaten ohne PIN (Anmeldename, IBAN, BLZ, FinTS-Adresse) und der SMTP-Benutzername. Ein Backup gehört deshalb nicht ungeschützt weitergegeben.

Dreistufig: auswählen, vollständig prüfen, kontrolliert beim nächsten App-Start einspielen.

Vor dem Einspielen erstellt SEDO automatisch eine Sicherung des aktuellen Stands. SMTP-Passwort, KI-Schlüssel und Bank-PIN müssen danach neu hinterlegt werden. Das SEDO-Anmeldepasswort entspricht dem Stand der Sicherung; aktive Sitzungen werden nicht übernommen.

### calendar/form.html

Uhrzeiten gelten für Deutschland (Europe/Berlin), einschließlich Sommerzeit. Bei ganztägigen Terminen gehört der gewählte letzte Tag dazu. Eine Kundenzuordnung bleibt nur in SEDO sichtbar.

### calendar/index.html

Termine in ausgewählten iCloud-Kalendern werden mit Apple Kalender auf deinen Geräten abgeglichen. Lokale SEDO-Termine bleiben auf diesem Rechner.

### calendar/settings.html

Jeder SEDO-Benutzer verbindet seinen eigenen Apple-Account und wählt seine Kalender. Termine in diesen Kalendern sind in SEDO und Apple Kalender sichtbar. Der automatische Abgleich läuft alle zwei Minuten, solange SEDO geöffnet ist.

Ein App-spezifisches Passwort im Apple-Account unter „Anmelden und Sicherheit“ erstellen. Das normale Apple-Passwort wird hier nicht benötigt. Zugangsdaten bleiben lokal verschlüsselt und werden aus Sicherungen entfernt.

### cashbook_form.html

Kassenbuchung erfassen

### cashbook_list.html

Monatsansicht: Einnahmen und Ausgaben werden je Monat geführt. Die PDF-Ausgabe ist für Ablage und Ausdruck gedacht.

### customer_form.html

Stammdaten für Rechnungen, Angebote und Objekte.

### customer_import.html

Excel/CSV aus PlanD, WISO oder eigener Liste übernehmen

Unterstützt werden .xlsx , .xls und .csv . Numbers-Dateien bitte vorher in Numbers als Excel exportieren.

### customer_list.html

Stammdaten, Ansprechpartner und Objekte verwalten.

Kundenimport: Excel/CSV hochladen, danach stehen Kunden in Rechnungen und Angeboten zur Auswahl.

### dashboard.html

Dein Arbeitstag. Dein Überblick.

### incoming/detail.html

Bonsai liest Text-PDFs und XML lokal. Vorschläge werden erst beim Speichern übernommen. Scans und Bilder derzeit manuell erfassen. Eine KI-Prüfung ersetzt keine Belegprüfung.

### incoming/index.html

PDF- und XML-Anhänge aus dem Posteingang werden automatisch gesammelt. Bilder werden bei Rechnungshinweisen im Betreff oder Dateinamen erkannt. Belegart und Angaben vor der Freigabe prüfen. Originale bleiben unverändert in SEDO und sind im Backup enthalten. Es werden keine Zahlungen ausgelöst.

Bis zu 100 Belege pro Paket. Ein Download bestätigt keinen tatsächlichen Druck. Das Steuerbüro-Paket enthält die ausgewählten Originalbelege, Quell-E-Mails, Übersicht und Prüfsummen. Versand folgt erst nach Prüfung des Mailentwurfs.

### invoice_detail.html

Archiv: Dateien öffnen, Zahlungen erfassen, korrigieren, mahnen oder stornieren.

Erzeugt einen individuellen Begleittext mit neutraler Anrede. An Bonsai gehen nur Rechnungs-Eckdaten und Positionsbezeichnungen – keine Kunden- oder Objektdaten.

### invoice_edit.html

Die Nummer bleibt beim Speichern unverändert. Es entsteht nur ein Wasserzeichen-PDF, keine E-Rechnung.

Hier werden nur vorhandene Kunden und Objekte ausgewählt. Stammdaten werden weder verändert noch neu angelegt.

{{ field.help_text }}

Das Objekt muss zum ausgewählten Kunden gehören.

### invoice_finalize.html

Dabei verschiebt sich die Fälligkeit um denselben Zeitraum. Das vereinbarte Zahlungsziel und der Leistungszeitraum bleiben erhalten.

### invoice_form.html

In fünf Schritten zur fertigen Rechnung – Empfänger, Leistung und Betrag vor dem Erstellen prüfen.

Gespeicherten Kunden auswählen oder einen neuen Empfänger erfassen.

Bei ausgewählten Kunden werden die gespeicherten Stammdaten verwendet.

Manuelle Eingaben legen beim Speichern einen neuen Kunden an. Bei ausgewähltem Kunden gelten dessen Stammdaten.

Für den E-Mail-Entwurf.

Objekt auswählen oder die Adresse der ausgeführten Leistung eingeben.

Ohne Objektauswahl wird ein manuell benannter Leistungsort beim Speichern als neues Objekt angelegt.

Die Vorgaben passen meistens – nur bei Bedarf ändern.

Bezeichnung, Menge und Einzelpreis eingeben – Gesamtbeträge werden automatisch berechnet.

Gesamtbeträge werden während der Eingabe berechnet.

Empfänger, Leistungsort, Positionen und Gesamtbetrag noch einmal kontrollieren.

Die Übersicht rechnet während der Eingabe mit. Mit „Vorschau“ die serverseitig berechneten Beträge prüfen.

Vorlage erscheint unter Monatsrechnung und kann monatlich erzeugt werden.

### invoice_list.html

Belege finden, Entwürfe weiterbearbeiten und Originale nachschlagen.

### invoice_preview.html

Diese Ansicht speichert nichts. Zurück im Browser bringt dich wieder zur Eingabe.

### invoice_zugferd_validation.html

Offline-Prüfung des versiegelten Originals vor dem Versand.

### mail/base.html

Anfragen lesen. Antworten schreiben. Direkt weiterarbeiten.

Firmenpostfächer sind für angemeldete SEDO-Nutzer sichtbar. Eigene Entwürfe bleiben beim Verfasser.

### mail/compose.html

Die Anhänge der ursprünglichen Nachricht werden beim Speichern übernommen.

Entwürfe liegen in SEDO und sind nur für dich sichtbar. Vor dem Versand erscheint eine Zusammenfassung. Änderungen bitte ausdrücklich speichern.

### mail/inbox.html

Die Suche umfasst die bereits geladenen Nachrichten. Ältere E-Mails werden beim weiteren Abruf nachgeladen.

{{ selected.spam_reason|default:'Nachricht liegt im Spamordner.' }} Neue Geschäftsabläufe erst nach „Kein Spam“ fortsetzen. Bereits gesicherte Belege bleiben erhalten.

Lege bei Bedarf ein Angebot oder eine Aufgabe an. Die E-Mail bleibt als Quelle erhalten.

### mail/settings.html

Deine Mails bleiben auf dem Server. SEDO verbindet sich verschlüsselt mit imaps.udag.de:993 und smtps.udag.de .

Passwörter werden lokal verschlüsselt gespeichert und aus SEDO-Sicherungen entfernt. Nach einer Wiederherstellung müssen sie neu eingegeben werden.

{{ field.help_text }}

Die Verbindungsprüfung meldet sich nur an und liest Ordnernamen. Sie versendet keine Test-Mail.

Die vorhandenen Outlook-/Serverordner weiterverwenden. Für „Gesendet“, „Papierkorb“ und „Archiv“ jeweils genau einen Ordner auswählen.

### mail/spam_settings.html

Die Einstellungen gelten für alle Benutzer dieses Postfachs. „Nur markieren“ lässt Nachrichten im bisherigen Ordner. Automatisch sortiert werden ausschließlich neue Nachrichten nach deinen Sperrregeln. Bereits verknüpfte Geschäftsmails bleiben an ihrem Platz.

Eine genaue Adresse hat Vorrang vor einer Domainregel. Domainregeln erfassen genau die angegebene Domain. „Zulassen“ ist eine Sortierregel und bestätigt nicht die Echtheit des Absenders. Deine manuelle „Kein Spam“-Entscheidung hat Vorrang. Nachrichten im Server-Spamordner werden durch eine Freigaberegel nicht automatisch zurückgeholt.

Die lokale Prüfung verwendet keine KI und übermittelt keine Mailinhalte an zusätzliche Dienste. Ungeprüfte Angaben in Mail-Headern gelten nicht als verlässliche Spamwertung. Die automatische Löschung ist nicht Bestandteil dieses Filters.

### month_close.html

Rechnungsexport für den Steuerberater.

Erzeugt einen geordneten Monatsordner mit Originalbelegen, Buchungsübersicht, Prüfsummen und Exportprotokoll. Die CSV dient der Steuerberatung als Arbeitsdatei; für einen direkten DATEV-Import fehlen noch die abgestimmten Kontenzuordnungen.

### object_form.html

Objekt / Leistungsort für Kunden und Monatsrechnungen.

### offer_detail.html

Aus E-Mail entstanden: Ursprüngliche Anfrage öffnen

Den nächsten Kontakt planen und nach einem Gespräch direkt neu terminieren.

### offer_form.html

Kundenvorschlag und Leistungsumfang bitte prüfen. Die Nachricht bleibt mit dem gespeicherten Angebot verknüpft.

In sechs Schritten zum fertigen Angebot – die KI übernimmt die Positionen.

Kunden auswählen – alle Felder werden automatisch befüllt und können geprüft werden.

Kunden auswählen → alle Felder werden sofort automatisch befüllt.

Leistungsort – erscheint im Angebot und später in der Rechnung.

Das heutige Datum ist bereits eingetragen – nur bei Bedarf ändern.

Beispiele: „Beräumung Reihenhaus mit Entsorgung, 1200 Euro“ · „monatliche Treppenhausreinigung, 4 Etagen“. Bitte keine Namen oder Adressen ansagen – nur die Leistung. Annahmen und geschätzte Preise werden markiert. Kalkulationsangaben wie „3 Mann, 2 Tage“ helfen der KI beim Preis, erscheinen aber nicht im Angebot. Lokales Whisper verarbeitet Audio auf dem SEDO-Rechner. Bonsai verarbeitet den eingegebenen Text und Katalogdaten lokal auf diesem Rechner. Es gibt keinen Cloud-Fallback. Freitext wird nicht automatisch anonymisiert. Kein Diktat nötig? Einfach auf Weiter klicken und die Positionen selbst eintragen.

Einfach grob ansagen – die KI baut daraus die Positionen samt Leistungstexten.

Bezeichnung, Menge und Einzelpreis eingeben – Gesamtbeträge werden automatisch berechnet. Von der KI vorbefüllte Texte und Preise lassen sich frei anpassen.

Von der KI vorbefüllt – Texte und Preise lassen sich frei anpassen.

„Vorschau“ zeigt das Angebot, ohne zu speichern. „Angebot erstellen“ speichert es und erzeugt direkt das PDF.

Passt alles? Dann Angebot erstellen – das PDF wird sofort erzeugt.

### offer_list.html

Offene Entscheidungen und fällige Wiedervorlagen im Blick behalten.

### open_items.html

Forderungen priorisieren, Zahlungen erfassen und Mahnschritte kontrolliert ausführen

### payment_list.html

Bankumsätze importieren, automatisch zuordnen, offene Fälle prüfen

Rein lesender Abruf der Umsätze direkt bei der Bank. Je nach Bank ist eine Freigabe in der Banking-App nötig (pushTAN) – Freigabe bestätigen, SEDO wartet bis zu 90 Sekunden. Zugang einrichten unter Einstellungen → Bankkonto. Rechnungsnummern im Verwendungszweck werden automatisch zugeordnet.

Noch kein Bankzugang hinterlegt. Einstellungen → Bankkonto ausfüllen, dann erscheint hier der Abruf-Knopf.

JSON-Datei mit Umsätzen einlesen. Nach dem Import werden Rechnungsnummer und Betrag geprüft; unklare Buchungen lassen sich manuell zuordnen.

### payment_manual.html

Für Teilzahlungen einfach nur den tatsächlich eingegangenen Betrag erfassen. Der Restbetrag bleibt offen.

### readiness.html

Alle wichtigen technischen und praktischen Abnahmen an einem Ort.

{{ item.hint }}

### recurring_monthly.html

Gespeicherte Vorlagen auswählen, Monat festlegen und den Lauf gesammelt erzeugen. Die GWG-Monatsrechnung bleibt separat.

Abo-/Monatsrechnungsvorlagen für Kunden ohne GWG-Mapping erzeugen.

Unverbindliche Vorschau aus den aktiven Vorlagen. Es werden dabei keine Rechnungen erzeugt. Die Vorschau verwendet „Vorhandene Rechnungen überspringen“ und alle aktiven Vorlagen.

Für Kunden ohne GWG-Mapping. Vorlagen entstehen entweder über „Neue Rechnung“ mit Haken „als Abo-/Monatsrechnungsvorlage speichern“ oder direkt hier über „Vorlage anlegen“.

### recurring_template_form.html

Monatsrechnung ohne GWG-Mapping anlegen oder bearbeiten.

Eine Position ist vorbereitet. Weitere Positionen kannst du mit dem Plus-Button ergänzen.

### reports.html

Abgerechneter Umsatz, Forderungsalter und Angebotsentwicklung auf einen Blick.

Ausgestellte Rechnungen und Korrekturen ohne Stornos

Unabhängig vom gewählten Geschäftsjahr

Angenommen oder berechnet ÷ entschiedene Angebote

Quote nur aus bereits entschiedenen Angeboten

### security_status.html

Kompakte Kontrollseite für Produktivbetrieb, Backups und spätere Bankanbindung.

{{ c.hint }}

Auszug der letzten sicherheitsrelevanten Aktionen.

Die Prüfung oben liest nur den Bestand. Sie verändert keine Rechnungen und keine Dateien.

Der Befehl python manage.py belegketten_pruefen zeigt den ausführlichen Trockenlauf. Erst --reparieren übernimmt belegbare Statuskorrekturen mit Pflicht-Protokoll. Vorher die App beenden und eine Sicherung erstellen. Entwurfs-XML-Verweise und Entwürfe älter als 30 Tage werden nur gemeldet; widersprüchliche Folgebelege bleiben ein manueller Prüfauftrag.

Vor echter Bankanbindung sollten Rollen/Rechte, Audit-Log, externe verschlüsselte Backups und eine sichere Ablage für Zugangsdaten aktiv sein. Bank-PINs dürfen nicht im Klartext in der Datenbank, in ZIP-Backups oder in Logs landen.

### service_catalog_form.html

Der Beschreibungstext wird vom KI-Assistenten für jedes Angebot mit dieser Leistung wiederverwendet.

### service_catalog_list.html

Wiederverwendbare Leistungen mit Beschreibungstexten. Neue Leistungen lernt der KI-Assistent automatisch aus deinen Angeboten – hier kannst du sie prüfen und anpassen.

Der Katalog füllt sich automatisch, sobald der KI-Assistent Angebote erzeugt – oder lege die erste Leistung manuell an.

### settings.html

E-Mail, GWG-/XML-Einstellungen und Mapping-Verwaltung.

Den Ort und die Sichtbarkeit der Wetterkachel wählt jeder Benutzer über das Zahnrad im Dashboard.

Benötigt wird ein Wetter.com-/METEONOMIQS-Zugang für die PWA Forecast API v4.0 mit Nowcast per Postleitzahl. Zugang und Pakete ansehen . SEDO ruft Wetterdaten nur bei eingeblendeter Kachel ab und hält sie zehn Minuten vor.

Ein über die Systemumgebung eingerichteter Schlüssel hat Vorrang und wird hier nicht geändert.

Rechnungen werden direkt aus SEDO versendet, wenn Host und Benutzer konfiguriert sind. Ohne Konfiguration wird weiterhin eine .eml-Datei zum Download erzeugt.

587 = STARTTLS (empfohlen) · 465 = SSL/TLS. SEDO prüft in beiden Fällen das Zertifikat des Mailservers.

Wird lokal gespeichert. Leer lassen, um das bestehende Passwort zu behalten.

Rein lesender Umsatzabruf direkt bei der Bank (FinTS 3.0 PIN/TAN). Die FinTS-Adresse der Bank findest du z. B. unter fints.org oder bei deiner Sparkasse (banking.sparkasse.de-Adresse mit /fints30). PIN-Speicherung ist optional – ohne Speicherung wird sie bei jedem Abruf abgefragt. Für den Dauerbetrieb eine kostenlose DK-Produktregistrierung eintragen.

Wird lokal gespeichert und aus Backups bereinigt. Alternativ leer lassen – dann fragt der Abruf jedes Mal.

Angebotsentwürfe, Leistungstexte, Begleitschreiben und KI-Prüfungen werden auf diesem Rechner verarbeitet. Kein Cloud-Fallback und keine Modell-API-Gebühren. SEDO kann dein installiertes Bonsai automatisch mitstarten. Lokales Diktat nutzt Whisper; ist es nicht verfügbar, kannst du Text eingeben.

Entwürfe, Annahmen und geschätzte Preise vor der Übernahme prüfen. „Aktiviert“ bedeutet nicht, dass der Modellserver bereits gestartet ist.

Ein bereits laufender Server wird weiterverwendet. Bonsai bleibt beim Schließen von SEDO verfügbar, damit andere Anwendungen weiter darauf zugreifen können.

Vollständiger Pfad zum vorhandenen Bonsai-demo-Ordner, falls er woanders liegt. Es werden keine Modelle installiert oder heruntergeladen.

Standard: http://127.0.0.1:8080/v1. Nur Adressen auf diesem Rechner sind zugelassen.{% if ai_cfg.endpoint_from_env %} Durch die lokale Startkonfiguration vorgegeben.{% endif %}

Leer lassen, wenn genau ein Modell geladen ist.{% if ai_cfg.model_from_env %} Durch die lokale Startkonfiguration vorgegeben.{% endif %}

Die Administration verwaltet den lokalen Modellzugang.

Diese Angaben werden nur für GWG-Monatsrechnungen verwendet.

Wird in der XML als SellerTradeParty/ID ausgegeben. Pflichtfeld für ARP.

Standard: aus. GWG soll nach aktueller Logik nur Abrechnungseinheit + Leistungsart-ID erhalten.

Das GWG-Mapping wird hier zentral gepflegt. Die Monatsrechnung nutzt anschließend automatisch das zuletzt importierte Mapping.

Erwartet wird Mapping SEDO GWG.xlsx . Die Fachkopfzeile wird automatisch erkannt.

Kontrollansicht der ersten 50 Mapping-Zeilen.

Der Serverbetrieb ist auf HTTPS, SSH-Key-Zugriff und Audit-Log ausgelegt. Vor Bankanbindung: Rollen/Rechte finalisieren, externe verschlüsselte Backups einrichten und Zugangsdaten niemals im Klartext speichern.

### task_list.html

Freie Aufgaben und nächste Schritte zu Kunden, Angeboten oder Rechnungen.

Bitte höchstens einen Bezug auswählen.

## Weitere Bedienhinweise

- settings.html: Nach dem Verbinden erscheinen hier deine Kalender zur Auswahl.
- customer_detail.html: Angebote, Rechnungen, Zahlungen, Mahnungen, E-Mails und Notizen in zeitlicher Reihenfolge.
- customer_detail.html: Eine Notiz ist ein guter Anfang für die gemeinsame Kundenhistorie.
- customer_detail.html: Erstelle eine Rechnung mit den Stammdaten dieses Kunden.
- customer_detail.html: Lege wiederkehrende Leistungsorte an, um sie beim Erstellen von Belegen auszuwählen.
- dashboard.html: Wähle, was du hier sehen möchtest. Die Auswahl gilt nur für deinen Benutzer.
- dashboard.html: Ungelesene Spam- und Verdachtsfälle.
- dashboard.html: Beim Abruf kann deine Bank eine Freigabe in der Banking-App verlangen.
- dashboard.html: Bitte die Administration um Einrichtung des Bankzugangs bitten.
- dashboard.html: Bitte die Administration um Einrichtung des Wetterzugangs bitten.
- dashboard.html: Zahlungseingänge und den nächsten Schritt prüfen.
- dashboard.html: Kandidaten ansehen und nächste Mahnstufen bewusst bestätigen.
- dashboard.html: Fällige Gespräche und nächste Schritte an einer Stelle.
- dashboard.html: Lege Aufgaben frei oder direkt zu einem Kunden, Angebot oder einer Rechnung an.
- dashboard.html: Beginne mit einer neuen Rechnung. Gespeicherte Entwürfe findest du anschließend hier.
- dashboard.html: Mapping und Monatslauf
- dashboard.html: Datensicherung und Wiederherstellung
- dashboard.html: Öffne das Zahnrad oben rechts und wähle deine Kacheln aus.
- package.html: Enthält Originalbelege, zugehörige vollständige E-Mails, Übersicht und Prüfsummen.
- invoice_detail.html: Ausgestellte PDFs und XMLs werden unverändert wiederverwendet. Fachliche Änderungen erfolgen über eine Korrekturrechnung.
- invoice_detail.html: Beim Erzeugen oder Speichern entsteht nur ein Wasserzeichen-PDF, kein E-Rechnungs-XML. Die Rechnungsnummer wird erst beim Finalisieren vergeben; eine bereits vorhandene RE-Nummer bleibt erhalten.
- invoice_edit.html: Ohne JavaScript steht je Speichervorgang eine zusätzliche Positionszeile bereit.
- invoice_finalize.html: Eine EN-Entwurfsnummer wird jetzt durch eine RE-Rechnungsnummer ersetzt. Eine bereits vorhandene RE-Nummer bleibt unverändert.
- invoice_form.html: Ohne JavaScript: Mit „Vorschau“ unten die vollständigen Rechnungsdaten und Beträge prüfen.
- invoice_form.html: Entwürfe bleiben bearbeitbar. „Rechnung erstellen“ stellt den Beleg aus. „Erstellen & E-Mail vorbereiten“ lädt eine E-Mail-Datei zum Versand im Mailprogramm herunter.
- base.html: Hier finden deine beiden Firmenpostfächer ihren Platz.
- base.html: Bitte die Postfächer mit dem Administrationskonto einrichten.
- drafts.html: Hier bleiben auch Versandbestätigungen und noch zu prüfende Sendungen nachvollziehbar.
- inbox.html: Wähle links eine Nachricht zum Lesen aus. Angebote und Aufgaben sind dann nur einen Schritt entfernt.
- task.html: Ein Auftrag oder eine Anfrage wird so zur bearbeitbaren Aufgabe. Daraus entsteht noch kein Angebot und keine Rechnung.

## Zugang und Prüfung

- Mailpasswort: Leer lassen, um das gespeicherte Postfach-Passwort zu behalten. Nicht das SEDO-Anmeldepasswort verwenden.
- Der lokale Plausibilitätscheck vergleicht Historie, Dubletten und Nullwerte. Bonsai prüft nur Leistungsdaten, keine Kunden- oder Objektdaten.
- App-spezifisches Apple-Passwort: https://support.apple.com/de-de/102654
- Wetter.com-Zugang: https://www.wetter.com/apps_und_mehr/website/api/
- Eingangsbelege: Die Erkennung kann auf Rechnungshinweisen in Betreff oder Dateiname beruhen. Vor einer Freigabe bleibt die Prüfung des Originals erforderlich.

## Dashboard

„Letzte Rechnungen“ zeigt die acht neuesten Belege nach Rechnungsdatum.
„Bezahlte Rechnungen“ summiert bezahlte Belege des angezeigten Rechnungsmonats;
dies ist kein Bericht über Zahlungseingänge in diesem Monat.
Rechnungsentwürfe können über ihre Kachel weiterbearbeitet werden.

## Weitere Bedienhinweise

- E-Mail: Postfächer werden nach der Einrichtung automatisch abgeglichen. Ein leerer Ordner enthält derzeit keine geladenen Nachrichten; über die Ordnerauswahl lässt sich der Bereich wechseln.
- Die formatierte Mailansicht blockiert externe Bilder weiterhin. Eine Anfrage oder ein Auftrag lässt sich aus der Nachricht als Angebot weiterbearbeiten. Die Absenderadresse kann zu einem vorhandenen Kunden passen; die Zuordnung wird vor dem Speichern geprüft.
- Rechnungen und Angebote: Nach Auswahl eines Kunden stehen nur dessen Objekte zur Auswahl. Ohne Objekt kann der Leistungsort direkt angegeben werden.
- Monatsabschluss: Später ausgeblendete Rechnungsnummern werden getrennt aufgeführt und sind nicht Teil des Exports. Die Aufstellung erklärt die Lücke im Nummernkreis.
- Ein übernommener Statusfilter in der Rechnungsliste wird durch die Wahl einer Belegansicht aufgehoben.
- Altbelege ohne ursprünglichen Prüfsummennachweis werden unverändert verwendet. Ihre ursprüngliche Echtheit ist damit nicht nachträglich bestätigt.
