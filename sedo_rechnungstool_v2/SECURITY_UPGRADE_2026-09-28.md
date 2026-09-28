# Kontrollierte Übernahme der Sicherheitskorrekturen

PR #33, Code-Commit `87bad550f90898ccb7effd2127d222900e30cc6b`. Diese Betreibermaßnahmen wurden nicht auf der echten Installation ausgeführt. Der Stand ist bis zur vollständigen aktuellen und nativen Abnahme kein freigegebenes Release.

## Vor jeder Umstellung

Den tatsächlich verwendeten `SEDO_HOME`, Programmstand und alle Arbeitsplätze feststellen. Unabhängige geschützte Sicherung anlegen und deren Wiederherstellbarkeit prüfen. SEDO und andere schreibende Serverprozesse vollständig beenden. Kein blindes Überschreiben eines laufenden Datenordners und kein Löschen von Betriebsdaten aus Git vor ihrer gesicherten Ablösung. Die Desktop-Sperre des Werkzeugs erkennt nicht jeden beliebigen externen Schreiber.

## Übersicht ohne Änderung

Im App-Verzeichnis, mit dem festgestellten absoluten Datenpfad:

```bash
python tools/security_storage.py --source /ABSOLUT/ALT
```

Standardmäßig keine Kopie oder Rotation. Die Ausgabe nennt Anzahl, Größen und Existenzangaben, keine Geheimniswerte.

## Offline-Kopie und optionale Django-Schlüsselrotation

Das Ziel muss neu sein und außerhalb der Quelle liegen. Nach bestätigtem Stillstand:

```bash
python tools/security_storage.py --source /ABSOLUT/ALT --destination /ABSOLUT/NEU --execute --offline-confirm --rotate-django-key
```

Die Quelle bleibt erhalten. Nur die Zielkopie erhält den neuen Django-Schlüssel und verliert ihre alten Sitzungen. Bank-/Mail-Anbieterpasswörter werden nicht geändert. Der gesonderte Mail-Schlüssel bleibt erhalten, damit bestehende verschlüsselte Postfach-/Kalenderzugänge nicht unlesbar werden.

Bei überschreibendem `DJANGO_SECRET_KEY` in Umgebung oder `.env` wird diese automatische Rotation abgelehnt. Dann genau diese externe Vorgabe kontrolliert ersetzen; eine bloß geänderte `.secret_key`-Datei wäre wirkungslos. Den alten Schlüssel nicht dauerhaft als Fallback weiterführen.

Den geprüften Zielpfad anschließend in der tatsächlichen Startkonfiguration als `SEDO_HOME` setzen, bevor Django seine Einstellungen lädt. Prüfen, dass nicht versehentlich eine leere Datenbank im alten Standardpfad gestartet wird. Unter Windows die tatsächlichen Zugriffsrechte des Zielordners prüfen: POSIX-Modusprüfungen belegen keine NTFS-ACLs.

## SMTP und FinTS nach dem Upgrade

Alte ungebundene Klartextzugänge werden nicht automatisch weiterverwendet. Als Administration die vorgesehenen Hosts, Ports, Bankadresse und Anmeldeidentität kontrollieren und Passwort/PIN neu eingeben. Bei einem späteren Identitätswechsel wird das alte Geheimnis ohne neue Eingabe nicht weitergereicht.

Für Umgebungsgeheimnisse sind zusätzlich `SEDO_SMTP_IDENTITY_SHA256` und/oder `SEDO_FINTS_IDENTITY_SHA256` erforderlich. Nach Kontrolle der tatsächlich eingestellten Verbindungen können die Prüfsummen ohne Geheimnisausgabe gelesen werden:

```bash
python manage.py shell -c "from sedo.services.credential_binding import load_config, identity_digest; print('SEDO_SMTP_IDENTITY_SHA256=' + identity_digest('smtp', load_config('smtp'))); print('SEDO_FINTS_IDENTITY_SHA256=' + identity_digest('fints', load_config('fints')))"
```

Die jeweilige Prüfsumme zusammen mit dem betreffenden Geheimnis außerhalb der Weboberfläche konfigurieren. Ein Zielwechsel in SEDO verändert diese Betreiberbindung nicht automatisch. Keine echten Geheimnisse in Shell-Befehlszeilen, Logs, Issues oder PR-Kommentare schreiben.

## Anmelden und fachlich abnehmen

Bestehende Passwort-Hashes wurden nicht geändert. Erforderliche Passwortänderungen kontrolliert durchführen. Nach fünf Anmeldeversuchen pro Konto innerhalb von fünf Minuten gilt eine zeitweilige Sperre; ein korrektes Passwort hebt eine erreichte Sperre nicht sofort auf. Den Sicherheitszustand nicht bei jedem Start automatisch löschen.

Normale Benutzer müssen im administrativen Systembereich 403 erhalten. Die Administration muss Einstellungen/Backup weiterhin bedienen können. Rechnungsabläufe, Dokumentimport und Wiederherstellung auf einer isolierten Kopie prüfen. Ein Dokumentstatus `unknown` nach Ressourcenüberschreitung verlangt manuelle Prüfung.

## Git-Tracking und Historie

`export-ignore` schützt die erfassten Quellarchiv-Pfade, nicht `git clone`, Commit-Historie oder vorhandene Kopien. Eine Löschung aus dem Git-Baum kann bei anderen Arbeitskopien Betriebsdateien entfernen. Deshalb zuerst aktive Betriebsdaten aus synchronisierten Arbeitskopien herauslösen, unabhängige Sicherung prüfen und erst danach die gezielte Bereinigung koordinieren. Hier wurde kein Verlauf umgeschrieben und kein produktiver Datenbestand gelöscht.

## Isolierter Retest

Nur vollständigen versionsgleichen Checkout mit eigener Testumgebung verwenden. `SEDO_HOME` auf einen neuen temporären Ordner umlenken, synthetischen `DJANGO_SECRET_KEY` setzen, keine echten Zugangsdaten übernehmen.

```bash
python manage.py test sedo.tests.test_security_remediation sedo.tests.test_security_storage sedo.tests.test_banking_transport_security --noinput
python manage.py test sedo --noinput
```

Gesamten aktuellen Release-Lock sowie native Builds separat prüfen. Die dokumentierten 987 erfolgreichen Tests liefen auf der älteren vollständigen CI-Basis plus Korrekturen; sie ersetzen diese aktuelle Gesamtprüfung nicht.
