# Sicherheits- und Betriebshinweise

## Lokaler Einzelplatzbetrieb

Standardstarter und Desktop-App binden ausschließlich an `127.0.0.1`.
Nur auf dieser Loopback-Verbindung ist unverschlüsseltes HTTP vorgesehen:
**nicht im Heimnetz, Firmen-LAN oder Internet**. Anmeldung und Zugriffsschutz
gelten auch lokal. Debug-Modus ausgeschaltet lassen, Betriebssystem und Browser
aktuell halten und den Rechner gegen unbefugte Benutzer absichern.

Die Anzeige „lokal“ in den Einstellungen setzt einen vom Starter ausdrücklich
bestätigten Loopback-Listener voraus. Sie ist kein Penetrationstest und prüft
weder Router noch unbekannte Tunnel.

## Benutzerrechte und Anmeldung

- Administratoren: aktive Benutzer mit `is_staff` beziehungsweise Superuser.
  Einstellungen, Kontoziele, Sicherungen und Abnahmeschalter sind administrativ.
- Normale angemeldete Benutzer dürfen Geschäftsdaten bearbeiten.
- Die Gruppe `SEDO Mitarbeiter Lesen` erlaubt nur geprüfte Leseansichten.
  Änderungen und nicht ausdrücklich freigegebene Aufrufe werden serverseitig
  abgewiesen. Ein Superuser bleibt bewusst uneingeschränkt.
- Neue/geänderte Passwörter müssen mindestens zwölf Zeichen haben und werden
  zusätzlich gegen häufige, rein numerische und benutzerähnliche Werte geprüft.
  Das öffentlich bekannte frühere Admin-Standardpasswort wird beim Start durch
  ein neues Zufallspasswort ersetzt und einmalig angezeigt. Individuell gewählte
  Bestandspasswörter werden dabei nicht verändert.
  Bereits bestehende schwache Passwörter müssen aktiv geändert werden.
- Wiederholte Anmeldeversuche werden dauerhaft und parallelitätssicher begrenzt.
  Nach vielen Fehlversuchen gilt eine begrenzte Wartezeit. Auch legitime Benutzer
  können bei Angriffen zeitweilig betroffen sein; der Support darf deshalb nicht
  pauschal die Schutzfunktion abschalten.

## Zugangsdaten und Sicherungen

Unternehmens-, Absender-, Bank- und Steuerstammdaten werden ausschließlich lokal
eingerichtet. Das verteilte Programm enthält keine echten Firmenprofile. Ein
vollständiges, validiertes Absenderprofil ist Voraussetzung für die Belegerzeugung.
Es gehört als vertraulicher Betriebsinhalt zur geschützten Datensicherung.

SMTP-Passwort und FinTS-PIN werden verschlüsselt und an Server, Benutzer und
Verbindungsart gebunden. Ein anderes Ziel darf keine alten Zugangsdaten erhalten.
FinTS verlangt HTTPS, folgt keinen Weiterleitungen und begrenzt Antwortgröße
sowie Wartezeiten. Postfach-/Kalenderzugangsdaten verwenden eigene Schutzmechanismen.

Der Installationsschlüssel liegt privat im lokalen Datenverzeichnis. Das schützt
nicht vor Schadsoftware mit Zugriff auf dasselbe Benutzerkonto oder vor einem
vollständig kompromittierten Rechner. Datenträgerverschlüsselung und geschützte
Betriebssystemkonten bleiben erforderlich.

Beim Start werden alte Klartextwerte in der aktuellen SQLite-Datenbank migriert
und verbleibende SQLite-/WAL-Seiten bereinigt. Schlägt diese Bereinigung fehl,
wird kein normaler Start bestätigt. **Alte Backups, Kopien und Dateisystem-
Snapshots werden dadurch nicht rückwirkend bereinigt.** Nach einem vermuteten
Abfluss Zugangsdaten beim jeweiligen Anbieter wechseln.

