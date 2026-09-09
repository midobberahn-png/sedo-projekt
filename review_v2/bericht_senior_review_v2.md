# Senior-Code-Review SEDO v2 – Fundliste

**Stand:** September 2026, Codebasis SEDO Rechnungstool v2 (Django 6.0.8, SQLite), Prüfung ausschließlich lesend.
**Umfang:** ~7.500 Zeilen Python (views, services, core, models, migrations, Startskripte, desktop.py) plus 34 Templates, 8 JS-Dateien, Konfiguration, Tests (245 grün, 1 Skip).
**Methode:** 9 Prüfdimensionen (Datenmodell/Migrationen, Datenschutz/KI, Frontend/JS/Templates, Funktionalität der Views, GoBD/Geschäftslogik, Sicherheit Web, Sicherheit Secrets/Banking, Tests/Konfiguration/Deployment, ZUGFeRD/XML/PDF); jeder Fund von zwei unabhängigen Gutachtern (Reproduktion + Skeptiker) bestätigt, strittige Fälle vom Hauptreviewer entschieden; gleiche Ursachen aus mehreren Dimensionen wurden zu einem Fund zusammengeführt.

## 1. Auf einen Blick

| Schwere | Anzahl (nach Zusammenführung) | Rohfunde vor Zusammenführung |
|---|---|---|
| kritisch | 0 | 0 |
| hoch | 18 | 19 |
| mittel | 54 | 67 |
| niedrig | 53 | 57 |
| **Summe** | **125** | **143** |

**Gesamteinschätzung.** Das Grundgerüst ist solide: jede Route ist per Middleware und `@login_required` geschützt, CSRF ist in allen 51 POST-Formularen und allen fetch()-Aufrufen vorhanden, der Nummernkreis arbeitet mit F()-Update in einer Transaktion, Soft-Delete und Storno-statt-Löschen sind konsequent gedacht, und die ZUGFeRD-XML ist in den Kernfeldern (BT-1/2/3/5, Verkäufer-IDs, Summen, Rundung) korrekt. Die 245 Tests laufen grün. Kritische Funde (Datenverlust ohne Nutzeraktion, ausnutzbare Lücke mit fertigem Angriffspfad) gab es keine; dafür 18 Funde der Stufe „hoch“, die sich in vier Baustellen bündeln.

Erste Baustelle ist das **Statusmodell**: `Invoice.status` kodiert zugleich Zahlstatus und Lebenszyklus. Dadurch werden „korrigiert“ und „entwurf“ von einem bloßen GET auf „Offene Posten“, von Zahlungen und vom E-Mail-Versand überschrieben, Storno-Sperren fallen, Dashboard-Summen verdoppeln sich und Entwürfe bekommen echte Nummern ohne Weg zur Finalisierung (F-07, F-13, F-14, F-60, F-67). Zweite Baustelle ist die **formale Gültigkeit der E-Rechnung**: BT-25 steht an XSD-widriger Stelle, das PDF ist kein PDF/A-3, Stornos zu GWG-Rechnungen verlieren die ARS-Felder, und die Formulare lassen negative Preise, 0 % USt mit Kategorie S und rückwärtige Zeiträume durch (F-04, F-05, F-06, F-08, F-33, F-34). Dritte Baustelle ist **Datenschutz vor der KI**: die harte Projektregel „nur Leistungsdaten“ ist ausschließlich als Prompt-Anweisung umgesetzt, Transkripte werden ohne Prüfschritt automatisch gesendet, und der Web-Speech-Fallback schickt Audio an Google, während die UI das Gegenteil zusichert (F-41, F-42, F-50, F-51). Vierte Baustelle ist der **Betrieb im Tunnel**: Django-runserver ohne Brute-Force-Schutz, ein Admin mit hartem Löschen, Backups mit gültigen Session-Keys und Secret-Restbytes, kein Restore-Pfad, Testläufe, die den echten Backup-Ordner füllen, und Startskripte mit `makemigrations` (F-01, F-02, F-03, F-23, F-24, F-26, F-31, F-32, F-37, F-38). Quer dazu liegen drei destruktive Importpfade (Kundenimport überschreibt mit Leerwerten, Mapping-Import löscht bei 0 Zeilen, GWG-Lauf setzt 10,00 € bei leerem Preis – F-12, F-16, F-17), die mit je einer Zeile Prüfung entschärft wären.

## 2. Funde nach Schwere

### F-01 · [HOCH] · Kein Brute-Force-Schutz und keine Passwortregeln an /login/ und /admin/login/ trotz Tunnel-Exposition
- **Wo:** config/settings.py:192 (kein `AUTH_PASSWORD_VALIDATORS`), config/urls.py:9, sedo/middleware.py:17-22
- **Was:** Es gibt weder Passwortvalidatoren noch Rate-Limiting/Lockout (kein django-axes, keine eigene Middleware). `/admin/login/` ist zusätzlich vom Login-Zwang ausgenommen. Der einzige Account ist ein Superuser, dessen Passwort per Admin auf beliebig schwache Werte geändert werden kann.
- **Beleg:** `EXEMPT_PREFIXES = ('/login/', '/admin/', '/static/', '/favicon.ico')`; grep nach `AUTH_PASSWORD_VALIDATORS|axes|ratelimit` über config/, sedo/, requirements.txt: leer.
- **Szenario:** Tunnel aktiv, Passwort per Admin auf „Sedo2025!“ gesetzt. Ein Wortlisten-Skript mit tausenden Versuchen pro Minute gegen `https://<tunnel>/login/` wird nicht gebremst; nach Treffer voller Superuser-Zugriff inkl. Admin, Kundenexport, SMTP-Versand.
- **Auswirkung:** Realer Angriffspfad auf den einzigen Account; Kompromittierung aller Kunden- und Rechnungsdaten.
- **Fix:** `AUTH_PASSWORD_VALIDATORS` (MinimumLength ≥ 12, CommonPassword, UserAttributeSimilarity); Login-Throttling (django-axes oder Cache-Zähler pro Nutzer+IP mit Sperre) für `/login/` und `/admin/login/`; im Tunnelbetrieb Cloudflare Access vorschalten; `/admin/` nur lokal.
- **Aufwand:** M · **Gutachter-Notiz:** Drei Gutachter (Vorab-Verifikation), alle Belege bestätigt; „hoch“ statt „kritisch“, weil ein Angriff Kenntnis der Tunnel-URL voraussetzt.

### F-02 · [HOCH] · Django-Admin erlaubt hartes Löschen/Bearbeiten von Rechnungen, Zahlungen, Mahnungen und Audit-Logs – und zeigt soft-gelöschte Belege nicht
- **Wo:** sedo/admin.py:18-23 (InvoiceAdmin), :36 (Payment), :40 (DunningDocument), :44-55 (AuditLogAdmin); sedo/models.py:216/:445 (CASCADE), :341/:139 (SET_NULL)
- **Was:** Kein `has_delete_permission`, keine `readonly_fields`; der Admin nutzt den Default-Manager `objects` (ActiveManager), so dass ausgeblendete Belege nirgends einsehbar sind – der Docstring in models.py:18 behauptet das Gegenteil.
- **Beleg:** `@admin.register(Invoice) class InvoiceAdmin(admin.ModelAdmin): …` ohne Löschsperre; `admin.site.register(Payment)`.
- **Szenario:** Superuser markiert unter /admin/sedo/invoice/ eine versendete Rechnung → „Ausgewählte löschen“: Rechnung samt Positionen und Mahnungen dauerhaft weg, Zahlungen verwaist, Stornorechnung verliert `reference_invoice`, kein SEDO-Audit-Eintrag.
- **Auswirkung:** Pfad zu Datenverlust und GoBD-Verstoß (Nummernlücke, gelöschtes Protokoll); Soft-Delete ohne Einsicht erfüllt seinen Zweck nicht.
- **Fix:** Für Invoice, Payment, Offer, DunningDocument, AuditLog `has_delete_permission → False`, `readonly_fields` für Nummer, Beträge, Status, Typ, `deleted_at`; `get_queryset → all_objects` mit `deleted_at` in `list_display` – oder Admin abschalten und eigene Nur-Lese-Ansicht „Ausgeblendete Belege“. Optional `pre_delete`-Signal, das wirft.
- **Aufwand:** S · **Gutachter-Notiz:** Beide Gutachter bestätigen; Schwere „hoch“, weil der Pfad eine bewusste Nutzeraktion braucht, aber nichts ihn verhindert.

### F-03 · [HOCH] · Backup-Datenbank enthält `django_session` mit gültigen Session-Keys – Login ohne Passwort aus einem Backup heraus
- **Wo:** sedo/backup_utils.py:40-41; config/settings.py:110 (kein `SESSION_ENGINE`, kein `SESSION_COOKIE_AGE`)
- **Was:** Die „bereinigte“ Kopie leert nur drei AppSetting-Zeilen. Sessions liegen in der DB (14 Tage); der Session-Key ist der Cookie-Wert.
- **Beleg:** `con.execute(f"UPDATE sedo_appsetting SET value='' WHERE key IN ({placeholders})", SECRET_SETTING_KEYS)` – sonst keine Bereinigung.
- **Szenario:** Nutzer meldet sich an, Wochenbackup läuft, ZIP wird an Steuerberater/USB weitergegeben. Empfänger liest `SELECT session_key FROM django_session WHERE expire_date > now()`, setzt Cookie `sessionid=<key>` gegen die Tunnel-URL → als admin angemeldet.
- **Auswirkung:** Backup-Besitz = Kontoübernahme für bis zu 14 Tage.
- **Fix:** In `_sanitized_db_copy` zusätzlich `DELETE FROM django_session` (+ `VACUUM`, siehe F-31); `SESSION_COOKIE_AGE` verkürzen (F-26); Test erweitern.
- **Aufwand:** S · **Gutachter-Notiz:** Auf einer Scratchpad-Kopie nachgestellt; beide Gutachter bestätigen.

### F-04 · [HOCH] · `InvoiceReferencedDocument` (BT-25) an XSD-widriger Stelle – Storno-/Korrektur-XML schemaungültig
- **Wo:** sedo/core/rechnungslauf_core.py:743-749 (Summation erst Z. 780)
- **Was:** Bei gesetzter `reference_invoice_no` wird BT-25 direkt nach `InvoiceCurrencyCode` und vor `SpecifiedTradeSettlementPaymentMeans` eingefügt. Das CII-D16B/Factur-X-XSD verlangt es nach `SpecifiedTradeSettlementHeaderMonetarySummation`. Der Code kennt dasselbe Problem bei `PostalTradeAddress` (Z. 728-730), hier wurde es übersehen.
- **Beleg:** `sub(settlement, "ram", "InvoiceCurrencyCode", "EUR") … if ref_invoice_no: refdoc = sub(settlement, "ram", "InvoiceReferencedDocument") … means = sub(settlement, "ram", "SpecifiedTradeSettlementPaymentMeans")`
- **Szenario:** Storno (457) oder Korrektur (384) erzeugen → XSD-Validierung (Aareon ARP, Mustang, Factur-X-Validator) meldet „Invalid content … expected SpecifiedTradeSettlementPaymentMeans“.
- **Auswirkung:** Alle Storno- und Korrekturrechnungen sind als E-Rechnung schemaungültig – genau die Belege, mit denen Aareon/GWG Buchungen aufheben.
- **Fix:** Den if-Block hinter `sub(sm, 'DuePayableAmount', …)` (nach Z. 788) verschieben; optional `FormattedIssueDateTime` (BT-26, Format 102) ergänzen; `test_typecode_storno` um XSD-Validierung erweitern.
- **Aufwand:** S · **Gutachter-Notiz:** Reproduziert per Storno-Beispiel im Scratchpad; beide Gutachter bestätigen.

### F-05 · [HOCH] · ZUGFeRD-PDF ist kein PDF/A-3: keine XMP-Metadaten, Fonts nicht eingebettet, EmbeddedFile ohne /Subtype und /ModDate
- **Wo:** sedo/core/rechnungslauf_core.py:1120-1150 (`embed_xml_into_pdf`), :847 ff. (`create_pdf`, Helvetica ohne Einbettung)
- **Was:** Die XML wird nur als Attachment mit `/AFRelationship /Data` angehängt. Es fehlen `/Metadata`-XMP (pdfaid:part=3, conformance B, fx:DocumentType/DocumentFileName/Version/ConformanceLevel), OutputIntent, Dokument-ID, `/Subtype /text#2Fxml` und `/Params /ModDate` am EmbeddedFile; das `/AF`-Array enthält eine Kopie des Filespec statt der Indirektreferenz.
- **Beleg:** Am erzeugten PDF: `ROOT keys ['/Type','/Pages','/Names','/AF']; EmbeddedFile dict {'/Type': '/EmbeddedFile'}; Metadata XMP present: False; OutputIntents: False; Fonts {('/Helvetica', False), …}`
- **Szenario:** Beliebige Rechnung erzeugen → `*_zugferd.pdf` bei ARP, Mustangproject oder FNFE-Validator einreichen → „kein ZUGFeRD/Factur-X-Dokument“ bzw. PDF/A-3-Verletzungen; Empfänger, die die XML nur über die XMP-Kennung erkennen, behandeln das PDF als normales PDF.
- **Auswirkung:** Die Hybridrechnung ist formal keine E-Rechnung nach § 14 UStG i. V. m. EN 16931 (die reine XML bleibt nutzbar); Ablehnungsrisiko beim Kunden und bei Prüfungen.
- **Fix:** XMP-Paket mit pdfaid- und fx-Namespace als `/Metadata`-Stream, EmbeddedFile mit `/Subtype /text#2Fxml` und `/Params {/ModDate,/Size}`, `/AF` mit Indirektreferenz, ReportLab mit eingebetteten TTF-Fonts und sRGB-OutputIntent – oder Einbettung an die Bibliothek `factur-x` delegieren; danach mit veraPDF/Mustang gegenprüfen.
- **Aufwand:** L · **Gutachter-Notiz:** Beide Gutachter haben die PDF-Objektstruktur im Scratchpad nachgebaut und bestätigen alle Teilbefunde.

### F-06 · [HOCH] · Formulare ohne fachliche Validierung: negative Preise/Zahlungen, USt-Satz 0 %/<0/>100, Leistungszeitraum rückwärts erzeugen EN16931-ungültige XML und falsche Beträge
- **Wo:** sedo/forms.py:23-84 (InvoiceForm/InvoiceItemForm), :140-150 (ManualPaymentForm), :152-163 (CashbookEntryForm), :65/:115/:175 (vat_rate-Widgets nur `step`); sedo/models.py:129-133, :222-224, :343 (keine Validatoren); sedo/core/rechnungslauf_core.py:658, :664, :766-771
- **Was:** Keine `clean()`-Methoden, keine `min_value` außer bei `payment_days`/`valid_days`. Der Kern schreibt `unit_price` 1:1 als `ChargeAmount` (BR-27), hartkodiert `CategoryCode "S"` (BR-S-05 bei 0 %) und gibt `BillingSpecifiedPeriod` ohne Reihenfolgeprüfung aus (BR-29). `ManualPaymentForm` akzeptiert `amount ≤ 0`.
- **Beleg:** `class InvoiceItemForm(forms.ModelForm): … fields = ['description','long_text','quantity','unit','unit_price', …]` (kein clean); `sub(price, "ram", "ChargeAmount", money(pos.unit_price))`; ManualPaymentForm mit `amount='-50'` ist `is_valid()`.
- **Szenario:** Position „Rabatt Neukunde 1 × −50,00“ → `<ram:ChargeAmount>-50.00</ram:ChargeAmount>` → Validator lehnt ab, Rechnung ist nummeriert und ggf. versendet. Zahlung „−50“ → `open_amount` steigt von 100 auf 150 €. `vat_rate` „−19“ → negativer `TaxTotalAmount`.
- **Auswirkung:** Ungültige E-Rechnungen mit vergebener Nummer, falsche offene Posten und Mahnbeträge.
- **Fix:** `InvoiceItemForm.clean`: `unit_price ≥ 0`, `quantity ≠ 0`; `InvoiceForm.clean`: `period_end ≥ period_start`, `vat_rate ∈ {0,7,19}` mit passender Kategorie (siehe F-33); `clean_amount > 0` in ManualPaymentForm/CashbookEntryForm; `MinValueValidator` an den Modellfeldern; gleiche Regeln für OfferForm und GWG-Monatslauf.
- **Aufwand:** M · **Gutachter-Notiz:** Zusammengeführt aus gobd-geschaeftslogik (forms.py:69, hoch) und funktionalitaet-views_b (forms.py:140, mittel) – gleiche Ursache „keine clean()“.

### F-07 · [HOCH] · Ein Statusfeld für Zahlstatus UND Lebenszyklus: „korrigiert“ und „entwurf“ werden von OP-GET, Zahlung und E-Mail-Versand überschrieben – Storno-Sperre fällt, Dashboard/OP zählen doppelt
- **Wo:** sedo/services/calc.py:45-60 (`refresh_invoice_payment_status`, schützt nur 'storniert'); sedo/views.py:1204-1205 (open_items ruft refresh je Rechnung auf GET), :600-602 (Dashboard), :1192/:1200/:1331 (OP/Zahlungen), :1017-1019 und :1031-1033 (invoice_email setzt 'versendet'), :1245-1257 (Zahlungserfassung ohne Statusprüfung); sedo/services/invoices.py:44 (`ensure_storno_allowed` prüft nur `status == 'korrigiert'`); sedo/forms.py:137 (Assign-Queryset `Invoice.objects.all()`); sedo/templates/sedo/invoice_detail.html:12/13/18 (Buttons ohne Bedingung)
- **Was:** `Invoice.status` kodiert Zahlstatus (erstellt/teilbezahlt/bezahlt/ueberfaellig/gemahnt) und Belegzustand (entwurf/korrigiert/storniert) im selben Feld. Drei Auslöser überschreiben 'korrigiert'/'entwurf': (a) GET auf „Offene Posten“ → bei fehlender Zahlung und verstrichener Fälligkeit 'ueberfaellig'; (b) jede Zahlung auf das korrigierte Original → 'bezahlt'/'teilbezahlt'; (c) erneuter E-Mail-Versand → 'versendet' (auch für bezahlte/gemahnte Rechnungen). `calc.py:58-59` stellt 'korrigiert' nie wieder her. Danach greift `ensure_storno_allowed` nicht mehr, der DB-Constraint kennt nur Stornos, und Dashboard/OP summieren Original und Korrektur beide als offen.
- **Beleg:** `if invoice.status == 'storniert': return invoice … elif invoice.due_date and invoice.due_date < today: invoice.status = 'ueberfaellig'`; `if invoice.status != 'storniert': invoice.status = 'versendet'`; `invoices.exclude(status__in=['bezahlt','storniert'])`; `_UNDUNNABLE_STATUS = ('bezahlt','storniert','korrigiert','entwurf')` (dunning.py:30 – dort ist es richtig).
- **Szenario:** RE…0001 (119 €, fällig 15.08.) wird am 20.08. durch RE…0007 ersetzt. Dashboard zeigt 238 € offen. Nutzer öffnet „Offene Posten“ → Original wird 'ueberfaellig', Storno-Button erscheint, Batch-Mahnung ohne Auswahl (F-66) schickt eine Zahlungserinnerung zur ersetzten Rechnung; Klick auf Storno erzeugt zusätzlich eine Stornorechnung – Kunde hat Korrektur UND Storno. Gleiches nach Zahlung auf das Original oder nach „E-Mail“ auf die alte Rechnung; bezahlte Rechnung nach Neuversand wieder „offen“.
- **Auswirkung:** Doppelte Forderungsanzeige, Mahnungen für ersetzte Belege, doppelte Gutschrift, Verlust des Lebenszyklus-Status ohne Audit – GoBD-Nachvollziehbarkeit der Belegkette verletzt.
- **Fix:** Minimal: in `refresh_invoice_payment_status` früher Return für `status in ('storniert','korrigiert','entwurf')`; `ensure_storno_allowed`/`ensure_correction_allowed` zusätzlich auf `active_correction`/`active_storno` prüfen; `invoice_email` hebt Status nur von 'erstellt'/'entwurf' auf 'versendet', sonst nur Audit-Eintrag; Zahlungserfassung für korrigierte/stornierte Rechnungen sperren, Assign-Queryset einschränken; zentrale Helferfunktion `open_invoices()` mit `exclude(status__in=['bezahlt','storniert','korrigiert','entwurf'])` und `invoice_type != '457'`; Status-Refresh aus dem GET herausnehmen. Konzeptionell: Lebenszyklus (entwurf/aktiv/korrigiert/storniert) und Zahlstatus in getrennte Felder. Regressionstests.
- **Aufwand:** M (Minimalfix) / L (Trennung) · **Gutachter-Notiz:** Zusammengeführt aus sechs Rohfunden (gobd calc.py:45, views_b 1204, datenmodell calc.py:45, views_b 600, views_b 1017, gobd 1017); der Status-Flip durch open_items-GET und der anschließende Storno wurden per In-Memory-Test reproduziert.

### F-08 · [HOCH] · Storno- und Korrekturrechnungen zu Aareon/GWG-Rechnungen verlieren alle ARS-Felder (Empfänger-ID, Verkäufer-ID, ShipTo-ID, Leistungsart-Note)
- **Wo:** sedo/services/invoices.py:81-87 (`_is_ars_source`), :94, :115-149; :239 (`source='Storno'`); sedo/views.py:884 (`source='Korrektur'`)
- **Was:** Der ARS-Modus (BuyerTradeParty/ID = `aareon_receiver_id`, SellerTradeParty/ID, ShipToTradeParty/ID aus dem Mapping, Leistungsart-IncludedNote) hängt allein an einem Substring-Match auf `invoice.source`. Stornos und Korrekturen bekommen feste Quellen ohne „gwg/aareon/ars“.
- **Beleg:** `return label.startswith(('aareon','ars','gwg')) or 'gwg' in label or …`; `storno = create_numbered_invoice(invoice_type='457', …, source='Storno', reference_invoice=original, …)`
- **Szenario:** GWG-Monatsrechnung stornieren → Storno-XML enthält BuyerTradeParty/ID = customer_no/DB-ID statt Aareon-Empfänger-ID, keine SellerTradeParty/ID, keine Leistungsart-Note, ShipTo/ID nicht aus dem Mapping → ARP kann das Storno nicht zuordnen.
- **Auswirkung:** Stornos/Korrekturen an den Hauptkunden werden im Empfängersystem nicht verbucht bzw. abgewiesen; die Ursprungsbuchung bleibt offen.
- **Fix:** ARS-Modus aus einem Kundenmerkmal ableiten (`customer.aareon_receiver_id` gesetzt) oder für Folgebelege Modus/Source der `reference_invoice` übernehmen.
- **Aufwand:** S · **Gutachter-Notiz:** Beide Gutachter bestätigen die vollständige Kette von `_is_ars_source` bis zu den XML-Feldern.

### F-09 · [HOCH] · `SMTP_SSL` ohne SSL-Kontext: keine Zertifikatsprüfung beim impliziten TLS-Versand (SMTP-Passwort MITM-anfällig)
- **Wo:** sedo/services/mail.py:111-112 (STARTTLS-Pfad Z. 104-107 ist korrekt)
- **Was:** Bei deaktivierter STARTTLS-Option wird `smtplib.SMTP_SSL` ohne `context` aufgerufen; in CPython (venv: 3.14) entspricht das `ssl._create_unverified_context()` (check_hostname=False, CERT_NONE).
- **Beleg:** `with smtplib.SMTP_SSL(cfg['host'], cfg['port'], timeout=SMTP_TIMEOUT_SECONDS) as server: server.login(cfg['user'], cfg['password'])`
- **Szenario:** Port 465, „STARTTLS verwenden“ aus (UI-Hinweis „465 = SSL/TLS“). Im WLAN oder per manipuliertem DNS wird `smtp.<provider>` auf einen fremden Host mit beliebigem Zertifikat gelenkt → Python akzeptiert → `server.login` sendet Benutzer und Passwort an den Angreifer.
- **Auswirkung:** Abfluss des SMTP-Passworts; Versand gefälschter Rechnungen/Mahnungen im Namen der Firma.
- **Fix:** `smtplib.SMTP_SSL(…, context=ssl.create_default_context())`; Test mit gemocktem SMTP_SSL, der den verifizierenden Kontext prüft.
- **Aufwand:** S · **Gutachter-Notiz:** Drei Gutachter (Vorab-Verifikation), in der Projekt-venv nachvollzogen.

### F-10 · [HOCH] · Doppelte Rechnungen/Angebote durch Mehrfach-Submit; „Erstellen & versenden“ lässt die Formularseite stehen
- **Wo:** sedo/templates/sedo/invoice_form.html:228-231 (vier Submit-Buttons), sedo/static/sedo/unsaved_guard.js:19, sedo/views.py:939-948 (FileResponse bei `action=send`)
- **Was:** Submit-Buttons werden nicht deaktiviert, kein Idempotenz-Token. Bei `action=send` ohne SMTP antwortet der Server mit einem `.eml`-Download – die Maske bleibt mit allen Eingaben stehen, keine Navigation, keine Erfolgsmeldung.
- **Beleg:** `<button class="btn success" type="submit" name="action" value="send">Erstellen &amp; versenden</button>`; `form.addEventListener('submit', function () { submitting = true; });`; `return FileResponse(open(eml_path, 'rb'), as_attachment=True …)`
- **Szenario:** Nutzer klickt „Erstellen & versenden“ → `.eml` wird heruntergeladen, Maske bleibt. Nutzer denkt „hat nicht geklappt“, klickt erneut → zweite Rechnung RE-…-0002 mit identischem Inhalt, Status „versendet“.
- **Auswirkung:** Inhaltsgleiche Rechnungen mit fortlaufenden Nummern im GoBD-Nummernkreis; Bereinigung nur per Storno; Kunde erhält ggf. zwei Rechnungen.
- **Fix:** (1) Im `submit`-Handler alle Submit-Buttons deaktivieren, geklickten Wert als Hidden-Input mitgeben. (2) `action=send` nach Post/Redirect/Get: Rechnung erzeugen → Redirect auf Detailseite mit Toast und Download-Link (siehe auch F-115).
- **Aufwand:** M · **Gutachter-Notiz:** Beide Gutachter bestätigen; grep nach Idempotenz/Doppelklick-Schutz im Rechnungsformular leer.

### F-11 · [HOCH] · Verschachtelte Formulare in „Offene Posten“: „bezahlt“-Button der ersten Zeile löst die Batch-Mahnung aus
- **Wo:** sedo/templates/sedo/open_items.html:59 (Batch-Form um die ganze Tabelle), :86-88 (innere Formulare je Zeile), :102; sedo/views.py:1222-1241 (`bulk_dunning`, `kind = request.POST.get('kind','auto')`)
- **Was:** HTML erlaubt keine verschachtelten Formulare: der Parser ignoriert das erste innere `<form>`, dessen `</form>` schließt das äußere `batch-form`. Der „bezahlt“-Button der ersten Zeile gehört damit zum Batch-Formular mit Ziel `bulk_dunning`.
- **Beleg:** `<form method="post" action="{% url 'sedo:bulk_dunning' %}" id="batch-form">` … `<form method="post" action="{% url 'sedo:invoice_mark_paid' i.pk %}" class="inline-form">…<button …>bezahlt</button></form>`
- **Szenario:** Keine Checkbox angehakt, Klick auf „bezahlt“ in Zeile 1 → POST an `/offene-posten/batch-mahnung/` ohne `kind` und ohne `invoice_ids` → `bulk_dunning` fällt auf `kind='auto'` und ALLE Mahnkandidaten zurück → für jede überfällige Rechnung die nächste Mahnstufe.
- **Auswirkung:** Ungewollte Mahnschreiben an potenziell alle säumigen Kunden durch einen Fehlklick; Mahndokumente sind nicht rücknehmbar.
- **Fix:** Zeilenaktionen aus dem Batch-Formular herauslösen: Batch-Form nur um die Auswahl-Buttons, Checkboxen per `form="batch-form"` zuordnen, Zeilen-Buttons in eigenen Formularen außerhalb der Tabelle (`<button form="row-form-…">`). Zusätzlich F-66 (leere Auswahl abbrechen).
- **Aufwand:** M · **Gutachter-Notiz:** Skeptiker hat das Markup nachgebaut und das Parser-Verhalten im Browser empirisch bestätigt.

### F-12 · [HOCH] · Kundenimport überschreibt vorhandene Kundendaten mit Leerwerten, wenn Spalten in der Datei fehlen
- **Wo:** sedo/views.py:645-661 (`defaults`-Dict, `update_or_create`), :153-164 (`_pick` liefert '' bei fehlender Spalte)
- **Was:** Für jede Zeile wird ein vollständiges `defaults`-Dict gebaut; fehlt eine Spalte, wird '' in den bestehenden Kunden geschrieben. Keine „nur befüllte Felder“-Logik, keine Vorschau, keine Transaktion.
- **Beleg:** `defaults = {'salutation': _pick(row, 'Anrede'), …, 'email': _pick(row, 'E-Mail', 'Email', 'Mail'), …}` → `Customer.objects.update_or_create(customer_no=customer_no, defaults=defaults)`
- **Szenario:** Import einer Excel-Liste mit nur „Kundennummer“ und „Name“ → bei allen Bestandskunden werden E-Mail, Telefon, Straße, PLZ, Ort, Ansprechpartner auf '' gesetzt; nachfolgende Rechnungen tragen leere Adressen.
- **Auswirkung:** Massenverlust von Stammdaten; Rechnungsversand und E-Rechnungs-Validität betroffen.
- **Fix:** Nur Felder in `defaults` aufnehmen, deren Spalte existiert und deren Wert nicht leer ist; Import in `transaction.atomic()`; Vorschau vor dem Schreiben.
- **Aufwand:** S · **Gutachter-Notiz:** Beide Gutachter bestätigen; weitere Importfehler in F-62.

### F-13 · [HOCH] · Rechnungsentwürfe sind Sackgasse: echte Nummer, keine Finalisierung, aber versendbar (mit ENTWURF-Wasserzeichen), mahnbar, stornierbar, im Steuerexport
- **Wo:** sedo/views.py:862 (`status='entwurf'`), :894 (`save_with_unique_number`), :1004-1019 (invoice_email ohne Statusprüfung → 'versendet'); sedo/services/invoices.py:148 (`is_draft` steuert nur das Wasserzeichen, nicht die XML); sedo/services/calc.py:56-57; sedo/urls.py (keine `invoice_update`-Route); month_close views.py:2090
- **Was:** Ein Entwurf erhält beim Speichern eine Nummer aus dem Nummernkreis. Es gibt keine Bearbeitungs-/Finalisieren-View. Andere Views behandeln ihn wie eine echte Rechnung: E-Mail-Versand hängt das ENTWURF-PDF mit gültiger factur-x.xml an und setzt 'versendet'; OP-GET macht ihn 'ueberfaellig' (F-07) und damit Mahnkandidat; Storno erlaubt; Monatsabschluss zählt ihn in `gross_total` und ZIP.
- **Beleg:** `inv.status = 'entwurf' if action == 'draft' else 'erstellt'` … `save_with_unique_number(inv, 'RE')`; `is_draft=invoice.status == 'entwurf'`; `generate_invoice_files(invoice)` (Z. 1004, keine Statusprüfung).
- **Szenario:** Entwurf am 1.8. (Zahlungsziel 14 Tage) vergessen. Am 20.8. öffnet ein Kollege „Offene Posten“ → Entwurf 'ueberfaellig', Batch-Mahnung erzeugt Zahlungserinnerung für eine nie ausgestellte Rechnung. Oder: „per E-Mail senden“ → Kunde erhält PDF mit ENTWURF-Wasserzeichen, dessen XML ARP als reguläre Rechnung importiert.
- **Auswirkung:** Mahnungen/Versand für nicht ausgestellte Rechnungen, widersprüchliche Dokumente beim Kunden, falsche Monatssummen, Nummernkreis enthält dauerhaft Entwürfe.
- **Fix:** Entweder Entwürfe ohne Nummer speichern und eine „Finalisieren“-Aktion (Nummer + Dateien) anbieten – oder 'entwurf' konsequent sperren in `invoice_email`, `invoice_storno`, `invoice_reminder`, `refresh_invoice_payment_status`, `open_items`, `month_close`, `invoice_add_payment`.
- **Aufwand:** L · **Gutachter-Notiz:** Zusammengeführt aus views_a 862 (hoch) und zugferd invoices.py:148 (mittel) – gleiche Ursache „Entwurf nirgends gesperrt“.

