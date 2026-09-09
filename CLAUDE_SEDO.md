# CLAUDE.md – Projekt SEDO Rechnungstool

## PFLICHTLEKTÜRE VOR JEDER AUFGABE
1. Lies diese Datei vollständig
2. Lies `MEMORY_SEDO.md` – aktueller Projektstand und offene Aufgaben
3. Lies `kontext.md` – vollständiger technischer Projektkontext
4. Dann erst beginnen

---

## PROJEKTÜBERSICHT

**SEDO** ist ein lokal laufendes Django-Webtool zur Erstellung von ZUGFeRD-konformen E-Rechnungen für ein Gebäudereinigungsunternehmen.

Ziel: E-Rechnungen mit korrekt gesetzten BT-Feldern (Leistungsort, Abrechnungseinheit, Leistungsart-ID) erzeugen, die automatisch im Aareon/GWG-System (SAP-Kontierung) verarbeitet werden können.

---

## TECHNISCHER KONTEXT

- **Framework:** Django 5.2+, Python 3.13, SQLite
- **PDF:** ReportLab + pypdf + `qrcode[pil]` (GiroCode-QR); **STT:** `faster-whisper` (optional, lokales Diktat)
- **XML:** lxml, ZUGFeRD CII (CrossIndustryInvoice)
- **Kernlogik:** `sedo/core/rechnungslauf_core.py` (Django-frei: XML, Rechnungs-PDF, Excel-Loader)
- **Services:** seit v3.21 ein **Package** `sedo/services/` mit klarer Aufteilung:
  `numbering` (Nummernkreise, kollisionssicher/GoBD) · `calc` · `invoices` · `pdf` (Angebot/Kassenbuch) ·
  `pdf_chrome` (gemeinsamer Briefbogen) · `dunning` · `payments` · `mail` · `ai` (Claude) ·
  `transcription` (lokales Whisper) · `plausibility` (lokaler Plausi-Check) · `formatting`.
  `services/__init__.py` re-exportiert alles – `from sedo.services import …` funktioniert unverändert.
- **Views:** `sedo/views.py` – alle Django-Views inkl. AJAX-API
- **Tests:** `sedo/tests/` – **217 automatisierte Tests** (Kern, Wizard, KI, STT, Magic-Tone, Plausi, Sicherheit, Cleanup, Workflow-Sperren, Mahnstufen, Nummernkreis, Atomarität)
- **Lokal:** http://127.0.0.1:8000 | Login: admin – Passwort wird beim allerersten
  Start einmalig erzeugt/angezeigt (ändern: `python manage.py changepassword admin`)
- **Desktop-Start (seit v3.21):** Mac `SEDO.app` · Windows `SEDO.vbs` (Verknüpfung via
  `DESKTOP_VERKNUEPFUNG_ERSTELLEN.bat`) – startet Server unsichtbar und öffnet
  Chrome/Edge im `--app`-Modus als eigenes Programmfenster. Beenden:
  `SEDO_BEENDEN.command`/`.bat`. Fallback bleibt START_HIER/START_WINDOWS (sichtbar).

### Grundregeln seit v3.22 (Härtungs-Session 14.08.2026, zweiter Review)
- **Workflow-Sperren:** Storno/Korrektur/Angebot→Rechnung sind serverseitig
  idempotent (`ensure_storno_allowed`, `ensure_correction_allowed`,
  `ensure_offer_invoicing_allowed` → `WorkflowError`, Views zeigen
  `messages.error`). Neuer Invoice-Status **`korrigiert`**; pro Rechnung nur
  EIN aktives Storno bzw. EINE aktive Korrektur.
- **Mahnstufen:** `next_dunning_kind()` steuert Erinnerung → (Frist abgelaufen)
  → Mahnung; `ensure_dunning_allowed` blockiert Stufen-Duplikate;
  `send_bulk_dunning(kind='auto')` wählt je Rechnung die Stufe.
  `get_dunning_candidates()` liefert jetzt eine LISTE (mit `next_kind`).
- **Nummernvergabe seit v3.22 über `NumberRange`** (zentraler Zähler je
  Präfix/Monat, atomares F()-Update in derselben Transaktion wie die Anlage –
  `select_for_update` ist auf SQLite wirkungslos!). `next_number()` ist nur
  noch eine NICHT-verbrauchende Formular-Vorschau.
- **Datei-Erzeugung atomar:** `generate_invoice_files()` schreibt in ein
  Temp-Verzeichnis und übernimmt per `os.replace` – nie halbe Dateien.
- **GWG-Monatslauf idempotent:** Unique-Constraint
  `invoice_unique_gwg_monthly_run` (Kunde+Objekt+Zeitraum, aktive Rechnungen).
