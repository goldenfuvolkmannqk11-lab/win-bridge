@echo off
cd /d "%~dp0"
echo muse-bridge Windows agent install v0.1
echo.
python --version >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python not found. Install Python 3.10+ and tick "Add Python to PATH", then run this again.
  pause
  exit /b 1
)
python --version
echo.
echo [1/3] Installing pywin32 Pillow ...
pip install pywin32 Pillow
if errorlevel 1 (
  echo [ERROR] pip install failed. Check network and retry.
  pause
  exit /b 1
)
echo.
echo [2/3] Preparing work folder ...
if not exist "C:\muse-bridge\work" mkdir "C:\muse-bridge\work"
if not exist "bridge_config.json" (
  echo [ERROR] bridge_config.json not found. Run this script in the same folder as bridge_config.json and windows_agent.py.
  pause
  exit /b 1
)
echo.
echo [3/3] Next steps (manual):
echo   1. Open bridge_config.json in Notepad.
echo      - relay_url: for local test use http://127.0.0.1:8765
echo      - token: change from CHANGE_ME to your own long password.
echo   2. Start relay: set BRIDGE_TOKEN=your-token, then python relay_server.py
echo   3. Start agent: python windows_agent.py
echo   4. Or double-click start_bridge.bat to start both.
echo.
echo To stop agent: create STOP.txt in this folder, or press Ctrl+C.
echo Audit log: audit.log
pause
