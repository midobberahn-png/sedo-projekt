# Restabsicherung nach Welle 2 – F-63 / Abo-Zahlungsziel 0

Stand: 10.09.2026. Basis: main `7a653da452ebf8a33ac2a08b46950b559f0b220d`.
PR #1–5 sind integriert. Dieser Block enthält nur den Abo-Zahlungsziel-Restfix.
Laut Mirek kein Echtbetrieb, sämtliche vorhandenen Daten sind Testdaten.

## Bestätigter Fehler und Änderung

Im Monatslauf stand zuvor:

```python
due = issue_date + timedelta(days=tmpl.payment_days or tmpl.customer.payment_days or DEFAULT_PAYMENT_DAYS)
```

Eine explizite 0 wurde dadurch übersprungen. Zweite gleichartige Stelle:
`_create_recurring_template_from_invoice` ersetzte bei fehlender Rechnungsfälligkeit
den Kundenwert 0 durch 14. Beide Abo-Wege verwenden jetzt dieselbe kleine Funktion:

```python
def _recurring_payment_days(payment_days: int | None, customer_payment_days: int | None) -> int:
    """Vorlage/Rechnung vor Kundenwert; nur None fehlt, 0 bedeutet sofort fällig (F-63)."""
    if payment_days is not None:
        return payment_days
    if customer_payment_days is not None:
        return customer_payment_days
    return DEFAULT_PAYMENT_DAYS
```

Im Monatslauf zählt das Rechnungsdatum, nicht der Leistungsmonat und nicht das
heutige Datum. Beim Speichern einer Rechnung als Vorlage hat deren vorhandener
Abstand zwischen Rechnungs- und Fälligkeitsdatum Vorrang; auch Abstand 0 bleibt 0.

| Vorrangwert | Kundenwert | Ergebnis |
|---|---|---|
| 0 | 14 | 0 |
| 7 | 0 | 7 |
| None | 0 | 0 |
| None | 21 | 21 |
| None | None | DEFAULT_PAYMENT_DAYS (derzeit 14) |

Wichtig: Beide persistierten Zahlungszielfelder sind nicht nullable und bleiben
Pflichtfelder. Ein leeres Vorlagenformular wird weiterhin abgewiesen. Der Standard
bei einer neuen Vorlage bleibt 14; keine neue implizite Vererbung vom Kunden.
Die None-Zweige bewahren den bisherigen defensiven Rückfall, aber jetzt ohne
Verwechslung von 0 und fehlend. Sie werden mit ungespeicherten Testobjekten geprüft,
nicht durch Einschleusen ungültiger NULL-Werte in die Datenbank.

## Tests und Gegenproben

`sedo/tests/test_recurring_payment_days.py` enthält 21 Tests. Geprüft werden die
Vorrangmatrix, der echte POST-Monatslauf mit Datenbank- und XML-Fälligkeitsdatum,
positive Zahlungsziele, gemischte Vorlagen, Monats-/Jahreswechsel, unveränderte
Stammdaten, Formularvalidierung, Rechnung→Vorlage→Monatslauf, Überspringen einer
vorhandenen Rechnung und der bestehende vollständige Rückweg bei PDF-Fehlern.
Die reguläre XML-Erzeugung und der Archivnachweis bleiben aktiv. Keine PDF-/XML-
Erzeugung wird im normalen Erfolgsfall gemockt; keine externe Validierung/Zertifizierung
oder visuelle PDF-Prüfung wird daraus abgeleitet.

Fünf Mutationen in temporären Quellkopien müssen echte Assertions brechen:
Vorlagen-0 als fehlend, Kunden-0 als fehlend, alter Monatslauf ohne Helper,
alte Vorlagenanlage ohne Helper und hart codierter Standard statt der Konstante.
Der bestehende Runner prüft vorab einen erfolgreichen Basislauf und akzeptiert
keine Syntax-/Importfehler als fachliche Gegenprobe.

```bash
python manage.py test sedo.tests.test_recurring_payment_days --noinput
python manage.py test sedo --noinput
python tools/gegenproben_abo_zahlungsziel.py --output /tmp/sedo-abo-gegenproben
python manage.py makemigrations --check --dry-run
```

Für eigene Läufe vorher eine isolierte SEDO_HOME-Umgebung verwenden. Die reguläre
PR-CI ergänzt die neue Gegenprobengruppe zu den 63 bestehenden Gegenproben und
prüft weiterhin die SQLite-Parallelitätsfälle und den synthetischen Indexvergleich
unter Django 5.2 und 6.0. Konkrete Ergebnisse erst im PR-Abschlussvermerk.

## Umfang und Übernahme

Eine fachliche Quelldatei (`views.py`), neue Tests/Gegenproben, CI und Dokumentation.
Keine neue Modellmigration. Kein Ändern vorhandener Rechnungsfälligkeiten, keine
Neuerzeugung ausgestellter Dateien, kein Datenbank-Reparaturlauf, kein Mailversand.
Nummernvergabe, Archivschutz, Abo-Auswahl und bisherige Doppelprüfung bleiben gleich.
F-68, Feldtrennung, die bekannte leere-Abo-Auswahl-Problematik und andere Restfunde
werden nicht als gelöst ausgegeben.

Nach späterer Codeintegration reicht für diesen Fix die Übernahme des Quellcodes;
eine lokal noch ausstehende 0013 bleibt davon unabhängig. Kein Installer-Build und
keine Änderung der Versionsnummer in diesem Review-Commit. Vor Auslieferung das
Release-Paket mit dem dann vollständig geprüften Stand separat versionieren/bauen.
