# Sicherheitskorrekturen und Nachprüfung, 28.09.2026

Code-Commit: `87bad550f90898ccb7effd2127d222900e30cc6b`.
Zielbasis: `eee82e8714d60f5f010a927c00a643134ed37ecd`.
Arbeitszweig: `security/audit-2026-09-28`, Entwurfs-PR #33.

## Ergebnis und Abnahmegrenze

24 Code-, Konfigurations- und Testdateien wurden für den aktuellen Zielstand geändert. Die Anwendungskorrekturen sind implementiert. Alle 24 übertragenen Git-Blob-Hashes stimmen mit dem lokalen Änderungspaket überein. `main`, echte Betriebsdaten und echte Bank-/Mailzugänge wurden nicht verändert.

**Keine vollständige Produktiv- oder native Desktop-Freigabe.** SEC-03 ist vorbereitet, nicht operativ geschlossen. Eine tatsächlich durchgeführte Datenmigration, reale Schlüsselrotation, Historienbereinigung sowie vollständige aktuelle und native Abnahme fehlen.

## Tatsächlich ausgeführte Prüfungen

- 987 Tests bestanden in 101,331 Sekunden: 910 bestehende Tests des vollständigen CI-Quellpakets `a1c708c3613fe0c34aeebf09277bc30619a5bb15` vom 21.09.2026 plus 77 neue Tests mit den Sicherheitskorrekturen. **Nicht die vollständige neueste Main-Suite.**
- 23 ursprüngliche Audit-Gegenproben bestanden in 1,514 Sekunden. Ihre Schutzbehauptungen wurden nicht abgeschwächt. Angepasst wurden die Isolation des persistenten Anmeldezustands und der berechtigte Akteur für die geheimnisfreie Einstellungsanzeige.
- Patch auf die anhand aktueller Git-Blobs geprüften betroffenen Originaldateien angewendet: `git apply --check`, tatsächliche Anwendung, SHA-256-Vergleich aller 24 Dateien und Python-Kompilierung bestanden. Diese Prüfung ersetzt keinen vollständigen Integrationstest.
- Tatsächliche Linux-Unterprozesse: normale PDF, Betrieb ohne Konsolenstreams, CPU-Abbruch und Speicherbegrenzung geprüft. Windows-/macOS-Code nicht nativ ausgeführt.
- Synthetisches echtes `git archive`: bekannte Betriebsdateien ausgeschlossen, Originaldateien erhalten. Kein Beleg für eine bereinigte Git-Historie.

Die Änderungen erhalten aktuelle Kalender-/Mail-Routen, den aktuellen Desktop-Einstieg und aktuelle betroffene UI-Tests im Zielcode. Die lokale vollständige Regression mit der älteren Basis beweist diese neueren Integrationsstellen nicht.

## Implementiert

### SEC-01

Die HTTP-Systemeinstellungen laufen über `security_views.settings_page`: Login, aktiver Administrationsbenutzer, HTTP 403 vor Seiteneffekten für normale Konten. SMTP-/FinTS-Zugangsdaten werden verschlüsselt und an Dienst und Verbindungsidentität gebunden. Identitätswechsel ohne frische Eingabe widerruft das alte Geheimnis. Alte Klartextwerte werden nicht stillschweigend übernommen. Umgebungsgeheimnisse verlangen zusätzlich eine betreiberseitige Identitätsprüfsumme.

SMTP behält die Zertifikatsprüfung. Beide FinTS-Verbraucher installieren vor dem Dialog einen eigenen HTTPS-Transport: keine geerbten Proxys, keine Weiterleitung, exaktes Ziel, begrenzte Antwort. Die Zeitprüfung zwischen Antwortblöcken ist kein unabhängiger harter Gesamtzeit-Wächter.

### SEC-02

Backup-Auflistung, Prüfung, automatische Sicherungseinstellungen und Restore-Vorbereitung sind vor dem Aufruf der bisherigen Funktion administrativ geschützt. Bestehende Archiv-, Bestätigungs- und Prüfsummenprüfungen bleiben erhalten.

