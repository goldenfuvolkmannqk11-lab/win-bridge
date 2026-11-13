@echo off
cd /d "%~dp0"
if "%~1"=="" (
  echo Usage:
  echo   send_job.bat list_dir
  echo   send_job.bat run_zwcad_script flange_zwcad_v3.py
  echo   send_job.bat run_python hello.py
  pause
  exit /b 2
)
python send_job.py %*
pause
