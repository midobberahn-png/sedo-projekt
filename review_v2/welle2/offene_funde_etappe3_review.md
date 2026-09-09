# Offene Funde aus dem adversarischen Review der Etappe 3

Stand 09.09.2026. Gemeldet 35, nach Gegenpruefung bestaetigt 23,
davon 8 sofort behoben. Die folgenden 15 sind offen.

Methode: vier Blickwinkel griffen den Diff an (Filterlogik, Sichtbarkeit, Statuswechsel,
Alltagswege), danach versuchte je ein eigener Agent, jeden Fund zu WIDERLEGEN. Nur was das
ueberstanden hat, steht hier.

ACHTUNG: Mehrere dieser Funde beschreiben den Stand VOR der Nacharbeit. Der Wurzelfix
(active_correction/active_storno schliessen Entwuerfe aus, Commit 8f59780) hat einen Teil
davon bereits entschaerft – bitte am aktuellen Code gegenpruefen.

## [MITTEL] Die Mengen pruefen nur das Statusfeld, die Waechter den Beleg-Graphen – Bestandsbelege werden weiterhin doppelt gezaehlt

**Belegt durch:** Codelesen plus echter Testlauf. (1) Codelesen: calc.py:98-112, models.py:145-146/241, invoices.py:36-41/105/173, dunning.py:175-197, payments.py:55-81, views.py:601-613/1271-1300; `git show 3184595^:sedo/views.py` fuer den Zustand vor Etappe 1; ls von sedo/migrations/ und sedo/management/commands/. (2) Testlauf: Ich habe ein Wegwerf-Testmodul im Scratchpad (/private/tmp/claude-501/-Users-mirekdobberahn/cb40cf0f-a05c-4cc2-8297-abe531d98d90/scratchpad/probe_f07_bestand.py) angelegt und per PYTHONPATH mit `manage.py test probe_f07_bestand` laufen lassen – Django legt fuer SQLite eine In-Memory-Te

**Begruendung:** Der Fund haelt stand. Ich habe vier Widerlegungsversuche unternommen, alle vier sind gescheitert.

1) "Vielleicht prueft offene_rechnungen() doch den Graphen." Nein. sedo/services/calc.py:110-112 ist der vollstaendige Rumpf: `basis.exclude(status__in=_Invoice.NICHT_OFFENE_STATUS).exclude(invoice_type='457')`. Kein Zugriff auf `followups`, `active_correction` oder `reference_invoice`. NICHT_OFFENE_STATUS ist reine Feldpruefung (sedo/models.py:145-146). Dasselbe Muster in models.py:241 (is_overdue) und sedo/services/dunning.py:185.

2) "Vielleicht gibt es doch ein Reparaturskript/eine Datenmigration." Nein. sedo/migrations/ endet bei 0012_concurrency_constraints (aus der Zeit vor dem Review); sedo/management/commands/ enthaelt nur ensure_admin.py. Die Etappe-1-Commitnachricht (3184595) sagt selbst "alles migrationsfrei".

3) "Vielleicht heilt sich der Schaden beim naechsten Seitenaufruf." Nein. refresh_invoice_payment_status (calc.py:76-95) laesst 'ueberfaellig' unangetastet, und abgeleiteter_zahlstatus (calc.py:42-73) kennt den Graphen ueberhaupt nicht. In der Probe blieb der Status vor und nach dem Aufruf 'ueberfaellig' ([5]/[6]).

4) "Vielleicht war der Schaden vor Etappe 1 gar nicht erzeugbar." Doch, nachweislich. `git show 3184595^:sedo/views.py` zeigt: open_items hatte als Grundmenge `.exclude(status='storniert')` – ein korrigiertes Original war also drin – und rief fuer jede Zeile refresh_invoice_payment_status auf, das damals nur 'storniert' schuetzte. Ein einziger GET 

---

## [MITTEL] Entwuerfe reissen eine unerklaerte Nummernluecke in den Monatsabschluss

**Belegt durch:** Codelesen plus zwei echte Shell-Proben mit dem Django-Test-Client, dazu ein gezielter Testlauf.

Proben (beide in /private/tmp/claude-501/-Users-mirekdobberahn/cb40cf0f-a05c-4cc2-8297-abe531d98d90/scratchpad/, ausserhalb des Projekts): Django ueber config.settings gebootet, dann `DiscoverRunner.setup_databases()` – die DB war ausweislich der Ausgabe `file:memorydb_default?mode=memory&cache=shared`, data/sedo_web.sqlite3 wurde nicht geoeffnet. Belege wurden nicht per ORM konstruiert, sondern echt ueber POST auf `sedo:invoice_create` mit `action=save` bzw. `action=draft` angelegt, damit die Numm

**Begruendung:** Ich habe versucht, den Fund zu widerlegen, und keine Entlastung gefunden. Die Kette haelt an jeder Stelle:

1. Der Entwurf verbraucht eine echte Nummer. `sedo/views.py:888` setzt `inv.status = 'entwurf' if action == 'draft' else 'erstellt'`, und `sedo/views.py:920` ruft `save_with_unique_number(inv, 'RE')` unabhaengig von `action`. Der Zaehler in `sedo/services/numbering.py:71` wird per `F('next_number') + 1` hochgezaehlt und in `sedo/services/numbering.py:36` ueber `all_objects` geseedet – die Nummer kommt nie zurueck. Meine Probe bestaetigt das: nach 29 Rechnungen + 1 Entwurf steht `NumberRange.next_number = 31`.

