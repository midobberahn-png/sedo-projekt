"""Explizite Abo-Auswahl / F-68: alte Regeln müssen fachliche Assertions brechen."""
import gegenproben_entwurf as runner

runner.TEST = 'sedo.tests.test_hardening_selection_due.'
VIEWS = 'sedo/views.py'
DUNNING = 'sedo/services/dunning.py'
runner.MUTATIONEN = [
    ('leere_auswahl_wieder_alle', VIEWS,
     "    if not selected_ids:\n        messages.error(request, 'Bitte mindestens eine aktive Abo-Vorlage auswählen. Es wurde keine Rechnung erzeugt.')\n        return redirect('sedo:recurring_monthly')",
     "    if not selected_ids:\n        selected_ids = list(RecurringInvoiceTemplate.objects.filter(status='active').values_list('pk', flat=True))",
     'RecurringSelectionHardeningTests.test_leere_auswahl_ist_kein_gesamtlauf'),
    ('ungueltiger_teil_wird_ignoriert', VIEWS,
     '    if {tmpl.pk for tmpl in templates} != selected_ids:',
     '    if False:',
     'RecurringSelectionHardeningTests.test_pausierte_oder_fehlende_vorlage_lehnt_ganze_auswahl_ab'),
    ('faelligkeitstag_zu_frueh', DUNNING,
     '    if invoice.due_date >= today:', '    if invoice.due_date > today:',
     'DunningDueHardeningTests.test_faelligkeitstag_bleibt_zahlungstag'),
    ('fehlende_faelligkeit_freigegeben', DUNNING,
     "        return f'Rechnung {invoice.number} hat kein Fälligkeitsdatum und kann nicht gemahnt werden.'",
     "        return ''",
     'DunningDueHardeningTests.test_ohne_faelligkeitsdatum_keine_erfundene_frist'),
    ('null_restforderung_freigegeben', DUNNING,
     "    if invoice.open_amount <= Decimal('0.00'):",
     "    if invoice.open_amount < Decimal('0.00'):",
     'DunningDueHardeningTests.test_vollbezahlt_trotz_altem_status_gesperrt'),
    ('generator_guard_umgangen', DUNNING,
     '                ensure_dunning_allowed(current, use_kind)',
     '                pass',
     'DunningDueHardeningTests.test_generator_prueft_restbetrag_ohne_vorab_guard'),
]

if __name__ == '__main__':
    raise SystemExit(runner.main())