### F-14 · [HOCH] · Rechnungen jedes Status – auch Storno-/Korrekturbelege – können „ausgeblendet“ werden und fehlen danach im Steuerberater-Export und in allen Summen
- **Wo:** sedo/views.py:1068-1076 (`invoice_delete`, kein Status-/Typ-Guard), :1177-1183 (`offer_delete`), :2090 (month_close mit `Invoice.objects`); sedo/templates/sedo/invoice_detail.html:19 (Button ohne Bedingung); sedo/services/invoices.py:38-39/:60-61
- **Was:** Versendete oder (teil)bezahlte Rechnungen lassen sich soft-löschen; Nummer bleibt vergeben, Zahlungen hängen an einem unsichtbaren Beleg. Wird ein Storno/Korrektur ausgeblendet, bleibt das Original 'storniert'/'korrigiert' ohne aktiven Folgebeleg – die Sperren blockieren, kein UI-Weg zurück.
- **Beleg:** `invoice = get_object_or_404(Invoice, pk=pk); if request.method == 'POST': … invoice.soft_delete()`; `invoices = Invoice.objects.filter(issue_date__year=year, issue_date__month=month)`
- **Szenario:** RE…0007 versendet und zu 50 % bezahlt; Nutzer klickt „Löschen“ statt „Storno“ → Monatsabschluss-ZIP enthält …0006 und …0008, die Zahlung zeigt auf eine unsichtbare Rechnung. Oder: versehentliches Storno RE…0005 wird ausgeblendet → RE…0001 bleibt 'storniert', kann weder gemahnt noch erneut storniert werden.
- **Auswirkung:** Unvollständiger Buchhaltungsexport, unerklärbare Nummernlücke, verwaiste Zahlungen, blockierte Belegketten.
- **Fix:** Ausblenden nur für 'entwurf' bzw. Belege ohne Versand/Zahlung/Folgebeleg; beim Ausblenden eines Folgebelegs Original zurücksetzen oder verweigern; Button bedingen; Monatsabschluss mit Liste der ausgeblendeten Nummern.
- **Aufwand:** S · **Gutachter-Notiz:** Zusammengeführt aus views_b 1068 (hoch) und gobd 1067 (niedrig); `test_workflow_locks.py:74-79` setzt den Status manuell zurück – ein Hinweis, dass der Fall bekannt, aber nicht gelöst ist.

### F-15 · [HOCH] · Kassenbuch: Bankimport ohne Dublettenschutz/Transaktion und Doppel-Submit bei Neubuchung erzeugen doppelte Buchungen ohne Korrekturmöglichkeit
- **Wo:** sedo/views.py:1448-1456 (`cashbook_import_bank`), :1429-1430 (`cashbook_create`); sedo/urls.py (keine Kassenbuch-Korrektur-Route)
- **Was:** Pro erkannter Bargeldabhebung wird ein `CashbookEntry` angelegt – ohne Prüfung auf bereits importierte Transaktionen (anders als `import_bank_json`, payments.py:88-95) und ohne `transaction.atomic()`. `cashbook_create` vergibt `booking_no` per `next_cashbook_no()` ohne Sperre. Keine View zum Korrigieren/Stornieren.
- **Beleg:** `for t in payload: … CashbookEntry.objects.create(month=month, booking_no=next_cashbook_no(month), …, amount=abs(amount))`
- **Szenario:** Export „01.08.–31.08.“ und später „15.08.–30.09.“ importiert → alle Abhebungen vom 15.–31.08. stehen doppelt im Kassenbuch; Saldo falsch, PDF geht so in die Ablage.
- **Auswirkung:** Falsches Kassenbuch (GoBD-relevant), keine UI-Korrektur, Teilimporte.
- **Fix:** Dublettenprüfung analog `import_bank_json`, Import in `transaction.atomic()`; Korrekturbuchung-View; IntegrityError abfangen (siehe F-92).
- **Aufwand:** M · **Gutachter-Notiz:** Beide Gutachter bestätigen; Kassenbestand/Chronologie separat in F-69.

### F-16 · [HOCH] · GWG-Monatslauf: leere oder unparsbare Preis-/Mengenfelder werden stillschweigend zu 10,00 € bzw. 1,00 – Massenrechnungen mit Phantasiepreis
- **Wo:** sedo/views.py:1588-1594 (`_selected_monthly_services`), :1776-1786 (Verwendung); sedo/templates/sedo/aareon_monthly.html:32-34 (`<input>` ohne type/required)
- **Was:** Leeres Feld → `or '10'`/`or '1'`; unparsbarer Wert („12,50 €“, „2x“) → `except Exception` → 10,00 bzw. 1,00, ohne Meldung.
- **Beleg:** `price = Decimal(str(request.POST.get(f'price_{sid}') or '10')…except Exception: price = Decimal('10.00')`
- **Szenario:** Nutzer löscht beim Winterdienst den Preis, lässt den Haken stehen → 120 GWG-Rechnungen mit Winterdienst 10,00 €; Korrektur nur per 120 Stornos.
- **Auswirkung:** Falsche Beträge in großer Zahl, verbrannte Nummern, falsche E-Rechnungen an Aareon/GWG.
- **Fix:** Bei leerem/ungültigem Preis oder Menge mit `messages.error` abbrechen; Felder als `type=number required`; Vorschau vor dem Lauf.
- **Aufwand:** S · **Gutachter-Notiz:** Per Test reproduziert (`price_Winterdienst='12,50 €'` → 10,00).

### F-17 · [HOCH] · Mapping-Import löscht den gesamten Bestand auch dann, wenn 0 gültige Zeilen gelesen wurden
- **Wo:** sedo/views.py:1609-1612 (nur Spaltenprüfung), :1618-1622 (Zeilen ohne bu/sid werden still übersprungen), :1643-1645 (delete + bulk_create), :1655-1656 (Fehlermeldung erst danach)
- **Was:** Sind die Pflichtspalten vorhanden, aber alle Zeilen leer/ungültig (falsches Tabellenblatt, gefilterter Export), ist `rows` leer; dennoch `AareonMapping.objects.all().delete()` und `bulk_create([])`.
- **Beleg:** `with transaction.atomic(): AareonMapping.objects.all().delete(); AareonMapping.objects.bulk_create(rows.values())` … `else: messages.error(request, '0 Zeilen importiert. …')`
- **Szenario:** Gefilterte/leere Kopie der GWG-Mappingdatei mit korrekter Kopfzeile hochgeladen → alle Mapping-Zeilen gelöscht, Monatslauf blockiert.
- **Auswirkung:** Verlust des Produktiv-Mappings; nur durch erneuten Import einer korrekten Datei behebbar.
- **Fix:** Vor der Transaktion `if not rows: raise ValueError('Keine gültigen Mapping-Zeilen gefunden – Bestand bleibt unverändert.')`; optional Vorschau mit Bestätigung.
- **Aufwand:** S · **Gutachter-Notiz:** Beide Gutachter bestätigen; die bestehenden Tests decken nur den Erfolgspfad ab.

### F-18 · [HOCH] · Archivierte Rechnungsdateien werden bei Monatsabschluss/E-Mail/„Neu erzeugen“ mit aktuellen Stammdaten überschrieben
- **Wo:** sedo/views.py:2103-2104 (month_close: `generate_invoice_files` für jede Rechnung), :1004 (invoice_email), :295 (`_create_invoice_email_file`), :990 (invoice_generate); sedo/services/invoices.py:117 (Mapping live), :162-163 (`recalc_invoice` + save), :194-196 (`os.replace`)
- **Was:** `generate_invoice_files` baut PDF/XML/ZUGFeRD aus dem LIVE-Zustand (Kundenadresse, AareonMapping, AppSettings zur Laufzeit) und überschreibt die Dateien im `generated/`-Ordner – für alle Rechnungen des Monats, auch versendete/bezahlte/stornierte.
- **Beleg:** `for inv in invoices: generate_invoice_files(inv)`; `qs = AareonMapping.objects.filter(billing_unit_sender=object_name)`; `os.replace(tmp_pdf, pdf_path)`
- **Szenario:** Kunde zieht um, Adresse im Stamm geändert, Mapping neu importiert. Nutzer klickt „PDF-Export als E-Mail erstellen“ für einen alten Monat → sämtliche PDFs und Factur-X-XMLs dieses Monats werden mit neuer Adresse und neuem Mapping neu geschrieben; der Steuerberater erhält andere Dokumente als der Kunde damals. Gleiches bei jedem „E-Mail“-Klick in der Rechnungsliste.
- **Auswirkung:** GoBD-Verstoß (Unveränderbarkeit der Belege); Archiv-PDF/XML weicht vom Original ab; zusätzlich sehr langsam.
- **Fix:** Dateien nur einmal beim Erstellen erzeugen; in month_close, invoice_email und `_create_invoice_email_file` nur generieren, wenn die Datei fehlt; „Dateien neu erzeugen“ auf Entwürfe beschränken oder mit Audit-Warnung und Versionierung versehen.
- **Aufwand:** M · **Gutachter-Notiz:** Beide Gutachter bestätigen alle vier Aufrufstellen; Wechselwirkung mit F-39 (hart gelöschte Objekte).

<!-- ab hier ergaenzt aus verifiziert/_args_synthese.json -->


<!-- ---------- MITTEL ---------- -->

### F-19 · [MITTEL] · DEPLOYMENT_SECURITY_NOTES nennt Rollen-/Rechtetrennung als Voraussetzung für Bankanbindung – im Code gibt es keine Rechteprüfung
- **Wo:** DEPLOYMENT_SECURITY_NOTES.md:54
- **Was:** Die Notizen fordern vor der Bankanbindung „Rollen/Rechte für Admin/Buchhaltung/Mitarbeiter getrennt“. Migration 0003 legt drei Gruppen an, aber keine View prüft Gruppen oder Berechtigungen; FinTS-Abruf und Backup-Seite sind für jeden eingeloggten Benutzer erreichbar. Die Bankanbindung ist ausgeliefert, obwohl die eigene Checkliste nicht erfüllt ist.
- **Beleg:** DEPLOYMENT_SECURITY_NOTES.md:54-63 · sedo/migrations/0003_security_groups.py: nur `Group.objects.get_or_create(name=...)` · grep über sedo/views.py nach `has_perm|permission_required|user_passes_test|groups` → 0 Treffer · sedo/views.py:1304-1305 nur unter @login_required
- **Szenario:** Ein zweites Konto 'Mitarbeiter Lesen' wird angelegt. Dieser Nutzer öffnet /zahlungen/ und klickt 'Umsätze abrufen' – mit gespeicherter fints_pin läuft der Abruf durch, alle Kontoumsätze werden angezeigt.
- **Auswirkung:** Dokumentierte Sicherheitsvoraussetzung nicht umgesetzt; Bank- und Umsatzdaten für jeden angemeldeten Nutzer.
- **Fix:** Berechtigungen einführen (`@permission_required` für FinTS/Backup/Einstellungen) oder die Notizen ehrlich anpassen und `bank_ready` als Warnhinweis vor dem Abruf-Button anzeigen.
- **Aufwand:** M · **Dimension:** tests-konfig-deployment · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-20 · [MITTEL] · SEDO.app-Launcher legt die venv mit /usr/bin/python3 (3.9) an – Django 6 benötigt Python ≥ 3.12
- **Wo:** SEDO.app/Contents/MacOS/sedo:43
- **Was:** Der Mac-Desktop-Starter benutzt fest /usr/bin/python3 (Apple-CLT-Python), während START_HIER.command `python3` aus dem PATH nimmt. Auf einem Mac ohne Homebrew/python.org-Python ist /usr/bin/python3 Version 3.9.6, mit der `pip install Django>=5.2` scheitert. logs/setup.log enthält bereits eine Fehlermeldung aus diesem Pfad.
- **Beleg:** SEDO.app/Contents/MacOS/sedo:43: `if ! /usr/bin/python3 -m venv .venv >> logs/setup.log 2>&1; then` · START_HIER.command:7: `python3 -m venv .venv` · `/usr/bin/python3 --version` → Python 3.9.6 · logs/setup.log: „Error: [Errno 1] Operation not permitted“
- **Szenario:** Frischer Bürorechner (nur Xcode-CLT): Doppelklick auf SEDO.app → venv mit 3.9 → pip install schlägt fehl → Dialog 'Paket-Installation fehlgeschlagen'. START_HIER.command verwendet die vorhandene 3.9-venv wieder → gleicher Fehler.
- **Auswirkung:** Ersteinrichtung auf neuen Macs scheitert; verwirrende Fehlersuche.
- **Fix:** Im Launcher denselben Interpreter wie START_HIER verwenden und vor venv-Anlage die Version prüfen (≥ 3.12) mit klarer Meldung.
- **Aufwand:** S · **Dimension:** tests-konfig-deployment · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-21 · [MITTEL] · Einmaliges Admin-Passwort wird vom SEDO.app-Starter dauerhaft im Klartext in logs/setup.log geschrieben
- **Wo:** SEDO.app/Contents/MacOS/sedo:66
- **Was:** Der macOS-Starter fängt die komplette Ausgabe von `ensure_admin` ab und hängt sie an logs/setup.log an. Beim Erststart enthält diese Ausgabe die Zeile „Einmalig angezeigtes Passwort: …“. Der Dialog verspricht „nur dieses eine Mal“, tatsächlich liegt es unbegrenzt in einer 0644-Logdatei im Projektordner.
- **Beleg:** Zeile 65–66: `ADMIN_OUT=$(python manage.py ensure_admin 2>&1)` / `echo "$ADMIN_OUT" >> logs/setup.log`; ensure_admin.py:24: `self.stdout.write(self.style.SUCCESS(f'Einmalig angezeigtes Passwort: {password}'))`
- **Szenario:** Erststart über SEDO.app → Passwort steht in logs/setup.log. Wer später Lesezugriff auf den Projektordner hat (zweiter Mac-Account, Cloud-Sync, weitergegebener Projekt-ZIP), meldet sich als Superuser an.
- **Auswirkung:** Klartext-Zugangsdaten des einzigen Superusers auf der Platte; Zusage „nur einmal angezeigt“ ist falsch.
- **Fix:** Passwortzeile vor dem Loggen filtern bzw. `ensure_admin` um `--password-file` (0600) ergänzen, das der Starter nach dem Dialog löscht. Bestehende setup.log bereinigen. logs/ mit 0700 anlegen.
- **Aufwand:** S · **Dimension:** sicherheit-secrets-banking · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-22 · [MITTEL] · SEDO.vbs startet ab dem zweiten Start ohne migrate/Paketinstallation und ohne Server-Log – Updates führen zu 500ern, Fehler sind nicht nachvollziehbar
- **Wo:** SEDO.vbs:54
- **Was:** Nur wenn `.venv` oder die Datenbankdatei fehlen, ruft SEDO.vbs START_WINDOWS.bat (mit pip install/migrate) auf. Sonst wird `runserver` direkt und unsichtbar gestartet – ohne `migrate`, ohne Abgleich von requirements.txt und ohne Umleitung von stdout/stderr. Der Mac-Starter macht beides bei jedem Start und schreibt logs/server.log.
- **Beleg:** SEDO.vbs:46 `If (Not fso.FolderExists(dir & "\.venv")) Or (Not fso.FileExists(dir & "\data\sedo_web.sqlite3")) Then` … Z.53-54 `Else sh.Run "cmd /c cd /d """ & dir & """ && .venv\Scripts\python.exe manage.py runserver 0.0.0.0:8000", 0, False` – kein migrate, kein pip, kein Log. Vergleich SEDO.app…/sedo:62 `migrate --noinput >> logs/setup.log` und :75 `nohup … >> logs/server.log`.
- **Szenario:** Der Projektordner wird auf dem Windows-Rechner per Kopie aktualisiert (neue Migration, neues Paket). Der Nutzer startet über die Verknüpfung (SEDO.vbs). Der Server startet mit unmigrierter DB bzw. fehlendem Paket: 500er oder Server stirbt sofort; die VBS-Meldung sagt nur „Server nicht gestartet“, kein Log.
- **Auswirkung:** Nach jedem Update arbeitet der Windows-Nutzer mit kaputter App oder muss START_WINDOWS.bat manuell kennen; Ursachenanalyse ohne Log unmöglich.
- **Fix:** Im Else-Zweig vor `runserver` immer `migrate --noinput` ausführen und einen requirements-Hash prüfen; stdout/stderr nach `logs\server.log` umleiten; Fehlermeldung auf die Datei verweisen.
- **Aufwand:** S · **Dimension:** frontend-js-templates_b · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-23 · [MITTEL] · Django-Entwicklungsserver (runserver) auf 0.0.0.0:8000 als Produktiv-/Tunnel-Server in allen Startskripten
- **Wo:** START_HIER.command:30
- **Was:** Alle Endnutzer-Starter (START_HIER.command:30, START_WINDOWS.bat:15, SEDO.vbs:100, SEDO.app/Contents/MacOS/sedo:75) starten die App mit `manage.py runserver 0.0.0.0:8000` – genau diese Instanz wird laut Bedrohungsmodell per Cloudflare-Tunnel ins Internet gestellt. runserver ist Djangos Entwicklungsserver: keine Begrenzung gleichzeitiger Threads, keine Header-/Slowloris-Timeouts, keine Obergrenze für Request-Bodies. Nur desktop.py nutzt waitress – aber ausschließlich auf 127.0.0.1.
- **Beleg:** START_HIER.command:30 `python manage.py runserver 0.0.0.0:8000`; START_WINDOWS.bat:15; SEDO.vbs:100; SEDO.app/Contents/MacOS/sedo:75 `nohup python manage.py runserver 0.0.0.0:8000`; dagegen desktop.py:90 `serve(app=app, host='127.0.0.1', port=port, threads=6, ...)`
- **Szenario:** Tunnel aktiv → ein anonymer Internet-Client öffnet einige tausend Verbindungen und sendet je nur einen halben Request-Header (Slowloris) oder streamt mehrere GB an /kunden/import/ → runserver spawnt pro Verbindung einen Thread ohne Limit, Prozess läuft in Speicher-/Thread-Erschöpfung.
- **Auswirkung:** Denial of Service der Rechnungsstellung von außen ohne Login; fehlende Robustheits-Eigenschaften eines WSGI-Servers (Timeouts, Body-Limits).
- **Fix:** In allen Startern denselben Server wie desktop.py verwenden: `waitress-serve --listen=0.0.0.0:8000 --threads=6 --max-request-body-size=52428800 --channel-timeout=60 config.wsgi:application`. Optional Bindung standardmäßig auf 127.0.0.1 und 0.0.0.0 nur per Umgebungsvariable.
- **Aufwand:** S · **Dimension:** sicherheit-web · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-24 · [MITTEL] · Startskripte führen bei jedem Start `makemigrations sedo` auf Endnutzer-Rechnern aus
- **Wo:** START_WINDOWS.bat:9; START_HIER.command:24
- **Was:** START_WINDOWS.bat (Zeile 9) und START_HIER.command (Zeile 25) rufen `python manage.py makemigrations sedo` vor `migrate` auf. `makemigrations` ist ein Entwicklerwerkzeug: Es erzeugt lokal neue Migrationsdateien, sobald Modelle und Migrationen auseinanderlaufen. SEDO.app und desktop.py machen es richtig (nur `migrate`).
- **Beleg:** START_WINDOWS.bat 9–10: `python manage.py makemigrations sedo` / `python manage.py migrate`; START_HIER.command 25–26: gleiche Zeilen; SEDO.app/Contents/MacOS/sedo: `python manage.py migrate --noinput` (kein makemigrations).
- **Szenario:** Update-Release enthält eine Modelländerung, deren Migration vergessen wurde. Rechner A erzeugt lokal `00xx_auto_….py`. Das nächste Release liefert die offizielle Migration mit anderem Namen → „Conflicting migrations detected“, der Start bricht ab.
- **Auswirkung:** Startabbruch nach Update, divergierende Schemata zwischen den beiden Nutzer-Rechnern, manuelle Reparatur an der Live-DB.
- **Fix:** `makemigrations` aus beiden Startskripten entfernen und nur `python manage.py migrate --noinput` ausführen.
- **Gleiche Ursache (tests-konfig-deployment, START_HIER.command:24):** Startskripte führen `makemigrations sedo` auf dem Endnutzer-Rechner aus – Konfliktgefahr bei Updates – `makemigrations` aus beiden Startskripten entfernen und nur `migrate --noinput` ausführen.
- **Aufwand:** S · **Dimension:** frontend-js-templates_a · **Gutachter-Notiz:** Zusammengeführt aus 2 Rohfunden (frontend-js-templates_a + tests-konfig-deployment); jeder Rohfund von zwei Gutachtern bestätigt.

