# E-Mail in SEDO

Die Firmenpostfächer `info@sedo-magdeburg.de` und `rechnung@sedo-magdeburg.de`
lassen sich unter **E-Mail → Postfächer verwalten** einrichten. Der Nutzer hat
bestätigt, dass sämtliche bisherigen E-Mails auf dem Mailserver liegen.

## Einrichten und umsteigen

1. Als SEDO-Administrator Postfachadresse, Postfach-Benutzernamen und Passwort
   eingeben. Die Zugangsdaten gehören in die App, nicht in einen Chat.
2. **Verbindung prüfen** testet IMAP und SMTP und liest die Ordnerliste.
   Dabei wird keine Test-Mail versendet.
3. Vorhandene Ordner für **Gesendet**, **Papierkorb** und **Archiv** prüfen und
   gegebenenfalls zuordnen. Es darf je Rolle nur einen Zielordner geben.
4. Der Abruf startet automatisch mit der Desktop-App und läuft alle fünf
   Minuten. Zuerst werden neuere E-Mails geladen; weitere Abrufe ergänzen
   den Bestand. Bei geschlossener App bleiben neue Mails auf dem Server.
5. Für beide Postfächer Empfang, Ordner, Anhänge und eine ausdrücklich
   freigegebene Testantwort im Alltag prüfen. Erst danach Outlook ablösen.

United Domains verwendet IMAP **imaps.udag.de:993 mit SSL/TLS** und SMTP
**smtps.udag.de:465 mit SSL/TLS**, alternativ **587 mit STARTTLS**.
Zertifikate und Servernamen werden geprüft; das Desktop-Paket enthält eigene
öffentliche Stammzertifikate zusätzlich zum Systemvertrauen.

Provider-Dokumentation:
- https://www.united-domains.de/help/faq-article/wie-lauten-die-imap-daten/
- https://www.united-domains.de/help/faq-article/wie-lauten-die-smtp-daten/

## Tägliche Arbeit

- Gemeinsamer Posteingang oder ein einzelnes Postfach; vorhandene Serverordner
  bleiben nutzbar. Suche in Betreff, Absender, Empfänger und Nachrichtentext;
  Filter für ungelesene und markierte Mails, Listen mit 30 Einträgen je Seite.
- Lesen, als gelesen/ungelesen markieren, Stern setzen, archivieren,
  verschieben, in den Papierkorb legen und daraus zurückverschieben.
  Endgültiges Löschen verlangt eine gesonderte Bestätigung im Papierkorb.
- Antworten, allen antworten, weiterleiten oder neu schreiben; Cc/Bcc,
  Signatur, Dateianhänge, gespeicherte Entwürfe und eine Vorschau vor dem Senden.
  Entwürfe sind lokal in SEDO und nur für ihren Verfasser sichtbar.
- Nach erfolgreichem Versand wird eine Kopie in **Gesendet** abgelegt. Scheitert
  nur diese Ablage, speichert **Versandkopie speichern** die Kopie nachträglich,
  ohne die E-Mail erneut zu senden.
- Bricht die Verbindung während des Versands ab, kann die Nachricht bereits
  angekommen sein. Der Zustand **Versandstatus prüfen** verhindert einen
  automatischen Neuversand. Erst nach tatsächlicher Prüfung lässt sich der
  Vorgang als versendet bestätigen oder wieder als Entwurf freigeben.
- Empfangene Anhänge sowie das Original als EML lassen sich herunterladen.
  Aktive HTML-Inhalte und externe Bilder werden nicht geladen.

Die Postfächer sind gemeinsame Firmenpostfächer für angemeldete SEDO-Nutzer.
Einrichtung und Passwortänderungen erfordern ein Administrationskonto.

## Übersicht und Belegstatus

Links stehen die wichtigsten Ordner, weitere Serverordner sind aufklappbar.
Der Postfachwähler zeigt beide Firmenpostfächer gemeinsam oder einzeln. Auf
breiten Bildschirmen stehen Liste und Nachricht nebeneinander; auf schmalen
Bildschirmen führt „Nachrichtenliste“ zurück zur bisherigen Auswahl.

Die primären Aktionen sind Antworten, Weiterleiten, Papierkorb und Spam.
Markieren, Archivieren, Ordnerwechsel und endgültiges Löschen liegen unter
„Weitere Aktionen“. Eine Mehrfachauswahl blendet ihre Aktionsleiste ein und
wird vor dem Verschieben bestätigt. Ordner, Suche, Filter und Listenseite
bleiben erhalten. Eine Aktion betrifft die geöffnete Ordnerkopie.

Übernommene Rechnungsanhänge markieren die Quell-E-Mail automatisch:

- **Beleg übernommen · zu prüfen**: Das Original ist gesichert, mindestens
  ein verknüpfter Beleg wartet noch auf Prüfung.
