; Eingaben aus tools/windows_installer.py nach grüner Windows-Prüfung.
#ifndef BundleDir
  #error BundleDir fehlt. tools/windows_installer.py verwenden.
#endif
#ifndef InstallerOutputDir
  #error InstallerOutputDir fehlt.
#endif
#ifndef WebViewInstaller
  #error WebViewInstaller fehlt.
#endif
#ifndef AppVersion
  #error AppVersion fehlt.
#endif
#ifndef VCRuntimeURL
  #error Geprüfte C++-Laufzeit fehlt.
#endif
#define AppName "SEDO Rechnungstool"

[Setup]
AppId={{8E1F6C1A-SEDO-4C1B-9E2A-RECHNUNG2026}}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=SEDO
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir={#InstallerOutputDir}
OutputBaseFilename=SEDO_Rechnungstool_Setup_{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
UninstallDisplayIcon={app}\SEDO Rechnungstool.exe

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#WebViewInstaller}"; DestName: "MicrosoftEdgeWebview2Setup.exe"; Flags: dontcopy

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\SEDO Rechnungstool.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\SEDO Rechnungstool.exe"

[Run]
; Nur der aus SEDO gestartete Update-Lauf öffnet das Programm nach erfolgreichem Setup.
Filename: "{app}\SEDO Rechnungstool.exe"; Flags: nowait; Check: IsSedoUpdate

[Code]
function IsSedoUpdate: Boolean;
var I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), '/SEDOUPDATE=1') = 0 then begin
      Result := True;
      Exit;
    end;
end;

function HasVCRuntime: Boolean;
var Version: String; Installed: Cardinal; Current, Minimum: Int64;
begin
  Result := False;
  if not RegQueryDWordValue(HKLM64, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Installed', Installed) then Exit;
  if Installed <> 1 then Exit;
  if not RegQueryStringValue(HKLM64, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Version', Version) then Exit;
  if Copy(Version, 1, 1) = 'v' then Delete(Version, 1, 1);
  if StrToVersion(Version, Current) and StrToVersion('{#VCRuntimeVersion}', Minimum) then
    Result := ComparePackedVersion(Current, Minimum) >= 0;
end;

function HasWebView2: Boolean;
var Version: String;
begin
  Result := (RegQueryStringValue(HKLM32, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'));
  if not Result then
    Result := (RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var ExitCode: Integer; Started: Boolean;
begin
  Result := '';
  if not HasVCRuntime then begin
    try
      DownloadTemporaryFile('{#VCRuntimeURL}', 'VC_redist.x64.exe', '{#VCRuntimeHash}', nil);
    except
      Result := 'Microsoft Visual C++ konnte nicht geladen werden. Bitte Internetverbindung prüfen und die Installation wiederholen.';
      Exit;
    end;
    if IsAdminLoggedOn then
      Started := Exec(ExpandConstant('{tmp}\VC_redist.x64.exe'), '/install /quiet /norestart', '', SW_HIDE, ewWaitUntilTerminated, ExitCode)
    else
      Started := ShellExec('runas', ExpandConstant('{tmp}\VC_redist.x64.exe'), '/install /quiet /norestart', '', SW_HIDE, ewWaitUntilTerminated, ExitCode);
    if not Started then begin
      Result := 'Microsoft Visual C++ konnte nicht eingerichtet werden. Bitte die Windows-Administratorfreigabe bestätigen.';
      Exit;
    end;
    if ExitCode = 3010 then begin
      NeedsRestart := True;
      Result := 'Microsoft Visual C++ wurde eingerichtet. Bitte Windows neu starten und das SEDO-Setup erneut ausführen.';
      Exit;
    end;
    if (ExitCode <> 0) or not HasVCRuntime then begin
      Result := 'Microsoft Visual C++ konnte nicht eingerichtet werden. Fehlercode: ' + IntToStr(ExitCode);
      Exit;
    end;
  end;
  if not HasWebView2 then begin
    ExtractTemporaryFile('MicrosoftEdgeWebview2Setup.exe');
    if not Exec(ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe'), '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, ExitCode) then
      Result := 'Microsoft WebView2 konnte nicht gestartet werden. Bitte Windows neu starten und die Installation wiederholen.'
    else if not HasWebView2 then
      Result := 'Microsoft WebView2 konnte nicht eingerichtet werden. Bitte Internetverbindung prüfen und die Installation wiederholen. Fehlercode: ' + IntToStr(ExitCode);
  end;
end;

// Keine pauschale Löschung von {app} oder %APPDATA%\SEDO beim Deinstallieren.
