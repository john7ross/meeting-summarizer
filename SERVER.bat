@echo off
rem Meeting Summarizer - web cabinet (FastAPI). Double-click to launch,
rem then open http://localhost:8000 in a browser.
rem
rem Same interpreter search as RUN.bat: embedded runtime (full build), then the
rem one INSTALL.bat recorded (min install or a clone), then PATH.
cd /d "%~dp0"

set "PY=backend\python\python.exe"
if exist "%PY%" goto run
set "PY="
if exist "config\interpreter.txt" set /p PY=<config\interpreter.txt
if not defined PY set "PY=python"

:run
echo Web cabinet on http://localhost:8000  (Ctrl+C to stop)
"%PY%" server\run_server.py
if errorlevel 1 (
  echo.
  echo [!] Failed to start. See the message above.
)
pause
