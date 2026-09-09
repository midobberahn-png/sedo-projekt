# Übergabe: Abarbeitung des Senior-Code-Reviews v2

**Zweck:** Ein neuer Chat soll ohne den bisherigen Gesprächsverlauf weiterarbeiten können.
**Stand:** 9. September 2026. **Welle 1 komplett, Welle 2 zu drei von sieben Etappen.**

Vorgänger-Dokument: `UEBERGABE_CODE_REVIEW_V2.md` (Kontext, Fundübersicht, Arbeitsregeln).
Der Bericht selbst: `bericht_senior_review_v2.md` (125 Funde, 204 KB, abschnittsweise lesen).

---

## 1. Zuerst lesen

1. `~/Desktop/Co-Work OS/CLAUDE.md` – globale Arbeitsregeln (Antworten auf Deutsch, Plan vor Umsetzung)
2. `MEMORY_SEDO.md` im Projektordner – Projektgedächtnis, Abschnitt „OFFENE AUFGABEN"
3. Dieses Dokument
4. `review_v2/welle2/etappenplan_4_bis_7.md` – der Detailplan für die nächste Arbeit

## 2. Wo der Code steht

**Ordner:** `~/Desktop/Co-Work OS/Projekte/SEDO Rechnungstool/sedo_rechnungstool_v2/`
**Branch:** `review-v2-fixes` · **Nullpunkt:** `e9df5b5` auf `main`

Am 4.9. wurde ein Git-Repo angelegt – vorher gab es keins mehr. `.gitignore` schließt `data/`,
`backups/`, `media/`, `logs/`, `dist/`, `build/` und die venvs aus; **die Live-Datenbank ist
nicht im Repo**. Rückrollen: `git checkout main`, oder einzelne Commits per `git revert`.

**Testsuite: 387 Tests, grün.** Vorher waren es 245.

```bash
cd "/Users/mirekdobberahn/Desktop/Co-Work OS/Projekte/SEDO Rechnungstool/sedo_rechnungstool_v2" && DJANGO_ALLOWED_HOSTS=testserver,127.0.0.1 .venv/bin/python manage.py test sedo
```

Alle neuen Regressionstests liegen in `sedo/tests/test_review_v2.py` (142 Tests), benannt nach
den Fundnummern. **Keine der Änderungen brauchte eine Migration** – `makemigrations --check`
läuft ohne Befund.

## 3. Verbindliche Arbeitsregeln

- **Antworten auf Deutsch.** Code-Kommentare auf Deutsch.
- **`data/sedo_web.sqlite3` ist die Live-Datenbank mit 44 echten Kunden.** Nicht öffnen, nicht
  migrieren, nicht in Tests einbeziehen. Lesend nur mit `?immutable=1`.
- **Keine Dateien löschen ohne Bestätigung.** Statt Löschen in den Scratchpad verschieben.
- **Keine E-Mails ohne ausdrückliche Freigabe.**
- **GoBD:** Rechnungen, Zahlungen und Mahnungen sind aufbewahrungspflichtig. Änderungen an
  Nummernkreis, Storno-Logik oder Löschwegen immer daraufhin bewerten.
- **Zu jedem Fix gehört eine Gegenprobe:** Fix zurücknehmen, Test muss rot werden. Ein Test,
  der auch ohne den Fix grün ist, ist wertlos. Das hat in dieser Abarbeitung mehrfach eigene
  Denkfehler aufgedeckt.
- **Vor Änderungen den Plan zeigen.** Mirek arbeitet die Funde bewusst mit, nicht im Autopilot.