### F-25 · [MITTEL] · Keine LOGGING-Konfiguration: 'sedo'-Logger (FinTS/SMTP/KI-Fehler) verschwindet im Desktop-Bundle vollständig
- **Wo:** config/settings.py:1
- **Was:** settings.py definiert kein LOGGING; der Logger 'sedo' hat keinen Handler und fällt auf Pythons lastResort-Handler (nur WARNING+ nach stderr). Im PyInstaller-Bundle ist console=False und waitress läuft mit _quiet=True, stderr geht ins Leere. desktop.py legt einen logs/-Ordner an und verweist im Fehlerfall auf 'die Logs', aber nichts schreibt hinein.
- **Beleg:** `grep -n LOGGING config/settings.py` → kein Treffer · sedo/views.py:47 `logger = logging.getLogger('sedo')`, :1319 `logger.exception('FinTS-Abruf fehlgeschlagen')` · sedo/services/banking.py:156, :194 · sedo_desktop.spec:64 `console=False` · desktop.py:90 `_quiet=True`; :68-69 legt 'logs' an; :134 „Details ggf. in den Logs“
- **Szenario:** Im Desktop-Bundle schlägt der Umsatzabruf mit einer Exception fehl. Der Nutzer sieht nur 'Bankabruf fehlgeschlagen'. Der Traceback landet nirgends.
- **Auswirkung:** Fehler bei Bankanbindung, E-Mail-Versand, KI und Monatslauf sind im Produktivbetrieb nicht nachvollziehbar.
- **Fix:** LOGGING-Dict mit RotatingFileHandler nach `SEDO_HOME/logs/sedo.log` (Logger 'sedo' und 'django' auf INFO); in desktop.py sys.excepthook auf denselben Handler.
- **Aufwand:** S · **Dimension:** tests-konfig-deployment · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-26 · [MITTEL] · Sitzungen leben 14 Tage ohne Idle-Timeout – bei defektem Abmelden lässt sich eine Sitzung aus der App heraus nicht beenden
- **Wo:** config/settings.py:203
- **Was:** settings.py setzt nur SESSION_COOKIE_HTTPONLY/SECURE; SESSION_COOKIE_AGE, SESSION_EXPIRE_AT_BROWSER_CLOSE und SESSION_SAVE_EVERY_REQUEST bleiben auf Django-Defaults (14 Tage, False, False). Zusammen mit dem Fund, dass der Abmelden-Link per GET auf LogoutView geht und 405 liefert, existiert kein Weg, eine Sitzung aktiv zu beenden.
- **Beleg:** config/settings.py:194-207 definieren nur `SESSION_COOKIE_SECURE = _env_bool(...)` (Zeile 195) und `SESSION_COOKIE_HTTPONLY = True` (Zeile 203); grep nach `SESSION_COOKIE_AGE|SESSION_EXPIRE_AT_BROWSER_CLOSE|SESSION_SAVE_EVERY_REQUEST` liefert keinen Treffer.
- **Szenario:** Nutzer meldet sich unterwegs über die Tunnel-URL auf einem fremden Laptop an, schließt danach nur den Browser (Abmelden geht wegen 405 nicht). Jede Person mit Zugriff auf das Gerät ist innerhalb der nächsten 14 Tage ohne Passwort eingeloggt – inklusive Einstellungen mit Bank-PIN/SMTP/KI-Key.
- **Auswirkung:** Unbefugter Vollzugriff auf Kunden- und Finanzdaten über wiederhergestellte Sitzungen; kein Nutzer kann eine kompromittierte Sitzung selbst invalidieren.
- **Fix:** `SESSION_COOKIE_AGE = 8*60*60`, `SESSION_EXPIRE_AT_BROWSER_CLOSE = True`, `SESSION_SAVE_EVERY_REQUEST = True`; Abmelden auf POST-Formular umstellen; `clearsessions` im Wochenbackup-Lauf.
- **Aufwand:** S · **Dimension:** sicherheit-web · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-27 · [MITTEL] · Desktop-Bundle: kein Weg, ein verlorenes Admin-Passwort zurückzusetzen – Hinweise verweisen auf nicht vorhandenes manage.py
- **Wo:** desktop.py:183
- **Was:** Das Zufallspasswort wird einmalig per JS-alert gezeigt; im Fehlerfall geht es an print() (im Bundle mit console=False verloren). Die App bietet keine Passwort-Änderungs-/Reset-Route, und Sicherheitsübersicht wie README empfehlen `python manage.py changepassword admin` – im gebündelten Programm gibt es weder manage.py noch einen Python-Interpreter.
- **Beleg:** desktop.py:183-200: `if first_password: … window.evaluate_js(f'alert("{msg}")') except Exception: print('Erststart-Passwort für admin:', first_password)` · sedo_desktop.spec:64 `console=False` · sedo/views.py:2177: „python manage.py changepassword admin“ · sedo/urls.py: keine Passwortänderungs-Route
- **Szenario:** Nutzer installiert per Setup.exe, der Zettel mit dem Passwort geht verloren. Nächster Start: Login unmöglich. Einzige Option: `%APPDATA%\SEDO\data` löschen – womit alle Rechnungen weg sind.
- **Auswirkung:** Aussperrung ohne Wiederherstellungsweg; Anreiz zu Datenlöschung.
- **Fix:** desktop.py um CLI-Modus `--reset-admin-password` ergänzen und/oder PasswordChangeView einbinden; Hinweise an das Bundle anpassen.
- **Aufwand:** M · **Dimension:** tests-konfig-deployment · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-28 · [MITTEL] · requirements.txt komplett ungepinnt, Build-Skripte mit eigener Paketliste, .requirements.hash friert Umgebungen pro Rechner ein
- **Wo:** requirements.txt:1
- **Was:** Alle Pakete außer Django sind ohne Version; requirements_desktop.txt ebenso. build_mac.command und BUILD_WINDOWS.bat wiederholen die Paketliste per Hand statt `-r requirements.txt`. Die Starter installieren nur neu, wenn sich der Hash von requirements.txt ändert – zwei Rechner laufen dauerhaft mit unterschiedlichen Versionsständen. Kommentar in requirements_desktop.txt widerspricht dem Build (faster-whisper wird gebündelt).
- **Beleg:** requirements.txt: `Django>=5.2,<6.1`, `whitenoise`, `fints`, `mt-940`, `pandas`, … (keine Versionen) · build_mac.command:9: `pip install 'Django>=5.2,<6.1' whitenoise pandas openpyxl reportlab pypdf lxml python-dateutil 'qrcode[pil]' fints mt-940 faster-whisper` · START_HIER.command:11-20 (Hash-Vergleich) · requirements_desktop.txt:2-3 vs. sedo_desktop.spec:7
- **Szenario:** Neuinstallation auf dem Windows-Rechner zieht pandas 3.x/fints 5.x, während der Mac auf dem eingefrorenen Stand bleibt. Ein API-Bruch tritt nur auf einem Rechner auf.
- **Auswirkung:** Nicht reproduzierbare Installationen; Fehler, die auf dem Entwicklungsrechner nicht auftreten; Desktop-Bundle kann Pakete vermissen.
- **Fix:** Exakte Versionen pinnen (pip freeze → requirements.txt), Build-Skripte auf `-r requirements.txt` umstellen, Kommentar korrigieren.
- **Aufwand:** S · **Dimension:** tests-konfig-deployment · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-29 · [MITTEL] · Audit-Log übernimmt Client-IP aus X-Forwarded-For (erstes Element) – von jedem Client fälschbar
- **Wo:** sedo/audit.py:7
- **Was:** _client_ip() vertraut dem ersten Eintrag von X-Forwarded-For. Im LAN-Betrieb (runserver/waitress direkt auf 0.0.0.0:8000, kein Proxy) kann jeder Client den Header frei setzen. Auch hinter Cloudflare wird ein clientseitig mitgesendeter XFF-Wert nur ergänzt, nicht ersetzt – das erste Element bleibt angreiferkontrolliert. Die IP ist Bestandteil des GoBD-relevanten Audit-Trails (AuditLog.ip_address, Admin-Filter, Suche in audit_log_page).
- **Beleg:** audit.py:6-10: `forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '') …; if forwarded: return forwarded.split(',')[0].strip(); return request.META.get('REMOTE_ADDR')`.
- **Szenario:** Nutzer löscht eine Zahlung und sendet dabei `X-Forwarded-For: 10.0.0.99` (z. B. per Browser-Extension oder curl) → AuditLog-Eintrag weist die Aktion einem fremden Gerät zu; bei einer späteren Prüfung ist die Herkunft nicht mehr belastbar nachvollziehbar.
- **Auswirkung:** Fälschbarer Herkunftsnachweis im Audit-Log; Erschwerte Forensik nach Kompromittierung.
- **Fix:** Standardmäßig REMOTE_ADDR loggen; XFF nur auswerten, wenn REMOTE_ADDR ein bekannter Proxy ist (Konfigurationsliste), und dann das LETZTE vom Proxy angehängte Element nehmen; hinter Cloudflare CF-Connecting-IP bevorzugen.
- **Aufwand:** S · **Dimension:** sicherheit-web · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-30 · [MITTEL] · Audit-Log: Löschungen/Änderungen an Stammdaten und automatische Statuswechsel fehlen; Fehler beim Schreiben werden verschluckt; Nutzerbezug geht bei User-Löschung verloren
- **Wo:** sedo/audit.py:38
- **Was:** write_audit fängt jede Exception ab und kehrt still zurück. Nicht protokolliert: Kunden anlegen/ändern, Objekte anlegen/ändern/löschen (hart!), Abo-Vorlagen (hart gelöscht), automatische Statuswechsel durch Zahlungsabgleich/Mahnlauf, Datei-Neuerzeugung im Monatsabschluss. invoice_delete/payment_delete schreiben den Eintrag VOR der Aktion. AuditLog.user ist SET_NULL – nach User-Löschung erscheinen Einträge als „System“.
- **Beleg:** audit.py:36-39: `except (OperationalError, ProgrammingError): return` / `except Exception: return`; views.py:670-744 und 1846-1889 ohne write_audit; views.py:741 `obj.delete()`, :1886 `tmpl.delete()`; views.py:1072-1074: write_audit vor soft_delete(); models.py:479: `user = models.ForeignKey('auth.User', on_delete=models.SET_NULL, …)`
- **Szenario:** (1) Mitarbeiter löscht eine Abo-Vorlage – im Audit-Log nichts. (2) Admin löscht einen Benutzer – alle seine Einträge werden zu „System“. (3) soft_delete() scheitert → Log behauptet „gelöscht“, Rechnung ist aktiv.
- **Auswirkung:** Lückenhafte Nachvollziehbarkeit (GoBD verlangt Protokoll inkl. Verursacher); irreführende Einträge; stiller Totalausfall des Protokolls möglich.
- **Fix:** write_audit für alle Create/Update/Delete-Views ergänzen; Audit nach erfolgreicher Aktion bzw. in derselben Transaktion; im except logger.exception; Benutzername redundant als CharField speichern.
- **Aufwand:** M · **Dimension:** datenmodell-migrationen · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-31 · [MITTEL] · „Bereinigte“ Backup-DB enthält Secrets weiterhin als Rest-Bytes (UPDATE ohne VACUUM/secure_delete)
- **Wo:** sedo/backup_utils.py:40
- **Was:** _sanitized_db_copy leert die Werte von ai_api_key/smtp_password/fints_pin per UPDATE in einer Dateikopie. SQLite überschreibt dabei die alten Zellinhalte nicht (secure_delete ist standardmäßig aus, kein VACUUM); die alten Klartextwerte bleiben in Freiblöcken der Seite erhalten und sind mit `strings`/Hex-Editor aus der Backup-ZIP lesbar. BACKUP_INFO.txt (Z. 76–78) und settings.html („aus Backups bereinigt“) versprechen das Gegenteil. Der bestehende Test (test_security_hardening.py:47) prüft nur per SELECT, nicht die Rohbytes.
- **Beleg:** con.execute(f"UPDATE sedo_appsetting SET value='' WHERE key IN ({placeholders})", SECRET_SETTING_KEYS) con.commit() # kein PRAGMA secure_delete, kein VACUUM
- **Szenario:** Synthetischer Nachweis (eigene Test-DB, gleiche Tabellenstruktur, gleiches UPDATE): nach dem UPDATE stand der PIN-Wert noch als Klartext in der Datei; erst nach VACUUM war er weg. Realfall: FinTS-PIN/Claude-Key gespeichert → Wochenbackup → ZIP wird an Steuerberater/USB-Stick/Cloud weitergegeben → Empfänger extrahiert Online-Banking-PIN oder API-Key aus der SQLite-Datei.
- **Auswirkung:** Bank-PIN, SMTP-Passwort und Anthropic-Key können mit jedem weitergegebenen Backup abfließen, obwohl Doku und UI Sicherheit zusichern.
- **Fix:** In _sanitized_db_copy vor dem UPDATE `con.execute('PRAGMA secure_delete=ON')` und nach dem Commit `con.execute('VACUUM')` ausführen; zusätzlich django_session leeren. Test ergänzen, der die Rohbytes der Kopie auf die Secret-Werte prüft.
- **Aufwand:** S · **Dimension:** sicherheit-secrets-banking · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-32 · [MITTEL] · Kein Wiederherstellungsweg: weder Code, Kommando noch Dokumentation; Backup-Inhalt (DB) wird von keinem Test geprüft
- **Wo:** sedo/backup_utils.py:69
- **Was:** Es existiert keine Restore-Funktion, kein Management-Command und keine Anleitung. BACKUP_INFO.txt beschreibt nur das Neu-Eintragen der Keys. README, backup.html, DEPLOYMENT_SECURITY_NOTES enthalten nichts zu 'wiederherstellen'. Der Test zu create_backup räumt ein, dass die DB im Testlauf nie im ZIP liegt.
- **Beleg:** grep -ri 'restore|wiederherstell' → einziger Treffer im Docstring backup_utils.py:29-30 · sedo/tests/test_security_hardening.py:88-90: „In der Testumgebung liegt die DB im Speicher …“ · backup_utils.py:69-80 (BACKUP_INFO ohne Restore-Schritte)
- **Szenario:** Platte fällt aus, Nutzer hat ein SEDO_Backup_*.zip. Im Desktop-Bundle muss er wissen, dass SEDO_HOME `~/Library/Application Support/SEDO` bzw. `%APPDATA%\SEDO` ist, die App beenden, data/ und media/ dorthin entpacken, Keys neu eintragen – nichts davon steht irgendwo.
- **Auswirkung:** Wiederherstellung ist Glückssache; GoBD-relevante Aufbewahrung hängt an einem ungetesteten Pfad.
- **Fix:** Restore-Anleitung in README/BACKUP_INFO.txt plus Management-Command `restore_backup <zip>`; Test mit datei-basierter Test-DB, der prüft, dass die DB im ZIP liegt.
- **Aufwand:** M · **Dimension:** tests-konfig-deployment · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-33 · [MITTEL] · USt-Satz 0 % wird als Kategorie S mit RateApplicablePercent 0.00 ausgegeben (BR-S-05); keine Unterstützung für E/Z/AE (§19, §13b)
- **Wo:** sedo/core/rechnungslauf_core.py:664
- **Was:** CategoryCode ist hart auf 'S' gesetzt und RateApplicablePercent auf inv.vat_rate. Das Formular erlaubt 0.00. EN 16931 verlangt für 'S' einen Satz > 0 (BR-S-05); 0 % ist nur mit Kategorie Z, E (mit Befreiungsgrund) oder AE (Reverse Charge, für Gebäudereinigungsleistungen nach §13b UStG realistisch) zulässig. Bei Stornos mit 0 % entsteht zusätzlich '-0.00' als CalculatedAmount.
- **Beleg:** sub(tax, "ram", "CategoryCode", "S") sub(tax, "ram", "RateApplicablePercent", money(inv.vat_rate)) ... sub(htax, "ram", "CategoryCode", "S") # Z. 762
- **Szenario:** Nutzer trägt bei einer Reverse-Charge- oder steuerfreien Rechnung USt 0 ein → XML mit CategoryCode S / 0.00 % → Schematron-Fehler BR-S-05 beim Empfänger.
- **Auswirkung:** Steuerlich falsche E-Rechnung in einem realistischen Randfall; Validierungsabweisung.
- **Fix:** vat_rate == 0 im Formular sperren oder eine explizite Steuerkategorie am Invoice-Modell einführen (S/Z/E/AE) und in build_xml entsprechend ausgeben; '-0.00' normalisieren.
- **Aufwand:** M · **Dimension:** zugferd-xml-pdf · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-34 · [MITTEL] · IBAN (BT-84) mit Leerzeichen in der XML
- **Wo:** sedo/core/rechnungslauf_core.py:753
- **Was:** SELLER['iban'] ist in Druckschreibweise mit Leerzeichen hinterlegt und wird unverändert in ram:IBANID geschrieben. Die elektronische IBAN nach ISO 13616 ist ohne Trennzeichen; XRechnung prüft dies (BR-DE-19/-20 Pattern), Zahlungsläufe im ERP matchen auf die normalisierte IBAN. Der GiroCode entfernt die Leerzeichen korrekt (Z. 1083), die XML nicht.
- **Beleg:** "iban": "DE60 8105 3272 0641 0486 70", # Z. 62 ... sub(acc, "ram", "IBANID", SELLER["iban"]) # Z. 753 → XML: <ram:IBANID>DE60 8105 3272 0641 0486 70</ram:IBANID>
- **Szenario:** Rechnung an einen Empfänger, der die XML mit XRechnung-/Schematron-Regeln validiert oder die IBAN automatisiert in den Zahllauf übernimmt → Validierungsfehler 'IBAN ungültig' bzw. fehlgeschlagener IBAN-Abgleich.
- **Auswirkung:** Unsaubere Zahlungsdaten in jeder erzeugten E-Rechnung; Risiko verspäteter Zahlungen und Validierungsabweisung.
- **Fix:** In build_xml SELLER['iban'].replace(' ', '') verwenden.
- **Aufwand:** S · **Dimension:** zugferd-xml-pdf · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-35 · [MITTEL] · GiroCode verstößt gegen EPC-QR-Spezifikation (strukturierte UND unstrukturierte Verwendungszweckzeile belegt; negative Beträge bei Korrekturen)
- **Wo:** sedo/core/rechnungslauf_core.py:1084
- **Was:** Die EPC069-12-Payload füllt Zeile 10 (Remittance Information Structured, muss eine ISO-11649-RF-Referenz sein) mit der Rechnungsnummer UND Zeile 11 (Unstructured) mit 'Rechnung …'. Die Spezifikation erlaubt nur eines von beiden. Außerdem wird der GiroCode nur bei TypeCode 457 unterdrückt; eine Korrekturrechnung (384) mit negativem oder Null-Gesamtbetrag erzeugt 'EUR-12.34' bzw. 'EUR0.00'.
- **Beleg:** epc = "\n".join(["BCD", "002", "1", "SCT", SELLER["bic"], SELLER["name"], iban_raw, f"EUR{money(inv.gross)}", "", inv.invoice_no, f"Rechnung {inv.invoice_no}"]) ... if not is_storno: # nur 457 wird ausgenommen
- **Szenario:** Kunde scannt den GiroCode mit einer Banking-App → App meldet 'QR-Code ungültig' oder übernimmt den Verwendungszweck nicht; bei einer Gutschrift-Korrektur enthält das PDF einen QR-Code mit negativem Betrag.
- **Auswirkung:** Zahlungskomfort-Feature funktioniert je nach Bank nicht; Verwendungszweck geht verloren.
- **Fix:** Zeile 10 leer lassen, Zeile 11 mit der Rechnungsnummer füllen; GiroCode nur zeichnen, wenn inv.gross > 0.
- **Aufwand:** S · **Dimension:** zugferd-xml-pdf · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-36 · [MITTEL] · Objekt (Leistungsort) wird nicht gegen den Kunden validiert – fremdes Objekt auf Rechnung/Angebot/Abo möglich
- **Wo:** sedo/forms.py:29
- **Was:** InvoiceForm.obj, OfferForm.obj und RecurringInvoiceTemplateForm.obj bieten alle CustomerObjects an; kein clean() prüft obj.customer == customer. Nur das JS filtert die Dropdown-Liste. _object_from_invoice_form übernimmt obj ungeprüft.
- **Beleg:** forms.py 29-33: `obj = forms.ModelChoiceField(…, queryset=CustomerObject.objects.select_related('customer').all(), required=False, …)`; views.py 235-237: `obj = form.cleaned_data.get('obj')\n if obj:\n return obj`
- **Szenario:** Nutzer wählt Objekt von Kunde A, wechselt den Kunden auf B; nach Validierungsfehler bleibt der alte Wert im POST → Rechnung für B mit Leistungsort von A.
- **Auswirkung:** Falscher Leistungsort/ShipTo auf Beleg.
- **Fix:** clean() in den drei Forms: `if obj and customer and obj.customer_id != customer.pk: raise ValidationError`.
- **Aufwand:** S · **Dimension:** funktionalitaet-views_a · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-37 · [MITTEL] · Wochenbackup läuft nur einmal pro Prozessstart – bei dauerhaft laufendem Server nie wieder
- **Wo:** sedo/middleware.py:39; sedo/middleware.py:42
- **Was:** Das Flag `_checked` wird nach dem ersten Request dauerhaft True; ensure_weekly_backup() wird danach im laufenden Prozess nie wieder aufgerufen. Die README beschreibt, dass der Server nach Schließen des Fensters weiterläuft. Kein Scheduler/Timer.
- **Beleg:** sedo/middleware.py:39-44: `self._checked = False … if not self._checked: self._checked = True; ensure_weekly_backup()` · README.md: „Das Fenster schließen beendet nur die Ansicht – der Server läuft weiter“ · SEDO.app/Contents/MacOS/sedo: `nohup python manage.py runserver …`
- **Szenario:** Server wird Montag gestartet, Rechner bleibt drei Wochen an → in diesen drei Wochen entsteht kein Wochenbackup, obwohl die Oberfläche 'Automatisches Wochenbackup' verspricht.
- **Auswirkung:** Backup-Lücke von Wochen bis Monaten.
- **Fix:** Zeitbasierte Prüfung (z. B. höchstens alle 30 Minuten `last_weekly_backup_at` lesen) oder Timer-Thread in AppConfig.ready()/desktop.py.
- **Gleiche Ursache (sicherheit-secrets-banking, sedo/middleware.py:42):** Wochenbackup läuft nur beim ersten Request je Prozess und schluckt Fehler stumm – Statt `_checked` einen Zeitstempel je Prozess führen (`if now - self._last_check > 1h: ensure_weekly_backup()`); in ensure_weekly_backup Fehler mit `logger.exception` protokollieren und `last_backup_error` als AppSetting setzen.
- **Aufwand:** S · **Dimension:** tests-konfig-deployment · **Gutachter-Notiz:** Zusammengeführt aus 2 Rohfunden (tests-konfig-deployment + sicherheit-secrets-banking); jeder Rohfund von zwei Gutachtern bestätigt.

### F-38 · [MITTEL] · Test-Suite schreibt bei jedem Lauf ~20 Backup-ZIPs in den Live-Ordner backups/
- **Wo:** sedo/middleware.py:41; sedo/backup_utils.py:51
- **Was:** WeeklyBackupMiddleware ruft beim ersten Request jeder Handler-Instanz ensure_weekly_backup() auf. Im Testlauf hat jede TestCase-Klasse mit Client-Requests eine frische Test-DB ohne 'last_weekly_backup_at', deshalb erzeugt praktisch jede Testklasse ein 'weekly_auto'-Backup. backup_dir() zeigt auf SEDO_HOME/backups – den echten Backup-Ordner; nur MEDIA_ROOT ist im Testlauf isoliert.
- **Beleg:** sedo/middleware.py:41-44: `if not self._checked: self._checked = True; ensure_weekly_backup()` · sedo/backup_utils.py:51: `path = Path(getattr(settings, 'SEDO_HOME', settings.BASE_DIR)) / 'backups'` · config/settings.py:175-176: `if 'test' in sys.argv: MEDIA_ROOT = …` (kein Pendant für Backups). Beobachtet: backups/ vor Testlauf 178 Dateien, nach `manage.py test sedo` 199.
- **Szenario:** Entwickler führt `manage.py test sedo` aus → 20+ neue SEDO_Backup_<ts>_weekly_auto.zip landen im echten backups/-Ordner. Nach Wochen liegen hunderte Test-ZIPs zwischen den wenigen echten Sicherungen.
- **Auswirkung:** Live-Verzeichnis wird von Tests beschrieben; echte Wiederherstellungspunkte kaum von Test-Müll zu unterscheiden; Plattenwachstum.
- **Fix:** Im Test-Zweig von settings.py einen isolierten Backup-Pfad setzen und backup_dir() daraus lesen; alternativ Middleware im Test deaktivieren. Rotation einführen.
- **Gleiche Ursache (sicherheit-secrets-banking, sedo/backup_utils.py:51):** Test-Suite schreibt „weekly_auto“-Backups in den echten backups/-Ordner (207 Dateien, keine enthält eine Datenbank) – In settings.py bei `'test' in sys.argv` auch einen Test-Backup-Ordner setzen und backup_dir() darauf lesen lassen; alternativ in Tests `auto_weekly_backup_enabled='0'`. Bestehende Test-ZIPs aufräumen (nach Prüfung von BACKUP_INFO.txt).
- **Aufwand:** S · **Dimension:** tests-konfig-deployment · **Gutachter-Notiz:** Zusammengeführt aus 2 Rohfunden (tests-konfig-deployment + sicherheit-secrets-banking); jeder Rohfund von zwei Gutachtern bestätigt.

### F-39 · [MITTEL] · CustomerObject wird hart gelöscht (SET_NULL auf Rechnungen) – historische Rechnungen verlieren Leistungsort, Monatslauf-Idempotenz kippt
- **Wo:** sedo/models.py:124; sedo/views.py:741
- **Was:** Invoice.obj und RecurringInvoiceTemplate.obj verwenden SET_NULL; object_delete führt ein hartes obj.delete() ohne Prüfung auf verknüpfte Belege und ohne Audit aus. XML/PDF-Erzeugung liest die Objektadresse live aus dem FK, das ARS/GWG-Mapping wird über obj.name aufgelöst. Der GWG-Idempotenz-Check und der Unique-Index hängen ebenfalls am obj.
- **Beleg:** models.py:124: `obj = models.ForeignKey(CustomerObject, on_delete=models.SET_NULL, null=True, blank=True, related_name='invoices')`; views.py:741: `obj.delete()`; invoices.py:109: `object_name = obj.name if obj else ''`; views.py:1738: `Invoice.objects.filter(customer=customer, obj__name=unit_name, source='GWG Monatsrechnung', …)`; views.py:1757: `CustomerObject.objects.get_or_create(customer=customer, name=unit_name, …)`; views.py:2104: `generate_invoice_files(inv)` für alle Rechnungen des Monats
- **Szenario:** Nutzer löscht ein Objekt, zu dem versandte GWG-Monatsrechnungen existieren. (1) „Dateien neu erzeugen“ regeneriert diese Rechnungen: ShipToTradeParty/Objektadresse leer, WE/AE/NkSl leer → XML weicht vom Original ab. (2) Erneuter GWG-Lauf: der Check über obj__name findet nichts, get_or_create legt ein neues Objekt an, der Unique-Index greift nicht → zweite Monatsrechnung für denselben Zeitraum.
- **Auswirkung:** Abweichende E-Rechnungs-XML zu versandten Belegen, doppelte Rechnungen an Wohnungswirtschaft, keine Protokollspur.
- **Fix:** on_delete=PROTECT für Invoice.obj und Offer.obj (ProtectedError abfangen), alternativ Soft-Delete/is_active für CustomerObject; object_* mit write_audit versehen; GWG-Dedupe-Check zusätzlich auf source+period ohne obj.
- **Gleiche Ursache (funktionalitaet-views_b, sedo/views.py:741):** Hartes Löschen von Objekten trennt Rechnungen vom Leistungsort und hebelt die GWG-Dublettenprüfung aus – Objekte mit verknüpften Belegen nicht löschen (PROTECT) oder Soft-Delete.
- **Aufwand:** M · **Dimension:** datenmodell-migrationen · **Gutachter-Notiz:** Zusammengeführt aus 2 Rohfunden (datenmodell-migrationen + funktionalitaet-views_b); jeder Rohfund von zwei Gutachtern bestätigt.

### F-40 · [MITTEL] · Idempotenz-Constraint deckt nur 'GWG Monatsrechnung' ab; 'Abo Monatsrechnung' und Objekte ohne Zuordnung bleiben ungeschützt (source ist freier String)
- **Wo:** sedo/models.py:149
- **Was:** Der Unique-Constraint hängt am Literal source='GWG Monatsrechnung'. Der Abo-Monatslauf schreibt source='Abo Monatsrechnung' und schützt sich nur per Check-then-act mit optionaler Checkbox. obj ist bei Abo-Vorlagen nullable; SQLite behandelt NULL in UNIQUE als verschieden. `source` ist ein freies CharField ohne choices – die Steuerlogik hängt an exakten Zeichenketten.
- **Beleg:** models.py:149: `condition=(models.Q(source='GWG Monatsrechnung') & models.Q(deleted_at__isnull=True) & ~models.Q(status='storniert'))`; models.py:138: `source = models.CharField('Quelle', max_length=80, blank=True)`; views.py:1910: `source='Abo Monatsrechnung'`; views.py:1947: Check-then-act mit skip_existing; views.py:1904: `obj=tmpl.obj` (nullable)
- **Szenario:** Abo-Monatslauf wird zweimal ausgelöst (Doppelklick, zwei Tabs): beide Requests passieren den exists()-Check, es entstehen zwei nummerierte Rechnungen mit identischem Zeitraum; die zweite muss storniert werden.
- **Auswirkung:** Doppelte Rechnungen an Monatskunden, verbrauchte Nummern und Stornos ohne fachlichen Anlass.
- **Fix:** source auf TextChoices umstellen und Constraint auf `source__in=[GWG, ABO]` erweitern; für NULL-Objekte zweiten Constraint oder obj verpflichtend; IntegrityError im Abo-Lauf als „übersprungen“ behandeln.
- **Aufwand:** S · **Dimension:** datenmodell-migrationen · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-41 · [MITTEL] · Selbstlernender Katalog persistiert KI-Ausgaben ohne Bestätigung und injiziert sie in jeden künftigen System-Prompt (PII-Verstärkung + persistente Prompt-Injection)
- **Wo:** sedo/services/ai.py:412
- **Was:** `generate_offer_from_text` ruft direkt nach der Normalisierung `apply_and_learn_service_catalog` auf (ai.py:474) – BEVOR der Nutzer die Positionen sieht oder speichert. Für jede Position mit `katalog_neu`-Name wird ein ServiceCatalogItem mit dem KI-generierten `leistungstext` angelegt (ai.py:412–417). `_service_catalog_prompt_block` (ai.py:213–227) hängt Name + long_text aller Katalogeinträge ohne Trennzeichen oder Kennzeichnung als Daten an den System-Prompt der Angebotsgenerierung (ai.py:432) UND des KI-Plausibilitätschecks (ai.py:637). Folgen: (a) Objekt-/Personenbezug im katalog_name landet dauerhaft in der DB und wird bei jedem weiteren KI-Aufruf erneut gesendet. (b) Instruktionsartiger Text in Katalogeinträgen wirkt als System-Prompt-Anweisung. (c) Kein Bestätigungsschritt.
- **Beleg:** ai.py:472–474: `raw = _ai_generate_via_tool_use(text, cfg); result = normalize_ai_positions(raw); result['katalog_info'] = apply_and_learn_service_catalog(result['positionen'])` · ai.py:412–417: `ServiceCatalogItem.objects.create(name=name[:120], long_text=pos['leistungstext'], ..., created_by_ai=True)` · ai.py:432: `'system': _AI_SYSTEM_PROMPT_BASE + _service_catalog_prompt_block()`
- **Szenario:** Diktat „Fensterreinigung Bürohaus Schmidt GmbH, Lindenallee 8, alle 2 Wochen“. Claude liefert katalog_neu=true mit katalog_name „Fensterreinigung Bürohaus Lindenallee“. Der Eintrag wird sofort gespeichert; der Nutzer bricht das Angebot ab. Jede spätere Angebotsgenerierung überträgt Firma/Adresse erneut im System-Prompt.
- **Auswirkung:** Einmaliger Datenschutzfehler wird dauerhaft und multipliziert sich auf alle künftigen KI-Aufrufe; Katalog wird ohne Freigabe verschmutzt; manipulierbare Vorschläge über persistierte Prompt-Inhalte.
- **Fix:** Katalog-Lernen vom Generierungsaufruf entkoppeln (erst beim Speichern des Angebots); Katalogblock im Prompt als reine Daten kapseln; PII-Filter vor dem Persistieren.
- **Aufwand:** M · **Dimension:** datenschutz-ki · **Gutachter-Notiz:** Strittig, vom Hauptreviewer entschieden: Selbstlernender Katalog ist gewollt, aber das Lernen VOR Nutzerbestätigung macht einen einmaligen KI-Ausrutscher (Objekt-/Personenbezug) dauerhaft und schickt ihn in jeden Folge-Prompt. Fund #9 (gleiche Stellen) wird hier zusammengeführt; Prompt-Injection-Aspekt (#12) als Hygiene-Hinweis aufgenommen

### F-42 · [MITTEL] · Freitext (Diktat, Leistungstexte, Positionsbezeichnungen, Nutzerhinweis) geht ohne jede Redaktion an Anthropic – Datenschutzregel nur als Prompt-Anweisung umgesetzt
- **Wo:** sedo/services/ai.py:433; sedo/static/sedo/offer_ai.js:237
- **Was:** Alle vier Cloud-Aufrufe übertragen Freitext 1:1 in `messages`. Die Regel ist ausschließlich als Instruktion an Claude formuliert. Es existiert keine Redaktions-/Maskierungsfunktion (grep nach redact/anonym/maskier/pseudonym: 0 Treffer) und kein Abgleich gegen Customer-/CustomerObject-Namen. Betroffen: ai.py:433 Diktat; ai.py:568 polish_service_text; ai.py:630 ai_review_positions; views.py:471 _invoice_ai_context; ai.py:682 Begleittext-Hinweis.
- **Beleg:** ai.py:433: `'messages': [{'role': 'user', 'content': f'Ansage:\n{text}'}]` · ai.py:198-199: `'7. Datenschutz: Ignoriere Namen, Adressen und Telefonnummern aus dem Diktat vollständig …'` · ai.py:630: `lines.append(f'{idx}. {desc} – {menge} {einheit} × {preis} €')` · views.py:471: `lines.extend(f'- {item.description}' for item in items)` · ai.py:682: `content += f'\n\nHinweis des Nutzers: {hint}'`
- **Szenario:** Nutzer diktiert „Monatliche Treppenhausreinigung Musterstraße 12 für die Hausverwaltung Schulze, Ansprechpartner Herr Meier, 0391 123456, 450 Euro“ → der vollständige String steht im Request-Body an api.anthropic.com.
- **Auswirkung:** Personen-/Kunden-/Objektdaten gelangen an einen Cloud-Verarbeiter, obwohl die Projektregel dies ausschließt und die UI das Gegenteil zusichert. Die Testabdeckung prüft nur statische Kontextzeilen.
- **Fix:** Serverseitige `redact_for_ai(text)` vor JEDEM Payload: Regex (Telefon, E-Mail, PLZ+Ort, Straße+Hausnummer) + Abgleich gegen Customer/CustomerObject aus der DB → Platzhalter; Ergebnis dem Frontend zurückgeben; Unit-Tests auf den tatsächlichen Payload.
- **Gleiche Ursache (datenschutz-ki, sedo/static/sedo/offer_ai.js:237):** Freitext (Diktat, Leistungstext, Begleittext-Hinweis) geht ungefiltert an die Cloud – Whisper-Transkript wird sogar automatisch ohne Nutzerprüfung gesendet – 1) Auto-Senden nach Transkription entfernen: Generierung erst nach explizitem Klick. 2) Serverseitigen Vorfilter vor JEDEM KI-Aufruf: Regex-Redaktion von Telefonnummern, E-Mail-Adressen, Straße+Hausnummer, PLZ+Ort und Treffern aus Customer/CustomerObject der eigenen DB; dem Nutzer den redigierten Text anzeigen. 3) UI-Hinweistexte korrigieren.
- **Aufwand:** M · **Dimension:** datenschutz-ki · **Gutachter-Notiz:** Zusammengeführt aus 2 Rohfunden (datenschutz-ki + datenschutz-ki); jeder Rohfund von zwei Gutachtern bestätigt.