2. Der Knopf existiert wirklich: `sedo/templates/sedo/invoice_form.html:229` (`<button ... name="action" value="draft">Entwurf</button>`). Das Szenario ist also erreichbar, nicht theoretisch.

3. Seit Etappe 3 faellt der Entwurf aus dem Abschluss: `sedo/views.py:2375` haengt `.exclude(status='entwurf')` an. Zur Gegenprobe habe ich die alte Fassung gelesen – `git show HEAD:sedo/views.py`, Zeile 2294 – dort steht der Filter ohne `exclude`. Vor Etappe 3 stand die Entwurfsnummer also mit Badge "Entwurf" in der Liste; die Luecke ist neu.

4. Die neue Karte deckt sie nicht ab: `sedo/views.py:2378-2381` filtert ausschliesslich `deleted_at__isnull=False`, das Template zeigt sie nur unter `{% if ausgeblendete_nummern %}` (`sedo/templates/sedo/month_close.html:73`).

5. Der Entwurf ist kein Durchgangszustand, sondern eine Sackgasse. Es gibt keinen Weg von 'entwurf' nach 'er

---

## [MITTEL] F-63 unvollständig: Der Abo-Vorlagenlauf (views.py:2233) macht aus 0 Tagen weiterhin 14

**Belegt durch:** Codelesen plus echter Testlauf. (1) Shell-Probe mit dem Django-Testclient (eigenes Skript im Scratchpad, Test-DB in-memory, MEDIA_ROOT via 'test' in sys.argv nach /tmp umgelenkt, Live-DB data/sedo_web.sqlite3 nicht geöffnet, keine Projektdatei geändert): POST auf sedo:invoice_create mit payment_days='0' und save_as_recurring='1', danach POST auf sedo:recurring_generate. Gemessen: A) Rechnung RE2026090001 issue 2026-09-01 / due 2026-09-01 (F-63-Fix in invoice_create greift). B) Vorlage payment_days = 0, Kunde 14. C) Abo-Rechnung RE2026090002 issue 2026-10-01 / due 2026-10-15 = 14 Tage statt 0. 

**Begruendung:** Widerlegungsversuch gescheitert. Die Kette ist über die echte UI erreichbar und im Testlauf reproduziert: Haken „Als Abo-/Monatsrechnungsvorlage speichern" (sedo/templates/sedo/invoice_form.html:209) -> sedo/views.py:961-963 -> _create_recurring_template_from_invoice (views.py:317ff). Dort speichert views.py:325 bei due_date == issue_date korrekt payment_days = 0 (der `or 14` steht nur im else-Zweig). Im Lauf rechnet views.py:2233 `issue_date + timedelta(days=tmpl.payment_days or tmpl.customer.payment_days or DEFAULT_PAYMENT_DAYS)` — 0 ist falsy, also 14 Tage. Verschärfend: tmpl.payment_days ist PositiveIntegerField(default=14) ohne null=True (sedo/models.py:281), wird also nie None; der Fallback kann ausschließlich im Fall 0 greifen und ist damit rein schädlich. Folgewirkung bestätigt: is_overdue (models.py:243) und die Mengen in views.py:612 (Dashboard overdue_invoices) sowie views.py:1283 (Reiter „Nur überfällig") hängen an due_date, der Beleg fällt 14 Tage später hinein. ZWEI KORREKTUREN am Fund: (1) views.py:325 `(inv.customer.payment_days or 14)` ist KEIN aktiver Fehler — der Zweig greift nur bei fehlendem due_date, und der einzige Aufrufer (views.py:963) hat immer eines (gesetzt in views.py:886); tote Absicherung, in der Probe nicht erreicht. (2) Die Behauptung, der Fix falle wegen der GWG-/Abo-Masse „praktisch wieder heraus", ist überzogen: der GWG-Monatslauf ist sauber (views.py:1968-1976 liest „0" als nicht-leeren String, int("0")=0 überlebt, views.py:2000 rechnet m

---

## [MITTEL] Korrektur-Entwurf: Zahlung gesperrt, Mahnung erlaubt – Waechter widersprechen sich, Entwurf ist eine Sackgasse (Schadensbild des Melders aber falsch)

**Belegt durch:** Echter Testlauf ueber den Django-Test-Client, plus Codelesen und Git-Archaeologie. (1) Shell-Probe /private/tmp/claude-501/-Users-mirekdobberahn/cb40cf0f-a05c-4cc2-8297-abe531d98d90/scratchpad/probe_korrektur_entwurf.py: eigenes sys.argv=['probe','test'] (damit MEDIA_ROOT nach settings.py:183-186 in den Temp-Ordner zeigt), DiscoverRunner.setup_databases() legt eine eigene Test-DB an – die Live-DB data/sedo_web.sqlite3 wurde nicht geoeffnet, keine Projektdatei geaendert. Kompletter Klickpfad: Rechnung anlegen (action=create) -> POST invoice_email -> GET invoice_correction -> POST invoice_create

**Begruendung:** WIDERLEGUNGSVERSUCH GESCHEITERT – der Mechanismus ist echt, die Schadensbeschreibung nicht.

