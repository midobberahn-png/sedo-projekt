# Übergabe: Cowork-Session 14.08.2026 – SEDO Rechnungstool

> Zweck: Nahtlose Fortsetzung in einer neuen Session. Zuerst lesen, dann
> `CLAUDE_SEDO.md` → `MEMORY_SEDO.md` → `kontext.md` (alle heute aktualisiert).

---

## 1. Wo stehen wir?

- **Version:** v1.0 beta **3.21.0** (von 3.20.0) im Ordner
  `SEDO_Rechnungstool_Django_v1_0_beta_3_21_0_Clean` + gleichnamiges ZIP.
  Das alte v3.20.0 (Ordner + ZIP) blieb unangetastet.
- **Qualitätsstand:** **165 Tests grün**, verifiziert unter **Django 6.0.8**
  (requirements-konform `>=5.2,<6.1`), Python 3.14, inkl. installiertem
  faster-whisper. `manage.py check --deploy` mit .env-Beispielwerten sauber
  (nur 2 bewusste HSTS-Hinweise).
- **Komplette Änderungshistorie:** `AENDERUNGSBERICHT_v1_0_beta_3_21_0.md`
  im Versionsordner – 15 Abschnitte, chronologisch, mit Begründungen.

## 2. Was in dieser Session passiert ist (chronologisch)

1. **Code-Review + Bugfixes + Modularisierung** (Auftrag: „prüfen, berichtigen,
   ordnen nach Knigge“; Mirek gab maximalen Umfang frei):
   GoBD-Fixes (Nummernkreis kollisionssicher, Soft-Delete/Migration 0006,
   mutierende Aktionen nur per POST, BT-130-unitCode statt immer H87,
   `clone_position`-long_text-Verlust, exakter Zahlungsabgleich),
   `services.py` → Package mit 12 Modulen, Fat Views entschlackt.
2. **Design Web-UI:** Emoji → Inline-SVG-Sprite, Dashboard entdichtet,
   Float-Summen-Bug behoben; danach per Vergleichsseite **shadcn-Annäherung**
   gewählt und global umgesetzt (Radius 12/10/8, Gewichte 700/600, flache
   Schatten). Vergleich liegt als private Artifact-Seite „SEDO Designvergleich“
   in Mireks claude.ai-Account.
3. **Design PDFs:** erst Executive-Vereinheitlichung, dann auf Mireks Wunsch
   **schlichter Briefbogen, komplett monochrom** für alle vier Dokumente
   (Rechnung/Angebot/Mahnung/Kassenbuch). Blau-Akzent zuschaltbar über
   `core.PDF_ACCENT` + `services/pdf_chrome.ACCENT` (synchron halten).
4. **KI-Ausbau** (Vier Recherche-Vorschläge bewertet; Ranking umgesetzt):
   - **Lokales Whisper-Diktat** (faster-whisper auf eigenem Server,
     Fachvokabular-initial_prompt, Audio wird gelöscht; automatischer
     Browser-Fallback bei 503/`stt_unavailable`), clientseitige
     Zahlen-Normalisierung („dreihundertfünfzig Euro“→„350 €“),
     Lade-Skeletons, dauerhafte KI-Markierung (amber/blau) mit Legende.
   - **Magic-Tone:** ✨-Formulieren-Knopf an jedem Leistungstext (Angebot +
     Rechnung, MutationObserver für neue Zeilen) + **KI-Begleittext** für den
     Rechnungsversand (Kontext serverseitig, NUR Leistungsdaten – per Test
     bewiesen; `invoice_email` mit optionalem body-Override).
   - **Plausibilitäts-Check** im Prüfen-Schritt: lokal-deterministisch
     (Preis-Ausreißer gegen Kundenhistorie ≥25 %/≥5 € inkl.
     Zahlendreher-Erkennung 85↔58 mit Belegangabe, Dubletten, Nullwerte;
     läuft automatisch) + KI-Check per Knopf (nur Positionsdaten, Katalog als
     Kontext, max. 6 Befunde). Live bewiesen: 85 € Historie → 58 € → Warnung.
   - **Verworfen:** KI-Preis-/Rabatt-Vorschläge (LLM würde Statistiken
     erfinden). **Zurückgestellt:** Voice-to-Action. **Abgelehnt:** Whisper-
     Cloud, Kundendaten an die KI.
5. **Sicherheitspaket:** `ensure_admin` erzeugt **Zufallspasswort** (einmalig
   angezeigt; warnt bei altem sedo1234), Security-Status-Check, **Backups ohne
   Klartext-Secrets** (DB-Kopie bereinigt ai_api_key/smtp_password),
   **.env wird jetzt wirklich geladen** (eigener Loader in settings).
6. **Ordnungspaket:** kontext.md aktualisiert, Test-MEDIA_ROOT isoliert,
   **deutsche Tausenderpunkte im Web-UI** (USE_THOUSAND_SEPARATOR; 10 Stellen
   mit |unlocalize abgesichert – sonst bricht z. B. Batch-Mahnung ab pk 1000),
   ZIP gepackt. **Duplikat-Ordner gelöscht** (Freigabe Mirek, ~645 MB) –
   ABER: darin lagen Dev-DBs vom 26.07. mit **44 echten Kundendatensätzen** →
   gesichert in `Sicherung_Duplikat-Datenbanken_2026-08-14.zip` (Projektordner,
   inkl. LIESMICH mit Wiederherstellungsanleitung).
