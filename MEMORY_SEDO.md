# MEMORY.md – SEDO Rechnungstool

> Projektspezifisches Gedächtnis. Aktueller Review-Stand: 10.09.2026, freigegebener Härtungs- und Abnahmeplan. Historische Abschnitte darunter bleiben als Verlauf erhalten.


## Gestaltung „Licht & Blau“ (17.09.2026)

- Mirek hat Dashboard-Entwurf C ausgewählt und die Übertragung auf die App freigegeben.
- Helle Navigation, gedecktes Blau, Apricot-Akzente, sanftere Karten und Anmeldung;
  Dashboard mit Begrüßungsband und Aufgaben-Icons. Echte Werte und Routen erhalten.
- Zentrale neue CSS-Schicht einschließlich neutraler Legacy-Tokens. Warn-/Fehler-/
  Belegzustände behalten ihre Semantik; blauer Fokus auf heller Navigation.
- Keine Änderungen an Modellen, Views, Fachservices oder PDFs. Dashboard-Test sucht
  die Arbeitsbereichsklasse als Token statt als exakte Klassenzeichenfolge.
- Vollständige Regression und native Pakete vor Integration; Nachweise im PR.
  Anschließende Mac-Aktualisierung mit Sicherung vorgesehen, kein Passwortreset.


## UX-09 – Restmodule, Responsive & Accessibility (17.09.2026)

- Basis UX-08/main `3e357cb1b2c83513e84edf71add55c6cb981b6c2`;
  Branch `ux-09-restmodule-accessibility`.
- Alte `sidebar-cover`-Überlagerung entfernt. Mobile Navigation mit sichtbarem
  Menüknopf, inertem Hintergrund, Fokusumlauf, Escape und Fokus-Rückkehr;
  ohne JavaScript bleibt das Menü sichtbar. Desktop einklappbar, Administration
  und zugängliche Linknamen bleiben erhalten. Sprunglink zum Hauptinhalt.
- Globale Suche mit kontrastreichem Text, nativen Trefferlinks (Tab/Pfeile/Enter),
  Escape und lesender Statusansage. Überholte Antworten nach Eingabewechsel oder
  Schließen werden ignoriert; Suchfehler als solche sichtbar.
- Restmodul-Tabellen als beschriftete mobile Karten; native Tabellenrollen bleiben
  erhalten. Ohne JS fokussierbare Scrollbereiche mit sichtbaren Spaltenköpfen.
  Formulare/Importe/Filter beschriftet, Abo-Feld- und Formularfehler sichtbar,
  Angebotspositionen mit Labels und mobiler Darstellung wie Rechnungs-Wizard.
- Meldungen serverseitig ohne JS lesbar; dynamische Meldungen als Text statt HTML,
  ohne automatisches Verschwinden. Fokusmarkierung und reduzierte Bewegung;
  Wizard-Schrittnamen bleiben zugänglich, Prüfschritt bekommt sinnvollen Fokus.
- Zahlungs-Löschen unter Weitere Aktionen; bestätigende Seite und alle POST-/CSRF-
  Verträge unverändert. Keine Views-, Modell-, Fachservice- oder Datenänderung.
- Zehn neue gerenderte Funktionstests (zahlreiche Restmodul-Unterfälle), zusätzliche
  Browserprüfungen für Navigation/Suche/No-JS/320-Pixel-Darstellung. Vollständige
  Regression, Gegenproben und nativer Plattform-Teststand vor Integration;
  konkrete Prüfläufe im PR dokumentiert. Keine Behauptung einer WCAG-Zertifizierung.
- Kein Deployment auf dem Einsatzrechner, keine neue Releaseversionsnummer.
  Damit sind die geplanten UI/UX-Blöcke UX-01 bis UX-09 umgesetzt.


## UX-08 – Dashboard & Kundenakte (17.09.2026)

- Basis UX-07/main `9e93b7409256cca9e6251a934b4b9c346e4f31b3`;
  Branch `ux-08-dashboard-kundenakte`.
- Dashboard mit drei Arbeitseinstiegen: Entwürfe, überfällige Rechnungen und
  Mahnstufenprüfung. Direkter Sammelmahn-POST entfernt; Einstieg führt zur
  bestehenden Kandidatenprüfung in Offene Posten.
- Kundenakte mit serverseitigen Bereichen Übersicht/Rechnungen/Angebote/Objekte/
  Stammdaten. Kundenbezogene Suche, stabile 20er-Seiten und Rechnungsansichten
  Alle/Entwürfe/Offen/Überfällig. Übersicht maximal fünf Rechnungen/drei Angebote;
  Kennzahlen bleiben vollständig und schließen ausgeblendete Belege aus.
- Einheitliche Beleg-/Zahlungszustände, Entwurf führt zur Bearbeitung, sonst
  Detailansicht. Objektlöschen erst unter Weitere Aktionen und bestehender
  Bestätigungsseite. Keine direkten Zahlungs-, Versand- oder Stornoaktionen.
- Offene Summen/Kandidaten nutzen bestehende Fachregeln. Dashboard-Monatskennzahl
  unverändert berechnet, aber klar als bezahlte Rechnungen des Rechnungsmonats
  bezeichnet, nicht als Zahlungseingangsbericht.
- Gemeinsames seitenbezogenes CSS, ohne zusätzliches JavaScript bedienbar;
  synthetische Browserkontrolle Desktop/Mobil bis 320 Pixel.
- 14 neue Funktionstests; lokal 760/760 Gesamttests unter Django 5.2 bestanden.
  Vollständige CI einschließlich Gegenproben und nativer Testpakete vor Merge;
  konkrete Prüfläufe und Integration im PR dokumentiert.
- Keine Modell-/Serviceänderung oder Migration. Kein Deployment auf dem
  Einsatzrechner, keine neue Releaseversionsnummer. Nächster Block: UX-09
  Restmodule, Responsive & Accessibility.


## UX-06 – Rechnungsarchiv & Listen (17.09.2026)

- Ausgangspunkt: UX-05/main `b40dcdd371de434d2cf02286fd6efb5818e3ad26`.
- Branch `ux-06-rechnungsarchiv`: fünf gebündelte Spalten, Belegzustandsansichten,
  Suche/Jahr/Monat, 25 Belege pro Seite und Bruttosumme über alle Treffer.
- Dokumente aufklappbar; Entwürfe bieten Bearbeiten, sonst führt Öffnen zur
  abgesicherten Detailseite. Kein direkter Versand oder Korrekturstart aus der Liste.
- Legacy-Statuslinks kompatibel, Filter beim Blättern erhalten, ausgeblendete
  Belege ausgeschlossen. Zahlungszustand weiterhin aus aktiven Zahlungen.
- 14 neue Funktionstests; lokal 734 Tests unter Django 5.2.17 bestanden.
  macOS benötigt für diese Suite einen kanonischen Temp-Pfad (`TMPDIR=/private/tmp`),
  da bestehende Archivtests sonst /var und /private/var vergleichen.
- Keine Modell-/Serviceänderung, keine Migration, keine neue Releaseversionsnummer.
  PR/CI und nativer Teststand vor Integration verpflichtend; Ergebnis im PR.
  Keine Installation auf dem Einsatzrechner. Nächster UX-Block: UX-07 Rechnungs-Wizard.


## UX-07 – Rechnungs-Wizard (17.09.2026)

- Basis UX-06/main `e4f20daca83133222a259509f3ee2b6d864b380d`;
  Branch `ux-07-rechnungs-wizard`.
- Fünf Schritte erhalten. Gespeicherte Kunden/Objekte als kompakte Karten,
  Details aufklappbar; manuelle Neuerfassung explizit. Bestehende Stammdaten
  sind maßgeblich: manuelle Kopffelder ändern ausgewählte Stammdaten nicht.
- Sichere Kundenwechsel: alte Ladeantworten ignorieren, Objekt-/Kopffelder
  zurücksetzen, sichtbare Ladefehler und Wiederholung, kein Absenden während
  unvollständiger Übernahme. Vorbelegte/gebundene Formulare behalten ihre Werte.
- Rechnungsübersicht mit Adressen, Zeitraum, Positionen und Summen; Rücksprünge
  in die jeweiligen Schritte. Mobile Positionskarten und Feldbeschriftungen.
- Einziger primärer Abschluss: Rechnung erstellen. Die vorhandene send-Aktion
  heißt korrekt „Erstellen & E-Mail vorbereiten“: sie erzeugt eine .eml-Datei,
  keinen SMTP-Direktversand. Abo-Auswahl bleibt optional und bei Fehlern erhalten.
- Fachservices, Modelle und Speicherwege unverändert, keine Migration.
  Seiten-JavaScript ausgelagert; bestehender gemeinsamer Wizard unverändert.
- Lokal 746/746 Tests (12 neue); synthetische Browserprüfungen einschließlich
  Antwortreihenfolge, Ladefehler, Position hinzufügen/entfernen, HTML-Escaping,
  Zahlungsziel 0 und Mobilansichten. CI/Integration wird im PR dokumentiert.
- Kein Deployment auf dem Einsatzrechner und keine neue Releaseversionsnummer.
  Nächster UX-Block: UX-08 Dashboard & Kundenakte.

---

## Aktueller Plan – Härtung, Zustandsmodell, danach UI/UX (10.09.2026)

- Mirek hat die neue Reihenfolge vollständig freigegeben. Maßstab: verständliches Zustandsmodell und vollständiger funktionierender Arbeitsablauf; nicht nur Testzahlen.
- Papas Einsatzrechner ist erst in neun Tagen verfügbar: frühestens **19.09.2026**. Bis dahin hier weiterarbeiten; Rechnerabnahme separat offen halten, nicht durch CI ersetzen.
- Aktuelle Übersicht: **`review_v2/AKTUELLER_PLAN.md`**. Härtung vor UI/UX; die bestehende Oberfläche danach gezielt prüfen/aufwerten, nicht ungefragt komplett ersetzen.
- PR #1–6 integriert, Basis `c6cfbe08d22ad406b8c3a46bb1f3b8abd0fd6321`. Vorhandene Daten sind Testdaten; kein Echtbetrieb.
- H1: explizite Abo-Auswahl, keine vorausgewählten Vorlagen, „Alle auswählen“ erzeugt selbst nichts; ungültige/pausierte Auswahl vor der ersten Rechnung abweisen.
- H1/F-68: alle Mahn-Erstellungswege verlangen einen positiven offenen Forderungsbetrag und Fälligkeitsdatum vor heute. Keine Frist bei fehlendem Datum erfinden. Manuelle Stufenwahl bleibt bestehen; automatische 14-Tage-Schwelle unverändert.
- Gemeinsame CI für PR und main statt neuer Spezialworkflows je Merge; tatsächlicher Prüf-/Integrationsstand im Abschlussvermerk. Keine neue Modellmigration in diesem Block.
- Danach: reproduzierbarer Testbuild und Abhängigkeitsbindung, H3/Welle 3, risikobasierte Restfundprüfung, Gesamtprozessabnahme, UI/UX. Kein Mac-/Windows-/Einsatzrechnertest hier behauptet.

---

## Aktueller Stand – F-63 / Abo-Zahlungsziel 0 (10.09.2026)

- Mahnwächter / PR #5 ist in `main` integriert: `7a653da452ebf8a33ac2a08b46950b559f0b220d`. Dieser Abo-Restfix baut darauf auf. Vorhandene Daten sind laut Mirek ausschließlich Testdaten; kein Echtbetrieb und kein Mac-Deployment.
- `_recurring_payment_days` unterscheidet fehlende Werte (`None`) von 0 Tagen: Vorlage bzw. Rechnungsabstand vor Kundenwert vor `DEFAULT_PAYMENT_DAYS`. 0 bedeutet sofort fällig und wird nicht überschrieben.
- Beide Abo-Wege umgestellt: Monatslauf und Speichern einer Rechnung als Abo-Vorlage (Kundenwert bei fehlender Rechnungsfälligkeit). Keine Änderung bestehender Rechnungen/Archive, Nummernkreise, Auswahl-/Doppelregeln oder des Datenmodells.
- Beide `payment_days`-Modellfelder bleiben Pflichtfelder mit Standard 14 und erlaubtem Wert 0. `None`-Rückfälle sind defensive Codepfade, keine neue automatische Kundenvererbung leerer Formularfelder.
- 21 neue Tests, fünf neue Mutationsgegenproben; inklusive der bisherigen Suite 630 Tests und 68 Gegenproben vorgesehen. Tatsächliche Laufresultate stehen im PR-Abschlussvermerk, keine Vorwegnahme eines CI-Ergebnisses.
- Anleitung: `review_v2/welle2/abo_zahlungsziel_0.md`. Keine neue Migration, keine Versionsnummer/Installer-Erstellung. Dieser Eintrag behauptet keinen Merge oder lokale Installation des Abo-Fixes.
- Weiter separat: F-68 (manuelle Mahnfälligkeit/Offenbetrag/Stufenwahl), Welle 3/Feldtrennung, übrige Review-Funde einschließlich leerer Abo-Auswahl. Keine pauschale Erledigung aller Monatslauf-Funde.

