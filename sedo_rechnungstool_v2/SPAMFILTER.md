# Spamfilter für gemeinsame Firmenpostfächer

Im E-Mail-Bereich öffnet das Zahnrad am Postfach dessen Einstellungen.
Unter **Spamfilter & Absenderregeln** verwaltet die Administration den Filter
je Postfach. Die Vorgabe ist **Nur markieren**. Die bisherigen Serverordner
bleiben bestehen; genau ein Spamordner und ein Posteingang müssen zugeordnet sein.

## Bedienung

- **Spam prüfen** zeigt Spam und Verdachtsfälle aus den Posteingängen und
  Spamordnern aller Konten. Der Zähler zählt ungelesene Nachrichten.
- **Als Spam markieren / Kein Spam** öffnet eine Vorschau und verschiebt nach
  Bestätigung in den zugeordneten Ordner. Eine dauerhafte Absenderregel entsteht
  nur über die separate Einstellung. Die manuelle Entscheidung bleibt erhalten.
- Checkboxen aktivieren die Auswahlleiste. Bis zu 25 Nachrichten lassen sich
  gemeinsam bearbeiten. Die Vorschau nennt die betroffenen Postfächer; bei
  Teilfehlern werden die betroffenen Nachrichten einzeln aufgeführt.
- **Spamprüfung ansehen** prüft auch ausdrücklich ausgewählten Altbestand.
  Übernahme markiert nur; die Nachrichten bleiben im bisherigen Ordner.
- Einstellungen liegen hinter dem Zahnrad, Erklärungen hinter dem umkreisten i.
  Die optionale Dashboard-Kachel **Spam prüfen** wird über die Kachelauswahl aktiviert.
  Bestätigter Spam zählt nicht als neue Posteingangsmail. Verdachtsfälle bleiben
  im normalen Eingang sichtbar und zählen dort weiter mit.

## Regeln und Automatik

**Aus** deaktiviert lokale Inhalts-/Regelprüfung. Manuelle Entscheidungen und
der tatsächliche Server-Spamordner bleiben sichtbar. **Nur markieren** bewertet
lokal, verschiebt aber nichts automatisch. **Nach Regeln sortieren** benötigt
eine ausdrückliche Bestätigung und verschiebt nur nach passenden Sperrregeln.

Eine genaue Adresse hat Vorrang vor einer Domainregel. Domainregeln treffen exakt
die genannte Domain, keine Unterdomains oder ähnlich endenden Angreiferdomains.
Gegensätzliche Regeln für dieselbe Adresse/Domain werden abgewiesen. Domainweite
Regeln benötigen eine gesonderte Bestätigung. Freigaberegeln beweisen keine
Absenderidentität und holen nichts ungefragt aus dem Server-Spamordner zurück.

Nach Aktivierung oder Regeländerung bildet der nächste erfolgreiche Ordnerabruf
eine Ausgangsgrenze anhand aller vorhandenen Server-UIDs. Erst spätere UIDs dürfen
automatisch verschoben werden. Das schützt auch noch nicht heruntergeladenen
Altbestand. Bei UIDVALIDITY-Wechsel wird die Grenze neu gebildet. Bereits mit
Kunden, Angeboten, Aufgaben, Antworten oder Eingangsbelegen verknüpfte Nachrichten
werden nie automatisch verschoben. Die Automatik ist pro Abruf begrenzt.

Inhaltsmerkmale wie irreführende Linktexte, IP-/verschleierte Linkziele,
ausführbare Anhänge und dringende Anmeldeaufforderungen ergeben erst in Kombination
einen **Verdachtsfall**. Diese Merkmale lösen keine automatische Verschiebung aus.
Unvollständige, mehrdeutige oder nicht lesbare Nachrichten bleiben zur Prüfung.
Unbekannte Absender und einzelne Wörter wie „Rechnung“ reichen nicht aus.

SEDO nutzt keine KI, keine externen Reputationsdienste und ruft keine Mail-Links
auf. X-Spam- und Authentifizierungsheader sind nicht als vertrauenswürdig angebunden:
die tatsächlichen Providerheader wurden für diese Konten noch nicht verifiziert.
Der Server-Spamordner ist die einzige übernommene Provider-Einstufung. Vorhandene
United-Domains-Einstellungen werden durch dieses Feature nicht verändert.

## Belegeingang, Ausfälle und Wiederherstellung

Spam und ungeklärte Verdachtsfälle blockieren neue Eingangsbelege sowie neue
Angebote/Aufgaben aus der Mail, auch bei direktem Formularaufruf. Nach **Kein Spam**
und bestätigtem Eingang kann die Verarbeitung fortgesetzt werden. Vorhandene
Originalbelege und Quellen werden weder gelöscht noch nachträglich verändert.

Verschiebungen verwenden IMAP MOVE unter der bestehenden Postfachsperre und prüfen
UID/UIDVALIDITY. Fehlt MOVE oder ein eindeutiger Zielordner, bleibt die Markierung
sichtbar; die Mail wird nicht gelöscht. Vor dem Serverbefehl wird ein Versuch
gespeichert. Unklare Antworten werden nie automatisch wiederholt. Ein erneuter
vollständiger Ordnerabgleich klärt den Stand, soweit eindeutig möglich. Andernfalls
kann der Benutzer nach Prüfung im Webmail den Status ausdrücklich bestätigen.
Dieser Prüfschritt selbst verschiebt nichts. Ein früherer Versuch verhindert
erneute automatische Verschiebung derselben Nachricht.

Regeln, manuelle Korrekturen und Verschiebungsprotokolle liegen in der regulär
gesicherten Datenbank. Geschäftliche Originalquellen bleiben wie bisher gesichert.
Wiederhergestellte Postfächer bleiben bis zur erneuten Zugangseinrichtung pausiert.
Der Filter löscht keine Nachrichten und lernt nicht automatisch aus Korrekturen.
Ein trainierbarer Filter und verifizierte zusätzliche Providerbewertungen sind
spätere Erweiterungen. Unter Windows ist eine eigene Plattformprüfung nötig.
