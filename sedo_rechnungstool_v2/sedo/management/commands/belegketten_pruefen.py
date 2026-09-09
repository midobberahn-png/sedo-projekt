"""Lesender Belegketten-Bericht; Statusreparatur nur mit --reparieren."""
import json

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from sedo.audit import AuditWriteError
from sedo.services.belegketten import BelegkettenKonflikt, pruefe_belegketten


class Command(BaseCommand):
    help = ('Belegketten ohne Änderungen prüfen. --reparieren setzt belegbare Statuswerte '
            'mit Pflicht-Audit zurück (ganzer Lauf atomar). Keine Dateien/Nummern ändern.')
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument('--reparieren', action='store_true',
                            help='Gruppen A/B und falsch bezahlte Stornos (D) protokolliert reparieren.')
        parser.add_argument('--json', action='store_true', dest='als_json',
                            help='Einen JSON-Bericht (Schema 1) auf stdout ausgeben.')

    def handle(self, *args, **options):
        try:
            bericht = pruefe_belegketten(reparieren=options['reparieren'])
        except (AuditWriteError, BelegkettenKonflikt, DatabaseError) as exc:
            text = ('Reparatur abgebrochen; keine Reparatur dieses Laufs wurde übernommen. '
                    if options['reparieren'] else 'Lesender Prüflauf fehlgeschlagen. ') + str(exc)
            if options['als_json']:
                self.stdout.write(json.dumps({
                    'schema_version': 1,
                    'modus': 'reparatur' if options['reparieren'] else 'trockenlauf',
                    'fehler': text, 'aenderungen': [],
                }, ensure_ascii=False, indent=2))
            raise CommandError(text) from exc
        if options['als_json']:
            self.stdout.write(json.dumps(bericht, ensure_ascii=False, indent=2))
            return
        self.stdout.write('Belegketten – ' + ('REPARATUR' if options['reparieren'] else
                                            'TROCKENLAUF (keine Änderungen)'))
        self.stdout.write('Aktiv = nicht ausgeblendet und nicht Entwurf. Dateien werden nicht geöffnet.')
        for fund in bericht['funde']:
            ziel = f" → {fund['nach']}" if fund['reparierbar'] else ' (nur Hinweis)'
            self.stdout.write(f"[{fund['gruppe']}] {fund['nummer']}: {fund['von']}{ziel}. {fund['hinweis']}")
            for key, label in (('storno_nummern', 'Storno'), ('korrektur_nummern', 'Korrektur')):
                if fund[key]:
                    self.stdout.write(f"    {label}: {', '.join(fund[key])}")
        s = bericht['zusammenfassung']
        self.stdout.write(f"{s['belege_geprueft']} Belege geprüft; {s['befunde']} Befunde an "
                          f"{s['betroffene_belege']} Belegen; {s['reparierbar']} reparierbar.")
        if options['reparieren']:
            rest = bericht['nachpruefung']['zusammenfassung']
            self.stdout.write(f"{len(bericht['aenderungen'])} Änderungen mit Pflicht-Audit übernommen. "
                              f"Verbleibend: {rest['befunde']} Befunde, davon {rest['kettenfehler']} Kettenfehler.")
        elif s['reparierbar']:
            self.stdout.write('Nach Sicherung und Prüfung: python manage.py belegketten_pruefen --reparieren')
        self.stdout.write('C (Entwurfs-XML-Verweise) und alte Entwürfe (D) werden nicht automatisch repariert.')
