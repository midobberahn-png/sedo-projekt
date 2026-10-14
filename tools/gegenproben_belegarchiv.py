"""F-18: Entfernung der Schutzmaßnahmen muss fachliche Assertions brechen."""
import gegenproben_entwurf as runner

runner.TEST = 'sedo.tests.test_invoice_archive.InvoiceArchiveTests.'
ARCHIV = 'sedo/services/invoice_archive.py'
runner.MUTATIONEN = [
    ('wiederverwendung_ohne_generator', 'sedo/services/invoices.py',
     '        return invoice  # F-18: kein Recalc, keine Datei-/DB-Schreiboperation.',
     '        return _generate_invoice_files_inner(invoice)',
     'test_wiederverwenden_berechnet_nichts_neu_und_schreibt_nichts'),
    ('fremde_belegdateien', ARCHIV,
     '    if (Invoice.all_objects.exclude(pk=invoice.pk).filter(query).exists()',
     '    if False and (Invoice.all_objects.exclude(pk=invoice.pk).filter(query).exists()',
     'test_entwurf_darf_keine_fremden_dateien_quarantaenieren'),
    ('vorhandene_erstausstellungsziele', ARCHIV,
     '        if refs or any(p.exists() for p in targets.values()):', '        if False:',
     'test_erstausstellung_ueberschreibt_auch_keine_verwaisten_ziele'),
    ('original_pruefsumme', ARCHIV,
     "    if proof and (len(data) != proof[field + '_bytes'] or",
     "    if False and (len(data) != proof[field + '_bytes'] or",
     'test_manipulierte_datei_wird_erkannt_nicht_repariert'),
    ('pflichtaudit_erstausstellung', ARCHIV,
     '                metadata=metadata, pflicht=True)', '                metadata=metadata, pflicht=False)',
     'test_archivauditfehler_rollt_dateien_und_verweise_zurueck'),
    ('rueckweg_nimmt_nur_eigene_dateien', 'sedo/services/invoice_files.py',
     '                        if ident and stat and ident == (stat.st_dev, stat.st_ino):',
     '                        if stat:',
     'test_race_beim_veroeffentlichen_bewegt_keine_fremde_datei'),
    ('eml_helper_ohne_neuerzeugung', 'sedo/invoice_views.py',
     "    attachment = lese_archivdatei(invoice)\n    recipient = invoice.customer.email or ''",
     "    generate_invoice_files(invoice)\n    attachment = lese_archivdatei(invoice)\n    recipient = invoice.customer.email or ''",
     'test_email_helper_erzeugt_keine_rechnungsdateien'),
    ('sammelmail_vollstaendigkeit', 'sedo/services/monthly_delivery.py',
     '    attachments = [lese_archivdatei(inv) for inv in invoices]',
     '    attachments = [lese_archivdatei(inv) for inv in invoices if inv.zugferd_file]',
     'test_sammelmail_darf_fehlenden_beleg_nicht_still_auslassen'),
    ('sicherungsbytes_verifizieren', ARCHIV,
     "            if (len(data) != proof[field + '_bytes'] or",
     "            if False and (len(data) != proof[field + '_bytes'] or",
     'test_wiederherstellung_prueft_alle_bytes_vor_dem_ersten_schreiben'),
    ('pflichtaudit_wiederherstellung', ARCHIV,
     "                                  'dateien': ','.join(missing), 'verweise': ','.join(links)}, pflicht=True)",
     "                                  'dateien': ','.join(missing), 'verweise': ','.join(links)}, pflicht=False)",
     'test_wiederherstellung_auditfehler_nimmt_nur_eigene_dateien_zurueck'),
]

if __name__ == '__main__':
    raise SystemExit(runner.main())
