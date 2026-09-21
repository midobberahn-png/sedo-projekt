# SEDO AI: Bonsai lokal

Der bisherige Claude-Zugang ist durch den lokalen Bonsai-Server ersetzt.
Angebotsentwürfe, Leistungstexte, Rechnungsbegleitschreiben und die optionale
KI-Plausibilitätsprüfung verwenden denselben lokalen Anschluss. Es gibt keinen
Cloud-Fallback. Alte Claude-Schlüssel und Modellnamen werden nicht mehr gelesen;
vorhandene alte Schlüssel werden weiterhin aus Sicherungen entfernt.

## Einrichtung

1. Bonsai auf **demselben Rechner wie SEDO** starten. Auf dem geprüften Mac liegt
   die Installation unter `Documents/Bonsai-demo`; der vorhandene Einstieg ist
   `./scripts/start_llama_server.sh`. SEDO startet oder installiert das Modell
   nicht selbst.
2. In SEDO unter **Einstellungen → KI-Assistent (Bonsai · lokal)** die lokale
   Adresse `http://127.0.0.1:8080/v1` verwenden. Die Modellkennung kann leer bleiben:
   dann wird das einzelne geladene Modell erkannt, einschließlich installations-
   abhängiger Dateipfade. Bei mehreren Modellen die genaue Kennung angeben.
3. **Gespeicherte Verbindung prüfen** zeigt das geladene Modell. Dabei werden nur
   Modellinformationen abgefragt, keine Geschäftsdaten übertragen. Das Kennzeichen
   „Lokal aktiviert“ bezeichnet die Einstellung, nicht die Erreichbarkeit.
4. Einen kurzen Angebotsentwurf erzeugen und wie bisher prüfen. Genannte Preise,
   gekennzeichnete Schätzungen, Annahmen und vorhandene Katalogtexte behalten ihre
   bisherige Bedeutung. Neue Leistungen werden wie bisher als KI-Katalogeinträge
   markiert. Rechnungserstellung, Berechnung, Finalisierung und Versand folgen
   weiterhin ihren eigenen Prüf- und Freigabeschritten.

Nur die Administration kann den gemeinsamen Modellanschluss ändern oder prüfen.
Optional überschreiben `SEDO_AI_LOCAL_BASE_URL` und `SEDO_AI_LOCAL_MODEL` die
Einstellungen; die Oberfläche zeigt diese Vorgabe an. Es sind ausschließlich
`localhost`, `127.0.0.1` und `[::1]` zulässig. `localhost` wird fest auf IPv4-Loopback
abgebildet. Weiterleitungen werden abgewiesen; System-Proxyvariablen werden für
KI-Anfragen ignoriert. Auf Windows ist eine eigene lokale Bonsai-Installation
nötig. Der Mac-Server ist über diese lokale Adresse auf Windows nicht erreichbar.

## Diktat und Datenschutz

Audio wird über MediaRecorder an SEDO auf diesem Rechner geschickt und mit der
vorhandenen lokalen Whisper-Anbindung transkribiert. Fehlt sie, bleibt Texteingabe
verfügbar; die frühere automatische Browser-Spracherkennung entfällt. Das erste
Whisper-Diktat kann das Sprachmodell herunterladen; dabei wird keine Aufnahme
an einen Transkriptionsanbieter gesendet. Die Mikrofonfreigabe und tatsächliche
Diktatqualität sind auf jedem Einsatzrechner separat zu prüfen.

Bonsai erhält die Leistungsangaben und gegebenenfalls Katalogtexte bzw.
Rechnungseckdaten. Freitext wird nicht automatisch anonymisiert. Die SEDO-Anbindung
ruft keine Modellwerkzeuge oder externen Dienste auf. Das Modell läuft separat;
seine Betriebsprotokolle und zusätzlich eingerichtete Erweiterungen gehören
nicht zur SEDO-Konfiguration. Mail-, Bank- und Wetterverbindungen der Anwendung
bleiben eigenständige Dienste.

## Fehler und Grenzen

- Modell nicht erreichbar: Server starten oder Adresse prüfen; kein automatischer
  Ersatz durch einen externen Anbieter. Manuelle Bearbeitung bleibt möglich.
- Falsches/mehrdeutiges Modell: Kennung prüfen oder genau ein Modell laden.
- Unvollständiges bzw. falsch strukturiertes Ergebnis: keine Teilpositionen oder
  Katalogänderungen übernehmen; Text verkürzen und erneut versuchen.
- Die Anfragen verwenden schema-gebundenes JSON ohne erweiterten Denkmodus.
  Antworten werden zusätzlich in SEDO auf Pflichtfelder, Typen und erlaubte Werte
  geprüft. Das ersetzt nicht die inhaltliche Prüfung durch den Nutzer.
- Die lokale Laufzeit ist zusätzlich zur SEDO-App erforderlich. Keine externen
  Modell-API-Gebühren; Hardware- und Stromkosten bleiben bestehen.

Bonsai 2 in PQ2_0 benötigt derzeit die PrismML-Version von llama.cpp. Die konkrete
Kombination wurde am 21.09.2026 am lokalen Server geprüft; nicht ungeprüft durch
Stock-llama.cpp oder Ollama ersetzen.

Herstellerquellen:
- https://github.com/PrismML-Eng/Bonsai-demo
- https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf
- https://raw.githubusercontent.com/PrismML-Eng/llama.cpp/master/tools/server/README.md
