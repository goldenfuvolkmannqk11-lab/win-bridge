@echo off
cd /d "%~dp0"
echo muse-bridge v0.2 start
echo.
if exist STOP.txt (
  echo Found STOP.txt, deleting it or agent will stop at once.
  del STOP.txt
)
for /f "delims=" %%T in ('python get_token.py') do set "BRIDGE_TOKEN=%%T"
if "%BRIDGE_TOKEN%"=="" (
  echo [ERROR] Cannot read token. Check bridge_config.json exists.
  pause
  exit /b 1
)
if "%BRIDGE_TOKEN%"=="CHANGE_ME" (
  echo [ERROR] token is still the default CHANGE_ME.
  echo Open bridge_config.json in Notepad and change token first.
  pause
  exit /b 1
)
echo [1/2] Starting relay relay_server.py ...
start "muse-bridge relay" /min cmd /k python relay_server.py
echo [2/2] Starting agent windows_agent.py ...
start "muse-bridge agent" /min cmd /k python windows_agent.py
timeout /t 3 /nobreak >nul
echo.
echo Checking relay health ...
python -c "import json,urllib.request; c=json.load(open('bridge_config.json',encoding='utf-8')); print(urllib.request.urlopen(c['relay_url'].rstrip('/')+'/health',timeout=5).read().decode('utf-8'))"
if errorlevel 1 (
  echo [HINT] Relay did not respond. Open the window titled "muse-bridge relay" and read the error.
) else (
  echo Relay is up. Agent should poll every 2 seconds with no 401.
)
echo.
echo Do not close the two windows. To stop, double-click stop_bridge.bat.
pause
