@echo off
cd /d "%~dp0"
echo muse-bridge stop
echo STOP > STOP.txt
echo Created STOP.txt, agent will stop on next poll.
taskkill /FI "WINDOWTITLE eq muse-bridge relay*" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq muse-bridge agent*" /F >nul 2>&1
echo Relay and agent windows closed if they were running.
echo Done.
pause
