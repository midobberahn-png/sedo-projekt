"""F-18: fehlende Originaldateien aus einer expliziten Sicherungsquelle ergänzen."""
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from sedo.audit import AuditWriteError
from sedo.models import Invoice
from sedo.services.invoice_archive import stelle_archiv_wieder_her
from sedo.services.workflow_errors import WorkflowError


class Command(BaseCommand):
    help = ('Fehlende Originale aus einer Medien-Sicherung prüfen; --anwenden übernimmt '
            'nur Bytes mit passender ursprünglicher SHA-256-Prüfsumme. Keine Neuerzeugung.')
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument('nummer', help='Exakte Rechnungsnummer')
        parser.add_argument('--quelle', required=True, type=Path,
                            help='Sicherungsordner mit relativer Medienstruktur (z. B. generated/RE.../)')
        parser.add_argument('--anwenden', action='store_true', help='Fehlende Originalbytes tatsächlich ergänzen')

    def handle(self, *args, **options):
        try:
            invoice = Invoice.objects.get(number=options['nummer'])
            report = stelle_archiv_wieder_her(invoice, options['quelle'], anwenden=options['anwenden'])
        except (Invoice.DoesNotExist, WorkflowError, AuditWriteError, DatabaseError, OSError) as exc:
            raise CommandError(f'Wiederherstellung abgebrochen: {exc}') from exc
        self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))
