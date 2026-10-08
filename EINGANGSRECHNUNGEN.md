# Eingangsrechnungen und ruhige Bedienoberfläche

## Ablauf

1. Der reguläre IMAP-Abruf prüft jeden Anhang einzeln. Nur erkannte Rechnungen
   kommen in **Eingangsrechnungen → Zu prüfen**. Text-PDFs benötigen einen
   Rechnungsbezug im Dokument und einen Betrag; XML eine unterstützte Rechnungsart,
   Nummer und Summe. Angebote, Lieferscheine, Auftragsbestätigungen, Mahnungen,
   Gutschriften, Proforma-Rechnungen und Terminmitteilungen werden nicht als
   Eingangsrechnungen übernommen. Unklare Dokumente bleiben im Postfach.
   Textlose Scans/Bilder benötigen ausdrückliche Rechnungskennzeichen sowohl
   im Dateinamen als auch im Betreff. Ein Mailbetreff allein genügt nie.
   Bereits geladene ältere
   Nachrichten folgen in Gruppen von 25 pro Abruf. Gesendet, Spam und Papierkorb
   werden nicht automatisch ausgewertet. Keine externen Links oder ZIPs öffnen.
2. Originalanhang und vollständige Quell-E-Mail werden in der lokalen Datenbank
   gesichert. Exakt gleiche Anhänge werden postfachübergreifend zusammengeführt;
   alle Herkunftsnachrichten bleiben verknüpft. Die Originale sind im regulären
   Backup enthalten und bleiben nach einer Löschung auf dem Mailserver erhalten.
3. Den Originalbeleg öffnen, Lieferant, Nummer, Belegdatum, Brutto und Währung
   prüfen. **Zurück** führt aus der Dateiansicht und der Zuordnungsmaske
   zur vorherigen Ansicht; Suchfilter und Listenseite bleiben erhalten.
   **Angaben mit Bonsai vorschlagen** liest Text-PDFs und XML ausschließlich
   über den vorhandenen lokalen Transport. Scans/Bilder benötigen derzeit manuelle
   Eingaben. Vorschläge bleiben ungespeichert, bis der Benutzer sie übernimmt.
4. Erst **Prüfen & freigeben** mit Pflichtbestätigung erlaubt Sammelaktionen.
   PDF-Belege können als gemeinsames Druck-PDF heruntergeladen werden. Andere
   Formate bleiben einzeln als Original verfügbar. Der Benutzer bestätigt den
   tatsächlichen Ausdruck separat. Aussortieren ist reversibel, Originale werden
   dabei nicht gelöscht. Eine Bearbeitung ohne erneute Freigabe setzt den Beleg
   wieder auf „Zu prüfen“.
5. Freigegebene Belege eines Belegmonats auswählen und Steuerbüro-Paket erstellen.
   Es enthält Originalbelege, vollständige Quell-E-Mails, CSV-Übersicht und Manifest
   mit Prüfsummen. Das Paket ist ein unveränderlicher Schnappschuss der geprüften
   Angaben, kein DATEV-Buchungsstapel. Spätere Belegänderungen ändern es nicht.
6. Absender und Steuerbüro-Adresse prüfen und einen Mailentwurf erstellen. Versand
   erfolgt über den bestehenden Review-/SMTP-Ablauf erst nach ausdrücklichem Klick.
   Wiederholte Entwurfsanforderungen öffnen denselben Entwurf; andere Nutzer können
   ihn nicht bearbeiten. Die Übersicht zeigt den echten Versandstatus des Entwurfs.
   Unklarer SMTP-Ausgang führt weiterhin zu keiner automatischen Wiederholung.

Das Zahnrad im Belegeingang enthält Automatik und optionale Steuerbüro-Adresse.
Keine Kontierung, Zahlung oder Überweisungsvorlage wird erzeugt.

## Grenzen und Integrität