- **Zahlungsimport:** `Payment.external_id` (Bank-Transaktions-ID) ist der
  primäre Doubletten-Schlüssel; Regex nur noch `(?:RE|AG)\d{6,}`.
- **Angebote haben Soft-Delete** (wie Rechnungen/Zahlungen). ⚠️ Auch hier:
  Rückwärtsrelationen über `all_objects` → explizit filtern!
- **DEBUG ist standardmäßig AUS**; WhiteNoise liefert Statics, Medien über
  eigene Route in `config/urls.py`. Entwicklung: `DJANGO_DEBUG=1` setzen.

### Grundregeln seit v3.21 (Code-Review-Session 14.08.2026)
- **GoBD-Soft-Delete:** `Invoice`/`Payment` werden nie hart gelöscht (`deleted_at`,
  `SoftDeleteModel`, Migration 0006). Standard-Manager blendet aus; Vollzugriff `all_objects`.
  ⚠️ Rückwärtsrelationen (z. B. `invoice.payments`) laufen über `all_objects` –
  in Views/Templates explizit `deleted_at__isnull=True` filtern!
- **Nummernkreise kollisionssicher:** nie `Invoice.objects.create(number=next_number(...))`
  direkt – immer `create_numbered_invoice()` / `save_with_unique_number()` (Transaktion + Retry).
  Nummern werden nach Soft-Delete NICHT wiederverwendet.
- **Mutierende Aktionen nur per POST** (`@require_POST` + CSRF-Formulare): E-Mail-Versand,
  Storno, Mahnung, Statuswechsel, Datei-Neuerzeugung, Angebot→Rechnung.
- **BT-130 (Mengeneinheit):** `unece_unit_code()` mappt Einheiten (m²→MTK, Monat→MON,
  Std.→HUR, pauschal→C62 …) – vorher stand immer H87/Stück in der XML.
  ⚠️ Wie BT-154 vor Echteinsatz mit Tönnies/ARP prüfen.
- **UI-Icons:** Inline-SVG-Sprite in `base.html` (`<use href="#i-…">`), keine Emoji.
- **Design-Tokens (shadcn-Annäherung):** Radius 12/10/8 px, Schriftgewichte 700/600,
  flache Schatten – zentral in `app.css`.

### Neue Services (seit v3.13)
- `send_email_smtp()` – SMTP-Direktversand
- `get_smtp_config()` / `smtp_is_configured()` – SMTP-Konfiguration aus AppSettings
- `get_dunning_candidates(days=14)` – Mahnkandidaten-Erkennung
- `send_bulk_dunning(ids, kind)` – Batch-Mahnung
- `customer_data_api(request, pk)` – AJAX-Endpoint für Kundendaten + Objekte

### KI-Angebots-Generator (seit v3.18, Claude/Anthropic)
- **Paradigma:** grobe Ansage → vollständiges Angebot. Die KI DARF ergänzen: Positionen
  logisch zerlegen, Gesamtpreise aufteilen, fehlende Preise marktüblich schätzen,
  branchenübliche Annahmen treffen – alles wird markiert (`preis_art`, Annahmen-Liste).
- `generate_offer_from_text()` – Hauptfunktion in `services/ai.py` (urllib, Messages API
  `/v1/messages`, erzwungener Tool-Use; `AIError` mit deutschen Meldungen)
- `normalize_ai_positions()` – Validierung/Plausi (Aufteilungs-Summenkontrolle,
  €/m²-Bandbreite, Einheiten-Normalisierung); testbar ohne Netz
- `apply_and_learn_service_catalog()` – **selbstlernender Katalog**: bekannte Leistung →
  gespeicherter Text ersetzt generierten (Konsistenz!); neue Leistung → Anlage mit
  `created_by_ai=True`. Katalog wird via `_service_catalog_prompt_block()` in den
  System-Prompt eingebettet.
- Modelle: `OfferItem.long_text` (Leistungsbeschreibung, im PDF unter dem Titel) +
  `ServiceCatalogItem` (name unique, long_text, default_unit, created_by_ai)
- View `ai_generate_offer` → `/api/ki/angebots-entwurf/` (Login+CSRF, nur POST);
  Katalog-CRUD unter `/leistungskatalog/` (eigene Nav-Seite)
