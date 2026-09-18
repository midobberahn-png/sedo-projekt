# SEDO Teststand h2.1

Dieser Teststand ist Teil des freigegebenen Härtungsplans. Keine Produktivfreigabe,
kein signierter Installer, keine Abnahme von Papas Rechner. Dieser Rechner ist
frühestens am **19.09.2026** verfügbar. UI/UX folgt nach technischer Härtung.

## Was festgelegt wird

- Build-Python: **3.13.15**; Releaseprofil: Django **5.2.17**. Die zusätzliche
  Django-6.0-Kompatibilitätsprüfung bleibt in der regulären Regression erhalten.
- `requirements-release.lock` sperrt alle 76 plattformabhängigen Paketanforderungen
  samt SHA-256 der zugelassenen Distributionen. Auf einem einzelnen Betriebssystem
  werden nur die zutreffenden Plattformmarker installiert.
- `release-lock.json` bindet Lock, Python und vier Eingabedateien an ihre Prüfsummen.
  Eine geänderte Eingabe ohne explizit aktualisierten Lock bricht den Build ab.
- Lokales Diktat bleibt enthalten. Der Build lädt kein Whisper-Modell und verarbeitet
  keine Audioaufnahme. Ein Import-/Verfügbarkeitscheck ist kein Mikrofontest.
- Jede Installation der Build-Abhängigkeiten benutzt eine neue temporäre Umgebung.
  Auch pip, setuptools, wheel und packaging sind gesperrt. sdist-Pakete werden ohne
  automatische isolierte Nachinstallation unbekannter Buildabhängigkeiten gebaut.

## Lokale Buildbefehle – im sauberen Git-Klon

```bash
python tools/teststand.py validate
python tools/teststand.py source --output /pfad/zu/neuem/ausgabeordner
python tools/teststand.py build --output /pfad/zu/neuem/native-ausgabeordner
```

Für `install` und `build` muss Python genau 3.13.15 sein. Ein bestehender Zielordner
für native Builds bzw. eine vorhandene virtuelle Umgebung wird nicht überschrieben.
`build_mac.command` und `BUILD_WINDOWS.bat` sind nun dünne Einstiege in diesen
identischen Buildweg; eigene Paketlisten und `pip install --upgrade` entfallen.

**Bewusste Umstellung:** Diese beiden Einstiege erstellen portable Testpakete in
einem neuen temporären Ausgabeordner, nicht mehr automatisch einen Inno-Setup-
Installer oder ein Release unter `dist/`. Installer, Signierung/Notarisierung und
Versionsanzeigen der endgültigen Auslieferung sind eigene spätere Abnahmeschritte.
Die bisherige Versionsanzeige im normalen Programm bleibt hier unverändert;
den Teststand eindeutig über Dateiname, Commit und `BUILD_MANIFEST.json` zuordnen.

Ein Quellarchiv dient zur nachvollziehbaren Ablage der verwendeten Quellen; für
einen erneuten Build wird der darin bezeichnete Commit im Git-Klon verwendet.
Das Quellarchiv enthält keine Git-Historie. Der normale Build löst keine neuen
Paketversionen auf und aktualisiert den Lock nicht automatisch.

## Ergebnisse und Nachweise

Das Quellpaket enthält nur erlaubte, versionierte Programmdateien. Kein `data/`,
`media/`, `backups/`, `logs/`, keine `.env`, keine Secret-Datei oder virtuelle
Umgebung. `.env.example` ist absichtlich enthalten. Jede Quelldatei hat einen
SHA-256-Eintrag im `TESTSTAND_MANIFEST.json`; Commit und Git-Tree stehen ebenfalls darin.
Bei gleichem Commit und derselben Packumgebung ist das Quell-ZIP bytegleich.

Native Pakete müssen auf ihrem jeweiligen Betriebssystem gebaut werden. Vor der
Veröffentlichung wird das tatsächlich gebündelte Programm mit `--check-strict`
ausgeführt. Es muss Login-HTTP, CSS-Auslieferung, vollständige Migrationen, verfügbare
Whisper-Bibliothek und Hardlinks auf seinem isolierten Testdateisystem nachweisen.
Normaler Programmstart delegiert unverändert an `desktop.main`.

`BUILD_MANIFEST.json`, `installed.txt`, `self-check.json` und Archivprüfsumme gehören
zum nativen Teststand. Checksummen machen die Dateien vergleichbar; sie sind keine
Signatur und kein Schutz gegen jemanden, der Datei und Manifest zusammen ersetzt.
Keine Behauptung bitidentischer nativer Builds: Betriebssystem, Compiler, eingebettete
Zeitstempel und Signaturen können native Ausgaben trotz gleicher Eingaben verändern.

## Noch offen – nicht durch CI ersetzt

- Bedienungsablauf und Programmfenster auf Mac/Windows, Mikrofonfreigabe und echtes
  lokales Diktat, Schließen/Neustart, Updates und Rückkehr auf eine Sicherung.
- Installation und Gesamtprozess auf Papas Einsatzrechner ab 19.09.2026.
- H3-Zustandsmodell und übrige risikobasierte Härtung.
- Vollständige manuelle Bedienungsabnahme der UI/UX-Aufwertung auf Papas Einsatzrechner.
- Keine Nachinstallation oder Datenänderung auf einem persönlichen Rechner durch CI.

## Lock bewusst erneuern

In einer separaten Review-Änderung mit uv 0.10.0:

```bash
uv pip compile --universal --python-version 3.13 --generate-hashes --no-emit-index-url requirements-release.in -o requirements-release.lock
```

Danach `release-lock.json` mit den SHA-256 der tatsächlich geprüften Eingaben und
des neuen Locks aktualisieren und native Builds, Regression und Gegenproben neu
prüfen. Normale Builds dürfen nicht selbst reparieren, wenn diese Angaben abweichen.

Primärquellen zum Werkzeugverhalten:
- https://docs.astral.sh/uv/pip/compile/
- https://docs.astral.sh/uv/reference/settings/#require-hashes
- https://pyinstaller.org/en/stable/index.html
