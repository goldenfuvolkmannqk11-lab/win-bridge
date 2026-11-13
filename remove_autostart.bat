@echo off
cd /d "%~dp0"
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\muse-bridge.lnk" >nul 2>&1
echo Done. muse-bridge will NOT start on next login.
echo Stop the running relay and agent with stop_bridge.bat.
pause
