@echo off
REM Creates / refreshes Desktop shortcut with custom icon
cd /d "%~dp0"

set TARGET=%~dp0Launch Flatfinder.bat
set WORK=%~dp0
set ICON=%~dp0assets\flatfinder.ico
set DESKTOP=%USERPROFILE%\Desktop
set LNK=%DESKTOP%\Flatfinder.lnk

if not exist "%ICON%" (
  echo Icon missing: %ICON%
  echo Run: .venv\Scripts\python.exe scripts\make_icon.py
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%LNK%'); $s.TargetPath = '%TARGET%'; $s.WorkingDirectory = '%WORK%'; $s.WindowStyle = 1; $s.IconLocation = '%ICON%,0'; $s.Description = 'Flatfinder - hunt, settings, logs (all in one window)'; $s.Save(); Write-Host 'Created: %LNK%'; Write-Host 'Icon: %ICON%'"

echo.
echo Desktop shortcut: Flatfinder.lnk  (custom icon)
echo Double-click for the full menu.
pause