### F-43 · [MITTEL] · FinTS-Abruf schlägt ohne DK-Produktkennung immer fehl (python-fints ≥4 verlangt product_id), UI nennt sie „optional“
- **Wo:** sedo/services/banking.py:35
- **Was:** Der Kommentar `DEFAULT_PRODUCT_ID = None # None → python-fints-Standard` ist falsch: FinTS3Client.__init__ der installierten Version 5.0.0 wirft `TypeError("The product_id keyword argument is mandatory…")`, wenn product_id leer ist. banking.py übergibt product_id nur bei gesetztem AppSetting (Z. 145–147); der TypeError wird als „Bankabruf fehlgeschlagen: …“ durchgereicht. settings.html:83 zeigt das Feld mit Platzhalter „optional“.
- **Beleg:** kwargs = {} if cfg['product_id']: kwargs['product_id'] = cfg['product_id'] client = FinTS3PinTanClient(cfg['blz'], cfg['login'], use_pin, cfg['endpoint'], **kwargs) # fints/client.py:179: if not product_id: raise TypeError(...)
- **Szenario:** Nutzer lässt „DK-Produktkennung (optional)“ leer, gibt PIN ein, klickt „Umsätze abrufen“ → sofortiger Fehler mit englischer Bibliotheksmeldung, keine Verbindung zur Bank.
- **Auswirkung:** Kontoanbindung ist in der Standardkonfiguration nicht nutzbar; Nutzer sucht den Fehler bei PIN/Bank.
- **Fix:** Produktkennung als Pflichtfeld behandeln (banking_is_configured erweitern, klare deutsche Meldung), Kommentar und Platzhalter korrigieren; Test ohne Konstruktor-Mock ergänzen.
- **Aufwand:** S · **Dimension:** sicherheit-secrets-banking · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-44 · [MITTEL] · pushTAN-Warteschleife schluckt 90 s lang jede Exception (30 Bank-Anfragen) und blockiert den Request-Thread – echte Fehler erscheinen als „Freigabe nicht angekommen“
- **Wo:** sedo/services/banking.py:107
- **Was:** `_await_decoupled` pollt alle 3 s bis zu 90 s lang `client.send_tan(tan_request, None)`. Jede Exception – auch FinTSClientPINError, FinTSDialogError, Netzwerkfehler, Konto gesperrt – wird gespeichert und die Schleife läuft weiter. Erst nach Ablauf kommt eine Meldung, die suggeriert, der Nutzer habe nur die Freigabe nicht bestätigt. Die Schleife läuft synchron im Request-Thread.
- **Beleg:** Zeile 103–109: `while time.monotonic() < deadline: time.sleep(DECOUPLED_POLL_INTERVAL) try: result = client.send_tan(tan_request, None) except Exception as exc: last_exc = exc; continue`; Zeile 112–115: `raise TanConfirmationPending(…) from last_exc`.
- **Szenario:** Bank beendet den Dialog mit 9xxx → python-fints wirft FinTSDialogError → SEDO fragt 29-mal weiter gegen einen toten Dialog → nach 90 s: „Bitte die Freigabe bestätigen und erneut starten“ → Nutzer startet erneut, gleiche Schleife.
- **Auswirkung:** Irreführende Fehldiagnose, 90 s Blockade pro Versuch, unnötige Anfragen an die Bank.
- **Fix:** In der Schleife nur den „noch nicht freigegeben“-Zustand tolerieren (Code 3956, ohne Exception); jede Exception sofort als BankingError durchreichen. Wartezeit per Polling-Endpunkt/JS statt im Request-Thread.
- **Aufwand:** S · **Dimension:** sicherheit-secrets-banking · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-45 · [MITTEL] · TAN-Verfahren wird nicht ausgewählt – Bibliothek nimmt das erste der Bank, pushTAN ggf. nie erreicht
- **Wo:** sedo/services/banking.py:150
- **Was:** fetch_bank_transactions ruft weder get_tan_mechanisms() noch set_tan_mechanism()/set_tan_medium() auf. python-fints wählt bei Antwortcode 3920 das erste unterstützte Zwei-Schritt-Verfahren aus der Bankliste. Ist das kein entkoppeltes Verfahren, bricht banking.py mit „Dieses TAN-Verfahren wird noch nicht unterstützt“ ab, obwohl pushTAN verfügbar wäre.
- **Beleg:** client = FinTS3PinTanClient(cfg['blz'], cfg['login'], use_pin, cfg['endpoint'], **kwargs) with client: init = getattr(client, 'init_tan_response', None) if _is_tan_request(init): if getattr(init, 'decoupled', False): … else: raise BankingError('Die Bank verlangt eine TAN-Eingabe …')
- **Szenario:** Sparkasse meldet Verfahren in der Reihenfolge [chipTAN 912, pushTAN 922]; Bibliothek wählt 912 → nicht decoupled → Abbruch, obwohl der Nutzer pushTAN aktiv hat.
- **Auswirkung:** Kontoabruf bei realistischen Bankkonfigurationen nicht möglich.
- **Fix:** Nach Verbindungsaufbau `client.get_tan_mechanisms()` auslesen, bevorzugt ein decoupled-Verfahren wählen (`set_tan_mechanism`), ggf. `set_tan_medium()`; gewähltes Verfahren als AppSetting speichern.
- **Aufwand:** M · **Dimension:** sicherheit-secrets-banking · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-46 · [MITTEL] · Kein FinTS-Client-State (deconstruct/from_data) – jeder Abruf ist eine neue Geräteregistrierung mit TAN-Pflicht
- **Wo:** sedo/services/banking.py:150
- **Was:** python-fints sieht vor, System-ID, BPD und UPD zwischen Sitzungen zu persistieren (`client.deconstruct()` / `from_data=`). SEDO übergibt weder `from_data` noch `system_id`; der Client fordert bei jedem Abruf per HKSYN eine NEUE System-ID an. Für die Bank ist jeder Abruf ein neues Endgerät: Push-TAN-Freigabe bei jedem Abruf, PSD2-90-Tage-Ausnahme greift nie.
- **Beleg:** banking.py:150–151: `client = FinTS3PinTanClient(…)` – kein `from_data`, kein `deconstruct()`; fints/client.py:178: `self.system_id = system_id or SYSTEM_ID_UNASSIGNED`; client.py:1304–1316: `_ensure_system_id` → `HKSYN3(SynchronizationMode.NEW_SYSTEM_ID)`.
- **Szenario:** Buchhaltung ruft täglich Umsätze ab → täglich pushTAN-Freigabe nötig, täglich neuer Eintrag „neues Gerät“ bei der Bank; nach Wochen verweigert die Bank weitere Synchronisationen.
- **Auswirkung:** Unnötige TAN-Freigaben bei jedem Abruf, mögliche Ablehnung durch die Bank.
- **Fix:** Nach erfolgreichem Abruf `client.deconstruct(including_private=True)` base64-kodiert in `AppSetting('fints_client_state')` speichern, beim nächsten Aufruf `from_data=` übergeben; Blob in SECRET_SETTING_KEYS aufnehmen.
- **Aufwand:** M · **Dimension:** sicherheit-secrets-banking · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-47 · [MITTEL] · Stornorechnung übernimmt Leistungszeitraum nicht – XML ohne BillingSpecifiedPeriod, Lieferdatum = Stornodatum
- **Wo:** sedo/services/invoices.py:231
- **Was:** _create_storno_inner kopiert customer, obj, vat_rate und Positionen, aber nicht period_start/period_end. Für die Storno-XML entfällt BillingSpecifiedPeriod, und das Lieferdatum fällt auf das Stornodatum zurück; das PDF zeigt keinen Leistungszeitraum. Zeitpunkt der Leistung ist Pflichtangabe nach § 14 UStG; bei GWG-Monatsrechnungen ist der Zeitraum das zentrale Zuordnungsmerkmal.
- **Beleg:** invoices.py:231-242: `storno = create_numbered_invoice(invoice_type='457', customer=original.customer, obj=original.obj, issue_date=timezone.localdate(), due_date=timezone.localdate(), vat_rate=original.vat_rate, status='erstellt', source='Storno', reference_invoice=original, note=…)` (kein period_start/period_end) — rechnungslauf_core.py:739: `delivery_date = period_start if period_start is not None else issue_date`
- **Szenario:** GWG-Monatsrechnung August wird am 15.09. storniert → Storno-XML meldet Lieferdatum 15.09. und keinen Abrechnungszeitraum; Aareon kann den Storno keinem Abrechnungsmonat zuordnen.
- **Auswirkung:** Unvollständige Pflichtangaben auf Stornobelegen, Zuordnungsprobleme beim Empfänger.
- **Fix:** period_start/period_end im Storno übernehmen (für Korrekturrechnungen bereits vorhanden, views.py:826-827).
- **Aufwand:** S · **Dimension:** gobd-geschaeftslogik · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-48 · [MITTEL] · Unparsebare Buchungsdaten fallen stumm auf „heute“ zurück – falsches Zahlungsdatum und ausgehebelte Dublettenerkennung
- **Wo:** sedo/services/payments.py:21
- **Was:** parse_bank_date und parse_date_for_view akzeptieren nur drei Formate und liefern bei jedem anderen Wert kommentarlos timezone.localdate(). Die Zahlung wird mit falschem Buchungsdatum gespeichert; ohne external_id greift die Dubletten-Heuristik (Datum+Betrag+Name+Zweck) beim nächsten Import nicht mehr.
- **Beleg:** payments.py:26-31: `for fmt in _BANK_DATE_FORMATS: try: return datetime.strptime(…) except ValueError: continue` … `return timezone.localdate()` — payments.py:92-94: `elif Payment.all_objects.filter(booking_date=booking_date, amount=amount, payer_name=name, purpose=purpose).exists(): ignored += 1` — views.py:1470-1475
- **Szenario:** Bank-Export mit „31.08.2025 14:02“. Import am 03.09. → alle Zahlungen tragen booking_date 03.09. Derselbe Export am 04.09. erneut → keine Dublette erkannt → alle Zahlungen doppelt, Rechnungen überzahlt.
- **Auswirkung:** Falsche Buchungsdaten in GoBD-relevanten Zahlungsdatensätzen, Doppelimporte, falsche Zahlstatus.
- **Fix:** Bei nicht parsebarem Datum den Datensatz als „ignored“ auflisten; Formate mit Uhrzeit/ISO ergänzen; Dublettenschlüssel auf Rohdaten-Hash stützen.
- **Aufwand:** S · **Dimension:** gobd-geschaeftslogik · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-49 · [MITTEL] · Sammelüberweisung mit mehreren Rechnungsnummern wird komplett der neuesten Rechnung zugeordnet – übrige Rechnungen bleiben offen und werden gemahnt
- **Wo:** sedo/services/payments.py:41
- **Was:** _match_invoice extrahiert alle RE-Nummern aus dem Verwendungszweck, holt aber mit .order_by('-issue_date').first() genau EINE Rechnung und bucht den gesamten Zahlbetrag darauf. Keine Betragsprüfung, keine Aufteilung, keine Warnung bei mehreren Treffern. Wohnungsgesellschaften zahlen typischerweise mehrere Monatsrechnungen in einer Überweisung.
- **Beleg:** payments.py:41-49: `tokens = set(_INVOICE_NO_PATTERN.findall((purpose or '').upper()))` … `invoice = (Invoice.objects.exclude(status='storniert').filter(number__in=tokens).order_by('-issue_date').first())` — payments.py:97-105: `payment = Payment.objects.create(invoice=invoice, … amount=amount, … matched_automatically=bool(invoice))` — calc.py:50: `if paid >= invoice.gross_total …: invoice.status = 'bezahlt'`
- **Szenario:** Gutschrift über 357,00 € mit Verwendungszweck „RE2025080001 RE2025080002 RE2025080003“ (3 × 119 €). Zahlung wird RE2025080003 zugeordnet → 'bezahlt', open_amount −238 €. Die anderen beiden bleiben offen und werden gemahnt, obwohl der Kunde vollständig bezahlt hat.
- **Auswirkung:** Falsche offene Posten, unberechtigte Mahnungen an Großkunden, Überzahlung stumm akzeptiert; Zahlungsnachweis je Rechnung falsch.
- **Fix:** Bei mehr als einem Treffer die Zahlung als „unklar“ markieren und Aufteilung anbieten; alternativ automatisch aufteilen, wenn Summe exakt passt; bei Überzahlung warnen.
- **Aufwand:** M · **Dimension:** gobd-geschaeftslogik · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-50 · [MITTEL] · Transkript wird sofort automatisch an Claude gesendet – kein Prüfschritt trotz Label „prüfen und bei Bedarf korrigieren“
- **Wo:** sedo/static/sedo/offer_ai.js:237
- **Was:** Nach der Whisper-Transkription (und nach Web-Speech-Stopp) ruft das Frontend ohne Nutzerinteraktion `runGeneration()` auf. Der Nutzer hat keine Gelegenheit, versehentlich diktierte Namen/Adressen zu entfernen, obwohl die Oberfläche genau das nahelegt.
- **Beleg:** offer_ai.js:233-237: `... transcript.value = base ? base + '\n' + clean : clean; runGeneration();` · offer_ai.js:315-317: `if (text.length >= 5) runGeneration();` · offer_form.html:117: `<label for="ai-transcript">Ansage / Text (prüfen und bei Bedarf korrigieren)</label>`
- **Szenario:** Nutzer diktiert versehentlich „…für Frau Lehmann in der Bahnhofstraße“ und will den Namen löschen – der Text ist bereits gesendet.
- **Auswirkung:** Die einzige menschliche Kontrollinstanz vor der Cloud-Übertragung wird übersprungen.
- **Fix:** Nach Transkription nur das Textfeld befüllen und `runGeneration()` in beiden Pfaden entfernen; der Button `ai-extract-btn` übernimmt das Senden.
- **Aufwand:** S · **Dimension:** datenschutz-ki · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-51 · [MITTEL] · Web-Speech-Fallback schickt Diktat-Audio an Google – UI behauptet „Audio verlässt den Rechner nicht“
- **Wo:** sedo/static/sedo/offer_ai.js:243; sedo/static/sedo/offer_ai.js:253
- **Was:** Wenn lokales Whisper nicht verfügbar ist (faster-whisper nicht installiert ODER Modell-Download schlägt fehl → TranscriptionUnavailable → HTTP 503 'stt_unavailable'), schaltet das Frontend automatisch auf die Browser-Spracherkennung um. Diese streamt Audio an Google-Server. Template, Docstrings und Kommentare behaupten das Gegenteil.
- **Beleg:** offer_ai.js:243-246: `if (err && err.code === 'stt_unavailable') { whisperAvailable = false; notify('Lokales Whisper ist nicht installiert – ab jetzt wird die Browser-Spracherkennung genutzt.', 'warning'); }` · transcription.py:77-80: jede Exception beim Modell-Laden → TranscriptionUnavailable → views.py:403-404 → 503.
- **Szenario:** Windows-Desktop-App, erster Start ohne Internet: WhisperModel('small') kann das Modell nicht laden → 503 → JS setzt whisperAvailable=false. Der Nutzer diktiert Personendaten → Audio geht an Google, Tooltip sagt „Audio verlässt den Rechner nicht“.
- **Auswirkung:** Verstoß gegen die Datenschutzregel; falsche Datenschutzinformation gegenüber den Nutzern.
- **Fix:** Web-Speech-Fallback entfernen oder nur als Opt-in mit Warnung; Texte in offer_form.html:106, ai.py:7-8, offer_ai.js:8-12 korrigieren.
- **Gleiche Ursache (datenschutz-ki, sedo/static/sedo/offer_ai.js:253):** Browser-Spracherkennung als Fallback schickt Audio an Google – UI behauptet „das Audio verlässt den Rechner nicht“ – Web-Speech-Fallback entfernen oder nur nach explizitem Opt-in mit Warntext aktivieren; ohne Whisper den Diktat-Button deaktivieren. Template-Text an den tatsächlichen Zustand koppeln.
- **Aufwand:** S · **Dimension:** datenschutz-ki · **Gutachter-Notiz:** Zusammengeführt aus 2 Rohfunden (datenschutz-ki + datenschutz-ki); jeder Rohfund von zwei Gutachtern bestätigt.

### F-52 · [MITTEL] · Doppelklick auf „Diktat starten“ während der Mikrofon-Freigabe startet zwei MediaRecorder – erster Stream wird nie gestoppt, Mikrofon bleibt offen
- **Wo:** sedo/static/sedo/offer_ai.js:334
- **Was:** Der Klick-Handler prüft nur `busy` und `recording`. `recording` wird erst in `markRecording()` gesetzt, das nach Auflösung von `getUserMedia()` läuft (Freigabe-Dialog kann Sekunden dauern). Ein zweiter Klick in dieser Zeit ruft `startWhisperRecording()` erneut auf; `mediaStream`/`mediaRecorder` werden überschrieben, der erste Recorder und Stream bleiben aktiv und sind für Stop nicht mehr erreichbar.
- **Beleg:** offer_ai.js:332-337 `recordBtn.addEventListener('click', function () { if (busy) return; if (!recording) { if (whisperAvailable) startWhisperRecording(); else startWebSpeechRecording(); return; }` · Z.196-212 `navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) { mediaStream = stream; ... mediaRecorder = ...; mediaRecorder.start(); markRecording();` · Stopp Z.340-344 stoppt nur `mediaRecorder`
- **Szenario:** Nutzer klickt „Diktat starten“, der Browser zeigt den Mikrofon-Freigabedialog; der Nutzer klickt ungeduldig ein zweites Mal. Nach der Freigabe laufen Recorder A und B. „Diktat beenden“ stoppt B; Recorder A nimmt mit offenem Mikrofon weiter auf, bis die Seite verlassen wird.
- **Auswirkung:** Mikrofon bleibt unbemerkt aktiv, obwohl die UI „beendet“ meldet – für ein Tool mit Datenschutzzusage zum Diktat heikel.
- **Fix:** Beim Klick sofort `busy = true`/Button disabled setzen, bis `getUserMedia` aufgelöst ist; vor dem Start prüfen, ob `mediaRecorder && mediaRecorder.state !== 'inactive'`, und alte Tracks stoppen.
- **Aufwand:** S · **Dimension:** frontend-js-templates_b · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-53 · [MITTEL] · „Abmelden“ ist ein GET-Link – Django 6 LogoutView akzeptiert nur POST → 405
- **Wo:** sedo/templates/sedo/base.html:75
- **Was:** Seit Django 5.0 erlaubt `LogoutView` kein GET mehr (`http_method_names = ["post", "options"]`). base.html verlinkt den Logout als normalen Anker. Im Testlauf verifiziert: `LOGOUT GET STATUS: 405`.
- **Beleg:** base.html 75: `<a href="{% url 'logout' %}"><span class="nav-ico">…</span><span class="nav-text">Abmelden</span></a>`; config/urls.py: `path('logout/', auth_views.LogoutView.as_view(), name='logout')`.
- **Szenario:** Nutzer klickt im Menü „Abmelden“ → Browser zeigt „405 Method Not Allowed“, die Session bleibt gültig.
- **Auswirkung:** Abmelden ist unmöglich; im Tunnel-Betrieb und am gemeinsam genutzten Rechner bleibt die Sitzung bis zum Cookie-Ablauf aktiv.
- **Fix:** Logout als POST-Formular rendern: `<form method="post" action="{% url 'logout' %}">{% csrf_token %}<button type="submit">…Abmelden</button></form>`. Test ergänzen.
- **Aufwand:** S · **Dimension:** frontend-js-templates_a · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-54 · [MITTEL] · Primärschlüssel ohne |unlocalize in Query-Strings: ab ID 1000 wird „?customer=1.234“ erzeugt → HTTP 500
- **Wo:** sedo/templates/sedo/customer_detail.html:8
- **Was:** settings.py 176–181 setzt `USE_THOUSAND_SEPARATOR = True` und warnt ausdrücklich, dass IDs in URLs `|unlocalize` brauchen. Trotzdem werden `{{ customer.pk }}`, `{{ c.pk }}` und `{{ offer.pk }}` ungeschützt in Query-Strings ausgegeben. Betroffen: customer_detail.html 8 (3×), 34, 40, 44; customer_list.html 5 (2×); offer_detail.html 2. Im Testlauf mit pk=1234 verifiziert: Link `?customer=1.234`, Aufruf wirft `ValueError: Field 'id' expected a number but got '1.234'.`
- **Beleg:** customer_detail.html 8: `<a class="btn primary" href="{% url 'sedo:invoice_create' %}?customer={{ customer.pk }}">Rechnung</a>`; offer_detail.html 2: `href="{% url 'sedo:invoice_create' %}?from_offer={{ offer.pk }}"`; views.py 787–789: `pre_customer = request.GET.get('customer')` … `get_object_or_404(Customer, pk=pre_customer)`.
- **Szenario:** Der 1000. Kunde wird angelegt. Auf seiner Kundenakte klickt der Nutzer „Rechnung“ → 500-Fehlerseite. Dasselbe beim 1000. Angebot über „Als Rechnung bearbeiten“.
- **Auswirkung:** Kernfunktionen fallen für alle Datensätze ab ID 1000 aus.
- **Fix:** Überall `{{ customer.pk|unlocalize }}` bzw. `{% localize off %}` verwenden (wie bereits in invoice_detail.html 96 und open_items.html 75). Zusätzlich GET-Parameter defensiv parsen. Template-Test mit pk ≥ 1000.
- **Aufwand:** S · **Dimension:** frontend-js-templates_a · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-55 · [MITTEL] · Wizard versteckt Schritte per display:none – Pflichtfelder blockieren das Absenden ohne jede Meldung
- **Wo:** sedo/templates/sedo/invoice_form.html:3
- **Was:** wizard.js schaltet Schritte über `.wizard-ready .wizard-step{display:none}` um. Django rendert Pflichtfelder mit HTML-`required` (Rechnung: `issue_date`, `payment_days`, `vat_rate`); das Formular hat kein `novalidate`. Ist ein solches Feld leer und der Nutzer steht im Schritt „Prüfen“, verweigert der Browser den Submit, kann das unsichtbare Feld aber nicht fokussieren und zeigt keine Meldung. Im Browser verifiziert: `submitFired:false`, Konsole: „An invalid form control with name='issue_date' is not focusable.“
- **Beleg:** invoice_form.html 3: `<form method="post" id="invoice-form">` (kein novalidate); app.css 375: `.wizard-ready .wizard-step{display:none}`; forms.py 31: `payment_days = forms.IntegerField(… min_value=0 …)`; gerendert: `REQUIRED FIELDS INVOICE FORM: ['issue_date', 'payment_days', 'vat_rate']`, `NOVALIDATE PRESENT: False`.
- **Szenario:** Nutzer leert in Schritt 3 versehentlich das Zahlungsziel, geht mit „Weiter“ bis „Prüfen“, klickt „Rechnung erstellen“ → nichts passiert, kein Fehler, kein Sprung zum Feld.
- **Auswirkung:** Blockierter Arbeitsablauf ohne Rückmeldung; verleitet zu wiederholten Klicks.
- **Fix:** `novalidate` am Formular setzen – Django validiert serverseitig und wizard.js springt zum ersten `.errorlist`-Schritt. Alternativ `invalid`-Listener (capture) im Wizard, der zum Schritt des ungültigen Feldes wechselt.
- **Aufwand:** S · **Dimension:** frontend-js-templates_a · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-56 · [MITTEL] · Race bei schneller Kundenauswahl: verspätete API-Antwort überschreibt Kopfdaten und Objektliste des zuletzt gewählten Kunden
- **Wo:** sedo/templates/sedo/invoice_form.html:283
- **Was:** Der `change`-Handler auf `#id_customer` startet pro Auswahl ein `fetch('/api/kunden/<pk>/daten/')` ohne AbortController oder Sequenznummer und schreibt die Antwort ungeprüft in die Felder (identisch offer_form.html). Treffen zwei Antworten in vertauschter Reihenfolge ein (Tunnel-Latenz), zeigen Name/Adresse und das Objekt-Dropdown Kunde A, während `id_customer` auf Kunde B steht. Ein aus der falschen Liste gewähltes Objekt wird ohne Zugehörigkeitsprüfung übernommen.
- **Beleg:** invoice_form.html 296–298: `const resp = await fetch(\`/api/kunden/${pk}/daten/\`); if (!resp.ok) throw new Error('API Fehler'); const d = await resp.json(); fill('id_name', d.name); …`; 322–323: `customerObjectCache = d.objects || []; updateObjectDropdown(customerObjectCache);`
- **Szenario:** Nutzer wählt per Enter Kunde A, merkt den Irrtum und wählt sofort Kunde B. Antwort A kommt 400 ms später als B → Kopfdaten und Objektliste zeigen A. Nutzer wählt „Objekt Musterhaus“ (gehört A) → Rechnung für Kunde B mit Leistungsort von Kunde A.
- **Auswirkung:** Falscher Leistungsort auf einer erzeugten E-Rechnung; selten, aber bei Tunnel-Betrieb realistisch.
- **Fix:** Laufende Anfrage abbrechen (`AbortController`) oder Sequenzzähler und vor dem Befüllen `customerSel.value === pk` prüfen. Serverseitig `obj.customer_id == customer.pk` erzwingen.
- **Aufwand:** S · **Dimension:** frontend-js-templates_a · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-57 · [MITTEL] · Live-Summe im Rechnungs-/Angebotsassistenten rundet per Float anders als der gespeicherte Beleg (Cent-Abweichungen im Prüfen-Schritt)
- **Wo:** sedo/templates/sedo/invoice_form.html:424
- **Was:** Die clientseitige Kalkulation rechnet Zeilenbeträge als JS-Floats (`qty * price`), summiert ungerundet und rundet erst bei der Anzeige mit `toFixed(2)`. Der Server (calc.py:18-23) rundet jede Zeile einzeln mit Decimal ROUND_HALF_UP und summiert dann. Dadurch weichen Zeilen-Gesamt, Netto, USt und Brutto im Schritt „Prüfen“ regelmäßig um 1 Cent vom Beleg ab. Gleiches in offer_form.html:355-364.
- **Beleg:** invoice_form.html:422-424 `const qty = parseFloat((qtyEl ? qtyEl.value : '0').replace(',', '.')) || 0; const price = parseFloat(...) || 0; const line = qty * price;` · Z.431 `net += line;` · Z.435 `const vat = net * vatRate / 100;` · Z.255-259 `fmtMoney(v){... n.toFixed(2)...}`. Server calc.py:18-22 quantize(CENT, ROUND_HALF_UP). Nachgerechnet: 2,5 × 33,33 → JS „83,32 €“, Server 83.33.
- **Szenario:** Nutzer trägt Position 2,5 Std. × 33,33 € ein. Live-Zeile und Prüfen-Schritt zeigen 83,32 € netto; nach „Rechnung erstellen“ steht auf PDF/XML 83,33 €.
- **Auswirkung:** Der Nutzer bestätigt im Prüfen-Schritt einen anderen Betrag als der, der auf Rechnung/ZUGFeRD-XML landet; bei Kundenrückfragen entstehen Cent-Differenzen.
- **Fix:** Zeilenbetrag clientseitig in Integer-Cents rechnen mit kaufmännischer Rundung, jede Zeile erst runden, dann summieren – exakt wie calc._totals.
- **Aufwand:** S · **Dimension:** frontend-js-templates_b · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-58 · [MITTEL] · SMTP-Port als int in `<input type=number value="{{ smtp_port }}">` – ab Port 1000 lokalisiert („2.525“)
- **Wo:** sedo/templates/sedo/settings.html:28
- **Was:** `settings_page` übergibt `smtp_cfg['port']` als int. Mit `USE_THOUSAND_SEPARATOR` wird 2525 im Template zu „2.525“ gerendert. Im Testlauf verifiziert: `name="smtp_port" type="number" value="2.525"`. Beim Speichern kommt „2.525“ zurück; `_smtp_port()` fängt den `ValueError` und fällt still auf 587 zurück.
- **Beleg:** settings.html 28: `<input name="smtp_port" type="number" value="{{ smtp_port }}" placeholder="587">`; mail.py 51–56: `raw = (AppSetting.get('smtp_port', '') or '').strip() … except ValueError: return DEFAULT_SMTP_PORT`; views.py 2001: `AppSetting.set('smtp_port', request.POST.get('smtp_port', '587').strip() or '587')`.
- **Szenario:** Nutzer richtet einen Relay mit Port 2525 ein und speichert → Formular zeigt beim nächsten Öffnen „2.525“; beim Speichern anderer SMTP-Felder wird „2.525“ übernommen → Versand läuft unbemerkt über Port 587 und schlägt fehl.
- **Auswirkung:** Rechnungsversand bricht nach jedem Speichern der Einstellungen ab, Ursache nicht erkennbar. Standardports 25/465/587 nicht betroffen.
- **Fix:** `value="{{ smtp_port|unlocalize }}"` oder den Port im View als String übergeben; beim Speichern serverseitig validieren.
- **Aufwand:** S · **Dimension:** frontend-js-templates_a · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-59 · [MITTEL] · Änderungen an den Empfänger-Kopffeldern werden ignoriert, sobald ein gespeicherter Kunde ausgewählt ist
- **Wo:** sedo/views.py:210
- **Was:** Das Rechnungs-/Angebotsformular füllt Name/Adresse per JS aus dem Kunden und lässt die Felder editierbar. _customer_from_invoice_form gibt bei gesetztem `customer` sofort das Customer-Objekt zurück; die geänderten Kopffelder werden weder auf den Kunden geschrieben noch auf die Rechnung übernommen. Kein Hinweis.
- **Beleg:** views.py 210-213: `customer = form.cleaned_data.get('customer')\n if customer:\n return customer`; invoice_form.html 57-67: editierbare Felder `{{ form.name }} … {{ form.street }}`
- **Szenario:** Nutzer wählt Kunden, korrigiert im Formular die Straße und erzeugt die Rechnung → PDF/XML tragen die alte Adresse aus dem Kundenstamm.
- **Auswirkung:** Falsche Empfängeradresse auf Beleg; stille Verwerfung von Eingaben.
- **Fix:** Kopffelder bei gewähltem Kunden readonly setzen oder Abweichungen erkennen und Fehler ausgeben.
- **Aufwand:** M · **Dimension:** funktionalitaet-views_a · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-60 · [MITTEL] · Stornorechnungen (Typ 457) zählen im Dashboard als offene Posten mit negativem Betrag
- **Wo:** sedo/views.py:600
- **Was:** Das Dashboard filtert nur status in ['bezahlt','storniert'] aus. Eine Stornorechnung hat status 'erstellt', gross_total negativ und open_amount = gross_total → sie reduziert open_amount_total und zählt als offene Rechnung. Erst ein Besuch von open_items setzt sie per refresh auf 'bezahlt'.
- **Beleg:** views.py 600-601: `'open_invoices': invoices.exclude(status__in=['bezahlt','storniert']).count(),\n 'open_amount_total': sum((i.open_amount for i in invoices.exclude(status__in=['bezahlt','storniert'])…`; invoices.py 231-249: Storno mit `quantity=-item.quantity`, `status='erstellt'`
- **Szenario:** Rechnung A 1.190 € offen; Rechnung B 500 € wird storniert. Dashboard zeigt „offen: 690 €“ und 2 offene Rechnungen statt 1.190 € / 1.
- **Auswirkung:** Irreführende Finanzkennzahl auf der Startseite.
- **Fix:** Stornos in Dashboard/open_items/payment_list ausschließen (`.exclude(invoice_type='457')`) oder Storno direkt mit passendem Status anlegen.
- **Aufwand:** S · **Dimension:** funktionalitaet-views_a · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-61 · [MITTEL] · Datei-Uploads ohne Größen-/Typprüfung werden komplett in den Speicher gelesen bzw. an pandas/openpyxl übergeben
- **Wo:** sedo/views.py:629
- **Was:** customer_import liest CSV vollständig per file.read() und übergibt jede Nicht-CSV-Datei ungeprüft an pd.read_excel (kein try/except → beliebige Dateitypen führen zu 500). Bank-JSON (views.py:1323, 1444) und GWG-Mapping (views.py:1679, 1980 → pd.read_excel) haben ebenfalls keine Größen-/Typprüfung; DATA_UPLOAD_MAX_MEMORY_SIZE begrenzt Multipart-Dateien nicht. Nur der Audio-Upload ist auf 25 MB begrenzt (transcription.py:25/93) – allerdings erst nach vollständigem audio.read() in views.py:402. Im LAN-Betrieb steht kein Reverse-Proxy mit Body-Limit vor dem Prozess.
- **Beleg:** views.py:630-638: `file = request.FILES['file']; name = file.name.lower(); if name.endswith('.csv'): decoded = file.read().decode('utf-8-sig').splitlines() … else: df = pd.read_excel(file)`; views.py:1323: `payload = json.loads(request.FILES['file'].read().decode('utf-8-sig'))`; keine Prüfung von file.size an diesen Stellen.
- **Szenario:** Angemeldeter Nutzer lädt unter /kunden/import/ eine 2 GB große .csv oder eine XLSX-Zip-Bombe hoch → Prozess läuft in Speicherüberlast, Rechnungstool für beide Nutzer nicht mehr erreichbar. Upload einer .pdf → pandas-Exception → 500-Seite.
- **Auswirkung:** Dienstausfall des einzigen App-Prozesses; unfreundliche 500er statt Fehlermeldung.
- **Fix:** Einheitliche Upload-Prüfung: file.size ≤ z. B. 10 MB, Whitelist der Endungen/Content-Types (.csv/.xlsx/.json), Verarbeitung in try/except mit messages.error; pandas nur für .xlsx/.xls aufrufen; Audio-Größe vor read() über audio.size prüfen.
- **Aufwand:** S · **Dimension:** sicherheit-web · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-62 · [MITTEL] · Kundenimport: Windows-CSV (cp1252), doppelte Namen/Kundennummern oder Nicht-Excel-Datei führen zu 500 mitten im Import (Teilimport)
- **Wo:** sedo/views.py:633; sedo/views.py:150; sedo/views.py:160
- **Was:** Die Datei wird fest als utf-8-sig dekodiert; Excel-CSV ist unter Windows cp1252 → UnicodeDecodeError. Nicht-Excel-Dateien landen ungeprüft in pd.read_excel. update_or_create(name=…) wirft MultipleObjectsReturned bei doppelten Namen (kein Unique-Constraint). Kein try/except, keine Transaktion – bereits verarbeitete Zeilen bleiben gespeichert.
- **Beleg:** views.py 633: `decoded = file.read().decode('utf-8-sig').splitlines()`; 637: `df = pd.read_excel(file)`; 659/661: `Customer.objects.update_or_create(customer_no=customer_no, defaults=defaults)` / `update_or_create(name=name_value, …)`
- **Szenario:** Zwei Kunden heißen „Hausverwaltung Schmidt“; CSV ohne Kundennummern → Zeile 37 wirft MultipleObjectsReturned → Fehlerseite, Zeilen 1-36 bereits importiert. Oder: CSV aus Excel mit „ü“ in cp1252 → sofort 500.
- **Auswirkung:** Fehlerseite statt Meldung, inkonsistenter Teilimport.
- **Fix:** Dekodierung mit Fallback (utf-8-sig → cp1252), Dateityp prüfen, Import in transaction.atomic() mit try/except und Zeilenmeldung; bei Mehrdeutigkeit Zeile überspringen.
- **Gleiche Ursache (funktionalitaet-views_b, sedo/views.py:150):** Kundenimport aus Excel: numerische Spalten mit Leerzellen liefern „1234.0“ als Kunden-Nr./PLZ – Dubletten und falsche PLZ auf Rechnung/XML – _norm um die '.0'-Regel ergänzen bzw. pd.read_excel(file, dtype=str).
- **Gleiche Ursache (funktionalitaet-views_b, sedo/views.py:160):** Kundenimport: Fuzzy-Spaltenwahl setzt die Straße auf die E-Mail-Adresse („Adresse“ trifft „E-Mail-Adresse“) – Fuzzy-Suche auf Wortgrenzen beschränken und E-Mail-Spalten ausschließen; Spaltenzuordnung mit Vorschau.
- **Aufwand:** S · **Dimension:** funktionalitaet-views_a · **Gutachter-Notiz:** Zusammengeführt aus 3 Rohfunden (funktionalitaet-views_a + funktionalitaet-views_b, funktionalitaet-views_b); jeder Rohfund von zwei Gutachtern bestätigt.

