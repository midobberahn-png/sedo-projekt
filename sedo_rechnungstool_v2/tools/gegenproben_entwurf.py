"""Welle 2: Jeder entfernte Schutz muss seinen Regressionstest rot machen.

Ausführung mit der Test-Python-Umgebung: python tools/gegenproben_entwurf.py
Es werden NUR Kopien von Quellcode in temporären Verzeichnissen verändert.
Keine Betriebsdaten werden kopiert oder geöffnet; kein Git-Checkout/Reset.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

APP = Path(__file__).resolve().parents[1]
TEST = 'sedo.tests.test_draft_workflow.DraftWorkflowTests.'
INVOICES = 'sedo/services/invoices.py'
VIEWS = 'sedo/views.py'
# Name, Datei, eindeutiger Originaltext, Mutation, passender Test.
MUTATIONEN = [
    ('entwurf_xml_riegel', INVOICES,
     "    is_draft = invoice.status == 'entwurf'\n", "    is_draft = False\n",
     'test_f13_entwurf_ruft_keinen_xml_generator_auf'),
    ('en_statt_re_beim_anlegen', VIEWS,
     "save_with_unique_number(inv, 'EN' if action == 'draft' else 'RE')",
     "save_with_unique_number(inv, 'RE')", 'test_f13_neuer_entwurf_bekommt_en_ohne_re_verbrauch'),
    ('en_wird_re_beim_finalisieren', INVOICES,
     "            if invoice.number.startswith('EN'):", "            if False:",
     'test_f13_finalisieren_erzeugt_re_mit_xml_ohne_wasserzeichen'),
    ('re_altbestand_bleibt', INVOICES,
     "            if invoice.number.startswith('EN'):", "            if True:",
     'test_f13_bestandsentwurf_behaelt_re_nummer'),
    ('status_refresh_nach_guard', INVOICES,
     "    invoice.refresh_from_db()\n\n\ndef finalisiere_entwurf", "    pass\n\n\ndef finalisiere_entwurf",
     'test_f13_finalisieren_erzeugt_re_mit_xml_ohne_wasserzeichen'),
    ('doppelklick_guard', INVOICES,
     "    qs = Invoice.objects.filter(pk=invoice.pk, status='entwurf')",
     "    qs = Invoice.objects.filter(pk=invoice.pk)",
     'test_f13_veraltete_python_instanz_darf_nicht_erneut_finalisieren'),
    ('browser_revision', INVOICES,
     '        qs = qs.filter(updated_at=zeitpunkt)', '        qs = qs',
     'test_f14_zweites_browserfenster_ueberschreibt_nicht'),
    ('vormonatsbestaetigung', INVOICES,
     '                if not datum_bestaetigt:', '                if False:',
     'test_f13_vormonat_braucht_explizite_bestaetigung'),
    ('datum_heute_ausdruecklich', INVOICES,
     '            if datum_heute:', '            if False:',
     'test_f13_datum_heute_erhaelt_zahlungsziel_und_leistungszeitraum'),
    ('zahlungsziel_null', VIEWS,
     "timedelta(days=form.cleaned_data['payment_days'])",
     "timedelta(days=form.cleaned_data['payment_days'] or 14)",
     'test_f63_bearbeiten_erhaelt_zahlungsziel_null'),
    ('angebotsrueckabwicklung', VIEWS,
     "                            if invoice.status == 'entwurf' else [])",
     "                            if False else [])", 'test_f14_angebot_nach_entwurf_verwerfen_wieder_frei'),
    ('pflichtaudit', INVOICES,
     "                        pflicht=True, metadata={\n                            'von': 'entwurf'",
     "                        pflicht=False, metadata={\n                            'von': 'entwurf'",
     'test_f13_pflichtauditfehler_rollt_nummer_status_und_dateien_zurueck'),
    ('dateirueckweg_nach_veroeffentlichung', INVOICES,
     '            merken(invoice)\n            generate_invoice_files(invoice)',
     '            generate_invoice_files(invoice)',
     'test_f13_fehler_nach_dateiveroeffentlichung_entfernt_neue_xml_aus_medien'),
    ('alt_xml_nicht_mehr_ausliefern', INVOICES,
     '            quarantiniere_dateien(alt)', '            pass',
     'test_f13_bestands_xml_wird_erhalten_aber_nicht_mehr_ausgeliefert'),
    ('original_status_markieren', INVOICES,
     '            if original is not None:\n                mark_invoice_corrected',
     '            if False:\n                mark_invoice_corrected',
     'test_f14_korrekturentwurf_finalisiert_original'),
    ('storno_graph_trotz_falschem_status', INVOICES,
     ".exclude(pk=invoice.pk).exclude(status='entwurf').exists()):",
     ".exclude(pk=invoice.pk).exclude(status='entwurf').none().exists()):",
     'test_f14_storno_graph_blockiert_auch_bei_falschem_originalstatus'),
    ('verwerfensbestaetigung_revision', VIEWS,
     '                if bestaetigter_stand and bestaetigter_stand != invoice.updated_at.isoformat():',
     '                if False and bestaetigter_stand != invoice.updated_at.isoformat():',
     'test_f14_alte_verwerfensbestaetigung_verwirft_keine_finalisierte_rechnung'),
]


def testen(root: Path, namen: list[str], log: Path) -> subprocess.CompletedProcess:
    home = root.parent / (root.name + '_testhome')
    (home / 'data').mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, SEDO_HOME=str(home),
               DJANGO_SECRET_KEY='nur-isolierte-gegenprobe-kein-produktionsschluessel',
               DJANGO_ALLOWED_HOSTS='testserver,127.0.0.1,localhost', PYTHONDONTWRITEBYTECODE='1')
    command = [sys.executable, 'manage.py', 'test', *[TEST + n for n in namen], '--noinput']
    result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=120)
    log.write_text(result.stdout + result.stderr, encoding='utf-8')
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path(tempfile.mkdtemp(prefix='sedo_gegenproben_')))
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory(prefix='sedo_quellkopie_') as tmp:
        baseline = Path(tmp) / 'basis'
        baseline.mkdir()
        for name in ('sedo', 'config'):
            shutil.copytree(APP / name, baseline / name,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        shutil.copy2(APP / 'manage.py', baseline / 'manage.py')
        green = testen(baseline, sorted({m[4] for m in MUTATIONEN}), out / '00_basis.log')
        if green.returncode:
            raise RuntimeError(f'Unveränderte Basis ist nicht grün: {out / "00_basis.log"}')
        for index, (name, path, old, new, test) in enumerate(MUTATIONEN, 1):
            copy = Path(tmp) / name
            shutil.copytree(baseline, copy)
            source = copy / path
            text = source.read_text(encoding='utf-8')
            if text.count(old) != 1:
                raise RuntimeError(f'{name}: Mutationsanker ist nicht eindeutig.')
            changed = text.replace(old, new, 1)
            ast.parse(changed)  # Syntaxfehler sind keine fachliche Gegenprobe.
            source.write_text(changed, encoding='utf-8')
            run = testen(copy, [test], out / f'{index:02d}_{name}.log')
            # Ein ImportError oder ein kaputtes Test-Setup zählt NICHT als Nachweis.
            text = run.stdout + run.stderr
            erkannt = run.returncode != 0 and bool(re.search(r'FAILED \(failures=[1-9]', text)) and 'ERROR:' not in text
            results.append({'schutz': name, 'test': TEST + test, 'entfernen_macht_test_rot': erkannt})
            print(f'{name}: {"ROT wie erwartet" if erkannt else "NICHT BELEGT"}', flush=True)
        (out / 'ergebnisse.json').write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f'{sum(r["entfernen_macht_test_rot"] for r in results)}/{len(results)} Gegenproben belegt; Protokolle: {out}')
    return 0 if all(r['entfernen_macht_test_rot'] for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
