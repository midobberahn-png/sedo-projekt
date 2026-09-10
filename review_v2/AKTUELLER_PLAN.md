# SEDO – verbindlicher Arbeitsplan und Abnahmestand

Stand: 10.09.2026. Von Mirek freigegebene Neuordnung nach PR #1–6.
Ausgangsstand: `c6cfbe08d22ad406b8c3a46bb1f3b8abd0fd6321`.
Diese Datei ist die aktuelle Übersicht; frühere Übergaben bleiben historische Quellen.

## Maßstab und Einschränkung

Ein verständliches Zustandsmodell und ein nachweislich funktionierender vollständiger
Arbeitsablauf sind der Maßstab – nicht allein Anzahl der Tests oder geschlossener Funde.
Es gab noch keinen Echtbetrieb; alle bisherigen Daten sind Testdaten.

**Papas Einsatzrechner steht frühestens am 19.09.2026 für die Abnahme zur Verfügung.**
Die neun Tage Abwesenheit sind kein Entwicklungsstopp und keine Zusage, bis dahin
alle Aufgaben abgeschlossen zu haben. CI oder ein Test auf einem anderen Rechner
werden nicht als bestandene Abnahme auf Papas Rechner ausgegeben.

## Reihenfolge

| Block | Inhalt | Stand |
|---|---|---|
| H1 | Leere Abo-Auswahl verhindern; manuelle Mahnfälligkeit/Restforderung (F-68) | Umsetzung und lokale Prüfung in diesem Arbeitsblock; PR/Integration siehe Abschlussvermerk |
| H2 | Gemeinsame PR/main-CI; versionierter, reproduzierbarer Teststand; Abhängigkeiten festschreiben | Gemeinsame CI in H1 ergänzt; Release-Lock und Testbuild noch offen |
| H3 | Welle 3: Zustandsvertrag, zentrale Schreibfunktionen, kontrollierte Feldtrennung und passende DB-Bedingungen | Offen; keine neue Feldmigration in H1 |
| H4 | Übrige Funde gegen aktuellen Code neu priorisieren: fachliche Validierung, Datenverlust, Zugriff/Datenschutz, Exporte und Wiederherstellung vor Kosmetik | Offen; alte Fundliste ist keine aktuelle Gesamtfreigabe |
| H5 | Zusammenhängende Ablauf-, Update-, Backup-/Restore- und E-Rechnungsvalidierung am reproduzierbaren Teststand | Offen; bisherige Einzeltests ersetzen diese Abnahme nicht |
| UX | Nach technischer Härtung: vorhandenes UI/UX systematisch prüfen und gezielt aufwerten, kein blinder Neuaufbau | Anschließend; H1 verändert nur notwendige Sicherheitsbedienung |
| R | Echte Installation und vollständiger Ablauf auf Papas Einsatzrechner, einschließlich Beenden/Neustart/Update | Ausstehend; frühestens 19.09.2026 |

Die Rechnerprüfung ist ein eigener Freigabeschritt, kein Hindernis für H2–H5.
UI/UX kann nach der technischen Härtung auch vor dem Rechnertermin beginnen;
für eine Produktivfreigabe bleiben Rechner- und Gesamtprozessabnahme erforderlich.
Kein Hintergrundauftrag, kein automatischer Termin oder Versand eingerichtet.

## Fachlicher Vertrag für H1

### Abo-Auswahl

- Keine Vorauswahl und kein Rückfall auf alle aktiven Vorlagen bei leerem POST.
- „Alle aktiven Vorlagen auswählen“ ist eine getrennte Auswahlaktion, keine
  Rechnungsanlage. Der Startknopf verarbeitet anschließend nur die konkreten IDs.
- Pausierte Vorlagen sind nicht auswählbar; ungültige/unbekannte/pausierte IDs in
  einer gemischten Auswahl brechen die gesamte Prüfung vor der ersten Rechnung ab.
- Doppelte IDs innerhalb eines Aufrufs werden zusammengefasst. Eine neue Vorlage
  erweitert eine ältere Auswahl nicht. Das ist keine vollständige Abo-Idempotenz
  über mehrere getrennte HTTP-Aufrufe; diese bisherige Regel bleibt separat.
- Ohne JavaScript kann einzeln ausgewählt und abgesendet werden; die
  Sicherheitsgrenze ist auf dem Server, nicht am deaktivierten Knopf.

### Mahnung / Erinnerung

- Für jeden Erstellungsweg muss das Fälligkeitsdatum **vor** dem heutigen lokalen
  Datum liegen und der tatsächliche Restbetrag positiv sein.
- Der Fälligkeitstag ist noch Zahlungstag; auch eine sofort fällige Rechnung
  wird nicht schon am selben Tag angemahnt. Ohne Fälligkeitsdatum keine erfundene Frist.
- Aktive Zahlungen entscheiden über den Restbetrag, nicht ein alter Status oder Cache.
- Beide Bedingungen werden im verbindlichen Generator nach Erwerb der bestehenden
  SQLite-Schreibsperre geprüft. UI, öffentliche Vorprüfung und Stufenabfrage nutzen
  dieselbe Regel. Belegketten-/Archivschutz und Pflichtaudit bleiben bestehen.
- Bewusste manuelle Wahl von Erinnerung/Mahnung bleibt erhalten. Automatikschwelle
  von 14 Tagen und Erinnerungsfrist werden nicht verkürzt. Keine neue juristische
  Verzugsaussage; dies ist die freigegebene vorsichtige Programmregel.

## Zustandsarchitektur für H3

Belegzustand (Entwurf/ausgestellt/korrigiert/storniert) ausdrücklich von Zahlung,
Überfälligkeit, Versand und Mahnungen trennen. Zahlungszustand aus aktiven Zahlungen
ableiten; Überfälligkeit berechnen; Versand/Mahnungen durch ihre Nachweise abbilden.
Nicht für jede Anzeige ein weiteres konkurrierendes Statusfeld speichern.

Schreibwege über wenige zentrale Fachfunktionen mit aktuellen Prüfungen,
Transaktion und Pflichtaudit. `pre_save`-Signale sind höchstens ergänzend, keine
alleinige Sperre; QuerySet-Updates umgehen sie. Übergang des alten Statusfeldes
kontrolliert und endlich planen, keine dauerhafte Doppelwahrheit. Bestehende
Entscheidungen zu mildem Ausblenden, erneutem Versand und unangetasteter
Dashboard-Umsatzkachel bleiben bestehen.

## Nachweispflicht je Block

Ein Fund hat getrennte Zustände: offen, umgesetzt, getestet, integriert,
im Testbuild geprüft, am Einsatzrechner abgenommen oder ausdrücklich zurückgestellt.
Prüfberichte nennen den tatsächlichen Code-Commit und ihre Umgebung; ein roter
Gegenprobentest zählt nur bei erwarteter Assertion, nicht bei kaputtem Setup.

Nach Härtung UI/UX: eindeutige Zustände und nächste Aktionen, verständliche
Fehlermeldungen, sichere Sammelläufe, weniger unnötige Schritte, konsistente
Bedienelemente, Tastatur-/Fokusführung und lesbare Tabellen. Die vorhandene
Gestaltung ist Ausgangspunkt; Verbesserungen an echten Arbeitsabläufen messen.
