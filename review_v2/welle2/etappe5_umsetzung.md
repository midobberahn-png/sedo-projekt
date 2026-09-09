# Etappe 5 – Belegketten prüfen und Statusschäden reparieren

Stand: 09.09.2026. Implementierung auf Basis von Etappe 4/6 (`6e0f7ae`). Kein Produktivlauf.

## Vorher / nachher

Vorher gab es keinen Bestandsprüfer. Verlorene Belegzustände blieben trotz der neuen Wächter erhalten.
Jetzt teilen sich Verwaltungsbefehl und Sicherheitsanzeige dieselbe lesende Prüflogik in
`sedo/services/belegketten.py`. Nur die ausdrücklich angeforderte Reparatur schreibt Statuswerte.

## Gruppen aus dem Etappenplan

| Gruppe | Prüfung | Mit `--reparieren` |
|---|---|---|
| A | Ausgestelltes Storno vorhanden, Status verloren | `storniert` |
| A | Ausgestellte Korrektur vorhanden, Status weder storniert noch korrigiert | `korrigiert` |
| A | Storno und Korrektur gleichzeitig | Storno hat Vorrang; Prüfhinweis mit beiden Nummern bleibt auch danach |
| B | Storniert/korrigiert, aber kein ausgestelltes Storno und keine ausgestellte Korrektur | Über `erstellt` den wirklichen Zahlstatus ableiten |
| C | Entwurf mit XML-/ZUGFeRD-Dateiverweis | Ausschließlich melden, physische Dateien nicht prüfen oder ändern |
| D | Entwurf älter als 30 Kalendertage seit Anlage | Ausschließlich Hinweis |
| D | Stornobeleg mit Status bezahlt | `erstellt`, keine Zahlungen verändern |

Aktiv bedeutet **nicht ausgeblendet und nicht Entwurf**, konsistent mit `active_storno` und
`active_correction` aus Etappe 4/6. Ausgeblendete Originale werden ebenfalls nicht repariert.

### Präzisierungen gegenüber dem älteren Plan

- Der alte Wortlaut von A/B schloss Korrekturentwürfe nicht ausdrücklich aus. Die im aktuellen Code
  geltende Definition ist maßgeblich; ein Korrekturentwurf darf das Original nicht sperren.
- B verwendet die rein lesende Ableitung aus `calc.py` statt `refresh_invoice_payment_status()`
  mit dessen schreibendem Zwischenweg. Es gibt genau ein guarded Status-UPDATE und einen
  Pflicht-Audit je tatsächlicher Korrektur; der Audit nennt den Endzustand (etwa bezahlt).
- C bleibt ein Hinweis. Die im älteren Plan erwähnte „Entscheidung 6“ legt hier keine automatische
  Dateibereinigung fest. Der gezielte Dateirückweg aus Etappe 4/6 bleibt unverändert.
- Ein Entwurf/EN-Beleg mit ausgestelltem Folgebeleg sowie „storniert mit nur einer Korrektur“ werden
  zusätzlich als manuelle Prüfaufträge gemeldet. Keine automatische Ausstellung oder nicht
  beschlossene Entstornierung. Diese Befunde werden nicht als erfolgreich repariert gezählt.
- Der GANZE Reparaturlauf ist atomar, nicht nur ein einzelner Beleg. Auch eine nachträgliche
  Constraint-Verletzung (etwa GWG-Monatslauf) rollt frühere Änderungen/Audits desselben Laufs zurück.

## Aufruf und Bericht

Aus dem Django-Unterordner einer **separaten Quell-/Testkopie**:

```bash
python manage.py belegketten_pruefen
python manage.py belegketten_pruefen --json
```

Standardlauf und JSON-Ausgabe sind rein lesend. Befunde allein führen nicht zu einem Fehler-Exitcode.
Für den später freigegebenen Reparaturlauf: erst Sicherung erstellen, App beenden, Berichte prüfen,
dann das explizite `--reparieren` verwenden. Es gibt keinen automatischen Aufruf beim Start/Deploy.

