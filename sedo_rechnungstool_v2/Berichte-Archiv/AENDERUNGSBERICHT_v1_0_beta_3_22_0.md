# Änderungsbericht v1.0 beta 3.22.0 – „Härtung“ (14.08.2026)

**Anlass:** Mireks zweite, tiefe Prüfung (Geschäftslogik, Rechnungsabläufe,
Storno/Korrektur, Zahlungen, Mahnwesen, Monatsläufe, Aareon/GWG, ZUGFeRD,
Sicherheits-/Datenintegrität) mit 20 Befunden. Jeder Befund wurde zuerst gegen
den v3.21-Clean-Stand verifiziert: Die Punkte 3 (Aareon-Import), 4 (Monats-
rechnung atomar), 17 (ALLOWED_HOSTS) und 19 (Angebot→Rechnung transaktional)
waren dort bereits durch die Abschnitt-15-Nachbesserungen behoben – Mireks
Prüfstand war ein älteres Paket. Alle übrigen bestätigten Befunde behebt
diese Version.

**Ergebnis:** 165 → **217 Tests grün** (52 neue) unter Django 6.0.8 /
Python 3.14, `manage.py check --deploy` unverändert sauber (nur die zwei
bewussten HSTS-Hinweise). Migrationen 0007–0011.

---

## 1. Workflow-Sperren Storno (Befund 1)

- `create_storno()` prüft jetzt vor der Anlage (`ensure_storno_allowed`):
  Stornos von Stornos, bereits stornierte und bereits korrigierte Rechnungen
  sowie ein zweites aktives Storno werden mit verständlicher Meldung blockiert
  (`WorkflowError` → `messages.error`). Ein soft-gelöschtes Storno blockiert
  nicht (Folgebeleg-Prüfung nur über aktive Belege).
- UI: Storno-/Korrektur-Buttons werden in der Rechnungsakte ausgeblendet,
  wenn die Sperre greifen würde; die Detailseite zeigt Folgebelege
  (Storno/Korrektur) und den Bezug jetzt beidseitig verlinkt.

## 2. Angebot→Rechnung idempotent (Befund 2)

- `create_invoice_from_offer()` blockiert serverseitig, wenn
  `linked_invoice` gesetzt oder Status `in_rechnung` ist
  (`ensure_offer_invoicing_allowed`) – die UI versteckte bisher nur den Button.
- Beide Wege absichert: der Direkt-POST `/angebote/<pk>/rechnung/` UND der
  Formular-Weg `?from_offer=` (GET-Vorbefüllung wie POST).

## 3. Strenger Korrektur-Workflow (Befunde 10/11)

- Neuer Rechnungsstatus **„korrigiert“** (Migration 0007). Nach erfolgreicher
  Korrektur wird die Ursprungsrechnung markiert; Korrektur- und Storno-Sperren
  greifen dann.
- `ensure_correction_allowed`: keine Korrektur von Stornos, stornierten
  Rechnungen oder bereits korrigierten Rechnungen; nur EINE aktive Korrektur
  je Rechnung. Guards greifen am Link „Korrektur laden“, an der
  Formular-Vorbefüllung und am POST.
- Korrekturrechnung bleibt bewusst eine vollständige neue Rechnung (TypeCode
  384) mit `reference_invoice` – jetzt mit beidseitig sichtbarer Kette.
- Entwurfs-Korrekturen markieren das Original noch nicht als korrigiert,
  blockieren aber über die Folgebeleg-Sperre bereits eine zweite Korrektur.

## 4. Mahnstufen-Modell (Befunde 5/6)

- Vorher: `dunning_documents__isnull=True` – nach der ERSTEN Erinnerung fiel
  eine Rechnung für immer aus der Kandidatenliste; der Ablauf Erinnerung →
  Mahnung war unmöglich.
- Jetzt `next_dunning_kind()`: keine Mahnung → **Zahlungserinnerung**; die
  Erinnerung setzt eine neue Frist (7 Tage), nach deren Ablauf ist die
  **Mahnung** fällig; nach der Mahnung keine weitere automatische Stufe.
  Kandidaten mit offenem Betrag 0 werden übersprungen; `bezahlt/storniert/
  korrigiert/entwurf` sind nie mahnbar.