Anwendungssicherungen lassen Zugangsdaten und Installationsschlüssel aus.
Nach einer Wiederherstellung können neue Zugangsdaten erforderlich sein.
Die Sicherungen selbst enthalten vertrauliche Geschäftsdaten: separat geschützt
aufbewahren und einen Wiederherstellungstest durchführen.

## Dateien und Diktat

Nicht vertrauenswürdige PDFs, Bilder, XML-Dateien, Tabellen und Audiodateien
werden in getrennten, zeit- und größenbegrenzten Verarbeitungsprozessen geprüft.
Tabellen: höchstens 10 MiB, 10.000 Zeilen und 200 Spalten; Diktate: höchstens
25 MiB und fünf Minuten decodiertes Audio. Größere Vorgänge aufteilen.
Unterstützte Diktatmodelle: tiny, base und small (Standard). Das größere Modell
medium überschreitet die geprüfte Speichergrenze und wird vor einem Download
abgewiesen. Python-Pakete sind hashgebunden; Modelldownloads an feste Revisionen.

macOS verwendet eine ausdrücklich begrenzte Seatbelt-Sandbox; Linux benötigt
Bubblewrap und bricht ohne verfügbare Isolation ab. Windows besitzt derzeit
Prozess-/Ressourcenbegrenzungen, aber noch keinen gleichwertig nachgewiesenen
Datei- und Netzwerkschutz. Diese Einschränkung ist ein offener Punkt der
Windows-Freigabe, kein erfolgreich bestandener Isolationsnachweis.

Die native Druckvorschau verwendet außerdem Betriebssystemkomponenten. Ein
Unterprozess oder Virenscanner macht Dokumente nicht garantiert ungefährlich.

Im Paketprofil ist lxml 6.0.4 durch die FinTS-Abhängigkeit gebunden. Dazu besteht
die Warnung CVE-2026-41066. SEDO nutzt explizit deaktivierte externe Entitäten;
die betroffenen unsicheren Parser-Voreinstellungen dürfen nicht verwendet werden.
Die Mitigation entspricht dem Hersteller-Workaround; die Warnung wird im
Abhängigkeitsscan nicht ausgeblendet. Eine kompatible Aktualisierung bleibt eine
Wartungsaufgabe, auch wenn die geprüften eigenen Parserpfade sie bereits abfangen.
[Herstellerhinweis](https://github.com/lxml/lxml/security/advisories/GHSA-vfmq-68hx-4jfw).

## Netzwerkbetrieb: separate Abnahme erforderlich

Die lokalen Starter sind keine Netzwerkbereitstellung. Für einen bewusst
eingerichteten Server sind mindestens erforderlich:

- `SEDO_LOCAL_ONLY=0`, eigener zufälliger geheimer Django-Schlüssel und genaue
  `DJANGO_ALLOWED_HOSTS` ohne Wildcards.
- HTTPS, Secure-Session-/CSRF-Cookies und geeignete HTTPS-Weiterleitung.
- Genaue HTTPS-Ursprünge in `DJANGO_CSRF_TRUSTED_ORIGINS`.
- Nur tatsächlich vertrauenswürdige Proxy-Adressen/-Netze in
  `SEDO_TRUSTED_PROXIES`; der Proxy muss eingehende Forwarded-Header ersetzen.
  Ohne diesen Eintrag werden fremde Forwarded-Header verworfen.
- Firewall, externe Anmeldebegrenzung, eingeschränkte Serverkonten,
  geschützte Sicherungen, zeitnahe Sicherheitsupdates und Betriebsüberwachung.
- Tests der realen TLS-/Proxy-/Firewall-Konfiguration und Rechte auf diesem Server.

Eine grüne lokale Prüfung ist **keine Freigabe für Internetbetrieb**.
Keine echten Rechnungen, Datenbanken, `.env`, Schlüssel, Zugangsdaten, Backups
oder Protokolle an GitHub beziehungsweise in öffentliche Supportmeldungen senden.
