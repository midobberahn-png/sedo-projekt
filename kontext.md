# Projektkontext: SEDO Rechnungstool

## Was ist das Projekt?

SEDO ist ein selbst entwickeltes **Django-Webtool zur Erstellung von ZUGFeRD-konformen E-Rechnungen** für ein Dienstleistungsunternehmen im Bereich Gebäudereinigung.

Das Tool läuft lokal auf dem Rechner des Nutzers (Mac & Windows) und erzeugt ZUGFeRD-PDFs mit eingebetteter XML, die an Großkunden wie GWG/Aareon übermittelt werden.

---

## Technischer Stack

- **Framework:** Django 5.2+
- **Sprache:** Python
- **PDF-Erzeugung:** ReportLab + pypdf + qrcode[pil] (GiroCode)
- **XML:** lxml (ZUGFeRD / Factur-X / CII)
- **Daten:** pandas, openpyxl
- **KI:** Anthropic Claude (Tool-Use) · Diktat lokal via faster-whisper (Fallback: Browser)
- **Datenbank:** SQLite (`data/sedo_web.sqlite3`)
- **Konfiguration:** `.env` nach Muster `.env.example` (wird seit v3.21 automatisch geladen)
- **Start:** `START_HIER.command` (Mac) / `START_WINDOWS.bat` (Windows)
- **Lokale URL:** http://127.0.0.1:8000
- **Login:** admin – Passwort wird beim allerersten Start einmalig erzeugt und angezeigt
  (ändern/zurücksetzen: `python manage.py changepassword admin`)

---

## Aktuelle Version

**v2.0** – Stand 14. August 2026 (Release-Paket `sedo_rechnungstool_v2`, Code-Stand beta 3.23.1)

Härtung nach zweitem externem Review: Workflow-Sperren (Storno/Korrektur/
Angebot→Rechnung idempotent, Status „korrigiert“), Mahnstufen-Modell,
Cross-Customer-Sperre, Zahlungsimport mit Bank-Transaktions-ID, atomare
Datei-Erzeugung, GWG-Monatslauf-Unique-Constraint, zentrale
NumberRange-Nummernkreise, Angebots-Soft-Delete, DEBUG=0-Default + WhiteNoise –
**217 Tests**, Migrationen 0007–0011.
Details: `AENDERUNGSBERICHT_v1_0_beta_3_23_0.md` im Versionsordner
`SEDO_Rechnungstool_Django_v1_0_beta_3_23_0` (dort auch der v3.22-Bericht).

---

## Kernstruktur des Projekts

```
config/           Django-Konfiguration (settings mit .env-Loader, urls, wsgi)
sedo/
  models.py       Datenmodelle (Invoice/Payment mit GoBD-Soft-Delete)
  views.py        Alle Views inkl. KI-Endpoints
  forms.py        Formulare
  services/       Service-Package (12 Module):
                  numbering · calc · invoices · pdf · pdf_chrome · dunning ·
                  payments · mail · ai · transcription · plausibility · formatting
  core/
    rechnungslauf_core.py   Kernlogik ZUGFeRD-XML + Rechnungs-PDF (Django-frei)
  templates/sedo/ Alle HTML-Templates (SVG-Icon-Sprite in base.html)
  static/sedo/    app.css, wizard.js, offer_ai.js, magic_tone.js, plausibility.js
  tests/          217 automatisierte Tests
data/             SQLite-Datenbank
```

---

## ZUGFeRD / XML-Logik (kritisch für Aareon/GWG)

### Was SEDO in die XML schreibt:

**Abrechnungseinheit (= Liegenschaftsnummer):**
```xml
<ram:ShipToTradeParty>
  <ram:ID>Asternweg 2/1</ram:ID>
  <ram:Name>Asternweg 2/1</ram:Name>
  <ram:PostalTradeAddress>
    <!-- Objektadresse -->
  </ram:PostalTradeAddress>
</ram:ShipToTradeParty>
```

**Leistungsart-ID (je Position wenn mehrere Leistungsarten):**
```xml
<ram:IncludedNote>
  <ram:Content>Leistungsart: LeistungsartID:Reinigung;LeistungsartTEXT:Reinigung</ram:Content>
</ram:IncludedNote>
```

### Mapping-Logik bei GWG/Aareon:
- SEDO liefert: **Abrechnungseinheit** + **Leistungsart-ID**
- Aareon-Mapping löst daraus auf: Wirtschaftseinheit, NkSl, Sachkonto, gültig ab
- Diese Werte müssen **nicht** von SEDO in die XML geschrieben werden

### Wichtige Regel:
Der Wert in `ShipToTradeParty/ID` muss **exakt** mit Spalte A „Rechnungssteller Abrechnungseinheit" in der Aareon-Mapping-Datei übereinstimmen (Schreibweise, Slash, Leerzeichen).

---

## BT-Felder Kontext

| BT-Feld | Bedeutung | In SEDO |
|---------|-----------|---------|
| BT-75 | Adresszeile 1 der Liefer-/Leistungsanschrift | Objektstraße |
| BT-77 | Ort der Lieferadresse | Objektort |
| BT-80 | Land der Lieferadresse | DE |
| ShipToTradeParty/ID | Abrechnungseinheit (Aareon-Schlüssel) | Spalte A |
| ShipToTradeParty/Name | Objektname / Leistungsort | Objektname |

---

## Aareon/GWG Besonderheiten

- **Eingangsweg:** Rechnung muss über **„E-Rechnungen"** importiert werden, **nicht** über „Capture"
- Capture = OCR-Belegverarbeitung, liest ZUGFeRD-XML nicht aus
- E-Rechnungen = strukturierte XML wird ausgewertet, Mapping greift

---

## Offene Fragen / Noch zu klären

- [ ] Bestätigung von Tönnies (GWG): Welcher Wert wird exakt gegen Mapping gematcht?
- [ ] Werden Kontierungswerte (Sachkonto, NkSl) wirklich komplett durch Mapping erzeugt oder müssen einige doch direkt in XML?
- [ ] Testrechnung erfolgreich über „E-Rechnungen" (nicht Capture) importiert?
- [ ] **Seit v3.21 geänderte XML-Felder mit Tönnies gegenprüfen:** BT-154
      (Leistungsbeschreibung) und BT-130 (Mengeneinheiten – jetzt m²→MTK, Monat→MON
      statt bisher immer H87/Stück)

---

## Hintergrund: Warum SEDO existiert

Ausgangslage: WISO MeinBüro Desktop kann BT-75 (Leistungsort) nicht sauber als semantisches E-Rechnungsfeld setzen. Größter Kunde (GWG über Aareon) verlangt korrekte ZUGFeRD-Felder für automatische SAP-Kontierung.

Entscheidung: Eigenes Rechnungstool entwickeln statt teurem Softwarewechsel.
