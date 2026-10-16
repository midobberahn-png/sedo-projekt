# SEDO Teststand h2.1

Diese Anleitung beschreibt reproduzierbare Softwareprüfungen und native
Testpakete. Sie bescheinigt keine Produktivfreigabe, Signierung oder Abnahme auf
einem Endnutzerrechner. Ergänzende lokale Prüfungen: [LOCAL_CHECKS.md](LOCAL_CHECKS.md).

## Automatische Pull-Request-Prüfung

`.github/workflows/pr-regression.yml` prüft jeden Pull Request gegen `main`
und jeden Push auf `main` auf einem macOS-15-ARM64-Runner. Für Django 5.2.17 und
6.0.9 laufen jeweils `tools/teststand.py validate`, `manage.py check`,
`makemigrations --check --dry-run` und die vollständigen `sedo`-Tests.
Die GitHub-Actions-Bausteine sind auf konkrete Commits festgelegt; der Job
erhält keine Betriebszugänge oder Schreibrechte am Repository. Das optionale
Diktatpaket `faster-whisper` wird wie bei der lokalen Entwicklungsprüfung
für diese beiden Testumgebungen ausgelassen. Die übrigen Release-Kernpakete
nutzen die festgelegten Versionsgrenzen aus
`requirements-release-constraints.txt`.

Der PR-Check ist eine schnelle Regression, **kein Ersatz** für den
commitgebundenen lokalen Vollprüfstand `tools/local_checks.py`, dessen
Gegenproben, den nativen Build oder die fachliche und betriebliche Abnahme.

## Was festgelegt wird

- Build-Python: **3.13.15**; Releaseprofil: Django **5.2.17**. Die zusätzliche
  Django-6.0-Kompatibilitätsprüfung bleibt in der regulären Regression erhalten.
- `requirements-release.lock` sperrt alle plattformabhängigen Paketanforderungen
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
Zusätzlich werden PDF-/Bild-/XML- und Excel-Verarbeitung samt Betriebssystem-
Isolation geprüft. Ein synthetisches Firmenprofil und eine vollständige
Rechnung durchlaufen Finalisierung, PDF-/XML-Erzeugung und Belegvalidierung.
Normaler Programmstart delegiert unverändert an `desktop.main`.

Der aktuelle native Mac-Build ist **Apple Silicon (ARM64), macOS 14 oder neuer**;
für Intel ist ein eigener geprüfter Build erforderlich. Der Paketprüfer liest die
Architektur und Mindestversion jeder eingebetteten Mach-O-Bibliothek. Fehlende
oder zu niedrige Angaben in der App-Metadatei brechen den Build ab. Das belegt die
Binäranforderung, keine Bedienungsabnahme auf allen möglichen Zielsystemen.

Am neu erzeugten Bundle werden ausschließlich die signaturunzulässigen Attribute
FinderInfo/ResourceFork entfernt; Quarantäne- und Sicherheitsattribute bleiben
unangetastet. Die strenge Signaturprüfung muss am Bundle und erneut nach frischem
Entpacken des ZIPs bestehen. Das ZIP bewahrt Laufzeit-Symlinks, übernimmt aber keine
Ressourcenforks oder erweiterten Dateiattribute. Auch eine gültige ad-hoc Signatur
ist noch keine Herausgebersignatur oder Notarisierung.
Diese Signaturprüfungen finden außerhalb synchronisierter FileProvider-Ordner
im privaten temporären Buildverzeichnis statt. Die zusätzliche entpackte App
unter `desktop/` dient nur lokalen Bedienungstests; nachträglich vom Dateisystem
angehängte Finder-Metadaten machen sie nicht zum Signaturnachweis für das ZIP.

`BUILD_MANIFEST.json`, `installed.txt`, `self-check.json` und Archivprüfsumme gehören
zum nativen Teststand. Checksummen machen die Dateien vergleichbar; sie sind keine
Signatur und kein Schutz gegen jemanden, der Datei und Manifest zusammen ersetzt.
Keine Behauptung bitidentischer nativer Builds: Betriebssystem, Compiler, eingebettete
Zeitstempel und Signaturen können native Ausgaben trotz gleicher Eingaben verändern.

## Signierter Mac-Build

Ohne explizite Signierauswahl entsteht weiterhin ein ad-hoc signiertes Testpaket.
Für einen Herausgeberbuild kann `SEDO_CODESIGN_IDENTITY` auf den exakten
40-stelligen Zertifikatsfingerabdruck einer gültigen **Developer ID Application**-
Identität im lokalen Schlüsselbund gesetzt werden. Das Buildwerkzeug prüft diese
Auswahl vorab; ein Apple-Development-Zertifikat genügt nicht. Private Schlüssel
oder Passwörter gehören weder in den Quellcode noch in Buildprotokolle.

