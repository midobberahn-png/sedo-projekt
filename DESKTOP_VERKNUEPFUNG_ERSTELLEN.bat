@echo off
cd /d %~dp0
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $lnk = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\SEDO Rechnungstool.lnk'); $lnk.TargetPath = 'wscript.exe'; $lnk.Arguments = '\"%~dp0SEDO.vbs\"'; $lnk.WorkingDirectory = '%~dp0'; $lnk.IconLocation = '%~dp0sedo\assets\sedo.ico'; $lnk.Description = 'SEDO Rechnungstool starten'; $lnk.Save()"
echo Desktop-Verknuepfung "SEDO Rechnungstool" wurde erstellt.
pause
