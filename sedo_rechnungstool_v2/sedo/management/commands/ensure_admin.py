"""Legt den lokalen Admin-Benutzer an – seit v3.21 mit Zufallspasswort.

Hintergrund: Die Startskripte binden an 0.0.0.0 (ganzes Heimnetz erreichbar)
und die App ist zeitweise per Cloudflare-Tunnel erreichbar – ein dokumentiertes
Standard-Passwort wäre ein echtes Risiko. Das Passwort wird deshalb einmalig
zufällig erzeugt und nur beim Anlegen angezeigt.
"""
import secrets

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Erstellt den lokalen Admin-Benutzer (Zufallspasswort) bzw. warnt bei Standard-Passwort.'

    def handle(self, *args, **options):
        User = get_user_model()
        user = User.objects.filter(username='admin').first()
        if user is None:
            password = secrets.token_urlsafe(24)  # 32 Zeichen, URL-sicher; oberhalb der Mindestlänge
            User.objects.create_superuser('admin', 'admin@local.test', password)
            self.stdout.write(self.style.SUCCESS('Lokaler Benutzer erstellt: admin'))
            self.stdout.write(self.style.SUCCESS(f'Einmalig angezeigtes Passwort: {password}'))
            self.stdout.write('Bitte jetzt notieren! Später änderbar mit: python manage.py changepassword admin')
        elif user.check_password('sedo1234'):
            self.stdout.write(self.style.WARNING('⚠ Der Benutzer "admin" nutzt noch das alte Standard-Passwort "sedo1234".'))
            self.stdout.write(self.style.WARNING('  Bitte ändern: python manage.py changepassword admin'))
        else:
            self.stdout.write('Benutzer admin existiert bereits.')
