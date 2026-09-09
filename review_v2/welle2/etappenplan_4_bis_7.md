# Welle 2 – Etappenplan 4 bis 7 (offen)

Auszug aus der Synthese vom 08.09.2026. Etappen 1-3 sind umgesetzt und committet.
Grundlage: drei unabhaengige Loesungsentwuerfe, bewertet von drei Jurys.

**Empfohlener Weg:** Entwurf 1 bleibt die Basis – migrationsfrei, in sieben einzeln committebaren Etappen, Rückweg per git revert –, aber inhaltlich aufgewertet: der Entwurfs-Riegel wandert aus dem Mailversand an die Quelle (kein factur-x.xml für Entwürfe, aus Entwurf 2 und 3), der Entwurf bekommt mit invoice_edit und invoice_finalize erstmals einen echten Rückweg (Entwurf 2), und die Bestandsreparatur rekonstruiert den Belegzustand aus den tatsächlichen Folgebelegen (Regel aus Entwurf 3, Ausführungsort aus Entwurf 1). Nicht übernommen werden alle drei Vorschläge, die Daten auf der Live-DB schreiben oder die Tabelle neu aufbauen: Feldtrennung, CheckConstraint, pre_save-Wächter und ruecknahme_folgebeleg bleiben Welle 3.

## Etappe 4: Der Entwurf bekommt Ausgänge: keine gültige XML, dafür bearbeiten und finalisieren

**Zweck:** F-13 vollständig und F-14 für den Alltagsfall. Der gefährlichste Einzelweg des ganzen Clusters wird an der Quelle geschlossen, und 'erstmal speichern, morgen weitermachen' wird erstmals real. Dazu der neu bestätigte Angebots-Blocker.

**Dateien:** sedo/services/invoices.py, sedo/views.py, sedo/urls.py, sedo/templates/sedo/invoice_detail.html, sedo/templates/sedo/invoice_form.html, sedo/services/__init__.py, sedo/tests/test_review_v2.py

**Migration noetig:** nein

### Schritte

