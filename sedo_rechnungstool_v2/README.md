# SEDO Rechnungstool · v1.0 beta 3.21.0

Lokales Rechnungstool für ZUGFeRD-E-Rechnungen. Der KI-Assistent nutzt
**Bonsai auf dem SEDO-Rechner**: keine Cloud-Anfragen und keine externen
Modell-API-Gebühren. Lokales Diktat nutzt Whisper; ohne lokale Spracherkennung
bleibt Texteingabe verfügbar. Mail, Bank und Wetter verwenden weiterhin ihre
jeweiligen ausdrücklich eingerichteten Schnittstellen.

## Starten (empfohlen: Desktop-Modus)

**Mac:** `SEDO.app` doppelklicken → SEDO öffnet sich als eigenes Programmfenster.
Tipp: `SEDO.app` ins Dock ziehen.

**Windows:** Einmalig `DESKTOP_VERKNUEPFUNG_ERSTELLEN.bat` doppelklicken –
danach liegt „SEDO Rechnungstool“ mit Logo auf dem Desktop und startet das
Programmfenster (über `SEDO.vbs`).

Beim **allerersten Start** richtet sich SEDO selbst ein (Pakete installieren,
kann einige Minuten dauern) und zeigt **einmalig das Admin-Passwort** an –
bitte sofort notieren!

- Benutzer: `admin`
- Passwort: wird beim ersten Start einmalig angezeigt
  (ändern/zurücksetzen: `python manage.py changepassword admin`)

## Beenden

Das Fenster schließen beendet nur die Ansicht – der Server läuft weiter
(nächster Start ist dadurch sofort da). Komplett beenden:
`SEDO_BEENDEN.command` (Mac) bzw. `SEDO_BEENDEN.bat` (Windows).

## Klassischer Start (Fallback / Fehlersuche)

`START_HIER.command` (Mac) bzw. `START_WINDOWS.bat` (Windows) doppelklicken –
zeigt alle Meldungen sichtbar an. Danach im Browser: http://127.0.0.1:8000

## Erste Schritte nach der Installation

1. `.env` nach dem Muster von `.env.example` anlegen (Produktivbetrieb).
2. Bonsai auf demselben Rechner installieren; SEDO startet es aus `Dokumente/Bonsai-demo` automatisch mit. Einstellungen → **KI-Assistent (Bonsai · lokal)** → **Gespeicherte Verbindung prüfen**. Anderer Installationsort und Details: [BONSAI.md](BONSAI.md).
3. Erstes Diktat lädt das Whisper-Sprachmodell (~460 MB) einmalig herunter.
4. Vor dem ersten ARP-/Aareon-Echtlauf: Checkliste in `MEMORY_SEDO.md` beachten
   (ARP Seller-ID, Empfänger-ID, BT-130/BT-154-Abgleich mit Tönnies).

Details zu allen Änderungen: `AENDERUNGSBERICHT_v1_0_beta_3_21_0.md`


## Komplettpaket (echtes Desktop-Programm, seit v2.2)

Alternativ zum klassischen Starter gibt es SEDO als gebündeltes Programm mit
eigenem Fenster – ohne Browser und ohne Python-Installation:

- **Mac:** `build_mac.command` doppelklicken → `dist/SEDO Rechnungstool.app`
  (bei Bedarf in „Programme“ ziehen).
- **Windows:** Ordner auf einen Windows-Rechner kopieren (z. B. Parallels),
  Python 3.12+ von python.org installieren („Add to PATH“ anhaken),
  `BUILD_WINDOWS.bat` doppelklicken → `dist\SEDO Rechnungstool\…exe`.
  Den GANZEN dist-Ordner auf den Zielrechner kopieren.

Wichtig zu wissen:
- Das Komplettpaket speichert seine Daten im **Benutzerordner**
  (Mac: `~/Library/Application Support/SEDO`, Windows: `%APPDATA%\SEDO`) –
  getrennt vom Projektordner des klassischen Starters. Bestehende Daten
  übernehmen: die Ordner `data/` und `media/` dorthin kopieren (bei
  gestopptem Programm).
- Beim allerersten Start zeigt das Fenster einmalig das Admin-Zufallspasswort.
- Seit v2.3 ist das **Whisper-Diktat auch im Komplettpaket enthalten** –
  beim ersten Diktat fragt macOS/Windows einmalig nach der Mikrofon-Freigabe,
  und das Sprachmodell (~460 MB) lädt in den SEDO-Datenordner.

## Kontoanbindung (FinTS, seit v2.3)

Unter **Einstellungen → Bankkonto** BLZ, FinTS-Adresse der Bank und
Anmeldenamen hinterlegen (PIN-Speicherung optional; gespeicherte PINs werden
aus Backups bereinigt). Danach gibt es unter **Zahlungen** den Knopf
„Umsätze abrufen“ – SEDO holt die Kontoumsätze direkt bei der Bank
(rein lesend, niemals Überweisungen) und ordnet sie den Rechnungen zu.
Je nach Bank ist pro Abruf eine pushTAN-Freigabe in der Banking-App nötig –
SEDO wartet bis zu 90 Sekunden auf die Bestätigung.


## Installieren & Updaten (seit v2.3.1)

**Mac:** `dist/SEDO_Rechnungstool_Installer.dmg` öffnen → App auf „Programme“
ziehen. **Update:** neues DMG, App erneut rüberziehen und ersetzen – fertig,
die Daten bleiben erhalten (liegen unter `~/Library/Application Support/SEDO`).

**Windows:** `BUILD_WINDOWS.bat` erzeugt (mit installiertem Inno Setup 6)
zusätzlich `dist\SEDO_Rechnungstool_Setup_<Version>.exe` – ein normales
Setup mit Startmenü-/Desktop-Verknüpfung und Deinstallierer.
**Update:** einfach das neue Setup.exe ausführen; es installiert über die
alte Version, die Daten in `%APPDATA%\SEDO` bleiben unberührt.
Hinweis: Ohne Code-Signing-Zertifikat zeigt Windows beim ersten Start des
Setups „Unbekannter Herausgeber“ – „Weitere Informationen → Trotzdem
ausführen“ wählen.
