# Restabsicherung nach Welle 2 – Mahnwächter

Stand: 10.09.2026. Basis: `50611bcdf653077ef16a89efc246725198817a20`
(F-18 bereits in main integriert). Laut Mirek ausschließlich Testdaten, kein
Echtbetrieb. Dieser Block verändert keine vorhandenen Datenbanken oder Medien,
startet keinen Webserver und versendet keine E-Mails.

## Auftrag und fachliche Abgrenzung

Ein Original mit ausgestellter, nicht ausgeblendeter Storno- oder
Korrekturrechnung darf nicht gemahnt werden – auch bei verfälschtem Zahlstatus,
veralteter Browserauswahl oder einer alten Python-Instanz. Entwürfe von
Folgebelegen zählen wie bisher nicht als ausgestellte Ersatzbelege. Die
Korrekturrechnung selbst bleibt mahnbar, sofern sie nicht ihrerseits gesperrt ist.
Eine später stornierte Korrektur reaktiviert nicht automatisch das alte Original;
dies entspricht dem bestehenden Belegbegriff `active_correction`.

Nicht Bestandteil: Abo-Zahlungsziel 0, Feldtrennung/Welle 3, Dashboard-Umsatz,
neue Regeln zur manuellen Fälligkeit oder zum manuellen Stufenaufstieg (F-68).
Der bestehende manuelle Weg darf weiterhin unmittelbar eine Mahnung wählen;
die automatische Stufenfolge bleibt Erinnerung → Fristablauf → Mahnung.
Diese Änderung ist keine pauschale Freigabe aller sonstigen Mahn-/Review-Funde.

## Lesewege

- `mahn_hindernis(invoice)` lädt den aktuellen Datensatz und liefert einen
  verständlichen Sperrgrund. Kein Vertrauen in den Objekt-/Prefetch-Cache.
- `next_dunning_kind()` und `ensure_dunning_allowed()` prüfen ebenfalls frisch.
  Belegtyp 457 und ausgeblendete/fehlende Originale werden gesondert gesperrt.
- Die Kandidatenabfrage schließt aktive Folgebelege bereits mit `NOT EXISTS`
  im SQL aus, nicht durch eine zusätzliche Graph-Abfrage pro Rechnungszeile.
  Das behebt nicht die übrigen vorhandenen N+1-Zahlungsabfragen.
- Die Rechnungsdetailseite verwendet denselben Sperrgrund, blendet die beiden
  Mahnaktionen bei Belegsperre aus und zeigt die Erklärung dauerhaft an.
  Die Anzeige ist keine Sicherheitsgrenze; ein direkter POST bleibt gesperrt.

## Verbindlicher Schreibweg

`generate_dunning_document(invoice, kind, *, request=None)` prüft selbst, auch
wenn der Aufrufer keinen vorgeschalteten Guard aufgerufen hat. Der neue optionale
Request dient der unveränderten Zuordnung des Audits zum angemeldeten Benutzer.
`send_bulk_dunning(..., *, request=None)` behält den Rückgabevertrag
`{'created': n, 'skipped': n}`. Wie bisher erzeugt dieser Service Dokumente,
keine E-Mails. Unbekannte Stufen werden nicht mehr als Erinnerung interpretiert.

Innerhalb einer Transaktion ist das erste Datenstatement ein UPDATE von
`updated_at` auf seinen bestehenden Wert. Auf SQLite wird dadurch die
Schreibtransaktion vor den Prüf-Lesezugriffen begonnen. Danach werden Rechnung,
Beleggraph und Mahnstufe frisch geprüft. `auto` prüft zusätzlich Fälligkeit,
offenen Betrag und nächste Stufe innerhalb der Sperre: eine alte Auswahl genügt
nicht. Der Statuswechsel selbst ist zusätzlich an Ausgangsstatus, Sichtbarkeit,
Belegtyp und das Fehlen aktiver Folgebelege gebunden.

Der Einzelbeleg umfasst jetzt zusammenhängend PDF-Erzeugung, DB-Zeile,
Statuswechsel und genau einen verpflichtenden Audit mit `von`, `nach`,
`anlass=mahnung`, `nummer` und `mahnstufe`. Der frühere nachträgliche
Einzel-View-Audit entfällt; der bisherige Batch-Zusammenfassungseintrag bleibt.
Ein Fehler beim Einzelbeleg rollt dessen Datenänderungen zurück. Bereits
abgeschlossene andere Belege eines Sammellaufs werden nicht rückgängig gemacht.
Fachlich gesperrte sowie erwartete Datei-/Protokollfehler werden als übersprungen
gezählt; Detail-/Einzelaufrufe zeigen den konkreten Fehler. Unbekannte technische
Fehler werden nicht als erfolgreiche Erstellung ausgegeben.

## Dateien und Fehlerkompensation