WAS SICH BESTAETIGT (Klickpfad im Probelauf, Zeilen unten):
1. Der Korrektur-Entwurf entsteht wirklich: sedo/views.py:941-945 markiert das Original nur bei `action != 'draft'` als 'korrigiert'; der Knopf "Entwurf" steht im Korrekturformular unbedingt da (sedo/templates/sedo/invoice_form.html:229). sedo/models.py:227-229 filtert `active_correction` nur auf Typ+deleted_at, der Entwurf haengt also als 384-Folgebeleg am Original. Probe: RE2026090002 ('384','entwurf'), Original RE2026090001 bleibt 'versendet', active_correction=True.
2. Die Knopf-Asymmetrie im Etappe-3-Template ist real: invoice_detail.html:20 (Zahlung erfassen), :23-26 (Zahlungserinnerung/Mahnung) und :65 (Restbetrag als bezahlt) pruefen NUR das Statusfeld, waehrend die Nachbarknoepfe :22 (Korrektur laden), :27 (Storno) und :28 (Loeschen) zusaetzlich `not invoice.active_correction`/`not invoice.active_storno` tragen. Probe auf der Detailseite des Originals: E-Mail/Zahlung erfassen/Zahlungserinnerung/Mahnung/Restbetrag SICHTBAR, Korrektur laden/Storno weg, Loeschen deaktiviert.
3. Die beiden Waechter widersprechen sich tatsaechlich: sedo/services/invoices.py:105-114 blockiert die Zahlung ("Rechnung RE2026090001 wurde durch eine Korrektur ersetzt. Bitte auf RE2026090002 buchen." – wortgleich reproduziert), sedo/services/dunning.py:61-65 kennt die Kreuzpruefung nicht und laesst durch; sedo/services/dunning.py:169-171 setzt d

---

## [MITTEL] Ein Entwurf ist eine Sackgasse: Es gibt keinen Knopf und keine Route, um ihn zu finalisieren

**Belegt durch:** Drei Wege, alle nur lesend – am Projekt wurde keine Datei geaendert (git status zeigt danach dieselben fuenf Dateien wie vorher, data/ unberuehrt).

1) Echte Shell-Probe mit Django-Test-Client: /private/tmp/claude-501/-Users-mirekdobberahn/cb40cf0f-a05c-4cc2-8297-abe531d98d90/scratchpad/probe_entwurf.py. Das Skript startet Django mit SEDO_HOME auf den Scratchpad (die Live-DB data/sedo_web.sqlite3 wurde nie geoeffnet – ausgegebener DB-Pfad: .../scratchpad/sedo_home/data/sedo_web.sqlite3), baut ueber DiscoverRunner eine frische Test-DB, legt per POST auf sedo:invoice_create mit action=draft eine

**Begruendung:** Ich konnte den Fund nicht widerlegen – er ist in jedem Einzelpunkt reproduzierbar.

1) Der Entwurf entsteht mit echter Nummer. sedo/templates/sedo/invoice_form.html:229 stellt den Knopf `action=draft` gleichberechtigt neben "Rechnung erstellen" (:230). sedo/views.py:888 setzt daraufhin `inv.status = 'entwurf'`; sedo/views.py:920 ruft `save_with_unique_number(inv, 'RE')` unabhaengig von der Aktion. Mein Testlauf: Rechnung RE2026090001, Status 'entwurf'.

2) Auf der Detailseite bleiben genau zwei Bedienelemente. Gemessen (HTML der gerenderten Seite): "Dateien neu erzeugen" und "Loeschen" – sonst nichts. E-Mail (invoice_detail.html:19), Zahlung (:20), Korrektur (:22), Mahnung/Zahlungserinnerung (:23-26), Storno (:27), "Restbetrag als bezahlt" (:65) und die komplette KI-Begleittext-Karte samt ZWEITEM E-Mail-Formular (:93) haengen jetzt hinter `status != 'entwurf'`. Der String '/email/' kommt auf der Seite eines Entwurfs kein einziges Mal mehr vor.

3) Keine Route fuehrt hinaus. Ich habe jede Rechnungsroute direkt aufgerufen – erzeugen/, email/, zahlung/ (GET+POST), bezahlt/, korrektur/, storno/, mahnung/reminder/, mahnung/dunning/, zusaetzlich dashboard, offene-posten/ (auch ?status=all), rechnungen/, zahlungen/, monatsabschluss/ und zahlungen/<pk>/zuordnen/. Ergebnis ausnahmslos: Status entwurf -> entwurf. Die Sperren greifen in sedo/services/invoices.py:46, 74, 101, 124 und sedo/services/calc.py:89 (`GESCHUETZTE_STATUS` laesst refresh_invoice_payment_status frueh zurueckkehren)

---

## [MITTEL] Entwürfe verbrauchen Rechnungsnummern, fallen ab Etappe 3 aus dem Monatsabschluss – und die neue Karte „ausgeblendete Nummern“ erklärt diese Lücke nicht

**Belegt durch:** Codelesen plus zwei echte Testläufe gegen eine In-Memory-Test-DB (die Live-DB data/sedo_web.sqlite3 wurde nicht geöffnet, keine Projektdatei geändert – die Probeskripte liegen im Scratchpad unter /private/tmp/claude-501/-Users-mirekdobberahn/cb40cf0f-a05c-4cc2-8297-abe531d98d90/scratchpad/probe_entwurf_luecke.py, probe2.py und probe3.py und laufen über DiscoverRunner.setup_databases() mit der venv-Python).

