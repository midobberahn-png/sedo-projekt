# Sicherheitskorrekturen zum Audit vom 28.09.2026

Arbeitszweig für die vom Auftraggeber freigegebene Behebung SEC-01 bis SEC-06.
Ausgangsstand: eee82e8714d60f5f010a927c00a643134ed37ecd.

## Abnahmekriterien

- Administrative Berechtigungen für SMTP/FinTS und Backup/Restore werden serverseitig erzwungen.
- Gespeicherte Zugangsdaten werden niemals nach einem Identitätswechsel an einen anderen Dienst weitergereicht.
- Betriebsdaten und Schlüssel werden nicht in neue Quell-/Releasepakete aufgenommen; notwendige Migration und Rotation sind kontrolliert und nicht datenvernichtend.
- Passwortvalidierung und persistente, nicht ausschließlich IP-basierte Login-Drosselung gelten auch für die Admin-Anmeldung.
- Dokumentklassifikation ist begrenzt und gegen überlange Ziffernfolgen abgesichert.
- Regressionen und Angriffsnachweise werden auf einem konsistenten aktuellen Quellstand erneut ausgeführt.

## Status

In Bearbeitung. Dieser Zwischenstand behauptet keine bestandenen Tests oder abgeschlossene Sicherheitsfreigabe. Keine echten Bank-, E-Mail- oder Betriebszugänge dürfen für Gegenproben verwendet werden.
