# Übergabe: Senior-Code-Review SEDO Rechnungstool v2

**Zweck dieser Datei:** Ein neuer Chat soll ohne den bisherigen Gesprächsverlauf weiterarbeiten
können. Alles Nötige steht hier oder in den unten genannten Dateien.

**Stand:** 4. September 2026. Das Review ist **abgeschlossen**, die Funde sind verifiziert und
dokumentiert. Offen ist nur noch das gemeinsame Abarbeiten mit Mirek.

---

## 1. Worum es geht

SEDO ist ein Django-Rechnungstool (Django 6.0.8, SQLite) für die Gebäudereinigungsfirma von
Mireks Vater. Es erzeugt E-Rechnungen im ZUGFeRD/Factur-X-Format. Hauptkunde ist die
GWG Reform, deren Rechnungen über das Aareon-ARS-System laufen. Ab 1. Januar 2027 gilt dort
der Echtbetrieb, bis dahin läuft eine Testphase.

**Code:** `~/Desktop/Co-Work OS/Projekte/SEDO Rechnungstool/sedo_rechnungstool_v2/`
**Review-Unterlagen:** `~/Desktop/Co-Work OS/Projekte/SEDO Rechnungstool/review_v2/`
**Projektgedächtnis:** `MEMORY_SEDO.md` im Projektordner. Vor Arbeitsbeginn lesen.

## 2. Verbindliche Arbeitsregeln

Diese Regeln gelten unabhängig vom Review und dürfen nicht stillschweigend gelockert werden.

- **Antworten auf Deutsch.**
- **Datenschutz:** In KI-Aufrufe gehen ausschließlich Leistungsdaten, niemals Kunden- oder
  Objektdaten. Kein Audio-Upload. Diese Regel ist Anlass mehrerer Funde (F-41, F-42, F-50, F-51).
- **Keine E-Mails ohne ausdrückliche Freigabe von Mirek.**
- **Keine Dateien löschen ohne Bestätigung.** Statt Löschen in den Scratchpad verschieben.
- **Die Datei `data/sedo_web.sqlite3` ist die Live-Datenbank mit 44 echten Kunden.** Nicht
  verändern, nicht migrieren, nicht in Tests einbeziehen.
- **GoBD:** Rechnungen, Zahlungen und Mahnungen sind aufbewahrungspflichtige Belege. Änderungen
  an Nummernkreis, Storno-Logik oder Löschwegen immer mit Blick darauf bewerten.
- Vor Änderungen den Plan zeigen und auf Freigabe warten. Mirek arbeitet die Funde bewusst
  gemeinsam durch, nicht im Autopilot.

## 3. Was geprüft wurde

Neun Prüfdimensionen, jeweils ein eigener Prüfagent: Datenmodell und Migrationen, Datenschutz
und KI, Frontend mit JavaScript und Templates, Funktionalität der Views, GoBD und
Geschäftslogik, Sicherheit Web, Sicherheit Secrets und Banking, Tests mit Konfiguration und
Deployment, ZUGFeRD mit XML und PDF.

Jeder Rohfund wurde anschließend von **zwei unabhängigen Gutachtern** geprüft, einem, der ihn
reproduzieren sollte, und einem, der ihn widerlegen sollte. Uneinige Fälle habe ich als
Hauptreviewer entschieden. Die Prüfung war ausschließlich lesend, es wurde nichts am Code
geändert.

| | Anzahl |
|---|---|
| Rohfunde aus den Prüfagenten | 143 bestätigt, 11 verworfen |
| Funde nach Zusammenführung gleicher Ursachen | **125** |
| davon hoch | 18 |
| davon mittel | 54 |
| davon niedrig | 53 |
| kritisch | 0 |

Schwerpunkte nach Datei:

- `sedo/views.py` – 38 Funde
- `sedo/backup_utils.py` – 7 Funde
- `sedo/core/rechnungslauf_core.py` – 6 Funde
- `sedo/templates/sedo/invoice_form.html` – 6 Funde
- `sedo/static/sedo/offer_ai.js` – 6 Funde
- `config/settings.py` – 5 Funde

## 4. Wo was liegt

