# Änderungsbericht – SEDO Rechnungstool

**Version:** v1.0 beta 3.21.0 (Code-Review, Bugfixes & Aufräumung)
**Basis:** v1.0 beta 3.20.0
**Datum:** 14.08.2026
**Umfang:** Bugfixes + „Knigge"-Aufräumung + Modularisierung + vier freigegebene Verhaltensänderungen + **Design-Feinschliff (Web-UI & PDFs)**
**Status:** ✅ `manage.py check` sauber · **165 Tests grün** (vorher 108; die Zahl wuchs im
Sessionverlauf – die Abschnitte nennen den jeweiligen Zwischenstand) · alle Seiten rendern (HTTP 200)
**Verifiziert mit:** Django 6.0.8 (requirements-konform `>=5.2,<6.1`), Python 3.14, inkl. installiertem faster-whisper

Das Original (`SEDO_Rechnungstool_Django_v1_0_beta_3_20_0`) wurde **nicht verändert**.
Diese Fassung liegt in einem eigenen Ordner.

---

## 1. Kurzfassung

Das Projekt lief funktional bereits, hatte aber mehrere teils **compliance-/GoBD-relevante Bugs**
und deutlichen Aufräumbedarf. Alles Kritische ist behoben, der Code ist sauber modularisiert und
durch neue Tests abgesichert.

**Zahlen:** `services.py` (1041 Zeilen) → Package `services/` mit 9 fokussierten Modulen ·
13 neue Tests · 0 fehlende Migrationen · Migration `0006` (Soft-Delete).

---

## 2. Behobene Bugs (kritisch → mittel)

### 2.1 Nummernkreis nicht kollisionssicher (GoBD)
Bei parallelen Anfragen konnten **doppelte Rechnungsnummern** entstehen; im GWG-Lauf wurden
zudem Nummern „verbrannt" (Rechnung angelegt → wieder gelöscht → Lücke).
- **Neu:** `services/numbering.py` mit `create_numbered_invoice()` / `save_with_unique_number()` –
  Nummernvergabe + Anlage laufen in einer Transaktion und werden bei UNIQUE-Kollision mit frischer
  Nummer wiederholt.
- `next_number()` zählt jetzt über `all_objects` (inkl. soft-gelöschter Rechnungen) → eine Nummer
  wird nach Storno/Löschung **nie wiederverwendet**.
- GWG-Monatslauf: Positionen werden **erst geprüft**, eine Nummer wird nur vergeben, wenn es echte
  Positionen gibt → keine verbrannten Nummern mehr.
- Nebenbei behoben: `next_number` zählte für andere Präfixe als `RE`/`AG` nie hoch (Ternary-Logikfehler).

### 2.2 `clone_position` verlor `long_text` (BT-154)
Im Excel-Massenlauf ging die Leistungsbeschreibung still verloren, weil `clone_position` nur 11 von
12 Feldern kopierte.
```python
# vorher: ... getattr(p, "sachkonto", "") )   # long_text fehlt
# nachher: ... getattr(p, "sachkonto", ""), getattr(p, "long_text", "") )
```

### 2.3 ZUGFeRD-Menge immer `unitCode="H87"` (Stück)
Die XML meldete für **jede** Position „Stück", auch bei m²/Monat/Std. → EN-16931-Konformitätsfehler.
- **Neu:** `unece_unit_code()` bildet Einheiten auf UN/ECE-Rec-20-Codes ab
  (m²→`MTK`, Monat→`MON`, Std.→`HUR`, Stk.→`H87`, pauschal→`C62` …). Unbekannt → `H87` (bleibt gültig).
- ⚠️ **ARP-Hinweis:** Diese Änderung verändert die XML-Ausgabe. Laut Projektdoku ist die
  Mengeneinheit bei Aareon/ARP **vor Echteinsatz mit Tönnies zu prüfen**.

### 2.4 Fehlende Fehlerbehandlung / Pflichtfelder
- `D()` (Zahl-Parser) wirft bei unlesbaren Werten jetzt eine **klare `ValueError`** statt einer
  kryptischen `InvalidOperation`, die den ganzen Lauf abbrach.
- `build_xml` wirft bei **leerer Rechnungsnummer** eine klare `ValueError` (BT-1 ist Pflicht) statt
  strukturell ungültiges XML zu erzeugen.