1. sedo/services/invoices.py:154-202 generate_invoice_files bekommt einen Entwurfszweig – die wichtigste einzelne Änderung des ganzen Plans. Für invoice.status == 'entwurf' wird im Temp-Verzeichnis NUR create_pdf(row, …) aufgerufen, das PDF nach out_dir übernommen, invoice.pdf_file gesetzt und invoice.xml_file.name = '' sowie invoice.zugferd_file.name = '' geleert. Kein build_xml, kein embed_xml_into_pdf. Nachgeprüfte Begründung: is_draft steuert in sedo/services/invoices.py:148 nur das Wasserzeichen, build_xml (sedo/core/rechnungslauf_core.py:593) kennt das Flag überhaupt nicht (einzige Fundstellen: :294 Dataclass-Feld, :878/:959/:1044/:1077 in create_pdf). Ein Entwurf trägt heute also eine vollgültige factur-x.xml. Nebeneffekt: die Download-Knöpfe in invoice_detail.html:9 und :11 verschwinden für Entwürfe von selbst, weil die Feldwerte leer sind.
2. sedo/views.py:996-1003 invoice_generate braucht damit KEINEN eigenen Statusguard – der Klick auf 'Dateien neu erzeugen' (invoice_detail.html:14) erzeugt für einen Entwurf nur noch das Wasserzeichen-PDF und ist harmlos.
3. sedo/services/invoices.py: NEU finalisiere_entwurf(invoice, request). Reihenfolge ist tragend, hier hat Entwurf 3 sich geirrt und Entwurf 1 recht: (1) in transaction.atomic ein guarded UPDATE 'Invoice.objects.filter(pk=…, status="entwurf").update(status="erstellt", updated_at=now)' im Muster von mark_invoice_corrected (invoices.py:72-74); bei 0 Zeilen WorkflowError gegen den Doppelklick. (2) invoice.refresh_from_db(fields=['status']) – ohne diese Zeile bleibt der In-Memory-Status 'entwurf', is_draft (invoices.py:148) liefert True und die finalisierte Rechnung bekommt ein Wasserzeichen-PDF ohne XML. (3) NUR mit Etappe 6: wenn invoice.number mit 'EN' beginnt, save_with_unique_number(invoice, 'RE'). (4) generate_invoice_files(invoice) – jetzt alle drei Dateien. (5) Wenn invoice_type == '384' und reference_invoice gesetzt: erst prüfen, ob das Original inzwischen ein aktives Storno hat oder eine FREMDE aktive Korrektur (original.followups.filter(invoice_type='384', deleted_at__isnull=True).exclude(pk=invoice.pk)), dann mark_invoice_corrected(original). ACHTUNG: ensure_correction_allowed(original) darf hier NICHT aufgerufen werden – Zeile 56 prüft original.active_correction, und das ist der Entwurf selbst; der Aufruf würde immer scheitern (Entwurf 2 schlägt genau das vor). (6) write_audit(…, 'update', invoice, pflicht=True, metadata={'von':'entwurf','nach':'erstellt','anlass':'finalisieren'}).
4. sedo/services/invoices.py: bei Storno und Korrektur denselben Protokolleintrag nachziehen – mark_invoice_corrected (72-77) und _create_storno_inner (257-258) schreiben zusätzlich write_audit mit metadata={'von':…, 'nach':'korrigiert'/'storniert', 'anlass':'korrektur'/'storno'}. Abgeleitete Zahlstatus-Wechsel werden bewusst NICHT protokolliert: ein GET auf Offene Posten würde sonst bis zu MAX_OPEN_ITEMS=500 Protokollzeilen erzeugen (sedo/views.py:52).
5. sedo/views.py: NEU invoice_finalize(request, pk), @login_required @require_POST, dünne Hülle um finalisiere_entwurf mit WorkflowError-Behandlung im Muster von invoice_storno (1065-1074).
6. sedo/views.py: NEU invoice_edit(request, pk) – bewusst KEINE Auslagerung des 190-Zeilen-POST-Zweigs von invoice_create. Der Zweig mischt Kundenanlage aus Kopffeldern (_customer_from_invoice_form, :210-231, legt in Zeile 221 einen NEUEN Customer an), Objektanlage (:234-248), Angebots- und Korrekturherkunft, Nummernvergabe und Dateierzeugung; ihn wiederzuverwenden erzeugt in einer Live-DB mit 44 echten Kunden Dubletten. Stattdessen eine eigene, kurze View: nur für invoice.status == 'entwurf' (sonst messages.error + Redirect); InvoiceForm(instance=invoice) und inlineformset_factory(…, extra=1) auf derselben Instanz; im POST wird ausschliesslich ein BEREITS GESPEICHERTER Kunde akzeptiert – ist form.cleaned_data['customer'] leer, gibt es einen Formularfehler statt einer Kundenanlage; danach recalc_invoice, invoice.save(update_fields=['customer','obj','issue_date','period_start','period_end','vat_rate','note','net_total','vat_amount','gross_total','updated_at']), _normalize_item_positions und generate_invoice_files (Entwurfszweig, also nur das Wasserzeichen-PDF). invoice_type und status werden hier NICHT gesetzt – sedo/views.py:871-872 darf im Edit-Pfad nicht laufen, sonst wird aus einer Korrektur (384) wieder eine normale Rechnung.
7. sedo/views.py:1078-1096 invoice_delete: beim Verwerfen eines Entwurfs die Angebotsverknüpfung lösen. Der Fund ist bestätigt – sedo/views.py:912-920 setzt offer.status='in_rechnung' und linked_invoice AUCH bei action=='draft', anders als die Zeilen 910 und 925, die beide auf action != 'draft' prüfen; danach sperrt ensure_offer_invoicing_allowed (sedo/services/invoices.py:268-274) das Angebot dauerhaft. In derselben Transaktion wie der soft_delete: für jedes Angebot aus invoice.source_offer.all() linked_invoice=None und status='angenommen' setzen, mit zweitem Pflicht-Audit, der beide Nummern nennt. Nebenbefund für den Umsetzer: der '(ausgeblendet)'-Zweig in invoices.py:270 ist toter Code – Invoice._base_manager ist all_objects (geerbt über die MRO von SoftDeleteModel, siehe .venv/.../django/db/models/options.py:471-479), offer.linked_invoice liefert also auch ausgeblendete Rechnungen mit ihrer echten Nummer.
8. sedo/urls.py: zwei Zeilen nach Zeile 22 – path('rechnungen/<int:pk>/finalisieren/', views.invoice_finalize, name='invoice_finalize') und path('rechnungen/<int:pk>/bearbeiten/', views.invoice_edit, name='invoice_edit').
9. sedo/templates/sedo/invoice_detail.html: für Entwürfe ein Hinweisband ('Entwurf – noch nicht ausgestellt. Es gibt kein XML und keine E-Rechnung.') plus drei Knöpfe: 'Bearbeiten', 'Rechnung finalisieren' (POST, mit Bestätigungsdialog) und 'Entwurf verwerfen'.
10. sedo/templates/sedo/invoice_form.html: der Hilfetext in Zeile 191 verspricht heute 'Entwurf speichert ohne Dateierzeugung' – das stimmt erst jetzt. Text präzisieren: 'Entwurf speichert ohne E-Rechnungs-XML; du kannst ihn später bearbeiten und finalisieren.'

