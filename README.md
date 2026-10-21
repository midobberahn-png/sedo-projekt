# SEDO Rechnungstool

Für die interne Webversion mit gemeinsamem Datenbestand siehe [SEDO Online](WEBBETRIEB.md). Desktop-Installationen bleiben lokal, bis der Online-Modus ausdrücklich eingerichtet wird.

SEDO erstellt und verwaltet Rechnungen einschließlich ZUGFeRD-E-Rechnungen.
Bonsai und Whisper verarbeiten Assistentenanfragen beziehungsweise Diktate lokal.
Eingerichtete Mail-, Kalender-, Bank- und Wetterverbindungen kommunizieren mit
den jeweiligen Anbietern. Beim erstmaligen Diktat wird ein Sprachmodell geladen.

## Programm ausprobieren und prüfen

Den vollständigen Quellcode über **Code → Download ZIP** herunterladen oder
dieses Repository klonen. Für den Start aus dem Quellcode Python **3.13.15**
installieren, das Archiv vollständig entpacken und den passenden Starter ausführen:
`START_HIER.command` auf macOS oder `START_WINDOWS.bat` auf Windows.
Abhängigkeiten werden beim ersten Start automatisch eingerichtet.

Für Apple-Silicon-Macs ab macOS 14 gibt es außerdem ein signiertes und
notarisiertes Testprogramm unter [Releases](https://github.com/midobberahn-png/sedo-projekt/releases/tag/v3.21.0-rc.1).
Dafür ist keine Python-Installation nötig. Für Windows x64 ist der
[Testinstaller 3.21.0.7](https://github.com/midobberahn-png/sedo-windows-updates/releases/tag/v3.21.0.7-windows)
öffentlich erhältlich; auch dafür ist kein Python nötig. Der einzelne
Installer-Download enthält keinen getrennten Teststarter. Das Windows-Setup
ist nicht mit einem Herausgeberzertifikat signiert und auf dem Ziel-PC noch
nicht abgenommen. Version 3.21.0.7 enthält den Online-Modus und unter
**Hilfe → Auf Windows-Updates prüfen** das [Ein-Klick-Update](WINDOWS_UPDATES.md).
Das Update-Manifest wird kryptografisch geprüft; der Installer selbst bleibt
ohne Herausgebersignatur. Das Update-Menü wurde bereits mit Version 3.21.0.5
veröffentlicht. Bei einer Erstinstallation wird das aktuelle Setup benötigt;
ein Update von einer Vorversion auf dem Ziel-PC ist noch nicht abgenommen.

Bei einer neuen lokalen Installation nach der ersten Anmeldung die eigenen
Firmendaten einrichten. Zum Ausprobieren zunächst erfundene Kunden und
Rechnungen verwenden. Zugangsdaten für optionale
Mail-, Bank- und Kalenderdienste richtet jeder Tester selbst ein.

Entwickler finden die vollständigen Tests in `sedo/tests/`; die reproduzierbare
Prüfung und Buildanleitung stehen in [LOCAL_CHECKS.md](LOCAL_CHECKS.md) und
[TESTSTAND.md](TESTSTAND.md). Der Weg von Eingabe über Belegerzeugung bis
zum Archiv steht in [RECHNUNGSREGELN.md](RECHNUNGSREGELN.md). Fehlerberichte
sollten den Ablauf, die Plattform und den verwendeten Commit nennen und keine
Passwörter oder Geschäftsdaten enthalten.

## Stand und Freigabe

Dieser Branch ist ein gehärteter **Veröffentlichungskandidat**, keine bestätigte
allgemeine Produktfreigabe. Erfolgreiche Softwaretests ersetzen weder die
Prüfung der Installationspakete auf den Zielsystemen noch eine fachliche
Abnahme von Rechnungen. Software wird hier nicht als „unhackbar“ bezeichnet.

Der Quellstand enthält Code, Migrationen, Vorlagen, statische Dateien,
Prüfschemata, Start-/Installationswerkzeuge, Regressionstests und aktuelle
Bedien-/Sicherheitshinweise. Nicht enthalten: echte Datenbanken, Rechnungen,
Kundendaten, Zugangsdaten, Schlüssel, Backups, Protokolle oder fertige Programme.
Git-Ausschlüsse sind eine zusätzliche Sicherung, kein Ersatz für eine Uploadprüfung.

## Start aus dem Quellstand

Genau **Python 3.13.15** verwenden. Auf macOS `START_HIER.command`, auf Windows
`START_WINDOWS.bat` starten. Beim ersten Start wird eine getrennte Umgebung mit
festen Paketversionen und geprüften Paket-Hashes eingerichtet. Dafür wird eine
Internetverbindung benötigt. Danach im Browser `http://127.0.0.1:8000` öffnen.

Der Server ist ausschließlich auf dem eigenen Rechner erreichbar. Das Terminal
offen lassen; mit **Strg+C in diesem Terminal** beenden. Die kleine `SEDO.app`
im Quellordner ist nur eine Startverknüpfung, kein gebündeltes Desktop-Programm.
Die Beenden-Hilfen erklären diesen Ablauf und beenden keine fremden Prozesse.

Beim ersten Start wird der Benutzer `admin` mit einem zufälligen Passwort
angelegt. Das Passwort wird einmalig angezeigt: sicher notieren und anschließend
ändern. Niemals Bildschirmfotos oder Startausgaben mit diesem Passwort teilen.
Eine `.env` ist für den lokalen Standardbetrieb nicht erforderlich; zusätzliche
Konfiguration nur anhand von [.env.example](.env.example) vornehmen.

## Gebündeltes Desktop-Programm

Das hier geprüfte Mac-Paket ist für **Apple Silicon (ARM64), macOS 14 oder neuer**.
Intel-Macs benötigen einen eigenen geprüften Build; dieses Paket ist nicht universal.
Die angegebene Mindestversion beschreibt die Binäranforderung, nicht eine bereits
erfolgte Bedienungsabnahme auf jeder macOS-Version.

Ein auf dem jeweiligen Betriebssystem gebautes Desktop-Paket benötigt keine
separate Python-Installation. Es speichert Daten getrennt vom Programm:

- macOS: `~/Library/Application Support/SEDO`
- Windows: `%APPDATA%\\SEDO`

Das Schließen des Programmfensters beendet den zugehörigen lokalen Server.
Build- und Installationsprüfung: [TESTSTAND.md](TESTSTAND.md) und
[Windows-Erstinstallation](WINDOWS_ERSTINSTALLATION.txt). Die Buildstarter
erzeugen zunächst Testpakete; sie erzeugen nicht automatisch ein signiertes,
notarisiertes oder allgemein freigegebenes Installationspaket.

## Einrichtung und sicherer Betrieb

1. Nach der ersten Anmeldung öffnet sich **Firmendaten einrichten**.
   Mit einem Administrationskonto die eigenen Firmen-, Kontakt-, Steuer- und
   Bankdaten eingeben. Erst mit einem gültigen Profil werden die Arbeitsbereiche
   freigegeben. Die Angaben werden lokal für diese Installation gespeichert;
   es sind keine Firmendaten vorbelegt. Bestehende gültige Profile bleiben bei
   Updates erhalten. Spätere Änderungen erfolgen unter **Einstellungen →
   Firmendaten**. Wer umzieht, kann auf der Erststartseite stattdessen eine
   vorhandene Sicherung wiederherstellen. Eine Installation verwaltet ein
   Unternehmen. Anschließend Empfängerkennungen,
   Steuersätze, Leistungszeiträume und erzeugte Rechnungsdateien vor dem
   Echtbetrieb fachlich prüfen.
2. Administrationskonten sparsam vergeben. Die explizite Rolle
   `SEDO Mitarbeiter Lesen` ist schreibgeschützt; normale Benutzer können
   Geschäftsdaten bearbeiten. Details: [Sicherheitshinweise](DEPLOYMENT_SECURITY_NOTES.md).
3. Zugangsdaten nur für die tatsächlich gewünschten Anbieter einrichten.
   Gespeicherte Bank-PIN und SMTP-Passwort sind verschlüsselt und an das
   konfigurierte Ziel gebunden. Zieländerungen verlangen neue Zugangsdaten.
4. Sicherungen und Wiederherstellung mit Testdaten erproben. Exporte und
   Sicherungen enthalten Geschäftsdaten und gehören niemals ins Repository.

Bedienung: [BEDIENHINWEISE.md](BEDIENHINWEISE.md),
[E-Mail](E_MAIL.md), [Kalender](KALENDER.md),
[Eingangsrechnungen](EINGANGSRECHNUNGEN.md), [Bonsai](BONSAI.md).
Für Änderungen am Rechnungsablauf: [Rechnungsregeln](RECHNUNGSREGELN.md).

Für öffentliche Verteilung müssen außerdem die Herausgeberlizenz und die
Lizenzhinweise aller mitgelieferten Drittkomponenten geklärt sein. Dieser
Quellstand räumt nicht allein durch seine Zugänglichkeit neue Lizenzrechte ein.
