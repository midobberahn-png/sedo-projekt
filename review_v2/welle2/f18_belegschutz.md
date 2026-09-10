# Restabsicherung nach Welle 2 – F-18 / Belegdateien

Stand: 10.09.2026. Basis: integriertes main `cafb1822834ccdcf4aa03677c8d174250f79da5c`.
Vom Nutzer freigegeben: zunächst F-18, nicht Mahnwächter oder Abo-Zahlungsziel.
Noch kein Echtbetrieb; vorhandene Daten sind Testdaten. In diesem Block ausschließlich
synthetische Testdaten und temporäre Medien verwendet. Kein echter Versand, kein Mac-Deploy.

## Regel und betroffene Wege

Entwürfe bleiben bearbeitbar und erzeugen nur ein Wasserzeichen-PDF. Bereits
 ausgestellte PDF-/XML-/ZUGFeRD-Dateien werden durch den Anwendungsworkflow nicht
überschrieben oder aus aktuellen Stammdaten neu berechnet. Das gilt auch nach
Kunden-, Objekt- oder Positionsänderungen sowie bei bezahlt/korrigiert/storniert.

Die Sperre sitzt zentral in `generate_invoice_files`, nicht nur im Template.
Ohne internes `erstmalig=True` werden ausgestellte Archive nur geprüft und wiederverwendet:
keine Neuberechnung, kein neues Audit, keine Änderung von Timestamp oder Dateiverweisen.
Ein fehlendes Original oder ein fehlender Verweis ist keine Neuerzeugungserlaubnis.
Der Knopf auf der Detailseite wird nur bei Entwürfen angeboten; ein direkter POST
oder übermitteltes `erstmalig`/`force` schaltet keine Erstausstellung frei.

Alle bekannten schreibenden Erstellungswege sind explizit umgestellt: neue Rechnung,
Finalisierung, Korrektur, Storno, Angebot→Rechnung, GWG- und Abo-Lauf. Eine Erstausstellung
verlangt Status erstellt, keinen EN-Präfix, keinen alten Archivnachweis, keine
Dateiverweise und freie Ziele. Historische RE-Entwurfsvorschauen werden beim
Finalisieren aufbewahrt, bevor dort das erste ausgestellte Original entsteht.
Das Argument ist ein interner Python-Vertrag, keine Sicherheitsgrenze gegen
beliebigen Python-Code oder direkte Datenbankmanipulation.

Versand (.eml/SMTP), der zusätzliche .eml-Helfer, GWG-Sammel-ZIP und
Steuerberater-PDF-Export lesen nur die jeweils benötigten Originale. Keine
Fallback-Erzeugung und kein stilles Überspringen fehlender Anhänge. Ein Sammel-
Export prüft die gesamte Auswahl vor dem Anlegen/Überschreiben seines ZIPs.
`EmailAttachment` hält einmal gelesene Bytes unveränderlich; die geprüften Bytes
sind damit dieselben, die später angehängt werden. Ein ausdrücklich angegebener,
fehlender Mail-Anhang führt auch im allgemeinen Mailservice zum Fehler.

## Archivnachweis ohne neue Modellmigration

Jede neue Erstausstellung schreibt im selben DB-Vorgang einen Pflicht-Audit
`vorgang=belegarchiv_erstellt`, `schema=1`, `nummer` und pro `pdf_file`, `xml_file`,
`zugferd_file` die Felder `<feld>`, `<feld>_sha256`, `<feld>_bytes`.
Flache Werte sind notwendig: die bestehende Audit-Normalisierung kürzt verschachtelte
Strukturen. Es wird keine neue Tabellenstruktur vorausgesetzt.

Lesen für Versand/Export und die Detailprüfung vergleichen bei neuen Archiven
Verweis, Größe und SHA-256. Fehlende, veränderte oder mehrdeutige Nachweise werden
nicht automatisch ersetzt. Ein irrtümlicher Statuswechsel zurück auf Entwurf
entfernt einen vorhandenen Archivnachweis nicht.

Altbelege ohne Nachweis bleiben mit vorhandenen, nicht leeren Originaldateien
lesbar und versendbar. Sie bekommen kein rückwirkendes Echtheitssiegel. Die UI
weist diese Grenze aus. Fehlende Altdateien werden NICHT neu erzeugt.

## Veröffentlichung und Rückweg

SQLite-Schreibsperre durch ein UPDATE ohne Änderung des Revisionswerts, danach
aktuellen Beleg neu laden. Neue Originale zunächst im temporären Medienverzeichnis
erzeugen, anschließend mit `os.link` exklusiv veröffentlichen: vorhandene Ziele
werden nicht ersetzt, auch wenn sie erst nach der Vorprüfung belegt wurden.
Quelle/Ziel liegen auf demselben Dateisystem. Bei fehlender Hardlink-Unterstützung
wird abgebrochen, kein unsicherer Überschreib-Fallback (auf dem Einsatzsystem prüfen).

Pfad-Traversal, symbolische Verknüpfungen und Schreibziele anderer Rechnungen
(einschließlich ausgeblendeter Rechnungen und gespeicherter Archivnachweise)
werden vor dem Dateirückweg abgewiesen. Entwürfe dürfen nicht fremde Originale
über einen falschen Dateiverweis in Quarantäne verschieben.

