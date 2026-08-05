@echo off
rem Meeting Summarizer - desktop client. Double-click to launch.
rem Uses pythonw.exe so no console window stays open behind the app.
cd /d "%~dp0"
set "PYW=backend\python\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw.exe"
start "" "%PYW%" "desktop\run.py"