---

## Aktueller Stand – Mahnwächter (10.09.2026)

- F-18 / PR #4 ist in `main` integriert: `50611bcdf653077ef16a89efc246725198817a20`; darauf baut dieser Block auf. Kein Mac-Deployment. Vorhandene Daten laut Mirek ausschließlich Testdaten, kein Echtbetrieb.
- Alle Mahnwege prüfen ausgestellte, nicht ausgeblendete Storno-/Korrekturfolgen zusätzlich zum Status. Folgeentwürfe blockieren das Original nicht. Keine alte Python-/Prefetch-Instanz darf die Prüfung umgehen.
- Lesende Kandidatenauswahl mit NOT EXISTS; Detailseite blendet gesperrte Mahnaktionen mit erklärtem Grund aus. Verbindlicher Guard direkt im Generator unter SQLite-Schreibsperre, weitere Graph-/Statusbedingung am UPDATE.
- Mahn-PDF, Belegzeile, Status und Einzel-Pflichtaudit in einem Vorgang. Exklusive Datei-Veröffentlichung; Fehler am DB-Abschluss nehmen nur eigene neue Dateien in Quarantäne zurück. Bestehende PDFs nicht überschreiben. Keine gemeinsame Dateisystem-/DB-Crashgarantie.
- `auto` bestimmt Fälligkeit und Stufe erneut innerhalb der Sperre. Manuelle Fälligkeits-/Stufenregeln (F-68) bleiben bestehen. Kein E-Mail-Versand durch diese Services.
- Lokal 609 Tests (47 neu) und 63 Mutationsgegenproben (14 neu) bestanden; zusätzlich drei koordinierte Datei-SQLite-Gegenläufe. Konkreter PR-/CI-/Integrationsstand folgt im Abschlussvermerk; nicht mit einer lokalen Installation verwechseln.
- Anleitung/Prüfgrenzen: `review_v2/welle2/mahnwaechter.md`. Keine neue Modellmigration über 0013 hinaus. Abo-Zahlungsziel 0, Feldtrennung und übrige Review-Funde bleiben separat.

---

## Aktueller Stand – Restabsicherung F-18 (10.09.2026)

- Basis: `main` `cafb1822834ccdcf4aa03677c8d174250f79da5c`; PR #1–3 integriert, kein Mac-Deployment. Vorhandene Daten laut Mirek ausschließlich Testdaten, kein Echtbetrieb.
- Beauftragt: F-18 zuerst. Keine Änderung am Mahnwächter, Abo-Zahlungsziel 0 oder an der geplanten Feldtrennung.
- Ausgestellte Belegdateien werden nicht mehr neu berechnet/überschrieben. Generatorstandard ist lesende Wiederverwendung; Erstausstellung explizit intern. Fehlende Dateien führen zum Fehler, nicht zum Generator-Fallback.
- Rechnungsmail, .eml-Helfer, GWG-Sammel-ZIP und Steuerberater-PDF-Export verwenden einmal gelesene Originalbytes. Fehlende Anhänge werden nicht still übersprungen. Entwürfe bleiben bearbeitbar.
- Neue Originale: flache SHA-256-/Größen-/Pfad-Metadaten in einem Pflicht-Audit. Keine neue Migration. Altbelege ohne Siegel bleiben lesbar, werden aber nicht rückwirkend als prüfsummengesichert ausgegeben.
- Neues explizites Kommando `belegarchiv_wiederherstellen NUMMER --quelle MEDIEN-SICHERUNG`: Trockenlauf; nur mit `--anwenden` fehlende Originalbytes/leere Verweise nach ursprünglichem SHA-256 ergänzen. Kein Überschreiben abweichender vorhandener Dateien. Ohne ursprüngliches Siegel keine automatische Altbeleg-Wiederherstellung.
- Neue Originaldateien werden exklusiv per Hardlink veröffentlicht; kein überschreibender Fallback. Dateikompensation berücksichtigt eigene Veröffentlichungen und äußere Transaktionsfehler. Kein gemeinsamer DB-/Dateisystem-Crashschutz behauptet.
- Neue Regressionstests und elf echte Archiv-Gegenproben; inklusive vorhandener Gruppen 49 Gegenproben. Exakte Abschlusszahlen und Veröffentlichungs-/CI-Stand siehe PR und Prüfbericht, nicht mit bereits erfolgter Integration verwechseln.
- Details/Anwendung/Grenzen: `review_v2/welle2/f18_belegschutz.md`. Kein echter Versand, keine Originaldatenbanken geändert. Offene Review-Funde bleiben separat.

---

## Aktueller Stand – Etappe 7 (Indizes)

**Betriebsstatus laut Mirek:** Noch kein Echtbetrieb; sämtliche vorhandenen Daten sind Testdaten. Historische Bezeichnungen als Live-/Produktivbestand weiter unten sind überholt.

- PR #1 (Etappen 4/6) und PR #2 (Etappe 5) sind in `main` integriert: `cd42c2d6682cf6b410ce536be67a2a58a869811a`. Kein lokales Desktop-Deployment.
- Die Bestandsprobe auf Kopien der Haupt- und GWG-Datenbank ist abgeschlossen: 0 bzw. 9 Rechnungen, keine A–D-Befunde, keine Reparatur nötig. Das Reparaturverfahren wurde separat mit markierten synthetischen Fehlerfällen geprüft.
- Etappe 7 ergänzt **0013_invoice_query_indexes**, genau zwei AddIndex-Operationen. Keine Felder, Constraints, Statuswerte oder Belegdateien ändern; kein Tabellen-Neuaufbau.
- Bewusste Präzisierung des alten Plans: `invoice_status_due_idx(status, due_date)` bleibt. Der redundante Einzelindex auf status entfällt. Stattdessen `invoice_active_due_status_idx(due_date, status) WHERE deleted_at IS NULL` für die tatsächliche NOT-IN-Mahnabfrage. Abfragen und Geschäftslogik bleiben unverändert.
- Nachweise: 15 neue Tests, lokaler Gesamtlauf **515 Tests grün**. Vier neue Index-Gegenproben; bestehende Entwurfs-/Belegketten-Gegenproben weiterhin Teil der CI. Keine timingabhängigen Testbehauptungen.
- Auf beiden vorhandenen Testbestandskopien: 0012 → 0013 → 0012 → 0013 erfolgreich, fachliche Tabellen und Tabellen-Schema unverändert; wiederholtes migrate byteidentisch. Originaldateien nicht überschrieben.
- Messwerkzeug: `tools/benchmark_invoice_indexes.py` erzeugt ausschließlich eine neue synthetische Temp-DB. SQL-Pläne, Ergebnis-Hashes, Lese-/Schreibzeiten und belegte Seiten werden protokolliert. Laufzeiten sind keine Garantie für die ganze Anwendung; Indizes kosten Platz und Schreibarbeit.
- Umsetzung und Anwendung: `review_v2/welle2/etappe7_umsetzung.md`. Der nachfolgende PR/CI-Nachweis dokumentiert den Veröffentlichungsstand; diese Etappe ist hier nicht als in main integriert oder auf dem Mac installiert behauptet.
- Etappen 1–7 sind damit im jeweiligen Implementierungsstand umgesetzt. Welle 3/Feldtrennung und die übrigen Review-Funde bleiben ausdrücklich separat; keine pauschale Freigabe aller offenen Fehler.

---

## Umsetzung 09.09.2026 – Welle 2, Etappe 5 (Belegketten)

Basis: geprüfter Etappe-4/6-Commit `6e0f7ae`; eigener Folgebranch `review-v2-etappe5-belegketten`.
**Kein Merge, kein Produktiv-Deploy und KEINE Bestandsreparatur auf der Live-DB ausgeführt.**

- `python manage.py belegketten_pruefen` liest nur den Bestand; `--json` liefert Schema 1.
- `--reparieren` übernimmt belegbare Statuskorrekturen in A/B und falsch bezahlte Stornos (D). Ein Lauf ist vollständig atomar; ein fehlgeschlagener Pflicht-Audit oder Constraint rollt ALLE Änderungen dieses Laufs zurück.
- Aktiv bedeutet wie in Etappe 4/6: Folgebeleg ist nicht ausgeblendet und nicht Entwurf. Storno hat Vorrang; gleichzeitig vorhandene Korrektur bleibt als manueller Prüfhinweis samt beiden Nummern sichtbar.
- Gruppe B berechnet den endgültigen Zahlstatus zunächst rein lesend, dann ein guarded UPDATE und ein Audit mit tatsächlichem von/nach/anlass. Kein ungeschütztes Zwischen-save().
- Gruppe C (XML-/ZUGFeRD-Verweise) und Entwürfe älter als 30 Kalendertage (D, ab created_at) werden ausschließlich gemeldet. Keine Dateien öffnen, löschen oder verschieben, keine Nummern vergeben. EN-/Entwurfswidersprüche werden niemals automatisch ausgestellt.
- SQLite-Schreibsperre vor Aufnahme der Befunde; Status-/Revisions-Guard vor jeder Änderung. Nicht-SQLite-Reparatur wird ausdrücklich abgewiesen, statt eine ungeprüfte Sperrwirkung zu behaupten.
- Sicherheitsseite: rein lesende Zeile „Belegketten in Ordnung“ zählt A/B; CLI zeigt zusätzlich C/D. Kein Reparaturknopf und keine neue HTTP-Schreibroute.
- Neue Tests: `sedo/tests/test_belegketten.py` (60). Gesamtsuite lokal: 500 grün, neue Gegenproben 17/17 sowie bisherige Entwurfsgegenproben 17/17 belegt. Echte zweite SQLite-Verbindung im separaten Prozess, query_only-Trockenlauf, Ganzlauf-Rollback und GWG-Constraint-Konflikt geprüft.
- Neue Anleitung: `review_v2/welle2/etappe5_umsetzung.md`. CI prüft als gestapelter PR gegen Etappe 4/6 zusätzlich Django 5.2 und 6.0; Ergebnisse in den PR-Checks.
- Welle 2: Etappen 1–6 im Review-Code umgesetzt. Etappe 7 bleibt optional; Bestandslauf erst nach eigener Sicherung/Freigabe. Die historischen Aufgabenstände weiter unten sind nicht der aktuelle Implementierungsstand.

---

## Umsetzung 09.09.2026 – Welle 2, Etappe 4 + EN (Etappe 6)

Arbeitsbranch: `review-v2-etappe4-entwurf`, Basis des GitHub-Snapshots `c73050f`.
**Nicht auf main zusammengeführt, nicht auf den lokalen Produktivrechner ausgerollt.**

- Neue Entwürfe bekommen EN und ausschließlich ein Wasserzeichen-PDF. Finalisieren vergibt RE; historische RE-Entwürfe behalten ihre Nummer.
- Neue Routen: `invoice_edit` und `invoice_finalize`. Finalisieren hat eine POST-Bestätigungsseite; Vormonatsdatum bleibt ohne ausdrücklich gesetztes Häkchen erhalten. Zahlungsziel (auch 0 Tage) und Leistungszeitraum bleiben bei Datumsverschiebung erhalten.
- Editieren akzeptiert nur gespeicherte Kunden/zugehörige Objekte und verändert weder Belegart noch Herkunft. Guarded UPDATE und Formularrevision sichern paralleles Bearbeiten/Finalisieren/Verwerfen ab.
- Korrekturentwurf prüft das Original nochmals; erfolgreicher Abschluss markiert es erst dann als korrigiert. Storno/Korrektur/Finalisieren schreiben verpflichtende Status-Audits.
- Verwerfen eines Angebotsentwurfs gibt das Angebot transaktional frei. Beide Pflicht-Audits enthalten beide Nummern; ein Auditfehler rollt den ganzen Vorgang zurück.
- Fehlerkompensation für PDF/XML-Dateien: vorherige Dateien werden bei abgefangenen Fehlern wiederhergestellt; neue sowie alte Entwurfs-XMLs werden ohne Löschen außerhalb von MEDIA_ROOT verwahrt. Pfad: Geschwisterordner `.media_quarantaene` (bei abweichendem Mediennamen entsprechend). **Keine gemeinsame absturzsichere DB-/Dateisystemtransaktion bei Stromausfall.**
- Isolierte Prüfung Python 3.13 / Django 5.2.17: **440 Tests grün (387 Bestand + 53 neue), 17/17 Mutationsgegenproben rot wie erwartet**, `makemigrations --check --dry-run`: keine Änderungen. Neue Tests: `sedo/tests/test_draft_workflow.py`; reproduzierbare Gegenproben: `tools/gegenproben_entwurf.py` (ändert ausschließlich temporäre Quellkopien).
- Chromium-Smokechecks: neue Positionszeilen, kundengefilterte Objektauswahl und nicht vorausgewähltes Vormonats-Häkchen. Kein JavaScript-Laufzeitfehler.
- Live-DB, Betriebsmedien und Sicherungen wurden nicht geöffnet oder verändert. Tests mit In-Memory-DB und temporären Pfaden; kein Versand. `main`, GWG-Testinstanz und Dashboard-Umsatz unverändert.
- Etappe 5 (Bestandsreparatur), Etappe 7 (optionale Indizes), Feldtrennung und F-18 für bewusstes Neuerzeugen ausgestellter Dateien bleiben offen. Bereits bestehende Entwürfe werden nicht pauschal angefasst; alte XMLs werden erst beim expliziten Bearbeiten/Neuerzeugen des jeweiligen Entwurfs aus den Medien genommen.