- „Aktiv"-Filter im Excel-Loader ist robust gegen `1.0` (float) und Leerzeichen.
- SMTP-Port: nicht-numerischer Wert fällt sauber auf 587 zurück (vorher unbehandelter `ValueError`).
- `customer_import`: kein `IndexError` mehr bei leerer CSV.
- `cashbook_pdf`: prüft, ob die PDF existiert, statt bei `open()` mit 500 zu scheitern.

### 2.5 Unscharfer Zahlungsabgleich (Substring)
`RE…0001` passte fälschlich auf `RE…00010`, und es wurde über **alle** Rechnungen iteriert (N+1).
- **Neu:** `_match_invoice()` extrahiert Rechnungsnummern per Regex und schlägt sie in **einer**
  Abfrage exakt nach.

### 2.6 N+1 im GWG-Monatslauf
Mapping-Lookups liefen pro Einheit × Leistung als Einzelquery. Jetzt einmal als Dict indexiert.

---

## 3. Freigegebene Verhaltensänderungen

### 3.1 Mutierende Aktionen nur noch per POST (Sicherheit)
`invoice_email` verschickte bei konfiguriertem SMTP eine **echte E-Mail** per einfachem Link-Aufruf
(GET, ohne CSRF). Betroffen außerdem: Storno, Dateien-Neuerzeugung, Mahnung/Erinnerung, Angebots-PDF,
Angebotsstatus, Angebot→Rechnung.
- Alle diese Views haben jetzt `@require_POST`.
- Die zugehörigen Buttons in `invoice_detail`, `offer_detail`, `invoice_list`, `open_items` sind auf
  **POST-Formulare mit CSRF-Token** umgestellt (Optik unverändert). Storno zusätzlich mit Rückfrage.

### 3.2 Kein hartes Löschen (GoBD-Soft-Delete)
`Invoice` und `Payment` werden nicht mehr aus der DB entfernt, sondern erhalten `deleted_at` und
verschwinden aus den Standard-Ansichten (Manager). Vollzugriff über `all_objects`.
- **Migration 0006**, `SoftDeleteModel`-Basisklasse, `soft_delete()`.
- Nummernkreis bleibt lückenlos; `paid_amount` ignoriert soft-gelöschte Zahlungen.
- Buttons/Meldungen heißen jetzt „ausblenden" (Datensatz bleibt erhalten).

### 3.3 ZUGFeRD-Einheit korrigiert
Siehe 2.3 (mit ARP-Vorbehalt).

---

## 4. „Knigge für Programmierer" – Aufräumung

- **Tote Importe entfernt** (`HttpResponseRedirect`, `InvalidOperation`, `call_command`, `File`,
  `ContentFile`, `FileResponse`-Doppelimport, `DunningDocument` in views); lokale Importe nach oben.
- **PEP8:** Mehrfach-Statements per Semikolon aufgelöst; `print()`-Debugausgabe blieb bewusst nur im
  eigenständigen CLI-`main()`.
- **DRY:** E-Mail-Betreff/-Text (4× dupliziert) → `invoice_email_subject_body()`;
  Positions-Normalisierung (4×) → `_normalize_item_positions()`; `recalc_invoice/recalc_offer` teilen
  einen Helfer; doppelte Monatsnamen-Dicts → eine Konstante `MONTHS_DE`; `_euro` zentral.
- **Magic Numbers** benannt (`DEFAULT_PAYMENT_DAYS`, `MAX_OPEN_ITEMS`, `MAX_PAYMENTS_LIST`,
  `DUNNING_GRACE_DAYS`, `SMTP_TIMEOUT_SECONDS`, KI-Limits).
- **Docstrings** an den zentralen Funktionen ergänzt; veralteter Datei-Header modernisiert.
- **Zu breite `except Exception`** an vielen Stellen auf konkrete Ausnahmen verengt
  (`ValueError`, `InvalidOperation`, …). Bewusst breit blieben nur die
  „darf-die-App-nicht-abschießen"-Stellen (Audit, Backup, KI-Fehlerübersetzung).
- **Robustere Dataclass:** `document_title`, `document_type_code`, `is_draft`, `intro_text`,
  `reference_invoice_no` sind jetzt echte `InvoiceRow`-Felder statt loser `setattr` – Tippfehler
  fallen nicht mehr still auf einen Default.
