# Persönliches Dashboard

Über das Zahnrad neben „Rechnung erstellen“ und „Angebot erstellen“ wählt jeder
angemeldete Benutzer seine Kacheln. „Auswahl speichern“ merkt sich die Auswahl
auch nach der Abmeldung. „Standardauswahl“ stellt sie zurück. Alle Kacheln dürfen
ausgeblendet werden. Rechnungsentwürfe, Kontostand und Wetter sind zunächst aus;
neue E-Mails und die bisherigen Arbeitsbereiche sind eingeblendet.

Neue E-Mails zählt die ungelesenen Nachrichten in den aktiven, auswählbaren
Posteingängen. Die Zahl entspricht dem zuletzt synchronisierten Stand. Ein Klick
öffnet die ungelesenen Nachrichten im integrierten E-Mail-Programm.

## Kontostand

Die Kachel zeigt den zuletzt ausdrücklich über „Kontostand abrufen“ abgerufenen,
gebuchten Banksaldo mit Währung, Bankdatum, Abrufzeit und maskiertem Konto. Der
Dashboard-Aufruf selbst startet keinen Bankdialog. Ohne gespeicherte PIN wird
diese nur für den aktuellen Abruf abgefragt. Je nach Bank ist eine Freigabe in
der Banking-App nötig; unterstützt sind entkoppelte Push-Verfahren. Bei mehreren
Konten muss die gewünschte IBAN unter Einstellungen → Bankkonto stehen.

Der Abruf liest ausschließlich den Saldo. Er importiert keine Zahlungen und
erteilt keine Überweisungen. Ein Fehler erhält den alten, datierten Stand.
Änderungen an der Bankverbindung machen einen neuen Abruf erforderlich.

## Wetter.com

Die Anbindung nutzt die offizielle Wetter.com-/METEONOMIQS PWA Forecast API v4.0:
https://doc.meteonomiqs.com/doc/forecast_v4_0.html

Erforderlich ist ein Zugang mit Nowcast per Postleitzahl. Ohne Schlüssel zeigt
die Kachel den Einrichtungsstatus. Die Administration trägt den Schlüssel unter
Einstellungen → Wetter.com ein; alternativ hat `SEDO_WEATHER_API_KEY` Vorrang.
Es werden weder ein Konto angelegt noch kostenpflichtige Pakete automatisch gebucht.
Zugang und Pakete: https://www.wetter.com/apps_und_mehr/website/api/

Land, Postleitzahl und Ortsname stehen in der persönlichen Kachelauswahl
(Deutschland, Österreich, Schweiz, Liechtenstein). Der Ortsname ist die Anzeige;
die Wetterabfrage erfolgt anhand von Land und Postleitzahl. Vorgabe: Magdeburg,
Deutschland, 39104. Nur eine sichtbare Wetterkachel lädt Wetterdaten. Der Server
übermittelt den Schlüssel im Header, hält Ergebnisse zehn Minuten vor und zeigt
den Beobachtungszeitpunkt. Fehler werden kurz zwischengespeichert. Der Browser
erhält keinen Schlüssel; bereinigte Sicherungen entfernen ihn ebenfalls.

Die Anbieteranbindung wird in Tests simuliert. Ein Live-Test ist erst nach
Einrichtung eines gültigen API-Zugangs möglich.