7. **Desktop-Paket** (statt .exe – bewusst abgelehnt: 2 OS, Antivirus-Fehlalarme,
   Update-Bremse): **SEDO.app** (Mac, mit Icon, Erststart-Passwort-Dialog,
   Chrome/Edge `--app`-Fenster; auf dem Mac end-to-end getestet),
   **SEDO.vbs** + `DESKTOP_VERKNUEPFUNG_ERSTELLEN.bat` (Windows, ungetestet!),
   `SEDO_BEENDEN.command/.bat`, README neu als Endnutzer-Anleitung.
8. **Externer Static-Review von Mirek** → Nachbesserungen (Abschnitt 15 im
   Bericht): **Django-Pin-Fehler bestätigt und behoben** (meine Testumgebung
   hatte ungepinnt 6.1; jetzt alles unter 6.0.8 verifiziert, Migration 0006
   regeneriert), **Aareon-Import transaktional** (Datenverlustrisiko weg,
   per Test bewiesen), ALLOWED_HOSTS ohne Wildcard bei DEBUG=0, SECRET_KEY
   ohne Code-Fallback (persistierter Zufallsschlüssel), Env-Overrides
   `SEDO_AI_API_KEY`/`SEDO_SMTP_PASSWORD`, GWG-/Abo-Lauf + Storno +
   Angebot→Rechnung **atomar** (keine Teilrechnungen), Logging in breiten
   Handlern, Berichts-Header korrigiert (121→165).

## 3. Kritische Fallen für die Fortsetzung (Kurzfassung)

- **Rückwärtsrelationen** (`invoice.payments`, `customer.invoices`) laufen über
  `all_objects` → soft-gelöschte Datensätze IMMER explizit filtern
  (`deleted_at__isnull=True`). Zweimal passiert.
- **Nummernvergabe** nie direkt – nur `create_numbered_invoice()` /
  `save_with_unique_number()`.
- **Zahlen in value=/JS-Kontexten** brauchen `|unlocalize` (Tausenderpunkte!).
- `<input type="number">` verwirft deutsche Kommawerte stillschweigend.
- Testumgebungen **exakt nach requirements.txt** aufsetzen (der 6.1-Fehler).
- PDF-Akzent: `PDF_ACCENT` (core) und `ACCENT` (pdf_chrome) synchron halten.
- Im v3.21-Ordner liegt ein **.git-Repo** (nicht von Claude; vermutlich aus
  Mireks Prüfung, 14.08. 19:07) – unangetastet gelassen, aus dem ZIP
  ausgeschlossen. Bei Neupacken: `-x "*/.git/*"`.

## 4. Offene Punkte (Priorität, Details in MEMORY_SEDO.md)

1. **Produktionsstart:** SEDO.app/Startskript ausführen (installiert Pakete;
   erstes Diktat lädt Whisper-Modell ~460 MB), neues Admin-Passwort notieren,
   `.env` nach `.env.example`, Claude-API-Key eintragen. Dabei entscheiden:
   **44 Kunden aus der Sicherung übernehmen?** (DB-Datei einlegen,
   Migrationen laufen automatisch.)
2. **Windows-Desktop-Starter auf Papas Rechner verifizieren** (SEDO.vbs ist
   nur geschrieben, nicht getestet – danebensitzen).
3. **Tönnies/ARP-Abgleich vor Echtlauf:** BT-154 UND neue BT-130-Einheiten
   (XML weicht von alten Testrechnungen ab!). Empfehlung: Musterrechnung
   vorher durch offiziellen ZUGFeRD-Validator.
4. **Live-Test mit Papa** (Diktat, Generator, Magic-Tone, Plausi) →
   danach €/m²-Bandbreiten erfragen, 6-Schritte-Frage klären.
5. **Business-E2E-Testkette** Rechnung→PDF→ZUGFeRD→Zahlung→Mahnung→Storno
   (Empfehlung aus Mireks Review) – guter nächster Entwicklungsschritt.
6. ARP Seller-ID + GWG Receiver-ID in den Einstellungen (Pflicht vor ARP).

## 5. Arbeitsweise mit Mirek (bewährt in dieser Session)

- Deutsch, direkt, ehrlich – auch eigene Fehler klar benennen (kam gut an).
- **Plan/Optionen vorlegen → per AskUserQuestion entscheiden lassen → umsetzen.**
  Bei Design: Varianten als Vorschau/Vergleich zeigen, dann wählen lassen.
- Nichts löschen ohne ausdrückliche Freigabe; vor dem Löschen hineinschauen
  (hat die 44 Kunden gerettet).
- Nach jeder Phase: Tests laufen lassen, Bericht/MEMORY fortschreiben.
- Datenschutz-Regel ist hart: NIE Kunden-/Objektdaten oder Audio in die Cloud.

## 6. Wichtige Orte

| Was | Wo |
|---|---|
| Aktueller Code | `SEDO_Rechnungstool_Django_v1_0_beta_3_21_0_Clean/` (+ ZIP) |
| Änderungsbericht (15 Abschnitte) | im Versionsordner |
| Projektregeln / Gedächtnis / Technik | `CLAUDE_SEDO.md` / `MEMORY_SEDO.md` / `kontext.md` |
| Gesicherte 44 Kunden (26.07.) | `Sicherung_Duplikat-Datenbanken_2026-08-14.zip` |
| Desktop-Starter | `SEDO.app`, `SEDO.vbs`, `DESKTOP_VERKNUEPFUNG_ERSTELLEN.bat`, `SEDO_BEENDEN.*` |
| Designvergleich (Web-UI) | Artifact „SEDO Designvergleich“ in Mireks claude.ai-Account |
