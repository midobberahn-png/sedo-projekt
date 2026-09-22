"""Preview or reversibly separate untouched, clearly misclassified imports."""
import json
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from sedo.services.incoming_invoices import reclassify_unreviewed


class Command(BaseCommand):
    help = 'Eindeutige Fehlimporte der Eingangsrechnungen prüfen; standardmäßig nur Vorschau.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--user')

    def handle(self, *args, **options):
        user = None
        if options['user']:
            user = get_user_model().objects.filter(username=options['user'], is_staff=True).first()
            if user is None:
                raise CommandError('Administrationskonto nicht gefunden.')
        result = reclassify_unreviewed(apply=options['apply'], user=user)
        self.stdout.write(json.dumps({'apply':options['apply'], 'candidates':len(result),
            'changed':sum(row['applied'] for row in result), 'documents':result}, ensure_ascii=False, indent=2))