### F-63 · [MITTEL] · Zahlungsziel 0 Tage wird stillschweigend zu 14 Tagen (falsy-Fallback mit `or`)
- **Wo:** sedo/views.py:860
- **Was:** payment_days/valid_days erlauben min_value=0 (forms.py 34, 89), werden aber per `x or default` ausgewertet; 0 ist falsy. Gleiche Stelle: Z. 230, Z. 1108 (Angebotsgültigkeit), Z. 1950 (Abo-Lauf).
- **Beleg:** views.py 860: `inv.due_date = inv.issue_date + timedelta(days=form.cleaned_data.get('payment_days') or customer.payment_days)`; forms.py 34: `payment_days = forms.IntegerField(…, min_value=0, …)`
- **Szenario:** Barzahler-Rechnung: Nutzer trägt Zahlungsziel 0 ein → Rechnung/XML erhält Fälligkeit +14 Tage, Mahnwesen greift 14 Tage später.
- **Auswirkung:** Falsches Fälligkeitsdatum in PDF/XML (BT-9) und im Mahnlauf.
- **Fix:** `if payment_days is None: payment_days = customer.payment_days` an allen vier Stellen.
- **Aufwand:** S · **Dimension:** funktionalitaet-views_a · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-64 · [MITTEL] · Korrektur einer teilbezahlten Rechnung: bereits geleistete Zahlungen bleiben am Original, Korrekturrechnung gilt voll als offen
- **Wo:** sedo/views.py:876
- **Was:** ensure_correction_allowed prüft weder Zahlungen noch Offenbetrag; invoice_create kopiert die Positionen und setzt das Original auf 'korrigiert'. Zahlungen bleiben am Original, werden nirgends auf die Korrektur angerechnet. Die Korrekturrechnung erscheint mit vollem Brutto in OP und Mahnkandidaten. Im Testlauf bestätigt.
- **Beleg:** views.py:876-886 · invoices.py:50-61: ensure_correction_allowed prüft nur invoice_type, status und active_correction · payments.py:55-56.
- **Szenario:** RE…0010 über 1.000 €, Kunde zahlt 400 €. Korrektur auf 900 € → RE…0011 offen 900 €. Nach 14 Tagen Mahnkandidat über 900 € statt 500 €.
- **Auswirkung:** Falsche Mahn-/OP-Beträge, Kundenreklamationen; Zahlungshistorie zerreißt.
- **Fix:** Korrektur bei paid_amount > 0 blockieren (Hinweis auf Storno + Neurechnung) oder Zahlungen in der Transaktion umhängen und im Audit vermerken.
- **Aufwand:** M · **Dimension:** funktionalitaet-views_b · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-65 · [MITTEL] · Angebot-Anlage: nicht atomar (anders als Rechnung) und ?customer=-Vorbelegung wird ignoriert
- **Wo:** sedo/views.py:1111
- **Was:** offer_create speichert Angebot, Positionen, Summen und PDF nacheinander ohne transaction.atomic(); invoice_create tut dies atomar. Schlägt formset.save() oder generate_offer_pdf fehl, existiert ein Angebot mit verbrauchter Nummer ohne Positionen/PDF. Außerdem verlinkt customer_detail.html auf `offer_create?customer=<pk>`, offer_create liest request.GET nie aus.
- **Beleg:** views.py 1111-1118: `save_with_unique_number(off, 'AG')\n formset.instance = off\n formset.save()\n …\n generate_offer_pdf(off)` (kein atomic); 1090-1091: kein `request.GET.get('customer')`; customer_detail.html 8: `href="{% url 'sedo:offer_create' %}?customer={{ customer.pk }}"`
- **Szenario:** (a) Klick in der Kundenakte auf „Angebot“ → leeres Formular. (b) PDF-Erzeugung wirft Fehler → 500, Angebot trotzdem angelegt, Nummer verbraucht.
- **Auswirkung:** Funktionslücke im Alltag; halbe Angebote bei Fehlern.
- **Fix:** offer_create analog zu invoice_create: `pre_customer = request.GET.get('customer')`; Speichern + PDF in transaction.atomic().
- **Aufwand:** S · **Dimension:** funktionalitaet-views_a · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-66 · [MITTEL] · Batch-Mahnung ohne Auswahl mahnt ALLE Kandidaten mit der erzwungenen Stufe (Mahnung ohne vorherige Erinnerung)
- **Wo:** sedo/views.py:1234
- **Was:** Werden im Offene-Posten-Formular keine Checkboxen gewählt, fällt bulk_dunning auf alle get_dunning_candidates() zurück – aber mit dem vom Button gesetzten kind ('reminder' oder 'dunning'), nicht mit 'auto'. ensure_dunning_allowed erlaubt eine Mahnung ohne vorherige Erinnerung. Mahndokumente lassen sich nicht löschen, der Status wird 'gemahnt'.
- **Beleg:** views.py 1231-1236: `selected = request.POST.getlist('invoice_ids')\n if selected:\n result = send_bulk_dunning(selected, kind)\n else:\n candidates = get_dunning_candidates()\n result = send_bulk_dunning([str(inv.pk) for inv in candidates], kind)`; open_items.html 65: `<button name="kind" value="dunning" …>Auswahl – Mahnung</button>`
- **Szenario:** Nutzer klickt „Auswahl – Mahnung“, ohne eine Zeile angehakt zu haben → für alle 14+ Tage überfälligen Rechnungen wird sofort eine Mahnung (Stufe 2) erzeugt, obwohl noch keine Zahlungserinnerung existierte.
- **Auswirkung:** Irreversible Massenaktion mit Außenwirkung gegenüber allen säumigen Kunden; Mahnstufenmodell ausgehebelt.
- **Fix:** Bei leerer Auswahl mit messages.error abbrechen; den „Alle“-Pfad nur mit kind='auto' zulassen.
- **Aufwand:** S · **Dimension:** funktionalitaet-views_a · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-67 · [MITTEL] · Zahlungserfassung ohne Betrags- und Statusprüfung: negative/0-Beträge, Zahlungen auf stornierte oder bereits bezahlte Rechnungen, Storno bezahlter Rechnungen ohne Guthabenführung
- **Wo:** sedo/views.py:1245
- **Was:** ManualPaymentForm (amount ohne Untergrenze), invoice_add_payment, invoice_mark_paid und payment_assign prüfen weder Vorzeichen noch Rechnungsstatus/-typ. Negative Zahlungen erhöhen open_amount; Zahlungen auf 'storniert' werden gespeichert; ensure_storno_allowed lässt Storno voll bezahlter Rechnungen zu, ohne dass die Zahlung als Rückzahlungsverpflichtung geführt wird.
- **Beleg:** forms.py:140-147: `class ManualPaymentForm(forms.ModelForm): … fields = ['booking_date', 'amount', 'payer_name', 'purpose']` (kein clean_amount) — views.py:1250-1255 — views.py:1348-1352 (queryset Invoice.objects.all(), forms.py:137) — invoices.py:33-47 — models.py:186 — calc.py:45-46
- **Szenario:** (a) Rückerstattung als Zahlung „−119,00“ auf RE…0001 (119 €): open_amount = 238 €; die spätere Mahnung nennt „Offener Betrag 238,00 €“. (b) Voll bezahlte Rechnung wird storniert → Zahlung hängt daran, kein Kundenguthaben ausgewiesen. (c) Zahlung wird einer stornierten Rechnung zugewiesen → faktisch verschwunden.
- **Auswirkung:** Falsche Offen-/Mahnbeträge, unerkannte Rückzahlungspflichten, Zahlungen an nicht mehr aktiven Belegen.
- **Fix:** clean_amount > 0; Assign-Queryset auf aktive Belege einschränken; Warnung bei Überzahlung und beim Storno einer (teil)bezahlten Rechnung; optional Guthabenposten.
- **Aufwand:** M · **Dimension:** gobd-geschaeftslogik · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-68 · [MITTEL] · Zahlungserinnerung/Mahnung ohne Fälligkeits- oder Offenbetragsprüfung möglich
- **Wo:** sedo/views.py:1389
- **Was:** invoice_reminder prüft nur ensure_dunning_allowed (Status, vorhandene Stufen). Weder Fälligkeit (due_date in der Zukunft) noch open_amount > 0 werden geprüft. Die Buttons in invoice_detail.html sind immer sichtbar.
- **Beleg:** views.py 1388-1390: `ensure_dunning_allowed(invoice, kind)\n doc = generate_dunning_document(invoice, kind)`; dunning.py 58-73 prüft nur `_UNDUNNABLE_STATUS` und vorhandene Dokumente
- **Szenario:** Rechnung heute erstellt, Zahlungsziel 14 Tage. Nutzer klickt versehentlich „Mahnung“ → Mahnung mit „Fällig war: <Datum in 14 Tagen>“ wird erzeugt, Status 'gemahnt'; Dokument nicht löschbar.
- **Auswirkung:** Unberechtigte Mahnung an Kunden, Verkürzung des Zahlungsziels, falscher Status.
- **Fix:** In ensure_dunning_allowed zusätzlich due_date < heute und open_amount > 0 prüfen; Buttons nur bei is_overdue anzeigen.
- **Aufwand:** S · **Dimension:** funktionalitaet-views_a · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-69 · [MITTEL] · Kassenbuch ohne Kassenbestand/Übertrag und ohne Chronologieschutz – Saldo zeigt nur Monatsbewegung, Rückdatierungen erhalten laufende Nummern
- **Wo:** sedo/views.py:1400
- **Was:** cashbook_list und generate_cashbook_pdf berechnen nur Einnahmen−Ausgaben des Monats; kein Anfangsbestand/Übertrag, negativer Kassenbestand nicht erkennbar. Der Monat einer Buchung ergibt sich aus dem frei wählbaren booking_date; eine nachträglich in einen abgeschlossenen Vormonat datierte Buchung erhält dort die nächste Nummer.
- **Beleg:** views.py:1403-1410: `'saldo': income_total - expense_total` — pdf.py:242-243, 277 — views.py:1428-1429: `e.month = e.booking_date.strftime('%Y-%m'); e.booking_no = next_cashbook_no(e.month)` — models.py:403-404: `ordering = ['month', 'booking_no']; unique_together = [('month', 'booking_no')]`
- **Szenario:** August Saldo 150 €; September nur Ausgabe 300 € → PDF „Saldo −300,00 €“, obwohl der Bestand −150 € ist. Am 15.09. wird eine Ausgabe vom 03.08. nachgetragen → Nr. 12 im August hinter Buchungen vom 30.08.; die versendete August-PDF stimmt nicht mehr.
- **Auswirkung:** Kassenbuch erfüllt Kassensturzfähigkeit und Chronologie-Anforderung der GoBD nicht.
- **Fix:** Anfangs-/Endbestand je Monat ausweisen; Buchungen in abgeschlossene Monate nur als Korrekturbuchung; Buchungsnummer chronologisch nach booking_date.
- **Aufwand:** M · **Dimension:** gobd-geschaeftslogik · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-70 · [MITTEL] · _month_bounds wirft bei jedem Monatswert außerhalb „YYYY-MM“ eine unbehandelte Exception (500)
- **Wo:** sedo/views.py:1574
- **Was:** month kommt aus request.POST. `int(x) for x in month_str.split('-')` bzw. date(year, month, 1) werfen ValueError bei „2025-13“, „08/2025“ oder Text. Safari (macOS) rendert `<input type="month">` als Freitextfeld.
- **Beleg:** views.py 1574-1575: `year, month = [int(x) for x in month_str.split('-')]\n start = date(year, month, 1)`; 1724: `ps, pe = _month_bounds(month)`; 1942: `ps, pe = _month_bounds(month)`
- **Szenario:** Nutzer tippt in Safari „08.2025“ in das Monatsfeld und startet den GWG-Lauf → Django-Fehlerseite.
- **Auswirkung:** Abbruch des Monatslaufs mit Fehlerseite statt Meldung.
- **Fix:** _month_bounds mit try/except versehen und in beiden Views mit messages.error abbrechen; alternativ Regex-Prüfung.
- **Aufwand:** S · **Dimension:** funktionalitaet-views_a · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-71 · [MITTEL] · Abo-Monatslauf hat keinen DB-seitigen Dublettenschutz – Doppelklick kann doppelte Rechnungen erzeugen
- **Wo:** sedo/views.py:1947
- **Was:** Der GWG-Lauf ist über invoice_unique_gwg_monthly_run abgesichert. Der Abo-Lauf prüft nur per exists() (check-then-act). Zwei parallele POSTs können beide die Prüfung passieren.
- **Beleg:** views.py 1947: `if skip_existing and Invoice.objects.filter(customer=tmpl.customer, obj=tmpl.obj, source='Abo Monatsrechnung', period_start=ps, period_end=pe).exclude(status='storniert').exists():`; models.py 149: `condition=(models.Q(source='GWG Monatsrechnung') …)`
- **Szenario:** Doppelklick auf „Ausgewählte Monatsrechnungen erzeugen“ → jede aktive Vorlage bekommt zwei Rechnungen.
- **Auswirkung:** Doppelte Rechnungen an Kunden, verbrauchte Nummern, Stornoaufwand.
- **Fix:** UniqueConstraint auch für source='Abo Monatsrechnung' und IntegrityError als „übersprungen“ behandeln; Button nach Klick deaktivieren.
- **Aufwand:** S · **Dimension:** funktionalitaet-views_b · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-72 · [MITTEL] · Ungeprüfte Monats-/Datumsparameter fließen in Dateisystempfade und int()-Parsing – Verzeichnisanlage außerhalb von media/ und 500er
- **Wo:** sedo/views.py:2097
- **Was:** month_close übernimmt selected_month aus POST/GET; die Validierung prüft nur, dass die ersten beiden '-'-Teile Integer sind, der Rest bleibt beliebig (inkl. '/' und '..'). Der Rohwert wird als Pfadkomponente für out_dir mit mkdir(parents=True) genutzt – dadurch können Verzeichnisse außerhalb von MEDIA_ROOT angelegt werden; die anschließende ZIP-Erstellung schlägt fehl (500). Ähnlich: _month_bounds(month) (views.py:1574, aufgerufen 1724 und 1942) wirft bei nicht 'YYYY-MM' ValueError → 500; cashbook_pdf (views.py:1480-1481 → pdf.py:237 `out_dir / f'Kassenbuch_{month}.pdf'`) → FileNotFoundError/500 bei '/' im Monat.
- **Beleg:** views.py:2081: `selected_month = request.POST.get('month') or request.GET.get('month') or default_month`; 2085: `year, month = [int(x) for x in selected_month.split('-')[:2]]` (selected_month wird bei Erfolg NICHT normalisiert); 2097-2098: `out_dir = Path(settings.MEDIA_ROOT) / 'month_close' / selected_month; out_dir.mkdir(parents=True, exist_ok=True)`; 2099: `zip_path = out_dir / f'SEDO_Rechnungen_{selected_month}_Steuerberater_PDF.zip'`.
- **Szenario:** POST /monatsabschluss/ mit action=export_tax_email und month=`2025-01-../../../../../../tmp/sedo_pwn` → mkdir legt /tmp/sedo_pwn an, ZipFile-Öffnen wirft FileNotFoundError → Server antwortet 500. GET /kassenbuch/pdf/?month=2025/01 → 500. POST /monatsrechnung/abo/erzeugen/ mit month=abc → ValueError in _month_bounds → 500.
- **Auswirkung:** Authentifizierter Nutzer (oder XSS/CSRF-Kette) kann Verzeichnisse an beliebigen schreibbaren Orten des Servers erzeugen; mehrere ungefangene Ausnahmen (bei DEBUG=1 mit Stacktrace/Settings-Leak).
- **Fix:** Monat zentral mit `re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', value)` validieren und bei Fehler 400/Message + Redirect; selected_month nach dem Parsen immer auf f'{year:04d}-{month:02d}' normalisieren; Pfade nur aus validierten Werten bauen.
- **Aufwand:** S · **Dimension:** sicherheit-web · **Gutachter-Notiz:** Beide Gutachter bestätigen.


<!-- ---------- NIEDRIG ---------- -->

### F-73 · [NIEDRIG] · Versions- und Dokumentationsdrift, verwaiste Dateien im Projektordner
- **Wo:** README.md:1
- **Was:** Versionsangaben widersprechen sich (README 'v1.0 beta 3.21.0', START_HIER 'v1.0 beta', desktop.py 'v2.3', spec 2.3.0, Inno-Setup 2.3.1, CHANGELOG 2.3.1). README verweist auf MEMORY_SEDO.md und AENDERUNGSBERICHT_v1_0_beta_3_21_0.md im Projektordner – beide existieren dort nicht. Die Hetzner-Sektion der Deployment-Notizen beschreibt systemd/nginx, aber im Projekt gibt es weder Unit-Dateien noch einen WSGI-Server in requirements.txt. Build-Artefakte (dist/, build/, .venv_build, DMGs) liegen im Projektordner.
- **Beleg:** README.md:1 `# SEDO Rechnungstool · v1.0 beta 3.21.0`; README verweist auf MEMORY_SEDO.md/AENDERUNGSBERICHT → `ls` beide: No such file · desktop.py:210 „v2.3“ · sedo_desktop.spec:85 `'CFBundleShortVersionString': '2.3.0'` · sedo_setup_windows.iss:14 `#define AppVersion "2.3.1"`
- **Szenario:** Nutzer folgt der README vor dem ersten ARP-Echtlauf und sucht die Checkliste – Datei fehlt; Supportfall meldet 'v2.3', Installer sagt 2.3.1.
- **Auswirkung:** Fehlleitende Anleitung, unklare Versionsidentität, unnötig große Projektkopien.
- **Fix:** Eine Versionsquelle (sedo/__init__.py __version__) und daraus spec/iss/About/README speisen; README-Verweise korrigieren; Build-Artefakte aus dem Projektordner heraushalten.
- **Aufwand:** S · **Dimension:** tests-konfig-deployment · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-74 · [NIEDRIG] · SEDO.vbs: 300-Sekunden-Timeout ist bei der Erstinstallation zu kurz → Fehlerdialog trotz laufender Einrichtung
- **Wo:** SEDO.vbs:59
- **Was:** Beim Erststart startet SEDO.vbs START_WINDOWS.bat (venv anlegen, `pip install -r requirements.txt` inkl. faster-whisper – leicht > 5 Minuten) und wartet dann nur 300 × 1 s auf den Server. Danach erscheint „Der SEDO-Server ist nicht gestartet“, obwohl die Installation noch läuft; das App-Fenster wird nicht mehr automatisch geöffnet.
- **Beleg:** SEDO.vbs 59–62: `For i = 1 To 300 / If ServerUp() Then Exit For / WScript.Sleep 1000 / Next` … `MsgBox "Der SEDO-Server ist nicht gestartet." …`; SEDO.vbs 47–52: Erststart-Zweig startet START_WINDOWS.bat
- **Szenario:** Neuer Windows-Rechner, Doppelklick auf die Verknüpfung. pip lädt 6 Minuten. Nach 5 Minuten Fehlermeldung; das schwarze Fenster zeigt später das Einmal-Passwort, aber kein App-Fenster öffnet sich.
- **Auswirkung:** Verwirrender Erststart, Passwortanzeige und Fehlerdialog widersprechen sich; kein Datenschaden.
- **Fix:** Im Erststart-Zweig ohne festes Limit (oder 30 Minuten) warten und den Dialog nur zeigen, wenn der Bat-Prozess beendet ist und der Server trotzdem nicht antwortet.
- **Aufwand:** S · **Dimension:** frontend-js-templates_a · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-75 · [NIEDRIG] · Platzhalter-SECRET_KEY aus .env.example wird ohne Prüfung übernommen
- **Wo:** config/settings.py:56
- **Was:** _get_secret_key akzeptiert jeden nicht-leeren Wert aus DJANGO_SECRET_KEY. .env.example (Z. 4) enthält den dokumentierten Wert "CHANGE-ME-GENERATE-A-STRONG-SECRET-KEY". Wer .env.example nach .env kopiert, bekommt einen öffentlich bekannten SECRET_KEY, der den zufällig erzeugten Dateischlüssel verdrängt.
- **Beleg:** key = os.environ.get('DJANGO_SECRET_KEY', '').strip() if key: return key # kein Abgleich mit dem Platzhalter, keine Mindestlänge
- **Szenario:** Nutzer kopiert .env.example → .env, ändert nur ALLOWED_HOSTS/SSL-Flags und schaltet den Tunnel frei → Django signiert Cookies/signing.dumps mit einem bekannten Schlüssel.
- **Auswirkung:** Signierte Werte sind fälschbar; das eingebaute „kein dokumentierter Standard-Schlüssel“ wird unterlaufen.
- **Fix:** Platzhalter erkennen und verwerfen (`if key and key != 'CHANGE-ME-…' and len(key) >= 50`), sonst Fallback auf data/.secret_key und Warnung im security_status.
- **Aufwand:** S · **Dimension:** sicherheit-secrets-banking · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-76 · [NIEDRIG] · CSRF_TRUSTED_ORIGINS enthält fest kodierte Wildcards für fremde Quick-Tunnel-Domains
- **Wo:** config/settings.py:92
- **Was:** 'https://*.trycloudflare.com' und 'https://*.cloudflareaccess.com' sind hart im Code hinterlegt. Damit besteht jede Origin-/Referer-Prüfung für POSTs von beliebigen fremden Quick-Tunnels (jeder kann sich kostenlos einen <name>.trycloudflare.com-Endpunkt erzeugen). Der eigentliche Token-Vergleich und SameSite=Lax verhindern aktuell noch die Ausnutzung, die Origin-Prüfung als zweite Verteidigungslinie ist aber wirkungslos.
- **Beleg:** settings.py:91-96: `CSRF_TRUSTED_ORIGINS = ['https://*.trycloudflare.com', 'https://*.cloudflareaccess.com', 'http://127.0.0.1:8000', 'http://localhost:8000']`.
- **Szenario:** Angreifer betreibt eine Seite unter https://boese-seite.trycloudflare.com und lässt den eingeloggten Nutzer dort ein Formular gegen https://sedo-xyz.trycloudflare.com/rechnungen/5/storno/ abschicken. Die Origin-Prüfung akzeptiert die fremde Origin; nur weil der CSRF-Token nicht mitgeliefert werden kann, scheitert der Request. Fällt der Token-Schutz (z. B. durch Fund 3 oder eine künftige csrf_exempt-View), gibt es keine zweite Barriere.
- **Auswirkung:** Geschwächte Defense-in-Depth gegen CSRF im Tunnelbetrieb.
- **Fix:** Wildcards entfernen; die konkrete Tunnel-Origin nur per DJANGO_CSRF_TRUSTED_ORIGINS in der .env eintragen (der Mechanismus existiert bereits in settings.py:97-99).
- **Aufwand:** S · **Dimension:** sicherheit-web · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-77 · [NIEDRIG] · Kein Passwortwechsel in der Anwendung – Zugangsdaten lassen sich nach Verdacht auf Kompromittierung nur per Django-Admin oder CLI rotieren
- **Wo:** config/urls.py:9
- **Was:** config/urls.py bindet nur LoginView/LogoutView ein; weder `django.contrib.auth.urls` noch eine eigene password_change-Route existieren. Die Sicherheitsseite verweist auf `python manage.py changepassword admin` – im PyInstaller-Bundle gibt es dieses manage.py nicht. Nicht-Staff-Nutzer haben keinen Weg, ihr Passwort zu ändern.
- **Beleg:** config/urls.py:9-10 `path('login/', auth_views.LoginView.as_view(...))`, `path('logout/', auth_views.LogoutView.as_view(), name='logout')` – keine weiteren Auth-Routen; grep nach `password_change|auth.urls|PasswordChange` ohne Treffer; sedo/views.py:2177 Hinweistext `python manage.py changepassword admin`
- **Szenario:** Der zweite Nutzer hat sein Passwort auf einem fremden Rechner eingegeben und möchte es sofort ändern → in der App gibt es keine Funktion, /admin/ verweigert ihm als Nicht-Staff den Zugang, im Desktop-Bundle existiert kein manage.py.
- **Auswirkung:** Verlängerte Angriffsfenster nach Passwort-Leak; Selbstbedienungs-Passwortwechsel fehlt in einem tunnel-exponierten System mit Bank-/Kundendaten.
- **Fix:** `path('konto/passwort/', auth_views.PasswordChangeView.as_view(...), name='password_change')` ergänzen, Link in base.html, nach Wechsel andere Sessions des Nutzers löschen; AUTH_PASSWORD_VALIDATORS setzen.
- **Aufwand:** S · **Dimension:** sicherheit-web · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-78 · [NIEDRIG] · Django-Admin zeigt alle AppSettings inkl. Bank-PIN, SMTP-Passwort und Claude-Key im Klartext
- **Wo:** sedo/admin.py:41
- **Was:** AppSetting ist ohne eigenes ModelAdmin registriert. Unter /admin/sedo/appsetting/ erscheinen Schlüssel und Wert im Klartext – auch fints_pin, smtp_password, ai_api_key. Die eigene Einstellungsseite maskiert diese Werte bewusst, der Admin unterläuft das.
- **Beleg:** admin.site.register(AppSetting)
- **Szenario:** Zweiter Nutzer mit is_staff öffnet /admin/sedo/appsetting/ → sieht die Online-Banking-PIN des Firmenkontos; Werte landen im Browser-Cache/-Verlauf.
- **Auswirkung:** Secrets sind für jeden Staff-Account lesbar; widerspricht der Trennung „Rollen/Rechte“ aus DEPLOYMENT_SECURITY_NOTES.md.
- **Fix:** Eigenes AppSettingAdmin mit maskierten Secret-Werten oder Registrierung entfernen.
- **Aufwand:** S · **Dimension:** sicherheit-secrets-banking · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-79 · [NIEDRIG] · Temp-Kopie der kompletten Datenbank und Backup-ZIPs mit 0644; Temp-Datei bleibt bei Fehlern liegen
- **Wo:** sedo/backup_utils.py:32
- **Was:** mkstemp erzeugt die Datei mit 0600, shutil.copy2 überträgt danach die Rechte der Quelle (0644) auf die Kopie im Temp-Ordner; die ZIPs entstehen mit 0644. Schlägt `sqlite3.connect(tmp)` fehl (außerhalb des try), bleibt die Voll-Kopie im Temp-Ordner liegen.
- **Beleg:** fd, tmp_name = tempfile.mkstemp(suffix='.sqlite3') … shutil.copy2(db_path, tmp) # kopiert Modus 0644 der Quelle con = sqlite3.connect(tmp) # außerhalb des try/finally
- **Szenario:** Linux-Server mit weiterem Shell-Nutzer: während des Backups liegt /tmp/tmpXXXX.sqlite3 (komplette Kunden-DB) mit 0644; Backup-ZIPs dauerhaft 0644.
- **Auswirkung:** Lokale Offenlegung von Kunden- und Zugangsdaten auf Mehrbenutzersystemen; verwaiste Temp-Kopien.
- **Fix:** `shutil.copyfile` statt copy2 (oder `os.chmod(tmp, 0o600)`), ZIP `os.chmod(out, 0o600)`; Kopie in try/finally kapseln.
- **Aufwand:** S · **Dimension:** sicherheit-secrets-banking · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-80 · [NIEDRIG] · Backup kopiert die laufende SQLite-Datei per shutil.copy2 statt über die SQLite-Backup-API
- **Wo:** sedo/backup_utils.py:36; sedo/backup_utils.py:36
- **Was:** _sanitized_db_copy kopiert die Datenbankdatei byteweise, während die App schreiben kann. journal_mode der Live-DB ist `delete`; das Rollback-Journal wird nicht mitkopiert. Läuft während des Kopierens eine Transaktion, enthält die Kopie teilweise geschriebene Seiten ohne Journal → beschädigte Sicherung. Das Backup läuft innerhalb eines Requests (Middleware).
- **Beleg:** shutil.copy2(db_path, tmp) con = sqlite3.connect(tmp)
- **Szenario:** Nutzer A speichert eine Rechnung, gleichzeitig löst der erste Request von Nutzer B nach Serverstart das Wochenbackup aus → Kopie erhält Seiten aus der Mitte der Transaktion → integrity_check der Sicherung schlägt fehl.
- **Auswirkung:** Backup kann im Ernstfall nicht wiederhergestellt werden.
- **Fix:** Konsistenten Snapshot per `sqlite3.connect(db_path).backup(sqlite3.connect(tmp))` erzeugen; danach Bereinigung + VACUUM; optional `PRAGMA integrity_check` loggen.
- **Gleiche Ursache (tests-konfig-deployment, sedo/backup_utils.py:36):** SQLite-Backup per Dateikopie statt sqlite3-Backup-API – Kopie kann bei gleichzeitigem Schreiben inkonsistent sein – `src.backup(dst)` (SQLite Online-Backup-API) statt copy2; danach Secret-Bereinigung auf der Kopie.
- **Aufwand:** S · **Dimension:** sicherheit-secrets-banking · **Gutachter-Notiz:** Zusammengeführt aus 2 Rohfunden (sicherheit-secrets-banking + tests-konfig-deployment); jeder Rohfund von zwei Gutachtern bestätigt.