| Datei | Inhalt |
|---|---|
| `review_v2/bericht_senior_review_v2.md` | **Der Bericht.** 125 Funde mit Beleg, Szenario, Auswirkung, Fix und Aufwand; dazu die empfohlene Reihenfolge, die verworfenen Funde und die Abdeckungsnotizen. 204 KB, abschnittsweise lesen. |
| `review_v2/UEBERGABE_CODE_REVIEW_V2.md` | Diese Datei. |
| `review_v2/verifiziert/_args_synthese.json` | Maschinenlesbare Fundliste mit allen Feldern und den Gutachterbegründungen. Grundlage des Berichts. |
| `review_v2/verifiziert/<dimension>.json` | Verifikationsergebnisse je Dimension inklusive beider Gutachtervoten. |
| `review_v2/verifiziert/_entscheidungen.json` | Die 16 strittigen Funde und meine Entscheidung dazu. |
| `review_v2/funde/<dimension>.json` | Rohfunde der Prüfagenten samt Abdeckungsnotizen. |
| `review_v2/schritt_workflow.js` | Das Workflow-Skript des Reviews. Wird für die Abarbeitung nicht gebraucht. |

Eine Sache fehlt: Der Kritiker-Durchgang zur Abdeckung (`kritik_abdeckung_v2.md`) wurde nie
erzeugt, weil das Guthaben aufgebraucht war. Die Abdeckungsnotizen der Prüfer stehen aber in
Abschnitt 5 des Berichts, das deckt den Zweck weitgehend ab.

## 5. Vorschlag für den Einstieg

Abschnitt 3 des Berichts enthält die vollständige Reihenfolge in vier Wellen. Der erste Schritt
wäre Welle 1: zehn Funde mit kleinem Aufwand, die Datenverlust und falsche Rechnungen
verhindern. In Kurzform:

1. **F-17** Mapping-Import bricht bei 0 gültigen Zeilen ab, statt den Bestand zu löschen.
2. **F-16** GWG-Monatslauf bricht bei leerem Preis ab, statt 10,00 € einzusetzen.
3. **F-12** Kundenimport schreibt keine Leerwerte über vorhandene Stammdaten.
4. **F-02** Django-Admin bekommt eine Löschsperre für Belege.
5. **F-03**, **F-31**, **F-81** Backup: Sessions löschen, `VACUUM`, Fehler melden.
6. **F-09** SMTP-Versand mit Zertifikatsprüfung.
7. **F-04** BT-25 an die schemakonforme Stelle, damit Storno-XML gültig wird.
8. **F-11**, **F-66** Formularverschachtelung auflösen, Batch-Mahnung ohne Auswahl abbrechen.
9. **F-53** Abmelden als POST, funktioniert derzeit gar nicht.
10. **F-24** `makemigrations` aus den Startskripten entfernen.

Sinnvolles Vorgehen je Fund: Fundblock im Bericht lesen, Stelle im Code prüfen, Fix mit Mirek
abstimmen, Regressionstest schreiben, dann ändern. Die Testsuite hat 245 Tests und läuft grün.
Sie muss nach jeder Änderung grün bleiben.

## 6. Was das Review nicht abgedeckt hat

- **Keine ZUGFeRD-Vollvalidierung gegen einen echten Validator.** Die XML-Funde beruhen auf
  Codeanalyse und XSD-Wissen. Der Mustang-Validator ist weiterhin ein offener Punkt und braucht
  eine Java-Installation.
- **Kein Lasttest, kein Penetrationstest, keine Prüfung der erzeugten PDF-Dateien mit einem
  PDF/A-Validator.**
- **Der Fund F-05 (PDF/A-3) ist der einzige mit Aufwand L in Welle 2** und braucht vermutlich
  eine andere PDF-Bibliothek. Das ist eher Projekt als Fix.
- Die Abdeckungsnotizen je Dimension stehen in Abschnitt 5 des Berichts und nennen konkret, was
  gelesen und was ausgelassen wurde.

## 7. Offene Punkte außerhalb des Reviews

- **GWG-Testphase:** Am 2. September wurden sechs Testrechnungen aus einer separaten
  Testinstanz an die Aareon-Testadresse geschickt. Eine Antwort von Markus Assmann steht aus,
  offen sind drei fehlende Abrechnungseinheiten und die SEDO-Verkäufer-ID. Details in
  `MEMORY_SEDO.md`.
- **Produktivsystem einrichten**, Windows-Starter auf dem Rechner von Mireks Vater verifizieren,
  Live-Test mit ihm. Alles in `MEMORY_SEDO.md` unter „Offene Aufgaben".

## 8. Alle Funde im Überblick

Sortiert nach Schwere, dann nach Datei. Aufwand: S bis etwa einen halben Tag, M bis etwa zwei
Tage, L größer. Details zu jedem Fund im Bericht unter der jeweiligen Nummer.