Bei abgefangenen Fehlern bis zum äußeren DB-Abschluss werden nur die vom eigenen
Vorgang veröffentlichten neuen Dateien zurückgenommen; dev/inode unterscheiden
sie von einer konkurrierend hinzugekommenen Datei. Fehlgeschlagene Veröffentlichungen
und historische Vorschauen werden außerhalb MEDIA_ROOT aufbewahrt, nicht gelöscht.
Die äußeren Erstellungs-/Finalisierungstransaktionen umfassen diese Kompensation.
Das ist KEINE absturzsichere gemeinsame Datenbank-/Dateisystemtransaktion bei Stromausfall.

## Explizite Wiederherstellung aus einer Sicherung

Neues Kommando, ohne Web-Schreibroute:

```bash
# Nur prüfen. Quelle ist z. B. das entpackte media-Verzeichnis einer Sicherung.
python manage.py belegarchiv_wiederherstellen RE2026090001 --quelle /pfad/zur/sicherung/media
# Erst die ausdrückliche Option ergänzt fehlende Originalbytes/Verweise.
python manage.py belegarchiv_wiederherstellen RE2026090001 --quelle /pfad/zur/sicherung/media --anwenden
```

Erforderlich ist der bereits vorhandene ursprüngliche Prüfsummennachweis. Alle drei
Dateien werden vor einer Übernahme geprüft. Nur fehlende Dateien oder leere Verweise
werden ergänzt, niemals vorhandene abweichende Dateien/Verweise überschrieben.
Auch falsche Sicherungsbytes werden abgewiesen. Wiederholung ist ohne Änderung/Audit,
wenn nichts fehlt. Eine tatsächliche Übernahme hat einen Pflicht-Audit
`vorgang=belegarchiv_wiederhergestellt`; sein Fehlschlag rollt die Übernahme zurück.

Ohne ursprüngliche Prüfsummen kann dieses Kommando die Echtheit einer Altsicherung
nicht beweisen und verweigert die automatische Wiederherstellung. Es ist kein
allgemeiner ZIP-Import, keine Rekonstruktion aus heutigen Daten und keine Funktion
zum stillen Überschreiben korrupter Dateien. Solche Fälle bedürfen gesonderter Prüfung.

## Prüfumfang

Baseline: 515 Tests unter Django 5.2.17 erfolgreich. Neue Tests in
`test_invoice_archive.py` umfassen reale PDFs/XML, unveränderte Bytes/Timestamps,
fehlende Archive/Verweise, falsche Prüfsummen, Pfadfehler, HTTP/CSRF/erzwungene POSTs,
SMTP/.eml/Sammel-ZIP/Steuerberaterexport, Erst-Ausstellung aller wesentlichen Wege,
Wiederherstellung und Pflichtaudit-Rollback. Ein eigener TransactionTestCase verwendet
zwei DB-Verbindungen für konkurrierende Erstausstellung; der bestehende echte
Parallel-Finalisierungstest bleibt erhalten. Kein echter SMTP-Server wird benutzt.

Zwei ergänzende direkte Guardtests verhindern, dass neue redundante Schutzmaßnahmen
einen entfernten alten Entwurfs-Guard in der Gegenprobe verdecken. Die bisherigen
Integrationstests bleiben erhalten. Die entsprechende Gegenproben-Zuordnung wurde
angepasst, nicht die Schutzregel gelockert. Ältere Dateitests bekommen eigene
Medienverzeichnisse; historische Alt-XML-Fixtures werden ausdrücklich ohne neues
Siegel erstellt. Versandtext-Tests mocken nun die Leseschnittstelle mit Testbytes,
nicht einen nicht mehr zulässigen Generator-Fallback.

Neue Gegenproben: `tools/gegenproben_belegarchiv.py` (11). Sie entfernen zentrale
Wiederverwendung, Archiv-/Fremdbeleg-/Zielprüfung, Byteprüfung, Pflicht-Audits,
Rückweg-Eigentumsprüfung oder führen .eml-Neuerzeugung/ZIP-Überspringen wieder ein.
Jede muss an einer echten Assertion scheitern; Syntax-/Importfehler zählen nicht.
Zusammen mit 17 Entwurfs-, 17 Belegketten- und vier Indexproben: 49 Gegenproben.
Die PR-CI wiederholt Gesamtsuite und alle Gruppen unter Django 5.2 und 6.0.
Konkrete Testzahlen/Ergebnisse des veröffentlichten Commits stehen in den PR-Checks.

## Grenzen und ausdrücklich nicht erledigt

Keine vollständige Unveränderlichkeit aller Rechnungsmodellfelder: F-18 schützt
Dokumentdateien, nicht die gesamte Datenmodellarchitektur. Keine WORM-/GoBD-Zertifizierung,
kein Schutz gegen Administratoren mit direktem DB-/Dateisystemzugriff oder gegen
gleichzeitig manipulierte Auditdaten und Dateien. Bereits bekannte Medien-URLs werden
nicht durch eine neue geprüfte Download-Route ersetzt; Prüfsummen werden bei den
oben genannten Servicewegen/der Detailprüfung kontrolliert. Externe Dateisystem-
Manipulation ist kein unterstützter paralleler Anwendungsweg.

Mahn-Beleggraph, Abo-Zahlungsziel 0, Feldtrennung/Welle 3, UI-Download-Autorisierung
und Mail-Begleittextprüfung sind separate Aufgaben. Keine Änderung an Dashboard-Umsatz.
F-18 erfordert keine zusätzliche Migration zu 0013; auf bisher nicht aktualisierten
Installationen bleibt die schon integrierte Indexmigration 0013 auszuführen.
