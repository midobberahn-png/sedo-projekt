"""Migrationstest in einem eigenen Prozess mit ausschließlich kopierter Datenbank."""
import os
from pathlib import Path


def check_restore(home):
    home = Path(home).resolve()
    os.environ['SEDO_HOME'] = str(home)
    os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
    import django
    django.setup()
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    from django.apps import apps
    try:
        executor = MigrationExecutor(connection)
        unknown = set(executor.loader.applied_migrations) - set(executor.loader.disk_migrations)
        if unknown:
            raise ValueError('Die Sicherung stammt aus einer unbekannten oder neueren Programmversion.')
        executor.loader.check_consistent_history(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        with connection.cursor() as cursor:
            for model in apps.get_models():
                if not model._meta.managed or model._meta.proxy:
                    continue
                actual = {column.name for column in connection.introspection.get_table_description(cursor, model._meta.db_table)}
                if not {field.column for field in model._meta.local_fields}.issubset(actual):
                    raise ValueError('Die Sicherung hat eine unvollständige Tabellenstruktur.')
            connection.check_constraints()
        # Keine Sitzungen aus manuell erzeugten oder alten Sicherungen übernehmen.
        from django.contrib.sessions.models import Session
        Session.objects.all().delete()
    finally:
        connection.close()


def main(home):
    try:
        check_restore(home)
    except Exception as exc:
        print(f'Wiederherstellungssimulation fehlgeschlagen: {exc}')
        return 1
    print('Wiederherstellungssimulation einschließlich Migrationen bestanden.')
    return 0