---

## PROJEKTSTATUS

- **Aktuelle Version:** **v2.3.0** – `sedo_rechnungstool_v2/` ist seit 17.08. der EINZIGE
  Ordner: Code + Mireks Live-Daten (44 Kunden, API-Key – am 17.08. aus „…_3_22_0 2“
  migriert, DB-Integrität geprüft) + Desktop-App in `dist/` + `Berichte-Archiv/`.
  ALLE alten Versionsordner liegen im Papierkorb (Mireks Freigabe 17.08.), darunter
  auch `…_3_21_0_Clean` MIT Mireks eigenem .git-Repo!
  (Code-Stand = beta 3.23.1; 142 statt 184 Dateien: alle VERSION_*.md und Alt-Berichte raus,
  stattdessen kompakte CHANGELOG.md). Neues Namensformat: `sedo_rechnungstool_v[MAJOR]_[MINOR]`.
  Mireks Live-Instanz bleibt vorerst der Ordner „…_3_22_0 2“ (Fußzeile dort ebenfalls auf v2.0)
  (+ ZIP; im Ordner liegen die Berichte v3.22 UND v3.23; v3.21-Clean unangetastet).
  **Mireks Live-Instanz:** Ordner `SEDO_Rechnungstool_Django_v1_0_beta_3_22_0 2` (ZIP-Entpackung,
  enthält seine Daten/API-Key) – v3.23-Code am 14.08. per rsync eingespielt, Daten unberührt
- **Status:** Härtung nach Mireks zweitem, tiefem Review abgeschlossen: alle bestätigten
  Geschäftslogik-Bugs behoben (Storno-/Korrektur-/Angebots-Sperren, Mahnstufen,
  Cross-Customer, Zahlungsimport-ID, Datei-Atomarität, Monatslauf-Idempotenz,
  NumberRange-Nummernkreise, Angebots-Soft-Delete, DEBUG=0+WhiteNoise) –
  **217 Tests grün unter Django 6.0.8** (52 neue), Migrationen 0007–0011
- **Letzter Stand:** v3.23-UI-Feinschliff im Browser verifiziert (Kundensuche mit Trefferliste,
  ⓘ-Tooltips, 1 Startposition, Business-Design) + Muster-Angebots-PDF geprüft, 14.08.2026 spät
- **GWG-Testphase (Mails M. Assmann, 09/2026) – Rückfragen beantwortet:**
  Testrechnungen an **ars-399662000@aareon.com** (Kennung gilt NUR fürs Testsystem;
  Produktiv-Adresse kommt später von GWG). **Kein neues Mapping nötig** – die
  GWG-Änderungen sind rein intern. **Bestandsbetreuer muss NICHT auf die Rechnung/XML**
  → keine Codeänderung. Wirtschaftseinheiten je Betreuer:
  **Neitzel 3501/3601/3801 · Radtke 2001/2101/3201 · Lammel 2501/2513/2517**
  (WE → Abrechnungseinheit über unser vorhandenes Mapping auflösen).
  GWG prüft nach Versand, ob die Rechnungen bei den Mitarbeitern ankommen.
  Scharfe Umstellung **01.01.2027**, bis dahin Sammelrechnung per Mail wie bisher.
  **Mapping-Blocker GELÖST:** Mirek fand die Datei im alten MVP-Ordner
  (Downloads/SEDO PROGRAMM/…/input/Mapping_SEDO_ARS.xlsx, Stand 06.05.2026) –
  **1.956 Zeilen am 02.09. in die v2-Instanz importiert** (App-Leselogik, transaktional);
  Sicherungskopie: `mapping_sedo_ars_stand_2026-05-06.xlsx` im Projektordner.
  **WE→Abrechnungseinheiten (je 3 Leistungsarten Reinigung/Winterdienst/Heckenschnitt):**
  Neitzel: 3501=Quittenweg 4/6/14 · 3801=Resedaweg 11 li./15 re./17 li. ·
  Radtke: 2101=Heckenweg 1/1 · 3201=Nelkenweg 1/1–1/3 ·
  Lammel: 2501=Kirschweg 1+3 · 2513=Kirschweg 32–38 (8 Einheiten).
  ⚠️ **3 der 9 WEs fehlen im Mapping (Stand Mai): 3601 (Neitzel), 2001 (Radtke),
  2517 (Lammel)** – Assmann melden/aktuelle Datei erbitten. Pro Betreuer ist trotzdem
  mind. eine WE testbar. **Seller-ID weiter unbeantwortet** – Testsystem wird zeigen,
  ob sie zwingend ist.
  **02.09.: 6 Testrechnungen ERZEUGT** in separater Test-Instanz
  `sedo_testinstanz_gwg/` (Minimal-Kopie von v2 mit EIGENER DB; Live-Nummernkreis
  unberührt; Ordner trägt NUR_TESTINSTANZ.md): RE2026090001–06 für Heckenweg 1/1,
  Kirschweg 1, Kirschweg 32 re., Nelkenweg 1/1, Quittenweg 14, Resedaweg 11 li. –
  je 3 Positionen (Reinigung 58/Winterdienst 45/Heckenschnitt 120 € Testpreise,
  pauschal→C62), brutto je 265,37 €. XML geprüft: BT-1, ShipTo-ID=Abrechnungseinheit,
  BT-130 C62, Leistungsart-Notes, USt-ID DE314938054 ✓; SellerAssignedID bewusst
  leer (Test zeigt ob nötig); BT-154 nicht enthalten (Monatslauf setzt keine
  Leistungstexte – separat testen falls gewünscht).
  **02.09.: Testrechnungen VERSENDET** – Mirek hat die Mail mit allen 6 ZUGFeRD-PDFs
  an ars-399662000@aareon.com geschickt (Entwurf lag als .eml in der Test-Instanz);
  Info-Mail an Assmann hinterher. Jetzt: auf GWG-Rückmeldung warten
  (Zuordnung bei Neitzel/Radtke/Lammel angekommen? Seller-ID nötig?).
  Hinweis Test-Client in v2: DEBUG=0 → für Shell-Läufe DJANGO_ALLOWED_HOSTS=testserver setzen.
- **Nächste offene Schritte:** Rückfragen an Assmann (Entwurf liegt vor) →
  GWG-Testrechnungen aus `sedo_rechnungstool_v2/`; parallel Produktionsstart +
  Live-Test mit Papa. Der BT-154/BT-130-Abgleich läuft über diese Testphase.
---

## OFFENE AUFGABEN

- [ ] **SENIOR-CODE-REVIEW v2 – ABARBEITUNG LÄUFT (Stand 09.09.2026): Welle 1 komplett,
      Welle 2 zu 3 von 7 Etappen. → EINSTIEG FÜR EINEN NEUEN CHAT:
      `review_v2/UEBERGABE_WELLE_1_UND_2.md`** – dort steht alles: Git-Stand, Arbeitsregeln,
      getroffene Entscheidungen, offene Etappen, GWG-Stand, Aufräumliste.
      Detailplan der nächsten Arbeit: `review_v2/welle2/etappenplan_4_bis_7.md`.
      Offene Funde aus dem Etappe-3-Review: `review_v2/welle2/offene_funde_etappe3_review.md`.
      **Suite: 387 Tests grün** (vorher 245), 14 Commits auf Branch `review-v2-fixes`,
      alles migrationsfrei. **Nächster Schritt: Etappe 4** – der Entwurf bekommt Ausgänge
      (Bearbeiten + Finalisieren; heute ist ein Entwurf eine Sackgasse ohne Weg zurück).
      **GWG:** Testrechnungen wurden erfolgreich eingelesen; Rechnungstext angepasst
      (Commit `12a2c3e`), drei neue Testrechnungen und ein Antwortentwurf liegen bereit,
      **nicht versendet**. **F-05 (PDF/A-3) entschärft** – Aareon liest die XML auch ohne
      PDF/A-3. **Aufräumen offen:** 91 Test-Backups vom 09.09. in `backups/` (Freigabe nötig).

- [x] ~~**Stand 08.09.2026: Welle 1 komplett** (überholt, Fortschritt siehe oben)~~
      **Code liegt auf dem Git-Branch `review-v2-fixes`** im Ordner `sedo_rechnungstool_v2/`.
      Am 04.09. wurde dort ein Git-Repo angelegt (vorher gab es keins mehr); Nullpunkt-Commit
      `e9df5b5` auf `main` ist der unveränderte Ausgangsstand. `.gitignore` schließt `data/`,
      `backups/`, `media/`, `logs/`, `dist/`, `build/` und die venvs aus – die Live-DB ist
      **nicht** im Repo. Rückrollen: `git checkout main` bzw. einzelne Commits per `git revert`.
      **Suite: 335 Tests grün** (vorher 245); alle neuen Regressionstests in
      `sedo/tests/test_review_v2.py`, benannt nach den Fundnummern.
      **Erledigt (15 Funde aus dem Bericht + 3 neue):**
      F-38 · F-17 · F-16 · F-12 · F-02 · F-03 · F-31 · F-81 · F-09 · F-04 · F-53 · F-24 ·
      F-11 · F-66 · F-48 sowie NEU-1/NEU-2/NEU-3 (unten).
      **Arbeitsweise:** Vor jedem Block eine Workflow-Analyse mit je einem Fachagenten und
      einem Skeptiker pro Fund, dazu Musterschauen nach derselben Fehlerklasse an anderen
      Stellen. Zu jedem Fix eine Gegenprobe: Fix zurücknehmen, Tests müssen rot werden.
      **Spürbare Verhaltensänderungen, die Papa kennen muss:**
      (1) Der GWG-/Abo-Monatslauf **bricht ab**, wenn Menge, Preis, Rechnungsdatum,
      Zahlungsziel oder Testlimit fehlen oder unlesbar sind – statt still 10,00 €/1,00/heute
      einzusetzen. (2) Der Django-Admin ist für Rechnungen, Angebote, Zahlungen, Mahnungen und
      das Protokoll **nur noch lesend**; Kassenbuch bleibt änderbar (nur Löschen gesperrt),
      weil die App keine Korrekturbuchung kennt. Damit sind **Rechnungsentwürfe endgültig
      unänderbar** – Weg: ausblenden + neu anlegen, die Nummer ist dann verbraucht.
      (3) Ausblenden von Rechnung/Angebot/Zahlung **unterbleibt**, wenn sich der Vorgang nicht
      protokollieren lässt. (4) Bankimporte weisen Zeilen ohne lesbares Buchungsdatum ab und
      listen sie einzeln auf. (5) „Auswahl – Mahnung" ohne Häkchen mahnt nicht mehr alle.
      **SICHERHEIT, noch offen:** Die 284 vorhandenen Backup-ZIPs in `backups/` sind weiter
      belastet – sie enthalten gültige Session-Keys (14 Tage) sowie Bank-PIN und Claude-Key als
      Rest-Bytes. Der Fix wirkt erst ab dem nächsten Backup. `manage.py changepassword admin`
      entwertet die Session-Keys aus allen Altbackups auf einen Schlag. **Offene Frage an
      Mirek:** Ist zwischen dem 25.08. und dem 08.09.2026 ein Backup-ZIP herausgegangen?
      **NEUE FUNDE, die NICHT im Bericht stehen** (aus den Musterschauen) – erledigt:
      NEU-1 `sedo/audit.py`: write_audit verschluckte jeden Schreibfehler an ~44 Aufrufstellen;
      beim Ausblenden lief es VOR der Aktion, der Nachweis verschwand also, während die Aktion
      durchlief. NEU-2 `views.parse_date_for_view`: stiller Heute-Fallback im Massenlauf.
      NEU-3 `test_security_hardening.py`: der Test für „keine Secrets im ZIP" prüfte faktisch
      nichts, weil die Test-DB im Speicher liegt.
      **NEUE FUNDE, noch offen** (in keinem Bericht, beim Weiterarbeiten mitnehmen):
      · Storno-/Korrektur-PDF nennt die Ursprungsrechnung **im sichtbaren Text nicht**
      (`rechnungslauf_core.py:1006/1100` versprechen sie, `intro_text` wird nie gefüllt).
      · `recurring_monthly_generate` hat dasselbe Muster wie F-66: leere Auswahl = alle Vorlagen,
      entgegen der Zusage in `recurring_monthly.html:21`.
      · `SEDO.vbs` migriert ab dem zweiten Start nie und installiert keine Pakete – ein Update
      mit neuer Migration liefe dort still gegen ein altes Schema.
      · `app.css:614` blendet bei gepinnter Seitenleiste Einstellungen/Backup/Sicherheit/Admin/
      Abmelden komplett aus – auf reinen Touch-Geräten unerreichbar.
      · `AppSetting` ist im Admin ohne Schreibschutz registriert; ein Superuser kann den
      Protokoll-Fehlervermerk am Protokoll vorbei löschen.
      · Von 86 Exception-Handlern im Projekt sind 16 reines `pass` und 57 ohne jede Meldung –
      die häufigste Fehlerklasse überhaupt (u. a. `settings.py:60-73` SECRET_KEY-Persistierung,
      `numbering.py:40-43` Nummernkreis-Fallback auf 1).
      **NÄCHSTER SCHRITT: Welle 2** – die übrigen hoch-Funde: F-07 (Statusfeld kodiert
      Zahlstatus UND Lebenszyklus), F-13/F-14 (Entwürfe, Ausblenden), F-18 (archivierte Dateien
      werden überschrieben), F-15, F-06, F-08, F-10, F-01, F-05.
      **Vor dem Ausliefern:** Das gebündelte Programm unter `dist/` trägt noch den alten Code –
      `build_mac.command` bzw. `BUILD_WINDOWS.bat` neu laufen lassen. Die Starter aus dem
      Quellordner (`SEDO.app`, `START_HIER.command`) sind sofort aktuell.

