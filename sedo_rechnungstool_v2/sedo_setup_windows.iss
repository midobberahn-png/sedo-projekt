; ─────────────────────────────────────────────────────────────────
; SEDO Rechnungstool – Windows-Installer (Inno Setup, seit v2.3.1)
;
; Erzeugt ein Setup.exe, das nach C:\Programme installiert, Startmenü-
; und Desktop-Verknüpfung anlegt und einen Deinstallierer mitbringt.
; UPDATES: einfach das neue Setup.exe ausführen – es installiert über
; die alte Version; die Daten bleiben unberührt (%APPDATA%\SEDO).
;
; Bauen: erst BUILD_WINDOWS.bat (PyInstaller), dann kompiliert das
; Skript automatisch, wenn Inno Setup 6 installiert ist
; (kostenlos: https://jrsoftware.org/isinfo.php).
; ─────────────────────────────────────────────────────────────────
#define AppName "SEDO Rechnungstool"
#define AppVersion "2.3.1"
#define AppPublisher "SEDO Objektdienstleistungen GmbH"

[Setup]
AppId={{8E1F6C1A-SEDO-4C1B-9E2A-RECHNUNG2026}}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=SEDO_Rechnungstool_Setup_{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Update-freundlich: schließt eine laufende SEDO-Instanz vor dem Update
CloseApplications=yes
RestartApplications=no
PrivilegesRequired=admin
UninstallDisplayIcon={app}\SEDO Rechnungstool.exe

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"

[Files]
; Der komplette PyInstaller-Ausgabeordner
Source: "dist\SEDO Rechnungstool\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\SEDO Rechnungstool.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\SEDO Rechnungstool.exe"

[Run]
Filename: "{app}\SEDO Rechnungstool.exe"; Description: "SEDO jetzt starten"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Programmreste ja – die DATEN in %APPDATA%\SEDO bleiben bewusst erhalten!
Type: filesandordirs; Name: "{app}"