Probe 1 (echter View-Pfad, Django-Test-Client): POST auf sedo:invoice_create mit action='save' und danach mit action='draft'. Ergebnis: Bestand RE2026090001 (erstellt) und RE2026090002 (ent

**Begruendung:** Ich habe versucht, den Fund zu widerlegen, und bin an jeder Stelle gescheitert. Die drei möglichen Entlastungen greifen alle nicht:

WIDERLEGUNGSVERSUCH 1 – „Ein Entwurf bekommt vielleicht gar keine echte Nummer.“ Falsch. /Users/mirekdobberahn/Desktop/Co-Work OS/Projekte/SEDO Rechnungstool/sedo_rechnungstool_v2/sedo/views.py:888 setzt nur den Status ('entwurf' if action == 'draft' else 'erstellt'), und views.py:920 ruft danach bedingungslos save_with_unique_number(inv, 'RE') auf – auch für den Entwurf. Die Vergabe läuft über sedo/services/numbering.py:60-74 (_allocate_number), das den Zähler NumberRange.next_number per F()-Update hochzählt; der Modulkommentar numbering.py:11-13 hält ausdrücklich fest, dass einmal vergebene Nummern nie wiederverwendet werden. Meine Probe zeigt nach einem Entwurf next_number=3 bei zwei Belegen – die Nummer ist verbraucht.

WIDERLEGUNGSVERSUCH 2 – „Die Lücke schließt sich, sobald der Entwurf finalisiert wird.“ Falsch, es gibt diesen Weg gar nicht. 'erstellt' wird im ganzen Belegfluss nur bei der Neuanlage gesetzt (views.py:888); invoice_generate (views.py:1026-1031) erzeugt nur Dateien neu und lässt den Status unberührt, und refresh_invoice_payment_status (sedo/services/calc.py:89) steigt bei GESCHUETZTE_STATUS = ('entwurf', 'korrigiert', 'storniert') (sedo/models.py:145) sofort aus. Auch über das Admin geht es nicht: InvoiceAdmin trägt den NurLesenMixin (sedo/admin.py:71). Ein Entwurf bleibt also dauerhaft Entwurf – die Lücke ist nicht temporär

---

## [MITTEL] 'Dateien neu erzeugen' und der erneute E-Mail-Versand ueberschreiben archivierte Belege mit tagesaktuellen Stammdaten (F-18 nur in month_close erledigt)

**Belegt durch:** Codelesen plus echter Testlauf. (1) Gelesen: invoice_detail.html:19-28, views.py:1025-1031 und 1038-1096, services/invoices.py:117-127/216-278/300-333, calc.py:27-32, models.py:156, views.py:2393-2404, backup_utils.py:214-223, sowie `git diff HEAD` der Etappe 3. (2) Nachgestellt mit einem eigenen Skript im Scratchpad (/private/tmp/claude-501/-Users-mirekdobberahn/cb40cf0f-a05c-4cc2-8297-abe531d98d90/scratchpad/repro_f18.py), das Djangos Test-Runner programmatisch startet: frische In-Memory-Test-DB (file:memorydb_default) und MEDIA_ROOT=/tmp/sedo_test_media, weil config/settings.py:184-186 bei 

**Begruendung:** Der Fund haelt jeder Widerlegung stand; ich habe ihn in einem echten Django-Testlauf nachgestellt.

WIDERLEGUNGSVERSUCHE, DIE FEHLGESCHLAGEN SIND
1) "Vielleicht traegt die Rechnung ihre Adresse als Schnappschuss." Nein. sedo/models.py:156 haelt nur `customer = models.ForeignKey(Customer, ...)`; es gibt kein Adressfeld an der Rechnung. sedo/services/invoices.py:262-266 liest bei JEDER Erzeugung live `buyer_street=customer.street, buyer_postcode=customer.postcode, buyer_city=customer.city` (dazu buyer_name, buyer_id/customer_no, Objektadresse ab Zeile 260, Aareon-Mapping ab 240 und `aareon_seller_id` aus AppSetting, Zeile 250).
2) "Vielleicht gibt es doch einen Guard." Nein. sedo/views.py:1025-1031 ist vollstaendig: get_object_or_404 -> generate_invoice_files -> write_audit -> messages.success. Kein ensure_*, kein Vorhandenheits-Check, keine Statuspruefung. sedo/templates/sedo/invoice_detail.html:21 ist die einzige Zeile der Leiste ohne {% if %} - Zeile 19 (E-Mail), 20 (Zahlung), 22 (Korrektur), 23-26 (Mahnwesen), 27 (Storno) und 28 (Loeschen) tragen alle Bedingungen.
3) "Vielleicht bleiben die alten Dateien daneben liegen." Nein. sedo/services/invoices.py:326-328 schreibt per `os.replace` auf dieselben Pfade media/generated/<Nummer>/<Nummer>.pdf, factur-x.xml und <Nummer>_zugferd.pdf.
4) "Vielleicht ist der Versandpfad harmlos." Nein - und hier liegt die eigentliche Luecke. sedo/views.py:1050 ruft `generate_invoice_files(invoice)` bedingungslos vor jedem Versand. Der Guard dav

---

## [MITTEL] Korrektur-Entwurf ist eine Sackgasse – Original dauerhaft für Storno/Korrektur gesperrt, Entwurf seit Etappe 3 nicht mehr ausblendbar (Zahlung bleibt aber buchbar)

**Belegt durch:** Codelesen plus echter Testlauf. (1) Diff der Etappe gelesen (`git diff HEAD`), Vorzustand über `git show HEAD:sedo/views.py` verglichen. (2) Eigenes Testmodul probe_g.py NUR im Scratchpad (/private/tmp/.../scratchpad/probe_g.py) abgelegt und mit `PYTHONPATH=<scratchpad>:. .venv/bin/python manage.py test probe_g` gefahren – dadurch In-Memory-Test-DB und MEDIA_ROOT unter /tmp (config/settings.py:184-186), die Live-DB wurde nicht angefasst. Kein Projektdateien geändert (`git status` unverändert bei den 5 Etappe-3-Dateien). Die Probe fährt ausschließlich über den Django-Test-Client gegen die echte

