# Windows-Updates im Testkanal

Der Windows-Client hat unter **Hilfe → Auf Windows-Updates prüfen** einen
benutzerinitiierten Update-Ablauf. Er liest ausschließlich die Releases des
[öffentlichen Binärkanals](https://github.com/midobberahn-png/sedo-windows-updates).
Dieser Kanal enthält keine Quellen, Kundendaten oder Zugangsdaten. Der
Quellcode liegt im separaten, derzeit öffentlichen Projekt-Repository.

## Bedienablauf

1. Offene Eingaben speichern und **Auf Windows-Updates prüfen** wählen.
2. SEDO zeigt eine neuere Testversion an und fragt vor der Installation nach.
3. Im lokalen Modus erstellt und prüft SEDO automatisch eine Sicherung des
   **gerade verwendeten** Datenordners. Der Teststarter nutzt weiterhin seinen
   getrennten Datenordner. Im Online-Modus liegen die Daten auf dem Server;
   dort gilt die getrennte Server-Sicherung.
4. SEDO lädt die Windows-Installationsdatei, prüft Größe, SHA-256 und das
   signierte Update-Manifest. Bei jeder Abweichung bleibt die alte App geöffnet.
5. Der bestehende Inno-Installer ersetzt das Programm im selben Verzeichnis.
   Ein Installationsfehler bleibt sichtbar; nach Erfolg startet SEDO erneut.
   `%APPDATA%\SEDO` und der getrennte Testordner
   werden nicht durch den Installer gelöscht.

Version **3.21.0.5** hat das Update-Menü erstmals veröffentlicht. Für eine
Erstinstallation wird ein Installer mit Update-Menü benötigt; ab dann sind
spätere freigegebene Testversionen direkt aus der App erreichbar. Version
**3.21.0.7** bietet das Menü auch im Online-Modus. Das ist ein bewusster
Testkanal; es gibt keine stillen Hintergrund- oder Zwangsupdates.

Die SEDO-Sicherung entfernt aus der Backup-Kopie einige Zugangsdaten. Die
Aktualisierung selbst erhält diese Daten, weil sie außerhalb des
Programmverzeichnisses liegen. Vor produktiven Freigaben muss der Rückweg für
Datenbankmigrationen auf einem echten Windows-Rechner geprobt werden.

## Veröffentlichungsvertrag

Eine neue Windows-Version ändert `WINDOWS_VERSION` in `sedo/windows_updates.py`.
Der Windows-Workflow übernimmt diesen Wert für den Installer. Ein manueller
Workflow-Lauf auf `main` erstellt nach vollständiger Windows-Prüfung und
Installationstest zwei Freigabedateien:

- `SEDO_Rechnungstool_Setup_<Version>.exe`
- `SEDO-update.json` mit Version, Kanal, Größe, SHA-256 und Ed25519-Signatur

Das Manifest wird nur signiert, wenn der isolierte Installationstest und die
erneute Installation bei unveränderten Testdaten erfolgreich waren. Der
private Signaturschlüssel gehört ausschließlich in das GitHub-Actions-Secret
`SEDO_UPDATE_SIGNING_KEY` des Quellprojekts. Sein öffentlicher
Gegenpart wird in der App fest eingebaut. Solange er fehlt, lehnt die App alle
Updates ab. Weder Schlüssel noch GitHub-Zugangstoken dürfen in den Client oder
das öffentliche Release-Repository aufgenommen werden.

Nach Prüfung der CI-Artefakte werden **nur** die beiden Freigabedateien als
Prerelease `v<Version>-windows` im öffentlichen Binärkanal veröffentlicht.
Ein Release ohne gültiges Manifest kann nicht installiert werden. Die
Veröffentlichung erfolgt derzeit bewusst manuell; eine separate Automatisierung
ist für das Ein-Klick-Update der Nutzer nicht erforderlich.

Der gegenwärtige Windows-Testinstaller hat keine Herausgebersignatur; die
geprüfte Signatur des Update-Manifests ist etwas anderes. Für den vorgesehenen
internen Einsatz ist eine kostenpflichtige Herausgebersignatur nicht geplant.
Vor der Freigabe müssen ein Update von der Vorversion auf dem Ziel-PC und der
Rückweg nach einer fehlgeschlagenen Datenbankmigration getestet werden.
