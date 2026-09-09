"""Etappe 7: reproduzierbarer SQL-Vergleich auf ausschließlich synthetischer Temp-DB.

Misst den echten ersten SELECT von get_dunning_candidates und den Statusfilter
von invoice_list, nicht die gesamte Webanwendung oder die Python-Mahnlogik.
Kein Bestandszugriff, kein Versand, keine Änderung an Anwendungsquellen.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import sqlite3
import statistics
import sys
import tempfile
import time

APP = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rows', type=int, default=50000)
    parser.add_argument('--repeats', type=int, default=9)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 100 <= args.rows <= 200000 or not 3 <= args.repeats <= 30:
        parser.error('Erlaubt: 100–200000 Zeilen, 3–30 Wiederholungen.')
    out = args.output.resolve()
    with tempfile.TemporaryDirectory(prefix='sedo_index_benchmark_') as tmp:
        home = Path(tmp)
        (home / 'data').mkdir()
        os.environ.update(SEDO_HOME=str(home), DJANGO_SETTINGS_MODULE='config.settings',
                          DJANGO_SECRET_KEY='synthetischer-indexbenchmark-kein-produktionsschluessel')
        sys.path.insert(0, str(APP))
        import django
        django.setup()
        from datetime import date, datetime, timedelta, timezone as utc
        from django.conf import settings
        from django.core.management import call_command
        from django.db import connection, connections, transaction
        from django.test import RequestFactory
        from django.test.utils import CaptureQueriesContext
        from unittest.mock import patch
        from sedo.models import Customer, Invoice
        from sedo.services.dunning import get_dunning_candidates
        from sedo.views import invoice_list

        assert Path(settings.DATABASES['default']['NAME']).is_relative_to(home)
        try:
            call_command('migrate', 'sedo', '0012', interactive=False, verbosity=0)
            today = date(2026, 9, 9)
            with patch('sedo.services.dunning.timezone.localdate', return_value=today), CaptureQueriesContext(connection) as captured:
                get_dunning_candidates(14)
            dunning_sql = captured[0]['sql']
            request = RequestFactory().get('/rechnungen/', {'status': 'entwurf'})
            with patch('sedo.views.render', side_effect=lambda r, t, c: c):
                context = invoice_list.__wrapped__(request)
            status_sql, status_params = context['invoices'].query.sql_with_params()
            queries = {'mahnkandidaten_select': (dunning_sql, ()),
                       'rechnungslisten_statusfilter': (status_sql, status_params)}
            # 85 % bezahlt; übrige Status deterministisch und unabhängig vom Datum verteilt.
            statuses = (['bezahlt'] * 85 + ['entwurf'] * 3 + ['storniert'] * 2 +
                        ['korrigiert'] * 2 + ['erstellt'] * 3 + ['versendet'] * 2 +
                        ['teilbezahlt', 'gemahnt', 'ueberfaellig'])
            customers = [Customer.objects.create(name=f'TEST-IDX Kunde {i}') for i in range(44)]
            for start in range(0, args.rows, 1000):
                Invoice.objects.bulk_create([
                    Invoice(number=f'TEST-IDX-{i:07}', customer=customers[i % 44],
                        status=statuses[(i * 37) % 100], gross_total='119.00',
                        issue_date=today - timedelta(days=i % 400),
                        due_date=None if i % 17 == 0 else today + timedelta(days=(i * 29) % 730 - 365),
                        deleted_at=datetime(2026, 8, 1, tzinfo=utc.utc) if i % 13 == 0 else None)
                    for i in range(start, min(start + 1000, args.rows))])
            cursor = connection.cursor()
            report = {'python': platform.python_version(), 'django': django.get_version(),
                      'sqlite': sqlite3.sqlite_version, 'rows': args.rows, 'repeats': args.repeats,
                      'analyze': False, 'daten': 'ausschließlich synthetisch; frische temporäre SQLite-DB',
                      'messumfang': 'SQL SELECT inklusive fetchall; kein End-to-End-Webbenchmark',
                      'queries': {k: {'sql': sql, 'params': params} for k, (sql, params) in queries.items()},
                      'varianten': {}}
            reference = {}
            variants = [
                ('ohne_neue_indizes', []),
                ('urspruenglicher_plan', [
                    'CREATE INDEX invoice_status_idx ON sedo_invoice(status)',
                    'CREATE INDEX invoice_status_due_idx ON sedo_invoice(status, due_date)']),
                ('umsetzung_etappe7', [
                    'DROP INDEX invoice_status_idx', 'DROP INDEX invoice_status_due_idx']),
            ]
            for variant, ddl in variants:
                for sql in ddl:
                    cursor.execute(sql)
                if variant == 'umsetzung_etappe7':
                    call_command('migrate', 'sedo', '0013', interactive=False, verbosity=0)
                result = {}
                for name, (sql, params) in queries.items():
                    # Ein unterschiedlicher Kommentar vermeidet veraltete EXPLAIN-Cacheeinträge.
                    plan = [r[3] for r in cursor.execute('EXPLAIN QUERY PLAN ' + sql + ' -- ' + variant, params).fetchall()]
                    cursor.execute(sql, params).fetchall()  # Aufwärmlauf, nicht gemessen.
                    durations = []
                    for _ in range(args.repeats):
                        start = time.perf_counter()
                        rows = cursor.execute(sql, params).fetchall()
                        durations.append((time.perf_counter() - start) * 1000)
                    digest = hashlib.sha256(json.dumps(sorted(rows, key=repr), default=str,
                                ensure_ascii=False).encode()).hexdigest()
                    if name not in reference:
                        reference[name] = digest
                    assert digest == reference[name], 'Abweichende Ergebnisdaten: ' + name
                    steps = [0]
                    def count_steps():
                        steps[0] += 1000
                        return 0
                    connection.connection.set_progress_handler(count_steps, 1000)
                    try:
                        cursor.execute(sql, params).fetchall()
                    finally:
                        connection.connection.set_progress_handler(None, 0)
                    result[name] = {'plan': plan, 'rows': len(rows), 'sha256_ergebnis': digest,
                                    'median_ms': statistics.median(durations), 'samples_ms': durations,
                                    'vm_steps_approx': steps[0]}
                # Schreibkosten mitmessen, Rollback lässt die Messdaten unverändert.
                writes = []
                for _ in range(args.repeats):
                    with transaction.atomic():
                        start = time.perf_counter()
                        Invoice.all_objects.filter(pk__lte=500).update(status='erstellt')
                        writes.append((time.perf_counter() - start) * 1000)
                        transaction.set_rollback(True)
                result['status_update_500_rollback_median_ms'] = statistics.median(writes)
                size = cursor.execute('PRAGMA page_size').fetchone()[0]
                pages = cursor.execute('PRAGMA page_count').fetchone()[0]
                free = cursor.execute('PRAGMA freelist_count').fetchone()[0]
                result['belegte_seiten_bytes'] = (pages - free) * size
                report['varianten'][variant] = result
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
            print(f'{args.rows} synthetische Rechnungen; alle Ergebnis-Hashes gleich. Bericht: {out}')
        finally:
            connections.close_all()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