- `requirements.txt`: fehlendes `qrcode[pil]` (GiroCode-QR) ergänzt.

**Bewusst NICHT angefasst** (hohes Regressionsrisiko, kein Testschutz, rein optisch):
die große `create_pdf`-Layoutfunktion – sie ist bereits klar in Unterfunktionen gegliedert.

---

## 5. Struktur / Modularisierung

`sedo/services.py` (1041 Zeilen, 5 Themen gemischt) → Package `sedo/services/`:

| Modul | Inhalt |
|-------|--------|
| `numbering.py` | Nummernkreise (kollisionssicher, GoBD) |
| `calc.py` | Betrags-/Statusberechnung |
| `invoices.py` | Rechnungslogik (Kern-Mapping, Dateien, Storno, Angebot→Rechnung) |
| `pdf.py` | Angebots-/Kassenbuch-PDF |
| `pdf_chrome.py` | Gemeinsamer Navy-Briefkopf/-Fuß der Zusatz-PDFs |
| `dunning.py` | Mahnwesen |
| `payments.py` | Bank-JSON-Import + Zuordnung |
| `mail.py` | E-Mail-Entwürfe + SMTP-Versand |
| `ai.py` | KI-Angebotsassistent (Claude) |
| `formatting.py` | Formatierungshelfer |

`services/__init__.py` re-exportiert die komplette öffentliche API → **bestehende Aufrufe
`from sedo.services import …` laufen unverändert**. In `views.py` wurde Geschäftslogik weiter in die
Services gezogen (Nummernvergabe, Positions-Normalisierung, E-Mail-Bausteine).

---

## 6. Tests

Neue Datei `sedo/tests/test_cleanup_v3_21.py` (13 Tests) sichert genau die vorher blinden Flecken ab:
Einheiten-Code in der XML, `long_text`-Übertragung, Pflichtfeld BT-1, `D()`-Fehlerfall, GoBD-Soft-Delete
(inkl. `paid_amount` und Nummern-Nichtwiederverwendung), exakter Zahlungsabgleich, POST-Pflicht (405 bei GET).

Bestehende KI-Tests: Patch-Ziel an das neue Package angepasst
(`sedo.services.ai._ai_generate_via_tool_use`).

```
Ran 121 tests … OK
```

---

## 7. Offene Punkte / Empfehlungen (nicht umgesetzt)

- **BT-154 & unitCode bei Aareon/ARP** vor Echteinsatz mit Tönnies verifizieren (XML-Ausgabe geändert).
- **Secrets** (Claude-Key, SMTP-Passwort) liegen weiter im Klartext in `AppSetting`. Für den
  lokalen Einzelplatzbetrieb vertretbar; bei Mehrplatz/Server verschlüsselt ablegen.
- **Keine Mandanten-/Rollenrechte:** jeder eingeloggte Nutzer sieht alle Daten. Falls Mehrbenutzer
  geplant ist, wäre eine Rechteschicht nötig.
- **Admin sieht soft-gelöschte Rechnungen nicht** (nutzt Standard-Manager). Bei Bedarf `all_objects`
  im Admin freischalten.

---

## 8. Design-Feinschliff (Web-UI & PDFs)

Ausgangslage: Die Oberfläche war bereits gut (Navy-Sidebar, KPI-Kacheln, WISO-artiger
Schritt-Assistent). Umgesetzt wurde ein **Feinschliff** ohne Bruch des Looks.

### 8.1 Web-Oberfläche
- **Einheitliches Icon-Set:** Alle Emoji-Icons (🏠👥🧾📅 …) durch ein konsistentes
  Inline-SVG-Sprite im Strichstil ersetzt (`base.html`, erbt Farbe via `currentColor`,
  sieht auf jedem Betriebssystem gleich aus). Betrifft Sidebar-Navigation, Dashboard-
  Kacheln, Schritt-Assistent (Rechnung + Angebot), Listen-Leerzustände und Seitenköpfe.
- **Dashboard entdichtet:** Die redundante „focus-strip"-Zeile (dieselben Zahlen wie die
  KPI-Kacheln) entfernt; die verschachtelte „Systemschutz"-Karte als eigene Karte neben
  „Letzte Rechnungen" gestellt (`.split`). Hinweisboxen als saubere `.notice`-Komponente
  statt Inline-Styles.
