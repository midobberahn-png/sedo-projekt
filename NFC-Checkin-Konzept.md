# NFC Check-in System — Konzept & Architektur

**Projektname:** NFC Check-in  
**Stand:** 25.06.2026  
**Stack:** React Native (Expo) · TypeScript · Supabase  

---

## 1. Ziel

Mitarbeiter weisen nach, dass sie zu einem bestimmten Zeitpunkt an einer Adresse / einem Objekt waren und die Leistung ausgeführt haben. Der Nachweis erfolgt durch Scannen eines NFC-Stickers (am Objekt angebracht) mit dem Smartphone. Das System erfasst automatisch Zeitstempel und GPS-Koordinaten und speichert alles in einer Cloud-Datenbank. Vorgesetzte können alle Check-ins im Web-Dashboard einsehen.

---

## 2. Tech-Stack

| Schicht | Technologie | Begründung |
|---|---|---|
| Mobile App | React Native + Expo | iOS & Android aus einer Codebasis; Expo NFC-Support |
| Sprache | TypeScript | Typsicherheit, bessere Wartbarkeit |
| Backend / DB | Supabase (PostgreSQL) | Open-Source, Row Level Security, kostenfrei startbar |
| Web-Dashboard | React (Next.js) oder Supabase Studio | Einfache Einbindung, gleiche API |
| NFC | `expo-nfc-manager` | Liest NDEF-Tags auf iOS & Android |
| Standort | `expo-location` | GPS-Koordinaten im Hintergrund |

---

## 3. Datenbankschema

### `employees`
```
id          UUID PRIMARY KEY
name        TEXT NOT NULL
device_id   TEXT UNIQUE NOT NULL   -- Geräte-Fingerprint (einmalig beim Setup)
active      BOOLEAN DEFAULT true
created_at  TIMESTAMPTZ DEFAULT now()
```

### `locations`
```
id           UUID PRIMARY KEY
name         TEXT NOT NULL          -- z.B. "Musterstraße 12, Berlin"
address      TEXT
nfc_tag_id   TEXT UNIQUE NOT NULL   -- auf dem Sticker gespeicherte UUID
notes        TEXT                   -- optionale Infos zum Objekt
active       BOOLEAN DEFAULT true
created_at   TIMESTAMPTZ DEFAULT now()
```

### `checkins`
```
id           UUID PRIMARY KEY DEFAULT gen_random_uuid()
employee_id  UUID REFERENCES employees(id)
location_id  UUID REFERENCES locations(id)
checked_in_at TIMESTAMPTZ DEFAULT now()
gps_lat      NUMERIC(10,7)
gps_lon      NUMERIC(10,7)
gps_accuracy NUMERIC(6,2)          -- in Metern
created_at   TIMESTAMPTZ DEFAULT now()
```

---

## 4. NFC-Tag Encoding

Jeder Sticker speichert einen NDEF-Record mit einer UUID:

```
Typ:    text/plain
Inhalt: "SEDO-LOC:<UUID>"
```

Beispiel: `SEDO-LOC:3f8a2c1b-4d9e-4a2f-8c3d-1b2e3f4a5c6d`

Das Präfix `SEDO-LOC:` stellt sicher, dass die App fremde Tags ignoriert. Die UUID wird in der `locations`-Tabelle als `nfc_tag_id` hinterlegt.

**NFC-Tags programmieren:**
- App: NFC Tools (iOS/Android) — kostenlos
- Alternativ: eigene kleine Admin-Funktion in der App

---

## 5. App-Flows

### 5.1 Einmaliger Geräte-Setup (Admin)
```
App starten
  → Gerät noch nicht registriert?
    → Admin-Code eingeben (6-stellig, in Supabase hinterlegt)
    → Mitarbeiter aus Liste auswählen
    → Gerät-ID wird gespeichert (Supabase: employees.device_id)
    → Setup abgeschlossen ✓
```

