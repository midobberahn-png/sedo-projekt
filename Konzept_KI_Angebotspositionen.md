# Konzept – KI-gestützte Angebotspositionen per Diktat

> SEDO Rechnungstool · Feature-Konzept · Stand: 26.07.2026
> Ziel-Version: v1.0 beta 3.17.0

---

## 1. Ziel

Angebotserstellung beschleunigen, indem die **Positionen** nicht mehr getippt,
sondern **diktiert** werden. Ein LLM wandelt das gesprochene Diktat in
strukturierte Angebotspositionen um und befüllt damit den Positionsteil der
bestehenden Angebotsmaske. Hintergrund: das manuelle Eintippen kostet aktuell
viel Zeit; das Sprechen ist der eigentliche Zeitgewinn.

---

## 2. Scope – bewusst eng geschnitten

**IN Scope (KI/Cloud):**
- Diktat der Leistungspositionen (Bezeichnung, Menge, Einheit, Einzelpreis)
- Kurze, sachliche Angebotstexte je Position
- Plausibilitätsprüfung + Hinweise (fehlender Preis, Menge ohne Einheit, auffälliger €/m²-Wert)

**AUS Scope (bleibt lokal / manuell):**
- **Kundendaten und Objektdaten** – werden wie bisher manuell ausgewählt
  (bestehende lokale Autofill-Logik `customer_data_api`). Sie verlassen den
  Server **nicht**.
- Preisfindung – die KI **erfindet keine Preise**, sie überträgt nur, was
  diktiert wurde, und markiert Lücken.

---

## 3. Datenschutz

Da ausschließlich **Leistungspositionen** (keine personenbezogenen Daten) an die
Cloud gehen, ist der Verarbeitungsumfang unkritisch. Zusätzliche Absicherungen:

- UI-Hinweis: „Bitte nur die Leistungen diktieren, keine Namen/Adressen."
- Prompt-Schutz: das Modell ignoriert erkannte Namen/Adressen und übernimmt sie
  nicht in die Positionen.
- Ein AV-Vertrag (DPA) mit dem Anbieter bleibt empfohlene gute Praxis, ist für
  den Start aber kein harter Blocker mehr.

---

## 4. Datenfluss

```
[Kunde + Objekt manuell wählen]   ← lokal, unverändert
            │
            ▼
[Mikrofon: Positionen diktieren]
            │  Audio
            ▼
[Speech-to-Text (Whisper)]  → Transkript
            │
            ▼
[LLM mit striktem JSON-Schema]
            │  positions[] + texte + warnungen
            ▼
[JavaScript befüllt das Positions-Formset]
            │
            ▼
[Papa prüft/korrigiert → speichert]
            │
            ▼
[recalc_offer() + generate_offer_pdf()]   ← unverändert
```

---

## 5. JSON-Schema (Modell-Ausgabe)

Spiegelt exakt die Felder von `OfferItem`:

```json
{
  "positionen": [
    {
      "bezeichnung": "Unterhaltsreinigung Treppenhaus",
      "menge": 220,
      "einheit": "m²",
      "einzelpreis": 0.45,
      "kurztext": "Monatliche Unterhaltsreinigung der Treppenhausflächen.",
      "warnung": null
    }
  ],
  "globale_hinweise": [
    "Position 2 ohne Preis diktiert – bitte ergänzen."
  ]
}
```

---

## 6. Neue Bausteine (an bestehende Architektur angelehnt)

| Ort | Ergänzung |
|-----|-----------|
| `services.py` | `transcribe_audio()` + `extract_offer_positions_from_text()` als **austauschbarer Adapter** (Anbieterwechsel = eine Zeile) |
| `views.py` | Endpoint `/angebote/ki-positionen/` (nimmt Audio/Text, gibt JSON) – parallel zu `customer_data_api` |
| `offer_form.html` | Mikrofon-Button + Freitextfeld über dem Positions-Formset; JS befüllt Zeilen live (gleiche Technik wie bestehendes Autofill) |
| `AppSetting` | API-Key-Feld – exakt wie bereits für SMTP gelöst |
| `tests/` | Extraktions-Test mit **gemockter** LLM-Antwort (prüft Feld-Zuordnung ohne echten Key) |

---

## 7. Plausibilitätsregeln (erste Version)

- Position ohne Einzelpreis → Warnung „Preis fehlt"
- Menge ohne Einheit → Warnung „Einheit fehlt"
- Reinigungsleistung mit €/m² außerhalb Bandbreite → Hinweis „Preis prüfen"
- Position ohne erkennbare Menge → als Pauschale (Menge 1) markiert

---

## 8. MVP vs. später

**MVP (v3.17.0):**
- Diktat → Transkript → strukturierte Positionen → Formset befüllt
- Kurztexte + Basis-Plausi-Check
- Anbieter: OpenAI (Text + Whisper aus einer Hand), über Adapter austauschbar

**Später:**
- Erweiterte Plausi-Regeln / Preisbänder pro Leistungsart
- Optional lokales Whisper/LLM als Alternative
- Übertragung des Musters auf die Rechnungsmaske

---

## 9. Offene Punkte vor dem Bauen

1. Freigabe: OpenAI als Startanbieter? (jederzeit austauschbar)
2. API-Key liegt für den ersten Live-Test bereit (Einstellungen)
3. Realistische €/m²-Bandbreiten für die Plausi-Prüfung (von Papa)