**Begruendung:** Der Widerlegungsversuch ist am Kern gescheitert – der Fund ist real und über die echte Oberfläche in einem Klick erreichbar. Zwei Teilbehauptungen konnte ich dagegen widerlegen, und die Schwere sinkt dadurch.

WAS ICH BESTÄTIGEN MUSSTE

1. Der Zustand entsteht über den normalen UI-Weg.
- „Korrektur laden" ist bei 'ueberfaellig' freigeschaltet (sedo/templates/sedo/invoice_detail.html:22), die View leitet auf das Formular um (sedo/views.py:1097-1106).
- Das Formular trägt das hidden-Feld (sedo/templates/sedo/invoice_form.html:6) UND den unbedingten Entwurf-Knopf (sedo/templates/sedo/invoice_form.html:229) – keine Bedingung auf correction_source.
- Die View setzt dann status='entwurf' (sedo/views.py:888), invoice_type='384' (sedo/views.py:909), reference_invoice=src_invoice (sedo/views.py:911), ruft mark_invoice_corrected aber bewusst nicht auf (sedo/views.py:941: `if src_invoice and action != 'draft'`). Probe: RE2026090001 typ=384 status=entwurf ref=RE2098010001, Original bleibt 'ueberfaellig'.

2. Alle Wächter greifen ab da gegen das Original, weil active_correction nur auf invoice_type und deleted_at filtert, nicht auf den Status (sedo/models.py:228-229).
Gemessene Meldungen: Zahlung → „Rechnung RE2098010001 wurde durch eine Korrektur ersetzt. Bitte auf RE2026090001 buchen." (sedo/services/invoices.py:105-109); Storno → „…existiert bereits die Korrekturrechnung RE2026090001" (invoices.py:57-61); zweite Korrektur → invoices.py:80-83; „Restbetrag als bezahlt" ebenfalls gesperrt

---

## [MITTEL] Mahnwesen prueft weiter nur das Statusfeld – status='gemahnt' landet auf einer bereits ersetzten Rechnung

**Belegt durch:** Codelesen plus echter Laufzeit-Nachweis, keine Datei im Projekt geaendert.

(a) Codelesen: sedo/services/dunning.py (ganz), sedo/services/invoices.py:32-190, sedo/services/calc.py:74-125, sedo/models.py:120-250, sedo/views.py:601-615, 877-990, 1026-1100, 1271-1355, 1546-1561, sedo/templates/sedo/invoice_form.html:6/229, sedo/templates/sedo/open_items.html:37-40, sedo/urls.py. Ausserdem `git diff HEAD` der Etappe 3 – dunning.py kommt darin nicht vor.

(b) Laufzeitprobe: eigenes Skript im Scratchpad (/private/tmp/claude-501/-Users-mirekdobberahn/cb40cf0f-a05c-4cc2-8297-abe531d98d90/scratchpad/pr

**Begruendung:** Ich habe vier Widerlegungswege versucht; alle vier scheitern am Code und an der Probe.

1) "Ein Original mit Korrektur traegt immer status='korrigiert', also greift die Statussperre." — Falsch. sedo/views.py:942-945: `if src_invoice and action != 'draft': mark_invoice_corrected(src_invoice)`. Beim Knopf "Entwurf" (sedo/templates/sedo/invoice_form.html:229, das Feld correction_of steht im selben Formular, Zeile 6) wird das Original NICHT markiert. Der Beleggraph kennt die Korrektur trotzdem: inv.invoice_type='384' und inv.reference_invoice=src_invoice werden vorher gesetzt (sedo/views.py:907-910), damit liefert Invoice.active_correction (sedo/models.py:228-230) den Entwurf. Der Kommentar in views.py:943-944 ("Bei Entwuerfen greift bereits die Folgebeleg-Sperre") stimmt fuer ensure_storno_allowed/ensure_correction_allowed (sedo/services/invoices.py:52-60, 80-88) und fuer ensure_zahlung_erlaubt (invoices.py:105-112) und ausblenden_hindernis (invoices.py:176-178) – aber NICHT fuer das Mahnwesen.

2) "get_dunning_candidates filtert doch ueber offene_rechnungen()/den Beleggraphen." — Falsch. sedo/services/dunning.py:183-190 filtert ausschliesslich `.exclude(status__in=_UNDUNNABLE_STATUS)` + due_date, danach nur noch next_dunning_kind (dunning.py:46) und open_amount (dunning.py:196). _UNDUNNABLE_STATUS = Invoice.GESPERRT_FUER_MAHNUNG = ('entwurf','korrigiert','storniert','bezahlt') (sedo/models.py:144-147). 'ueberfaellig' ist nicht dabei. ensure_dunning_allowed (dunning.py:63) pruef

---

## [NIEDRIG] Reiter „Alle ohne Storno": Spalte „Offen" und KPI-Karte widersprechen sich bei korrigierten Originalen, Zeilenaktionen prallen ab

