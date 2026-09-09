# Änderungsbericht v1.0 beta 3.23.1 – Concurrency & Validation (14.08.2026)

**Anlass:** Mireks dritter Review (gegen das echte v3.23-ZIP): Gesamturteil gut,
aber zwei echte Race-Conditions offen – die `ensure_*`-Guards prüfen VOR der
Transaktion und halten parallelen Requests nicht stand. Dazu die fachliche
Frage zu Zahlungen auf korrigierte Rechnungen. Bewusst KEIN großer Umbau,
nur der empfohlene kleine Patch. Migration 0012.

**Ergebnis:** 217 → **228 Tests grün** (11 neue, davon 9 Parallelitäts-Tests,
die die Guards gezielt umgehen und die DB-Schranken beweisen).

## 1. Harte DB-Schranken gegen parallele Requests (Befunde 1+2)

Neue Constraints (Migration 0012):

| Constraint | Wirkung |
|---|---|
| `dunning_invoice_kind_unique` (DunningDocument: invoice+kind) | je Rechnung maximal EINE Erinnerung und EINE Mahnung |
| `invoice_unique_active_storno` (reference_invoice, Typ 457, aktiv) | maximal EIN aktives Storno je Ursprungsrechnung |
| `invoice_unique_active_correction` (reference_invoice, Typ 384, aktiv) | maximal EINE aktive Korrektur je Ursprungsrechnung |

Soft-gelöschte Folgebelege blockieren nicht (bedingte Constraints) – konsistent
zur bisherigen Guard-Logik.

**Übersetzung in Nutzermeldungen:** Der zweite (verlierende) Request bekommt
keinen 500er, sondern einen `WorkflowError` („… wurde soeben bereits erzeugt
(paralleler Vorgang)“): `create_storno` und `generate_dunning_document` fangen
den `IntegrityError` und übersetzen ihn; der Korrektur-Pfad in `invoice_create`
ebenso. Wichtig dabei: SQLite meldet die SPALTE (`reference_invoice_id`), nicht
den Constraint-Namen – das Matching berücksichtigt beides. Und der fehlschlagende
INSERT läuft in einem eigenen `transaction.atomic()` (Savepoint), damit eine
umgebende Transaktion intakt bleibt.

**Guarded Updates statt Check-then-act:**
- Angebot→Rechnung: `Offer.objects.filter(pk=…, linked_invoice__isnull=True)
  .exclude(status='in_rechnung').update(…)` – verliert dieser Request das
  Rennen, rollt die GESAMTE Transaktion zurück (inkl. Rechnung und Nummer,
  dank NumberRange-in-Transaktion aus v3.22). Beide Pfade abgesichert
  (Direkt-POST und Formular-Weg).
- `mark_invoice_corrected`: markiert nur, wenn das Original noch nicht
  korrigiert/storniert ist – sonst `WorkflowError` + Rollback.

**Altbestände:** Migration 0012 dedupliziert vor dem Dunning-Constraint
Doppel-Mahnungen gleicher Stufe (Artefakte des in v3.22 behobenen
Mehrfachklick-Bugs; das ÄLTESTE Dokument je Rechnung+Stufe bleibt).
Für Storno/Korrektur wird NICHT automatisch bereinigt – sollte ein Altbestand
(z. B. die 44-Kunden-Sicherung) doppelte aktive Stornos enthalten, schlägt die
Migration sichtbar fehl und wird manuell geklärt (echte Belege!).

## 2. Zahlungen auf korrigierte Rechnungen (Befund 10, Mireks Entscheid)

Zahlt ein Kunde mit der Nummer einer KORRIGIERTEN Rechnung im Verwendungszweck,
ordnet der Import die Zahlung jetzt der **aktiven Korrekturrechnung** zu
(Kettenauflösung, max. 5 Stufen). Gibt es keine aktive Korrektur, bleibt die
Zahlung bewusst „unklar“ zur manuellen Zuordnung. Per Test abgedeckt
(Umleitung + Unklar-Fall).

## 3. Einordnung zu Befund 3 (Datei-Atomarität)

Bewusst nicht weiter verschärft – Mireks eigene Einstufung („nicht dringend,
aktuelle Lösung sehr robust“). Die drei `os.replace`-Aufrufe sind Renames auf
demselben Dateisystem; das Restrisiko zwischen zwei Renames ist praktisch
vernachlässigbar, und `generate_invoice_files` läuft in den kritischen Pfaden
ohnehin innerhalb der DB-Transaktion.

## 4. Offen (nächster Schritt)

- **ZUGFeRD-Validator:** Mireks Entscheid: Java installieren, dann voller
  Mustang-Lauf (inkl. EN16931-Geschäftsregeln) gegen eine echte Musterrechnung.
  Steht aus, bis ein Java-Runtime installiert ist (z. B. Temurin).

## Neue Tests (11)

- `test_concurrency_locks.py` (9): Guards per Patch umgangen („beide Requests
  haben den Guard passiert“) → DB stoppt den zweiten mit WorkflowError;
  direkte Doppel-Inserts verletzen die Constraints; soft-gelöschte Folgebelege
  blockieren nicht; Erinnerung+Mahnung koexistieren.
- `test_import_hardening.py` (+2): Zahlung auf korrigierte Rechnung →
  Korrektur bzw. „unklar“.