- **Anzeige-Bug behoben:** Beträge wie `5274,7000000000 €` (SQLite gibt bei `Sum()` über
  Decimal einen Float zurück) werden über einen Helfer `_sum_decimal()` korrekt als
  `5.274,70 €` angezeigt (Dashboard, Rechnungsliste, Kassenbuch, Monatsabschluss).

### 8.2 PDFs
- **Neuer gemeinsamer Briefkopf** `services/pdf_chrome.py` (Navy-Header mit Logo +
  Kontakt, Navy-Fuß mit Bank-/Steuerdaten, Absenderzeile, Info-Box-Helfer) – aus den
  Stammdaten in `core.rechnungslauf_core.SELLER`.
- **Angebot, Mahnung, Kassenbuch** komplett neu im **Executive-Design der Rechnung**
  (vorher schlichte Helvetica-Briefe): Navy-Kopf/Fuß, Info-Box, blaue Tabellenköpfe,
  alternierende Zeilen, Summen-Box. Die Leistungsbeschreibung (`long_text`) erscheint jetzt
  auch im Angebots-PDF unter der Position.
- **Konsistenz-Fix:** Der linke Navy-Akzent in Info-/Summen-/Leistungsort-Boxen überlappte
  bisher Titel/Beschriftung; jetzt ein schmaler, vertikal eingerückter Streifen – einheitlich
  in allen vier Dokumenten (Rechnung, Angebot, Mahnung, Kassenbuch).

Alle vier Dokumenttypen wurden gerendert und visuell geprüft.

### 8.3 Design-Sprache: shadcn-Annäherung übernommen
Nach direktem Vergleich (aktueller Look vs. shadcn/ui-Annäherung) wurde die **ruhigere,
knappere Variante** gewählt und global in `app.css` umgesetzt – rein über Tokens, ohne
Struktur- oder Farbänderung:
- **Eckenradius** deutlich knapper (Karten 22→12px, Buttons/Felder 14→8px; Pills bleiben rund).
- **Schriftgewichte** dezenter (Überschriften 900→700, Text/Buttons 850→600) für klarere Hierarchie.
- **Schatten** flacher/kürzer; Steuerelemente etwas kompakter.
- Navy/Blau/Orange-Palette und das Linien-Icon-Set bleiben unverändert.

Ergebnis: professionellerer, in sich stimmigerer Gesamteindruck bei erhaltenem SEDO-Charakter.
Barrierefreiheit (sichtbarer Tastatur-Fokus, semantische Statusfarben) unverändert gewahrt.

---

## 9. KI-Angebotsassistent: Lokales Whisper + UX-Ausbau

Entscheidungen: **lokale** Spracherkennung (kein Cloud-Upload), **keine** Kundenerkennung
(Datenschutzregel bleibt unangetastet), UX-Paket Pulse/Markierung/Normalisierung.

### 9.1 Lokale Whisper-Transkription (`services/transcription.py`)
- Diktat wird jetzt bevorzugt als Audio aufgenommen (MediaRecorder) und auf dem
  **eigenen Server** mit `faster-whisper` transkribiert (`/api/ki/transkription/`).
  Deutlich bessere Erkennung von Fachbegriffen und Zahlen als die Browser-Erkennung.
- **Fachvokabular-`initial_prompt`** (Unterhaltsreinigung, Netto, MwSt 19 %, m², pauschal …)
  zwingt Whisper, Zahlen/Einheiten direkt korrekt zu schreiben.
- **Datenschutz:** Audio bleibt auf dem eigenen Rechner und wird nach der Transkription
  sofort gelöscht. An Claude geht weiterhin **nur Text**.
- **Robuster Fallback:** Ist `faster-whisper` nicht installiert (Endpoint antwortet 503 +
  `stt_unavailable`), nutzt die Oberfläche automatisch die Browser-Spracherkennung wie bisher.
- ⚠️ Einrichtung: `pip install faster-whisper` (in requirements.txt ergänzt). Das erste
  Diktat lädt das Modell („small“, ~460 MB) einmalig herunter – danach läuft alles offline.
  Modellgröße per AppSetting `stt_model` änderbar (tiny/base/small/medium).