### SEC-03

Exportschutz für bekannte Betriebsverzeichnisse und Root-Archive; kein universeller Geheimnisscanner. Das getestete `tools/security_storage.py` bietet eine schreibfreie Bestandsübersicht und einen expliziten Offline-Umzug in einen neuen Zielordner mit Dateiprüfung und SQLite-Integritätsprüfung. Optionale Django-Schlüsselrotation betrifft nur die Zielkopie und entfernt deren Sitzungen. Der gesonderte Mail-Verschlüsselungsschlüssel wird erhalten.

**Nicht durchgeführt:** tatsächlichen produktiven Datenordner umstellen, tatsächlich verwendete Schlüssel/Zugänge rotieren, Tracking und Historie bereinigen. Exportregeln schützen nicht Git-Klone oder vorhandene Kopien. Vor jeder Bereinigung unabhängige, geprüfte Sicherung und koordinierte Ablösung der Betriebsdaten aus Arbeitskopien.

### SEC-04

Django-Passwortvalidatoren für Ähnlichkeit, mindestens 15 Zeichen, häufige und rein numerische Passwörter. Erststartpasswort auf etwa 32 zufällige Zeichen verlängert. Bestehende Passwörter nicht automatisch verändert; direkte administrative ORM-Aufrufe sind kein durch Formularvalidierung abgesicherter Weg.

### SEC-05

Persistente SQLite-Zulassung vor der Passwortprüfung: fünf Versuche pro normalisiertem Konto in fünf Minuten; zusätzlich 100 pro Quelladresse. Beide Loginwege einschließlich Admin teilen sich das Kontokontingent. Transaktionale Reservierungen verhindern paralleles Überschreiten. Keine Klartext-Benutzernamen/IPs/Passwörter in diesem Speicher. HTTP 429 mit Retry-After, bei Speicherfehler 503 statt stiller Abschaltung. Ein neuer Prozess sieht die Sperre weiter. Ein korrektes Passwort hebt eine bereits erreichte Sperre nicht sofort auf.

### SEC-06

Begrenzte Betragssuche und Textlänge; Klassifikation in separatem Prozess mit Ressourcenlimits und Elternprozess-Zeitbegrenzung. Maximal zwei parallele Prüfprozesse pro Anwendungsprozess. Keine Zugangsdaten über die Kindprozess-Umgebung. Private Dateikommunikation funktioniert ohne Konsole. Fehler führen zu `unknown`, nie zur automatischen Freigabe. Das ist eine Ressourcenbegrenzung, keine allgemeine Datei-/Netzwerksandbox.

Text-Mikrobenchmark, Median aus drei Läufen: 12.000 Ziffern, alte CPU-Zeit ca. 0,730 s, neue ca. 0,000558 s; beide `unknown`. Keine pauschale Aussage zur Geschwindigkeit der gesamten PDF-/Mailpipeline.

## Offene Freigabeschritte

Vollständiger aktueller Quellstand mit festgelegten Abhängigkeiten; native macOS-/Windows-Builds mit tatsächlich gestartetem Prüf-Unterprozess und WebView-Abnahme; vollständiger aktueller Abhängigkeits-/CVE-Abgleich; abschließende Rollen-/Objektprüfung; reale Betriebsdaten-/Schlüsselumstellung und kontrollierte Repository-Bereinigung.

Die CI-Abfrage für Code-Commit `87bad550` lieferte im Prüfzeitpunkt keine Workflow-Läufe. Der erweiterte Workflow allein ist kein bestandener Test. PR #33 bleibt Entwurf. Es wurde kein Installationsprogramm freigegeben oder produktiv installiert.

Ausführliche Logs, Patch, Dateihashes und isolierte Gegenproben sind Bestandteil des zugehörigen Änderungspakets. Betreibermaßnahmen stehen in `SECURITY_UPGRADE_2026-09-28.md`.
