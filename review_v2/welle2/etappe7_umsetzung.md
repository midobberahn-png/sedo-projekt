# Welle 2, Etappe 7 – Abfrageindizes

Basis: integriertes `main`, `cd42c2d6682cf6b410ce536be67a2a58a869811a`.
Mireks Klarstellung: kein Echtbetrieb, alle vorhandenen Daten sind Testdaten.

## Umfang und begründete Abweichung vom ursprünglichen Plan

Der ursprüngliche Etappenplan nennt einen Einzelindex auf `status` und einen
kombinierten Index auf `(status, due_date)`. Die Geschäftsabfragen wurden erneut
am tatsächlichen Code geprüft: `invoice_list` filtert mit Statusgleichheit,
`get_dunning_candidates` dagegen mit `NOT IN(status)`, `due_date <= cutoff`,
`deleted_at IS NULL` und `ORDER BY due_date`.

Der Vergleich auf 50.000 synthetischen Rechnungen zeigte, dass die beiden alten
Plan-Indizes für den ersten Mahn-SELECT ohne ANALYZE weiterhin einen Tabellen-Scan
und eine temporäre Sortierung ergeben. Ein einfacher Statusindex ist außerdem
bereits als linker Präfix im kombinierten Index enthalten. Deshalb wird der
Plan offen präzisiert, nicht unverändert als optimal ausgegeben:

| Index | Spalten | Bedingung | Zweck |
|---|---|---|---|
| `invoice_status_due_idx` | `status`, `due_date` | keine | Statusfilter, auch Status allein |
| `invoice_active_due_status_idx` | `due_date`, `status` | `deleted_at IS NULL` | Fälligkeit zuerst, danach Statusausschluss im Index; aktive Mahnauswahl |

Der zusätzliche Statuswert im Fälligkeitsindex vermeidet unnötige Tabellenzugriffe
für ausgeschlossene Statuswerte. Ein nur auf `due_date` beschränkter Teilindex
wurde im Vorversuch ebenfalls betrachtet und war dort langsamer. Es gibt keine
Statusliste im Indexprädikat, die bei neuen fachlichen Statuswerten veralten würde.
Die bestehenden Views, Filter, Sortierungen und Geschäftsregeln werden NICHT geändert.

## Migration und Rückweg

`sedo/migrations/0013_invoice_query_indexes.py` hängt ausschließlich von
`0012_concurrency_constraints` ab und enthält genau zwei `AddIndex`-Operationen.
`sqlmigrate` zeigt zwei `CREATE INDEX`, rückwärts zwei `DROP INDEX`. Kein
`RunPython`, keine Tabellenumschreibung und keine Constraint-Änderung. Das Anlegen
liest natürlich die vorhandenen Rechnungszeilen, braucht Speicherplatz und kann
währenddessen Schreibzugriffe blockieren. Kein automatisches ANALYZE eingebaut.

Nach Aktualisierung des Quellcodes, bei gestoppter Anwendung und vorhandener
Sicherung im Ordner mit `manage.py` und der dazugehörigen Python-Umgebung:

```bash
python manage.py migrate --plan
python manage.py migrate sedo 0013
python manage.py showmigrations sedo
```

Der Plan soll ab einem Stand mit angewandter 0012 nur 0013 nennen. Bei älteren
Datenbanken zuerst deren ausstehende Migrationen separat prüfen. Keine alten
Migrationen nur aufgrund dieses Indexauftrags blind nachholen.

Rückweg **von 0013**, solange keine spätere Migration hinzugekommen ist:

```bash
python manage.py migrate sedo 0012
```

Das entfernt nur die beiden neuen Indizes; es setzt keine Belegzustände zurück.
Migrationsbuchführung und Datenbankdatei ändern sich erwartungsgemäß. Deshalb ist
hier Daten-/Schema-Gleichheit der Anwendungstabellen der Nachweis, nicht eine
fälschlich verlangte identische gesamte Datenbank-Prüfsumme nach CREATE INDEX.
Die Wiederholung derselben bereits angewandten Migration bleibt hingegen unverändert.

## Tatsächlich ausgeführte lokale Prüfungen

- Basis: 500 Tests grün. Nach Änderung: 515 Tests grün, davon 15 neue.
- Physische Indexspalten, Teilindexbedingung und echte Service-SQL-Auswahl geprüft.
- 0012 → 0013 → 0012 → 0013 in automatisierten Tests; alle fachlichen Tabellen,
  bestehende Eindeutigkeits-Schranken, Rechnungs-Tabellenwurzel und -DDL erhalten.