- [x] ~~**SENIOR-CODE-REVIEW v2 ABGESCHLOSSEN (04.09.2026) – Funde jetzt gemeinsam abarbeiten.**~~ (Ergebnis siehe oben)
      Ergebnis: **125 Funde** (0 kritisch, 18 hoch, 54 mittel, 53 niedrig) aus 143 verifizierten
      Rohfunden, 11 Funde geprüft und verworfen. Methode: 9 Prüfdimensionen, je Fund zwei
      unabhängige Gutachter (Reproduktion + Skeptiker), 16 strittige Fälle vom Hauptreviewer
      entschieden, ausschließlich lesend geprüft.
      **Einstieg für einen neuen Chat: `review_v2/UEBERGABE_CODE_REVIEW_V2.md`** (Kontext,
      Arbeitsregeln, Fundübersicht als Tabelle, Vorschlag Welle 1).
      Vollständiger Bericht mit Beleg/Szenario/Fix je Fund: `review_v2/bericht_senior_review_v2.md`
      (204 KB, abschnittsweise lesen). Maschinenlesbar: `review_v2/verifiziert/_args_synthese.json`.
      Vier Baustellen: (1) Statusfeld kodiert Zahlstatus UND Lebenszyklus – „korrigiert"/„entwurf"
      werden von OP-GET, Zahlung und E-Mail-Versand überschrieben; (2) formale Gültigkeit der
      E-Rechnung – BT-25 an XSD-widriger Stelle, kein PDF/A-3, Stornos verlieren ARS-Felder;
      (3) Datenschutz vor der KI – „nur Leistungsdaten" ist nur Prompt-Anweisung, Web-Speech
      schickt Audio an Google; (4) Betrieb im Tunnel – runserver, kein Brute-Force-Schutz,
      Backup mit gültigen Session-Keys, kein Restore-Weg.
      Drei destruktive Importpfade mit je einer Zeile Prüfung entschärfbar: Kundenimport
      überschreibt mit Leerwerten (F-12), Mapping-Import löscht bei 0 Zeilen (F-17),
      GWG-Monatslauf setzt 10,00 € bei leerem Preis (F-16).
      Nicht erzeugt: der Abdeckungs-Kritiker (`kritik_abdeckung_v2.md`) – Guthaben war
      aufgebraucht; die Abdeckungsnotizen stehen in Abschnitt 5 des Berichts.
- [ ] **ZUGFeRD-Vollvalidierung (letzter Review-Punkt!):** Mirek installiert Java
      (Temurin), danach Mustang-Validator herunterladen (~60 MB, vorher ansagen),
      Musterrechnung erzeugen und EN16931-Prüfung lokal fahren


- [ ] **Produktionssystem einrichten (jetzt mit v3.22!):** Startskript ausführen
      (installiert requirements automatisch, seit v3.22 inkl. whitenoise; erstes
      Diktat lädt Whisper-Modell ~460 MB einmalig), **neues Admin-Passwort notieren**
      (wird beim ersten Start einmalig angezeigt; Bestandssystem:
      `manage.py changepassword admin`), `.env` nach `.env.example` anlegen und
      **Claude-API-Key** in Einstellungen → KI-Assistent eintragen
- [ ] Beim Einspielen der 44-Kunden-Sicherung: Migrationen 0007–0011 laufen
      automatisch; falls Migration 0009 wegen doppelter aktiver
      GWG-Monatsrechnungen fehlschlägt → melden, gezielt bereinigen
- [ ] Live-Test mit Papa: Diktat (Whisper), Angebots-Generator, Magic-Tone, Plausi-Check
- [ ] **Windows-Desktop-Starter auf Papas Rechner verifizieren** (SEDO.vbs + Verknüpfung –
      auf dem Mac getestet, unter Windows nur geschrieben; beim Erststart danebensitzen)
- [ ] **GWG-Testphase (Mail Assmann 02.09.2026)** – ersetzt den bisherigen Tönnies-Punkt:
      1) Rückfragen-Mail an Assmann senden (Entwurf liegt vor: aktualisierte Mapping-Datei
      mit Bestandsbetreuer-Zuordnung, Abrechnungseinheiten je Bestand Neitzel/Radtke/Lammel,
      Bestätigung Empfänger-ID 399662000 + gilt sie produktiv?, SEDO Seller-ID,
      Bestandsbetreuer in XML nötig ja/nein) ·
      2) Testkunde „GWG Reform (TEST)" mit E-Mail ars-399662000@aareon.com anlegen ·
      3) neue Mapping-Datei importieren (Import ist transaktionssicher) ·
      4) Testrechnungen je Betreuer-Bestand erzeugen – dabei werden BT-154
      (Leistungsbeschreibung) und die korrigierten BT-130-Einheiten (m²→MTK, Monat→MON;
      vorher immer H87) im Testsystem mitvalidiert – vorab ZUGFeRD-Validator, dann per
      SMTP an die Testadresse · **Scharf ab 01.01.2027**, bis dahin Sammelrechnung
- [ ] Nach dem ersten echten Durchlauf prüfen, ob 6 Schritte beim Angebot für Papa passen oder ob Objekt/Angebotsdaten zusammengelegt werden sollten
- [x] ~~Duplikat-Ordner gelöscht~~ (Freigabe 14.08., ~645 MB frei). **Wichtig:** Die darin
      gefundenen Entwicklungs-DBs vom 26.07. – inkl. **44 importierten Kundendatensätzen**
      und einem Angebot – liegen gesichert in `Sicherung_Duplikat-Datenbanken_2026-08-14.zip`.
      Beim Produktionsstart von v3.21 prüfen, ob die 44 Kunden aus dieser Sicherung
      übernommen werden sollen (Datei als data/sedo_web.sqlite3 einlegen, Migrationen laufen automatisch)
