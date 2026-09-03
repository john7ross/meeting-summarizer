@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY="
call :supported "py -3.11"
call :supported "py -3.12"
call :supported "py -3.10"
call :supported "py -3.9"
call :supported "python"
call :supported "python3"
call :supported "py"
call :any "python"
call :any "python3"
call :any "py"
if not defined PY (
  for /f "usebackq delims=" %%P in (`powershell -NoProfile -ExecutionPolicy Bypass -File "desktop\packaging\bootstrap_python.ps1" -ProbeOnly`) do set "PY=%%P"
)
if not defined PY goto :nopython
%PY% desktop\packaging\installer.py %*
if errorlevel 1 (
  echo.
  echo [!] Setup did not finish. See the message above.
)
pause
exit /b 0

:supported
if defined PY goto :eof
%~1 -c "import sys;raise SystemExit(0 if (3, 9)<=sys.version_info[:2]<=(3, 12) else 1)" >nul 2>&1 && set "PY=%~1"
goto :eof

:any
if defined PY goto :eof
%~1 -c "import sys" >nul 2>&1 && set "PY=%~1"
goto :eof

:nopython
echo.
echo [!] Python not found / Python не найден
echo.
echo     This project needs Python 3.11. It can be installed now,
echo     for this user only, from python.org (about 25 MB).
echo     Проекту нужен Python 3.11. Могу поставить его прямо сейчас,
echo     только для текущего пользователя, с python.org (~25 МБ).
echo.
set "ANS=Y"
echo %* | find /i "--yes" >nul || set /p "ANS=Install it now? / Поставить сейчас? [Y/n]: "
if /i "%ANS%"=="n" goto :manual
for /f "usebackq delims=" %%P in (`powershell -NoProfile -ExecutionPolicy Bypass -File "desktop\packaging\bootstrap_python.ps1"`) do set "PY=%%P"
if not defined PY goto :manual
echo   using %PY%
"%PY%" desktop\packaging\installer.py %*
if errorlevel 1 (
  echo.
  echo [!] Setup did not finish. See the message above.
)
pause
exit /b 0

:manual
echo.
echo     Install Python 3.11 from https://www.python.org/downloads/
echo     and tick "Add python.exe to PATH" on the first screen.
echo     Do NOT install it from the Microsoft Store: that build is
echo     3.13 or newer, which this project does not support.
echo.
echo     Установите Python 3.11 с https://www.python.org/downloads/
echo     и отметьте "Add python.exe to PATH" на первом экране.
echo     Версия из Microsoft Store НЕ подойдёт: это 3.13 и новее,
echo     а закреплённый numpy^<2.0 под неё колёс не публикует.
echo.
pause
exit /b 1