### 5.2 Normaler Check-in (Mitarbeiter)
```
App öffnen
  → Automatisch Mitarbeiter-Name anzeigen (via device_id)
  → "Jetzt einchecken" Button
  → NFC bereit: Handy an Sticker halten
  → Tag wird gelesen → "SEDO-LOC:<UUID>" ausgelesen
  → UUID in DB abfragen → Objekt-Name gefunden
  → GPS wird automatisch erfasst
  → Check-in in Supabase gespeichert
  → ✅ Bestätigung: "Eingestempelt bei [Objekt] um [Uhrzeit]"
```

### 5.3 Fehlerbehandlung
- Tag unbekannt → Fehlermeldung, kein Check-in gespeichert
- GPS nicht verfügbar → Check-in trotzdem speichern (gps_lat/lon = NULL)
- Kein Internet → Lokal zwischenspeichern, bei Verbindung synchronisieren

---

## 6. Web-Dashboard (Vorgesetzte)

**URL:** Eigene Domain oder Supabase-basiert  
**Zugang:** E-Mail + Passwort (Supabase Auth)

### Features:
- **Übersicht:** Alle Check-ins, neueste zuerst
- **Filter:** nach Mitarbeiter, Objekt/Adresse, Datum/Zeitraum
- **Detailansicht:** Zeitstempel, GPS-Koordinaten (mit Google Maps Link)
- **Export:** CSV-Download für Abrechnungszwecke
- **Objekte verwalten:** Neue Standorte anlegen, NFC-Tag-UUID zuweisen
- **Mitarbeiter verwalten:** Neue Mitarbeiter, Geräte-Reset

---

## 7. Sicherheit

- **Row Level Security (Supabase):** Mitarbeiter können nur eigene Check-ins lesen; Vorgesetzte sehen alle
- **Gerätebindung:** Kein Passwort nötig — das Gerät ist der Ausweis
- **Admin-Code:** Nur Admins können neue Geräte registrieren
- **Offline-Schutz:** Lokale Daten werden erst nach Sync gelöscht

---

## 8. Integrationspotenzial mit dem Rechnungstool

In einer späteren Phase kann das Rechnungstool die Check-in-Daten nutzen:
- Automatisch Leistungen aus Check-ins ableiten
- Arbeitszeiten pro Objekt für Rechnungen vorschlagen
- Nachweis-PDF direkt an Rechnung anhängen

---

## 9. Entwicklungsphasen

### Phase 1 — MVP (Mobile App)
- [ ] Expo-Projekt aufsetzen (TypeScript)
- [ ] Supabase-Projekt anlegen, Tabellen erstellen, RLS konfigurieren
- [ ] Geräte-Setup Flow
- [ ] NFC-Scan + Check-in speichern
- [ ] GPS-Erfassung
- [ ] Bestätigungsscreen + eigener Verlauf

### Phase 2 — Web-Dashboard
- [ ] Next.js Dashboard aufsetzen
- [ ] Supabase Auth für Admins
- [ ] Check-in Übersicht mit Filter
- [ ] Objekte & Mitarbeiter verwalten
- [ ] CSV-Export

### Phase 3 — Verfeinerung
- [ ] Offline-Sync (Daten lokal zwischenspeichern)
- [ ] Push-Notifications (z.B. fehlende Check-ins)
- [ ] Integration Rechnungstool
- [ ] App Store Veröffentlichung (iOS + Android)

---

## 10. Geschätzte Aufwände

| Phase | Aufwand |
|---|---|
| Phase 1 MVP | ~3–5 Tage Entwicklung |
| Phase 2 Dashboard | ~2–3 Tage |
| Phase 3 + App Store | ~2–3 Tage + Review-Zeit |

**Kosten laufend (Supabase Free Tier):**
- Bis 500 MB DB, 50.000 API-Calls/Monat: kostenlos
- Danach: ~$25/Monat (Pro Plan)
