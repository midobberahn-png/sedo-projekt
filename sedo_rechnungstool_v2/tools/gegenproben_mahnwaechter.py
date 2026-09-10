"""Mahnwächter: entfernte Schutzmaßnahmen müssen echte Assertions brechen.

Wiederverwendet den Runner, der nur temporäre Quellkopien verändert. Keine
Bestandsdatenbank und kein Rücksetzen des Arbeitsverzeichnisses.
"""
import gegenproben_entwurf as runner

runner.TEST = 'sedo.tests.test_dunning_guard.'
SERVICE = 'sedo/services/dunning.py'
T = 'DunningGuardTests.'
runner.MUTATIONEN = [
    ('graphpruefung', SERVICE,
     '    if folge is not None:', '    if False:',
     T+'test_korrekturgraph_sperrt_trotz_falschem_status'),
    ('frischer_status', SERVICE,
     '    return Invoice.all_objects.filter(pk=invoice.pk).first()', '    return invoice',
     T+'test_alter_status_auf_pythonobjekt_wird_nicht_vertraut'),
    ('entwuerfe_sind_keine_folgebelege', SERVICE,
     "    ).exclude(status='entwurf')", '    )',
     T+'test_korrekturentwurf_blockiert_das_original_nicht'),
    ('ausgeblendete_folgebelege_zaehlen_nicht', SERVICE,
     "        deleted_at__isnull=True, invoice_type__in=('384', '457'),",
     "        invoice_type__in=('384', '457'),",
     T+'test_ausgeblendete_folgebelege_blockieren_nicht'),
    ('kandidaten_sql_graph', SERVICE,
     "\n        .filter(~Exists(_aktive_folgebelege().filter(reference_invoice_id=OuterRef('pk'))))\n",
     '\n', T+'test_kandidaten_filtern_graphen_im_sql_ohne_folgeabfragen'),
    ('zentraler_guard_vor_pdf', SERVICE,
     '                ensure_dunning_allowed(current, use_kind)', '                pass',
     T+'test_direkter_generator_blockiert_vor_pdf_und_audit'),
    ('schreibsperre_vor_lesen', SERVICE,
     "Invoice.objects.filter(pk=invoice.pk).update(updated_at=F('updated_at'))",
     'Invoice.objects.filter(pk=invoice.pk).exists()',
     T+'test_schreibsperre_ist_erstes_datenstatement'),
    ('guarded_status_update', SERVICE,
     'Invoice.objects.filter(pk=current.pk, status=before)', 'Invoice.objects.filter(pk=current.pk)',
     T+'test_statuswechsel_zwischen_guard_und_update_wird_nicht_ueberschrieben'),
    ('graph_am_update', SERVICE,
     "                               .filter(~Exists(_aktive_folgebelege().filter(reference_invoice_id=OuterRef('pk'))))\n",
     '', T+'test_neuer_folgebeleg_zwischen_guard_und_update_wird_nicht_ueberschrieben'),
    ('pflichtaudit', SERVICE,
     "f'{doc.get_kind_display()} zu {current.number} erzeugt', pflicht=True,",
     "f'{doc.get_kind_display()} zu {current.number} erzeugt', pflicht=False,",
     T+'test_pflichtauditfehler_rollt_gesamten_einzelbeleg_zurueck'),
    ('atomarer_einzelbeleg', SERVICE,
     '            with transaction.atomic():', "            with __import__('contextlib').nullcontext():",
     T+'test_pflichtauditfehler_rollt_gesamten_einzelbeleg_zurueck'),
    ('exklusive_veroeffentlichung', SERVICE,
     '                    os.link(temp_pdf, target)', '                    os.replace(temp_pdf, target)',
     T+'test_zwischenzeitlich_belegtes_dateiziel_bleibt_unveraendert'),
    ('automatik_vor_schreiben', SERVICE,
     '                    if current.due_date is None or current.due_date > cutoff or current.open_amount <= 0:',
     '                    if False:', T+'test_auto_prueft_geaenderte_faelligkeit_und_echte_zahlung_neu'),
    ('dateirueckweg_beim_commitfehler', SERVICE,
     '            if identity is not None and target is not None and target.is_file():',
     '            if False:',
     'DunningGuardConcurrencyTests.test_commitfehler_nimmt_eigene_veroeffentlichung_zurueck'),
]

if __name__ == '__main__':
    raise SystemExit(runner.main())