### F-81 · [NIEDRIG] · Fehlschlag der Secret-Bereinigung wird stumm ignoriert – Backup enthält dann alle Secrets, UI behauptet das Gegenteil
- **Wo:** sedo/backup_utils.py:43
- **Was:** Der UPDATE, der Secrets aus der Backup-Kopie entfernt, steht in einem `except sqlite3.Error: pass`. Es fängt jeden SQLite-Fehler („database disk image is malformed“, „database is locked“, „disk I/O error“). In all diesen Fällen wird die ungesäuberte Kopie kommentarlos ins ZIP geschrieben; UI meldet weiterhin „Secrets nicht enthalten“.
- **Beleg:** Zeile 43–44: `except sqlite3.Error:` / `pass # Tabelle existiert evtl. noch nicht`; Zeile 82–84: `sanitized = _sanitized_db_copy(db_path)` … `zf.write(sanitized, …)` ohne Statusprüfung.
- **Szenario:** Wochenbackup läuft, während ein zweiter Prozess eine Rechnung schreibt → Kopie inkonsistent → UPDATE wirft DatabaseError → geschluckt → ZIP mit Klartext-PIN wird als bereinigt gemeldet.
- **Auswirkung:** Secrets in weitergegebenen Backups ohne jeden Hinweis.
- **Fix:** Nur „no such table: sedo_appsetting“ tolerieren; bei jedem anderen Fehler `create_backup` abbrechen und melden. Nach dem UPDATE per SELECT verifizieren, dass keine Secret-Werte mehr vorhanden sind.
- **Aufwand:** S · **Dimension:** sicherheit-secrets-banking · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-82 · [NIEDRIG] · Backup-Ordner ohne Rotation – jedes ZIP enthält den kompletten Medienordner, 259 Dateien liegen bereits vor
- **Wo:** sedo/backup_utils.py:64
- **Was:** `create_backup` legt bei jedem Lauf ein neues ZIP mit vollständiger DB und allen PDFs/XMLs/EMLs an; weder create_backup noch ensure_weekly_backup noch die Backup-Seite löschen alte Sicherungen oder begrenzen deren Anzahl. Aktuell 259 ZIPs im Ordner.
- **Beleg:** Zeile 62–64: `out = backup_dir() / f'SEDO_Backup_{ts}_{reason_safe}.zip'`; kein `unlink`/`glob` alter Backups in backup_utils.py oder views.backup_page; `ls backups | wc -l` → 259.
- **Szenario:** Nach zwei Jahren Betrieb: media/ ≈ 1–2 GB, 100 Wochenbackups × 1–2 GB → Platte läuft voll; Wochenbackup schlägt still fehl, App nicht mehr schreibfähig.
- **Auswirkung:** Schleichender Plattenverbrauch, im Extremfall Ausfall von App und Backup gleichzeitig.
- **Fix:** Aufbewahrungsregel in `create_backup`: z. B. die letzten 8 weekly_auto- und 10 manuellen Backups behalten, ältere löschen (mit Audit-Eintrag); Anzahl/Gesamtgröße auf der Backup-Seite anzeigen.
- **Aufwand:** S · **Dimension:** sicherheit-secrets-banking · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-83 · [NIEDRIG] · Leere XML-Elemente bei fehlenden Adress-/ID-Daten (PostcodeCode, ShipTo/ID)
- **Wo:** sedo/core/rechnungslauf_core.py:697
- **Was:** sub() erzeugt ein Element, sobald text nicht None ist – auch bei ''. Fehlt beim Kunden die PLZ oder beim ARS-Objekt die Abrechnungseinheit, entstehen leere Elemente statt weggelassener Optionalfelder. Betroffen: Z. 697 (Käufer-PLZ), 719 (ShipTo/ID), 731 (ShipTo-PLZ), 732/733.
- **Beleg:** sub(buyer_addr, "ram", "PostcodeCode", inv.buyer_postcode) ... sub(shipto, "ram", "ID", billing_unit_id or billing_unit_name) → Testlauf mit buyer_postcode='': <ram:PostcodeCode></ram:PostcodeCode>
- **Szenario:** Kunde ohne PLZ anlegen (Feld ist blank=True) → XML enthält leeres PostcodeCode; Schematron-Regeln gegen leere Elemente schlagen an.
- **Auswirkung:** Vermeidbare Validierungswarnungen/-fehler.
- **Fix:** In sub() bei leerem String kein Element anlegen; Pflichtfelder weiterhin erzwingen.
- **Aufwand:** S · **Dimension:** zugferd-xml-pdf · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-84 · [NIEDRIG] · Migration 0009 (und 0012) legt Unique-Constraints ohne Datenbereinigung an – Upgrade mit Altbestand bricht ab, App startet nicht
- **Wo:** sedo/migrations/0009_gwg_monthly_unique.py:13
- **Was:** 0009 erzeugt einen partiellen UNIQUE INDEX über (customer, obj, period_start, period_end) für aktive GWG-Monatsrechnungen. Vor v3.22 war der Monatslauf nicht idempotent, doppelte aktive Monatsrechnungen sind in Bestandsdaten realistisch. Keine vorbereitende RunPython-Operation. Dasselbe Muster in 0012 für invoice_unique_active_storno/-correction: 0012 bereinigt nur DunningDocument, nicht Invoice.
- **Beleg:** 0009:13-16: `migrations.AddConstraint(model_name='invoice', constraint=models.UniqueConstraint(condition=…, fields=('customer', 'obj', 'period_start', 'period_end'), name='invoice_unique_gwg_monthly_run'))`; 0012:31-43: nur `RunPython(_dedupe_dunning_documents, …)` vor den Invoice-Constraints; desktop.py:73: `call_command('migrate', interactive=False, verbosity=0)`; START_HIER.command:24 / START_WINDOWS.bat:10: `python manage.py migrate`
- **Szenario:** Zweite Installation oder Restore eines Backups von vor 3.22 enthält für einen Kunden/Einheit/Monat zwei aktive GWG-Rechnungen. Update → migrate → 0009 wirft `IntegrityError: UNIQUE constraint failed` → Migration abgebrochen, 0010–0012 fehlen, Desktop-App startet nicht.
- **Auswirkung:** Upgrade-Blocker ohne verständliche Fehlermeldung; Nutzer kann nicht arbeiten bis jemand die DB manuell repariert. Manuelles Löschen wäre ein GoBD-Verstoß.
- **Fix:** Vor jedem AddConstraint eine RunPython-Operation: Duplikate protokollieren und Migration mit klarer Meldung abbrechen, oder jüngere Duplikate soft-deleten mit AuditLog-Eintrag. Für 0012 dieselbe Vorbereitung für Stornos/Korrekturen.
- **Aufwand:** M · **Dimension:** datenmodell-migrationen · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-85 · [NIEDRIG] · Migration 0012 löscht Mahndokumente hart und wählt das älteste – versandte Mahnschreiben verschwinden ohne Protokoll
- **Wo:** sedo/migrations/0012_concurrency_constraints.py:20
- **Was:** Zur Vorbereitung des Unique-Constraints (invoice, kind) werden Duplikate per doc.delete() hart entfernt. Mahnungen sind versandte Geschäftsbriefe; die zweite Erinnerung konnte nach Teilzahlung einen anderen Betrag und eine spätere Frist tragen. PDF-Dateien bleiben verwaist. Kein AuditLog-Eintrag.
- **Beleg:** 0012:17-22: `for doc in DunningDocument.objects.order_by('document_date', 'id'): key = (doc.invoice_id, doc.kind); if key in seen: doc.delete()`
- **Szenario:** Rechnung mit Erinnerung vom 05.06. (1.000 €) und zweiter Erinnerung vom 20.06. (400 €, Frist 04.07.). Migration behält nur die vom 05.06.; das zuletzt zugestellte Schreiben ist weg, next_dunning_kind rechnet mit der alten Frist.
- **Auswirkung:** Verlust von Belegdaten zu versandten Schreiben (GoBD-Aufbewahrung), falsche Mahnstufen-Logik, verwaiste Dateien.
- **Fix:** Feld `superseded`/`deleted_at` auf DunningDocument und partieller Index; Migration setzt nur das Flag und schreibt AuditLog. Falls Löschen bleibt: jüngstes statt ältestes behalten und protokollieren.
- **Aufwand:** M · **Dimension:** datenmodell-migrationen · **Gutachter-Notiz:** Strittig, vom Hauptreviewer entschieden: Hartes Löschen versandter Mahnschreiben ist auch als dokumentierte Entscheidung ein GoBD-Aufbewahrungsrisiko; Migration ist bereits angewendet, daher nur noch Konzept-Hinweis: künftig nie hart löschen in Migrationen, sondern Flag/Soft-Delete + Audit.

### F-86 · [NIEDRIG] · Bank-PIN, SMTP-Passwort und Claude-Key liegen unverschlüsselt in sedo_appsetting; DB-Datei ist 0644 – Schlüsseldatei dagegen 0600; Env-Alternative für die PIN undokumentiert
- **Wo:** sedo/models.py:425
- **Was:** AppSetting ist ein schlichtes TextField; alle drei Secrets werden im Klartext gespeichert, keine Verschlüsselung, keine Keychain-Anbindung. Die DB-Datei wird mit Standard-umask angelegt (0644), während `data/.secret_key` bewusst 0600 ist. `SEDO_FINTS_PIN` als Env-Alternative ist in .env.example nicht dokumentiert.
- **Beleg:** models.py:425: `value = models.TextField('Wert', blank=True)`; banking.py:56: `'pin': os.environ.get('SEDO_FINTS_PIN', '') or AppSetting.get('fints_pin', '')`; settings.py:71: `os.chmod(key_file, 0o600)` (nur .secret_key); Dateirechte: `-rw------- data/.secret_key`, `-rw-r--r-- data/sedo_web.sqlite3`; .env.example nennt nur SEDO_AI_API_KEY und SEDO_SMTP_PASSWORD.
- **Szenario:** Rechner mit zweitem lokalen Benutzerkonto, Projektordner in einem für andere lesbaren Pfad → `sqlite3 sedo_web.sqlite3 "select * from sedo_appsetting"` liefert PIN, SMTP-Passwort und API-Key ohne Login.
- **Auswirkung:** Klartext-Secrets mit Standard-Dateirechten; Weg zur sicheren Alternative für die PIN nicht dokumentiert.
- **Fix:** Kurzfristig: DB-Datei und data/-Ordner auf 0600/0700 setzen, `SEDO_FINTS_PIN` dokumentieren. Mittelfristig: Secret-Werte mit Fernet (aus SECRET_KEY abgeleitet) verschlüsselt ablegen oder `keyring` nutzen.
- **Aufwand:** M · **Dimension:** sicherheit-secrets-banking · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-87 · [NIEDRIG] · KI-erfundene Artikelnummern landen ungeprüft als SellerAssignedID in der ZUGFeRD-XML
- **Wo:** sedo/services/ai.py:156
- **Was:** Das Tool-Schema verlangt für jede Position ein Feld 'artikel_nr' (required, ohne Regel im System-Prompt). Das Modell erfindet bei generischen Diktaten plausibel klingende Nummern. normalize_ai_positions reicht den Wert nur längenbegrenzt durch, offer_ai.js schreibt ihn ohne Markierung ins Feld article_no. Aus dem Angebot wird die Rechnung erzeugt, der XML-Builder schreibt ihn als ram:SellerAssignedID – für alle Nicht-Aareon-Rechnungen ist include_seller_assigned_id immer True.
- **Beleg:** ai.py:156 `'artikel_nr': {'type': ['string', 'null']},` (in 'required' Zeile 158); ai.py:366; offer_ai.js:519 `if (pos.artikel_nr) setValue(inp.a, pos.artikel_nr);`; invoices.py:128 `include_seller_assigned_id = (not is_ars) or include_flag`; rechnungslauf_core.py:647-648
- **Szenario:** Diktat „Glasreinigung 120 m², 2 Euro pro m²“ → Claude liefert artikel_nr "GR-001" (halluziniert) → Angebot → Rechnung → E-Rechnung enthält <ram:SellerAssignedID>GR-001</ram:SellerAssignedID>, obwohl es diese Artikelnummer nicht gibt.
- **Auswirkung:** Erfundene Kennungen in rechtsverbindlichen E-Rechnungen; bei Empfängern mit Artikel-Abgleich Rückweisungen.
- **Fix:** artikel_nr aus 'required' entfernen, nur bei Katalog-/Mapping-Treffer übernehmen, im Frontend als KI-Vorschlag markieren.
- **Aufwand:** S · **Dimension:** datenschutz-ki · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-88 · [NIEDRIG] · Erzwungener Tool-Use (tool_choice type=tool) mit frei konfigurierbarem Modellnamen – mit Claude Fable 5.1/Mythos 5.1 schlagen alle KI-Funktionen mit HTTP 400 fehl
- **Wo:** sedo/services/ai.py:439
- **Was:** Alle drei Aufrufpfade erzwingen den Werkzeugaufruf über `tool_choice: {type: 'tool', name: …}`. Das Modell ist ein freies Textfeld in den Einstellungen. Laut Anthropic-API-Dokumentation ist erzwungener Tool-Use auf Claude Fable 5.1/Mythos 5.1 entfernt und liefert 400. Die Fehlermeldung nennt nur „Claude-Fehler (HTTP 400, invalid_request_error)“ ohne Bezug zur Modellwahl.
- **Beleg:** ai.py:439 `'tool_choice': {'type': 'tool', 'name': AI_TOOL_NAME},` (ebenso Zeile 546 und 644); settings.html:107 `<input name="ai_text_model" ...>`; views.py:1993 (keine Validierung); ai.py:94
- **Szenario:** Nutzer trägt „claude-fable-5-1“ ein → jedes Diktat endet mit HTTP 400; Whisper-Transkription geht verloren, Ursache nicht erkennbar.
- **Auswirkung:** Totalausfall der KI-Funktionen bei bestimmten Modellwahlen, unklare Fehlermeldung.
- **Fix:** Modellwahl auf Auswahlliste beschränken oder per Testaufruf validieren; alternativ tool_choice 'auto' + strict; 400-Fehler mit Hinweis auf die Modelleinstellung.
- **Aufwand:** S · **Dimension:** datenschutz-ki · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-89 · [NIEDRIG] · Stornorechnungen (negativer Brutto) werden beim Öffnen der Offenen Posten automatisch auf „bezahlt“ gesetzt – ohne Zahlung
- **Wo:** sedo/services/calc.py:50
- **Was:** Die Stornorechnung (Typ 457) wird mit status='erstellt' und negativem gross_total angelegt. refresh_invoice_payment_status prüft `paid >= gross_total` – bei paid = 0 und gross_total = −119 ist das wahr, sodass die Rechnung 'bezahlt' wird. Ausgelöst durch den GET auf Offene Posten.
- **Beleg:** invoices.py:231-238: `storno = create_numbered_invoice(invoice_type='457', … status='erstellt', …)` mit `quantity=-item.quantity` — calc.py:48-51: `if paid >= invoice.gross_total and invoice.gross_total != Decimal('0.00'): invoice.status = 'bezahlt'` — views.py:1204-1205
- **Szenario:** Storno RE2025090005 (−119 €) wird erzeugt. Nutzer öffnet „Offene Posten“ → RE2025090005 wird 'bezahlt' (kein Audit-Eintrag), Detailseite zeigt „Bezahlt“, Zahlungen: keine.
- **Auswirkung:** Irreführender Status ohne Zahlungsnachweis, automatischer Statuswechsel ohne Audit, Rückzahlungspflicht an den Kunden geht unter.
- **Fix:** Stornorechnungen (457) in refresh_invoice_payment_status ausnehmen bzw. eigenen Status führen; Vergleich nur für gross_total > 0; Gutschriften separat anzeigen.
- **Aufwand:** S · **Dimension:** gobd-geschaeftslogik · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-90 · [NIEDRIG] · Mahndokument nicht atomar: DB-Zeile vor PDF – ein PDF-Fehler verbraucht die Mahnstufe ohne Dokument und ohne Wiederholungsmöglichkeit
- **Wo:** sedo/services/dunning.py:84
- **Was:** generate_dunning_document legt zuerst den DunningDocument-Datensatz an (Unique je Rechnung+Stufe) und erzeugt danach außerhalb der Transaktion das PDF. Scheitert canvas.save(), bleibt ein DunningDocument ohne pdf_file zurück; die Stufe gilt als erledigt, der Status wird nicht auf 'gemahnt' gesetzt, kein Weg zur Neuerzeugung.
- **Beleg:** dunning.py:84-88: `with transaction.atomic(): doc = DunningDocument.objects.create(invoice=invoice, kind=kind, …)` — dunning.py:95-100: `c = canvas.Canvas(str(pdf_path), pagesize=A4)` (außerhalb von atomic) — dunning.py:162-168 — dunning.py:63-69: `if same: … raise WorkflowError('… wurde bereits am … erzeugt.')`
- **Szenario:** media/dunning ist nicht beschreibbar. Klick „Zahlungserinnerung“ → 500, DunningDocument(kind='reminder') existiert ohne PDF. Erneuter Klick → „wurde bereits erzeugt“. Nach 7 Tagen wird die Rechnung Kandidat für „Mahnung“, obwohl der Kunde nie eine Erinnerung erhielt.
- **Auswirkung:** Mahnstufen ohne Schreiben, keine Reparaturmöglichkeit über die UI, Mahnprozess springt eine Stufe.
- **Fix:** PDF zuerst in Temp-Datei erzeugen und DB-Zeile + Datei + Statuswechsel in EINER Transaktion abschließen; ensure_dunning_allowed nur Dokumente mit pdf_file werten.
- **Aufwand:** S · **Dimension:** gobd-geschaeftslogik · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-91 · [NIEDRIG] · Nummernkreis-Monat folgt dem Tagesdatum, Rechnungsdatum ist frei rück-/vordatierbar – Nummer und Datum können auseinanderlaufen
- **Wo:** sedo/services/numbering.py:66
- **Was:** _allocate_number leitet Jahr/Monat der Nummer aus timezone.localdate() ab, während issue_date im Formular ohne Grenzen frei gesetzt werden kann. Monatsabschluss, Rechnungsliste und USt-Zeitraum filtern nach issue_date. Damit können Belege RE202509xxxx ein Rechnungsdatum in einem anderen Jahr tragen.
- **Beleg:** numbering.py:66-74: `now = timezone.localdate(); range_row, _ = NumberRange.objects.get_or_create(prefix=prefix, year=now.year, month=now.month, …)` — forms.py:59 (kein clean_issue_date) — views.py:2090
- **Szenario:** Am 03.09.2025 Rechnung mit Rechnungsdatum 15.01.2024 (Tippfehler) → Nummer RE2025090004, Fälligkeit 29.01.2024, sofort Mahnkandidat; Beleg landet im Monatsabschluss Januar 2024, der beim Steuerberater ist.
- **Auswirkung:** Chronologie zwischen Nummer und Datum nicht gewährleistet, Rückdatierung in abgeschlossene Perioden, Fehl-Mahnung.
- **Fix:** issue_date auf ein Fenster validieren; Rückdatierung in exportierte Monate mit Warnung; optional Nummernkreis-Periode aus issue_date ableiten.
- **Aufwand:** S · **Dimension:** gobd-geschaeftslogik · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-92 · [NIEDRIG] · Kassenbuch-Buchungsnummer per Check-then-act ohne Transaktion – IntegrityError (HTTP 500) bei gleichzeitiger Buchung; is_locked ohne Funktion
- **Wo:** sedo/services/numbering.py:141
- **Was:** next_cashbook_no liest die höchste booking_no des Monats; die Views speichern danach ohne transaction.atomic() und ohne Retry. unique_together schützt die Eindeutigkeit, der Fehler wird nicht behandelt. Der Bankimport erzeugt Einträge in einer Schleife ohne Transaktion. Das Feld is_locked wird nirgends gesetzt oder ausgewertet.
- **Beleg:** numbering.py:143-144: `last = CashbookEntry.objects.filter(month=month).order_by('-booking_no').first(); return (last.booking_no + 1) if last else 1`; views.py:1429-1430; views.py:1453; models.py:404: `unique_together = [('month', 'booking_no')]`; models.py:399: `is_locked = models.BooleanField(default=False)` (kein Treffer in views/services)
- **Szenario:** Beide Nutzer erfassen gleichzeitig eine Kassenbuchung: beide erhalten booking_no 17; der zweite save() wirft IntegrityError → Fehlerseite, Eingabe verloren.
- **Auswirkung:** Fehlerseite statt Meldung, verlorene Eingabe, unvollständiger Import; fehlende Festschreibung des Kassenbuchs trotz vorhandenem Feld.
- **Fix:** Vergabe in transaction.atomic() mit Retry oder NumberRange-Mechanik (Präfix 'KB'); Bankimport in eine Transaktion; is_locked beim Monatsabschluss setzen oder Feld entfernen.
- **Aufwand:** S · **Dimension:** datenmodell-migrationen · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-93 · [NIEDRIG] · Enter im Kundensuchfeld ohne Treffer springt im Assistenten zum nächsten Schritt statt die Suche zu behandeln
- **Wo:** sedo/static/sedo/customer_search.js:88
- **Was:** Der Keydown-Handler der Kundensuche bricht bei leerer Trefferliste ohne `preventDefault/stopPropagation` ab. Der Formular-Keydown-Handler des Assistenten wertet Enter in `type=search` als „Weiter“.
- **Beleg:** customer_search.js:86-88 `input.addEventListener('keydown', function (e) { var items = currentItems(); if (list.hidden || !items.length) return;` · wizard.js:25 `ENTER_TYPES = ['text','number','date','email','tel','url','search',...]` und Z.172-179 `e.preventDefault(); if (index < steps.length - 1) next();`.
- **Szenario:** Nutzer tippt einen Namen, der nicht als Kunde existiert, und drückt Enter. Der Assistent wechselt zu „Leistungsort“, ohne dass ein Kunde gewählt wurde.
- **Auswirkung:** Verwirrender Sprung; Nutzer glaubt ggf., der Kunde sei übernommen.
- **Fix:** Im Such-Keydown Enter immer abfangen, auch bei leerer Liste; alternativ im Wizard Enter für `#customer-search` ausnehmen.
- **Aufwand:** S · **Dimension:** frontend-js-templates_b · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-94 · [NIEDRIG] · Web-Speech-Fallback: Netzwerkfehler führt zu Endlos-Neustart mit Toast pro Versuch
- **Wo:** sedo/static/sedo/offer_ai.js:308
- **Was:** Im Fallback ohne Whisper setzt `onerror` bei `network` nur einen Toast, lässt `recording` aber auf `true`. `onend` startet daraufhin `recognition.start()` sofort neu; Chrome liefert bei fehlender Verbindung zu Googles Speech-Dienst erneut `network` → Schleife mit einem Fehler-Toast pro Zyklus.
- **Beleg:** offer_ai.js 298–299: `} else if (event.error === 'network') { notify('Netzwerkfehler bei der Spracherkennung. Bitte erneut versuchen.', 'error');` … 307–310: `recognition.onend = function () { if (recording && !stoppedByUser) { baseText = transcript.value; try { recognition.start(); return; } catch (e) { … } }`
- **Szenario:** Rechner ohne Internet, faster-whisper nicht installiert. Nutzer klickt „Diktat starten“ → im Sekundentakt neue rote Toasts, Aufnahme-Indikator läuft weiter.
- **Auswirkung:** Toast-Spam, unklare Bedienlage; kein Datenschaden.
- **Fix:** Bei `network`/`audio-capture`/`aborted` mit `stoppedByUser = true` beenden und `resetRecordBtn()` aufrufen, alternativ Retry-Zähler (max. 2).
- **Aufwand:** S · **Dimension:** frontend-js-templates_a · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-95 · [NIEDRIG] · KI-Befüllung reaktiviert Positionszeilen, die per „Löschen“-Haken markiert sind – Position verschwindet stillschweigend beim Speichern
- **Wo:** sedo/static/sedo/offer_ai.js:447
- **Was:** `nextEmptyRow()` wählt jede Zeile mit leerer Bezeichnung, auch solche mit gesetzter DELETE-Checkbox; `clearAllRows()` leert Werte, setzt den DELETE-Haken aber nicht zurück. Eine so befüllte Zeile wird vom Server beim Speichern verworfen, obwohl sie sichtbar mit KI-Werten gefüllt ist.
- **Beleg:** offer_ai.js:443-450 `function nextEmptyRow() { ... if (inp.d && !inp.d.value.trim()) return rows[i]; }` (keine DELETE-Prüfung) · Z.430-441 `clearAllRows` setzt nur d/lt/q/u/p/a, kein `-DELETE` · Formset mit `can_delete=True` (forms.py:131).
- **Szenario:** Nutzer legt 3 Positionen an, hakt bei Position 2 „Löschen“ an und leert deren Bezeichnung, startet dann „Angebot erzeugen“. Die KI füllt Position 2 wieder; die Zeile sieht gefüllt aus. Nach „Angebot erstellen“ fehlt die Position im PDF.
- **Auswirkung:** Angebot mit fehlender Leistung wird erzeugt und ggf. versendet.
- **Fix:** In `nextEmptyRow()` Zeilen mit gesetztem `-DELETE` überspringen und in `clearAllRows()`/`fillPositions()` den DELETE-Haken zurücksetzen.
- **Aufwand:** S · **Dimension:** frontend-js-templates_b · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-96 · [NIEDRIG] · KI-Preise, Mengen und Artikelnummern fließen ohne Sperre in Formularfelder; „genannte“ Preise ohne Prüfmarkierung, Aufteilung nicht gegen Gesamtpreis verifiziert
- **Wo:** sedo/static/sedo/offer_ai.js:518
- **Was:** `fillPositions` schreibt `einzelpreis`, `menge`, `einheit` und `artikel_nr` direkt in die Formset-Felder (offer_ai.js:514–519). Nur preis_art ≠ 'genannt' erhält die Prüfmarkierung; bei 'genannt' kein Abgleich mit Zahlen im Diktat. Bei 'aufgeteilt' kein Vergleich mit dem diktierten Gesamtpreis. `artikel_nr` wird ungeprüft übernommen. Zusätzlich ersetzt `apply_and_learn_service_catalog` den KI-Leistungstext serverseitig durch den Katalogtext bei `name__iexact`-Treffer (ai.py:400–405), ohne Markierung.
- **Beleg:** offer_ai.js:514–522: `setValue(inp.q, pos.menge || '1.00'); ... setValue(inp.p, pos.einzelpreis || ''); if (pos.artikel_nr) setValue(inp.a, pos.artikel_nr); var pruefen = pos.preis_art && pos.preis_art !== 'genannt';` · ai.py:403–404: `if pos.get('leistungstext') != existing.long_text: pos['leistungstext'] = existing.long_text`
- **Szenario:** Diktat „Beräumung 1.200 Euro, Entsorgung 300 Euro“. Modell liefert Entsorgung 3000 (Zahlendreher) mit 'genannt' → blau markiert, keine Warnung; Nutzer speichert 3000 €.
- **Auswirkung:** Falsche Angebotsbeträge/-texte; die Farbmarkierung suggeriert bei 'genannt' fälschlich Verlässlichkeit.
- **Fix:** Serverseitig prüfen, ob jeder 'genannt'-Preis im normalisierten Diktat vorkommt, sonst auf 'geschaetzt' herabstufen; `artikel_nr` nur bei Katalog-/Mapping-Treffer übernehmen; Katalogtext-Ersetzung als Hinweis ausgeben.
- **Aufwand:** M · **Dimension:** datenschutz-ki · **Gutachter-Notiz:** Strittig, vom Hauptreviewer entschieden: Kern bleibt: „genannt“-Preise werden nicht gegen das Diktat geprüft und blau als verlässlich markiert. Teilbehauptung „Katalog-Ersetzung unmarkiert“ ist falsch (wird gemeldet). Zusammengeführt mit #10.

