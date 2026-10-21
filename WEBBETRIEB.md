# SEDO Online: interner Firmenbetrieb

Die erste Webversion verwendet dieselbe Django-Oberfläche wie das Desktop-Programm. Ein zentraler Server hält **eine** SQLite-Datenbank und den zugehörigen Medienordner. Mehrere Mitarbeiter greifen per Browser oder mit dem Online-Modus der aktualisierten Desktop-App auf genau diesen Server zu. Die bisherigen Desktop-Installationen verwenden bis zur ausdrücklichen Umstellung weiterhin ihre **eigenen lokalen Daten**. Es gibt keine automatische Synchronisierung oder Zusammenführung mehrerer Bestände. Da aktuell keine Echtdaten übernommen werden müssen, ist für SEDO Online ein **frischer zentraler Datenbestand** vorgesehen.

Diese Anleitung setzt einen dauerhaft laufenden Python-Prozess, dauerhaften lokalen Speicher und einen HTTPS-Reverse-Proxy auf demselben **Linux-Server** voraus (VPS oder gleichwertiges Managed Hosting). Das zentrale Webprofil ist auf POSIX-Dateirechte angewiesen; Windows ist als Browser- oder Desktop-Client vorgesehen. Ein reines Dateihosting-Paket genügt dafür nicht. Hetzner ist als Anbieter genannt; das konkrete Hosting-Produkt und die Domain sind noch offen. Die Beispiel-Domain unten ist ein Platzhalter.

## Datenfluss und Grenzen

Browser/Desktop-Fenster → HTTPS-Reverse-Proxy → `127.0.0.1:8766` (`web.py`, Waitress) → Django → `SEDO_HOME/data/sedo_web.sqlite3` und `SEDO_HOME/media/`. Die breite `/media/`-Dateiroute ist im Webprofil abgeschaltet; Rechnungen und andere Geschäftsdokumente laufen über die engen, angemeldeten Vorschau- und Downloadansichten. E-Mail- und Kalenderabgleich laufen im einzelnen Webprozess. Bonsai und das ressourcenintensive Diktat sind im Webprofil standardmäßig deaktiviert; bei späterer Freigabe benötigen sie einen separat geprüften lokalen Dienst beziehungsweise ein Modell **auf dem Server**. Ein Modell auf dem PC eines Mitarbeiters wird vom Webserver nicht benutzt.

Der Server muss alleiniger Eigentümer des Datenordners sein. `web.py` hält eine Prozesssperre, sodass nicht versehentlich zwei SEDO-Prozesse denselben SQLite-Bestand öffnen. SQLite bleibt für den zunächst kleinen internen Nutzerkreis bewusst erhalten: Backup, Wiederherstellung und mehrere Schutzmechanismen sind derzeit SQLite-spezifisch. Bei nachgewiesenen Schreibkonflikten oder deutlich höherer gleichzeitiger Last ist eine gesonderte Datenbankmigration erforderlich. Datenbankdateien gehören auf ein lokales dauerhaftes Dateisystem, nicht auf einen Netzlaufwerks-/NFS-Ordner oder in einen flüchtigen Container.

## Vorbereitung auf dem Server

1. Python **3.13.15**, Quellstand und eine isolierte virtuelle Umgebung bereitstellen. Die Pakete aus `requirements_web.txt` mit den Projekt-Constraints installieren. Das Webprofil installiert das auf dem Server standardmäßig deaktivierte Whisper-Diktat nicht. Vor echtem Betrieb muss das Paketprofil für die Zielplattform reproduzierbar festgeschrieben und geprüft sein.
2. Einen eigenen, nur für den SEDO-Dienst zugänglichen Datenordner außerhalb des Quellcodes anlegen, z. B. `/var/lib/sedo`, mit Dateimodus **0700** und ohne symbolische Verknüpfung. Sein Benutzer benötigt Schreibrechte auf `data`, `media`, `logs`, `backups` und `staticfiles`; andere lokale Konten und der Webserver-Prozess benötigen keinen direkten Zugriff auf die Geschäftsdaten.
3. Die folgenden Werte ausschließlich als geschützte Umgebungsvariablen oder in einer privaten, nicht versionierten Datei setzen. Die Datei darf nicht über den Webserver erreichbar sein. Einen langen zufälligen `DJANGO_SECRET_KEY` einmalig erzeugen, sicher sichern und bei Updates beibehalten:

   ```text
   SEDO_HOME=/var/lib/sedo
   SEDO_WEB_ORIGIN=https://rechnung.example.de
   DJANGO_SECRET_KEY=<eigener langer Zufallswert>
   DJANGO_DEBUG=0
   ```

   `SEDO_WEB_ORIGIN` bestimmt den einzigen erlaubten Host und den CSRF-Ursprung. Im Webprofil sind HTTPS-Weiterleitung, sichere Sitzungs-Cookies, eine anfängliche HSTS-Dauer von einer Stunde und eine achtstündige Sitzung fest eingestellt. Der Secret-Key darf **nicht** im Repository, in einem Ticket oder im Chat landen.
4. Mit gestopptem Webdienst und als SEDO-Dienstbenutzer einmalig `python web.py prepare` ausführen. Das bereitet Schema und statische Dateien vor. Beim ersten Lauf zeigt das Terminal einmalig das Admin-Passwort. Es gehört in einen Passwortmanager und nicht in Dienstprotokolle. Später die Passwortänderung über die Anwendung durchführen.
5. `python web.py serve --port 8766` als überwachten Dienst mit automatischem Neustart betreiben. Dieser Start bindet ausschließlich an `127.0.0.1`, übernimmt vorgemerkte geprüfte Wiederherstellungen unter exklusiver Sperre und wendet ausstehende Migrationen sowie aktualisierte statische Dateien an. Vor jedem Code-Update: geprüfte Sicherung erstellen und den Dienst kontrolliert anhalten.

