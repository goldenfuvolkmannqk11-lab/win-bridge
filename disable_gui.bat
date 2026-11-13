@echo off
cd /d "%~dp0"
del gui_enabled.txt >nul 2>&1
echo GUI control DISABLED. Screenshot/click/type jobs will now be refused.
pause