| Nr. | Schwere | Aufw. | Datei:Zeile | Titel |
|---|---|---|---|---|
| F-01 | hoch | M | `config/settings.py:192` | Kein Brute-Force-Schutz und keine Passwortregeln an /login/ und /admin/login/ trotz Tunnel-Exposition |
| F-02 | hoch | S | `sedo/admin.py:18-23` | Django-Admin erlaubt hartes Löschen/Bearbeiten von Rechnungen, Zahlungen, Mahnungen und Audit-Logs – und zeigt soft-gelöschte B… |
| F-03 | hoch | S | `sedo/backup_utils.py:40-41` | Backup-Datenbank enthält `django_session` mit gültigen Session-Keys – Login ohne Passwort aus einem Backup heraus |
| F-04 | hoch | S | `sedo/core/rechnungslauf_core.py:743-749` | `InvoiceReferencedDocument` (BT-25) an XSD-widriger Stelle – Storno-/Korrektur-XML schemaungültig |
| F-05 | hoch | L | `sedo/core/rechnungslauf_core.py:1120-1150` | ZUGFeRD-PDF ist kein PDF/A-3: keine XMP-Metadaten, Fonts nicht eingebettet, EmbeddedFile ohne /Subtype und /ModDate |
| F-06 | hoch | M | `sedo/forms.py:23-84` | Formulare ohne fachliche Validierung: negative Preise/Zahlungen, USt-Satz 0 %/<0/>100, Leistungszeitraum rückwärts erzeugen EN1… |
| F-07 | hoch | ? | `sedo/services/calc.py:45-60` | Ein Statusfeld für Zahlstatus UND Lebenszyklus: „korrigiert“ und „entwurf“ werden von OP-GET, Zahlung und E-Mail-Versand übersc… |
| F-08 | hoch | S | `sedo/services/invoices.py:81-87` | Storno- und Korrekturrechnungen zu Aareon/GWG-Rechnungen verlieren alle ARS-Felder (Empfänger-ID, Verkäufer-ID, ShipTo-ID, Leis… |
| F-09 | hoch | S | `sedo/services/mail.py:111-112` | `SMTP_SSL` ohne SSL-Kontext: keine Zertifikatsprüfung beim impliziten TLS-Versand (SMTP-Passwort MITM-anfällig) |
| F-10 | hoch | M | `sedo/templates/sedo/invoice_form.html:228-231` | Doppelte Rechnungen/Angebote durch Mehrfach-Submit; „Erstellen & versenden“ lässt die Formularseite stehen |
| F-11 | hoch | M | `sedo/templates/sedo/open_items.html:59` | Verschachtelte Formulare in „Offene Posten“: „bezahlt“-Button der ersten Zeile löst die Batch-Mahnung aus |
| F-12 | hoch | S | `sedo/views.py:645-661` | Kundenimport überschreibt vorhandene Kundendaten mit Leerwerten, wenn Spalten in der Datei fehlen |
| F-13 | hoch | L | `sedo/views.py:862` | Rechnungsentwürfe sind Sackgasse: echte Nummer, keine Finalisierung, aber versendbar (mit ENTWURF-Wasserzeichen), mahnbar, stor… |
| F-14 | hoch | S | `sedo/views.py:1068-1076` | Rechnungen jedes Status – auch Storno-/Korrekturbelege – können „ausgeblendet“ werden und fehlen danach im Steuerberater-Export… |
| F-15 | hoch | M | `sedo/views.py:1448-1456` | Kassenbuch: Bankimport ohne Dublettenschutz/Transaktion und Doppel-Submit bei Neubuchung erzeugen doppelte Buchungen ohne Korre… |
| F-16 | hoch | S | `sedo/views.py:1588-1594` | GWG-Monatslauf: leere oder unparsbare Preis-/Mengenfelder werden stillschweigend zu 10,00 € bzw. 1,00 – Massenrechnungen mit Ph… |
| F-17 | hoch | S | `sedo/views.py:1609-1612` | Mapping-Import löscht den gesamten Bestand auch dann, wenn 0 gültige Zeilen gelesen wurden |
| F-18 | hoch | M | `sedo/views.py:2103-2104` | Archivierte Rechnungsdateien werden bei Monatsabschluss/E-Mail/„Neu erzeugen“ mit aktuellen Stammdaten überschrieben |
| F-19 | mittel | M | `DEPLOYMENT_SECURITY_NOTES.md:54` | DEPLOYMENT_SECURITY_NOTES nennt Rollen-/Rechtetrennung als Voraussetzung für Bankanbindung – im Code gibt es keine Rechteprüfung |
| F-20 | mittel | S | `SEDO.app/Contents/MacOS/sedo:43` | SEDO.app-Launcher legt die venv mit /usr/bin/python3 (3.9) an – Django 6 benötigt Python ≥ 3.12 |
| F-21 | mittel | S | `SEDO.app/Contents/MacOS/sedo:66` | Einmaliges Admin-Passwort wird vom SEDO.app-Starter dauerhaft im Klartext in logs/setup.log geschrieben |
| F-22 | mittel | S | `SEDO.vbs:54` | SEDO.vbs startet ab dem zweiten Start ohne migrate/Paketinstallation und ohne Server-Log – Updates führen zu 500ern, Fehler sin… |
| F-23 | mittel | S | `START_HIER.command:30` | Django-Entwicklungsserver (runserver) auf 0.0.0.0:8000 als Produktiv-/Tunnel-Server in allen Startskripten |
| F-24 | mittel | S | `START_WINDOWS.bat:9` | Startskripte führen bei jedem Start `makemigrations sedo` auf Endnutzer-Rechnern aus |
| F-25 | mittel | S | `config/settings.py:1` | Keine LOGGING-Konfiguration: 'sedo'-Logger (FinTS/SMTP/KI-Fehler) verschwindet im Desktop-Bundle vollständig |
| F-26 | mittel | S | `config/settings.py:203` | Sitzungen leben 14 Tage ohne Idle-Timeout – bei defektem Abmelden lässt sich eine Sitzung aus der App heraus nicht beenden |
| F-27 | mittel | M | `desktop.py:183` | Desktop-Bundle: kein Weg, ein verlorenes Admin-Passwort zurückzusetzen – Hinweise verweisen auf nicht vorhandenes manage.py |
| F-28 | mittel | S | `requirements.txt:1` | requirements.txt komplett ungepinnt, Build-Skripte mit eigener Paketliste, .requirements.hash friert Umgebungen pro Rechner ein |
| F-29 | mittel | S | `sedo/audit.py:7` | Audit-Log übernimmt Client-IP aus X-Forwarded-For (erstes Element) – von jedem Client fälschbar |
| F-30 | mittel | M | `sedo/audit.py:38` | Audit-Log: Löschungen/Änderungen an Stammdaten und automatische Statuswechsel fehlen; Fehler beim Schreiben werden verschluckt;… |
| F-31 | mittel | S | `sedo/backup_utils.py:40` | „Bereinigte“ Backup-DB enthält Secrets weiterhin als Rest-Bytes (UPDATE ohne VACUUM/secure_delete) |
| F-32 | mittel | M | `sedo/backup_utils.py:69` | Kein Wiederherstellungsweg: weder Code, Kommando noch Dokumentation; Backup-Inhalt (DB) wird von keinem Test geprüft |
| F-33 | mittel | M | `sedo/core/rechnungslauf_core.py:664` | USt-Satz 0 % wird als Kategorie S mit RateApplicablePercent 0.00 ausgegeben (BR-S-05); keine Unterstützung für E/Z/AE (§19, §13b) |
| F-34 | mittel | S | `sedo/core/rechnungslauf_core.py:753` | IBAN (BT-84) mit Leerzeichen in der XML |
| F-35 | mittel | S | `sedo/core/rechnungslauf_core.py:1084` | GiroCode verstößt gegen EPC-QR-Spezifikation (strukturierte UND unstrukturierte Verwendungszweckzeile belegt; negative Beträge … |
| F-36 | mittel | S | `sedo/forms.py:29` | Objekt (Leistungsort) wird nicht gegen den Kunden validiert – fremdes Objekt auf Rechnung/Angebot/Abo möglich |
| F-37 | mittel | S | `sedo/middleware.py:39` | Wochenbackup läuft nur einmal pro Prozessstart – bei dauerhaft laufendem Server nie wieder |
| F-38 | mittel | S | `sedo/middleware.py:41` | Test-Suite schreibt bei jedem Lauf ~20 Backup-ZIPs in den Live-Ordner backups/ |
| F-39 | mittel | M | `sedo/models.py:124` | CustomerObject wird hart gelöscht (SET_NULL auf Rechnungen) – historische Rechnungen verlieren Leistungsort, Monatslauf-Idempot… |
| F-40 | mittel | S | `sedo/models.py:149` | Idempotenz-Constraint deckt nur 'GWG Monatsrechnung' ab; 'Abo Monatsrechnung' und Objekte ohne Zuordnung bleiben ungeschützt (s… |
| F-41 | mittel | M | `sedo/services/ai.py:412` | Selbstlernender Katalog persistiert KI-Ausgaben ohne Bestätigung und injiziert sie in jeden künftigen System-Prompt (PII-Verstä… |
| F-42 | mittel | M | `sedo/services/ai.py:433` | Freitext (Diktat, Leistungstexte, Positionsbezeichnungen, Nutzerhinweis) geht ohne jede Redaktion an Anthropic – Datenschutzreg… |
| F-43 | mittel | S | `sedo/services/banking.py:35` | FinTS-Abruf schlägt ohne DK-Produktkennung immer fehl (python-fints ≥4 verlangt product_id), UI nennt sie „optional“ |
| F-44 | mittel | S | `sedo/services/banking.py:107` | pushTAN-Warteschleife schluckt 90 s lang jede Exception (30 Bank-Anfragen) und blockiert den Request-Thread – echte Fehler ersc… |
| F-45 | mittel | M | `sedo/services/banking.py:150` | TAN-Verfahren wird nicht ausgewählt – Bibliothek nimmt das erste der Bank, pushTAN ggf. nie erreicht |
| F-46 | mittel | M | `sedo/services/banking.py:150` | Kein FinTS-Client-State (deconstruct/from_data) – jeder Abruf ist eine neue Geräteregistrierung mit TAN-Pflicht |
| F-47 | mittel | S | `sedo/services/invoices.py:231` | Stornorechnung übernimmt Leistungszeitraum nicht – XML ohne BillingSpecifiedPeriod, Lieferdatum = Stornodatum |
| F-48 | mittel | S | `sedo/services/payments.py:21` | Unparsebare Buchungsdaten fallen stumm auf „heute“ zurück – falsches Zahlungsdatum und ausgehebelte Dublettenerkennung |
| F-49 | mittel | M | `sedo/services/payments.py:41` | Sammelüberweisung mit mehreren Rechnungsnummern wird komplett der neuesten Rechnung zugeordnet – übrige Rechnungen bleiben offe… |
| F-50 | mittel | S | `sedo/static/sedo/offer_ai.js:237` | Transkript wird sofort automatisch an Claude gesendet – kein Prüfschritt trotz Label „prüfen und bei Bedarf korrigieren“ |
| F-51 | mittel | S | `sedo/static/sedo/offer_ai.js:243` | Web-Speech-Fallback schickt Diktat-Audio an Google – UI behauptet „Audio verlässt den Rechner nicht“ |
| F-52 | mittel | S | `sedo/static/sedo/offer_ai.js:334` | Doppelklick auf „Diktat starten“ während der Mikrofon-Freigabe startet zwei MediaRecorder – erster Stream wird nie gestoppt, Mi… |
| F-53 | mittel | S | `sedo/templates/sedo/base.html:75` | „Abmelden“ ist ein GET-Link – Django 6 LogoutView akzeptiert nur POST → 405 |
| F-54 | mittel | S | `sedo/templates/sedo/customer_detail.html:8` | Primärschlüssel ohne /unlocalize in Query-Strings: ab ID 1000 wird „?customer=1.234“ erzeugt → HTTP 500 |
| F-55 | mittel | S | `sedo/templates/sedo/invoice_form.html:3` | Wizard versteckt Schritte per display:none – Pflichtfelder blockieren das Absenden ohne jede Meldung |
| F-56 | mittel | S | `sedo/templates/sedo/invoice_form.html:283` | Race bei schneller Kundenauswahl: verspätete API-Antwort überschreibt Kopfdaten und Objektliste des zuletzt gewählten Kunden |
| F-57 | mittel | S | `sedo/templates/sedo/invoice_form.html:424` | Live-Summe im Rechnungs-/Angebotsassistenten rundet per Float anders als der gespeicherte Beleg (Cent-Abweichungen im Prüfen-Sc… |
| F-58 | mittel | S | `sedo/templates/sedo/settings.html:28` | SMTP-Port als int in `<input type=number value="{{ smtp_port }}">` – ab Port 1000 lokalisiert („2.525“) |
| F-59 | mittel | M | `sedo/views.py:210` | Änderungen an den Empfänger-Kopffeldern werden ignoriert, sobald ein gespeicherter Kunde ausgewählt ist |
| F-60 | mittel | S | `sedo/views.py:600` | Stornorechnungen (Typ 457) zählen im Dashboard als offene Posten mit negativem Betrag |
| F-61 | mittel | S | `sedo/views.py:629` | Datei-Uploads ohne Größen-/Typprüfung werden komplett in den Speicher gelesen bzw. an pandas/openpyxl übergeben |
| F-62 | mittel | S | `sedo/views.py:633` | Kundenimport: Windows-CSV (cp1252), doppelte Namen/Kundennummern oder Nicht-Excel-Datei führen zu 500 mitten im Import (Teilimp… |
| F-63 | mittel | S | `sedo/views.py:860` | Zahlungsziel 0 Tage wird stillschweigend zu 14 Tagen (falsy-Fallback mit `or`) |
| F-64 | mittel | M | `sedo/views.py:876` | Korrektur einer teilbezahlten Rechnung: bereits geleistete Zahlungen bleiben am Original, Korrekturrechnung gilt voll als offen |
| F-65 | mittel | S | `sedo/views.py:1111` | Angebot-Anlage: nicht atomar (anders als Rechnung) und ?customer=-Vorbelegung wird ignoriert |
| F-66 | mittel | S | `sedo/views.py:1234` | Batch-Mahnung ohne Auswahl mahnt ALLE Kandidaten mit der erzwungenen Stufe (Mahnung ohne vorherige Erinnerung) |
| F-67 | mittel | M | `sedo/views.py:1245` | Zahlungserfassung ohne Betrags- und Statusprüfung: negative/0-Beträge, Zahlungen auf stornierte oder bereits bezahlte Rechnunge… |
| F-68 | mittel | S | `sedo/views.py:1389` | Zahlungserinnerung/Mahnung ohne Fälligkeits- oder Offenbetragsprüfung möglich |
| F-69 | mittel | M | `sedo/views.py:1400` | Kassenbuch ohne Kassenbestand/Übertrag und ohne Chronologieschutz – Saldo zeigt nur Monatsbewegung, Rückdatierungen erhalten la… |
| F-70 | mittel | S | `sedo/views.py:1574` | _month_bounds wirft bei jedem Monatswert außerhalb „YYYY-MM“ eine unbehandelte Exception (500) |
| F-71 | mittel | S | `sedo/views.py:1947` | Abo-Monatslauf hat keinen DB-seitigen Dublettenschutz – Doppelklick kann doppelte Rechnungen erzeugen |
| F-72 | mittel | S | `sedo/views.py:2097` | Ungeprüfte Monats-/Datumsparameter fließen in Dateisystempfade und int()-Parsing – Verzeichnisanlage außerhalb von media/ und 5… |
| F-73 | niedrig | S | `README.md:1` | Versions- und Dokumentationsdrift, verwaiste Dateien im Projektordner |
| F-74 | niedrig | S | `SEDO.vbs:59` | SEDO.vbs: 300-Sekunden-Timeout ist bei der Erstinstallation zu kurz → Fehlerdialog trotz laufender Einrichtung |
| F-75 | niedrig | S | `config/settings.py:56` | Platzhalter-SECRET_KEY aus .env.example wird ohne Prüfung übernommen |
| F-76 | niedrig | S | `config/settings.py:92` | CSRF_TRUSTED_ORIGINS enthält fest kodierte Wildcards für fremde Quick-Tunnel-Domains |
| F-77 | niedrig | S | `config/urls.py:9` | Kein Passwortwechsel in der Anwendung – Zugangsdaten lassen sich nach Verdacht auf Kompromittierung nur per Django-Admin oder C… |
| F-78 | niedrig | S | `sedo/admin.py:41` | Django-Admin zeigt alle AppSettings inkl. Bank-PIN, SMTP-Passwort und Claude-Key im Klartext |
| F-79 | niedrig | S | `sedo/backup_utils.py:32` | Temp-Kopie der kompletten Datenbank und Backup-ZIPs mit 0644; Temp-Datei bleibt bei Fehlern liegen |
| F-80 | niedrig | S | `sedo/backup_utils.py:36` | Backup kopiert die laufende SQLite-Datei per shutil.copy2 statt über die SQLite-Backup-API |
| F-81 | niedrig | S | `sedo/backup_utils.py:43` | Fehlschlag der Secret-Bereinigung wird stumm ignoriert – Backup enthält dann alle Secrets, UI behauptet das Gegenteil |
| F-82 | niedrig | S | `sedo/backup_utils.py:64` | Backup-Ordner ohne Rotation – jedes ZIP enthält den kompletten Medienordner, 259 Dateien liegen bereits vor |
| F-83 | niedrig | S | `sedo/core/rechnungslauf_core.py:697` | Leere XML-Elemente bei fehlenden Adress-/ID-Daten (PostcodeCode, ShipTo/ID) |
| F-84 | niedrig | M | `sedo/migrations/0009_gwg_monthly_unique.py:13` | Migration 0009 (und 0012) legt Unique-Constraints ohne Datenbereinigung an – Upgrade mit Altbestand bricht ab, App startet nicht |
| F-85 | niedrig | M | `sedo/migrations/0012_concurrency_constraints.py:20` | Migration 0012 löscht Mahndokumente hart und wählt das älteste – versandte Mahnschreiben verschwinden ohne Protokoll |
| F-86 | niedrig | M | `sedo/models.py:425` | Bank-PIN, SMTP-Passwort und Claude-Key liegen unverschlüsselt in sedo_appsetting; DB-Datei ist 0644 – Schlüsseldatei dagegen 06… |
| F-87 | niedrig | S | `sedo/services/ai.py:156` | KI-erfundene Artikelnummern landen ungeprüft als SellerAssignedID in der ZUGFeRD-XML |
| F-88 | niedrig | S | `sedo/services/ai.py:439` | Erzwungener Tool-Use (tool_choice type=tool) mit frei konfigurierbarem Modellnamen – mit Claude Fable 5.1/Mythos 5.1 schlagen a… |
| F-89 | niedrig | S | `sedo/services/calc.py:50` | Stornorechnungen (negativer Brutto) werden beim Öffnen der Offenen Posten automatisch auf „bezahlt“ gesetzt – ohne Zahlung |
| F-90 | niedrig | S | `sedo/services/dunning.py:84` | Mahndokument nicht atomar: DB-Zeile vor PDF – ein PDF-Fehler verbraucht die Mahnstufe ohne Dokument und ohne Wiederholungsmögli… |
| F-91 | niedrig | S | `sedo/services/numbering.py:66` | Nummernkreis-Monat folgt dem Tagesdatum, Rechnungsdatum ist frei rück-/vordatierbar – Nummer und Datum können auseinanderlaufen |
| F-92 | niedrig | S | `sedo/services/numbering.py:141` | Kassenbuch-Buchungsnummer per Check-then-act ohne Transaktion – IntegrityError (HTTP 500) bei gleichzeitiger Buchung; is_locked… |
| F-93 | niedrig | S | `sedo/static/sedo/customer_search.js:88` | Enter im Kundensuchfeld ohne Treffer springt im Assistenten zum nächsten Schritt statt die Suche zu behandeln |
| F-94 | niedrig | S | `sedo/static/sedo/offer_ai.js:308` | Web-Speech-Fallback: Netzwerkfehler führt zu Endlos-Neustart mit Toast pro Versuch |
| F-95 | niedrig | S | `sedo/static/sedo/offer_ai.js:447` | KI-Befüllung reaktiviert Positionszeilen, die per „Löschen“-Haken markiert sind – Position verschwindet stillschweigend beim Sp… |
| F-96 | niedrig | M | `sedo/static/sedo/offer_ai.js:518` | KI-Preise, Mengen und Artikelnummern fließen ohne Sperre in Formularfelder; „genannte“ Preise ohne Prüfmarkierung, Aufteilung n… |
| F-97 | niedrig | S | `sedo/static/sedo/plausibility.js:141` | Plausibilitäts-Check feuert beim Laden vorbefüllter Formulare, weil das Skript vor dem Wizard-Init läuft |
| F-98 | niedrig | S | `sedo/templates/registration/login.html:1` | Login-Seite ohne Viewport-Meta und ohne Cache-Busting – auf Handy via Tunnel unlesbar klein |
| F-99 | niedrig | S | `sedo/templates/sedo/backup.html:52` | Backup verspricht „keine Bank-Zugangsdaten“ – Legitimations-ID, BLZ, FinTS-Endpunkt, IBAN und SMTP-Benutzer bleiben aber vollst… |
| F-100 | niedrig | S | `sedo/templates/sedo/base.html:140` | Toast fügt Meldungstext per innerHTML ein – Django-Messages mit Nutzereingaben werden als HTML interpretiert |
| F-101 | niedrig | S | `sedo/templates/sedo/base.html:162` | Toasts sind die einzige Anzeige für Django-Fehlermeldungen und verschwinden nach 5–8 s – lange Fehlertexte (SMTP, FinTS, Import… |
| F-102 | niedrig | S | `sedo/templates/sedo/invoice_detail.html:16` | Irreversible GoBD-relevante Aktionen (Mahnung, Erinnerung, Batch „Nächste Stufe für alle“) per Ein-Klick-Button ohne Bestätigun… |
| F-103 | niedrig | S | `sedo/templates/sedo/invoice_detail.html:97` | Cache-Busting manuell und inkonsistent (gleiche Datei mit zwei Versions-Strings, login.css ohne Version) |
| F-104 | niedrig | M | `sedo/templates/sedo/invoice_form.html:57` | Barrierefreiheit: Labels ohne Zuordnung, Suchfeld und Checkboxen ohne Beschriftung |
| F-105 | niedrig | M | `sedo/templates/sedo/invoice_form.html:374` | Vorbefüllte Formulare (?customer=, Angebot, Korrektur, Fehler-Rerender) haben keinen JS-Zustand: Kunden-Pill fehlt, Objektliste… |
| F-106 | niedrig | S | `sedo/templates/sedo/offer_detail.html:4` | Angebotsdetail: Spalte „Gesamt“ zeigt keinen Zeilenbetrag, sondern „Menge × Einzelpreis“ |
| F-107 | niedrig | S | `sedo/templates/sedo/settings.html:60` | Private E-Mail-Adresse des Entwicklers als Platzhalter im Einstellungs-Formular ausgeliefert |
| F-108 | niedrig | M | `sedo/tests/test_security_hardening.py:81` | Testlücken: Backup-Zyklus, Middleware, Desktop-Starter, pushTAN-Timeout und Whisper-Fallback ungetestet |
| F-109 | niedrig | S | `sedo/views.py:274` | Vorschau rundet anders (ROUND_HALF_EVEN) als die gespeicherte Rechnung (ROUND_HALF_UP) |
| F-110 | niedrig | S | `sedo/views.py:464` | KI-Begleittext: Rechnungsnummer, Betrag und Fälligkeit werden nicht gegen die Rechnung rückgeprüft – bei fehlendem Fälligkeitsd… |
| F-111 | niedrig | M | `sedo/views.py:471` | Positionsbezeichnungen und Rechnungsnummer werden als „Leistungsdaten“ übertragen, sind aber Freitext mit typischem Objekt-/Lei… |
| F-112 | niedrig | S | `sedo/views.py:754` | Listenansichten ohne Limit/Pagination (Rechnungen, Angebote, Kunden, Leistungskatalog, Monatsabschluss) |
| F-113 | niedrig | S | `sedo/views.py:798` | Nicht-numerische ID-/Monatsparameter führen zu ValueError/TypeError (500) statt 404/Meldung |
| F-114 | niedrig | S | `sedo/views.py:852` | Kunde und Objekt werden vor der Transaktion angelegt – bei Rollback bleiben verwaiste Stammdaten, Doppel-Submit erzeugt doppelt… |
| F-115 | niedrig | S | `sedo/views.py:939` | „Erstellen & versenden“ versendet nie per SMTP, setzt aber Status 'versendet' vor der .eml-Erzeugung – UI verspricht Direktversand |
| F-116 | niedrig | S | `sedo/views.py:1164` | Rechnung aus abgelehntem/abgelaufenem Angebot möglich; Status eines bereits verrechneten Angebots kann auf „abgelehnt“/„offen“ … |
| F-117 | niedrig | M | `sedo/views.py:1204` | N+1 und Schreibzugriffe auf GET in Offene Posten/Dashboard/Zahlungen: prefetch_related('payments') wird durch .filter() umgangen |
| F-118 | niedrig | S | `sedo/views.py:1259` | Offener Redirect über POST-Parameter „next“ bei Zahlungserfassung |
| F-119 | niedrig | S | `sedo/views.py:1479` | Kassenbuch-PDF: zustandsändernder GET-Endpunkt schreibt pro beliebigem month-Wert eine Datei nach media/ und liefert bei '/' im… |
| F-120 | niedrig | S | `sedo/views.py:1524` | Irreführende Rückmeldungen/Vorbelegungen: ZIP-Versand zählt fehlende PDFs mit, Importe ohne Datei bleiben stumm, Zahlungsvorsch… |
| F-121 | niedrig | S | `sedo/views.py:2006` | Gespeichertes SMTP-Passwort kann über die Oberfläche nicht gelöscht werden – kein Pendant zu ai_api_key_clear / fints_pin_clear |
| F-122 | niedrig | S | `sedo/views.py:2014` | PIN/Passwort-Formulare ohne sensitive_post_parameters; kompletter Banking-Config inkl. PIN im Template-Kontext |
| F-123 | niedrig | S | `sedo/views.py:2016` | FinTS-Endpunkt wird ohne https-Pflicht gespeichert – PIN ginge bei http-URL im Klartext |
| F-124 | niedrig | S | `sedo/views.py:2025` | Gespeicherte Bank-PIN wird ungetrimmt gespeichert und nach einem PIN-Fehler nie invalidiert – drei Abrufe sperren das Online-Ba… |
| F-125 | niedrig | S | `sedo/views.py:2241` | Sicherheitsstatus-Seite meldet „Bankanbindung vorbereitet“ trotz fehlgeschlagenem Passwort-Check und enthält einen hart auf ok=… |
