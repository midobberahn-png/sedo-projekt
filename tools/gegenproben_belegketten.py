"""Etappe 5: gezielte Mutationen in Wegwerf-Quellkopien, niemals im Bestand.

Verwendet denselben bereits geprüften Testläufer wie Etappe 4/6. Zuerst muss
die unveränderte Auswahl grün sein; danach zählt nur ein Assertion-Fehler als
Nachweis. Syntaxfehler, Importfehler und fehlerhafte Testaufbauten zählen nicht.
"""
import gegenproben_entwurf as runner

SERVICE = 'sedo/services/belegketten.py'
runner.TEST = 'sedo.tests.test_belegketten.BelegkettenTests.'
runner.MUTATIONEN = [
    ('trockenlauf_schreibt_nicht', SERVICE,
     'def pruefe_belegketten(*, reparieren=False):',
     'def pruefe_belegketten(*, reparieren=True):',
     'test_f07_trockenlauf_meldet_vier_funde_ohne_schreibzugriff'),
    ('entwurfsfolgebelege_ausgeschlossen', SERVICE,
     "if folge.reference_invoice_id and folge.lifecycle != 'draft':",
     'if folge.reference_invoice_id:',
     'test_f13_korrekturentwurf_blockiert_original_nicht'),
    ('ausgeblendete_folgebelege_ausgeschlossen', SERVICE,
     "invoices = list(Invoice.objects.order_by('pk'))",
     "invoices = list(Invoice.all_objects.order_by('pk'))",
     'test_f14_ausgeblendeter_folgebeleg_zaehlt_nicht'),
    ('storno_vorrang', SERVICE,
     """                    'storniert', 'Storno hat Vorrang. Beide Folgebelege bitte prüfen.' if beide else
                    'Ausgestelltes, nicht ausgeblendetes Storno vorhanden.', nach_lifecycle='cancelled', bitte_pruefen=beide, **graph))""",
     """                    'korrigiert', 'Storno hat Vorrang. Beide Folgebelege bitte prüfen.' if beide else
                    'Ausgestelltes, nicht ausgeblendetes Storno vorhanden.', nach_lifecycle='corrected', bitte_pruefen=beide, **graph))""",
     'test_f07_storno_hat_vorrang_und_beide_nummern_sind_sichtbar'),
    ('zahlstatus_neu_ableiten', SERVICE,
     "    return abgeleiteter_zahlstatus(probe) or 'erstellt'",
     "    return 'erstellt'",
     'test_f14_gruppe_b_leitet_bezahlt_ab_und_auditiert_endzustand'),
    ('pflichtaudit', SERVICE,
     '        pflicht=True, metadata={',
     '        pflicht=False, metadata={',
     'test_auditfehler_rollt_status_und_zeitstempel_zurueck'),
    ('ganzlauf_rollback', SERVICE,
     '    with transaction.atomic():',
     '    from contextlib import nullcontext\n    with nullcontext():',
     'test_zweiter_auditfehler_rollt_ganzen_lauf_zurueck'),
    ('status_guard', SERVICE,
     "pk=fund['invoice_id'], status=fund['von'], lifecycle=fund['von_lifecycle'],",
     "pk=fund['invoice_id'], lifecycle=fund['von_lifecycle'],",
     'test_guard_weist_veralteten_status_ab'),
    ('revisions_guard', SERVICE,
     "        updated_at=fund['revision'],\n",
     "",
     'test_guard_weist_veraltete_revision_ab'),
    ('f89_storno_status', SERVICE,
     "elif invoice.invoice_type == '457' and invoice.status == 'bezahlt':",
     'elif False:',
     'test_f89_storno_bezahlt_wird_erstellt'),
    ('entwurf_nicht_ausstellen', SERVICE,
     "nicht_ausgestellt = invoice.lifecycle == 'draft' or invoice.number.startswith('EN')",
     'nicht_ausgestellt = False',
     'test_f13_entwurf_mit_folgebeleg_wird_nie_ausgestellt'),
    ('vollstaendiger_bestand', SERVICE,
     "invoices = list(Invoice.objects.order_by('pk'))",
     "invoices = list(Invoice.objects.order_by('pk')[:500])",
     'test_kein_500er_limit_und_ein_graph_select'),
    ('sicherheitsanzeige', 'sedo/views.py',
     "'ok': belegketten['kettenfehler'] == 0,", "'ok': True,",
     'test_security_zeigt_fehler_ohne_writes'),
    ('alte_entwuerfe_melden', SERVICE,
     'if timezone.localdate(invoice.created_at) < heute - timedelta(days=30):',
     'if False:', 'test_f13_alter_entwurf_nur_hinweis'),
    ('xml_verweise_melden', SERVICE,
     'if invoice.xml_file.name or invoice.zugferd_file.name:',
     'if False:', 'test_f13_auch_nur_zugferd_verweis_wird_gemeldet'),
    ('mehrdeutige_ketten_bleiben_sichtbar', SERVICE,
     "if invoice.lifecycle != 'cancelled' or beide:",
     "if invoice.lifecycle != 'cancelled':",
     'test_f07_storno_hat_vorrang_und_beide_nummern_sind_sichtbar'),
    ('sperre_vor_befundaufnahme', SERVICE,
     '            _sperre_reparaturlauf()', '            pass',
     'test_reparatur_schreibsperre_kommt_vor_select'),
]

if __name__ == '__main__':
    raise SystemExit(runner.main())