**Technische Eigenheit der Umgebung:** Ein Hook („GateGuard") weist die **erste** Änderung an
jeder Datei einmal ab und verlangt Angaben zu Importeuren, betroffener API und Datenformaten.
Einfach liefern und den Aufruf wiederholen. Abschaltbar nur beim Sessionstart über
`ECC_GATEGUARD=off`.

## 4. Was erledigt ist

14 Commits seit dem Nullpunkt, 30 Dateien, +3268/−269 Zeilen.

### Welle 1 – vollständig

| Fund | Was jetzt anders ist |
|---|---|
| F-38 | Testläufe schreiben keine Sicherungen mehr in den Live-Ordner |
| F-17 | Mapping-Import löscht nicht mehr bei 0 gültigen Zeilen |
| F-16 | Monatslauf bricht ab statt still 10,00 € einzusetzen |
| F-12 | Kundenimport überschreibt keine Stammdaten mit Leerwerten |
| F-02 | Belege im Admin und auf Modellebene nicht mehr hart löschbar |
| F-03/31/81 | Backups ohne Sessions, ohne Rest-Bytes, fail-closed |
| F-09 | SMTP prüft in beiden Wegen das Zertifikat |
| F-04 | Storno-/Korrektur-XML ist wieder schemagültig (gegen echtes CII-D16B-XSD belegt) |
| F-11/F-66 | Fehlklick löst keine Massenmahnung mehr aus |
| F-53 | Abmelden funktioniert überhaupt wieder (war HTTP 405) |
| F-24 | `makemigrations` raus aus den Startskripten |
| F-48 | Zahlungsimport erfindet kein Buchungsdatum mehr |

### Welle 2 – Etappen 1 bis 3

| Etappe | Inhalt |
|---|---|
| 1 (`3184595`) | Der Zahlstatus überschreibt keinen Belegzustand mehr. Neue Konstanten `GESCHUETZTE_STATUS`/`NICHT_OFFENE_STATUS`, `abgeleiteter_zahlstatus()`, `offene_rechnungen()`, `zuordenbare_rechnungen()`. Mit F-89. |
| 2 (`4a692b1`) | Wächter prüfen den Beleg-Graphen statt des Statusfelds. Neu: `ensure_zahlung_erlaubt`, `ensure_versand_erlaubt`, `versand_nachweis`, `ausblenden_hindernis`. |
| 3 (`8f59780`) | Views, Mengen, Sichtbarkeit. Mit F-18, F-60, F-63, F-115. |

### Drei Funde, die in keinem Bericht stehen

- **NEU-1** `sedo/audit.py`: `write_audit` verschluckte jeden Schreibfehler an ~44 Aufrufstellen.
  Weil es beim Ausblenden **vor** der Aktion läuft, verschwand der Nachweis, während die Aktion
  durchlief. Jetzt drei Kanäle und `pflicht=True` an den drei Ausblende-Stellen.
- **NEU-2** `views.parse_date_for_view`: stiller Rückfall auf *heute* im Massenlauf.
- **NEU-3** `test_security_hardening.py`: Der Test für „keine Secrets im ZIP" prüfte faktisch
  nichts, weil die Test-DB im Speicher liegt.

## 5. Entscheidungen, die Mirek getroffen hat

Diese gelten weiter – nicht neu aufrollen:

1. **Ausblenden bleibt „mild":** erlaubt, solange kein Versandnachweis, keine Zahlung, keine
   Mahnung und kein Folgebeleg vorliegt. Geprüft wird der **Nachweis** (`.eml`, Protokoll),
   ausdrücklich **nicht** der Statuswert – der wird von `ueberfaellig` überschrieben.
2. **Eigener Entwurfs-Nummernkreis `EN`, sofort mitmachen** (Etappe 6).
3. **Beim Finalisieren aus einem Vormonat wird nachgefragt**, mit Häkchen „Rechnungsdatum auf
   heute setzen" – das Datum wird nicht still gezogen.
4. **Audit-Log fail-closed:** Lässt sich das Ausblenden nicht protokollieren, unterbleibt es.
5. Erneutes Versenden einer korrigierten Rechnung bleibt **erlaubt**.
6. Die Dashboard-Kachel **„Umsatz im Monat" wird nicht angefasst** – das ist die Zahl, die
   täglich angeschaut wird.

## 6. Was als Nächstes ansteht

**Etappe 4** ist der wichtigste offene Punkt: *Der Entwurf bekommt Ausgänge.*
Heute ist ein Entwurf eine Sackgasse – es gibt **keine Bearbeiten- und keine Finalisieren-Route**
(`sedo/urls.py` kennt weder `invoice_edit` noch `invoice_finalize`). Wer sich vertippt, verbrennt
eine Nummer. Etappe 4 bringt beides plus den Riegel, dass zu einem Entwurf gar keine gültige
`factur-x.xml` mehr entsteht.

Der vollständige Plan mit Schritten und Testplan: **`review_v2/welle2/etappenplan_4_bis_7.md`**

| Etappe | Inhalt | Migration |
|---|---|---|
| 4 | Entwurf: keine gültige XML, dafür Bearbeiten und Finalisieren | nein |
| 5 | `belegketten_pruefen` repariert die **heute schon** kaputten Belege | nein |
| 6 | Entwurfs-Nummernkreis `EN` | nein |
| 7 | *optional:* Index auf `status` – die einzige Migration, kann entfallen | ja |

Danach: **Welle 3** (Feldtrennung `lifecycle` + `payment_state`, `pre_save`-Wächter,
CheckConstraint) und **Welle 4** (die restlichen mittel/niedrig-Funde).

**F-05 (PDF/A-3) ist entschärft:** Die sechs Testrechnungen wurden bei Aareon *ohne* PDF/A-3,
ohne XMP und mit nicht eingebetteten Schriften erfolgreich eingelesen. Kein Blocker für den
1.1.2027. Details in `antwort_assmann_entwurf_2026-09-09.md`.

## 7. Offene Funde aus dem Etappe-3-Review

Nach Etappe 3 lief ein adversarischer Review: vier Blickwinkel griffen den Diff an, danach
versuchte je ein eigener Agent, jeden Fund zu **widerlegen**. 35 gemeldet, 23 bestätigt,
8 sofort behoben. **Die verbleibenden 15 stehen in
`review_v2/welle2/offene_funde_etappe3_review.md`** – mit Belegen aus echten Testläufen.

Die wichtigsten davon sind Inhalt der Etappen 4–6 (Sackgasse, Nummernkreis, Bestandsreparatur).
Eigenständig offen sind unter anderem:

- **Mahnwesen prüft weiter nur das Statusfeld** – `status='gemahnt'` kann auf einer bereits
  ersetzten Rechnung landen (`get_dunning_candidates`).
- **F-63 ist unvollständig:** Der Abo-Vorlagenlauf (`views.py:2233`) macht aus 0 Tagen weiterhin 14.
- **„Dateien neu erzeugen"** überschreibt weiterhin archivierte Belege. Beim E-Mail-Versand ist
  das behoben; der Knopf selbst ist eine bewusste Nutzeraktion und blieb deshalb stehen.
- **Die Mengen prüfen den Status, die Wächter den Beleg-Graphen.** Bei Bestandsbelegen mit
  verfälschtem Status zählen die Mengen weiter doppelt – das repariert Etappe 5.

## 8. GWG/Aareon – laufende Testphase

Markus Assmann hat am 9.9. gemeldet: Die sechs Testrechnungen vom 2.9. wurden **erfolgreich
eingelesen und verteilt**. Eine Anpassung war nötig und ist erledigt (Commit `12a2c3e`): Im
Rechnungstext (`ram:Name`) steht jetzt das Objekt.

```
vorher    Rechnung September 2026
nachher   Rechnung Resedaweg 11 li. · September 2026
```

**Bereitgelegt, noch nicht versendet:**
- `antwort_assmann_entwurf_2026-09-09.md` – Antwortentwurf mit drei Rückfragen
- `testrechnungen_2026-09-09/` – drei neue Testrechnungen (RE2026090007–09), je eine für die
  Bestände Neitzel, Radtke und Lammel

**Offen bei GWG:** die drei fehlenden Wirtschaftseinheiten 3601, 2001 und 2517 in der
Mappingdatei; die SEDO-Verkäufer-ID; und ob die Empfänger-ID 399662000 auch produktiv gilt.
Die Empfänger-ID in der XML ist derzeit `1` (die Kundennummer) – das war beim erfolgreichen
Testlauf genauso.

**Testinstanz:** `sedo_testinstanz_gwg/` mit **eigener Datenbank**. Dort und nur dort werden
GWG-Testrechnungen erzeugt; der Live-Nummernkreis bleibt unberührt. Sie trägt seit 9.9. den
aktuellen `rechnungslauf_core.py`; die vorherige Fassung liegt im Scratchpad.

## 9. Aufräumen, das noch aussteht

- **`backups/` enthält 373 ZIPs**, davon **91 Test-Artefakte vom 9.9.** Sie entstanden, weil
  Prüf-Agenten Django programmatisch starteten – der F-38-Riegel hing an `'test' in sys.argv`.
  Das ist jetzt behoben (`backup_dir()` erkennt den Testbetrieb am Datenbanknamen), **die 91
  Dateien liegen aber noch da.** Muster: `SEDO_Backup_2026-09-09_*_weekly_auto.zip`.
  Löschen erst nach Mireks ausdrücklicher Freigabe.
- **Die Altbackups bleiben belastet:** Alle ZIPs von vor dem 4.9. enthalten gültige Session-Keys
  und Secrets als Rest-Bytes. Ein Backup-ZIP ist laut Mirek nie herausgegangen, deshalb kein
  akuter Handlungsbedarf. `manage.py changepassword admin` würde die Session-Keys entwerten.
- **Das gebündelte Programm unter `dist/` trägt noch den alten Code.** Vor einer Auslieferung
  `build_mac.command` bzw. `BUILD_WINDOWS.bat` neu laufen lassen. Die Starter aus dem
  Quellordner (`SEDO.app`, `START_HIER.command`) sind sofort aktuell.

## 10. Was ein neuer Chat wissen sollte

**Die Arbeitsweise hat sich bewährt:** Vor jedem Block eine Analyse mit je einem Fachagenten und
einem Skeptiker pro Fund, dazu Musterschauen nach derselben Fehlerklasse an anderen Stellen.
Das hat mehrfach Dinge gefunden, die im Bericht fehlten – und mehrfach Behauptungen des Berichts
korrigiert (etwa die falsch zitierte Validator-Meldung bei F-04).

**Zwei Fallen, in die diese Abarbeitung selbst getappt ist:**

1. Beim Zusammenstellen eines Jury-Prompts wurden drei Entwürfe aneinandergehängt und auf 60.000
   Zeichen gekürzt – **einer fiel raus und bekam 0 Punkte, ohne gesehen worden zu sein.** Bei
   Workflow-Skripten immer prüfen, ob die Eingabe vollständig ankommt.
2. Ein Test legte eine Rechnung mit `status='bezahlt'` an, ohne eine Zahlung dazu. Die Ableitung
   setzte sie korrekt zurück – **der Code hatte recht, der Testaufbau nicht.**

**Der Zustand des Statusfelds in einem Satz:** `Invoice.status` trägt zwei Bedeutungen. Der
Zahlstatus ist eine *abgeleitete* Größe (aus Zahlungen, Mahnungen, Fälligkeit reproduzierbar),
der Belegzustand eine *gesetzte* (entsteht an je einer Stelle, nicht rekonstruierbar). F-07 war
exakt: eine abgeleitete Größe überschreibt eine gesetzte. Welle 2 macht das unmöglich; Welle 3
würde die Vermischung selbst auflösen.