- Auf Kopien der beiden vorhandenen Testdatenbanken zusätzlich denselben Zyklus
  und einen Wiederholungslauf ausgeführt. 0 bzw. 9 Rechnungen, alle fachlichen
  Tabellen-Hashes gleich, Integrität/Fremdschlüssel ohne Befund. Originale unverändert.
- Vier neue Gegenproben: jeden Index entfernen, Teilindexbedingung entfernen,
  Spaltenreihenfolge vertauschen. Jeweils echte Assertion rot; kein Syntax-/Importfehler.
- Bestehende 17 Entwurfs- und 17 Belegketten-Gegenproben bleiben in der CI.
- `makemigrations --check --dry-run`: keine weitere Migration nötig.

Die GitHub-CI prüft zusätzlich Python 3.13 mit Django 5.2 und 6.0. Ihr konkretes
Ergebnis und Commit stehen in den PR-Checks; ein lokaler Lauf ersetzt diesen
Nachweis nicht. Keine neue Anwendungs-Versionsnummer oder Installer-Erstellung.

## Messung: Nutzen und Kosten, keine pauschale Geschwindigkeitszusage

Reproduzierbar mit:

```bash
python tools/benchmark_invoice_indexes.py --rows 50000 --repeats 9 --output /tmp/sedo-indexvergleich.json
python tools/gegenproben_indizes.py --output /tmp/sedo-indexgegenproben
```

Das Skript hat keinen Datenbankpfad-Parameter und erzwingt eine frische temporäre
`SEDO_HOME`-Umgebung. 44 synthetische Kunden, 50.000 Rechnungen, 85 % bezahlt,
übrige Status verteilt, NULL-Fälligkeiten und ausgeblendete Rechnungen eingeschlossen.
Es erfasst den echten ersten SELECT des Mahnservices sowie die QuerySet-Auswahl
der Rechnungslisten-View. Neun warme Messläufe, ohne ANALYZE und ohne erzwungenen
Index. Ergebnisinhalte werden bei jeder Variante per Hash verglichen; gleiche
Fälligkeiten haben wie zuvor keine garantierte Reihenfolge untereinander.

Lokaler Beispiellauf, Python 3.13.5 / Django 5.2.17:

| Messung | Ohne neue Indizes | Ursprünglicher Plan | Umsetzung |
|---|---:|---:|---:|
| Mahn-SELECT, Median | 19,08 ms | 18,96 ms | 14,07 ms |
| Statuslisten-SELECT, Median | 14,03 ms | 10,05 ms | 10,18 ms |
| Mahn-SELECT, ca. VM-Schritte | 808.000 | 808.000 | 372.000 |
| Statusänderung an 500 Zeilen, Median, anschließend Rollback | 0,55 ms | 2,24 ms | 3,80 ms |
| Belegte Datenbankseiten | 9.920.512 Bytes | 12.021.760 Bytes | 12.451.840 Bytes |

Die Verbesserung betrifft hier nur die SQL-Leseauswahl, NICHT die gesamte
Mahnlogik mit Folgeabfragen, PDF-Erstellung oder die ganze Benutzeroberfläche.
Auch die angegebene Schreibmessung schließt keinen dauerhaften COMMIT ein.
Die zusätzlichen rund 2,53 MB und höheren Schreibkosten sind der Gegenpreis.
Bei 0 bzw. 9 vorhandenen Rechnungen ist kein spürbarer Nutzen zu versprechen.
Datenverteilung, Statistiken, SQLite-Version, Cache und Hardware beeinflussen
Indexauswahl und Zeiten. Die Tests enthalten deshalb keine flüchtigen Zeitlimits.

## Primärquellen zur Einordnung

- SQLite Query Planning, insbesondere mehrspaltige Indizes und linker Präfix:
  https://www.sqlite.org/queryplanner.html
- SQLite EXPLAIN QUERY PLAN: https://www.sqlite.org/eqp.html
- Django AddIndex/RemoveIndex:
  https://docs.djangoproject.com/en/5.2/ref/migration-operations/

Die Indexentscheidung beruht auf dem aktuellen Code und den eigenen Messungen;
sie ist eine dokumentierte Anpassung, keine Aussage des ursprünglichen Plans.
Welle 3 und andere offene Fachfehler werden durch diese Performance-Migration
weder behoben noch stillschweigend als erledigt erklärt.