- **Spracherkennung seit v3.21 zweistufig:** bevorzugt Aufnahme (MediaRecorder) →
  **lokales Whisper** auf dem eigenen Server (`/api/ki/transkription/`,
  `services/transcription.py`, faster-whisper mit Fachvokabular-`initial_prompt`,
  Audio wird nach Transkription gelöscht). Ist faster-whisper nicht installiert
  (503 + `stt_unavailable`), fällt `offer_ai.js` automatisch auf die
  Browser-Spracherkennung (Web Speech API) zurück. In beiden Fällen: kein
  Cloud-Audio – nur Text geht an Claude. Diktat wird clientseitig normalisiert
  („dreihundertfünfzig Euro“ → „350 €“, „neunzehn Prozent“ → „19 %“).
- AppSetting-Keys: `ai_api_key` (Claude-Key), `ai_text_model` (Standard: `claude-sonnet-5`)
- **Datenschutz-Regel:** In KI-Aufrufe kommen NUR Leistungsdaten – niemals Kunden-/Objektdaten
- Seit v3.20: `InvoiceItem.long_text` und `RecurringInvoiceItem.long_text` (Migration 0005).
  Leistungsbeschreibung geht ins Rechnungs-PDF und als **BT-154**
  (`SpecifiedTradeProduct/Description`) in die ZUGFeRD-XML – nur wenn befüllt.
  Übernahme bei Angebot→Rechnung, Korrektur, Storno und Abo-Vorlagen.
  ⚠️ BT-154 ist bei Aareon/ARP noch nicht erprobt – vor Echteinsatz mit Tönnies prüfen.
