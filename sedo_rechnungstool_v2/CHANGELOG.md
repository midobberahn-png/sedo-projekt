# SEDO Rechnungstool – Änderungshistorie

**Aktuelle Version: v2.3.1** (17.08.2026)

v2.0 ist das aufgeräumte Release des bisherigen Entwicklungsstrangs
„v1.0 beta 3.x“ – gleicher Code-Stand wie beta 3.23.1, neues sauberes Paket
(`sedo_rechnungstool_v2`). Die ausführlichen Änderungsberichte der letzten
Entwicklungsschritte liegen im Projektordner in den Archiv-Versionsordnern.

## Weg zur v2.0 (Kurzfassung)

| Stand | Inhalt |
|---|---|
| **v2.3.1** | Installer + Update-Weg: Mac-DMG („in Programme ziehen“, Update = ersetzen) und Windows-Setup.exe per Inno Setup (Update = neues Setup ausführen, Daten bleiben in %APPDATA%\SEDO); Projektordner konsolidiert – ein Ordner, Altversionen entsorgt |
| **v2.3.0** | Echte Kontoanbindung (FinTS, rein lesender Umsatzabruf mit pushTAN, Bank-Referenz als Doubletten-Schlüssel); Diktat jetzt AUCH im Komplettpaket (faster-whisper gebündelt, Mikrofon-Berechtigung, Modell-Cache im SEDO-Datenordner); App-Feinschliff: Menüleiste, Fensterposition wird gemerkt, gezähmtes Kontextmenü – 245 Tests |
| **v2.2.0** | Echtes Desktop-Programm: PyInstaller-Komplettpaket mit nativem Programmfenster (pywebview/waitress, ohne Browser); Daten liegen im Benutzerordner (SEDO_HOME); Build-Skripte für Mac (`build_mac.command`) und Windows (`BUILD_WINDOWS.bat`); Diktat bleibt dem klassischen Starter vorbehalten – 236 Tests |
| **v2.1.0** | UX-Runde: globale Suche (Kunden/Rechnungen/Angebote/Objekte, Taste „/“), Dashboard mit „Heute wichtig“-Priorisierung, klarere Begriffe (GWG-Abrechnung, Wiederkehrende Rechnungen), ruhige Navigation ohne Pulse, Warnung bei ungespeicherten Formularen, Status-Badges mit Symbol; Audit-Fixes: CSRF-Cookie HttpOnly, Aareon-Import per bulk_create – 236 Tests |
| **v2.0.1** | Security: /media/ (Rechnungs-/Angebots-PDFs) nur noch mit Login abrufbar – wichtig für Heimnetz-/Cloudflare-Betrieb; Betriebsmodi LOKAL vs. NETZWERK/TUNNEL in DEPLOYMENT_SECURITY_NOTES dokumentiert – 230 Tests |
| **v2.0** (= beta 3.23.1) | Concurrency-Patch: DB-Constraints gegen parallele Doppel-Stornos/-Korrekturen/-Mahnungen, Guarded Updates für Angebot→Rechnung, Zahlungen auf korrigierte Rechnungen laufen auf die Korrekturrechnung – 228 Tests |
| beta 3.23.0 | UI-Feinschliff nach Live-Test: Kundensuche mit Trefferliste, ⓘ-Hinweise, Positionen als kaufmännische Tabellenzeilen, Vorschau-Rücksprung, bessere Angebotstexte, Business-Design (SEDO-Farben) |
| beta 3.22.0 | Härtung nach externem Review: Workflow-Sperren (Storno/Korrektur/Angebot→Rechnung, Status „korrigiert“), Mahnstufen-Modell (Erinnerung→Mahnung), zentrale Nummernkreise (NumberRange), atomare Dateierzeugung, Monatslauf-Idempotenz, Angebots-Soft-Delete, DEBUG=0 + WhiteNoise |
| beta 3.21.0 | Code-Review-Großsession: GoBD-Bugfixes (Nummernkreis, Soft-Delete, POST-only, BT-130-Einheiten), Services-Package, monochrome Briefbogen-PDFs, lokales Whisper-Diktat, Magic-Tone, Plausibilitäts-Check, Sicherheitspaket, Desktop-Starter |
| beta 3.18–3.20 | KI-Angebots-Generator (Claude), Schritt-Assistent für Angebot/Rechnung, Leistungsbeschreibungen inkl. BT-154 |
| beta 3.12–3.17 | ARP-Compliance, SMTP-Versand, Mahnautomat, Rechnungsformular-Komfort, PDF-Design, KI-Diktat (erst OpenAI, dann Claude) |
| bis beta 3.11 | Kernentwicklung: ZUGFeRD-XML (Aareon/GWG-Mapping, Leistungsort BT-75), Rechnungs-PDF, GWG-Monatslauf |

## Wichtige Eckdaten v2.0

- 228 automatisierte Tests, verifiziert unter Django 6.0.8 / Python 3.14
- Start: `SEDO.app` (Mac) bzw. `SEDO.vbs`/`DESKTOP_VERKNUEPFUNG_ERSTELLEN.bat`
  (Windows); Details in der `README.md`
- Erststart erzeugt einmalig ein Admin-Zufallspasswort (Dialog) – notieren!
- Vor dem ersten ARP-/Tönnies-Echtlauf: ARP Seller-ID + GWG Receiver-ID
  eintragen und die Musterrechnung durch den offiziellen ZUGFeRD-Validator
  laufen lassen (siehe README/Änderungsberichte)
