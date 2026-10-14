# Rechnungsablauf und Änderungsregeln

Diese Übersicht beschreibt den Quellstand ab dem Audit-Zweig vom 14.10.2026.
Sie ergänzt die Bedienhinweise; für fachliche Freigaben sind synthetische
Referenzrechnungen und eine Prüfung der erzeugten PDF-/XML-Werte erforderlich.

## Wege durch die Anwendung

| Schritt | Zuständiger Code | Maßgeblicher Nachweis |
|---|---|---|
| Eingabe und Vorschau | `sedo/views.py:invoice_create`, `sedo/forms.py` | `test_invoice_wizard_v2.py`, `test_rechnungslauf.py` |
| Atomare Erstanlage | `sedo/services/invoice_creation.py:save_new_invoice` | `test_invoice_archive.py`, `test_draft_workflow.py` |
| GWG-Monatslauf | `sedo/views.py:aareon_import`, `sedo/services/monthly_invoices.py:generate_gwg_monthly` | `test_monthly_invoices.py`, `test_review_v2.py` |
| Nummer, Betrag und Dokument | `sedo/services/numbering.py`, `calc.py`, `invoices.py:generate_invoice_files`, `sedo/core/rechnungslauf_core.py` | `test_atomic_files.py`, `test_rechnungslauf.py`, `test_invoice_archive.py` |
| Archiv und erneute Ausgabe | `sedo/services/invoice_archive.py`, `sedo/views.py:invoice_email` | `test_invoice_archive.py` |
| Versandentscheidung | `sedo/services/invoices.py:versand_lage`, `sedo/services/smtp_transport.py`, `sedo/services/mail.py` | `test_review_v2.py`, `test_mail_client.py` |

Eine neue Rechnung, ihre Positionen, Archivdateien und die Angebots- oder
Korrekturverknüpfung entstehen gemeinsam in einer Datenbank-/Dateitransaktion.
Schlägt die Dokumenterzeugung fehl, darf weder ein halber Beleg noch ein neues
Monatslaufobjekt übrig bleiben. Für jeden GWG-Eintrag ist der Monatslauf einzeln
atomar; andere Einträge können weiter verarbeitet werden, der Fehler wird
im Ergebnis angezeigt.

## Zustände und erlaubte Aktionen

`lifecycle` beschreibt die fachliche Existenz des Belegs. `status` zeigt den
Arbeits- oder Zahlungsstand und ist kein alleiniger Beweis für den Versand.

| Beleg | Regel im Code | Grenzfalltest |
|---|---|---|
| Entwurf (`draft`) | Bearbeiten oder verwerfen; kein Versand und keine Zahlung. `finalisiere_entwurf`, `ensure_versand_erlaubt`, `ensure_zahlung_erlaubt` | `test_draft_workflow.py`, `test_review_v2.py` |
| Ausgestellt (`issued`) | Archivierte Originalbytes verwenden. Nachträgliche Stammdatenänderungen erzeugen den Beleg nicht neu. `lese_archivdatei`, `pruefe_archiv` | `test_invoice_archive.py` |
| Korrigiertes Original (`corrected`) | Keine weitere Korrektur, kein Storno und keine Zahlung auf das Original; erneut versenden ist möglich. `ensure_correction_allowed`, `ensure_storno_allowed`, `ensure_zahlung_erlaubt` | `test_belegketten.py`, `test_review_v2.py` |
| Storniert (`cancelled`) oder Stornobeleg (Typ `457`) | Nicht erneut stornieren oder als Forderung bezahlen. `ensure_storno_allowed`, `ensure_zahlung_erlaubt` | `test_belegketten.py`, `test_workflow_locks.py` |
| E-Mail-Entwurf (`.eml`) | Eine erzeugte Datei belegt keinen Versand. Der Nutzer kann externen Versand ausdrücklich bestätigen. `versand_lage`, `invoice_confirm_external_send` | `test_invoice_archive.py`, `test_review_v2.py` |
| SMTP-Versuch | `abgelehnt` erlaubt erneute Entscheidung, `unklar` verlangt manuelle Prüfung, `angenommen` zählt als Versand auch bei anschließendem QUIT-/Ablagefehler. `classify_failure` | `test_review_v2.py`, `test_mail_client.py` |

Ein Beleg mit Zahlung, Mahnung, Folgebeleg oder ungeklärtem/belegtem Versand
darf nicht ausgeblendet werden (`ausblenden_hindernis`). Bei alten Daten kann
ein `status='versendet'` ohne unabhängigen Nachweis einen **ungeklärten**
Versand bedeuten (`versand_lage`). Die sichtbare Kennzeichnung ist deshalb
keine automatische Korrektur historischer Vorgänge.

## Änderungen an Steuer und Ausgabe

Die gegenwärtige ZUGFeRD-XML-Erzeugung verwendet die positive
Umsatzsteuerkategorie `S`; Eingabe und Kern lehnen 0 % und negative Sätze ab.
Eine neue Steuerart erfordert eine fachlich festgelegte Kategorie samt
Pflichtangaben, übereinstimmende PDF-/XML-Beträge und positive wie negative
Referenzfälle. Bei Änderungen an `build_xml` oder `create_pdf` zuerst
Beispielausgaben, Schemaprüfung und Archivregression festhalten; die Länge
dieser Funktionen allein rechtfertigt keinen risikoreichen Umbau.

Der lokale Prüfstand in `LOCAL_CHECKS.md` benötigt einen sauberen Git-Commit.
Er prüft beide Django-Reihen und auf dem jeweiligen Host ein natives Testpaket.
Ein bestandener Softwaretest ersetzt keine fachliche Freigabe oder Prüfung
auf einem anderen Betriebssystem.