### 9.2 UX-Ausbau (`offer_ai.js`)
- **Lade-Feedback:** Shimmer-Skeleton + Puls während Transkription und Claude-Generierung
  (respektiert `prefers-reduced-motion`).
- **Dauerhafte KI-Markierung:** Positionen bleiben markiert, bis der Nutzer sie bearbeitet –
  amber = Preis geschätzt/aufgeteilt (Preisfeld zusätzlich hervorgehoben), blau = aus Diktat.
  Legende über der Positionsliste erklärt die Farben. (Vorher: 6-Sekunden-Rahmen.)
- **Zahlen-/Einheiten-Normalisierung** des Diktats: deutsche Zahlwörter → Ziffern
  („dreihundertfünfzig Euro“ → „350 €“, „neunzehn Prozent“ → „19 %“, „drei Komma fünf“ → „3,5“,
  „hundertzwanzig Quadratmeter“ → „120 m²“). Artikel („ein Angebot“) bleiben unangetastet.

### 9.3 Bewusst NICHT umgesetzt (Empfehlungs-Abgleich)
- **Whisper-Cloud-API / Deepgram:** verworfen – widerspricht der dokumentierten Regel
  „Kein Audio-Upload“; stattdessen lokales Whisper.
- **Kundenliste an die KI senden:** verworfen – widerspricht der Regel „niemals
  Kunden-/Objektdaten in KI-Aufrufe“. (Structured Output und Katalog-Kontext waren
  bereits seit v3.18 umgesetzt.)

### 9.4 Tests
Neue Datei `sedo/tests/test_transcription.py` (10 Tests): POST-only, Login-Pflicht,
503-Fallback ohne Installation, Erfolgs-/Fehlerpfade, `stt_available`-Flag im Formular.
**Gesamt: 131 Tests grün.**

---

## 10. PDF-Redesign: Schlichter Briefbogen (alle vier Dokumente)

Auf Wunsch wurde das Executive-Design (Navy-Kopf-/Fußbänder, farbige Boxen) durch einen
**deutlich schlichteren Briefbogen-Stil** ersetzt – konsequent zur shadcn-Linie des Web-UI:

- **Weißer Grund, feine Haarlinien, kleines Logo, viel Weißraum** – keine Farbflächen mehr.
- Eckdaten (Rechnungsnr., Datum, Fälligkeit …) als schlichte Beschriftung/Wert-Spalte rechts,
  ohne Box; Leistungsort als einzeilige Angabe statt farbiger Kasten.
- Tabelle: graue Versalien-Spaltenköpfe + Haarlinien statt blauer Kopfzeile und Zebra-Zeilen.
- Summenblock rechtsbündig mit einer Abschlusslinie; Fuß mit drei schmalen Spalten
  Kleingedrucktem (Firma · Bank · Steuer/Kontakt).
- **Gewählte Variante: komplett monochrom** (nur Schwarz-/Grautöne). Ein dezenter
  Blau-Akzent für Titel/Gesamtbetrag bleibt über eine Konstante zuschaltbar
  (`core.PDF_ACCENT` bzw. `pdf_chrome.ACCENT`).
- Erhalten: GiroCode-QR, ENTWURF-Wasserzeichen, Storno-Texte, Leistungsbeschreibungen,
  mehrseitige Fortsetzung; Mengenformat im Angebot an das deutsche Format angeglichen
  („1 pauschal“ statt „1.00 pauschal“).

Betroffen: `core/rechnungslauf_core.py` (Rechnung), `services/pdf_chrome.py` (gemeinsamer
Briefbogen), `services/pdf.py` (Angebot, Kassenbuch), `services/dunning.py` (Mahnung).
Alle vier Dokumente gerendert und geprüft · 131 Tests grün.

---

## 11. Magic-Tone: Profi-Text-Veredelung + KI-Begleitschreiben

Erster Schritt der beschlossenen KI-Roadmap (Rangfolge: Magic-Tone → Plausibilitäts-Check;
Voice-to-Action zurückgestellt, Preis-/Rabatt-Vorschläge verworfen).

### 11.1 „Formulieren“-Knopf an jedem Leistungstext (Angebot + Rechnung)
- Neuer Endpoint `/api/ki/text-veredeln/` + `polish_service_text()` in `services/ai.py`:
  lose/emotionale Ansage → professionelle Leistungsbeschreibung (2–4 Sätze).