### Testplan

- F-13 Kern: Entwurf anlegen und 'Dateien neu erzeugen' auslösen → pdf_file gesetzt, xml_file und zugferd_file LEER; der Verweis darauf, dass invoice_to_core_row is_draft=True liefert.
- Nach dem Finalisieren sind alle drei Dateien gesetzt, die factur-x.xml existiert physisch und enthält die Rechnungsnummer, und invoice_to_core_row liefert is_draft=False. (Dieser Test fällt durch, wenn jemand refresh_from_db vergisst.)
- Zweiter POST auf invoice_finalize → abgewiesen, keine zweite Nummer, keine zweiten Dateien.
- Korrektur-Entwurf finalisieren → das Original steht danach auf 'korrigiert'. Gegenprobe: wurde das Original zwischenzeitlich storniert, bricht das Finalisieren ab und der Entwurf bleibt Entwurf.
- invoice_edit: Position und Betrag eines Entwurfs ändern → Nummer unverändert, gross_total neu gerechnet, KEIN neuer Customer entstanden (Customer.objects.count() vor/nach). invoice_edit auf einer finalisierten Rechnung → 302 mit Meldung.
- invoice_edit mit leerem customer-Select → Formularfehler, kein neuer Kunde, Rechnung unverändert.
- Angebot: Rechnung aus Angebot als Entwurf speichern, Entwurf verwerfen → offer.linked_invoice ist None, offer.status ist 'angenommen', ensure_offer_invoicing_allowed wirft nicht mehr, zwei Pflicht-Audit-Einträge nennen beide Nummern.
- Entwurf im Monatsabschluss: ein Entwurf und eine ausgestellte Rechnung im selben Monat → invoice_count, gross_total und ZIP enthalten nur die ausgestellte.
- Bankimport mit der Entwurfsnummer im Verwendungszweck → Payment entsteht, bleibt aber unzugeordnet.
- AuditLog: nach Storno, Korrektur und Finalisieren existiert je ein Eintrag mit metadata['von'], ['nach'] und ['anlass']. Nach einem GET auf Offene Posten mit 20 Rechnungen entsteht KEIN einziger zusätzlicher Eintrag.

---

## Etappe 5: Bestandsreparatur: belegketten_pruefen mit Trockenlauf