- `ensure_dunning_allowed`: dieselbe Stufe nur EINMAL (Mehrfachklick im
  manuellen Endpoint erzeugte beliebig viele Erinnerungen); Erinnerung nach
  bereits vorhandener Mahnung ist gesperrt (kein Stufen-Rückschritt).
  Manuell darf eine Mahnung weiterhin ohne vorherige Erinnerung erzeugt werden.
- `send_bulk_dunning(kind='auto')` wählt je Rechnung die richtige Stufe;
  Offene-Posten-Seite zeigt eine Spalte „Nächste Stufe“ und bietet einen
  Sammel-Button „nächste Stufe ausführen“; Dashboard-Notice entsprechend.

## 5. Cross-Customer-Sperre im Monatsversand (Befund 7)

- `send_monthly_email` bricht ab, wenn die ausgewählten Rechnungs-IDs zu
  mehreren Kunden gehören – vorher hätte ein manipulierter POST Rechnungen
  fremder Kunden in die ZIP-Mail eines Empfängers mischen können.

## 6. Zahlungsimport (Befunde 12/13)

- Neues Feld `Payment.external_id` (Migration 0008, bedingter
  Unique-Constraint): Die **Bank-Transaktions-ID** aus dem Export
  (`transaktions_id`/`transaction_id`/`id`/…) ist jetzt der primäre
  Doubletten-Schlüssel. Zwei echte Zahlungen mit identischem
  Datum/Betrag/Name/Zweck bleiben damit unterscheidbar; die alte
  Text-Heuristik greift nur noch ohne ID. Soft-gelöschte Zahlungen zählen
  als bereits importiert (kein Re-Import durch die Hintertür).
- Rechnungsnummern-Regex von `[A-Z]{2}\d{6,}` auf `(?:RE|AG)\d{6,}`
  eingeschränkt – Fremdkennzeichen wie „KD2026080001“ werden gar nicht erst
  als Kandidat extrahiert. (Die Zuordnung war schon vorher exakt gegen
  existierende Nummern; das ist zusätzliche Vorsicht.)

## 7. Datei-Atomarität (Befund 14)

- `generate_invoice_files()` erzeugt PDF, XML und ZUGFeRD-PDF jetzt komplett
  in einem Temp-Verzeichnis (auf demselben Dateisystem) und übernimmt alle
  drei erst nach Erfolg per `os.replace` in `media/generated/…`. Scheitert
  z. B. die ZUGFeRD-Einbettung, bleiben keine halben Dateien zurück (per
  Test bewiesen).

## 8. Monatslauf-Idempotenz (Befund 20)

- Bedingter Unique-Constraint `invoice_unique_gwg_monthly_run`
  (Migration 0009): pro Kunde+Objekt+Leistungszeitraum nur EINE aktive
  GWG-Monatsrechnung (stornierte/ausgeblendete ausgenommen). `skip_existing`
  bleibt der freundliche Weg; der Constraint ist der harte Schutz gegen
  doppelt/parallel gestartete Läufe – die Verletzung wird im Lauf als
  „übersprungen“ gezählt, nicht als Fehler.

## 9. Zentraler Nummernkreis (Befunde 8/9)

- Neue Tabelle **`NumberRange`** (prefix, Jahr, Monat, next_number;
  Migration 0010). Vergabe über atomares `F()`-Update **in derselben
  Transaktion wie die Beleganlage**: schlägt die Anlage fehl, rollt der
  Zähler mit zurück – keine „verbrannten“ Nummern (per Test bewiesen).
  Wichtig: `select_for_update()` wäre auf SQLite wirkungslos, deshalb das
  F()-Update.
- Seeding aus dem Altbestand (über `all_objects`, Nummern werden nach
  Soft-Delete nie wiederverwendet); hängt der Zähler nach einem DB-Import
  hinterher, wird er bei Kollision automatisch nachgezogen.
- Der Nummern-Retry greift nur noch bei ECHTEN Nummernkollisionen – andere
  Unique-Verletzungen (z. B. der Monatslauf-Schutz) gehen sofort nach oben.
