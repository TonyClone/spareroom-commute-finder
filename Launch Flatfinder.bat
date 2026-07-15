@echo off
setlocal EnableExtensions
title Flatfinder
cd /d "%~dp0"

set "ROOT=%CD%"
set "PY=%ROOT%\.venv\Scripts\python.exe"
set "ENVFILE=%ROOT%\.env"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

echo(
echo   Flatfinder
echo(

if exist "%PY%" goto :have_venv

REM ------------------------------------------------------------------
REM First run: build a private Python environment and install the app.
REM ------------------------------------------------------------------
echo   First-time setup - about a minute, and only happens once.
echo(

set "BOOT="
py -3 --version >nul 2>&1
if %errorlevel%==0 set "BOOT=py -3"
if defined BOOT goto :have_boot
python --version >nul 2>&1
if %errorlevel%==0 set "BOOT=python"
:have_boot
if not defined BOOT goto :no_python

echo   Creating a private environment...
%BOOT% -m venv "%ROOT%\.venv"
if not exist "%PY%" goto :venv_fail
echo   Installing Flatfinder (downloading a few packages)...
"%PY%" -m pip install -q -U pip
"%PY%" -m pip install -q -e "%ROOT%"
if errorlevel 1 goto :install_fail
echo   Setup complete.
echo(

:have_venv
REM The app creates and manages config.yaml, .env and data/ itself — in your
REM FLATFINDER_HOME folder if you've set one, otherwise here. Nothing to seed.

"%PY%" -m flatfinder menu
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
  echo(
  echo   Flatfinder exited with code %EXITCODE%.
  echo   Logs: %ROOT%\data\logs\latest.txt
)
goto :end

:no_python
echo   ============================================================
echo    Flatfinder needs Python 3.11 or newer ^(a one-time install^).
echo(
echo    Easiest way: press Start, type "Terminal", open it, then run:
echo(
echo        winget install -e --id Python.Python.3.12
echo(
echo    Or download from https://www.python.org/downloads/
echo    and tick "Add Python to PATH" during setup.
echo(
echo    Then just double-click this file again.
echo   ============================================================
goto :end

:venv_fail
echo   ERROR: could not create the Python environment in .venv
goto :end

:install_fail
echo   ERROR: install failed - see the messages above.
goto :end

:end
echo(
pause
endlocal
