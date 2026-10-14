"""Mahnwächter auf einer frischen dateibasierten SQLite-DB, nicht nur shared-memory.

Drei koordinierte Paare echter Verbindungen. Kein Bestands-DB-Parameter,
keine Laufzeitgrenzen als Erfolgskriterium, kein Server oder Mailversand.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    output = parser.parse_args().output.resolve()
    with tempfile.TemporaryDirectory(prefix='sedo_mahn_dateidb_') as tmp:
        home = Path(tmp)
        (home/'data').mkdir()
        os.environ.update(SEDO_HOME=str(home), DJANGO_SETTINGS_MODULE='config.settings',
                          DJANGO_SECRET_KEY='nur-synthetische-mahnprobe')
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import django
        django.setup()
        from datetime import timedelta
        from django.conf import settings
        from django.core.management import call_command
        from django.db import close_old_connections, connection, connections
        from django.utils import timezone
        from sedo.models import AuditLog, Customer, DunningDocument, Invoice, InvoiceItem
        from sedo.company_profile import save_profile
        from sedo.tests.company_fixtures import SYNTHETIC_PROFILE
        from sedo.services.invoices import create_storno, finalisiere_entwurf
        from sedo.services import invoices, dunning
        from sedo.services.workflow_errors import WorkflowError
        assert Path(settings.DATABASES['default']['NAME']).is_relative_to(home)
        call_command('migrate', interactive=False, verbosity=0)
        save_profile(SYNTHETIC_PROFILE)
        results = []
        try:
            customer = Customer.objects.create(
                name='TEST Mahnwächter Datei-SQLite', street='Testweg 1',
                postcode='12345', city='Teststadt', country='DE',
            )
            for mode in ('storno', 'korrektur', 'mahnung'):
                inv = Invoice.objects.create(customer=customer, number='RE-FILE-'+mode,
                    status='ueberfaellig', gross_total=119,
                    due_date=timezone.localdate()-timedelta(days=30))
                InvoiceItem.objects.create(invoice=inv, description='TEST Leistung', unit_price=100)
                stale = Invoice.objects.get(pk=inv.pk)
                if mode == 'storno':
                    target = 'sedo.services.invoices._create_storno_inner'
                    real = invoices._create_storno_inner
                    def action():
                        create_storno(Invoice.objects.get(pk=inv.pk))
                    expected = 'storniert'
                elif mode == 'korrektur':
                    draft = Invoice.objects.create(customer=customer, number='EN-FILE-KORREKTUR',
                        status='entwurf', invoice_type='384', reference_invoice=inv)
                    InvoiceItem.objects.create(invoice=draft, description='TEST Korrektur', unit_price=100)
                    target = 'sedo.services.invoices._generate_invoice_files_inner'
                    real = invoices._generate_invoice_files_inner
                    def action():
                        finalisiere_entwurf(Invoice.objects.get(pk=draft.pk))
                    expected = 'korrigiert'
                else:
                    target = 'sedo.services.dunning._render_dunning_pdf'
                    real = dunning._render_dunning_pdf
                    def action():
                        dunning.generate_dunning_document(Invoice.objects.get(pk=inv.pk))
                    expected = 'gemahnt'
                ready, release, attempted = threading.Event(), threading.Event(), threading.Event()
                writer_result, dunning_result, statements = [], [], []
                def held(*args, **kwargs):
                    value = real(*args, **kwargs)
                    ready.set()
                    if not release.wait(15):
                        raise RuntimeError('Schreibprobe wurde nicht freigegeben')
                    return value
                def writer():
                    close_old_connections()
                    try:
                        action()
                        writer_result.append('ok')
                    except Exception as exc:
                        writer_result.append(repr(exc))
                    finally:
                        connections.close_all()
                def record(execute, sql, params, many, context):
                    # Profil-Lektüre darf den konkurrierenden Schreibversuch
                    # nicht vorzeitig freigeben. Maßgeblich ist die erste
                    # Berührung der betroffenen Rechnung.
                    upper = sql.lstrip().upper()
                    if (upper.startswith(('UPDATE', 'SELECT', 'INSERT', 'DELETE'))
                            and 'SEDO_INVOICE' in upper):
                        statements.append(sql)
                        attempted.set()
                    return execute(sql, params, many, context)
                def second():
                    close_old_connections()
                    try:
                        with connection.execute_wrapper(record):
                            dunning.generate_dunning_document(stale)
                        dunning_result.append('UNERLAUBT erzeugt')
                    except WorkflowError as exc:
                        dunning_result.append(str(exc))
                    except Exception as exc:
                        dunning_result.append('FEHLER '+repr(exc))
                    finally:
                        connections.close_all()
                with patch(target, side_effect=held):
                    first_thread = threading.Thread(target=writer)
                    second_thread = threading.Thread(target=second)
                    first_thread.start()
                    try:
                        assert ready.wait(15), writer_result
                        second_thread.start()
                        assert attempted.wait(15), dunning_result
                    finally:
                        release.set()
                        first_thread.join(15)
                        if second_thread.ident is not None:
                            second_thread.join(15)
                    assert not first_thread.is_alive() and not second_thread.is_alive()
                assert writer_result == ['ok'], writer_result
                assert statements and statements[0].lstrip().upper().startswith('UPDATE'), statements
                assert len(dunning_result) == 1 and 'FEHLER' not in dunning_result[0], dunning_result
                assert 'UNERLAUBT' not in dunning_result[0], dunning_result
                # Nicht nur ein Lockfehler: die wartende Verbindung hat den
                # jetzt committed Folgebeleg bzw. das Stufenduplikat gelesen.
                assert 'gerade bearbeitet' not in dunning_result[0], dunning_result
                inv.refresh_from_db()
                assert inv.status == expected, inv.status
                count = DunningDocument.objects.filter(invoice=inv).count()
                assert count == (1 if mode == 'mahnung' else 0), count
                audits = AuditLog.objects.filter(metadata__anlass='mahnung', metadata__nummer=inv.number).count()
                assert audits == count
                results.append({'fall': mode, 'writer': 'ok', 'zweiter_aufruf': dunning_result[0],
                                'status': inv.status, 'mahndokumente': count, 'pflichtaudits': audits,
                                'erstes_statement': 'UPDATE', 'ergebnis': 'bestanden'})
            assert connection.cursor().execute('PRAGMA integrity_check').fetchall() == [('ok',)]
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps({'django': django.get_version(), 'faelle': results,
                'daten': 'frische synthetische Datei-SQLite; nach Probe entfernt'}, indent=2, ensure_ascii=False))
            print('3/3 koordinierte Datei-SQLite-Gegenläufe bestanden; '+str(output))
        finally:
            connections.close_all()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
