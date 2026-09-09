# Antwortentwurf an Markus Assmann (GWG Reform)

**Stand:** 09.09.2026 · **Status: ENTWURF – nicht versendet**
Bezug: Seine Mail vom 09.09.2026 zu den sechs Testrechnungen vom 02.09.2026.

---

## Entwurf

Betreff: Re: Testrechnungen – Rechnungstext angepasst, drei Rückfragen

Guten Morgen Herr Assmann,

danke für die schnelle Rückmeldung – und schön zu hören, dass die Testrechnungen
sauber eingelesen und verteilt wurden.

**Zum Rechnungstext:** Ist erledigt. Im Feld steht künftig die Belegart, das Objekt
und der Zeitraum. Für Ihr Beispiel also:

> Rechnung Resedaweg 11 li. · September 2026

Bei einer Stornorechnung entsprechend „Stornorechnung Resedaweg 11 li. · September 2026",
damit im Klartext erkennbar bleibt, um welche Belegart es sich handelt.

Falls Sie das Objekt lieber ohne die vorangestellte Belegart hätten oder eine
andere Schreibweise brauchen, sagen Sie einfach Bescheid – das ist schnell geändert.

**Im Anhang drei neue Testrechnungen**, je eine aus einem Bestand, damit Sie das
gleich im System sehen können:

| Rechnung | Objekt | Wirtschaftseinheit | Bestand |
|---|---|---|---|
| RE2026090007 | Resedaweg 11 li. | 3801 | Neitzel |
| RE2026090008 | Heckenweg 1/1 | 2101 | Radtke |
| RE2026090009 | Kirschweg 1 | 2501 | Lammel |

Der Zeitraum ist diesmal Oktober 2026 – für September lagen ja schon Rechnungen
vor, und unser System verhindert bewusst, dass für denselben Zeitraum und dasselbe
Objekt zweimal abgerechnet wird.

**Was uns noch fehlt** – dabei würden wir uns über Ihre Zuarbeit freuen:

1. **Drei Abrechnungseinheiten fehlen in der Mappingdatei.** Unsere Datei hat den
   Stand 06.05.2026. Darin nicht enthalten sind die Wirtschaftseinheiten
   **3601 (Bestand Neitzel)**, **2001 (Bestand Radtke)** und **2517 (Bestand Lammel)**.
   Pro Bestandsbetreuer konnten wir deshalb nur einen Teil testen. Können Sie uns
   eine aktuelle Mappingdatei schicken?

2. **Die Verkäufer-ID von uns.** Wir haben in den Testrechnungen bewusst keine
   gesetzt, um zu sehen, ob das System sie zwingend braucht. Falls eine benötigt
   wird: Welche sollen wir eintragen, und in welchem Format?

3. **Die Empfänger-ID 399662000.** Die haben wir für den Test verwendet. Gilt die
   auch im Echtbetrieb, oder bekommen wir dafür eine andere?

Dass die Zuordnung des Sachbearbeiters im REB auf Ihrer Seite liegt, haben wir
notiert – da müssen wir nichts weiter liefern.

Wir würden nach Ihrer Rückmeldung gern einen zweiten Testlauf machen, dann mit
allen neun Wirtschaftseinheiten und dem angepassten Rechnungstext.

Viele Grüße nach Reform
Mirek Dobberahn

---

## Notizen für Dich (nicht mitschicken)

**Die drei Testrechnungen**
- Liegen als PDF in `testrechnungen_2026-09-09/` (RE2026090007–09), erzeugt in der
  Testinstanz `sedo_testinstanz_gwg/` – die Live-Buchhaltung ist unberührt, der
  Live-Nummernkreis ebenso.
- Geprüft: XML wohlgeformt, `factur-x.xml` im PDF eingebettet (`/AF` gesetzt),
  ShipTo-ID = Abrechnungseinheit aus Spalte A der Mappingdatei, Zeitraum
  01.–31.10.2026, brutto je 265,37 €.
- **Achtung, unverändert offen:** Die Empfänger-ID in der XML ist `1` (die
  Kundennummer), nicht `399662000`. Das war bei den sechs Septemberrechnungen
  genauso – und die wurden erfolgreich eingelesen. Die `399662000` steckt nur in
  der Mailadresse. Das Feld „Aareon/ARP Rechnungsempfänger-ID" beim Kunden GWG ist
  leer; sobald Markus sagt, was dort hingehört, tragen wir es ein. Genau das ist
  Frage 3 oben.

**Was technisch schon erledigt ist**
- Rechnungstext: umgesetzt und getestet, Commit `12a2c3e`. Der Zeitraum war
  vorher schon im Feld – er fiel nur nicht auf, weil das Objekt fehlte.
- Der Objektname kommt aus dem Kundenobjekt (`CustomerObject.name`). Bei den
  Testrechnungen stand dort „Resedaweg 11 li.", „Quittenweg 14" usw. – genau die
  Schreibweise, die Markus im Beispiel nennt.

**Was der Testlauf nebenbei geklärt hat**
- Die PDFs sind **kein** PDF/A-3 (keine XMP-Metadaten, Schriften nicht eingebettet)
  und wurden trotzdem verarbeitet. Damit ist der Review-Fund F-05 kein Blocker für
  den 01.01.2027. Falls Du magst, kannst Du Markus fragen, ob Aareon perspektivisch
  PDF/A-3 erwartet – nötig scheint es nicht zu sein.

**Noch offen, aber nicht Teil dieser Mail**
- BT-154 (Leistungsbeschreibung je Position) setzt der Monatslauf bisher nicht.
  Markus hat es nicht angesprochen; falls die Sachbearbeiter mehr Detail zur
  Leistung brauchen, wäre das der nächste Hebel.
