"""Optional worker for web installations; desktop starts its own worker."""
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from sedo.models import MailAccount
from sedo.services.mail_imap import sync_account
from sedo.services.mail_secrets import MailError
from sedo.services.mail_worker import recover_interrupted_sends


class Command(BaseCommand):
    help = 'Ruft eingerichtete, aktive Postfächer ab. Versendet keine E-Mails.'

    def add_arguments(self, parser):
        parser.add_argument('--account', type=int, help='Nur dieses aktive Postfach abrufen.')
        parser.add_argument('--watch', action='store_true', help='Bis zum Beenden jede Minute abrufen.')

    def handle(self, *args, **options):
        try:
            while True:
                close_old_connections()
                recover_interrupted_sends()
                accounts = MailAccount.objects.filter(enabled=True).exclude(password_encrypted='')
                if options['account']:
                    accounts = accounts.filter(pk=options['account'])
                    if not accounts.exists():
                        raise CommandError('Aktives Postfach mit Zugangsdaten nicht gefunden.')
                for pk in accounts.values_list('pk', flat=True):
                    try:
                        sync_account(pk)
                        self.stdout.write(f'Postfach {pk}: Abruf abgeschlossen.')
                    except MailError as exc:
                        self.stderr.write(f'Postfach {pk}: {exc}')
                if not options['watch']:
                    return
                time.sleep(60)
        except KeyboardInterrupt:
            self.stdout.write('Postfachabruf beendet.')