- 25 MB pro Dokument, 100 Belege und 100 MB Originaldaten pro Auswahl,
  maximal 500 PDF-Seiten in der Drucksammlung. Für den Mailversand gilt das
  bestehende 25-MB-Anhangslimit; größere Pakete kleiner zusammenstellen.
- Erkennung erzeugt bewusst Prüfkandidaten, keine automatischen Buchungen.
  Die lokale, regelbasierte Erkennung nutzt die ersten drei PDF-Seiten; sie
  ersetzt keine inhaltliche Prüfung oder OCR. Irrtümer sind über „Aussortieren“
  reversibel korrigierbar. Verschieden gescannte Fassungen
  werden nicht per Hash zusammengeführt; gleiche Lieferanten-/Rechnungsnummern
  werden bei der Belegprüfung als mögliche Dublette angezeigt.
- Prüfsummen vor Originaldownload, Drucken und Export; UTF-8 CSV schützt
  Benutzereingaben vor Tabellenformeln. Originalnamen sind bereinigt, ZIP-Pfade
  werden ausschließlich durch die Anwendung erzeugt.
- XML-Auswertung ohne DTD, Netzwerkzugriff oder Entity-Auflösung. Keine
  Bankdaten an den Assistenten anfordern. Dokumentanweisungen sind untrusted Text.
- Review-Revisionen verhindern Überschreiben durch veraltete Formulare.
  Paket- und Mailentwurferstellung erfolgen transaktional, mit Audit-Eintrag
  für Erfassung, Freigabe, Druckbestätigung und Export.
- Prüfsummen und Backups sind technische Schutzmaßnahmen, keine Zusage einer
  rechtlich zertifizierten Archivierung. Externe Sicherung bleibt erforderlich.

## Bedienmuster

Gelegentliche Einrichtung: Zahnrad bei Postfächern/Bankabruf/Belegeingang,
aufklappbare Einstellungsabschnitte für SMTP, Bank, Bonsai, Wetter, GWG-Mapping
und Backup-Automatik. Vorhandene Direktlinks auf Abschnitte öffnen deren Maske.

Erklärungen stehen in dieser Dokumentation und in `BEDIENHINWEISE.md`. Die
Anwendung zeigt keine Info-Icons oder dauerhaften Hilfetexte. Fehler, Status
und notwendige Bestätigungen bleiben sichtbar.

## Bestehende Fehlimporte prüfen

`python manage.py recheck_incoming_documents` erzeugt nur eine Vorschau.
Nach Sicherung sortiert `python manage.py recheck_incoming_documents --apply
--user ADMINNAME` eindeutig erkannte andere Dokumenttypen reversibel aus.
Berücksichtigt werden ausschließlich unbearbeitete Belege in „Zu prüfen“ mit
Revision 0, ohne erfasste Angaben, Freigabe oder Druckbestätigung. Unklare
Dokumente, bearbeitete und freigegebene Belege bleiben erhalten. Originalbytes,
Prüfsummen und Quell-E-Mails werden nicht verändert. Änderungen werden protokolliert;
„Aussortiert → Beleg prüfen → Wieder aufnehmen“ macht sie rückgängig. Das normale
Archiv blendet aussortierte Belege aus, der Reiter „Aussortiert“ bleibt verfügbar.

Die XML-Erkennung unterstützt UBL Invoice und CII mit expliziten Rechnungsarten
nach der [Peppol-Rechnungsartenliste](https://docs.peppol.eu/poacc/billing/3.0/codelist/UNCL1001-inv/).
Dies ist eine Typ-Erkennung, keine vollständige ZUGFeRD-/XRechnung-Validierung.

## Prüfung

Gezielte Fälle: `sedo.tests.test_incoming_detection`, `sedo.tests.test_incoming_invoices`
und bestehende Mailtests; neun Gegenproben in `tools/gegenproben_incoming.py`.
Vollständige Freigabe weiterhin mit `tools/local_checks.py` (siehe
`LOCAL_CHECKS.md`). Native Mac-Validierung ersetzt keine Windows-/Linux-Abnahme.