Der Build übergibt die Identität an PyInstaller für das Programm und seine
eingebetteten Bibliotheken. Für Diktate ist ausschließlich die Mikrofon-
Berechtigung `com.apple.security.device.audio-input` vorgesehen. Anschließend
sind Apples Notarisierung, das Anheften des Tickets und eine erneute Prüfung
des tatsächlich verteilten Pakets erforderlich. Das Buildmanifest bleibt bis
zu einer gesonderten Abnahme ausdrücklich **keine Veröffentlichungsgenehmigung**.

## Noch offen – nicht durch CI ersetzt

- Bedienungsablauf und Programmfenster auf Mac/Windows, Mikrofonfreigabe und echtes
  lokales Diktat, Schließen/Neustart, Updates und Rückkehr auf eine Sicherung.
- Installation, Updates, Wiederherstellung und Gesamtprozess auf sauberen
  unterstützten Zielsystemen.
- Herausgebersignaturen, macOS-Notarisierung und vollständiger Windows-Nachweis
  der Datei-/Netzwerkisolation für nicht vertrauenswürdige Dokumente.
- Vollständige manuelle Bedienungsabnahme und fachliche Rechnungsprüfung.
- Keine Nachinstallation oder Datenänderung auf einem persönlichen Rechner durch CI.

## Lock bewusst erneuern

In einer separaten Review-Änderung mit der in release-lock.json genannten uv-Version:

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

## Windows-Installer für die Erstinstallation

Der Installer wird auf Windows mit Python 3.13.15 **x64** gebaut (auch in
Windows 11 ARM über x64-Emulation). Der manuelle Bau benötigt keine GitHub
Actions. Zusätzlich prüft `.github/workflows/windows-teststand.yml` jeden PR
auf einem Windows-x64-Runner: vollständiger Prüfstand, Installerbau,
Testinstallation, strenger Selbsttest und Deinstallation. Der erfolgreiche
Actions-Lauf liefert ein kurzlebiges Testpaket, das nach unabhängiger
Prüfsummenkontrolle als privates GitHub-Vorabrelease verteilt werden kann.

1. Sauberen Commit mit `python tools/local_checks.py --output C:\SEDO-Build\checks`
   vollständig prüfen. Das enthält beide Django-Reihen, Gegenproben und nativen Build.
2. Inno Setup 6.7.3, den offiziellen Microsoft-WebView2-Bootstrapper und das
   aktuelle Microsoft Visual C++ x64 Redistributable bereitstellen.
   Der Paketierer prüft beide Microsoft-Signaturen; keine eigenen Download-Mirrors verwenden.
   Für Visual C++ die feste HTTPS-Downloadadresse auf download.visualstudio.microsoft.com
   nach der Weiterleitung von https://aka.ms/vc14/vc_redist.x64.exe festhalten.
3. `python tools/windows_installer.py --checks C:\SEDO-Build\checks --iscc C:\SEDO-Build\Inno\ISCC.exe --webview-bootstrapper C:\SEDO-Build\MicrosoftEdgeWebview2Setup.exe --vc-runtime C:\SEDO-Build\VC_redist.x64.exe --vc-runtime-url "<feste Microsoft-Downloadadresse>" --output C:\SEDO-Build\installer`
4. Setup separat auf Windows installieren, Start/Selbsttest und Deinstallation prüfen.
   Der Prüfbericht des Builds ersetzt diesen Installationstest nicht.

Nur das hashgeprüfte native Archiv des erfolgreichen Windows-Berichts wird
verpackt. Beschädigte Pakete, fremde Plattformen und unvollständige Prüfungen werden
abgewiesen. Alle Anwendungsquellen und Anwendungstests müssen bytegleich zum
vollständig geprüften Stand sein. Reine Änderungen am Installer-Rezept, seiner
Anleitung und seinen Tests erhalten einen getrennten Commit-/Dateinachweis in
BUILD_INFO.json; dazu kommen Paketierer-Tests und der Windows-Installationstest.
Das unveränderte App-Paket wird dafür nicht neu zertifiziert. Die ZIP enthält Setup,
`START_HIER.txt`, `START_WINDOWS_TEST.bat` und `BUILD_INFO.json`.
Der Teststarter verwendet einen eigenen Datenordner
`%APPDATA%\SEDO-Test-3.21.0.2` statt des regulären Datenordners. Dieser
Testdatenordner wird bei späteren Testinstaller-Updates weiterverwendet.
Die ZIP-Prüfsumme liegt daneben.
Betriebsdaten und Zugangsdaten sind nicht enthalten. Die Ziel-PC-Abnahme
und eine Herausgebersignatur werden nicht als bereits erledigt ausgegeben.

Offizielle Quellen: https://jrsoftware.org/isdl.php und
https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution

Microsoft Visual C++ wird bei Bedarf direkt von der festen Microsoft-Adresse
geladen (SHA-256-Prüfung vor dem Start), nicht in der SEDO-ZIP weiterverteilt.
Vorhandene Laufzeiten werden anhand ihrer Version erkannt. Eine erforderliche
Administratorfreigabe erfolgt nur für diese Microsoft-Komponente.