**Belegt durch:** Zwei eigene Django-Test-Client-Proben im Scratchpad (/private/tmp/claude-501/-Users-mirekdobberahn/cb40cf0f-a05c-4cc2-8297-abe531d98d90/scratchpad/probe_all_tab.py und probe_echter_weg.py), gestartet mit `DJANGO_ALLOWED_HOSTS=testserver,127.0.0.1 PYTHONPATH=<scratchpad> .venv/bin/python manage.py test <modul>` gegen die In-Memory-Test-DB; keine Projektdatei angefasst (git status zeigt weiterhin nur die 5 Etappe-3-Dateien), data/sedo_web.sqlite3 nicht geoeffnet. Probe 1 (Szenario des Kollegen: vier offene Rechnungen ueber 3.590,00 EUR plus korrigiertes Original ueber 1.190,00 EUR, GET /offene-p

**Begruendung:** Der Fund ist im aktuellen Etappe-3-Stand reproduzierbar und liess sich nicht widerlegen. Belege: sedo/views.py:1286-1289 entfernt auf dem Reiter 'all' nur 'entwurf', die Grundmenge (sedo/views.py:1281-1283) nur status='storniert' und invoice_type='457' – ein Original mit status='korrigiert' bleibt in der Liste. sedo/views.py:1297-1301 berechnet open_total dagegen mit dem Filter `i.status not in Invoice.NICHT_OFFENE_STATUS` (Konstante: sedo/models.py:146), zaehlt das Original also nicht mehr mit. sedo/models.py:218-220 macht open_amount zu reinem gross_total - paid_amount ohne jede Zustandspruefung (anders als is_overdue, sedo/models.py:241), und sedo/templates/sedo/open_items.html:90 rendert genau diesen Wert. Die KPI-Karte „offene Forderungen" (open_items.html:8) wird auf jedem Reiter unveraendert aus open_total gerendert, ist also nicht reiterabhaengig beschriftet oder ausgeblendet. Der Aktionsblock haengt nur an `{% if i.open_amount > 0 %}` (open_items.html:96); die Guards ensure_zahlung_erlaubt (sedo/services/invoices.py:94-114) und ensure_dunning_allowed (sedo/services/dunning.py:61-64) weisen die Klicks erst serverseitig ab (sedo/views.py:1361-1365, 1395-1399, 1554). Nicht vorbestehend: vor Etappe 3 lautete open_total `sum(... if i.status != 'bezahlt')`, Karte und Spalte zeigten auf dem 'all'-Reiter beide denselben Betrag – die Divergenz entsteht durch die Umstellung in dieser Etappe (die alte Zahl war allerdings sachlich falsch, insofern Restarbeit statt Rueckschritt).

---

## [NIEDRIG] KPI-Karte "offene Forderungen" auf Offene Posten haengt am Filter, am Suchfeld und an der 500er-Grenze

**Belegt durch:** Drei Wege kombiniert. (1) Codelesen: sedo/views.py:1271-1314, sedo/templates/sedo/open_items.html:7-12 und 45-56, sedo/services/calc.py:98-112, sedo/services/dunning.py:175-201, sedo/templates/sedo/dashboard.html:40, sedo/models.py:145-147 und 174-175; Herkunft der Zeilen ueber `git diff HEAD` und `git log -S "open_total = sum"` bzw. `-S "MAX_OPEN_ITEMS"`. (2) Echte Shell-Probe mit dem Django-Test-Client: Skript /private/tmp/claude-501/-Users-mirekdobberahn/cb40cf0f-a05c-4cc2-8297-abe531d98d90/scratchpad/probe_open_items.py, ausgefuehrt mit .venv/bin/python; es baut ueber django.test.runner.Di

**Begruendung:** Der Fund ist real und exakt reproduzierbar. open_total, overdue_total und overdue_count werden aus invoice_list gerechnet (sedo/views.py:1299-1302 und 1311), und invoice_list entsteht nach dem Reiterfilter (views.py:1281-1291), nach dem Suchfeld q (views.py:1292-1293) und nach der Kappung auf MAX_OPEN_ITEMS=500 (views.py:1294, Konstante views.py:54). dunning_candidates daneben (views.py:1303) fragt global Invoice.objects ab, ohne Filter oder q (sedo/services/dunning.py:183-190). Die Karte in sedo/templates/sedo/open_items.html:8 heisst unqualifiziert "offene Forderungen"; auf derselben Reihe steht in Zeile 11 die globale Mahnkandidatenzahl. Gemessene Werte aus der Probe: Standardaufruf 4.890,00 EUR bei 5 Zeilen; mit q=RE2026090040 700,00 EUR bei 1 Zeile, Karte weiterhin "offene Forderungen"; mit status=paid 0,00 EUR, daneben unveraendert 1 Mahnkandidat; bei 520 offenen Rechnungen zeigt die Karte 5.000,00 EUR, waehrend die DB-Summe 10.090,00 EUR betraegt. TEILWEISE WIDERLEGT ist nur das Konsistenzargument des Kollegen: dashboard.html:40 verlinkt auf open_items OHNE Parameter, also auf den Standardreiter status='open' (views.py:1273); dort stimmen Dashboard-Betrag und Kartenbetrag exakt ueberein (4.890,00 = 4.890,00). Die Abweichung entsteht erst durch aktives Filtern/Suchen oder oberhalb von 500 Belegen. ZWEI ERGAENZUNGEN, die der Fund nicht nennt: Invoice.Meta.ordering = ['-issue_date','-number'] (sedo/models.py:174-175) bedeutet, dass die Kappung ausgerechnet die aeltesten –

---

## [NIEDRIG] Archivliste zeigt "E-Mail" und "Korrektur" weiterhin bedingungslos (invoice_list.html:112-113) — bestaetigt, aber Schwere niedrig

**Belegt durch:** Drei Wege, alle nur lesend; die Live-DB data/sedo_web.sqlite3 wurde nicht geoeffnet.

1. Codelesen: invoice_list.html:60-116 komplett gelesen (kein umschliessendes if), sedo/views.py:768-801 (invoice_list-Queryset), sedo/views.py:1036-1106 (invoice_email/invoice_correction), sedo/services/invoices.py:66-127 (die beiden Waechter), sedo/static/sedo/app.css (.table-actions), `git diff HEAD` und `git status` fuer die Etappe-3-Abgrenzung.

2. Echte Renderprobe mit dem Django-Test-Client. Skript unter /private/tmp/claude-501/-Users-mirekdobberahn/cb40cf0f-a05c-4cc2-8297-abe531d98d90/scratchpad/probe

**Begruendung:** Der Fund ist real und im aktuellen Arbeitsstand reproduzierbar. Alle Widerlegungsversuche schlagen fehl:

1. Die Zeilen stehen genau so da. sedo/templates/sedo/invoice_list.html:112 rendert `<form method="post" action="{% url 'sedo:invoice_email' r.pk %}">…E-Mail…</form>`, Zeile 113 `<a href="{% url 'sedo:invoice_correction' r.pk %}">Korrektur</a>`. Die Schleife beginnt bei invoice_list.html:77 (`{% for r in invoices %}`) und enthaelt zwischen 77 und 116 keine einzige Statusbedingung um die Aktionsspalte (invoice_list.html:109-115); die einzigen `{% if %}` im Zeilenkoerper betreffen Leistungszeitraum (87), Badge-Farbe (93-100) und Dateilinks (104-106).

2. Kein anderes Template, kein CSS-Trick. `sedo/views.py:794` rendert genau 'sedo/invoice_list.html'; weitere Kopien liegen nur unter dist/ (Build-Artefakte). `.table-actions` ist in sedo/static/sedo/app.css:30/34/199 als `display:flex` definiert, blendet also nichts aus.

3. Kein Queryset-Filter rettet die Liste. sedo/views.py:774 nimmt `Invoice.objects.select_related(...).all()` — ohne jede Einschraenkung auf Belegzustand oder Rechnungsart; gefiltert wird nur nach q/Jahr/Monat/Status (views.py:776-785).

4. Die Zuordnung zu Etappe 3 stimmt. `git diff HEAD --stat` beruehrt nur invoice_detail.html, month_close.html, open_items.html, test_review_v2.py und views.py. Die beiden Bedingungen, gegen die die Liste jetzt abweicht, sind genau in diesem Diff entstanden: invoice_detail.html:19 (`{% if invoice.status != 'entwurf' %}` vor 

---

## [NIEDRIG] Offene Posten, Reiter 'Alle ohne Storno': korrigiertes Original bekommt vier Knoepfe, alle vier laufen in eine Fehlermeldung

**Belegt durch:** Ich habe versucht, den Fund zu widerlegen, und dafuer drei Wege kombiniert.

(1) RENDERPROBE mit dem Django-Test-Client. Standalone-Skript im Scratchpad: /private/tmp/claude-501/-Users-mirekdobberahn/cb40cf0f-a05c-4cc2-8297-abe531d98d90/scratchpad/probe_open_items.py. Es setzt sys.argv=['probe','test'] VOR django.setup(), damit config/settings.py:184-186 greift und MEDIA_ROOT/SEDO_BACKUP_DIR nach /tmp zeigen; die Test-DB ist 'file:memorydb_default?mode=memory&cache=shared'. Die Live-Datei data/sedo_web.sqlite3 wurde nicht geoeffnet, keine Projektdatei geaendert (git status unveraendert). Aufba

**Begruendung:** Der Fund ist real und in jedem Detail reproduzierbar. Ich konnte ihn nicht widerlegen. Meine fuenf Widerlegungsversuche und woran sie gescheitert sind:

VERSUCH 1: 'Ein korrigiertes Original erscheint auf dem Reiter all gar nicht.' Gescheitert. sedo/views.py:1286-1289 nimmt auf dem Reiter 'all' aus der Grundmenge (views.py:1279-1281, ohne status='storniert' und ohne Typ 457) nur zusaetzlich status='entwurf' heraus. 'korrigiert' bleibt stehen. Die Renderprobe bestaetigt es.

VERSUCH 2: 'open_amount ist bei einem ersetzten Original 0, also greift das {% if i.open_amount > 0 %} in open_items.html:96 nicht, und die drei Knoepfe fallen weg.' Gescheitert. sedo/models.py:217-219 rechnet open_amount = gross_total - paid_amount ganz ohne Statusbedingung. Das Original hat keine Zahlung, also 119.00 > 0. Interessant im Kontrast: models.py:236-243 (is_overdue) hat die Statusbedingung sehr wohl bekommen (NICHT_OFFENE_STATUS), open_amount nicht - genau diese Asymmetrie laesst die Knoepfe stehen.

VERSUCH 3: 'refresh_invoice_payment_status (views.py:1295-1296) rechnet den Status um, die Zeile heisst gar nicht mehr korrigiert.' Gescheitert - und zwar durch die Arbeit der Etappen 1/2: models.py:145 GESCHUETZTE_STATUS schuetzt 'korrigiert' vor dem abgeleiteten Zahlstatus. Die Probe zeigt den Status unveraendert auf 'korrigiert'.

VERSUCH 4: 'Der Zahlung-Knopf ist nur ein Link auf ein Formular, der laeuft nicht in eine Fehlermeldung.' Gescheitert. sedo/views.py:1361-1365 ruft ensure_zahlung_erl

---

## [NIEDRIG] Zahlungserinnerung und Mahnung bleiben sichtbar, nachdem sie erzeugt wurden – der zweite Klick ist immer eine Fehlermeldung

**Belegt durch:** Codelesen plus echter Testlauf mit dem Django-Test-Client. Probe-Modul im Scratchpad (/private/tmp/claude-501/-Users-mirekdobberahn/cb40cf0f-a05c-4cc2-8297-abe531d98d90/scratchpad/probe_dunning_buttons.py), per PYTHONPATH an 'manage.py test probe_dunning_buttons' uebergeben – im Projekt wurde keine Datei angelegt oder geaendert, die Live-DB nicht geoeffnet (Django nutzt fuer SQLite ohne TEST-NAME eine In-Memory-Test-DB).

Ergebnis (ueberfaellige Rechnung, Status versendet, 30 Tage): Start – beide Knoepfe sichtbar. 1. Klick Zahlungserinnerung -> 'Zahlungserinnerung wurde erzeugt.', Status danac

**Begruendung:** Der Fund ist real und nicht widerlegbar. Die Bedingung in sedo/templates/sedo/invoice_detail.html:23 kennt nur invoice_type und status; beide Mahn-Formulare (Zeile 24 und 25) hängen an dieser einen Bedingung. sedo/views.py:1011-1021 (invoice_detail) uebergibt keinerlei Mahnstufen-Information in den Kontext. Entscheidend: sedo/services/dunning.py:169-171 setzt den Status nach dem Erzeugen auf 'gemahnt', und 'gemahnt' (sedo/models.py:126) steht nicht in der Ausschlussliste der Zeile 23 – die Knoepfe bleiben also zwangslaeufig stehen. ensure_dunning_allowed (sedo/services/dunning.py:61-72) lehnt jede zweite Erinnerung/Mahnung derselben Stufe mit einer Fehlermeldung ab, die der View (sedo/views.py:1554-1559) als messages.error anzeigt.

ZWEI KORREKTUREN am Fund: (1) Der Satz, die Meldung 'eine Zahlungserinnerung waere ein Rueckschritt in der Mahnstufe' (dunning.py:73-76) erreiche den Nutzer nie, ist falsch. Gegenprobe im Testlauf: klickt man zuerst 'Mahnung' und danach 'Zahlungserinnerung', ist die Gleichstufen-Liste leer, die Pruefung faellt durch und genau diese Meldung erscheint. Unerreichbar ist sie nur in der Folge Erinnerung -> Mahnung. (2) Es ist kein Regress dieser Etappe: vor dem Diff standen beide Formulare ganz ohne Bedingung da (git diff HEAD); Etappe 3 hat den Status-/Art-Filter erst eingefuehrt und damit entwurf/bezahlt/storniert/korrigiert ausgesperrt. Der Fund beschreibt die verbliebene Luecke.

WARNUNG ZUM VORSCHLAG: Nur den Knopf fuer next_dunning_kind(invoice) 

---

## [NIEDRIG] SMTP-Ausnahme nach erfolgreicher Zustellung: kein Statuswechsel, kein Protokoll – die versendete Rechnung bleibt ausblendbar

**Belegt durch:** Codelesen plus vier echte Testlaeufe. Kein Projektdateizugriff schreibend, die Live-DB wurde nicht geoeffnet (git status vor und nach der Pruefung identisch: dieselben 5 geaenderten Dateien des Etappe-3-Diffs).

1) Codelesen: sedo/views.py:1040-1093 (invoice_email), 1124-1150 (invoice_delete), 2370-2385 (month_close); sedo/services/invoices.py:130-190 (versand_nachweis, ausblenden_hindernis); sedo/services/mail.py:91-156 (send_email_smtp); sedo/audit.py:84-130 (write_audit); git diff HEAD zur Abgrenzung Etappe 3 gegen vorher.

2) Probe B und D als Django-Testklassen im Scratchpad (/private/tmp

**Begruendung:** Der Kern des Fundes haelt stand, seine Beweisfuehrung nicht.

WAS ICH NICHT WIDERLEGEN KONNTE (der Kern)
Statuswechsel und Protokolleintrag stehen tatsaechlich beide hinter dem Aufruf im selben try-Block: sedo/views.py:1061-1075 (send_email_smtp in Zeile 1062, save in 1068, write_audit mit metadata vorgang='versand' in 1071-1073); jede Ausnahme landet in sedo/views.py:1076-1079 und hinterlaesst nichts. versand_nachweis() kennt genau drei Quellen – Status 'versendet' (sedo/services/invoices.py:144), eine .eml-Datei unter media/emails/<nummer> (invoices.py:147-149, im SMTP-Weg entsteht keine) und AuditLog-Eintraege (invoices.py:151-155). Keine davon existiert nach dem Abbruch. ausblenden_hindernis() gibt dann '' zurueck (invoices.py:158-190), und der Waechter in invoice_delete (sedo/views.py:1132-1135) laesst durch. Probe B (Testlauf) bestaetigt Punkt fuer Punkt: Status 'erstellt', AuditLog leer, versand_nachweis()=False, ausblenden_hindernis()='', anschliessender POST auf invoice_delete setzt deleted_at, die Rechnung verschwindet aus Invoice.objects – und damit auch aus dem Monatsabschluss, der ueber Invoice.objects laeuft (sedo/views.py:2375).

WAS ICH WIDERLEGEN KONNTE (die Begruendung)
Der Kollege beruft sich auf sedo/services/mail.py:148-151 ("QUIT ueber den halb toten Socket schiebt einen OSError nach, der hier vorbeilaeuft") und nennt als Szenario OSError('Connection reset by peer') beim QUIT. Dieser konkrete Weg fuehrt NICHT zum Fund: smtplib wandelt einen OSError am So

---
