# Lokale Entwicklungsprüfung

Die Prüfungen können auf einem eigenen Testrechner ohne GitHub-Actions-Kontingent
ausgeführt werden. Ein solcher Lauf öffnet keinen dauerhaften GitHub-Runner auf
einem Arbeitsrechner. Repository-Einstellungen werden dabei nicht verändert.

## Ein Aufruf

Im sauberen, bereits committeten Git-Checkout mit **Python 3.13.15**:

```bash
python3.13 tools/local_checks.py
```

Unter Windows entsprechend `python tools\local_checks.py` mit derselben
Python-Version. Der Rechner muss Git und Zugriff auf die Paketquellen haben;
Tests und Builds selbst laufen lokal. Es fallen keine GitHub-Actions-Minuten an.
Ein Modellserver oder echte Mail-/Bankzugänge werden nicht benötigt.

Der Aufruf erstellt zwei frische, temporäre Testumgebungen für Django 5.2 und 6.0.
Die Testpakete entsprechen dem bisherigen Kompatibilitätslauf; tatsächlich
installierte Versionen werden protokolliert. Der native App-Build verwendet
weiter den vollständigen Hash-Lock mit exakt gesperrten Abhängigkeiten.

Optional können bereits vorhandene **dedizierte Testumgebungen** verwendet werden:

```bash
python3.13 tools/local_checks.py --python52 /pfad/test52/bin/python --python60 /pfad/test60/bin/python
```

Diese werden nicht verändert. Python-/Django-Versionen werden vor den Tests
geprüft und sämtliche Paketversionen im Bericht festgehalten. Kein Rückgriff auf
die installierte SEDO-App oder ihre Datenbank.

## Was der vollständige Lauf prüft

- Zwei bytegleiche Quellpakete ausschließlich aus versionierten Programmdateien.
- Gesamte Testsuite unter Django 5.2 und 6.0.
- Alle zwölf Gegenprobenketten: Entwurf, Belegketten, Indizes, Belegarchiv,
  Mahnwächter, Abo-Zahlungsziel, Bedienrisiken, Buildvertrag, E-Mail, Eingangsbelege, Spamfilter und Kalender.
- Koordinierte SQLite-Parallelitätsfälle und synthetischer 50.000-Beleg-Indexvergleich.
- Fehlende Modellmigrationen.
- macOS/Windows: nativer Build mit Release-Lock, erneute Gesamtsuite und echter
  gebündelter Selbsttest. Linux: Release-Umgebung, Gesamtsuite und Quell-Selbsttest.
- Unveränderter Git-Stand vom Beginn bis zum Ende der Prüfung.

Fehler brechen ab und ergeben keinen grünen Abschluss. Ein unterbrochener Lauf
bleibt als unvollständig erkennbar. Die Ausgabe wird nicht über einen vorherigen
Bericht geschrieben. Temporäre Quellen und Testdaten sind von den Betriebsdaten
getrennt; SEDO-/Django-Konfiguration aus der Umgebung wird für Testprozesse entfernt.

## Ergebnisse und Entwicklungsgate

Unter `.local-checks/Zeit-Commit/` liegen `report.md`, `report.json`, Einzelprotokolle,
Quellpakete und auf Mac/Windows das native Paket unter `native/`. Der Ordner wird
nicht mit Git synchronisiert. `--output /neuer/ordner` wählt einen anderen Ort.
Ein einzelner Lauf kann wegen des App-Pakets mehrere GB belegen.

Für einen Entwicklungs-Merge müssen `status: passed` und `development_ready: true`
vorliegen, Commit und Git-Baum zum PR-Kopf passen und die Änderung geprüft sein.
Das Ergebnis mit Plattform und wesentlichen Einschränkungen in der PR-Beschreibung
festhalten. Nur danach mergen bzw. die **lokale Entwicklungsinstallation** mit
Sicherung aktualisieren. Keine als erfolgreich ausgegebenen fremden CI-Kontexte
und kein Übergehen tatsächlicher Testfehler.

Ein Mac-Bericht ist **keine Windows- oder Linux-Prüfung**. Vor einer Auslieferung
auf Windows denselben Lauf auf Windows durchführen; Bonsai, Mikrofon, Bedienung
und Installation auf dem Einsatzrechner bleiben zusätzliche Abnahmen. Eine
Windows-VM kann das später ergänzen, ist hier nicht automatisch installiert.
Die Berichte dokumentieren lokale Ausführung; sie sind keine signierte externe
Zertifizierung und können von einem lokalen Bearbeiter verändert werden.

## Ergänzende Prüfung auf GitHub

Die automatische PR-Prüfung ist in
[TESTSTAND.md](TESTSTAND.md#automatische-pull-request-prüfung) beschrieben.
Sie prüft denselben Commit auf macOS ARM64 und im Linux-Webprofil mit
synthetischen Testdaten. Der Windows-PR-Job führt zusätzlich den vollständigen
Windows-Prüfstand mit Gegenproben, nativem Build und Installer aus. Der
commitgebundene lokale Vollprüfstand bleibt für den Entwicklungs-Merge
Voraussetzung; der Windows-Job ersetzt keinen Mac-Build.
Betriebsdaten, Zugangsdaten und lokale Testprotokolle werden nicht hochgeladen.
Die lokale Prüfung verändert weder Workflow-Einstellungen noch Branch-Schutzregeln.