Der Reverse-Proxy muss Port 80 auf HTTPS umleiten, bei unbekannten Hosts ablehnen, für `rechnung.example.de` ein gültiges Zertifikat vorweisen und **nur HTTPS-Anfragen** an `127.0.0.1:8766` weitergeben. Er muss den ursprünglichen `Host` erhalten und `X-Forwarded-For` mit der tatsächlichen Client-IP **überschreiben** (nicht an einen vom Client gelieferten Wert anhängen). Auch `X-Forwarded-Proto` muss er selbst mit `https` setzen und einen gleichnamigen Client-Header verwerfen. Waitress vertraut diesen beiden Headern nur vom lokalen Proxy; fehlt das sichere Schema, leitet Django auf HTTPS um. Der Waitress-Port darf weder im Internet noch im Firmen-LAN direkt erreichbar sein. `/media/` niemals als statisches Verzeichnis im Proxy konfigurieren.

## Gemeinsamen Bestand beginnen oder übernehmen

**Aktueller Fall: keine Echtdaten.** Nach `prepare` den frischen Online-Bestand als künftige einzige Quelle festlegen. Firmenstammdaten und Mitarbeiterkonten online einrichten und alle Rechnungen ab dem vereinbarten Stichtag ausschließlich dort erstellen. Lokale Desktop-Testdaten bleiben getrennt und gehören nicht in den produktiven Bestand.

**Nur falls später echte lokale Belege zu übernehmen sind:**

Zuerst festlegen, **welcher lokale SEDO-Bestand maßgeblich** ist. Wenn auf Mac und Windows bereits unterschiedliche echte Rechnungen existieren, ist ein fachlicher Abgleich nötig; zwei SQLite-Dateien lassen sich nicht gefahrlos zusammenkopieren. Die gewählte Desktop-Installation nach Sicherung nicht weiter zum Erstellen echter Belege verwenden.

Auf der maßgeblichen Desktop-Installation über **Backup** eine geprüfte Anwendungssicherung erstellen. Das ZIP nur über einen geschützten Übertragungsweg in den Backup-Ordner des Servers kopieren. In SEDO Online mit einem Administrationskonto die Sicherung prüfen und die Wiederherstellung vormerken; anschließend den Webdienst kontrolliert neu starten. `web.py serve` spielt sie vor dem Datenbankstart ein und führt nötige Migrationen aus. Danach Belegzahlen, letzte Rechnungen, PDFs/XMLs, Firmenstammdaten und Nummernkreise auf dem Server gegen die Quelle vergleichen. Zugangsdaten für Mail, Kalender, SMTP und Bank sind absichtlich nicht vollständig im Backup und müssen auf dem Server neu eingerichtet und getestet werden.

Anschließend Mitarbeiterkonten mit eigenen Passwörtern anlegen. Die Weboberfläche ist im Browser auf Mac und Windows sofort nutzbar, sobald der Server freigegeben ist. Der Online-Modus einer **neu gebauten** Desktop-Version wird einmalig mit `SEDO --connect https://rechnung.example.de` eingerichtet; danach öffnet das normale Programmsymbol den gemeinsamen Server, ohne lokale Datenbank, lokale Migrationen oder lokale Hintergrunddienste zu starten. Die bereits veröffentlichten Windows-Installer enthalten diesen Modus noch nicht und benötigen dafür ein neues Paket. `SEDO --local` schaltet bewusst zum bisherigen lokalen Bestand zurück; dort erstellte Belege erscheinen nicht online.

## Abnahme vor echten Rechnungen

- `DJANGO_SETTINGS_MODULE=config.settings_web python manage.py check --deploy` im Zielsystem ausführen und Warnungen einzeln bewerten. Die anfängliche kurze HSTS-Dauer ist beabsichtigt; erst nach erfolgreicher TLS-Abnahme verlängern.
- Von zwei Geräten Anmeldung, Abmeldung, CSRF-Schutz, Rechnungsentwurf, PDF/XML-Erzeugung und Abruf geschützter Dokumente mit **synthetischen Daten** prüfen. Die breite `/media/`-Route muss auch nach Anmeldung HTTP 404 liefern.
- Direkte Zugriffe auf den Waitress-Port von außen müssen scheitern; HTTP muss auf HTTPS wechseln. Zertifikat, Host-Filter, Proxy-Header, Anmeldebegrenzung und der tatsächliche Client-IP-Eintrag sind am echten Server zu prüfen.
- Gleichzeitig erzeugte Belege müssen eindeutige Nummern erhalten; eventuelle `database is locked`-Fehler sind ein Stoppkriterium für die Freigabe. Die vorhandenen Code-Tests ersetzen diesen Mehrgeräte-Test nicht.
- Eine vollständige Sicherung und Rücksicherung mit Testdaten durchführen. Wöchentliche ZIPs auf demselben Server sind kein ausreichender Ausfallschutz; zusätzlich verschlüsselte, getrennt aufbewahrte Sicherungen und einen Wiederherstellungsplan vorsehen.
- E-Mail, iCloud-Kalender, Druck, Dateiupload und bestehende Windows/macOS-Funktionen auf den vorgesehenen Geräten fachlich abnehmen. Fachlich freigegebene Beispielrechnungen für Standardfall, Storno und Steuer-Sonderfälle fehlen derzeit noch.

Bis diese Punkte auf der tatsächlichen Infrastruktur bestanden sind, ist die Webversion ein Entwicklungsstand und keine Freigabe für echte Rechnungen.
