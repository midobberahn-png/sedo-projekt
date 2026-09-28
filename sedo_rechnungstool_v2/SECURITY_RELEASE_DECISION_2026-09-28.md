# Sicherheitsfreigabeprüfung vom 28.09.2026: NO-GO

Geprüfter Anwendungskandidat: `5113419475120d7bb895dda9eb37c830a8af8c99`, Code-Korrekturen `87bad550f90898ccb7effd2127d222900e30cc6b`, PR #33. Eine vollumfängliche Freigabe ist nicht erreicht. Der zusätzliche Commit mit diesem Dokument ergänzt nur eine datierte Sperrprüfung, deren Tests und CI-Verkabelung; er behebt die folgenden Anwendungslücken nicht.

## REL-01 – bekannte Sicherheitsmeldungen für das gesperrte pypdf

Der aktuelle `requirements-release.lock` enthält in Zeile 1066 `pypdf==6.18.0`. Der vollständige Lock wurde rekonstruiert und bytegenau gegen Git geprüft: Blob `192a5dea3f561789fd41c13aed2de755fca361b0`, SHA-256 `55df5f29eadf915a0020fbe707ec9370800e6e11b4b697543a9a29b5a58b0cfd`.

Sechs Maintainer-Meldungen treffen auf die Versionsnummer zu:

- GHSA-jw7q-gvrg-4vj3, GHSA-g9cg-prrw-2r8q, GHSA-fp3h-c4fm-7vvf: betroffen `<6.18.1`, veröffentlicht 11.09.2026.
- GHSA-php9-fj8v-98fj, GHSA-w23x-9jrw-r45c, GHSA-v247-6f48-mgcj: betroffen `<6.19.0`, veröffentlicht 16.09.2026.

Quellen sind die entsprechenden Meldungen unter `https://github.com/py-pdf/pypdf/security/advisories/`. Einstufung jeweils Moderate, auf den geprüften Seiten keine bekannte CVE-ID. Dies sind sechs Versionsbereichstreffer, nicht sechs demonstrierte SEDO-Exploits.

Der neue rein lesende Prüfer `tools/check_known_pypdf_advisories.py` gibt beim aktuellen Lock Exit 1 mit sechs Treffern aus. Seine zehn eigenen Tests bestanden lokal; die CI-Konfiguration ist kein behaupteter CI-Lauf. Der Prüfer deckt nur diese datierte Teilmenge ab und kann niemals selbst eine allgemeine Sicherheitsfreigabe erteilen.

Erforderlich: pypdf mindestens 6.19.0 bzw. einen erneut geprüften neueren Stand, konsistente Constraints/Lock/Prüfsummen und erneute vollständige Regression. Das offizielle 6.19.0-Wheel und Quellarchiv wurden über den von PyPI referenzierten Upstream-Workflow bezogen und ihre SHA-256-Werte gegen PyPI geprüft. Ein separater harmloser Lese-/Text-/Schreibtest mit 6.19.0 bestand. Das Paket wurde NICHT in SEDOs Lock aktiviert oder produktiv installiert.

## REL-02 – zwei PDF-Verarbeitungswege ohne den Prüf-Unterprozess

Aktuelle Quelle: `sedo/services/incoming_invoices.py`, Blob `aecb35c46583c733e70fc8883b104a86ed5d55d4`, SHA-256 `de4037a0e559bffa11eef6e97745720beac8181a7b70d2153ea6db87104df01b`.

`print_collection` (Zeilen 125–152) ruft den PDF-Parser in Zeile 136 und den Schreiber direkt auf. `suggest_fields` (Zeilen 203–235) ruft den Parser in Zeile 210 und Textauslese in Zeile 213 direkt auf. In beiden Fällen erfolgt die Parserarbeit im aufrufenden Prozess, nicht in dem zuvor eingeführten Ressourcen-Unterprozess.

Tatsächlich ausgeführte Gegenprobe: unveränderte Funktionskörper der hashgeprüften aktuellen Quelle, echter pypdf-6.18.0-Parser, harmlose vierseitige PDF mit 2937 Byte. In beiden Funktionen war die protokollierte Parser-Prozesskennung identisch mit der des Aufrufers. Seite vier erreichte den Vorschlagstext, während die geschützte Klassifikation nur die ersten drei Seiten betrachtet. Der KI-Transport wurde simuliert. Keine Datenbank, keine echte Verbindung, keine bösartige PDF oder Überlastung; kein vollständiger HTTP-/Django-Test.

Positivkontrolle: Die bestehenden hashgeprüften Klassifikations-Fixdateien starteten auf derselben PDF tatsächlich einen Kindprozess und erkannten die Rechnung. Der Schutz besteht also für die Klassifikation, nicht für alle späteren Parseroperationen.

`incoming_views.detail` erreicht den Vorschlagsweg als angemeldeter POST mit `action=suggest`. Sammeldruck erfordert freigegebene Belege. Kein Nachweis anonymer RCE oder einer bereits eingetretenen Betriebsstörung. Gerade Font-Widths/ToUnicode betreffen laut Maintainer Textauslese. Seiten- und Textlängenchecks nach der Parserarbeit ersetzen keinen unabhängigen Ressourcenabbruch.

Erforderlich: Textauslese und Sammeldruck ebenfalls begrenzen, sichere Abbrüche/Parallelität und native Paketierung prüfen. Ein reines Paketupdate beweist diese Ressourcenisolation nicht.

## Weitere offene Pflichtbedingungen

- SEC-03: `.secret_key` und `sedo_web.sqlite3` sind weiterhin im Git-Baum. Nur Metadaten gelesen, keine Inhalte. Aktive Betriebsdaten, unabhängige Sicherung, tatsächlicher Umzug und reale Schlüsselrotation nicht geprüft oder durchgeführt; Historie/Klone nicht bereinigt.
- Vollständige aktuelle Suite mit endgültigen Abhängigkeiten fehlt. Die vorherigen 987 Tests liefen auf einer älteren CI-Basis plus Korrekturen und werden nicht als aktuelle Freigabeevidenz wiederverwendet.
- Für den geprüften Commit lieferte die Actions-Abfrage null Läufe. Keine Ursache unterstellt, keine unbekannten lokalen Fremdtests beurteilt.
- Native macOS-/Windows-Builds, paketierter Prüfprozess und WebView-Abnahme fehlen.
- Vollständiger aktueller Abhängigkeits-/CVE-Scan und abschließende Rollen-/Objektabnahme fehlen. Der neue Teilprüfer ersetzt sie nicht.

## Entscheidung

Keine Zusammenführung oder Produktivfreigabe aufgrund dieses Prüflaufs. Keine echten Bank-/Mailzugriffe, keine Änderung von Betriebsdaten. PR #33 bleibt Entwurf. Das gewünschte Freigabeergebnis ist noch nicht erreicht; dieses Dokument hält die nachgewiesenen zusätzlichen Sperrgründe fest, statt aus unvollständigen Nachweisen eine Freigabe abzuleiten.

Prüfumgebung der engeren Gegenproben: Linux/Python 3.13.5, nicht der vollständige Release-Lock mit Python 3.13.15. Der aus einem alten Mac-Artefakt entnommene reine Python-Code von pypdf wurde unter Linux ausgeführt; daraus folgt keine native Mac-Abnahme.

Upstream-Quellen: https://github.com/py-pdf/pypdf/releases/tag/6.18.1 , https://github.com/py-pdf/pypdf/releases/tag/6.19.0 , https://pypi.org/project/pypdf/6.19.0/ . Je Meldung gilt die primäre Advisory-Seite mit ihrer GHSA-ID.
