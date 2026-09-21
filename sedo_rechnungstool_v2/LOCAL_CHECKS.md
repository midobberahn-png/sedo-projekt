# Lokale Entwicklungsprüfung

Während der Entwicklung läuft die Prüfung auf dem eigenen Rechner. GitHub dient
weiterhin für Versionsverwaltung und Pull Requests. Die zwei automatischen
GitHub-Actions-Workflows für Regression und Teststand werden in den Repository-
Einstellungen ausgesetzt; ihre Definitionen bleiben für spätere Plattformläufe
erhalten. Es wird kein dauerhafter GitHub-Runner auf einem Arbeitsrechner geöffnet.

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
- Alle neun Gegenprobenketten: Entwurf, Belegketten, Indizes, Belegarchiv,
  Mahnwächter, Abo-Zahlungsziel, Bedienrisiken, Buildvertrag und E-Mail.
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

## GitHub-Prüfungen bei Bedarf wieder einschalten

Ausgesetzt werden nur `SEDO Regression und Gegenproben` und `SEDO Teststand`.
GitHub speichert das außerhalb des Git-Baums als `disabled_manually`. Alte rote
Läufe wegen des Kontolimits bleiben sichtbar. Branch-Schutzregeln werden nicht
geändert. Weitere Workflows sind kein Bestandteil dieser Umstellung.

Nach bewusster Entscheidung für wieder verfügbare Actions-Kapazität:

```bash
gh workflow enable sedo-entwurf-tests.yml
gh workflow enable sedo-teststand.yml
gh workflow run sedo-teststand.yml --ref BRANCH
```

Das Einschalten aktiviert auch die bisherigen automatischen PR-/Push-Auslöser.
Die native Windows-/Mac-Matrix wird explizit über `workflow_dispatch` gestartet.
GitHub beschreibt das reversible Aussetzen in seiner
[Dokumentation](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows).
