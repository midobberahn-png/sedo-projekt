' Source-checkout shortcut; keep setup/password output visible, never log it.
Option Explicit
Dim sh, fso, appDir
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
appDir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.Run """" & appDir & "\START_WINDOWS.bat""", 1, False