- System-Prompt entfernt Namen/Adressen/Schuldzuweisungen ersatzlos, erfindet nichts,
  weist keine Kalkulationsangaben aus (konsistent zu Regel 8 des Angebots-Generators).
- `magic_tone.js`: hängt den ✨-Knopf an jedes `long_text`-Feld – per MutationObserver
  auch an nachträglich hinzugefügte Positionszeilen (Formset „+ Position“).

### 11.2 KI-Begleittext für den Rechnungsversand
- Neue Karte „E-Mail mit KI-Begleittext“ auf der Rechnungsseite: optionaler Hinweis
  (Anlass/Ton) → `/api/ki/begleittext/` erzeugt den E-Mail-Text; nach Prüfung
  „E-Mail mit diesem Text senden“ (POST an den bestehenden Versand).
- `invoice_email` akzeptiert dazu einen optionalen `body`-Parameter; ohne ihn bleibt
  der Standardtext unverändert.
- **Datenschutz:** Der Kontext wird serverseitig gebaut (`_invoice_ai_context`) und
  enthält ausschließlich Leistungsdaten (Nummer, Betrag, Fälligkeit, Zeitraum,
  Positionsbezeichnungen) – per Test abgesichert, dass Kundenname/Adresse/E-Mail
  und Objektname NICHT übertragen werden. Anrede immer neutral, keine Namen.

### 11.3 Nebenbei behoben
- Rechnungsseite zeigte in der Zahlungsliste auch soft-gelöschte Zahlungen
  (Rückwärtsrelation über `all_objects`); jetzt gefiltert – konsistent zu den Summen.

### 11.4 Tests
`sedo/tests/test_magic_tone.py` (9 Tests): POST-only, API-Key-Pflicht, Fehlerpfade (502),
Datenschutz-Kontexttest, body-Override beim Versand. **Gesamt: 140 Tests grün.**

---

## 12. Plausibilitäts-Check (Prüfen-Schritt, Angebot + Rechnung)

Zweiter Schritt der KI-Roadmap – bewusst zweigeteilt:

### 12.1 Lokale Prüfung (automatisch, ohne KI)
Läuft beim Betreten des Prüfen-Schritts automatisch, komplett auf dem eigenen Server
(`services/plausibility.py`) – Kundendaten verlassen den Rechner nicht:
- **Preis-Ausreißer je Kunde:** Abgleich gegen die eigene Rechnungshistorie (letzte
  berechnete Preise derselben Leistung; nur echte Rechnungen – kein Entwurf/Storno/
  Ausgeblendetes). Warnung ab ≥ 25 % und ≥ 5 € Abweichung, inkl. expliziter
  **Zahlendreher-Erkennung** (85 ↔ 58) und Beleg („Rechnung RE… vom …“).
- **Dubletten** innerhalb des Dokuments, **Nullpreise/-mengen**.

### 12.2 KI-Check (per Knopf, nur Leistungsdaten)
`ai_review_positions()` prüft ausschließlich Bezeichnung/Menge/Einheit/Preis auf
kaufmännische Plausibilität: Dubletten/Überschneidungen, unplausible Preis-Einheiten-
Kombinationen, fehlende typische Begleitpositionen (Leistungskatalog als Kontext),
widersprüchliche Einheiten. Max. 6 Befunde, keine erfundenen Marktdaten, leere Liste
wenn nichts auffällt („✓ KI-Check: keine Auffälligkeiten“ + Toast).

### 12.3 Integration
- Endpoint `/api/ki/plausibilitaet/` (Modi lokal/ki); `plausibility.js` hängt das
  Prüf-Panel in den Prüfen-Schritt beider Formulare, markiert betroffene Positionen
  (amber, bis zur Bearbeitung) und nutzt Skeleton/Toasts des bestehenden Systems.
- Per Test abgesichert: an die KI-Funktion geht **nur** die Positionsliste –
  die Kunden-ID wird ausschließlich für den lokalen Historien-Abgleich verwendet.