### F-97 · [NIEDRIG] · Plausibilitäts-Check feuert beim Laden vorbefüllter Formulare, weil das Skript vor dem Wizard-Init läuft
- **Wo:** sedo/static/sedo/plausibility.js:141
- **Was:** Der Fallback „ohne Assistent einmal beim Laden prüfen“ testet `step.offsetParent !== null`. Zu diesem Zeitpunkt hat wizard.js (DOMContentLoaded) die Klasse `wizard-ready` noch nicht gesetzt, alle Schritte sind sichtbar – der Test ist immer wahr. Bei vorbefüllten Positionen geht deshalb sofort ein POST an /api/ki/plausibilitaet/, obwohl der Nutzer auf Schritt 1 steht; beim Erreichen von „Prüfen“ läuft er erneut.
- **Beleg:** plausibility.js:140-141 `if (!document.querySelector('[data-wizard]') || step.offsetParent !== null) runLocalCheck();` · app.css:375 `.wizard-ready .wizard-step{display:none}` · wizard.js:233 `document.addEventListener('DOMContentLoaded', …)` setzt `wizard-ready` erst dort; plausibility.js wird in invoice_form.html:554 als normales Skript vor DOMContentLoaded ausgeführt.
- **Szenario:** „Als Rechnung bearbeiten“ aus einem Angebot mit 8 Positionen: Beim Öffnen wird sofort der lokale Check gegen die Kundenhistorie ausgeführt; die Arbeit ist doppelt.
- **Auswirkung:** Überflüssige Requests und DB-Abfragen je Formularaufruf. Kein Datenfehler.
- **Fix:** Fallback auf `document.querySelector('[data-wizard]') === null` beschränken oder den Initialcheck nach dem Wizard in `DOMContentLoaded` verschieben.
- **Aufwand:** S · **Dimension:** frontend-js-templates_b · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-98 · [NIEDRIG] · Login-Seite ohne Viewport-Meta und ohne Cache-Busting – auf Handy via Tunnel unlesbar klein
- **Wo:** sedo/templates/registration/login.html:1
- **Was:** base.html setzt `<meta name="viewport">` und versionierte Assets, die eigenständige Login-Seite nicht. Die App ist zeitweise per Tunnel (auch mobil) erreichbar.
- **Beleg:** login.html:1 `<!doctype html><html lang="de"><head><meta charset="utf-8"><title>SEDO Login</title><link rel="stylesheet" href="/static/sedo/app.css"></head>` – kein viewport-Meta; base.html:5 `<meta name="viewport" content="width=device-width, initial-scale=1">`.
- **Szenario:** Nutzer öffnet die Tunnel-URL am Smartphone: Login-Formular wird in Desktop-Breite gerendert.
- **Auswirkung:** Erster Eindruck/Bedienbarkeit mobil; inkonsistent zur restlichen App.
- **Fix:** Login-Template von base.html-Head ableiten (Viewport, `app.css?v=…`, `autofocus`).
- **Aufwand:** S · **Dimension:** frontend-js-templates_b · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-99 · [NIEDRIG] · Backup verspricht „keine Bank-Zugangsdaten“ – Legitimations-ID, BLZ, FinTS-Endpunkt, IBAN und SMTP-Benutzer bleiben aber vollständig im Backup
- **Wo:** sedo/templates/sedo/backup.html:52
- **Was:** SECRET_SETTING_KEYS enthält nur `ai_api_key`, `smtp_password`, `fints_pin`. Die restlichen Bank-Zugangsdaten (`fints_login`, `fints_blz`, `fints_endpoint`, `fints_iban`, `fints_product_id`) und `smtp_user`/`smtp_host` werden mitgesichert. Oberfläche, BACKUP_INFO, DEPLOYMENT_SECURITY_NOTES und der Security-Status behaupten Gegenteiliges; der Security-Status-Check ist hart auf `ok: True`.
- **Beleg:** backup.html:52: `<li><strong>Nicht enthalten:</strong> .env, Secret Key, Server-Zugangsdaten oder Bank-Zugangsdaten</li>`; backup_utils.py:18: `SECRET_SETTING_KEYS = ('ai_api_key', 'smtp_password', 'fints_pin')`; views.py:2228–2231: `'label': '.env nicht im App-Backup', 'ok': True`.
- **Szenario:** Nutzer gibt ein Backup-ZIP im Vertrauen auf die Zusage weiter. Empfänger liest Legitimations-ID + BLZ + Endpunkt; zusammen mit einer anderweitig erlangten PIN ist der Online-Banking-Zugang vollständig.
- **Auswirkung:** Falsche Sicherheitszusage in vier Stellen; die Hälfte der Bank-Zugangsdaten verlässt das System mit jedem Backup.
- **Fix:** SECRET_SETTING_KEYS um `fints_login` erweitern oder alle Texte korrigieren; Security-Status-Check dynamisch aus SECRET_SETTING_KEYS ableiten; BACKUP_INFO.txt daraus generieren.
- **Aufwand:** S · **Dimension:** sicherheit-secrets-banking · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-100 · [NIEDRIG] · Toast fügt Meldungstext per innerHTML ein – Django-Messages mit Nutzereingaben werden als HTML interpretiert
- **Wo:** sedo/templates/sedo/base.html:140
- **Was:** `showToast` baut den Toast per Template-String und `innerHTML` und setzt den Text ungeschützt ein. Die Django-Messages werden nur mit `escapejs` in ein JS-Literal geschrieben – das schützt das Skript, nicht das HTML. Mehrere Messages enthalten Nutzereingaben oder Fremdtexte (views.py 938 `tmpl.title`, 586 Leistungsname, 1021 `recipient`, Exception-Texte aus SMTP/FinTS/Excel). Auch die KI-Skripte rufen `showToast(err.message)` mit Servertexten auf.
- **Beleg:** base.html 140–144: `toast.innerHTML = \`<span class="toast-icon">${ICONS[level]}</span><span class="toast-text">${text}</span>…\``; base.html 97: `window.__sedoMessages = [{% for message in messages %}{"text":"{{ message|escapejs }}",…}]`; views.py 938: `messages.success(request, f'Abo-/Monatsrechnungsvorlage „{tmpl.title}“ wurde gespeichert.')`.
- **Szenario:** Nutzer trägt als Vorlagenname `<img src=x onerror=alert(1)>` (oder harmlos `Reinigung <Haus A> & B`) ein. Nach dem Redirect erscheint der Toast: das Markup wird ausgeführt bzw. der Titel wird verstümmelt.
- **Auswirkung:** Skriptausführung im Kontext der App (Self-XSS); Fremdtexte aus SMTP-/Bank-/Excel-Fehlern landen ungefiltert im DOM.
- **Fix:** Toast per `document.createElement` aufbauen und `textContent` setzen; Messages mit `{{ messages|json_script }}` ausgeben.
- **Aufwand:** S · **Dimension:** frontend-js-templates_a · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-101 · [NIEDRIG] · Toasts sind die einzige Anzeige für Django-Fehlermeldungen und verschwinden nach 5–8 s – lange Fehlertexte (SMTP, FinTS, Import, Workflow) sind nicht nachlesbar
- **Wo:** sedo/templates/sedo/base.html:162
- **Was:** Alle `messages.*` werden ausschließlich als auto-schließende Toasts gerendert; es gibt keinen persistenten Meldungsbereich. views.py enthält 35 `messages.error/warning`-Aufrufe, darunter Meldungen mit Exceptiontext oder Handlungsanweisungen.
- **Beleg:** base.html:161-163 `const delay = level === 'error' ? 8000 : 5000; setTimeout(() => dismiss(toast), delay);` · Z.95-99: Messages nur in `window.__sedoMessages`, kein serverseitiges Fallback-Markup.
- **Szenario:** SMTP-Versand schlägt fehl; der Toast zeigt „E-Mail-Versand fehlgeschlagen: (535, …)“ und ist nach 8 s weg. Der Text ist nirgends mehr abrufbar.
- **Auswirkung:** Fehlerursachen gehen verloren; bei Toasts während eines Datei-Downloads sieht der Nutzer sie u. U. gar nicht.
- **Fix:** Fehler-/Warn-Toasts nicht automatisch schließen, oder zusätzlich einen Fallback-Block `{% for message in messages %}` im Content rendern.
- **Aufwand:** S · **Dimension:** frontend-js-templates_b · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-102 · [NIEDRIG] · Irreversible GoBD-relevante Aktionen (Mahnung, Erinnerung, Batch „Nächste Stufe für alle“) per Ein-Klick-Button ohne Bestätigungsdialog – nur Storno fragt nach
- **Wo:** sedo/templates/sedo/invoice_detail.html:16
- **Was:** Mahnschreiben/Erinnerungen erzeugen Dokumente und Statuswechsel, die nicht gelöscht werden; die Buttons stehen ohne `confirm()` direkt neben harmlosen Aktionen. Auf dem Dashboard löst ein Klick den Batch für alle Kandidaten aus.
- **Beleg:** invoice_detail.html:16-17 `<form method="post" action="{% url 'sedo:invoice_reminder' invoice.pk 'reminder' %}" …><button type="submit" class="btn">Zahlungserinnerung</button></form>` (kein onsubmit) · dashboard.html:21-24 `<button class="btn primary" type="submit">Nächste Stufe für alle</button>` · Einziges confirm: invoice_detail.html:18 (Storno).
- **Szenario:** Nutzer will „Zahlung erfassen“ und trifft daneben „Mahnung“: sofort entsteht ein Mahndokument, Status wechselt auf „gemahnt“; auf dem Dashboard erzeugt ein Fehlklick Mahnschreiben für alle 9 Kandidaten.
- **Auswirkung:** Ungewollte Mahnungen an Kunden, nicht rückgängig zu machende Belege.
- **Fix:** `onsubmit="return confirm('…')"` bzw. Zwischenseite mit Betrag/Stufe für Mahnung, Erinnerung und Batch; Batch-Button auf dem Dashboard durch Link zur Offene-Posten-Seite ersetzen.
- **Aufwand:** S · **Dimension:** frontend-js-templates_b · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-103 · [NIEDRIG] · Cache-Busting manuell und inkonsistent (gleiche Datei mit zwei Versions-Strings, login.css ohne Version)
- **Wo:** sedo/templates/sedo/invoice_detail.html:97
- **Was:** Die Versions-Suffixe sind handgepflegte Konstanten je Template: base.html (`?v=3_23`, `?v=2_1`), invoice_form.html 552 `magic_tone.js?v=3_23`, invoice_detail.html 97 `magic_tone.js?v=3_21` (dieselbe Datei, zwei URLs), login.html lädt `app.css` ohne Version; offer_form.html mischt `{% static %}` und hartes `/static/…`. WhiteNoise liefert ohne Manifest-Storage `Cache-Control: max-age=60`.
- **Beleg:** invoice_detail.html 97: `<script src="/static/sedo/magic_tone.js?v=3_21"></script>`; invoice_form.html 552: `<script src="/static/sedo/magic_tone.js?v=3_23"></script>`; login.html: `<link rel="stylesheet" href="/static/sedo/app.css">`; settings.py 166–167: `WHITENOISE_USE_FINDERS = True`, `WHITENOISE_AUTOREFRESH = True`.
- **Szenario:** Update ändert wizard.js und base.html, der Entwickler vergisst `?v=` hochzuzählen → in der ersten Minute nach Neustart läuft altes JS gegen neues Markup.
- **Auswirkung:** Gering (60 s Revalidierung), aber schwer diagnostizierbare Sporadikfehler direkt nach Updates.
- **Fix:** Einheitlich `{% static %}` nutzen und die Version zentral bereitstellen (Context-Processor `STATIC_VERSION`), oder Template-Tag mit Datei-Hash; login.html einbeziehen.
- **Aufwand:** S · **Dimension:** frontend-js-templates_a · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-104 · [NIEDRIG] · Barrierefreiheit: Labels ohne Zuordnung, Suchfeld und Checkboxen ohne Beschriftung
- **Wo:** sedo/templates/sedo/invoice_form.html:57
- **Was:** Durchgängiges Muster `<label>{{ form.x.label }}</label>{{ form.x }}` ohne `for`-Attribut und ohne Umschließen in invoice_form, offer_form, recurring_template_form, service_catalog_form, customer_form, object_form, payment_manual. Kundensuche `#customer-search` hat nur Placeholder. Checkboxen `invoice_ids`, `template_ids` und `DELETE` haben keinen zugänglichen Namen. Ein Fund für das Muster.
- **Beleg:** invoice_form.html 57: `<div><label>{{ form.name.label }} *</label>{{ form.name }}{{ form.name.errors }}</div>`; invoice_form.html 42: `<input type="search" id="customer-search" placeholder="Kunden suchen…" autocomplete="off">`; open_items.html 75: `<input type="checkbox" name="invoice_ids" value="{{ i.pk|unlocalize }}" class="row-check">`
- **Szenario:** Screenreader-Nutzer im Rechnungsformular hört „Eingabefeld, leer“ ohne Feldnamen; Klick auf die Beschriftung fokussiert das Feld nicht.
- **Auswirkung:** Eingeschränkte Bedienbarkeit für assistive Technik; kein funktionaler Schaden.
- **Fix:** `<label for="{{ form.name.id_for_label }}">`, `aria-label` für Suchfeld und Checkboxen.
- **Aufwand:** M · **Dimension:** frontend-js-templates_a · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-105 · [NIEDRIG] · Vorbefüllte Formulare (?customer=, Angebot, Korrektur, Fehler-Rerender) haben keinen JS-Zustand: Kunden-Pill fehlt, Objektliste ungefiltert mit irreführendem Hinweis, Objektwahl blankt Adressfelder
- **Wo:** sedo/templates/sedo/invoice_form.html:374
- **Was:** Objektfilterung, Pill und Adress-Autofill hängen ausschließlich am `change`-Event der Kundenauswahl. Serverseitig vorbelegte Selects lösen es nicht aus; die serverseitig gerenderten `<option>`s des Objekt-Selects tragen keine `data-*`-Attribute. Der Hinweis „Wähle zuerst einen Kunden“ steht dann neben einer Liste aller Objekte aller Kunden. Gleiches in offer_form.html.
- **Beleg:** invoice_form.html:374-378 `fill('id_object_name', opt.dataset.name || opt.text.split(' – ')[0]); fill('id_object_street', opt.dataset.street); …` · Z.246-252 `fill()` schreibt `''` bei `undefined` · Z.343-365 `updateObjectDropdown` nur aus dem Kunden-`change`-Handler · forms.py:29-33 `obj` Queryset = alle CustomerObjects.
- **Szenario:** Aus der Kundenakte „Rechnung“ klicken (?customer=…): Schritt 1 zeigt gefüllte Kopfdaten, aber keine Kunden-Pill; Schritt 2 listet alle Objekte (auch fremder Kunden). Wählt der Nutzer sein Objekt, werden Straße/PLZ/Ort auf leer gesetzt.
- **Auswirkung:** Fehlleitende UI in genau den Einstiegen, die Zeit sparen sollen; erhöht die Wahrscheinlichkeit des Fremd-Objekt-Fehlers.
- **Fix:** Beim Laden, wenn `#id_customer` einen Wert hat, den Kunden-`change`-Handler einmal auslösen; Objekt-Queryset serverseitig auf den Kunden einschränken.
- **Aufwand:** M · **Dimension:** frontend-js-templates_b · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-106 · [NIEDRIG] · Angebotsdetail: Spalte „Gesamt“ zeigt keinen Zeilenbetrag, sondern „Menge × Einzelpreis“
- **Wo:** sedo/templates/sedo/offer_detail.html:4
- **Was:** OfferItem hat – anders als InvoiceItem (models.py:230) – keine `line_total`-Property; das Template füllt die „Gesamt“-Spalte mit dem Ausdruck statt dem Betrag.
- **Beleg:** offer_detail.html:4 `<th>Gesamt</th> … <td>{{ p.quantity|floatformat:2 }} × {{ p.unit_price }} €</td>`.
- **Szenario:** Angebot mit 12,5 m² × 4,80 €: In der Spalte „Gesamt“ steht „12,50 × 4,80 €“ statt „60,00 €“.
- **Auswirkung:** Irreführende Tabellenüberschrift, erschwerte Kontrolle vor „Angenommen“.
- **Fix:** `line_total`-Property analog InvoiceItem an OfferItem ergänzen und im Template ausgeben.
- **Aufwand:** S · **Dimension:** frontend-js-templates_b · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-107 · [NIEDRIG] · Private E-Mail-Adresse des Entwicklers als Platzhalter im Einstellungs-Formular ausgeliefert
- **Wo:** sedo/templates/sedo/settings.html:60
- **Was:** Das Feld „Test-E-Mail senden an“ zeigt eine persönliche Gmail-Adresse als `placeholder` (nicht die Firmenadresse wie in Z.33). Die Adresse wird an alle Nutzer/Installationen ausgeliefert.
- **Beleg:** settings.html:60 `<input name="test_email" type="email" placeholder="…@gmail.com">` (Adresse hier bewusst nicht wiedergegeben).
- **Szenario:** Ein Mitarbeiter öffnet Einstellungen → SMTP-Test; der Platzhalter suggeriert eine Zieladresse und landet ggf. per Klick/Autofill als Testempfänger.
- **Auswirkung:** Unnötige Weitergabe einer privaten Adresse in einem Kundenprodukt; Verwechslungsgefahr.
- **Fix:** Neutralen Platzhalter (z. B. `name@firma.de`) verwenden oder mit der konfigurierten Absenderadresse vorbelegen.
- **Aufwand:** S · **Dimension:** frontend-js-templates_b · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-108 · [NIEDRIG] · Testlücken: Backup-Zyklus, Middleware, Desktop-Starter, pushTAN-Timeout und Whisper-Fallback ungetestet
- **Wo:** sedo/tests/test_security_hardening.py:81
- **Was:** 245 Tests laufen grün (1 Skip). Nicht abgedeckt: ensure_weekly_backup()/WeeklyBackupMiddleware, create_backup mit datei-basierter DB, Restore-Logik (existiert nicht), desktop.py, `_await_decoupled`-Timeout und die Startskripte. Der einzige Skip betrifft genau den Whisper-Fallback-Pfad.
- **Beleg:** Testlauf: `Ran 245 tests in 20.554s – OK (skipped=1)` · sedo/tests/test_transcription.py:46 `self.skipTest('faster-whisper ist hier installiert – Fallback-Pfad nicht testbar.')` · grep 'WeeklyBackup|ensure_weekly_backup' in sedo/tests → 0 Treffer · grep 'desktop' in sedo/tests → 0 Treffer · test_banking.py: kein Test für `_await_decoupled`
- **Szenario:** Eine Änderung an der Zeitlogik in ensure_weekly_backup bricht Backups still; die Suite bleibt grün. Ein Regressionsfehler im 503-Fallback der Transkription bliebe unbemerkt.
- **Auswirkung:** Gerade die Rettungspfade (Backup/Restore, Erststart im Bundle) sind ohne Regressionsschutz.
- **Fix:** Tests mit tempdir für backup_dir, Zeitreise für ensure_weekly_backup, datei-basierte Test-DB für create_backup-Inhalt; `_await_decoupled` mit gepatchtem time; Whisper-Fallback per patch.
- **Aufwand:** M · **Dimension:** tests-konfig-deployment · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-109 · [NIEDRIG] · Vorschau rundet anders (ROUND_HALF_EVEN) als die gespeicherte Rechnung (ROUND_HALF_UP)
- **Wo:** sedo/views.py:274
- **Was:** _preview_from_forms quantisiert Zeilensumme und USt ohne rounding-Argument (Decimal-Default HALF_EVEN); services/calc.py nutzt ROUND_HALF_UP. Bei Netto-Beträgen mit x,50 € und bei Produkten auf halbe Cent differiert die Vorschau um 1 Cent.
- **Beleg:** views.py 262: `line_total = (qty * price).quantize(Decimal('0.01'))`; 274: `vat = (net * vat_rate / Decimal('100')).quantize(Decimal('0.01'))`; calc.py 22: `vat = (net * vat_rate / Decimal('100')).quantize(CENT, rounding=ROUND_HALF_UP)`; geprüft: Netto 3,50 € → Vorschau USt 0,66 €, Rechnung 0,67 €
- **Szenario:** Position 1 × 3,50 € netto: Vorschau zeigt Brutto 4,16 €, erzeugte Rechnung 4,17 €.
- **Auswirkung:** Vorschau-Betrag stimmt nicht mit Beleg überein.
- **Fix:** In _preview_from_forms `_totals()` aus services.calc wiederverwenden bzw. `rounding=ROUND_HALF_UP` ergänzen.
- **Aufwand:** S · **Dimension:** funktionalitaet-views_a · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-110 · [NIEDRIG] · KI-Begleittext: Rechnungsnummer, Betrag und Fälligkeit werden nicht gegen die Rechnung rückgeprüft – bei fehlendem Fälligkeitsdatum zwingt der Prompt zum Erfinden
- **Wo:** sedo/views.py:464
- **Was:** _invoice_ai_context schreibt bei due_date = NULL den Platzhalter „siehe Rechnung“ in die Eckdaten, während der System-Prompt verlangt „Fälligkeitsdatum korrekt nennen“ und „keine Platzhalter“. Der zurückgelieferte Text wird nirgends geprüft, bevor er als E-Mail-Body an den Kunden geht.
- **Beleg:** views.py:464 `f'Fällig bis: {invoice.due_date.strftime("%d.%m.%Y") if invoice.due_date else "siehe Rechnung"}',`; ai.py:522-523 `'2. ... keine Platzhalter.\n' '3. Rechnungsnummer, Betrag und Fälligkeitsdatum aus den Eckdaten korrekt nennen.\n'`; views.py:1008-1010 `custom_body = ...; if custom_body: body = custom_body` (keine Prüfung)
- **Szenario:** Rechnung ohne Fälligkeitsdatum → Claude formuliert „zahlbar bis 30.09.2026“ (frei erfunden) → Kunde erhält falsches Zahlungsziel in der Begleitmail.
- **Auswirkung:** Widersprüchliche Zahlungsangaben zwischen E-Mail und Rechnung.
- **Fix:** Bei fehlendem Fälligkeitsdatum die Zeile weglassen und kein Datum erlauben; nach der Generierung prüfen, dass invoice.number vorkommt und keine fremden Datums-/Betragsangaben enthalten sind.
- **Aufwand:** S · **Dimension:** datenschutz-ki · **Gutachter-Notiz:** Strittig, vom Hauptreviewer entschieden: Der Prompt-Widerspruch („keine Platzhalter“ vs. Eckdatum „siehe Rechnung“) ist konkret und in einer Zeile behebbar; Nutzer prüft den Text vor Versand, daher niedrig.

### F-111 · [NIEDRIG] · Positionsbezeichnungen und Rechnungsnummer werden als „Leistungsdaten“ übertragen, sind aber Freitext mit typischem Objekt-/Leistungsortbezug
- **Wo:** sedo/views.py:471
- **Was:** `_invoice_ai_context` sendet `invoice.number` und die ersten 10 `item.description` (views.py:462, 471); `ai_review_positions` sendet `bezeichnung` aller Formularpositionen (ai.py:624–630). `description` ist ein freies CharField; im Rechnungsalltag einer Gebäudereinigung steht dort häufig der Leistungsort. Keine Prüfung auf Objekt-/Personendaten. Der bestehende Test prüft nur Modellfelder, nicht den Inhalt der Positionen.
- **Beleg:** views.py:461–471: `lines = [f'Rechnungsnummer: {invoice.number}', ...]; ... lines.extend(f'- {item.description}' for item in items)` · ai.py:624–630: `desc = _ai_clean_str(pos.get('bezeichnung'), 200) ...` · views.py:1595: `desc = request.POST.get(f'desc_{sid}') or sid`
- **Szenario:** Rechnung mit Positionen „Unterhaltsreinigung Objekt Gartenweg 7 (Fam. Krause)“. Klick auf „Begleittext erzeugen“ oder „KI-Check starten“ überträgt Adresse und Bewohnername an Anthropic.
- **Auswirkung:** Objekt-/Adressdaten verlassen das System, obwohl UI (invoice_detail.html:81) und Docstrings das Gegenteil zusichern.
- **Fix:** Bezeichnungen vor der Übertragung durch den PII-Filter schicken; Rechnungsnummer durch Platzhalter ersetzen und serverseitig einsetzen. Test um Positionsinhalte erweitern.
- **Aufwand:** M · **Dimension:** datenschutz-ki · **Gutachter-Notiz:** Strittig, vom Hauptreviewer entschieden: Positionsbezeichnungen tragen in der Gebäudereinigung routinemäßig Leistungsorte – gehört als Stellenliste zum bestätigten Redaktions-Fund (ai.py:433); UI-Hinweis ist vorhanden, daher niedrig.

### F-112 · [NIEDRIG] · Listenansichten ohne Limit/Pagination (Rechnungen, Angebote, Kunden, Leistungskatalog, Monatsabschluss)
- **Wo:** sedo/views.py:754
- **Was:** invoice_list, offer_list, customer_list, service_catalog_list und month_close rendern den kompletten Bestand ohne Paginierung; open_items/payment_list/audit_log sind gekappt, was inkonsistent ist.
- **Beleg:** views.py 754: `invoices = Invoice.objects.select_related('customer', 'obj').all()` … 774: `'invoices': invoices,` (kein Slice/Paginator)
- **Szenario:** Nach 3 Jahren liegen >5.000 Rechnungen vor; „Rechnungsarchiv“ ohne Filter rendert 5.000 Zeilen mit Formular pro Zeile → Seite mehrere MB.
- **Auswirkung:** Träge Oberfläche; kein Datenfehler.
- **Fix:** Paginator (z. B. 100/Seite) oder Standardfilter „aktuelles Jahr“.
- **Aufwand:** S · **Dimension:** funktionalitaet-views_a · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-113 · [NIEDRIG] · Nicht-numerische ID-/Monatsparameter führen zu ValueError/TypeError (500) statt 404/Meldung
- **Wo:** sedo/views.py:798
- **Was:** get_object_or_404(…, pk='abc') und `.filter(pk=…)`/`id__in=[…]` werfen bei nicht-numerischen Werten ValueError, den Django nicht in 404 übersetzt. Betroffen: invoice_create GET-Parameter from_offer/correction_of/customer (Z. 798, 816, 832, 960), ai_cover_letter `Invoice.objects.filter(pk=data.get('invoice'))` (Z. 488), aareon_import send_monthly_email `id__in=ids` (Z. 1687), recurring_monthly_generate `id__in=selected_ids` (Z. 1939), bulk_dunning ids (dunning.py 210), cashbook_pdf month mit „/“ (Z. 1481).
- **Beleg:** views.py 798: `offer = get_object_or_404(Offer.objects.select_related('customer','obj'), pk=pre_offer)`; 488: `invoice = Invoice.objects.filter(pk=data.get('invoice')).first()`
- **Szenario:** Ein Link mit `?customer=12a` oder ein JS-Fehler, der `invoice: null` sendet → Django-Fehlerseite bzw. 500-JSON.
- **Auswirkung:** Fehlerseiten statt sauberer Rückmeldung; nur durch fehlerhafte Links/Clients auslösbar.
- **Fix:** Parameter mit `str(x).isdigit()` prüfen bzw. ValueError/TypeError abfangen und Http404/JsonResponse(400) liefern.
- **Aufwand:** S · **Dimension:** funktionalitaet-views_b · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-114 · [NIEDRIG] · Kunde und Objekt werden vor der Transaktion angelegt – bei Rollback bleiben verwaiste Stammdaten, Doppel-Submit erzeugt doppelte Kunden
- **Wo:** sedo/views.py:852
- **Was:** In invoice_create wird _customer_from_invoice_form/_object_from_invoice_form (Customer.objects.create) aufgerufen, bevor `with transaction.atomic()` beginnt. Schlägt die Beleganlage fehl, bleibt ein neuer Kunde ohne Rechnung stehen. Bei Mehrfach-Submit mit manuellen Kopfdaten entsteht bei jedem Submit ein weiterer gleichnamiger Kunde. offer_create hat dasselbe Muster.
- **Beleg:** views.py:852-856: `customer = _customer_from_invoice_form(form)` … `obj = _object_from_invoice_form(form, customer)` · views.py:892: `with transaction.atomic():` (erst danach) · views.py:217: `return Customer.objects.create(…)` ohne get_or_create.
- **Szenario:** Nutzer füllt Kopfdaten für einen Neukunden aus und klickt zweimal → zwei Kunden „Muster GmbH“, einer ohne Belege.
- **Auswirkung:** Stammdaten-Dubletten, verwaiste Kunden/Objekte.
- **Fix:** Kunden-/Objektanlage in den atomic-Block ziehen; get_or_create über (customer_no) bzw. (name, postcode).
- **Aufwand:** S · **Dimension:** funktionalitaet-views_b · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-115 · [NIEDRIG] · „Erstellen & versenden“ versendet nie per SMTP, setzt aber Status 'versendet' vor der .eml-Erzeugung – UI verspricht Direktversand
- **Wo:** sedo/views.py:939
- **Was:** invoice_create action='send' erzeugt immer nur eine .eml-Datei, obwohl invoice_email bei konfiguriertem SMTP direkt sendet. Das Formular sagt bei konfiguriertem SMTP „sendet direkt“. Der Status wird auf 'versendet' gesetzt, bevor die .eml existiert; schlägt die Erzeugung fehl, bleibt 'versendet' ohne Datei.
- **Beleg:** views.py 943-944: `inv.status = 'versendet'; inv.save(update_fields=['status','updated_at'])\n eml_path = _create_invoice_email_file(inv)`; invoice_form.html 191: `„Erstellen &amp; versenden“ sendet die Rechnung direkt{% if not smtp_configured %} (SMTP noch nicht konfiguriert – .eml-Download){% endif %}`
- **Szenario:** SMTP eingerichtet. Nutzer klickt „Erstellen & versenden“, erhält einen .eml-Download (ignoriert ihn), Rechnung steht auf „Versendet“ – der Kunde hat nie eine E-Mail erhalten.
- **Auswirkung:** Rechnungen gelten als versendet, wurden aber nicht zugestellt.
- **Fix:** Im send-Zweig denselben Pfad wie invoice_email nutzen und den Status erst nach erfolgreichem Versand setzen; UI-Text angleichen.
- **Aufwand:** S · **Dimension:** funktionalitaet-views_b · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-116 · [NIEDRIG] · Rechnung aus abgelehntem/abgelaufenem Angebot möglich; Status eines bereits verrechneten Angebots kann auf „abgelehnt“/„offen“ zurückgesetzt werden
- **Wo:** sedo/views.py:1164
- **Was:** offer_to_invoice und create_invoice_from_offer prüfen nur linked_invoice/status 'in_rechnung', nicht ob das Angebot angenommen und noch gültig ist. offer_status setzt ohne Prüfung jeden der drei Status, auch bei Angeboten mit linked_invoice.
- **Beleg:** invoices.py:268-274 (keine Prüfung 'angenommen'/valid_until) — views.py:1154-1156: `if status in ['offen', 'angenommen', 'abgelehnt']: offer.status = status; offer.save(...)` — offer_detail.html:2: Buttons ohne Statusbedingung
- **Szenario:** Abgelehntes Angebot (valid_until 15.08.) → am 20.09. „Direkt Rechnung erzeugen“ → nummerierte Rechnung entsteht, muss storniert werden. Umgekehrt: verrechnetes Angebot → „Abgelehnt“ → Status 'abgelehnt' mit verknüpfter Rechnung.
- **Auswirkung:** Unnötige Storno-Vorgänge im Nummernkreis, inkonsistente Angebotsstatistik.
- **Fix:** ensure_offer_invoicing_allowed um status == 'angenommen' und valid_until ≥ heute erweitern; offer_status bei linked_invoice verweigern.
- **Aufwand:** S · **Dimension:** gobd-geschaeftslogik · **Gutachter-Notiz:** Strittig, vom Hauptreviewer entschieden: Rechnung aus offenem Angebot ist per Test gewollt (Teil 1 verworfen). Bleibt: Status eines bereits verrechneten Angebots lässt sich auf „abgelehnt“/„offen“ zurücksetzen – kleine Inkonsistenz, S.

### F-117 · [NIEDRIG] · N+1 und Schreibzugriffe auf GET in Offene Posten/Dashboard/Zahlungen: prefetch_related('payments') wird durch .filter() umgangen
- **Wo:** sedo/views.py:1204
- **Was:** Invoice.paid_amount ruft `self.payments.filter(deleted_at__isnull=True)` auf – filter() auf einer Rückwärtsrelation ignoriert den Prefetch. open_items rendert pro Zeile paid_amount, open_amount, is_overdue, days_overdue → ~5 Queries je Rechnung, dazu refresh_invoice_payment_status je Rechnung (Aggregat + UPDATE mit updated_at, auch ohne Änderung). Gleiches Muster: dashboard, payment_list, get_dunning_candidates.
- **Beleg:** views.py 1192: `.prefetch_related('payments','dunning_documents')`; 1204-1205: `for inv in invoice_list:\n refresh_invoice_payment_status(inv)`; models.py 181: `active_payments = self.payments.filter(deleted_at__isnull=True)`; calc.py 60: `invoice.save(update_fields=['status', 'updated_at'])`
- **Szenario:** Nach zwei Jahren GWG-Monatsläufen hat der Filter „Alle ohne Storno“ 500 Zeilen → ca. 3.500 SELECTs + 500 UPDATEs pro Aufruf auf SQLite.
- **Auswirkung:** Langsame Seiten, unnötige Schreibvorgänge bei GET.
- **Fix:** paid_amount über prefetchte Objekte summieren oder Prefetch mit gefiltertem Queryset; Status-Refresh nur bei Änderung speichern und aus dem GET herausnehmen.
- **Aufwand:** M · **Dimension:** funktionalitaet-views_a · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-118 · [NIEDRIG] · Offener Redirect über POST-Parameter „next“ bei Zahlungserfassung
- **Wo:** sedo/views.py:1259; sedo/templates/sedo/payment_manual.html:10
- **Was:** invoice_add_payment und invoice_mark_paid leiten auf request.POST['next'] weiter, ohne Host/Schema zu prüfen. payment_manual.html befüllt next mit HTTP_REFERER.
- **Beleg:** views.py 1259-1260: `next_url = request.POST.get('next') or reverse('sedo:invoice_detail', args=[invoice.pk])\n return redirect(next_url)`; 1291-1292 identisch
- **Szenario:** Nutzer kommt über einen externen Link auf „Zahlung erfassen“ → nach dem Speichern Redirect auf die fremde Seite.
- **Auswirkung:** Geringer Angriffspfad (CSRF-geschützter POST nötig), aber verwirrende Weiterleitung.
- **Fix:** `url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()})` prüfen.
- **Gleiche Ursache (frontend-js-templates_b, sedo/templates/sedo/payment_manual.html:10):** `next`-Redirect aus HTTP_REFERER ungeprüft übernommen (Open Redirect nach Zahlungserfassung) – `url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()})` prüfen; statt Referer expliziten `?next=`-Parameter aus eigenen Links.
- **Aufwand:** S · **Dimension:** funktionalitaet-views_b · **Gutachter-Notiz:** Zusammengeführt aus 2 Rohfunden (funktionalitaet-views_b + frontend-js-templates_b); jeder Rohfund von zwei Gutachtern bestätigt.

