# Deutsche Postleitzahlen und Orte

`de_postcodes.json` ist ein lokal mitgelieferter, auf PLZ und Ortsnamen
verdichteter Auszug aus `streets.updated.csv` des Projekts
[OpenPLZ API Data](https://github.com/openpotato/openplzapi.data).
Quellstand: Commit `c26389b30573ab2738d0b14fd350f143aa0a1251` vom
22.11.2025; SHA-256 der Quelldatei:
`1066a96a6b3a16629a94c834fca49fe58a2bb01558e17242c53a12d68b290088`.
Erzeugung: `python tools/build_de_postcodes.py streets.updated.csv`.

Die Daten stammen überwiegend aus OpenStreetMap und stehen unter der
[Open Database License 1.0 (ODbL)](https://opendatacommons.org/licenses/odbl/1-0/).
© OpenStreetMap-Mitwirkende und OpenPLZ API. Änderungen an diesem Datenauszug:
Entfernung von Straßen, Geodaten und Regionalschlüsseln; Deduplizierung und
Sortierung der PLZ-Ort-Paare. Dieser abgeleitete Datenauszug wird ebenfalls
unter ODbL 1.0 bereitgestellt. Die Lizenz betrifft den Datenauszug, nicht den
Anwendungscode.

Die Liste enthält vor allem Straßen-PLZ, keine vollständige amtliche Liste
aller Sonder- und Großempfänger-PLZ. Sie ist ein Eingabehinweis und ersetzt
keine postalische Zustellprüfung. Bei einer unbekannten fünfstelligen PLZ
kann der Ort nach eigener Prüfung weiterhin eingegeben werden.