### 12.4 Tests
`sedo/tests/test_plausibility.py` (11 Tests): Ausreißer + Zahlendreher, Toleranzen,
Historie-Ausschlüsse (Storno/Entwurf), Dubletten, Nullwerte, Endpoint-Verhalten,
Datenschutz. **Gesamt: 151 Tests grün.** Live im Browser verifiziert
(Historie 85 € → Position 58 € → automatische Warnung im Prüfen-Schritt).

---

## 13. Sicherheits- und Ordnungspaket (Nachtrag)

### 13.1 Sicherheit
- **Zufallspasswort statt „sedo1234“:** `ensure_admin` erzeugt beim ersten Start ein
  Zufallspasswort (einmalig angezeigt; ändern: `manage.py changepassword admin`) und
  warnt laut, wenn ein bestehender Admin noch das alte Standard-Passwort nutzt.
  Neuer Check auf der Sicherheitsseite. Hintergrund: Startskripte binden an 0.0.0.0
  (Heimnetz) und die App ist zeitweise per Tunnel erreichbar.
- **Backups ohne Klartext-Secrets:** Die DB-Kopie im Backup-ZIP wird bereinigt –
  `ai_api_key` und `smtp_password` sind geleert (Live-DB unverändert; Hinweis in
  BACKUP_INFO.txt: nach Wiederherstellung Keys neu eintragen).
- **`.env` wird jetzt wirklich geladen:** Eigener Mini-Loader in `settings.py`
  (bereits gesetzte Umgebungsvariablen behalten Vorrang). `check --deploy` mit
  `.env` nach Beispielmuster: nur noch die zwei bewusst offenen HSTS-Hinweise.

### 13.2 Ordnung
- `kontext.md` von v3.11.5 (Mai) auf den v3.21-Stand gebracht.
- **Test-Medien isoliert:** Testläufe schreiben PDFs/XMLs jetzt in einen
  Temp-Ordner statt in den echten `media/`-Ordner.
- **Deutsche Tausenderpunkte im Web-UI** (`USE_THOUSAND_SEPARATOR`): 4.382,20 € wie
  in den PDFs. Dazu Audit aller Zahlenausgaben in URLs/JS/`value=`-Attributen –
  10 Stellen mit `|unlocalize` abgesichert (sonst würde z. B. eine Rechnungs-ID
  ab 1000 als „1.234“ gruppiert und Batch-Mahnung/JS brechen). Live verifiziert
  mit pk=1234.
- v3.21 zusätzlich als ZIP im gewohnten Namensschema gepackt.

**Tests: gesamt jetzt 158 grün** (7 neue Sicherheitstests).

---

## 14. Desktop-Paket: SEDO als „richtiges Programm“

Auf Mireks Wunsch („lieber lokale Installation als Browser-Skript“) – Architektur
bleibt (sie IST lokal), nur die Verpackung wird nativ. Bewusst **keine .exe**
(2 Nutzer × 2 Betriebssysteme, Antivirus-Fehlalarme bei PyInstaller-Bundles,
Whisper-Native-Libs, Update-Bremse).

- **Mac: `SEDO.app`** mit SEDO-Icon (icns aus dem Logo). Doppelklick: richtet
  beim ersten Mal alles ein (venv, Pakete, Migrationen; Benachrichtigung läuft),
  zeigt das einmalige Admin-Passwort als Dialog, startet den Server unsichtbar
  und öffnet Chrome/Edge im `--app`-Modus → eigenes Programmfenster ohne
  Browser-Leisten. Läuft der Server schon, öffnet sich nur das Fenster.
- **Windows: `SEDO.vbs`** (unsichtbarer Starter, gleiche Logik; Erststart läuft
  sichtbar über START_WINDOWS.bat, damit das Passwort lesbar ist) +
  `DESKTOP_VERKNUEPFUNG_ERSTELLEN.bat` → Desktop-Verknüpfung „SEDO
  Rechnungstool“ mit Logo (`sedo/assets/sedo.ico`).
- **Stopp-Skripte** `SEDO_BEENDEN.command`/`.bat` (beenden den Server auf Port
  8000); Fenster schließen lässt den Server bewusst weiterlaufen → Sofortstart.
- README als Endnutzer-Anleitung neu geschrieben (alte sedo1234-Angabe entfernt).
- **Getestet auf dem Mac:** SEDO.app-Start → Server hoch → Chrome-App-Fenster
  mit `--app`-Flag verifiziert → Stopp-Skript beendet den Server.
  ⚠️ Die Windows-Seite (VBS/PowerShell) ist sorgfältig geschrieben, aber hier
  nicht ausführbar – beim ersten Start auf Papas Rechner danebensitzen.