- `next_number()` bleibt eine nicht-verbrauchende Vorschau für die Formulare.

## 10. Angebots-Soft-Delete (Befund 18)

- `Offer` erbt jetzt von `SoftDeleteModel` (Migration 0011): Löschen blendet
  nur aus, Angebotsnummern werden nie wiederverwendet – konsistent zu
  Rechnungen/Zahlungen (Angebote sind Geschäftsbriefe).
- ⚠️ Gleiche Falle wie bei Rechnungen: Rückwärtsrelationen
  (`customer.offers` …) laufen über `all_objects` – Kundendetail filtert
  jetzt explizit (`customer_offers` im Kontext).

## 11. Production-Defaults (Befunde 16/17)

- **DEBUG ist standardmäßig AUS** (`DJANGO_DEBUG=1` nur noch explizit für
  Entwicklung). Damit entfällt auch der ALLOWED_HOSTS-Wildcard-Default
  (der galt nur bei DEBUG).
- **WhiteNoise** liefert statische Dateien bei DEBUG=0 aus
  (`WHITENOISE_USE_FINDERS`, kein collectstatic-Schritt für den Endnutzer);
  neue Abhängigkeit in requirements.txt, wird vom Startskript mitinstalliert.
- Medien (Rechnungs-PDFs usw.) laufen über eine eigene, DEBUG-unabhängige
  Media-Route in `config/urls.py` – vorher wären alle PDF-Links bei DEBUG=0
  ins Leere gelaufen.
- Live verifiziert (runserver mit DEBUG=0): static 200, media 200, login 200.
  `check --deploy` mit Beispielwerten: unverändert nur die 2 HSTS-Hinweise.

## 12. Nicht geändert (bewusst)

- **Befund 15 (ZUGFeRD-Validator):** kein Codefix – bleibt als offene Aufgabe
  „Musterrechnung durch offiziellen Factur-X/ZUGFeRD-Validator, dann Tönnies“.
- **Befund 13** war durch die exakte `number__in`-Auflösung bereits weitgehend
  entschärft; die Regex-Verschärfung ist Zusatzvorsicht (siehe 6).
- Die Befunde 3/4/17/19 waren im v3.21-Clean-Stand bereits behoben (extern
  geprüft wurde ein älteres Paket).

## Neue Tests (52)

| Datei | Inhalt |
|---|---|
| `test_workflow_locks.py` (14) | Storno-/Korrektur-/Angebot→Rechnung-Sperren, Markierung „korrigiert“, View-Pfade |
| `test_dunning_stages.py` (10) | Stufenmodell, Fristen, Doppel-Sperren, Bulk-Auto |
| `test_import_hardening.py` (6) | Bank-ID-Doubletten, Regex, Cross-Customer-Sperre |
| `test_atomic_files.py` (6) | keine halben Dateien, Monatslauf-Constraint, Retry-Scope |
| `test_number_range.py` (8) | Nummernkreis: Vergabe, Seeding, Rollback, Vorschau |
| `test_offer_soft_delete.py` (4) | Angebots-Soft-Delete inkl. Ansichten |
| `test_secure_defaults.py` (4) | DEBUG-Default, WhiteNoise, static/media ohne DEBUG |

## Migrationsübersicht

| Migration | Inhalt |
|---|---|
| 0007 | Invoice-Status „korrigiert“ |
| 0008 | `Payment.external_id` + bedingter Unique-Constraint |
| 0009 | Unique-Constraint GWG-Monatslauf |
| 0010 | Modell `NumberRange` |
| 0011 | Offer: Soft-Delete (`deleted_at`, Manager) |

**Hinweis für den DB-Import der 44 Kunden (Sicherung vom 26.07.):** Die
Migrationen laufen beim ersten Start automatisch. Sollte der Alt-Bestand
wider Erwarten doppelte aktive GWG-Monatsrechnungen (gleicher Kunde+Objekt+
Zeitraum) enthalten, schlägt Migration 0009 sichtbar fehl – dann bitte melden,
das wäre ein echter Datenbefund und wird gezielt bereinigt.
