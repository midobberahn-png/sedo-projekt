"""Create the local administrator and retire the public legacy password.

Only the exact historical default is replaced. Individual existing passwords
stay unchanged. Creation and replacement use the same one-time output consumed
by both desktop and source launchers, so a replacement is visible to the user.
"""
import secrets

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Erstellt den lokalen Admin-Benutzer und ersetzt ausschließlich das bekannte alte Standard-Passwort.'

    def handle(self, *args, **options):
        User = get_user_model()
        user = User.objects.filter(username='admin').first()
        if user is None:
            password = secrets.token_urlsafe(18)  # 24 URL-safe characters, 144 random bits.
            User.objects.create_superuser('admin', 'admin@local.test', password)
            self.stdout.write(self.style.SUCCESS('Lokaler Benutzer erstellt: admin'))
            self.stdout.write(self.style.SUCCESS(f'Einmalig angezeigtes Passwort: {password}'))
            self.stdout.write('Bitte jetzt notieren! Später änderbar mit: python manage.py changepassword admin')
        elif user.check_password('sedo1234'):
            previous_hash = user.password
            password = secrets.token_urlsafe(18)
            user.set_password(password)
            # A concurrent individual password change must never be overwritten.
            # Django session authentication hashes change with this stored hash,
            # automatically rejecting previously authenticated sessions.
            changed = User.objects.filter(pk=user.pk, password=previous_hash).update(password=user.password)
            if not changed:
                self.stdout.write('Benutzer admin wurde parallel geändert; das vorhandene Passwort bleibt erhalten.')
                return
            self.stdout.write(self.style.WARNING('Das bekannte alte Standard-Passwort wurde aus Sicherheitsgründen ersetzt.'))
            self.stdout.write(self.style.SUCCESS(f'Einmalig angezeigtes Passwort: {password}'))
            self.stdout.write('Bitte jetzt notieren! Später änderbar mit: python manage.py changepassword admin')
        else:
            self.stdout.write('Benutzer admin existiert bereits.')