- [ ] Erste KI-Angebote kritisch prüfen und die KI-gelernten Katalogtexte einmal sauber ziehen (Leistungskatalog-Seite)
- [ ] Aufräumen: In den Paketen v3.19.0 und älter liegen versehentlich Test-Backups im Ordner `backups/` (nur Ballast, ab v3.20.0 ausgeschlossen)
- [ ] Realistische €/m²-Preisbandbreiten von Papa erfragen (Feinjustierung Plausi-Check, aktuell 0,05–20 €)
- [x] ~~Testrechnung bei GWG/Tönnies einreichen~~ (aufgegangen in der GWG-Testphase oben; Eingangsweg bleibt „E-Rechnungen", nicht Capture)
- [ ] **Einstellungen → ARP Seller-ID** eintragen (Pflichtfeld! Sonst lehnt ARP ab)
- [ ] **Kunden GWG → Feld „Aareon/ARP Rechnungsempfänger-ID"** mit GWG-Portal-ID befüllen
- [x] ~~Test-MEDIA_ROOT isolieren + Tausenderpunkte im Web-UI~~ (erledigt 14.08., inkl. |unlocalize-Audit für IDs in Formularen/JS)
- [ ] Perspektive (zurückgestellt/verworfen): Voice-to-Action nur bei echtem Bedarf;
      statt KI-Preisvorschlägen ggf. lokale Kundenhistorien-Anzeige im Angebot
- [ ] Gezielte Business-E2E-Tests (Rechnung → PDF → ZUGFeRD → Zahlung → Mahnung → Storno) – Empfehlung aus dem externen Review
- [ ] Nächster Sprint nach Testbestätigung planen

---

## GETROFFENE ENTSCHEIDUNGEN

| Datum | Entscheidung | Begründung |
|-------|-------------|-----------|
| März 2026 | Eigenes Tool statt WISO-Wechsel | WISO Desktop kann BT-75 nicht sauber als Leistungsort setzen |
| März 2026 | Django als Framework | Python-Stack, lokal lauffähig, kein Cloud-Abo |
| Mai 2026 | ZUGFeRD CII als XML-Format | Aareon/GWG-Anforderung |
| Mai 2026 | ShipToTradeParty/ID = Spalte A der Mapping-Datei | Aareon-Logik: Schlüssel für automatische Kontierung |
| Mai 2026 | Leistungsart-ID positionsweise wenn mehrere Leistungsarten | Aareon-Vorgabe aus E-Mail |
| Juli 2026 | KI-Assistent: Cloud-API statt lokal | Beste Qualität; DSGVO-unkritisch, da nur Leistungsdaten übertragen werden |
| Juli 2026 | KI-Scope: nur Positionen per Diktat, Kundendaten bleiben lokal | Mireks Vorgabe – Kundendaten verlassen das Haus nicht |
| Juli 2026 | Kurztexte = professionelle Positionsbezeichnung (kein neues Modellfeld) | Keine Migration nötig, OfferItem.description reicht |
| Juli 2026 | KI erfindet nie Preise – fehlende Angaben nur als Hinweis | Vertrauen/Fehlervermeidung |
| Juli 2026 | Anbieter von OpenAI auf **Claude (Anthropic)** gewechselt | Mireks Präferenz – arbeitet lieber mit Claude |
| Juli 2026 | Textauswertung über Claude Tool-Use (input_schema, erzwungen) statt JSON-Mode | Liefert bereits geparstes JSON zurück, robuster als String-Parsing |
| Juli 2026 | Spracherkennung läuft im Browser (Web Speech API), nicht über Cloud-API | Claude bietet keine Transkriptions-API; spart zusätzlich einen Anbieter/Endpoint |
| Juli 2026 | **Paradigmenwechsel v3.18:** KI generiert vollständige Angebote statt 1:1-Übernahme | Mireks Vorgabe: grobe Ansage reicht, KI baut logische Positionen + Texte |
| Juli 2026 | Preise: KI darf Gesamtpreise aufteilen UND fehlende Preise schätzen – immer markiert (`preis_art`) | Mireks Antwort „2 und 3" auf die Preislogik-Frage |
| Juli 2026 | Selbstlernender Leistungskatalog statt vordefiniertem Katalog | Mireks Idee: neue Leistung → KI erzeugt Text einmalig, danach Wiederverwendung |
| Juli 2026 | Bei bekannter Katalogleistung ersetzt der gespeicherte Text den KI-generierten | Konsistenz: jedes Angebot klingt gleich („nach SEDO") |
| Juli 2026 | Fehlende Angaben: branchenübliche Annahmen treffen + einzeln als Annahme ausweisen | Papa bekommt fertiges Gerüst, bestätigt nur noch |
| Juli 2026 | UI-Runde: Look (Navy/Orange, Karten) bleibt, nur Struktur wird überarbeitet | Mireks Entscheidung – geringeres Risiko, schneller einsatzbereit |
| Juli 2026 | Echter Schritt-Assistent (ein Abschnitt pro Bildschirm) statt einer Seite mit Abschnitten | Mireks Entscheidung – am wenigsten überfordernd für Papa |
| Juli 2026 | KI-Assistent als Schritt 2, direkt nach der Kundenauswahl | Mireks Entscheidung – das ist Papas Haupteinstieg |
| Juli 2026 | UI zuerst Angebot/Rechnung, Dashboard & Listen danach | Mireks Entscheidung – der tägliche Prozess zuerst |
| Juli 2026 | Assistent blockiert nicht clientseitig; Django bleibt die einzige Validierung | Doppelte Prüflogik wäre fehleranfällig; stattdessen Auto-Sprung zum Serverfehler |
| Juli 2026 | KI-Schritt bleibt eigener Schritt (nicht mit Positionen verschmolzen) | Mireks Entscheidung |
| Juli 2026 | Kalkulationsangaben werden komplett verworfen, nicht intern angezeigt | Mireks Entscheidung – dient nur der Preisfindung |
| Juli 2026 | Leistungsbeschreibung auch in die ZUGFeRD-XML (BT-154) | Mireks Entscheidung; normkonform, Aareon-Verarbeitung aber noch ungetestet |
| Aug. 2026 | Code-Review-Auftrag: maximaler Umfang – Bugfixes + Knigge + Modularisierung + alle vier heiklen Änderungen | Mireks Freigabe (Nummernkreis atomar, POST-only, GoBD-Soft-Delete, BT-130-unitCode) |
| Aug. 2026 | Kein hartes Löschen mehr: Soft-Delete für Rechnungen/Zahlungen | GoBD-Unveränderbarkeit; Nummern werden nie wiederverwendet |
| Aug. 2026 | Web-UI: shadcn-Annäherung (Radius knapper, Gewichte dezenter, flache Schatten) | Mireks Wahl nach Direktvergleich – „professioneller und in sich stimmiger“ |
| Aug. 2026 | PDFs: schlichter Briefbogen statt Executive-Design, **komplett monochrom** | Mireks Wahl nach Vorschau beider Varianten; Blau-Akzent per Konstante zuschaltbar |
| Aug. 2026 | Diktat: **lokales Whisper** (faster-whisper) statt Browser-only oder Cloud-STT | Beste Erkennung OHNE Audio-Upload – „Kein Audio-Upload“-Regel bleibt gewahrt; Browser-Fallback wenn nicht installiert |
| Aug. 2026 | Keine Kundenerkennung im Diktat | Mireks Entscheidung – KI bleibt reines Leistungs-Werkzeug |
| Aug. 2026 | KI-Roadmap-Ranking: Magic-Tone → Plausi-Check; Voice-to-Action zurückgestellt; Preis-/Rabatt-Vorschläge **verworfen** | Zu wenig Datenbasis (LLM würde Statistiken erfinden) + Datenschutzkonflikt |
| Aug. 2026 | Plausi-Ausreißer **lokal-deterministisch** (eigene Rechnungshistorie) statt per KI | Präziser, kostenlos, offline – und Kundendaten bleiben auf dem Server |
| Aug. 2026 | KI-Begleittext: Kontext serverseitig, nur Leistungsdaten, Anrede immer neutral | Datenschutzregel bleibt hart; per Test abgesichert |
| Aug. 2026 | **Desktop-Paket statt .exe**: SEDO.app / SEDO.vbs + App-Fenster (`--app`-Modus) | Mireks Wunsch nach „richtigem Programm“; .exe abgelehnt (2 OS, Antivirus-Fehlalarme, Update-Bremse) |
| Aug. 2026 | v3.22-Härtung als **Vollpaket** (inkl. NumberRange + Angebots-Soft-Delete) | Mireks Wahl nach dem zweiten Review – nicht kosmetisch flicken, sondern Workflows sauber modellieren |
| Aug. 2026 | DEBUG-Default auf 0, statische Dateien via **WhiteNoise** (USE_FINDERS) | Mireks Wahl; sicherer Default ohne collectstatic-Hürde für Papa; Media über eigene Route |
| Aug. 2026 | Korrektur-Workflow **streng**: Original wird „korrigiert“, zweite Korrektur/Storno gesperrt | Mireks Wahl; Kette Original↔Korrektur beidseitig sichtbar |
| Aug. 2026 | Mahnprozess zweistufig: Erinnerung setzt 7-Tage-Frist, danach Mahnung; keine 3. Stufe | Modell hat zwei Mahnarten; manuelle Mahnung ohne Erinnerung bleibt erlaubt |

---

## WICHTIGE ERKENNTNISSE

✅ Capture ≠ E-Rechnung: Eingangsweg bei Aareon muss „E-Rechnungen" sein, nicht „Capture"
✅ SEDO muss keine Kontierungswerte (Sachkonto, NkSl) selbst in XML schreiben – Mapping bei Aareon löst das auf
✅ Exakte Schreibweise in ShipToTradeParty/ID entscheidend (Leerzeichen, Slash, Groß/Kleinschreibung)
✅ Bei mehreren Leistungsarten: Leistungsart-ID je Position, nicht nur im Kopf
✅ ARP benötigt SellerTradeParty/ID (ARP Seller-ID) + BuyerTradeParty/ID (ARP Receiver-ID) – beide Pflichtfelder
✅ ActualDeliverySupplyChainEvent ist ARP-Pflichtfeld (1..1) – jetzt immer gesetzt (Fallback = Rechnungsdatum)
✅ ExchangedDocument/Name = Rechnungstext für ARP-Portal – jetzt gesetzt (z. B. „Rechnung Mai 2026")
✅ Django-Formsets setzen `position` NICHT automatisch – muss nach `formset.save()` durchnummeriert werden (galt nur bei Rechnungen, bei Angeboten fehlte es bis v3.19)
✅ CSS: eine Autoren-Regel wie `display:inline-flex` überstimmt das `hidden`-Attribut des Browsers → `[hidden]{display:none !important}` als Reset ist Pflicht
✅ Beim Ausblenden von Formularabschnitten muss jedes Feld seine Fehler rendern, sonst wird ein Serverfehler unsichtbar
✅ `<input type="date">` akzeptiert NUR ISO (`2026-07-26`); mit `LANGUAGE_CODE='de-de'` rendert Django `26.07.2026` und der Browser verwirft den Wert kommentarlos → DateInput immer mit `format='%Y-%m-%d'`
✅ Ein neues Feld in einer Dataclass gehört ans ENDE, sonst brechen positionsbasierte Aufrufe (bei `InvoicePosition` passiert – von den Tests gefangen)
✅ Die Testsuite erzeugt automatische Backups in `backups/` – beim Paketieren ausschließen
✅ BT-130 (Mengeneinheit) stand bis v3.20 IMMER auf H87/Stück – auch bei m²/Monat; seit v3.21 gemappt (m²→MTK …). PDF und XML sagten vorher Unterschiedliches!
✅ `next_number()` hatte einen Ternary-Logikfehler und war nicht kollisionssicher – Nummernvergabe jetzt nur noch über `create_numbered_*`/`save_with_unique_number` (Transaktion + Retry)
✅ `clone_position()` kopierte `long_text` nicht → BT-154 ging im Excel-Massenlauf still verloren (Feld beim Erweitern von Dataclasses IMMER in Kopierfunktionen nachziehen)
✅ SQLite gibt bei `Sum()` über DecimalField einen **Float** zurück („5274,7000000000 €“) → `_sum_decimal()`-Helfer in den Views verwenden
✅ Bei `base_manager_name='all_objects'` laufen **Rückwärtsrelationen** über all_objects – soft-gelöschte Datensätze erscheinen in Templates, wenn nicht explizit `deleted_at__isnull=True` gefiltert wird (zweimal passiert: customer_detail, invoice_detail)
✅ `<input type="number">` verwirft deutsche Kommawerte (`58,00`) **stillschweigend** → leeres Feld; beim programmatischen Befüllen/Testen Punktnotation verwenden
✅ Der Django-Test-`MEDIA_ROOT` ist nicht isoliert – Testläufe überschreiben echte PDF-Dateien im `media/`-Ordner (DB ist getrennt)
✅ Whisper (`faster-whisper`) mit Fachvokabular-`initial_prompt` schreibt Zahlen/Einheiten direkt korrekt („19 %“ statt „neunzehn Prozent“); Rest erledigt die clientseitige Normalisierung
✅ Testumgebungen IMMER exakt nach requirements.txt aufsetzen – „pip install Django“ ohne Pin holte 6.1 und ließ die Suite auf einer nicht erlaubten Version laufen (extern gefunden)
✅ Lösch-dann-Import-Muster brauchen eine Transaktion – der Aareon-Import konnte bei Fehlern den kompletten Mapping-Bestand kosten (extern gefunden)
✅ E-Mail-Versand lief bis v3.20 per **GET-Link** – ein Prefetch/Crawler hätte echte Rechnungsmails auslösen können; mutierende Aktionen jetzt POST-only
✅ UI-Sperren reichen nie: `offer_to_invoice`/`invoice_storno` waren nur im Template versteckt – jeder direkte POST konnte beliebig oft Rechnungen/Stornos erzeugen → Guards gehören in den Service (`ensure_*` + WorkflowError)
✅ `dunning_documents__isnull=True` als Kandidatenfilter macht einen Mahnprozess unmöglich – nach der ersten Erinnerung nie wieder Kandidat; Stufen über Art+Datum der letzten Mahnung steuern
✅ `select_for_update()` ist auf SQLite **wirkungslos** (Django ignoriert es) – Nummernkreis-Zähler stattdessen per atomarem `F()`-Update in derselben Transaktion wie die Beleganlage
✅ `runserver` liefert bei DEBUG=0 weder static noch media aus – WhiteNoise (static) + eigene Media-Route in urls.py nötig, sonst startet die App „nackt“ und alle PDF-Links sind tot
✅ Django-`static()`-Helper in urls.py gibt bei DEBUG=False stillschweigend eine LEERE Liste zurück – für DEBUG-unabhängige Medien `re_path` + `django.views.static.serve` verwenden
✅ Ein bedingter Unique-Constraint (z. B. GWG-Monatslauf) landet als IntegrityError im Nummern-Retry – der Retry muss echte Nummernkollisionen von anderen UNIQUE-Verletzungen unterscheiden, sonst wiederholt er sinnlos
✅ Das Test-MEDIA_ROOT überlebt zwischen Testklassen – Datei-Assertions brauchen eindeutige Nummern oder Aufräumen im setUp
✅ Ein `<table>` als #position-list erbt ALTE Layout-Regeln: `display:flex` auf dem Container macht tbody zu block und die Spalten laufen auseinander – bei Markup-Umbauten immer die bestehenden ID-Regeln gegenprüfen
✅ `<input type=number>` verwirft auch beim PROGRAMMATISCHEN Befüllen Kommawerte („1,00“) – in JS-Simulationen/Tests Punktnotation verwenden (offer_ai.js macht das richtig)
❌ WISO MeinBüro Desktop: BT-75 wird nicht sauber als Leistungsort gemappt – deshalb SEDO

---

## ANSPRECHPARTNER

- **Tönnies** – Ansprechpartner bei GWG/Aareon für XML-Mapping und Testrechnungen
- **Markus Assmann** – Kaufmännischer Mitarbeiter GWG Reform; koordiniert seit 09/2026
  die ZUGFeRD-Testphase (Testadresse ars-399662000@aareon.com, Bestände
  Neitzel/Radtke/Lammel, scharfe Umstellung 01.01.2027)
- **Aareon-Portal** – Eingangsweg für E-Rechnungen (nicht Capture!)

---

## SESSION-VERLAUF

### 17.08.2026 (spät) – Erststart-Feedback der Desktop-App behoben

Mireks erster echter App-Start (Screenshots): 4 Punkte, alle behoben, App+DMG
neu gebaut (Self-Check OK, Whisper verfügbar, 245 Tests grün):
1. Passwort-Dialog erschien bei JEDER Navigation – window.events.loaded feuert
   pro Seitenwechsel; jetzt Einmal-Flag in desktop.py.
2. „Wiederkehrende Rechnungen“ war in der Sidebar abgeschnitten (nav-text
   max-width 210px) → heißt jetzt überall **„Abo-Rechnungen“**.
3. „Komische Symbole“ im Suchfeld = CSS-gezeichnete Pseudo-Lupe + „( / )“ im
   Platzhalter → entfernt, Platzhalter nur noch „Suchen …“.
4. System-Gruppe → EIN aufklappbarer Punkt **„Einstellungen“** (details/summary,
   Unterpunkte Allgemein/Backup/Sicherheit/Admin/Abmelden; offen wenn aktiv).
Zusätzlich: Die Desktop-App lief mit LEEREM eigenen Datenordner (0 Kunden im
Screenshot) → 44-Kunden-DB + media aus sedo_rechnungstool_v2/ in
~/Library/Application Support/SEDO übernommen (leere Erststart-DB als .bak
behalten). ⚠️ Dadurch gilt in der App wieder Mireks ALTES Admin-Passwort –
das im Dialog gezeigte ytJ-… gehört zur ersetzten leeren DB.



### 17.08.2026 (nachmittags) – Installer + Update-Weg (v2.3.0 → v2.3.1)

Mireks Frage nach Installer/Updates. Kernidee: Dank SEDO_HOME-Datentrennung
IST der Installer die Update-Datei (drüberinstallieren, Daten bleiben).
- **Mac:** `dist/SEDO_Rechnungstool_Installer.dmg` (124 MB, hdiutil, verifiziert)
  – „in Programme ziehen“; Update = App ersetzen.
- **Windows:** `sedo_setup_windows.iss` (Inno Setup 6, deutsch, CloseApplications,
  Uninstaller; Daten in %APPDATA%\SEDO bleiben bei Update UND Deinstallation);
  BUILD_WINDOWS.bat kompiliert das Setup automatisch mit, wenn ISCC.exe da ist,
  sonst Hinweis. Ohne Code-Signing zeigt Windows „Unbekannter Herausgeber“.
- Vorher am selben Tag: Projektordner konsolidiert – `sedo_rechnungstool_v2/`
  ist der EINZIGE Ordner (Live-Daten migriert: 44 Kunden, API-Key, Integrität
  geprüft; Berichte-Archiv/ gerettet; alle Altordner im Papierkorb, inkl.
  Mireks .git in 3_21_0_Clean – ihm gemeldet).

**Offen (Live-Termine):** FinTS-Erstabruf (pushTAN), Diktat-/Mikrofontest im
Bundle, Windows-Build+Setup in Parallels, ZUGFeRD-Validator (Java fehlt noch).



### 17.08.2026 – FinTS-Kontoanbindung + Diktat im Bundle + App-Feinschliff (v2.2.0 → v2.3.0)

**Anlass:** Mirek „mittel zufrieden“ mit v2.2: Diktat MUSS ins Komplettpaket,
Banking-Funktion rein (per Rückfrage: ECHTE FinTS-Kontoanbindung), und die
Webview-Skepsis („kein eigenständiges Programm“) → Feinschliff freigegeben;
Architektur-Einordnung gegeben (Slack/VS Code sind genauso gebaut).

**Umgesetzt:**
1. **FinTS (services/banking.py, python-fints):** rein lesender Umsatzabruf;
   Einstellungen → Bankkonto (BLZ/FinTS-URL/Login/IBAN/Produkt-ID; PIN optional
   gespeichert, in SECRET_SETTING_KEYS → Backup-Scrub, Env SEDO_FINTS_PIN);
   Zahlungen-Seite: „Umsätze abrufen“ (Zeitraum, Einmal-PIN wenn nicht
   gespeichert); pushTAN-decoupled mit bis zu 90 s Warten, klare Meldung bei
   nicht unterstützten TAN-Verfahren; mt940→Import-Pipeline mit Bank-Referenz
   (end_to_end/bank_reference, sonst stabiler FINTS-Hash) als external_id.
   9 gemockte Tests. ⚠️ ECHTER Abruf noch nie getestet – Live-Test mit Mireks
   Bank nötig (pushTAN-API-Verhalten von python-fints defensiv implementiert).
   ⚠️ Falle: mt940-date-Objekte müssen als ISO-String ins Payment.raw-JSONField.
2. **Diktat im Bundle:** Spec sammelt faster_whisper/ctranslate2/av/tokenizers/
   huggingface_hub/onnxruntime per collect_all; NSMicrophoneUsageDescription;
   HF_HOME → SEDO_HOME/whisper-cache; Self-Check meldet Whisper-Status.
   ⚠️ Mikrofon-Freigabe im WKWebView beim Live-Test prüfen.
3. **Feinschliff desktop.py:** Menüleiste (Ablage: Backup/Beenden; Hilfe: Über),
   Fenstergeometrie in SEDO_HOME/window.json, Kontextmenü nur in Eingabefeldern.
4. Backup-Scrub-Test iteriert jetzt dynamisch über SECRET_SETTING_KEYS.

**Verifikation:** 236 → **245 Tests grün**; Mac-Bundle v2.3 neu gebaut (Ausgang
siehe Build-Log). Live-Ordner synchronisiert (SEDO.app installiert fints/mt-940
beim nächsten Start automatisch via requirements-Hash).

**Offen:** FinTS-Livetest mit Mirek (pushTAN), Diktat-/Mikrofontest im Bundle,
Windows-Build (Parallels), ZUGFeRD-Validator (Java).



### 16.08.2026 (abends) – Echtes Desktop-Programm (v2.1.0 → v2.2.0)

**Anlass:** Mireks Wunsch „richtiges Programm ohne Umweg Browser“; per Rückfrage
entschied er sich für die Komplett-Paketierung (Mac UND Windows) trotz der in
v3.21 notierten Risiken.

**Umgesetzt:**
1. **SEDO_HOME-Pfadschicht** in settings/backup_utils: alle schreibenden Pfade
   (DB, media, backups, .env, .secret_key) über env SEDO_HOME; Default =
   Projektordner (Bestandsverhalten unverändert, Suite grün). Im frozen-Modus
   zeigt BASE_DIR auf sys._MEIPASS (Templates/Static aus dem Bundle).
2. **desktop.py**: waitress-Thread + natives pywebview-Fenster (WKWebView/
   WebView2), Migrationen + ensure_admin beim Start, Erststart-Passwort als
   Fenster-Dialog, freier Port (bevorzugt 8765), `--check`-Headless-Selbsttest.
3. **PyInstaller-Spec** (`sedo_desktop.spec`): collect_all django +
   collect_submodules sedo (Migrationen/Commands!), Templates/Static als datas,
   OHNE faster-whisper (bewusst: >200 MB, WKWebView hat eh keine Browser-STT –
   Diktat bleibt dem klassischen Starter vorbehalten). Icon aus SEDO.app.
4. **Build-Skripte:** `build_mac.command` (baut + führt --check aus) und
   `BUILD_WINDOWS.bat` (für Parallels/Papas Rechner – .exe kann NUR unter
   Windows gebaut werden). Eigene Build-venv `.venv_build`.
5. **Gebaut + verifiziert (Mac):** dist/SEDO Rechnungstool.app (117 MB, arm64);
   Selbsttest des GEBÜNDELTEN Binaries grün; echter Fensterstart mit
   Temp-SEDO_HOME geprüft (Login-Seite 200, sauberes Beenden). Mireks echter
   Erststart (Passwort-Dialog) blieb unangetastet.
6. Doku: README-Abschnitt Komplettpaket (Datenübernahme data/+media in den
   Benutzerordner!), CHANGELOG v2.2.0, Footer v2.2.0.

**Wichtig:** Komplettpaket-Daten liegen GETRENNT vom klassischen Starter
(~/Library/Application Support/SEDO bzw. %APPDATA%/SEDO). Windows-Build +
Test stehen aus (Parallels). ZUGFeRD-Validator wartet weiter auf Java.



### 16.08.2026 – UX-Runde v2.1 + Audit-Nachprüfung (v2.0.1 → v2.1.0)

**Anlass:** Mireks UX-Review (7,5–8/10, drei Kernpunkte: Dashboard priorisieren,
Navigation vereinfachen, globale Suche) + ein weiteres externes Code-Audit.

**Audit-Nachprüfung (wichtig):** Der „kritische“ Befund des Audits
(„@login_required hängt an der falschen Funktion, backup_page ungeschützt“)
war FALSCH – der Decorator sitzt korrekt, zusätzlich schützt die
LoginRequiredMiddleware; per Regressionstest belegt (GET+POST /backup/ ohne
Login → 302). Auch sonst enthielt das Audit Halluzinationen (Bootstrap,
fehlende Toasts/KI-Spinner – alles längst vorhanden). Echt und umgesetzt:
CSRF_COOKIE_HTTPONLY=True (gefahrlos – eigene JS lesen den Token aus dem DOM,
nie aus dem Cookie) und Aareon-Import per bulk_create (parsen komplett VOR
dem Löschen, ein Insert statt hunderter).

**UX-Runde v2.1:**
1. **Globale Suche**: /api/suche/ (Kunden/Rechnungen/Angebote/Objekte,
   gruppiert, Limits je Gruppe) + Suchfeld in der Sidebar (global_search.js,
   Debounce, Pfeiltasten, Shortcut „/“). Live verifiziert.
2. **Dashboard**: Zeile „Heute wichtig“ (Überfällig · Mahnstufe fällig ·
   Offene Forderungen als große klickbare Karten mit Direktaktion), sekundäre
   KPIs klein darunter; oben nur noch Rechnung/Angebot (GWG-Doppelung raus).
3. **Begriffe**: „GWG-Abrechnung“ und „Wiederkehrende Rechnungen“ (Sidebar,
   Seitentitel, Settings) – Mireks bester Review-Punkt, Verwechslungsgefahr weg.
4. nav-pulse-Animation entfernt (ai-rec-Punkt bleibt – funktionales Feedback);
   „+“-Präfixe aus Buttons (Icons stattdessen); Status-Badges mit Symbol
   (✓/✕/↷/!); beforeunload-Warnung bei ungespeicherten Angebots-/
   Rechnungsformularen (unsaved_guard.js); Nav-Labels dürfen umbrechen.

**Verifikation:** 228 → **236 Tests grün** (Suche, Backup-Auth, bulk_create-
Rollback angepasst); Dashboard + Suche im Browser geprüft. Footer v2.1.0,
Code in Mireks Live-Ordner „…_3_22_0 2“ synchronisiert (Neustart nötig),
ZIP neu gepackt. **Weiter offen:** ZUGFeRD-Vollvalidierung (wartet auf Java).



### 14.08.2026 (nachts) – Concurrency-Patch nach drittem Review (v3.23.0 → v3.23.1)

**Anlass:** Mireks dritter Review (gegen das echte v3.23-ZIP, Urteil ~8,5/10):
zwei echte Race-Conditions – die ensure_*-Guards prüfen VOR der Transaktion,
zwei parallele Requests können beide passieren. Sein Wunsch: kein großer Umbau,
nur ein „Concurrency & Validation“-Patch.

**Umgesetzt (Migration 0012, Details AENDERUNGSBERICHT_v1_0_beta_3_23_1.md):**
1. Unique-Constraints: DunningDocument(invoice,kind); je Ursprungsrechnung max.
   EIN aktives Storno (457) und EINE aktive Korrektur (384) – bedingt, soft-
   gelöschte Folgebelege blockieren nicht. Migration dedupliziert vorher
   Doppel-Mahnungen aus Altbeständen (ältestes Dokument bleibt).
2. Guarded Updates: Angebot→Rechnung verknüpft nur noch per bedingtem UPDATE
   (Verlierer → WorkflowError → komplette Transaktion inkl. Nummer rollt
   zurück); mark_invoice_corrected ebenso.
3. IntegrityError→WorkflowError-Übersetzung in create_storno,
   generate_dunning_document und im Korrektur-Pfad der View. ⚠️ Fallen:
   SQLite meldet SPALTEN (reference_invoice_id), nicht Constraint-Namen;
   und der fehlschlagende INSERT braucht ein eigenes atomic (Savepoint),
   sonst TransactionManagementError in umgebenden Transaktionen.
4. Zahlungsimport (Mireks Entscheid): Zahlung auf KORRIGIERTE Rechnung wird
   der aktiven Korrekturrechnung zugeordnet (Kette, max. 5); ohne aktive
   Korrektur bleibt sie „unklar“.

**Verifikation:** 217 → **228 Tests grün** (9 Concurrency-Tests, die die Guards
per Patch umgehen + 2 Zahlungs-Tests). Code per rsync in Mireks Live-Ordner
„…_3_22_0 2“ eingespielt (Migration 0012 läuft beim nächsten Start automatisch).

**Offen:** ZUGFeRD-Vollvalidierung per Mustang – siehe OFFENE AUFGABEN.

**Nachtrag 15.08. (v2.0 → v2.0.1, vierter Review 8,5–9/10):** Mireks letzter
Security-Befund behoben – /media/ war in der LoginRequiredMiddleware vom Login
ausgenommen, Rechnungs-PDFs waren bei Heimnetz-/Tunnel-Betrieb ohne Login
abrufbar. Fix: Exempt-Eintrag entfernt (Media-Route läuft durch die
Middleware → jeder Dateiabruf authentifiziert; /static/ bleibt frei).
Betriebsmodi LOKAL vs. NETZWERK/TUNNEL in DEPLOYMENT_SECURITY_NOTES
dokumentiert (Secure-Cookies/SSL-Redirect Pflicht vor Tunnel).
**230 Tests grün** (+2: media ohne Login → Redirect, static bleibt frei).
Paket + Live-Ordner aktualisiert. Mireks Schlusswort: Architektur jetzt gut
genug, nicht mehr aus Prinzip umbauen.

### 14.08.2026 (spät abends) – UI-Feinschliff nach Mireks Live-Test (v3.22.0 → v3.23.0)

**Anlass:** Mirek testete die v3.22 live (Ordner „…_3_22_0 2“ aus dem ZIP – macOS
hängte beim Entpacken „ 2“ an; die Fußzeile zeigte fälschlich 3.21.0, weil der
Footer-Bump in v3.22 vergessen wurde) und gab 6 Screenshot-Anmerkungen plus
generelle Design-Kritik („zu kitschig, nicht business genug, SEDO-Farben bleiben“).

**Umgesetzt (Details im AENDERUNGSBERICHT_v1_0_beta_3_23_0.md):**
1. Kundensuche: echtes Autocomplete (`customer_search.js`, Angebot+Rechnung) –
   vorher wurden nur Dropdown-Optionen versteckt und der Fokus resettete den Filter.
2. ⓘ-Hinweis-Pattern (`.info-tip`) global: KI-Schritt, Positionen, Prüfen,
   Plausi-Check, Monatsrechnung – lange Hinweise als Hover/Fokus-Tooltip.
3. Positionen: genau EINE Startposition (extra=0 – min_num+extra ergab zwei),
   „+ Position hinzufügen“ unter der letzten Position, kompaktere Karten.
4. Vorschau-Rücksprung: wizard.js merkt sich den Schritt (sessionStorage) und
   stellt ihn bei Browser-Zurück (back_forward-Navigation) wieder her.
5. Angebots-PDF: persönliche Anrede (Anrede+Ansprechpartner), neue Einleitung,
   Schlussteil (Gültigkeit, Gesprächsangebot, Zusicherung) + Grußformel;
   Firmenname zentral aus core.SELLER (war doppelt hart codiert).
6. Monatsrechnung: Prozessleiste markiert nur noch Schritt 1 (vorher 1+2).
7. Business-Design-Pass: Verläufe/Farbkreis raus, flache Karten, Navy-Akzentkante,
   sachliche Icons, höhere Dichte (Feldhöhe 42→38, Titel kleiner) – Sidebar-Navy
   und Orange-Akzente (Logo) unverändert. Footer jetzt v3.23.0, Cache-Buster v3_23.
8. Nachschlag am selben Abend: Positionen als TABELLENZEILEN (Variante A, per
   Vergleichs-Artifact „SEDO Positionsmasken“ gewählt) – tbody je Position,
   Beschreibung als einklappbare Folgezeile (`positions_table.js`); Textfelder
   global sachlicher (Radius 7, ohne Innenschatten). ⚠️ Falle dokumentiert:
   `#position-list{display:flex}` machte die Tabelle kaputt (tbody wurde block);
   `table-layout:fixed` nötig. KI-Befüllung/Live-Summen laufen unverändert, weil
   Selektoren (.position-form, Name-Suffixe, .pos-line-total-val) erhalten blieben.

**Verifikation:** 217/217 Tests grün; UI-Punkte live im Browser durchgeklickt
(Suche→Auswahl→Autofill, Tooltip, Position hinzufügen, Monatsleiste, Dashboard);
Muster-PDF gerendert und gegengelesen. **v3.23-Code per rsync in Mireks
Live-Ordner „…_3_22_0 2“ eingespielt (data/media/logs/.venv/.env ausgeschlossen)
– zum Aktivieren Server neu starten (SEDO_BEENDEN → SEDO.app).**

### 14.08.2026 (abends) – Härtungs-Session nach zweitem Review (v3.21.0 → v3.22.0)

**Anlass:** Mireks zweite, deutlich tiefere Prüfung (Geschäftslogik statt Syntax)
mit 20 Befunden. Vorgehen: jeden Befund zuerst gegen den v3.21-Clean-Stand
verifiziert – die Punkte Aareon-Import, Monatsrechnung-Atomarität,
ALLOWED_HOSTS und Angebot→Rechnung-Transaktion waren dort bereits behoben
(Mirek hatte gegen ein älteres ZIP geprüft). Alle übrigen bestätigt und in
v3.22 behoben; Mirek gab per Rückfrage das **Vollpaket** frei (inkl.
NumberRange + Angebots-Soft-Delete, DEBUG=0 mit WhiteNoise, strenger
Korrektur-Workflow).

**Umgesetzt (Details im Änderungsbericht):**
1. Workflow-Sperren: kein Doppel-Storno, kein Storno von Stornos/korrigierten
   Rechnungen; Angebot→Rechnung serverseitig idempotent (beide View-Pfade);
   strenger Korrektur-Workflow mit neuem Status „korrigiert“ und beidseitig
   verlinkter Beleg-Kette.
2. Mahnstufen-Modell: Erinnerung → (Frist 7 Tage abgelaufen) → Mahnung;
   Doppel-Stufen gesperrt, Bulk mit kind='auto', UI-Spalte „Nächste Stufe“.
   (Vorher: nach der ersten Erinnerung nie wieder Kandidat.)
3. Cross-Customer-Sperre im Monats-ZIP-Versand.
4. Zahlungsimport: `Payment.external_id` (Bank-Transaktions-ID) als primärer
   Doubletten-Schlüssel; Regex auf (RE|AG)-Präfixe eingeschränkt.
5. Datei-Erzeugung atomar (Temp-Verzeichnis + os.replace, keine halben Dateien).
6. GWG-Monatslauf: Unique-Constraint gegen Doppel-Läufe (Verletzung = übersprungen).
7. **NumberRange**-Nummernkreise: zentraler Zähler, atomares F()-Update in
   derselben Transaktion wie die Anlage (select_for_update ist auf SQLite
   wirkungslos!), Seeding/Nachziehen aus Altbestand, keine verbrannten Nummern;
   next_number() nur noch Formular-Vorschau.
8. Angebots-Soft-Delete (Migration 0011) inkl. all_objects-Filter im Kundendetail.
9. DEBUG=0 als Default + WhiteNoise (USE_FINDERS, kein collectstatic nötig) +
   DEBUG-unabhängige Media-Route (sonst wären alle PDF-Links tot gewesen).

**Verifikation:** 165 → **217 Tests grün** (52 neue in 7 Testdateien), Gesamtsuite
nach jeder Phase; Live-Smoke-Test runserver mit DEBUG=0 (static/media/login 200);
check --deploy unverändert sauber. Migrationen 0007–0011.

**Wichtig für DB-Import der 44 Kunden:** Migration 0009 (Monatslauf-Unique)
schlägt sichtbar fehl, falls der Altbestand doppelte aktive GWG-Monatsrechnungen
enthält – dann gezielt bereinigen.

### 14.08.2026 – Große Cowork-Session: Review, Design, KI-Ausbau (v3.20.0 → v3.21.0)

**Anlass:** Mireks Dreiklang: „Code prüfen, berichtigen, ordnen (Knigge)“ → „das Design“ →
„Angebots-KI verbessern“. Gearbeitet wurde auf einer **frischen Kopie**
(`…_3_21_0_Clean`), das Original v3.20.0 blieb unangetastet. Alle Details im
`AENDERUNGSBERICHT_v1_0_beta_3_21_0.md` im neuen Ordner.

**1. Code-Review & Bugfixes** (3 parallele Review-Agenten über views/services/core+Tests):
- GoBD-kritisch behoben: Nummernkreis kollisionssicher (Transaktion + Retry, keine
  „verbrannten“ Nummern im GWG-Lauf), Soft-Delete statt hartem Löschen (Migration 0006),
  mutierende Aktionen nur noch per POST (E-Mail-Versand lief per GET-Link!),
  `clone_position` verlor BT-154, **BT-130-unitCode war immer H87**.
- `services.py` (1041 Zeilen) → Package aus 12 Modulen, rückwärtskompatibel re-exportiert;
  Fat Views entschlackt, tote Importe/Semikolons/Magic Numbers bereinigt.

**2. Design:**
- Web-UI: Emoji → einheitliches Inline-SVG-Sprite; Dashboard entdichtet;
  Float-Summen-Anzeigebug behoben; danach per Direktvergleich (Artifact-Seite)
  **shadcn-Annäherung** gewählt und global umgesetzt (72 Radius- + 49 Gewichts-Tokens).
- PDFs: erst Executive-Vereinheitlichung aller vier Dokumente, dann auf Mireks Wunsch
  komplett neu als **schlichter Briefbogen, monochrom** (Vorschau beider Akzent-Varianten,
  Mirek wählte monochrom; Blau-Akzent bleibt per Konstante zuschaltbar).

**3. KI-Ausbau** (Bewertung der vier Recherche-Vorschläge mit Ranking):
- **Lokales Whisper-Diktat:** MediaRecorder → faster-whisper auf dem eigenen Server
  (Fachvokabular-initial_prompt, Audio wird gelöscht), automatischer Browser-Fallback;
  clientseitige Zahlen-Normalisierung; Lade-Skeleton; dauerhafte KI-Markierung mit Legende.
- **Magic-Tone:** ✨-Formulieren-Knopf an jedem Leistungstext + KI-Begleittext für den
  Rechnungsversand (Kontext serverseitig, nur Leistungsdaten – per Test bewiesen).
- **Plausibilitäts-Check** im Prüfen-Schritt: lokal-deterministisch (Preis-Ausreißer
  gegen Kundenhistorie inkl. Zahlendreher-Erkennung 85↔58, Dubletten, Nullwerte –
  automatisch) + KI-Check per Knopf (nur Positionsdaten, Katalog als Kontext).
  Live bewiesen: Historie 85 € → Position 58 € → automatische Warnung mit Beleg.
- Verworfen: Preis-/Rabatt-Vorschläge (LLM würde Statistiken erfinden); zurückgestellt:
  Voice-to-Action; abgelehnt: Kundendaten an die KI (Whisper-Cloud, Kundenlisten-RAG).

**Verifikation:** 108 → **158 Tests grün** (50 neue: Cleanup, STT, Magic-Tone, Plausi, Sicherheitspaket);
jede Phase einzeln getestet; UI und alle vier PDFs im Browser/als Render geprüft;
Kernszenarien (Zahlendreher-Warnung, Normalisierung, Fallbacks) live durchgespielt.

### 26.07.2026 – Nachbesserungen aus dem ersten Durchgang (v3.19.0 → v3.20.0)

**Anlass:** Mireks fünf Anmerkungen nach dem ersten Blick auf den Assistenten.

1. **Kunden-Autofill jetzt auch im Angebot** (bisher nur in der Rechnung):
   Schnellsuche, sofortige Befüllung aller Felder mit blauem Flash, „autobefüllt“-Badge,
   Kunden-Pill, gefiltertes Objekt-Dropdown inkl. Leistungsort-Übernahme.
2. **KI-Schritt an Position 4** (hinter Objekt und Angebotsdaten) – Mirek will erst
   Leistungsort und Angebotsdaten eingeben. Reihenfolge jetzt:
   Kunde · Objekt · Angebotsdaten · KI-Assistent · Positionen · Prüfen.
3. **Datumsfelder repariert (echter Bug):** `LANGUAGE_CODE='de-de'` rendert den
   Vorgabewert als `26.07.2026`; ein `<input type="date">` erwartet aber ISO und
   verwarf den Wert stillschweigend → Feld war immer leer. Alle DateInput-Widgets
   jetzt mit `format='%Y-%m-%d'`. Nebeneffekt: Datumsangaben überleben jetzt einen
   fehlgeschlagenen Speicherversuch.
4. **Kalkulationsangaben werden nicht mehr ausgewiesen:** Prompt-Regel 8 – Arbeiter,
   Tage, Stunden, Stundensätze dienen nur der Preisfindung und dürfen weder als
   Position noch im Titel/Leistungstext/Annahmen/Hinweisen auftauchen. Ausnahme:
   ausdrückliche Stundenabrechnung („auf Stundenbasis“).
5. **Leistungsbeschreibung auch in Rechnungen:** `InvoiceItem.long_text` +
   `RecurringInvoiceItem.long_text` (Migration 0005), im Rechnungs-PDF und als
   **BT-154** in der ZUGFeRD-XML (nur wenn befüllt). Übernahme bei Angebot→Rechnung,
   Korrektur, Storno und Abo-Vorlagen. Damit ist der alte offene Punkt erledigt.

**Nebenbei:** Gemeinsame Formular-Styles aus dem Rechnungstemplate nach `app.css`
verschoben (beide Masken nutzen jetzt dieselbe Grundlage). Beim Paketieren fiel auf,
dass die Testsuite automatische Backups in `backups/` erzeugt und diese bisher ins
Auslieferungspaket rutschten – ab v3.20.0 ausgeschlossen (Paket 293 KB statt 14 MB).

**Verifikation:** 108/108 Django-Tests · 54 DOM-Prüfungen (jsdom, echter Skriptlauf
inkl. Autofill über gemockte API) · Integrationstest gegen Fake-Anthropic-Server
(Kalkulationsregel im übertragenen System-Prompt nachgewiesen) · E2E gegen echte DB
mit PDF und BT-154-Nachweis in der XML · Testsuite zusätzlich aus dem entpackten
Paket gefahren.

**Entscheidung von Mirek:** BT-154 soll geschrieben werden, obwohl bei Aareon noch
ungetestet – deshalb als offene Aufgabe für den Tönnies-Test vermerkt.

### 26.07.2026 – UI-Überarbeitung Teil 1: Schritt-Assistent (v3.18.0 → v3.19.0)

**Anlass:** Mireks Ansage nach dem KI-Generator – das Tool wirkt unübersichtlich,
speziell Angebots- und Rechnungserstellung sind „kein runder Prozess mehr“.

**Beobachtung vorab:** Die Rechnungsmaske hatte bereits nummerierte Abschnitte,
Autofill-Badges und Live-Summe – die Angebotsmaske nichts davon, obwohl beide
dasselbe tun. Dazu 15 Versionen übereinandergelegtes CSS.

**Umsetzung:**
- Neues Modul `wizard.js` (abhängigkeitsfrei, global in `base.html`), steuert nur
  die Sichtbarkeit – alle Felder bleiben in einem `<form>`
- Angebot: 6 Schritte (Kunde · **KI-Assistent** · Objekt · Angebotsdaten ·
  Positionen · Prüfen), Rechnung: 5 Schritte im selben Muster
- Fortschritts-Pills (erledigt/aktuell/gesperrt), Aktionsleiste mit „Schritt X von Y“,
  Abschluss-Buttons erst im letzten Schritt
- Enter = „Weiter“ statt Absenden; im Diktat-Textfeld unverändert
- **Fehler-Automatik:** `.errorlist` im HTML → Assistent öffnet diesen Schritt und
  entsperrt alles. Dafür rendern jetzt ALLE Felder ihre Fehler (vorher nur „Name“)
- Zusammenfassung im letzten Schritt + freundliche Hinweise bei Lücken
- Nach KI-Generierung springt der Assistent automatisch zu den Positionen
- Angebot bekam die Komfortfunktionen der Rechnung (Zeilensumme, Live-Kalkulation)

**Dabei gefundene Fehler (behoben):**
- Angebotspositionen wurden nie durchnummeriert → im PDF stand in jeder Zeile „1“
  (Rechnungen machten das schon richtig)
- Die Diktat-Aufnahmeanzeige war dauerhaft sichtbar, weil `display:inline-flex`
  als Autoren-Regel das `hidden`-Attribut überstimmt → Reset
  `[hidden]{display:none !important}` ergänzt

**Verifikation:** 94/94 Django-Tests grün (25 neu in `test_wizard.py`) ·
55 DOM-Prüfungen mit jsdom (echter Skriptlauf: Navigation, Enter, Fehlersprung,
Live-Kalkulation, KI-Sprung) · E2E gegen echte DB inkl. PDF · Live-Server-Test
(alle Seiten + statische Dateien HTTP 200) · Testsuite zusätzlich aus dem
entpackten Auslieferungspaket gefahren.

### 26.07.2026 – KI-Angebots-Generator (v3.17.1 → v3.18.0)

**Anlass:** Papa soll nur noch grob ansagen, was gemacht werden muss („Beräumung
Reihenhaus mit Entsorgung, 1200 €") – die KI erstellt daraus ein vollständiges,
plausibles Angebot. Zusätzlich: sichtbare Aufnahme-Anzeige.

**Umsetzung:**
- **Datenmodell:** `OfferItem.long_text` (Leistungsbeschreibung) + neues Modell
  `ServiceCatalogItem` (selbstlernender Leistungskatalog) → Migration 0004
  (räumt auch den alten kosmetischen Migrationsrückstand mit auf)
- **Generator-Prompt/-Schema:** Positionen mit titel, leistungstext (2–4 Sätze),
  preis_art (genannt/aufgeteilt/geschaetzt/offen), katalog_name/katalog_neu;
  separate Listen für Annahmen und Hinweise. Aufteilungs-Summenkontrolle als
  Hinweis (Zeilensumme wird ausgewiesen).
- **Katalog-Lernlogik `apply_and_learn_service_catalog()`:** bekannte Leistung →
  gespeicherter Text ersetzt generierten (Konsistenz); leerer Eintrag wird
  befüllt; neue Leistung wird mit created_by_ai=True angelegt. Katalog wird bei
  jeder Generierung in den System-Prompt eingebettet.
- **Neue Katalogseite** in der Navigation (Liste/Suche/CRUD, Badges „KI-gelernt"/
  „Geprüft"; manuelles Bearbeiten setzt Status auf Geprüft)
- **UI:** Aufnahme-Anzeige (pulsierender roter Punkt + Laufzeit), Annahmen-Box
  (blau) getrennt von Hinweisen (orange), orange Umrandung für zu prüfende
  Preise, Ersetzen/Anhängen-Abfrage bei erneuter Generierung, Langtext-Textarea
  je Position (volle Breite)
- **PDF:** Leistungsbeschreibung mehrzeilig unter dem Positionstitel (simpleSplit,
  Seitenumbruch-sicher); Vorschau zeigt Langtexte ebenfalls
- **Endpoint:** `/api/ki/angebots-entwurf/` (ai_generate_offer) ersetzt den alten
  Positions-Endpoint; Response um annahmen + katalog_info erweitert
- **Verifikation:** 69/69 Tests grün; Fake-Anthropic-Integrationstest (Schema,
  Katalog im Prompt, Aufteilung 900+300=1200 €, Lernen + Wiederverwendung);
  Live-Smoke-Test. Ein Testskript-Fehler (case-sensitive Header-Lesen) beim
  Doppelprüfen gefunden – App-Code war korrekt.

**Zielbild von Mirek (Antworten auf Rückfragen):** Preise aufteilen UND schätzen
(markiert) · echte Leistungsbeschreibungen · selbstlernender Katalog · Annahmen
treffen + markieren.

### 26.07.2026 – KI-Assistent auf Claude umgestellt (v3.17.0 → v3.17.1)

**Anlass:** Mirek arbeitet lieber mit Claude als mit OpenAI.

**Umbau:**
- Textauswertung (Diktat → Positionen) läuft jetzt über Anthropic Messages API
  mit erzwungenem Tool-Use (`tool_choice: {"type":"tool", ...}`, striktes
  `input_schema`) statt OpenAI JSON-Mode. Claude liefert bereits geparstes
  JSON im `tool_use`-Block – kein String-/Markdown-Parsing mehr nötig.
- Wichtige Erkenntnis: Claude hat **keine Transkriptions-API**. Lösung:
  Spracherkennung läuft direkt im Browser (Web Speech API, `de-DE`,
  `continuous`, Auto-Restart bei Chrome-Timeout). Es wird **keine
  Audioaufnahme mehr übertragen** – nur der fertige Text geht an Claude.
  Endpoint `/api/ki/transkription/` und `transcribe_audio()` komplett entfernt.
- Neues Standardmodell: `claude-sonnet-5`. Einstellungen zeigen jetzt
  „Claude-API-Key" (console.anthropic.com).
- Fehlerbehandlung an Anthropic-Fehlercodes angepasst (401/402/403/413/429/529)
  mit deutschen Meldungen, inkl. Retry bei 5xx/529.
- Tests komplett umgeschrieben (Transkriptions-Tests entfernt, Claude-Extraktion
  gemockt) → 64/64 grün. Zusätzlich Integrationstest gegen Fake-Anthropic-Server
  (Header `x-api-key`/`anthropic-version: 2023-06-01`, erzwungener tool_choice,
  Fehlerpfade 401/429/529) + Live-Smoke-Test.
- Beim Review noch eine übersehene „OpenAI"-Fehlermeldung in `views.py`
  gefunden und korrigiert, bevor sie ausgeliefert wurde.

**Bekanntes Aufräum-To-do:** Im Projektordner liegen jetzt sowohl der
OpenAI-Zwischenstand (`…3_17_0`) als auch versehentlich entstandene
Duplikat-Ordner `…3_17_0 2/3/4` (Nebeneffekt der Dateisynchronisation beim
Teilen der 3.17.0-Dateien). Löschung erfordert Mireks Freigabe – wurde in
der Session angeboten.

### 26.07.2026 – KI-Angebotsassistent (v3.16.0 → v3.17.0)

**Hintergrund:** Papa braucht sehr lange zum Eintippen von Angeboten → Diktatfunktion.

**Feature (v3.17.0):**
- Neue Karte „🎤 KI-Diktat für Positionen" in der Angebotsmaske: Diktat (MediaRecorder)
  → Transkription (OpenAI Audio-API) → LLM mit striktem JSON-Schema → Positions-Formset
  wird automatisch befüllt; Transkript bleibt sichtbar/korrigierbar, Texteingabe als Fallback
- Plausi-Check mit deutschen Hinweisen: fehlender Preis (Feld bleibt leer!), fehlende Menge
  (→ Pauschale 1), €/m²-Bandbreite 0,05–20 €, Einheiten-Normalisierung (qm → m²)
- Einstellungen → „KI-Assistent (OpenAI)": API-Key + Modellnamen konfigurierbar
  (Defaults: gpt-5.4-mini / gpt-4o-mini-transcribe, verifiziert Juli 2026)
- Neue Endpoints `/api/ki/transkription/` + `/api/ki/angebots-positionen/` (Login+CSRF, POST)
- Keine neuen Python-Abhängigkeiten (urllib), kein Migrationsbedarf
- 28 neue Tests (OpenAI gemockt) → Suite 66/66 grün; zusätzlich Integrationstest gegen
  Fake-OpenAI-Server (Multipart, Auth, Schema, 401-Fehlerpfad) + Live-Smoke-Test runserver
- Datenschutz-Zuschnitt: NUR Leistungsdaten in die Cloud, Kunden-/Objektdaten bleiben lokal;
  Prompt ignoriert versehentlich mitdiktierte personenbezogene Daten

**Beobachtung (vorbestehend, nicht angefasst):** `makemigrations` meldet ausstehende
kosmetische Migration zu Invoice-Status-Choices aus früherer Version – ohne Betriebsauswirkung.

### 06.06.2026 – Große Cowork-Entwicklungssession (v3.11.5 → v3.16.0)

**Code-Qualität & Bug-Fixes (v3.12.0):**
- Storno/Korrektur TypeCode 457/384 in XML und Titel im PDF jetzt korrekt gesetzt
- ROUND_HALF_UP durchgehend vereinheitlicht (kein 1-Cent-Fehler mehr)
- Doppelter Mapping-Import-Code refaktoriert → `_perform_aareon_mapping_import()`
- `de_money()`: Tausender-Trennzeichen korrigiert (1.234,56 €)
- Tote Imports (`subprocess`, `platform`) entfernt

**ARP-Compliance (v3.12.0):**
- ARP-Leitfaden + E-Mail Tönnies vollständig analysiert
- `ActualDeliverySupplyChainEvent` jetzt immer gesetzt (Pflichtfeld ARP)
- `ExchangedDocument/Name` mit Rechnungstext befüllt
- Musterrechnung RE2026050004 Feld für Feld gegen Leitfaden geprüft
- ⚠️ ARP Seller-ID und GWG Receiver-ID noch manuell zu konfigurieren!

**Neue Features (v3.13.0):**
- SMTP-Konfiguration in Einstellungen (Host, Port, User, Passwort, TLS)
- Direkter E-Mail-Versand aus dem Tool + Test-Mail-Funktion
- 38 automatisierte Tests für XML-Kern und Berechnungen
- Rechnungsarchiv mit Filtern (Jahr, Monat, Status) + direktem Download
- Mahnautomat: `get_dunning_candidates()`, Batch-Mahnung, Dashboard-Warnung

**Rechnungserstellung optimiert (v3.14.0):**
- AJAX-Autobefüllung bei Kundenauswahl (neuer API-Endpoint `/api/kunden/<pk>/daten/`)
- Objekt-Autobefüllung (filtert Dropdown auf Kundenobjekte)
- Live-Positionskalkulation (Summen in Echtzeit)
- Fälligkeitsdatum-Vorschau
- Kundenschnellsuche im Dropdown

**UI & Design (v3.15.0 + v3.16.0):**
- Toast-Nachrichten (Slide-in, farbcodiert, Auto-Dismiss)
- Print-CSS (`Ctrl+P` blendet UI aus)
- Verbesserte Leer-Zustände (Icon + Text + CTA-Button)
- PDF Executive-Design: Navy-Header mit Logo, Navy-Footer, blauer Tabellenkopf, Summenbox
- GiroCode-QR für SEPA-Direktzahlung ins PDF integriert
- ENTWURF-Wasserzeichen bei Entwurfsrechnungen
- `|split`-Template-Bug in Rechnungsliste behoben

### 06.06.2026 – Projektübergabe zu Cowork (Beginn der Session)
- ChatGPT-Verlauf (3 Chats) + Programmcode in Claude importiert
- Projektkontext, MEMORY.md und CLAUDE.md für Cowork-Projekt erstellt

### 29.05.2026 – Termin mit Tönnies vorbereitet
- Erklärt: Capture vs. E-Rechnungen Eingangsweg
- Erklärt: Mapping-Logik (SEDO liefert Schlüssel, Aareon löst Kontierung auf)

### Mai 2026 – Kernentwicklung
- ZUGFeRD-XML-Logik implementiert
- ShipToTradeParty-Felder korrekt gesetzt
- Leistungsart-ID positionsweise implementiert
- PDF-Leistungsort Fix (v1.0 beta 3.11.5)
