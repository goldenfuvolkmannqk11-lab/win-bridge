@echo off
cd /d "%~dp0"
for /f "delims=" %%T in ('python get_token.py') do set "BRIDGE_TOKEN=%%T"
if "%BRIDGE_TOKEN%"=="CHANGE_ME" (
  echo [ERROR] token is still the default CHANGE_ME.
  echo Open bridge_config.json in Notepad and change token first.
  pause
  exit /b 1
)
if "%BRIDGE_TOKEN%"=="" (
  echo [ERROR] Cannot read token. Check bridge_config.json exists.
  pause
  exit /b 1
)
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\muse-bridge.lnk'); $s.TargetPath='%~dp0start_bridge.bat'; $s.WorkingDirectory='%~dp0'; $s.Save()"
if errorlevel 1 (
  echo [ERROR] Could not create the Startup shortcut.
  pause
  exit /b 1
)
echo Done. muse-bridge will start on next login.
echo To remove it, double-click remove_autostart.bat.
pause
