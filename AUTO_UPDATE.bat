@echo off
:: ============================================================
::  PRINTOSKY AUTO UPDATE (checked by Task Scheduler)
::  Registered by SETUP_AUTO_UPDATE.bat to run every 15 minutes.
::
::  Checks origin for new commits on the branch this PC has
::  checked out. If there are none, exits immediately -- no
::  restart, no disruption. If there ARE new commits, stops all
::  Printosky processes, hard-resets to origin, and restarts
::  everything silently (same as PULL_UPDATE.bat + START_SILENT.bat,
::  just unattended).
::
::  This is a fallback-interval mechanism, not instant: a change
::  pushed to main can take up to 15 minutes to reach this PC, and
::  a restart that lands mid-print interrupts it for the few
::  seconds the processes take to come back up. Schedule it for a
::  quiet period if that matters more than freshness -- see
::  docs/AUTO_UPDATE.md.
:: ============================================================

set ROOT=%~dp0
if "%ROOT:~-1%"=="\" set ROOT=%ROOT:~0,-1%
cd /d "%ROOT%"

set LOGS=%ROOT%\logs
if not exist "%LOGS%" mkdir "%LOGS%"
set LOGFILE=%LOGS%\auto_update.log

for /f "delims=" %%b in ('git rev-parse --abbrev-ref HEAD') do set BRANCH=%%b
for /f "delims=" %%h in ('git rev-parse HEAD') do set BEFORE=%%h

git fetch origin >nul 2>&1
if errorlevel 1 (
    echo %date% %time% - fetch failed, network down? skipping this cycle >> "%LOGFILE%"
    exit /b 1
)

for /f "delims=" %%h in ('git rev-parse origin/%BRANCH%') do set REMOTE=%%h

if "%BEFORE%"=="%REMOTE%" (
    :: Already up to date -- the common case. Stay quiet, don't spam the log.
    exit /b 0
)

echo %date% %time% - new commits on %BRANCH% (%BEFORE:~0,7% -^> %REMOTE:~0,7%^), updating >> "%LOGFILE%"

:: Stop everything before touching the working tree, same processes
:: STOP_PRINTOSKY.bat targets -- inlined (not `call`ed) because that
:: script ends in a bare `exit`, which would also kill THIS script if
:: called directly instead of in its own process.
taskkill /F /IM python.exe /T >nul 2>&1
taskkill /F /IM node.exe /T >nul 2>&1

git reset --hard origin/%BRANCH% >> "%LOGFILE%" 2>&1

echo %date% %time% - reset done, restarting silently >> "%LOGFILE%"

:: START_SILENT.bat also ends in a bare `exit` -- run it in its own
:: spawned shell so it can't terminate the Task Scheduler process.
start "" cmd /c "%ROOT%\START_SILENT.bat"

echo %date% %time% - restart triggered >> "%LOGFILE%"
exit /b 0
