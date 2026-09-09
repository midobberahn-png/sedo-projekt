' SEDO Rechnungstool - Desktop-Starter (v3.21)
' Startet den lokalen Server unsichtbar und oeffnet ein eigenes App-Fenster.
' Beim allerersten Start laeuft die Einrichtung SICHTBAR (zeigt das einmalige
' Admin-Passwort!) ueber START_WINDOWS.bat.
Option Explicit
Dim sh, fso, dir, url, i
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
url = "http://127.0.0.1:8000"

Function ServerUp()
  On Error Resume Next
  Dim http
  Set http = CreateObject("MSXML2.XMLHTTP")
  http.open "GET", url & "/login/", False
  http.send
  ServerUp = (Err.Number = 0)
  If ServerUp Then ServerUp = (http.status = 200)
  On Error GoTo 0
End Function

Sub OpenWindow()
  Dim candidates, c
  candidates = Array( _
    sh.ExpandEnvironmentStrings("%ProgramFiles(x86)%") & "\Microsoft\Edge\Application\msedge.exe", _
    sh.ExpandEnvironmentStrings("%ProgramFiles%") & "\Microsoft\Edge\Application\msedge.exe", _
    sh.ExpandEnvironmentStrings("%ProgramFiles%") & "\Google\Chrome\Application\chrome.exe", _
    sh.ExpandEnvironmentStrings("%ProgramFiles(x86)%") & "\Google\Chrome\Application\chrome.exe", _
    sh.ExpandEnvironmentStrings("%LocalAppData%") & "\Google\Chrome\Application\chrome.exe")
  For Each c In candidates
    If fso.FileExists(c) Then
      sh.Run """" & c & """ --app=" & url, 1, False
      Exit Sub
    End If
  Next
  sh.Run url, 1, False
End Sub

' Server laeuft schon? Dann nur das Fenster oeffnen.
If ServerUp() Then
  OpenWindow
  WScript.Quit
End If

If (Not fso.FolderExists(dir & "\.venv")) Or (Not fso.FileExists(dir & "\data\sedo_web.sqlite3")) Then
  ' Erste Einrichtung: sichtbar, damit das einmalige Admin-Passwort lesbar ist.
  MsgBox "Erste Einrichtung von SEDO." & vbCrLf & vbCrLf & _
         "Es oeffnet sich gleich ein schwarzes Fenster - dort erscheint am Ende" & vbCrLf & _
         "das EINMALIG angezeigte Admin-Passwort. Bitte notieren!" & vbCrLf & vbCrLf & _
         "Das SEDO-Fenster oeffnet sich danach automatisch.", vbInformation, "SEDO Rechnungstool"
  sh.Run """" & dir & "\START_WINDOWS.bat""", 1, False
Else
  sh.Run "cmd /c cd /d """ & dir & """ && .venv\Scripts\python.exe manage.py runserver 0.0.0.0:8000", 0, False
End If

' Warten bis der Server antwortet (Erststart kann durch Paket-Installation dauern)
For i = 1 To 300
  If ServerUp() Then Exit For
  WScript.Sleep 1000
Next

If ServerUp() Then
  OpenWindow
Else
  MsgBox "Der SEDO-Server ist nicht gestartet." & vbCrLf & _
         "Bitte START_WINDOWS.bat per Doppelklick ausfuehren - dort stehen die Details.", _
         vbExclamation, "SEDO Rechnungstool"
End If