- **Belege abgearbeitet**: Alle verknüpften Belege sind geprüft oder als
  Nicht-Rechnung abgelegt. Das bedeutet weder bezahlt noch ans Steuerbüro gesendet.
- **Belegübernahme fehlgeschlagen**: Die Übernahme muss geprüft werden.

Die entsprechenden Filter helfen beim Abarbeiten. Eine gewöhnliche Nachricht
wird nicht allein deshalb als erledigt markiert, weil sie auf Anhänge geprüft
wurde. Die Belege selbst lassen sich unter „Weiterarbeiten“ öffnen. Bereits
übernommene Dokumente und ihre Verknüpfungen bleiben auch bei Ordnerwechseln
bestehen.

Der Hintergrundabruf gibt Benutzeraktionen zwischen abgeschlossenen Schritten
Vorrang. Bei einer unterbrochenen Verschiebung wird zuerst der Serverstand
abgeglichen; die Aktion wird nicht blind wiederholt.

## Von der Anfrage zum Angebot

**Angebot erstellen** öffnet den vorhandenen Angebotsassistenten und schlägt
bei eindeutig passender Absenderadresse einen Kunden vor. Kunde, Leistungen
und Preise werden weiterhin geprüft und bewusst gespeichert. Die Originalmail
bleibt mit dem Angebot verknüpft. Es gibt keine automatische KI-Auswertung.

Ein gespeichertes Angebot lässt sich mit seinem PDF direkt als Antwortentwurf
im Postfach vorbereiten. Eine Aufgabe zur Anfrage oder zum Auftrag kann
alternativ angelegt werden; das ist noch keine eigenständige Auftragsverwaltung.
Die vorhandene, geschützte Umwandlung eines Angebots in eine Rechnung bleibt
unverändert. Zugeordnete E-Mails erscheinen auch in der Kunden-Chronik.

## Datenhaltung und Grenzen

- Zugangsdaten sind mit einem lokalen Schlüssel verschlüsselt. Der Schlüssel
  liegt in `data/.mail_key` außerhalb der App. Backups enthalten weder diesen
  Schlüssel noch die verschlüsselten Postfach-Passwörter; nach Wiederherstellung
  sind Passwort und automatischer Abruf erneut einzurichten.
- Normale Servernachrichten liegen im privaten lokalen `mail-cache`, außerhalb
  der Rechnungsdatenbank und ihrer Sicherungen. Nach einer Wiederherstellung
  werden fehlende Kopien erneut vom Server geladen. Die Metadaten und bereits
  gelesenen Nachrichtentexte bleiben in der Datenbank verfügbar.
- Vor dem Verknüpfen mit einem Angebot, einer Aufgabe, einem Antwortentwurf oder
  einer Kundenakte wird das vollständige Original als Geschäftsquelle in die
  Datenbank übernommen. Diese Quellen, eigene Entwürfe und Versandbelege sind
  somit auch ohne Mailserver im SEDO-Backup enthalten. Ein fehlendes oder
  beschädigtes Original blockiert die Verknüpfung, bis es neu abgerufen wurde.
- Die SEDO-Sicherung ersetzt kein vollständiges Backup des Mailservers. Dessen
  normaler Nachrichtenbestand bleibt dort führend; der lokale Zwischenspeicher
  wird nicht in jede Rechnungssicherung kopiert.
- Die Suche erfasst den bereits geladenen Bestand. Nachrichten über 50 MiB
  werden als unvollständig ausgewiesen; ihr Inhalt bleibt im Webmail abrufbar.
  Ausgangsanhänge sind zusammen auf 25 MiB und 50 Dateien begrenzt.
- IMAP MOVE ist für Verschieben erforderlich; endgültiges Löschen benötigt
  UIDPLUS. SEDO löscht niemals pauschal andere als gelöscht markierte Mails.
- Geschäftsquellen zu Angeboten, Aufgaben und Antwortentwürfen bleiben lokal
  erhalten, auch wenn eine Serverkopie entfernt wird. Das Postfach ist kein
  revisionssicheres E-Mail-Archiv.
- Lokale SEDO-Entwürfe werden nicht als bearbeitbare Entwürfe nach Outlook oder
  ins Webmail synchronisiert. Bestehende Serverentwürfe sind lesbar.
- Kalender, Kontakte-Synchronisierung, Regeln, HTML-Signaturen und automatische
  Rechnungserzeugung aus E-Mails sind nicht Teil dieses ersten Mailmoduls.

Für einen separat betriebenen Webserver gibt es `python manage.py sync_mail`
für einen einzelnen Abruf und `python manage.py sync_mail --watch` als Worker.
Die Desktop-App startet ihren Worker selbst. Die Verbindungs- und Versandtests
im Repository verwenden ausschließlich synthetische Daten und lokale Peers.