### F-119 · [NIEDRIG] · Kassenbuch-PDF: zustandsändernder GET-Endpunkt schreibt pro beliebigem month-Wert eine Datei nach media/ und liefert bei '/' im Wert HTTP 500
- **Wo:** sedo/views.py:1479
- **Was:** `cashbook_pdf` ist ein GET-Endpunkt mit Seiteneffekt: er erzeugt bei jedem Aufruf eine neue PDF unter `media/cashbook/Kassenbuch_{month}.pdf`, wobei `month` ungeprüft aus dem Query-String kommt (kein Regex, keine Längenbegrenzung).
- **Beleg:** sedo/views.py:1480 `month = request.GET.get('month') or timezone.localdate().strftime('%Y-%m')`; :1481 `path = generate_cashbook_pdf(month)`; sedo/services/pdf.py:237 `pdf_path = out_dir / f'Kassenbuch_{month}.pdf'`
- **Szenario:** (a) `/kassenbuch/pdf/?month=2025/09` → Zielpfad mit nicht existierendem Unterverzeichnis → reportlab wirft beim Speichern → HTTP 500. (b) Wiederholte Navigationen auf `/kassenbuch/pdf/?month=<zufall>` → pro Aufruf eine neue, nie gelöschte Datei in media/, die in jedes Backup-ZIP wandert.
- **Auswirkung:** 500er und unbegrenztes Anwachsen von media/ und Backups durch einen nicht validierten GET-Parameter; kein Traversal über media/ hinaus (Präfix `Kassenbuch_`).
- **Fix:** `month` gegen `^\d{4}-(0[1-9]|1[0-2])$` prüfen – zentral in `_parse_month()`; PDF in Temp-Verzeichnis rendern und per FileResponse streamen statt persistent in media/ zu schreiben.
- **Aufwand:** S · **Dimension:** sicherheit-web · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-120 · [NIEDRIG] · Irreführende Rückmeldungen/Vorbelegungen: ZIP-Versand zählt fehlende PDFs mit, Importe ohne Datei bleiben stumm, Zahlungsvorschlag bei bezahlter Rechnung
- **Wo:** sedo/views.py:1524
- **Was:** (a) _monthly_zip_email überspringt Rechnungen ohne zugferd_file still, schreibt aber „Enthaltene Rechnungen: {len(invoices)}“. (b) customer_import und cashbook_import_bank ohne Datei enden ohne Meldung. (c) invoice_add_payment schlägt bei vollständig bezahlter Rechnung den vollen Bruttobetrag vor. (d) payment_list zeigt gekappte Anzahl.
- **Beleg:** views.py 1512-1513: `if not inv.zugferd_file:\n continue`; 1524: `f'Enthaltene Rechnungen: {len(invoices)}\n\n'`; 1264: `'amount': invoice.open_amount if invoice.open_amount > Decimal('0.00') else invoice.gross_total,`
- **Szenario:** Nutzer erfasst zu einer bezahlten Rechnung erneut eine Zahlung: Formular schlägt 1.190,00 € vor → Überzahlung.
- **Auswirkung:** Fehlbuchungen durch falsche Vorbelegung, verwirrende Zahlen.
- **Fix:** Vorbelegung 0,00 € bei open_amount ≤ 0; messages.error bei fehlender Datei; gezippte Anzahl im E-Mail-Text.
- **Aufwand:** S · **Dimension:** funktionalitaet-views_b · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-121 · [NIEDRIG] · Gespeichertes SMTP-Passwort kann über die Oberfläche nicht gelöscht werden – kein Pendant zu ai_api_key_clear / fints_pin_clear
- **Wo:** sedo/views.py:2006
- **Was:** Beim Speichern der SMTP-Einstellungen gilt „leeres Feld = unverändert“. Anders als bei API-Key und Bank-PIN gibt es keine Checkbox zum Löschen. Wer das Passwort aus der DB entfernen will, kann das nur über den Django-Admin.
- **Beleg:** views.py:2005–2008: `new_pw = request.POST.get('smtp_password', '')` / `if new_pw: AppSetting.set('smtp_password', new_pw)`; settings.html:37–38: nur Passwortfeld mit Hinweis „Leer lassen, um das bestehende Passwort zu behalten“.
- **Szenario:** Nutzer trägt das Passwort in die .env ein und leert das Feld → Klartext-Passwort bleibt in sedo_appsetting stehen.
- **Auswirkung:** Secret bleibt unbeabsichtigt dauerhaft in der DB; inkonsistente Bedienung.
- **Fix:** Checkbox `smtp_password_clear` analog zu `ai_api_key_clear` ergänzen und in `save_smtp` auswerten.
- **Aufwand:** S · **Dimension:** sicherheit-secrets-banking · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-122 · [NIEDRIG] · PIN/Passwort-Formulare ohne sensitive_post_parameters; kompletter Banking-Config inkl. PIN im Template-Kontext
- **Wo:** sedo/views.py:2014
- **Was:** Die Views settings (save_banking/save_smtp/save_ai) und payment_list (fints_pin_once) sind nicht mit @sensitive_post_parameters markiert. Bei DJANGO_DEBUG=1 zeigt die technische 500-Seite die POST-Daten mit PIN/Passwort im Klartext. Zusätzlich übergibt Z. 2070 `get_banking_config()` komplett – inklusive 'pin' – an das Template.
- **Beleg:** 'banking_cfg': get_banking_config(), # enthält cfg['pin'] … fetch_bank_transactions(days=days, pin=request.POST.get('fints_pin_once', '')) # View ohne @sensitive_post_parameters
- **Szenario:** Entwicklung mit DJANGO_DEBUG=1: Nutzer speichert Bankzugang, AppSetting.set wirft OperationalError → Debug-Seite listet `fints_pin: 12345` unter „POST“.
- **Auswirkung:** Klartext-Secrets in Fehlerseiten/Reports; unnötig breiter Kontext für Templates.
- **Fix:** `@sensitive_post_parameters('fints_pin', 'fints_pin_once', 'smtp_password', 'ai_api_key')` auf beide Views; nur nicht-geheime Felder ans Template.
- **Aufwand:** S · **Dimension:** sicherheit-secrets-banking · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-123 · [NIEDRIG] · FinTS-Endpunkt wird ohne https-Pflicht gespeichert – PIN ginge bei http-URL im Klartext
- **Wo:** sedo/views.py:2016
- **Was:** fints_endpoint wird ungeprüft übernommen. python-fints postet an die URL, wie sie ist, ohne https zu erzwingen; der PIN/TAN-Mechanismus verschlüsselt die Nachricht selbst nicht, TLS ist der einzige Schutz.
- **Beleg:** AppSetting.set('fints_endpoint', request.POST.get('fints_endpoint', '').strip())
- **Szenario:** Nutzer tippt die Bankadresse als `http://banking-…/fints30` → erster Dialog-Init mit Login + PIN wird unverschlüsselt gesendet.
- **Auswirkung:** Klartextübertragung der Online-Banking-PIN.
- **Fix:** In save_banking `if not endpoint.startswith('https://'): messages.error(...)` – kein Speichern ohne https.
- **Aufwand:** S · **Dimension:** sicherheit-secrets-banking · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-124 · [NIEDRIG] · Gespeicherte Bank-PIN wird ungetrimmt gespeichert und nach einem PIN-Fehler nie invalidiert – drei Abrufe sperren das Online-Banking
- **Wo:** sedo/views.py:2025
- **Was:** Die Einmal-PIN wird gestrippt, die dauerhaft gespeicherte nicht. Eine per Copy-Paste eingefügte PIN mit Zeilenumbruch wird so gespeichert und bei jedem Abruf an die Bank geschickt. python-fints blockiert eine als falsch erkannte PIN nur innerhalb derselben Client-Instanz; SEDO erzeugt pro Abruf einen neuen Client, verwirft die gespeicherte PIN nach einem FinTSClientPINError nicht und zeigt nur „Bankabruf fehlgeschlagen: … PIN wrong?“. Jeder erneute Klick ist ein weiterer Fehlversuch bei der Bank.
- **Beleg:** views.py:2024–2025: `AppSetting.set('fints_pin', request.POST.get('fints_pin', ''))` (kein strip); banking.py:134: `use_pin = (pin or '').strip() or cfg['pin']`; banking.py:150: neue Instanz je Abruf; banking.py:193–195: generisches `except Exception`.
- **Szenario:** Nutzer kopiert die PIN mit angehängtem Zeilenumbruch → Abruf 1: PIN falsch → Nutzer klickt noch zweimal → Bank sperrt den Zugang (3938).
- **Auswirkung:** Sperre des Online-Bankings der Firma durch drei Klicks; keine Warnung.
- **Fix:** PIN beim Speichern strippen und plausibilisieren. FinTSClientPINError gezielt fangen: gespeicherte PIN löschen bzw. `fints_pin_invalid` setzen, klare Meldung „NICHT erneut versuchen“, Abruf-Button sperren.
- **Aufwand:** S · **Dimension:** sicherheit-secrets-banking · **Gutachter-Notiz:** Beide Gutachter bestätigen.

### F-125 · [NIEDRIG] · Sicherheitsstatus-Seite meldet „Bankanbindung vorbereitet“ trotz fehlgeschlagenem Passwort-Check und enthält einen hart auf ok=True gesetzten Prüfpunkt
- **Wo:** sedo/views.py:2241
- **Was:** Die Seite /sicherheit/ ist laut DEPLOYMENT_SECURITY_NOTES die Freigabe-Grundlage für die Bankanbindung. `bank_ready` toleriert jedoch genau einen fehlgeschlagenen Check, und der Check „.env nicht im App-Backup“ ist statisch `ok: True` – er prüft nichts.
- **Beleg:** sedo/views.py:2227-2232 `{'label': '.env nicht im App-Backup', 'ok': True, 'value': 'ausgeschlossen', ...}`; :2241 `'bank_ready': ok_count >= len(checks) - 1`; security_status.html:19 `{% if bank_ready %}Vorbereitet{% else %}Nacharbeit{% endif %}`
- **Szenario:** Alle Umgebungsschalter gesetzt, aber der Admin-Account hat weiterhin „sedo1234“ → Check 1 schlägt fehl, ok_count = 9 von 10 → `bank_ready = 9 >= 9` → Kachel zeigt „Vorbereitet“. Der Nutzer hinterlegt Bank-PIN, während der Login mit Standard-Passwort über den Tunnel erreichbar ist.
- **Auswirkung:** Falsches Sicherheitsgefühl an der einzigen Stelle, an der die Anwendung selbst eine Freigabeempfehlung ausspricht.
- **Fix:** `bank_ready = all(c['ok'] for c in checks)` bzw. Pflicht-Checks (Standard-Passwort, DEBUG, ALLOWED_HOSTS, Secure-Cookies) hart erzwingen; den .env-Check real implementieren oder als reinen Hinweis ohne ok-Wertung anzeigen.
- **Aufwand:** S · **Dimension:** sicherheit-web · **Gutachter-Notiz:** Beide Gutachter bestätigen.

## 3. Empfohlene Reihenfolge

**Welle 1 – Quick-Wins: kleiner Aufwand, verhindert Datenverlust und Falschrechnungen.**
1. **F-17** Mapping-Import bei 0 gültigen Zeilen abbrechen – eine Zeile Prüfung verhindert den Totalverlust des GWG-Mappings.
2. **F-16** GWG-Monatslauf: leerer oder unlesbarer Preis muss abbrechen statt 10,00 € zu setzen – betrifft Massenrechnungen an den Hauptkunden.
3. **F-12** Kundenimport: nur vorhandene, nicht leere Spalten schreiben – schützt den Stammdatenbestand.
4. **F-02** Admin-Löschsperre für Rechnungen, Zahlungen, Mahnungen, Audit-Log – schließt den einzigen Weg zum harten Löschen.
5. **F-03** plus **F-31** und **F-81**: `django_session` aus der Backup-Kopie löschen, `VACUUM` anhängen, Bereinigungsfehler laut melden.
6. **F-09** `ssl.create_default_context()` beim SMTP_SSL-Versand – eine Zeile gegen Passwortabfluss.
7. **F-04** BT-25 an die schemakonforme Stelle verschieben – macht Storno- und Korrektur-XML wieder gültig.
8. **F-11** und **F-66** Formularverschachtelung in „Offene Posten“ auflösen und Batch-Mahnung ohne Auswahl abbrechen – verhindert Massenmahnungen per Fehlklick.
9. **F-53** Abmelden als POST – Abmelden funktioniert derzeit gar nicht.
10. **F-24** `makemigrations` aus den Startskripten entfernen – verhindert kaputte Updates auf den Nutzerrechnern.

**Welle 2 – die übrigen hoch-Funde.**
11. **F-07** Minimalfix: Status „korrigiert“ und „entwurf“ in `refresh_invoice_payment_status`, beim E-Mail-Versand und bei der Zahlungserfassung schützen.
12. **F-13** Entwürfe konsequent sperren, **F-14** Ausblenden auf Entwürfe beschränken.
13. **F-18** Archivdateien nicht neu erzeugen, wenn sie existieren.
14. **F-15** Dublettenprüfung beim Kassenbuch-Bankimport, **F-06** Formularvalidierung, **F-08** ARS-Felder für Folgebelege, **F-10** Doppel-Submit, **F-01** Login-Schutz, **F-05** PDF/A-3.

**Welle 3 – Konzeptthemen, die eine Entscheidung brauchen.**
15. Lebenszyklus und Zahlstatus in zwei Felder trennen (Basis von F-07, F-13, F-14, F-60, F-89).
16. Entwurfs-Flow: Entwürfe ohne Nummer speichern, Aktion „Finalisieren“ ergänzen (F-13, F-63).
17. Backup und Restore: dokumentierter Wiederherstellungsweg, Rotation, Testläufe aus dem Live-Ordner heraushalten (F-32, F-37, F-38, F-82).
18. Redaktion vor der KI: Freitext serverseitig filtern statt per Prompt-Anweisung, Transkript erst nach Bestätigung senden, Web-Speech-Fallback abschalten oder die Zusage in der Oberfläche korrigieren (F-42, F-50, F-51, F-41).
19. Betriebsart: `runserver` durch einen echten Server ersetzen oder den Tunnel absichern (F-23, F-01, F-26).

**Welle 4 – Rest.**
20. Verbleibende mittel-Funde nach Modul abarbeiten, wenn die jeweilige Datei ohnehin angefasst wird.
21. niedrig-Funde als Sammelposten, mit Vorrang für die drei Sicherheitshinweise F-78, F-86 und F-122 (Secrets im Klartext) und für F-108 (Testlücken).


## 4. Geprüft und verworfen (Transparenz)

Diese Funde wurden gemeldet, von den Gutachtern aber widerlegt oder als nicht relevant eingestuft. Sie stehen hier, damit sie nicht erneut gemeldet werden.

| Titel | Datei:Zeile | Warum verworfen |
|---|---|---|
| Keine Indexe auf den Standardfiltern deleted_at, status, issue_date, source/period und Payment.booking_date | sedo/models.py:141 | Kein Fehlverhalten, heute ohne Auswirkung – als optionale Empfehlung (Meta.indexes) in den Anhang. |
| Anthropic-API-Key liegt im Klartext in der SQLite-Datenbank (AppSetting) | sedo/views.py:1992 | Dublette zum bestätigten Secrets-Fund (models.py:425, Klartext-Secrets); Env-Alternative und Backup-Bereinigung existieren. |
| KI-Ausgaben werden vor jeder Nutzerprüfung dauerhaft in den Leistungskatalog geschrieben und fließen in alle künftigen System-Prompts und Angebote | sedo/services/ai.py:474 | Dublette von #2 – dort zusammengeführt. |
| preis_art „genannt“ wird nicht gegen das Diktat verifiziert – Halluzinationen erscheinen als „aus Diktat übernommen“ ohne Warnung | sedo/services/ai.py:342 | Dublette von #4 – dort zusammengeführt. |
| Katalogtexte aus der Datenbank fließen ohne Abgrenzung in den System-Prompt – Prompt-Injection mit Selbstverstärkung über den lernenden Katalog | sedo/services/ai.py:213 | Kein realer Angriffspfad (nur die zwei Firmennutzer schreiben Katalogtexte); Datenblock-Kapselung als Hygiene-Hinweis in #2 aufgenommen. |
| KI-Ausgaben werden vor dem Persistieren nicht auf Personen-/Adressdaten geprüft – Treffer landen unlöschbar im Audit-Log | sedo/views.py:379 | Mehrfach spekulativ (setzt Regelverstoß des Modells voraus); Gebäudeadresse ist kein Personenbezug; kein Handlungsbedarf. |
| Stumme Fallbacks im Monatslauf: ungültiges Rechnungsdatum wird zu „heute“, gewählte pausierte Vorlagen werden ohne Hinweis übergangen | sedo/views.py:1465 | Datum kommt per <input type=date> nur ISO-valide an; pausierte Vorlagen sind laut UI-Text erwartungsgemäß ausgeschlossen. |
| D() liest englisches Tausenderformat falsch ("1,250.00" → 1.25) | sedo/core/rechnungslauf_core.py:129 | D() wird außerhalb des Kern-CLI nirgends aufgerufen; latente Parser-Schwäche ohne Pfad in der App. Als Hinweis für den Kern vermerkt. |
| BusinessProcess-ID (Peppol BIS) kombiniert mit Factur-X-EXTENDED-Guideline; TypeCode 457 außerhalb der XRechnung-/Peppol-Codeliste | sedo/core/rechnungslauf_core.py:607 | Im Factur-X-EXTENDED-Profil (einziges erzeugtes Profil) gültig; nur relevant, falls künftig XRechnung-Empfänger dazukommen – Zukunftshinweis. |
| Unbekannte Einheiten werden still auf H87 (Stück) gemappt – PDF-Einheit und XML-unitCode weichen ab | sedo/core/rechnungslauf_core.py:115 | Bewusst dokumentiert und getestet (H87-Fallback hält XML gültig). Mini-Aufräumhinweis: tote Dict-Einträge mit Punkt. |
| SECURE_PROXY_SSL_HEADER ist unbedingt gesetzt – ohne vorgeschalteten Proxy kann jeder Client HTTPS vortäuschen | config/settings.py:102 | Technisch stimmt der Befund (config/settings.py:102 setzt SECURE_PROXY_SSL_HEADER unbedingt, START_HIER.command:30 / START_WINDOWS.bat:15 binden runserver direkt auf 0.0.0.0:8000). Im Projektkontext i | Code-Fakten stimmen (config/settings.py:102 setzt SECURE_ |


## 5. Abdeckung der Prüfung

Notizen der Prüfer, was je Dimension gelesen und was nicht geprüft wurde. Grundlage für spätere Nachprüfungen.

- **datenmodell-migrationen** (Runde 1): GEPRÜFT (nur lesend): sedo/models.py vollständig (Feldtypen, null/blank, unique/UniqueConstraints, on_delete, Defaults, choices, Manager); alle Migrationen 0001–0012 inkl. `makemigrations --check --dry-run` (Ergebnis: „No changes detected“) und `showmigrations` (alle 12 auf dieser Maschine angewendet); SQL der Constraint-Migrationen per `sqlmigrate` 0001/0009/0012 (SQLite baut die DunningDocument-Tabelle in 0012 um; partielle Unique-Indizes mit WHERE); sedo/signals.py, sedo/admin.py, sedo/audit.py; alle write_audit-Aufrufstellen in views.py; Manager-Umgehungen per grep (all_objects, .delete(), aggregate/Sum, values/values_list, raw/cursor – keine Raw-SQL-Nutzung gefunden) und alle Rückwärtsrelationen (payments/followups/invoices/offers/dunning_documents/items) – die Stellen filtern korrekt mit deleted_at__isnull=True; Ausnahme: `invoice.dunning_documents`/`items` haben kein Soft-Delete (
- **datenschutz-ki** (Runde 1): GEPRÜFT (nur lesend, keine Datei geändert, keine Migration, kein Zugriff auf Live-DB/media): sedo/services/ai.py vollständig (alle 4 Payload-Pfade an api.anthropic.com: _ai_generate_via_tool_use/generate_offer_from_text, polish_service_text, generate_cover_letter_text, ai_review_positions; System-Prompts; _service_catalog_prompt_block; normalize_ai_positions; apply_and_learn_service_catalog; Fehlerübersetzung _ai_error_from_http/_ai_request). sedo/views.py: ai_generate_offer, ai_transcribe, ai_polish_text, _invoice_ai_context, ai_cover_letter, ai_plausibility, _read_json_body, save_ai-Settings, invoice_email (Body-Übernahme), GWG-Monatslauf-Beschreibungsquelle. sedo/services/transcription.py vollständig, sedo/services/plausibility.py vollständig. JS: offer_ai.js, magic_tone.js, plausibility.js vollständig (eingesammelte Felder: nur Transkript-Text bzw. long_text bzw. description/quantity
- **datenschutz-ki** (Runde 1): GEPRÜFT (nur lesend, Live-DB und media/ unangetastet): sedo/services/ai.py vollständig (alle vier Payload-Builder generate_offer_from_text/_ai_generate_via_tool_use, polish_service_text, generate_cover_letter_text, ai_review_positions, alle System-Prompts, _service_catalog_prompt_block, normalize_ai_positions, apply_and_learn_service_catalog, Fehlerübersetzung _ai_error_from_http, Retry-Logik); sedo/services/transcription.py vollständig; sedo/views.py: ai_generate_offer, ai_transcribe, ai_polish_text, _invoice_ai_context, ai_cover_letter, ai_plausibility, offer_form-Kontext (stt_available), Settings-View API-Key; sedo/static/sedo/offer_ai.js, magic_tone.js, plausibility.js vollständig; Templates offer_form.html (KI-Schritt), invoice_detail.html (Begleittext-Karte), invoice_form.html (Script-Einbindung); sedo/audit.py write_audit; services/plausibility.py Kopf (rein lokal, kein KI-Aufruf)
- **datenschutz-ki** (Runde 2): GEPRÜFT (nur lesend, keine Ausführung, keine DB-/Media-Zugriffe): sedo/services/ai.py vollständig (alle Payloads: generate_offer_from_text/_ai_generate_via_tool_use, polish_service_text, generate_cover_letter_text, ai_review_positions, System-Prompts, Katalogblock, Fehlerübersetzung, Retry); sedo/services/transcription.py vollständig; sedo/views.py: ai_generate_offer, ai_transcribe, ai_polish_text, _invoice_ai_context, ai_cover_letter, ai_plausibility, invoice_email, GWG-/Abo-Monatslauf (Herkunft der Positionsbezeichnungen), Settings-Speicherung; sedo/services/plausibility.py (lokaler Check); sedo/static/sedo/offer_ai.js, magic_tone.js, plausibility.js, wizard.js (Storage); Templates offer_form, invoice_form, invoice_detail, settings, service_catalog_*; sedo/urls.py; Modelle (Invoice, Items, ServiceCatalogItem, AareonMapping, SoftDelete-Manager); Nummernformat (numbering.py); XML-Erzeugu
- **frontend-js-templates** (Runde 1): GEPRÜFT (vollständig gelesen): alle 8 JS-Dateien in sedo/static/sedo (wizard.js, offer_ai.js, magic_tone.js, plausibility.js, customer_search.js, global_search.js, positions_table.js, unsaved_guard.js); alle 34 Templates in sedo/templates/sedo sowie registration/login.html; sedo/urls.py und config/urls.py (automatischer Abgleich aller {% url %}-Namen inkl. Argumentvarianten – alle Namen existieren; einziger Nicht-sedo-Name 'logout' ist definiert, aber nur per POST erreichbar); config/settings.py (USE_THOUSAND_SEPARATOR, WhiteNoise, CSRF/Cookie-Flags); sedo/forms.py (Widgets: quantity/unit_price/vat_rate als NumberInput step=0.01, Formset-Felder ohne required); die Frontend-relevanten Views (global_search_api, customer_data_api, alle ai_*-Endpunkte, invoice_create, offer_create, invoice_email, bulk_dunning, invoice_add_payment/mark_paid, invoice_reminder, _selected_monthly_services, setti
- **frontend-js-templates** (Runde 2): GEPRÜFT (nur lesend, keine Datei geändert, weder Server noch Tests gestartet): alle 8 JS-Dateien in sedo/static/sedo (wizard.js, offer_ai.js, magic_tone.js, plausibility.js, customer_search.js, global_search.js, positions_table.js, unsaved_guard.js) vollständig; alle 34 Templates in sedo/templates/sedo sowie registration/login.html vollständig; config/urls.py, sedo/urls.py; forms.py (Widgets/Lokalisierung), Modelle (Dezimalstellen, __str__), services/calc.py (Rundung), views-Gegenstücke der Frontend-Aufrufe (customer_data_api, global_search_api, ai_generate_offer, ai_transcribe, ai_polish_text, ai_plausibility, invoice_create, offer_create, invoice_email, invoice_mark_paid, bulk_dunning, _selected_monthly_services), settings (USE_THOUSAND_SEPARATOR, CSRF_COOKIE_HTTPONLY, STATIC), app.css nur für die Wizard-Regeln; Startskripte SEDO.vbs, START_WINDOWS.bat, START_HIER.command, SEDO_BEENDEN
- **funktionalitaet-views** (Runde 1): GEPRÜFT (nur lesend): sedo/views.py vollständig (2260 Zeilen, alle 60 Views und Hilfsfunktionen), sedo/forms.py vollständig, sedo/models.py, sedo/urls.py, config/settings.py sowie die von den Views genutzten Services (numbering.py, invoices.py, calc.py, payments.py, dunning.py, banking.py, mail.py, pdf.py, plausibility.py) und die zugehörigen Templates (invoice_form, invoice_detail, invoice_list, invoice_preview, open_items, payment_list, payment_manual, aareon_monthly, aareon_import, recurring_monthly, month_close, cashbook_list, customer_detail, settings-Formularaktionen). Verifiziert per Python: Django-6.0.8-Datumsformate akzeptieren ISO (type=date-Inputs parsen korrekt – kein Datumsfund), Rundungsdifferenz Vorschau/Rechnung (3,50 € → 0,66 vs. 0,67), country_code() im Kern normalisiert Ländernamen (deshalb KEIN Fund zu ungültigen CountryIDs). Test-Suite ausgeführt: 245 Tests OK (1 ski
- **funktionalitaet-views** (Runde 2): Geprüft (nur lesend): sedo/views.py vollständig (Z. 1-2260), sedo/forms.py vollständig, sedo/urls.py, sedo/models.py, sedo/services/invoices.py, payments.py, numbering.py, dunning.py, calc.py, Ausschnitte aus mail.py (SMTP-Port-Parsing ist robust), pdf.py (Kassenbuch-Pfad) und banking.py, sowie die Templates invoice_form.html, invoice_preview.html, invoice_detail.html, aareon_monthly.html, recurring_monthly.html, settings.html (Formular-Actions/Hidden-Felder). Fünf Kernbehauptungen wurden mit einem temporären Testmodul außerhalb des Projekts (Scratchpad, `manage.py test <pfad>`, In-Memory-Test-DB, RequestFactory ohne Middleware – keine Backups, keine Live-DB-Zugriffe, keine Dateien im Projekt) belegt: Status-Flip 'korrigiert'/'entwurf' → 'ueberfaellig' durch open_items-GET, Storno danach erlaubt, 10,00-€/1,00-Fallback im GWG-Lauf, _norm(1234.0) → '1234.0', Korrektur bei teilbezahlter Rec
- **gobd-geschaeftslogik** (Runde 2): Geprüft (nur lesend, keine Datei angelegt/verändert): sedo/services/numbering.py, calc.py, invoices.py, dunning.py, payments.py, banking.py (Normalisierung/External-ID), pdf.py (Kassenbuch), sedo/models.py, sedo/forms.py, sedo/views.py vollständig (Rechnungs-/Angebots-/Zahlungs-/Mahn-/Kassenbuch-/Monatslauf-/Monatsabschluss-Views), sedo/signals.py, sedo/urls.py, relevante Ausschnitte von core/rechnungslauf_core.py (build_xml, money, Rundung), Templates invoice_detail/open_items/offer_detail/dashboard/aareon_monthly/recurring_monthly per grep sowie die Testnamen in sedo/tests (number_range, dunning_stages, workflow_locks, review_fixes) zur Einordnung, was bereits abgedeckt ist. Nummernkreis: F()-Update in umgebender Transaktion, Seeding über all_objects, Retry nur bei echter Nummernkollision – als korrekt bewertet; Rundung (zeilenweise HALF_UP, dann Summe) ist zwischen calc.py, InvoiceIte
- **sicherheit-secrets-banking** (Runde 1): GEPRÜFT (nur lesend; Live-DB ausschließlich mit `?immutable=1` geöffnet, nur AppSetting-Schlüssel und Backup-Metadaten gelesen, keine Kunden-/Personendaten ausgegeben; Backups nur Dateinamen/BACKUP_INFO.txt): sedo/services/banking.py, mail.py, ai.py (Key-Handling: Env SEDO_AI_API_KEY vor AppSetting, Key nur im x-api-key-Header, nicht geloggt, Fehlermeldungen enthalten keinen Key), backup_utils.py, config/settings.py (.env-Loader, SECRET_KEY-Datei data/.secret_key = 0600 bestätigt, kein Code-Fallback), sedo/models.py (AppSetting: alle Werte Klartext-TextField), views.py (settings, payment_list, invoice_email, backup_page, security_status), middleware.py, audit.py (Metadaten enthalten keine Secrets), admin.py, templates settings.html/payment_list.html (Secrets werden nur als Platzhalter angezeigt, nicht zurückgerendert), desktop.py, Start-Skripte (START_HIER.command, START_WINDOWS.bat, SED
- **sicherheit-secrets-banking** (Runde 2): Geprüft (nur lesend, keine Datei verändert, keine DB-/Media-Zugriffe, Test-Suite bewusst nicht gestartet, da sie laut bekanntem Fund in den echten backups/-Ordner schreibt): sedo/services/banking.py, mail.py, ai.py (Key-Handling, Fehlerpfade, Header), transcription.py (Temp-Audio wird gelöscht), backup_utils.py, middleware.py, audit.py, signals.py, models.py (AppSetting), views.py (payment_list, settings/save_ai/save_smtp/save_banking/test_smtp, backup_page, security_status), Templates settings.html/backup.html/payment_list.html, config/settings.py (.env-Loader, SECRET_KEY-Datei, Session-/Logging-Settings), config/urls.py, desktop.py, SEDO.app-Launcher, START_HIER.command, START_WINDOWS.bat, SEDO.vbs, Build-Skripte/Spec/Inno-Setup, .env.example, requirements, DEPLOYMENT_SECURITY_NOTES.md, Tests test_banking.py/test_security_hardening.py sowie Dateirechte von data/.secret_key (0600), data
- **sicherheit-web** (Runde 1): GEPRÜFT (nur lesend, keine Datei geändert, keine Live-DB berührt): config/settings.py vollständig (DEBUG-Default aus, SECRET_KEY-Handling, ALLOWED_HOSTS, CSRF/Session/Cookie-Flags, Security-Header, Middleware-Reihenfolge); sedo/middleware.py (LoginRequiredMiddleware mit EXEMPT_PREFIXES – alle 59 Routen in sedo/urls.py laufen dadurch und zusätzlich per @login_required geschützt, /media/ ist geschützt, /admin/ hat eigene Auth); config/urls.py (Media-Auslieferung über django.views.static.serve – nutzt safe_join, kein Traversal); sedo/views.py vollständig (2260 Zeilen: alle mutierenden Aktionen nur bei POST bzw. @require_POST, CsrfViewMiddleware global, kein csrf_exempt im Projekt; alle FileResponse-Pfade serverseitig erzeugt; KI-Endpunkte mit Login, POST, 100-KB-Body-Limit, Audio 25 MB); sedo/urls.py; sedo/admin.py; sedo/audit.py; sedo/backup_utils.py (Backups nicht per Web abrufbar, Secret
- **sicherheit-web** (Runde 2): GEPRÜFT (nur lesend, keine Datei geändert, Live-DB nicht geöffnet): sedo/urls.py + config/urls.py vollständig gegen sedo/views.py abgeglichen – alle 60 gerouteten Views tragen @login_required, zusätzlich LoginRequiredMiddleware (Exempt nur /login/, /admin/, /static/, /favicon.ico; /media/ läuft durch die Middleware und über django.views.static.serve mit safe_join – kein Traversal, kein Directory-Listing). Mutierende Aktionen: require_POST bzw. Methodenprüfung vorhanden (Löschen/Storno/Status/Erzeugen/Mahnung/Batch/Monatsläufe); einzige Ausnahme mit Seiteneffekt auf GET ist cashbook_pdf (gemeldet). CSRF: alle 51 POST-Formulare in den Templates enthalten csrf_token (pro Template gezählt), alle fetch()-Aufrufe (offer_ai.js, magic_tone.js, plausibility.js) senden X-CSRFToken aus dem Formularfeld; CSRF_COOKIE_HTTPONLY konsistent dazu. JSON-/KI-Endpunkte: 100-KB-Body-Limit, Typprüfung dict/lis
- **tests-konfig-deployment** (Runde 1): GEPRÜFT: (a) Test-Suite `DJANGO_ALLOWED_HOSTS=testserver,127.0.0.1 .venv/bin/python manage.py test sedo` → Found 245 tests, Ran 245 in 20,6 s, OK, 1 Skip (test_transcription.py:46, faster-whisper installiert), 0 Fehler; Nebenwirkung: 3× UserWarning 'Duplicate name: media/factur-x.xml' und 21 neue weekly_auto-ZIPs im echten backups/-Ordner (178→199; während der Analyse lief offenbar parallel ein weiterer Testlauf, danach 207). (b) `check --deploy` mit DJANGO_DEBUG=0 + Dummy-Secret: 4 Warnungen (W004 HSTS, W008 SSL-Redirect, W012 Session-Cookie, W016 CSRF-Cookie – alle per .env schaltbar, Default lokal bewusst aus); mit .env.example-Flags nur W005/W021 (HSTS-Subdomains/Preload, bewusst 0). `makemigrations --check --dry-run`: No changes detected (12 Migrationen). (c) requirements.txt, requirements_desktop.txt, sedo_desktop.spec, desktop.py, build_mac.command, BUILD_WINDOWS.bat, sedo_setup_w
- **zugferd-xml-pdf** (Runde 1): Geprüft (nur lesend): sedo/core/rechnungslauf_core.py vollständig (build_xml, create_pdf, embed_xml_into_pdf, unece_unit_code, D, money/de_money, InvoiceRow-Summenlogik), sedo/services/invoices.py vollständig (invoice_to_core_row, generate_invoice_files, Storno/Korrektur), relevante Stellen in sedo/views.py (Rechnungsanlage, Korrektur, invoice_generate, invoice_email), sedo/models.py (Dezimalstellen, Choices), sedo/forms.py (vat_rate-Widget), pypdf 6.16.1 (_files.py/_writer.py add_attachment). Zur Verifikation wurde im Scratchpad (nicht im Projekt) ein Storno-Beispiel mit build_xml/create_pdf/embed_xml_into_pdf erzeugt und die XML sowie die PDF-Objektstruktur (Root-Keys, /AF, EmbeddedFile, Fonts, XMP) ausgelesen. Positiv geprüft und ohne Befund: BT-1/BT-2 (Format 102)/BT-3/BT-5, Verkäufer inkl. USt-ID (VA) und Steuernummer (FC), BT-44, BT-9/BT-20, BT-73/74, BT-154, Summenkonsistenz (Line
