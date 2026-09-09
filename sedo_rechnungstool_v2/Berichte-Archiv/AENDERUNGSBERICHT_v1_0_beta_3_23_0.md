# Änderungsbericht v1.0 beta 3.23.0 – UI-Feinschliff nach Live-Test (14.08.2026)

**Anlass:** Mireks erster Live-Test der v3.22 (KI-Angebotsstrecke) mit sechs
konkreten Anmerkungen plus genereller Design-Kritik („zu kitschig, nicht
business genug – SEDO-Farben analog Logo erhalten“). Keine Modell-/
Migrationsänderungen; Migrationen bleiben 0007–0011.

**Ergebnis:** 217 Tests grün, alle Punkte im Browser live verifiziert
(Kundensuche, ⓘ-Tooltips, Positionen, Monatsrechnung, Dashboard) und
Muster-Angebots-PDF geprüft.

## 1. Kundensuche mit echter Trefferliste (Bild 2)

- Vorher versteckte die „Suche“ nur Einträge im Dropdown – beim Tippen
  erschien nichts, und der Fokus aufs Dropdown setzte den Filter zurück.
- Neu: `customer_search.js` (Angebot + Rechnung gemeinsam) zeigt eine
  Autocomplete-Liste unter dem Suchfeld; Klick oder Enter wählt den Kunden
  und löst die bestehende Autobefüllung aus. Pfeiltasten/Escape unterstützt,
  „Kein Kunde gefunden“-Hinweis, ×-Knopf leert das Feld.

## 2. ⓘ-Hinweis-Pattern (Bilder 3 + 5, „generell“)

- Neue CSS-Komponente `.info-tip`: langer Hinweistext liegt hinter einem
  kleinen i-Symbol und erscheint bei Hover/Fokus als Tooltip.
- Angewandt auf: KI-Assistent-Schritt (Beispiele/Datenschutz/Kalkulation),
  Positionen-Schritt, Prüfen-Schritt (Vorschau/Erstellen bzw.
  Entwurf/Erstellen/Versenden), Plausibilitäts-Check (KI-Check-Hinweis),
  Monatsrechnungs-Seite (Seiten- und Vorlagen-Hinweis).

## 3. Positionen (Bild 4)

- Es startet genau EINE leere Position (vorher zwei: `min_num=1` + `extra=1`
  ergab doppelte Leerformulare; jetzt `extra=0` – auch bei Abo-Vorlagen).
- „+ Position hinzufügen“ steht jetzt UNTER der jeweils letzten Position
  (gestrichelte, volle Breite) – in Angebot und Rechnung.
- Positionskarten kompakter: flaches Weiß statt Verlauf, engere Abstände,
  kleinere Feldhöhen.

## 4. Vorschau-Rücksprung (Bild 5)

- „Zurück zur Eingabe“ führt jetzt in den LETZTEN aktiven Schritt (Prüfen)
  statt zu Schritt 1: der Assistent merkt sich den Schritt je Seite
  (sessionStorage) und stellt ihn bei Browser-Zurück-Navigation wieder her.

## 5. Angebots-PDF-Texte

- Persönliche Anrede, wenn Anrede + Ansprechpartner hinterlegt sind
  („Sehr geehrter Herr Mustermann,“), sonst neutral.
- Neue Einleitung („vielen Dank für Ihre Anfrage …“), Schlussteil mit
  Gültigkeitsdatum, Gesprächsangebot und Zusicherung sorgfältiger,
  termingerechter Ausführung, dazu Grußformel + Firmenname.
- Firmenname in Angebots- und Mahn-PDF kommt jetzt zentral aus
  `SELLER['name']` (vorher einmal „SEDO“, einmal „Sedo“ hart codiert).

## 6. Monatsrechnung (Bild 6)

- Prozessleiste markiert nur noch Schritt 1 als aktiv (vorher 1 UND 2).
- Seiten-/Vorlagen-Hinweise hinter ⓘ.

## 7. Business-Design-Pass (SEDO-Farben bleiben)

- Flächen-Verläufe entfernt (Seite, Detail-Hero, Positionskarten,
  Summen-Chip) → flache weiße Karten; Detail-Hero mit Navy-Akzentkante
  statt Farbverlauf + Orange-Kreis.
- Aktionskarten-Icons sachlich (Navy auf hellem Blau statt orange
  gestrichelt), Dichte insgesamt erhöht: kleinere Feldhöhen (42→38 px),
  kompaktere Dialogköpfe/Grids, ruhigere Schriftgrößen (Hero 38→28,
  Dialogtitel 28→21, Gesamtsumme 30→24).
- Sidebar-Navy und Orange-Akzente (Logo-Farben) unverändert.

## 8. Kleinigkeiten

- Fußzeile zeigt jetzt korrekt **v1.0 beta 3.23.0** (stand fälschlich noch
  auf 3.21.0 – vergessener Bump in v3.22).
- Cache-Buster aller geänderten CSS/JS auf `?v=3_23` (Browser laden die
  neuen Dateien ohne Handstand).

## 9. Positionen als Tabellenzeilen (Variante A, nach Vergleichsseite)

- Mirek wählte per Vergleichsseite („SEDO Positionsmasken“, drei Varianten)
  das klassisch kaufmännische Tabellen-Layout: eine Zeile pro Position
  (Pos. · Bezeichnung · Menge · Einheit · Einzelpreis · Gesamt · ×) wie in
  Lexoffice/sevdesk – identisch in Angebot UND Rechnung.
- Leistungsbeschreibung (und bei Rechnungen Artikelnr./Leistungsart) liegt in
  einer einklappbaren Folgezeile: „+ Beschreibung“ öffnet sie; sobald Text
  vorhanden ist (manuell, per KI oder vom Server), bleibt sie offen
  (`positions_table.js`; die KI-Befüllung feuert input-Events).
- Technik: jede Position ist ein `<tbody class="position-form">` – die
  bestehende JS-Logik (Live-Summen, Hinzufügen, Neunummerierung, KI-Befüllung,
  Magic-Tone, KI-Markierungen) läuft unverändert über dieselben Selektoren.
  Wichtig: die alte Karten-Regel `#position-list{display:flex}` musste weg –
  sie machte aus der Tabelle einen Flex-Container (Spalten liefen auseinander);
  außerdem `table-layout:fixed` für stabile Spaltenbreiten.
- Eingabefelder global sachlicher (Mireks „Textfelder sind noch nicht meins“):
  Radius 10→7 px, kompaktere Polsterung, kein Innenschatten, ruhigerer
  Navy-Fokusring, kleinere Labels.

## Verifikation

- 217/217 Tests grün (Django 6.0.8), keine Migrationsänderungen (nach jedem Umbau erneut).
- Live im Browser geprüft: Suche→Treffer→Autobefüllung, ⓘ-Tooltips,
  1 Startposition + Button unten, Monatsrechnungs-Leiste, Dashboard-Optik.
- Muster-Angebot AG…0001 als PDF erzeugt und Texte/Layout gegengelesen.