**Zweck:** Die heute schon kaputten Belege. Etappen 1-4 verhindern NEUE Überschreibungen, sie reparieren keine alten. Dieses Kommando setzt den Belegzustand aus den tatsächlichen Folgebelegen zurück – mit Protokolleintrag je Änderung, nicht still.

**Dateien:** sedo/management/commands/belegketten_pruefen.py, sedo/views.py, sedo/templates/sedo/security_status.html, sedo/tests/test_review_v2.py

**Migration noetig:** nein

### Schritte

1. Neue Datei sedo/management/commands/belegketten_pruefen.py (bisher liegt dort nur ensure_admin.py). Standardlauf ist REIN LESEND und druckt einen Bericht.
2. Gruppe A – verlorener Belegzustand (der F-07-Schaden im Bestand). Regel aus Entwurf 3: aktives Storno vorhanden (followups mit invoice_type='457' und deleted_at IS NULL) und status != 'storniert' → 'storniert'. Aktive Korrektur vorhanden (invoice_type='384', deleted_at IS NULL) und status nicht in ('storniert','korrigiert') → 'korrigiert'. Beides gleichzeitig → 'storniert' gewinnt, und der Fall wird mit beiden Belegnummern als 'bitte prüfen' ausgegeben.
3. Gruppe B – die F-14-Sackgasse: status in ('storniert','korrigiert'), aber KEIN aktiver Folgebeleg (der Fall, den sedo/tests/test_workflow_locks.py:72-80 heute nur dokumentiert, indem der Test den Status von Hand zurücksetzt). → 'erstellt', danach refresh_invoice_payment_status für die Neuableitung des Zahlstands.
4. Gruppe C – Bestandsentwürfe mit Dateien: status='entwurf' und (xml_file oder zugferd_file gesetzt). Diese Belege tragen bereits eine vollgültige factur-x.xml, die der Entwurfszweig aus Etappe 4 nicht rückwirkend entfernt. Behandlung nach Entscheidung 6.
5. Gruppe D – rein informativ: Entwürfe älter als 30 Tage, und Stornobelege (invoice_type='457') mit status='bezahlt' (der F-89-Schaden im Bestand) → auf 'erstellt' zurücksetzen.
6. Mit --reparieren werden die Gruppen A, B und D geschrieben, jede Änderung über eine einzige Hilfsfunktion, die ein guarded UPDATE absetzt und write_audit(action='update', metadata={'von':…, 'nach':…, 'anlass':'reparatur'}, pflicht=True) schreibt. Bricht der Protokolleintrag, bricht die Änderung.
7. Zusätzlich --json für einen maschinenlesbaren Bericht, damit der Trockenlauf vor und nach dem Deploy vergleichbar ist.
8. sedo/views.py:2385ff. security_status: eine rein LESENDE Prüfzeile in die checks-Liste – label 'Belegketten in Ordnung', ok = (keine Funde in Gruppe A und B), value = Anzahl der Funde, hint = 'Bitte python manage.py belegketten_pruefen --reparieren ausführen lassen'. Damit sehen die zwei Nutzer, DASS etwas hängt, ohne ein Terminal zu brauchen.

### Testplan

- Fünf künstlich verbogene Fälle anlegen: (1) Original 'ueberfaellig' trotz aktiver Korrektur → wird 'korrigiert'; (2) Original 'bezahlt' trotz aktivem Storno → wird 'storniert'; (3) beides gleichzeitig → 'storniert' plus Prüfhinweis mit beiden Nummern; (4) Original 'storniert' ohne aktiven Folgebeleg → wird 'erstellt'; (5) sauberer Fall → unverändert.
- Der lesende Standardlauf ändert NICHTS (Statuswerte und updated_at vorher/nachher vergleichen) und meldet vier Funde.
- --reparieren erzeugt je Änderung genau einen AuditLog-Eintrag mit von, nach und anlass='reparatur'.
- Nach der Reparatur von Fall (4) ist create_storno auf dem Original wieder möglich – dieser Test ersetzt fachlich das, was sedo/tests/test_workflow_locks.py:72-80 heute mit einem Handreset umgeht (der Test selbst bleibt unverändert grün).
- Stornobeleg mit status='bezahlt' → wird 'erstellt' (F-89 im Bestand).
- security_status: bei einem verbogenen Bestand enthält die gerenderte Seite die Zeile 'Belegketten in Ordnung' mit ok=False.