```bash
python manage.py belegketten_pruefen --reparieren --json
```

Schema 1 enthält `modus`, `pruefzeit`, `lauf_id`, `funde`, `zusammenfassung`, `aenderungen` und
`nachpruefung`. Bei Trockenlauf sind `aenderungen` leer und `nachpruefung` null. Bei Reparatur
beschreiben die Hauptbefunde den Vorzustand, `aenderungen` nur tatsächlich übernommene Änderungen,
`nachpruefung` den Zustand danach. Mehrere Hinweise zu einem Beleg zählen als mehrere Befunde,
aber nur ein betroffener Beleg. A/B werden separat als `kettenfehler` gezählt.

Bei Fehlern wird keine Reparatur dieses Laufs übernommen. Der Befehl endet mit Fehlercode; mit
`--json` bleibt stdout ein einzelnes gültiges Fehler-JSON, die Fehlermeldung erscheint auf stderr.
Der Bericht enthält keine Kundennamen, Zahlungszwecke oder Bankzugangsdaten.

## Parallelität und Sicherheit

Die Reparatur nimmt vor dem ersten Lesen eine SQLite-Schreibsperre durch ein wertgleiches UPDATE.
Danach können Beleggraph und Zahlungen während dieses Laufs nicht parallel geändert werden.
Jeder eigentliche Schreibzugriff prüft zusätzlich ID, bisherigen Status, updated_at und Aktivität.
Bei geänderter Revision wird abgebrochen, nicht blind überschrieben. Andere Datenbanktypen sind
nur für den Trockenlauf freigegeben. Es gibt keine Migration, keinen Tabellenumbau und keine
Änderung an Nummernkreisen, Rechnungsdateien, Beträgen, Zahlungen oder Mahndokumenten.

Die neue Sicherheitszeile ist rein lesend. Sie zählt die A/B-Befunde, nicht die C/D-Hinweise;
der CLI-Bericht zeigt alle vier Gruppen. Die schon vorher vorhandene Backup-Middleware ist
nicht Bestandteil dieser Prüflogik und wurde nicht verändert.

## Prüfungen

- 60 neue Regressionstests; lokale Gesamtsuite mit Python 3.13 / Django 5.2.17: 500 grün.
- 17 neue Mutationsgegenproben und die 17 bisherigen Entwurfsgegenproben: Schutz entfernen
  führt jeweils zum erwarteten Assertion-Fehler. Syntax-/Importfehler zählen nicht.
- In-Memory-DB; Parallelitätsprüfung zusätzlich auf eigener temporärer SQLite-Datei mit
  zweitem Prozess. Keine Betriebsdaten kopiert oder geöffnet.
- Schreibfreier Trockenlauf auch mit `PRAGMA query_only=ON`, JSON-Vertrag, Wiederholung ohne
  zusätzliche Audits, Ganzlauf-Rollback bei zweitem Auditfehler und GWG-Constraint-Konflikt.
- `makemigrations --check --dry-run`: keine Änderungen. CI-Matrix: Django 5.2/6.0, Python 3.13.
- Desktop-HTML in Chromium: Fehlerzahl/Prüfen-Kennzeichnung und Anleitung sichtbar,
  keine JavaScript-Laufzeitfehler. Keine allgemeine Überarbeitung des bestehenden Mobil-Layouts.

```bash
python manage.py test sedo --noinput
python tools/gegenproben_belegketten.py --output /tmp/sedo-ketten-gegenproben
python tools/gegenproben_entwurf.py --output /tmp/sedo-entwurf-gegenproben
python manage.py makemigrations --check --dry-run
```

Diese Befehle zum Nachtesten ausschließlich in einer isolierten Quellkopie mit eigenem
`SEDO_HOME` und Test-SECRET_KEY ausführen. Die Prüfungen ersetzen weder eine Bestandssichtung
noch eine rechtliche Zertifizierung. F-18, weitere Mahn-/Mengenfehler, Feldtrennung und optionale
Indizes bleiben außerhalb dieser Etappe. **Die Live-Datenbank ist noch nicht repariert.**