- Seit v3.20: Kalkulationsangaben (Arbeiter/Stunden/Tage/Stundensätze) sind
  Prompt-Regel 8 – sie dienen nur der Preisfindung und dürfen nirgends erscheinen.
  Ausnahme: ausdrückliche Stundenabrechnung („auf Stundenbasis").
- ⚠️ **Datumsfelder:** `<input type="date">` braucht ISO (`%Y-%m-%d`). Durch
  `LANGUAGE_CODE='de-de'` rendert Django sonst `26.07.2026` und der Browser
  verwirft den Wert. Alle DateInput-Widgets deshalb mit `format='%Y-%m-%d'` –
  bei neuen Datumsfeldern unbedingt mitgeben!

### KI-Ausbau seit v3.21 (Magic-Tone + Plausibilitäts-Check)
- **Magic-Tone** (`magic_tone.js` + `services/ai.py`):
  – „✨ Formulieren“-Knopf an jedem `long_text`-Feld (Angebot + Rechnung, auch
    dynamische Zeilen via MutationObserver): lose Ansage → professionelle
    Leistungsbeschreibung (`/api/ki/text-veredeln/`, `polish_service_text()`).
    Prompt entfernt Namen/Adressen, erfindet nichts, weist keine Kalkulation aus.
  – **KI-Begleittext** auf der Rechnungsseite (`/api/ki/begleittext/`,
    `generate_cover_letter_text()`): Kontext wird serverseitig gebaut
    (`_invoice_ai_context` – NUR Nummer/Betrag/Fälligkeit/Zeitraum/Positionstitel,
    per Test abgesichert). `invoice_email` akzeptiert optionalen `body`-Override.
- **Plausibilitäts-Check** im Prüfen-Schritt beider Formulare (`plausibility.js`,
  `/api/ki/plausibilitaet/`):
  – **Lokal, automatisch** (`services/plausibility.py`, ohne KI): Preis-Ausreißer
    gegen die eigene Kundenhistorie (≥ 25 % und ≥ 5 €, mit Zahlendreher-Erkennung
    85↔58 und Belegangabe), Dubletten, Nullwerte. Kundendaten bleiben auf dem Server.
  – **KI-Check per Knopf** (`ai_review_positions()`): nur Leistungsdaten; prüft
    Überschneidungen, unplausible Preis/Einheiten-Kombis, fehlende Begleitpositionen
    (Katalog als Kontext), max. 6 Befunde, keine erfundenen Marktdaten.
- **KI-Markierungen:** amber = geschätzt/aufgeteilt/prüfen (Preisfeld hervorgehoben),
  blau = aus Diktat – bleiben bis zur Bearbeitung, Legende über der Positionsliste.
- **Roadmap-Entscheid:** Voice-to-Action zurückgestellt; dynamische
  Preis-/Rabatt-Vorschläge verworfen (zu wenig Datenbasis → LLM würde Statistiken
  erfinden; Datenschutzkonflikt).

### PDF-Design seit v3.21: schlichter Briefbogen (monochrom)
- Alle vier Dokumente (Rechnung, Angebot, Mahnung, Kassenbuch) im reduzierten
  Briefbogen-Stil: weißer Grund, Haarlinien, kleines Logo, viel Weißraum –
  keine Navy-Bänder/Farbboxen mehr. GiroCode, ENTWURF-Wasserzeichen,
  Storno-Texte und Leistungsbeschreibungen bleiben erhalten.
- **Komplett monochrom** (Mireks Wahl). Dezenter Blau-Akzent (Titel/Gesamtbetrag)
  zuschaltbar über `core.PDF_ACCENT` + `services/pdf_chrome.ACCENT` (synchron halten!).
- Gemeinsamer Briefbogen der Zusatz-PDFs: `services/pdf_chrome.py`.

### Schritt-Assistent (seit v3.19, Angebot + Rechnung)
- `sedo/static/sedo/wizard.js` – abhängigkeitsfreies Modul, global in `base.html`
  eingebunden, wirkt nur auf Seiten mit `[data-wizard]`
- Markup-Vertrag: `[data-wizard]` (optional `data-wizard-unlocked`) →
  `[data-wizard-nav]` + mehrere `<section data-wizard-step data-step-label="…">` +
  Leiste mit `[data-wizard-prev] [data-wizard-progress] [data-wizard-next] [data-wizard-final]`
- Angebot = 6 Schritte (Kunde · KI-Assistent · Objekt · Angebotsdaten · Positionen · Prüfen),
  Rechnung = 5 Schritte (Empfänger · Leistungsort · Rechnungsdaten · Positionen · Prüfen)
- **Fehler-Automatik:** findet das Modul beim Laden ein `.errorlist`, öffnet es diesen
  Schritt und gibt alle Schritte frei. Deshalb müssen ALLE Felder ihre `.errors`
  rendern – sonst wäre ein Fehler unsichtbar. Beim Ergänzen von Feldern beachten!
- Enter in Textfeldern/Selects = „Weiter“, nicht Absenden (Textarea bleibt unverändert)
- API für Seitenskripte: `window.sedoWizard.goTo('step-positionen')` (nutzt `offer_ai.js`
  nach erfolgreicher KI-Generierung); Event `wizard:step` mit `detail.id`
- **Ohne JavaScript** sind alle Schritte sichtbar (Fallback wie vor v3.19) –
  serverseitig darf kein Schritt `hidden` gesetzt bekommen
- Keine Änderung an Modellen, Migrationen, URLs oder Formularen

---

## ARBEITSREGELN FÜR DIESES PROJEKT

### Vor jeder Änderung
- Immer zuerst betroffene Datei lesen, bevor du Änderungen vorschlägst
- Niemals mehrere große Dateien gleichzeitig ändern
- Plan vorlegen → Freigabe abwarten → dann umsetzen

### Bei Code-Änderungen
- Änderungen immer mit vorher/nachher zeigen
- Auf Versionsnummer hinweisen wenn Änderung releasebereit ist
- Kommentare im Code auf Deutsch

### Nach jeder Session
- `MEMORY_SEDO.md` aktualisieren mit erledigten Aufgaben
- Neue offene Punkte eintragen
- Neue Erkenntnisse unter „WICHTIGE ERKENNTNISSE" ergänzen

---

## ⚠️ OFFENE KONFIGURATION (vor erstem ARP-Test zwingend!)

1. **Einstellungen → ARP Seller-ID** → ARP-Identifikationsnummer von SEDO eintragen (von Aareon-Support)
2. **Kunden GWG → Feld „Aareon/ARP Rechnungsempfänger-ID"** → GWG-Portal-ID eintragen
3. **`pip install -r requirements.txt`** auf dem Produktionssystem ausführen
   (enthält jetzt `qrcode[pil]` für GiroCode und `faster-whisper` für das lokale
   Diktat; das erste Diktat lädt das Whisper-Modell „small“ (~460 MB) einmalig
   herunter – danach offline. Ohne faster-whisper greift automatisch die
   Browser-Spracherkennung.)
4. **Einstellungen → KI-Assistent: Claude-API-Key** eintragen (console.anthropic.com) –
   nötig für Angebots-Generator, Magic-Tone, Begleittext und KI-Check.

Ohne Punkt 1+2 werden Rechnungen von ARP abgelehnt (Pflichtfelder BT-29 und BT-46).
⚠️ **Vor Echteinsatz mit Tönnies prüfen:** BT-154 (Leistungsbeschreibung) UND die
seit v3.21 korrekten BT-130-Mengeneinheiten (m²→MTK statt immer H87) – beides
verändert die XML gegenüber den bisherigen Testrechnungen.

---

## KRITISCHE WISSENSBASIS

### ZUGFeRD XML-Felder (nicht verändern ohne Rückfrage)
```xml
ShipToTradeParty/ID    = Abrechnungseinheit (= Spalte A Mapping)
ShipToTradeParty/Name  = Objektname
PostalTradeAddress     = Objektadresse (BT-75 ff.)
IncludedNote/Content   = Leistungsart-ID je Position
```

### Aareon-Mapping-Logik
- SEDO liefert: Abrechnungseinheit + Leistungsart-ID
- Aareon löst daraus auf: Sachkonto, NkSl, Wirtschaftseinheit
- SEDO muss diese Werte **nicht** selbst in die XML schreiben

### Eingangsweg bei Aareon
- Richtig: **„E-Rechnungen"** im Mareon-Portal
- Falsch: „Capture" (= OCR, liest ZUGFeRD-XML nicht aus)

---

## TON & FORMAT

- Antworten auf Deutsch
- Direkt und ohne Umschweife
- Bei Code: vollständige Blöcke, keine Auslassungen mit „..."
- Keine langen Einleitungen

---

## VERSIONIERUNG

Aktuell: **v2.0** – sauberes Release-Paket `sedo_rechnungstool_v2/` (+ ZIP),
Code-Stand = beta 3.23.1. Kompakte Historie in dessen `CHANGELOG.md`;
die ausführlichen Änderungsberichte liegen in den Archiv-Versionsordnern
(`SEDO_Rechnungstool_Django_v1_0_beta_3_23_0` usw.).
**Neues Namensformat ab jetzt:** `sedo_rechnungstool_v[MAJOR]_[MINOR]`
(klein, Unterstriche – Mireks Vorgabe 14.08.); keine VERSION_*.md mehr
ins Paket, Historie ausschließlich in CHANGELOG.md.

### Versionshistorie (kompakt)
| Version | Inhalt |
|---------|--------|
| 3.11.5 | PDF-Leistungsort Fix |
| 3.12.0 | Bug-Fixes + ARP-Compliance (ActualDelivery, Document/Name) |
| 3.13.0 | SMTP-Versand, 38 Tests, Archiv-Filter, Mahnautomat |
| 3.14.0 | Rechnungsformular: AJAX-Autofill, Live-Kalkulation |
| 3.15.0 | Toast-UI, Print-CSS, Leer-Zustände, PDF-Redesign initial |
| 3.16.0 | PDF Executive-Design (Navy, Logo, GiroCode, Wasserzeichen) |
| 3.17.0 | KI-Angebotsassistent: Positionen per Diktat (OpenAI), 66 Tests – ersetzt durch 3.17.1 |
| 3.17.1 | KI-Assistent auf Claude umgestellt, Diktat via Browser-Spracherkennung, 64 Tests |
| 3.18.0 | KI-Angebots-Generator: Ansage → volles Angebot, Leistungstexte, selbstlernender Katalog, Aufnahme-Anzeige, Migration 0004, 69 Tests |
| 3.19.0 | UI Teil 1: Angebot (6) + Rechnung (5) als Schritt-Assistent, Fehler-Automatik, Zusammenfassung, Fix Positionsnummern + Aufnahme-Anzeige, 94 Tests |
| 3.20.0 | Kunden-Autofill im Angebot, KI-Schritt nach Angebotsdaten, Fix Datumsfelder (ISO), Kalkulationsangaben nicht ausweisen, Leistungsbeschreibung in Rechnungen + BT-154, Migration 0005, 108 Tests |
| 3.21.0 | Code-Review-Großsession: GoBD-Bugfixes (Nummernkreis atomar, Soft-Delete/Migration 0006, POST-only, BT-130-unitCode, long_text im Excel-Lauf), services→Package, SVG-Icons statt Emoji, shadcn-Design-Tokens, PDFs schlicht monochrom, lokales Whisper-Diktat (faster-whisper, Fallback Browser), Magic-Tone (Formulieren + Begleittext), Plausibilitäts-Check (lokal + KI), Sicherheitspaket (Zufallspasswort, Backup ohne Secrets, .env-Loader), 158 Tests |
| 3.22.0 | Härtung nach zweitem externem Review: Workflow-Sperren (Storno/Korrektur/Angebot→Rechnung idempotent, Status „korrigiert“), Mahnstufen-Modell (Erinnerung→Mahnung mit Fristen, Doppel-Sperren, Bulk-Auto), Cross-Customer-Sperre im Monatsversand, Zahlungsimport mit Bank-Transaktions-ID, Datei-Erzeugung atomar (Temp+replace), GWG-Monatslauf-Unique-Constraint, zentrale NumberRange-Nummernkreise, Angebots-Soft-Delete, DEBUG=0-Default + WhiteNoise + Media-Route, Migrationen 0007–0011, 217 Tests |