---

## Etappe 6: Optional und herauslösbar: eigener Entwurfs-Nummernkreis EN

**Zweck:** Der GoBD-Kern von F-13, den Entwurf 1 offen lässt: heute verbrennt jeder verworfene Entwurf eine echte Rechnungsnummer (sedo/views.py:904). Mit einem eigenen Präfix bleibt der RE-Kreis lückenlos. Bewusst als letzte Etappe, einzeln zurücknehmbar.

**Dateien:** sedo/views.py, sedo/services/invoices.py, sedo/templates/sedo/invoice_form.html, sedo/tests/test_number_range.py

**Migration noetig:** nein

### Schritte

1. sedo/views.py:904: save_with_unique_number(inv, 'RE') wird zu save_with_unique_number(inv, 'EN' if action == 'draft' else 'RE'). Technisch geprüft und tragfähig ohne Migration: NumberRange.prefix ist ein freies CharField(10) ohne choices (sedo/models.py:387), _model_for liefert für jeden Präfix ausser 'AG' das Invoice-Modell (sedo/services/numbering.py:28-29), _scan_next_seq filtert über number__startswith (:32-43), _allocate_number legt den Nummernkreis per get_or_create an (:67-70), und _INVOICE_NO_PATTERN = r'(?:RE|AG)\d{6,}' (sedo/services/payments.py:17) kann eine EN-Nummer nie aus einem Verwendungszweck treffen.
2. sedo/services/invoices.py finalisiere_entwurf, Schritt (3): nur wenn invoice.number mit 'EN' beginnt, wird save_with_unique_number(invoice, 'RE') aufgerufen. Bestandsentwürfe mit RE-Nummer behalten sie – eine Umnummerierung historischer Belege wäre schlimmer als die Lücke, die sie hinterlassen haben.
3. Kein Datei-Waisen-Problem: ein EN-Entwurf hat nach Etappe 4 nur ein Wasserzeichen-PDF unter media/generated/EN…/. Das Verzeichnis in derselben Transaktion nicht löschen, sondern beim Finalisieren stehenlassen und im Audit-Eintrag beide Nummern nennen (alte EN-Nummer und neue RE-Nummer), damit die Belegkette für einen Prüfer lesbar bleibt.
4. sedo/views.py:802 baut das leere Formular mit next_number('RE') – die Vorschau zeigt eine RE-Nummer, obwohl beim Speichern als Entwurf eine EN-Nummer vergeben wird. In sedo/templates/sedo/invoice_form.html neben dem Entwurf-Knopf (Zeile 229) einen Hinweis ergänzen: 'Ein Entwurf bekommt eine Entwurfsnummer (EN…) und erst beim Finalisieren die Rechnungsnummer.' next_number('EN') ist rein lesend und verbraucht nichts (sedo/services/numbering.py:46-57) und kann für die Vorschau zusätzlich angezeigt werden.
5. sedo/templates/sedo/invoice_detail.html: das Entwurfs-Hinweisband nennt die EN-Nummer und sagt ausdrücklich, dass die Rechnungsnummer erst beim Finalisieren entsteht.

### Testplan

