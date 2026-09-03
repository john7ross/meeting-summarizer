@echo off
rem Meeting Summarizer - desktop client. Double-click to launch.
rem
rem One launcher for all three layouts, because they only differ in WHERE the
rem interpreter is: the full build carries its own, a min install records the one
rem INSTALL.bat used, and a git clone has neither until INSTALL.bat has run.
rem Hardcoding backend\python here is what left a fresh clone unable to start.
cd /d "%~dp0"

rem pythonw, not python: the app has its own window and a console behind it is
rem just noise the user then has to close.
set "PY=backend\python\pythonw.exe"
if exist "%PY%" goto run

set "PY="
if exist "config\interpreter.txt" set /p PY=<config\interpreter.txt
if not defined PY goto onpath
rem What INSTALL.bat recorded is python.exe; use its windowless twin if present.
call set "PYW=%%PY:python.exe=pythonw.exe%%"
if exist "%PYW%" set "PY=%PYW%"
goto run

:onpath
set "PY=pythonw.exe"

:run
start "" "%PY%" "desktop\run.py"