---

## 15. Nachbesserungen aus dem externen Static-Review (14.08.2026)

Ein externer statischer Review des ZIP fand berechtigte Punkte – alle 🔴/🟠 behoben:

### 15.1 🔴 Django-Versionskonflikt (bestätigt – Fehler unserer Testumgebung)
Die Migration 0006 trug „Generated by Django 6.1“, obwohl requirements
`>=5.2,<6.1` verlangt: Unsere Verifikationsumgebung hatte Django ohne
Versions-Pin installiert. **Fix:** frische Umgebung exakt nach requirements.txt
(→ Django 6.0.8), Migration 0006 darunter neu erzeugt (inhaltlich identisch),
**gesamte Suite unter 6.0.8 erneut grün**, `makemigrations --check` sauber.
Der Bericht nennt jetzt die verifizierte Version.

### 15.2 🔴 Aareon-Import transaktional (Datenverlustrisiko beseitigt)
`AareonMapping.objects.all().delete()` lief vor dem Import; ein Fehler mitten
im Einlesen konnte den alten Bestand kosten. Jetzt laufen Löschen + Neuanlage
in **einer Transaktion** – jeder Fehler rollt vollständig zurück
(Pflichtspalten-Validierung weiterhin VOR dem Löschen). Per Test bewiesen:
simulierter DB-Fehler in Zeile 2 → alter Bestand vollständig intakt.

### 15.3 🟠 Produktions-Settings gehärtet
- `ALLOWED_HOSTS`: Wildcard `*` nur noch im DEBUG-/Einzelplatzmodus; bei
  `DJANGO_DEBUG=0` müssen Hosts explizit gesetzt werden (per .env).
- `SECRET_KEY`: kein fest einkodierter Fallback mehr – Umgebungsvariable, sonst
  einmalig erzeugter, lokal persistierter Schlüssel (`data/.secret_key`, 0600).
- Secrets optional ganz aus der DB heraus: `SEDO_AI_API_KEY` und
  `SEDO_SMTP_PASSWORD` als Env/.env haben Vorrang vor den AppSettings.

### 15.4 🟡 Läufe atomar, Logging ergänzt
- GWG-Monatslauf: jede Einheit in einer Transaktion – schlägt auch die PDF-/
  XML-Erzeugung fehl, wird die komplette Rechnung zurückgerollt (keine
  Teilrechnungen; Einheit landet in der Fehlerliste).
- Abo-Monatslauf: dito je Vorlage; eine fehlgeschlagene Vorlage bricht den Lauf
  nicht mehr ab und wird gemeldet.
- Storno und Angebot→Rechnung ebenfalls atomar (per Test: PDF-Fehler → keine
  Teilrechnung, Angebotsstatus unverändert).
- Verbleibende breite `except Exception` (bewusst: Batch-Läufe, KI-Endpoints
  mit Nutzerfreundlicher Meldung) protokollieren jetzt via `logging` ('sedo').

### 15.5 Bewusst NICHT umgesetzt (dokumentierte Abwägung)
- **Nummernkreis-Tabelle mit select_for_update:** Für SQLite im
  Einzelplatz-/Kleinstbetrieb bringt `select_for_update` praktisch nichts
  (SQLite sperrt ohnehin die Datei); UNIQUE-Constraint + Transaktion + Retry
  ist hier das angemessene Muster. Bei einem Wechsel auf PostgreSQL/Mehrplatz
  wäre die Zählertabelle der richtige nächste Schritt.
- **Secrets komplett aus AppSetting entfernen:** bewusst als Komfort für den
  Einzelplatz belassen (Backups sind bereinigt, Env-Override existiert jetzt).

### 15.6 Doku-Korrektur
Die Kopfzeile dieses Berichts nannte noch „121 Tests“ (Stand zur Zeit der
ersten Abschnitte). Korrigiert; die Testzahl wuchs im Sessionverlauf
(108 → 121 → 131 → 140 → 151 → 158 → **165**).

**Tests: 165 grün unter Django 6.0.8** (7 neue Review-Fix-Tests).