- next_number('RE') ist vor und nach dem Speichern eines Entwurfs identisch – der RE-Kreis wird nicht mehr angefasst.
- Die Entwurfsnummer beginnt mit 'EN'; NumberRange bekommt eine zweite Zeile mit prefix='EN'.
- Finalisieren: EN-Nummer wird zu einer RE-Nummer aus dem Kreis, der Audit-Eintrag nennt beide.
- Ein Bestandsentwurf mit RE-Nummer behält sie beim Finalisieren.
- Bankimport mit einer EN-Nummer im Verwendungszweck erzeugt keine Zuordnung (Regex trifft nicht).
- sedo/tests/test_number_range.py läuft unverändert (die vorhandenen Tests prüfen RE und AG, nicht EN); sedo/tests/test_wizard.py:350-378 legt einen Entwurf per POST an und prüft nur den Langtext – bleibt grün.

---

## Etappe 7: Optional, kann entfallen: Index auf status (die einzige Migration des Plans)

**Zweck:** Heute existiert kein einziger Index auf Invoice.status – das einzige db_index=True im Modell steht auf Payment.external_id (sedo/models.py:363) –, während get_dunning_candidates in sedo/services/dunning.py:182-183 genau die Kombination exclude(status__in=…) + filter(due_date__lte=…) abfragt. Ehrlich gesagt: bei 44 Kunden bringt das nichts. Es steht hier, weil es billig ist und nicht, weil es nötig wäre.

**Dateien:** sedo/models.py, sedo/migrations/0013_invoice_status_index.py

**Migration noetig:** JA

### Schritte

1. sedo/models.py in der Meta von Invoice (Zeilen 155-187) eine indexes-Liste ergänzen: models.Index(fields=['status'], name='invoice_status_idx') und models.Index(fields=['status','due_date'], name='invoice_status_due_idx').
2. manage.py makemigrations erzeugt 0013 mit zwei AddIndex-Operationen. Geprüft: der SQLite-Schema-Editor überschreibt add_index NICHT (.venv/lib/python3.14/site-packages/django/db/backends/sqlite3/schema.py enthält nur add_constraint/remove_constraint in Zeile 480-500) – ein AddIndex ist also ein schlichtes CREATE INDEX ohne Tabellen-Neuaufbau. Das ist der entscheidende Unterschied zum CheckConstraint aus Entwurf 3, den add_constraint in Zeile 489 in _remake_table leitet.
3. Kein RunPython, keine Datenänderung. reverse ist RemoveIndex.
4. Ablauf: App über SEDO_BEENDEN.command schliessen, Backup über die eingebaute Backup-Seite, Probelauf auf einer Kopie der Datenbank in einem Temp-Verzeichnis AUSSERHALB des Projektordners, dann migrate.

### Testplan

- manage.py makemigrations --check --dry-run ist nach dieser Etappe wieder befundfrei.
- migrate und migrate sedo 0012 laufen beide durch (Vorwärts- und Rückwärtslauf auf der Kopie).
- Die gesamte Suite bleibt grün – ein Index ändert kein Verhalten.

---

## Bewusst auf Welle 3 verschoben