Die PDF-Gestaltung ist unverändert; gerendert wird zunächst im temporären
Medienverzeichnis. Erst nach allen Prüfungen wird die komplette Datei per
Hardlink exklusiv veröffentlicht. Vorhandene Dateien werden nie überschrieben;
ein erst zwischen Prüfung und Veröffentlichung belegtes Ziel bleibt erhalten.
Rechnungsnummern mit Pfad-Traversal und Symlinks im Mahnziel werden abgewiesen.

Bei einem Fehler am DB-Abschluss wird nur die durch diesen Vorgang veröffentlichte
Datei anhand ihrer Geräte-/Inode-Identität in die vorhandene Medienquarantäne
verschoben. Keine Löschung fremder Dateien. Damit wird auch die bisherige
PDF-Fehlerlücke des Einzelgenerators entschärft: ein fehlgeschlagenes PDF verbraucht
keine Mahnstufe. Alte inkonsistente Mahndokumente werden nicht automatisch repariert.

Wie bei F-18 gilt: Hardlink-Unterstützung des Einsatzdateisystems ist erforderlich,
sonst sicherer Abbruch. Das ist keine gemeinsame stromausfallsichere Transaktion
von Datenbank und Dateisystem und kein Schutz gegen direkten Administratorzugriff.
Bei einer zusätzlichen, außerhalb dieses Services verwalteten äußeren Transaktion
muss deren Besitzer den Dateirückweg auch für spätere Fehler übernehmen; die
hier verdrahteten Einzel-/Batch-Views besitzen keine solche äußere Transaktion.
Klappt eine Quarantäneverschiebung selbst nicht, ist eine manuelle Dateiprüfung
nötig. Die Parallelitätsnachweise gelten ausdrücklich für SQLite.

## Nachweise und Reproduktion

- Unveränderte Basis: 562 Tests bestanden.
- Lokaler Abschluss: 609 Tests bestanden (562 Bestand + 47 neue), Python 3.13.5,
  Django 5.2.17. Keine alten Testmethoden entfernt.
- 14 neue Mutationsgegenproben; zusammen mit 11 Archiv-, 17 Entwurfs-,
  17 Belegketten- und 4 Indexproben: 63. Jede muss nach Entfernen des Schutzes
  an einer Assertion fehlschlagen; Syntax-/Importfehler zählen nicht.
- Tests umfassen falsche Originalstatus, Storno-/Korrektur-/Entwurfs-/
  Soft-Delete-Kombinationen, veraltete Prefetches, GET/POST/CSRF, direkte Service-
  und Batch-Aufrufe, Pflichtaudit- und PDF-Fehler, konkurrierende Dateiziele,
  zwei echte SQLite-Verbindungen sowie fehlgeschlagenen DB-Commit.
- Zusätzlich `probe_mahnwaechter_sqlite.py`: drei koordinierte Gegenläufe auf
  einer frisch erzeugten DATEI-SQLite-DB. Der zweite Mahnaufruf beginnt während
  einer schreibenden Storno-/Korrektur-/Mahntransaktion, wartet und lehnt nach
  deren Commit fachlich ab; nicht bloß mit einem Lockfehler. Datenbankintegrität
  danach geprüft. Keine Zeitmessung als Bestehenskriterium.
- Bestehende Mahnstufen-/Batch-Tests erhalten je ein eigenes temporäres
  Medienverzeichnis. Ihre fachlichen Assertions bleiben unverändert; alte
  Testdateien dürfen nicht durch den nächsten Test überschrieben werden.

In der passenden Python-Umgebung, mit isoliertem `SEDO_HOME`:

```bash
python manage.py test sedo --noinput
python tools/gegenproben_mahnwaechter.py --output /tmp/sedo-mahn-gegenproben
python tools/probe_mahnwaechter_sqlite.py --output /tmp/sedo-mahn-dateidb.json
python manage.py makemigrations --check --dry-run
```

Das Datei-Probeskript und die Mutationsrunner erzwingen eigene temporäre
Datenbanken. Die reguläre PR-CI wiederholt die gesamte Suite, alle fünf
Gegenprobengruppen, die Datei-SQLite-Probe und den Indexbenchmark unter Django
5.2 und 6.0. Das konkrete CI-Ergebnis steht im PR-Abschlussvermerk, nicht allein
in dieser vor der Veröffentlichung erstellten Anleitung.

Keine neue Modellmigration. Eine lokal noch ausstehende Migration 0013 aus
Etappe 7 bleibt separat. Kein Merge, Mac-Deployment oder Installer-Build wird
mit der Erstellung dieses Review-Standes behauptet.

## Technische Primärquellen

SQLite beginnt nach einem ersten UPDATE eine Schreibtransaktion; SELECT FOR
UPDATE ist in Django/SQLite wirkungslos. Diese Dokumentation begründet die
Sperrstrategie, ersetzt aber nicht die oben ausgeführten eigenen Gegenläufe:

- https://www.sqlite.org/lang_transaction.html
- https://docs.djangoproject.com/en/5.2/ref/databases/#sqlite-notes
