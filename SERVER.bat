@echo off
rem Meeting Summarizer - web cabinet (FastAPI). Double-click to launch,
rem then open http://localhost:8000 in a browser.
cd /d "%~dp0"
echo Starting the web cabinet on http://localhost:8000  (Ctrl+C to stop)
backend\python\python.exe server\run_server.py
if errorlevel 1 (
  echo.
  echo [!] Failed to start. See the message above.
  pause
)