- Die echte Feldtrennung (lifecycle + payment_state, Entwurf 2). Fachlich richtig und von allen drei Juroren so gesehen, aber XL-Aufwand, fünf neue Spalten, Datenumschrieb auf der Live-DB und Anpassungen in neun Testdateien. Gehört auf die vorhandene Testinstanz und hinter den 01.01.2027. Drei Vorgaben für dann: die Migration muss lifecycle aus den TATSÄCHLICHEN Folgebelegen rekonstruieren statt 1:1 aus dem verfälschten Altstatus; sent_at darf für den Bestand NICHT aus updated_at erfunden werden (NULL mit Vermerk); und die Kreuzprüfungen auf active_storno/active_correction müssen mitwandern.
- Der pre_save-Wächter aus Entwurf 3 (StatusUmgangenError). Er ist die sauberste Durchsetzung, kostet aber Anpassungen an sedo/tests/test_workflow_locks.py:58-59, :77-78 und :169-170 sowie eine Umstellung des blanken invoice.save() in sedo/services/invoices.py:163 – und er sieht QuerySet.update() prinzipiell nicht, also genau das Muster, das in invoices.py:72-74 produktiv steht. Für zwei Nutzer vier Monate vor dem Echtbetrieb ist das Verhältnis aus Nutzen und Bruchrisiko falsch herum.
- Der CheckConstraint auf die neun Statuswerte. Nachgeprüft und deshalb gestrichen: add_constraint erzwingt auf SQLite einen Neuaufbau der Rechnungstabelle (.venv/lib/python3.14/site-packages/django/db/backends/sqlite3/schema.py:480-489), während der Gewinn gering ist – er prüft nur den Wertebereich, den heute nichts verletzt, nicht die Übergänge. Wenn überhaupt, dann später auf der Testinstanz zusammen mit der Feldtrennung.
- ruecknahme_folgebeleg aus Entwurf 3 ('Storno zurücknehmen'). Der Gedanke ist gut – heute steht der einzige Ausweg aus einem Fehlklick im Testcode –, aber es bleibt ein Weg, einen vergebenen, nummerierten Beleg verschwinden zu lassen, und Juror 0 hat ihn ausdrücklich gestrichen. In dieser Welle ist der Ersatz das protokollierte Reparaturkommando (Gruppe B) und, für neue Fälle, das Storno des Stornos.
- Der Bauordnungs-Test aus Entwurf 3 (Quelltextsuche nach '.status =' und 'update(status=' ausserhalb einer Zustandsmaschine). Er schliesst die Lücke, die kein pre_save-Signal sieht, braucht aber eine gepflegte Ausnahmeliste – ein Grep kann Invoice nicht von Offer unterscheiden (sedo/views.py:1128 und :1174 sind legitime Treffer). Sinnvoll erst zusammen mit der Zustandsmaschine.
- Der GWG-Unique-Constraint (sedo/models.py:162-168) kennt weiterhin nur ~Q(status='storniert'). Eine korrigierte GWG-Monatsrechnung blockiert damit einen erneuten Monatslauf für denselben Zeitraum. Das zu ändern ist eine Constraint-Migration und damit ein Table-Rebuild – gleiche Begründung wie beim CheckConstraint. Juror 2 hat zu Recht angemerkt, dass der Fall selten ist, weil die Korrektur source='Korrektur' trägt (sedo/views.py:894) und gar nicht kollidiert.
- Eine Verlaufsanzeige je Rechnung auf der Detailseite. Die Belegzustandswechsel stehen ab Etappe 4 mit von/nach/anlass im AuditLog, aber AuditLog hat keinen Index auf object_type/object_id (sedo/models.py:481-507) – eine Historie je Rechnung wäre ein Table-Scan. Sichtbar sind die Wechsel bis dahin nur auf der allgemeinen Audit-Log-Seite.
- F-67 vollständig: negative oder Null-Beträge in ManualPaymentForm (sedo/forms.py:139-152) und die fehlende Guthabenführung beim Storno einer voll bezahlten Rechnung. Dieser Plan sperrt den Belegzustand, nicht den Betrag.
- F-68 vollständig: get_dunning_candidates prüft im Einzelfall weiterhin keine Fälligkeit über den Schwellwert hinaus. Entwürfe, Stornobelege und ersetzte Originale fallen ab Etappe 1 heraus, der Rest bleibt.
- F-63 an den verbleibenden Fundorten: sedo/views.py:321 (Abo-Vorlage aus Rechnung), :1127 (Angebots-Gültigkeit) und :2154 (Abo-Monatslauf). In dieser Welle werden nur die zwei Rechnungspfade :870 und :230 korrigiert, damit die Änderung im Statuscluster bleibt.
- Die kosmetischen Statusanzeigen ausserhalb der umgestellten Listen: sedo/views.py:80 (global_search_api, 'badge': get_status_display), aareon_monthly.html:51, aareon_import.html:66 und payment_list.html:32. Sie lesen den gespeicherten Wert weiter und zeigen für Entwürfe und korrigierte Belege dasselbe wie bisher.