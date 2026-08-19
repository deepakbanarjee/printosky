@echo off
:: ============================================================
::  PRINTOSKY AUTO UPDATE  (pull only -- never touches processes)
::
::  Fetches origin and hard-resets to it if there are new commits
::  on the branch this PC has checked out. That is ALL it does:
::  it does not stop or start anything.
::
::  It is called by BOOT_PRINTOSKY.bat BEFORE the services start,
::  so there is nothing running to interrupt and no restart to
::  perform -- the freshly pulled code is simply what gets launched.
::
::  Run it by hand mid-day and the new code sits on disk but does
::  NOT take effect until the services restart (Python has already
::  loaded the old modules into memory). Use STOP_PRINTOSKY.bat +
::  START_SILENT.bat, or just reboot, if you need it live now.
:: ============================================================

set ROOT=%~dp0
if "%ROOT:~-1%"=="\" set ROOT=%ROOT:~0,-1%
cd /d "%ROOT%"

set LOGS=%ROOT%\logs
if not exist "%LOGS%" mkdir "%LOGS%"
set LOGFILE=%LOGS%\auto_update.log

for /f "delims=" %%b in ('git rev-parse --abbrev-ref HEAD') do set BRANCH=%%b
for /f "delims=" %%h in ('git rev-parse HEAD') do set BEFORE=%%h

echo %date% %time% - checking %BRANCH% (at %BEFORE:~0,7%^) >> "%LOGFILE%"

git fetch origin >nul 2>&1
if errorlevel 1 (
    echo %date% %time% - FETCH FAILED (no network?^) -- starting on current code >> "%LOGFILE%"
    call :report_health False "git fetch failed at boot -- store PC may be running stale code"
    exit /b 1
)

for /f "delims=" %%h in ('git rev-parse origin/%BRANCH%') do set REMOTE=%%h

if "%BEFORE%"=="%REMOTE%" (
    echo %date% %time% - already up to date >> "%LOGFILE%"
    call :report_health True "up to date at %BEFORE:~0,7%"
    exit /b 0
)

echo %date% %time% - updating %BEFORE:~0,7% -^> %REMOTE:~0,7% >> "%LOGFILE%"
git reset --hard origin/%BRANCH% >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    echo %date% %time% - RESET FAILED -- starting on current code >> "%LOGFILE%"
    call :report_health False "git reset failed at boot -- store PC running stale code"
    exit /b 1
)

echo %date% %time% - updated to %REMOTE:~0,7% >> "%LOGFILE%"
call :report_health True "updated to %REMOTE:~0,7%"
exit /b 0

:: ------------------------------------------------------------
:: Report to ops_watchdog so a failed update is an ALERT, not a
:: log line nobody reads -- the whole point of docs/FAIL_LOUD.md.
:: Best-effort: if Python isn't up yet, the boot still proceeds.
:: ------------------------------------------------------------
:report_health
python -c "import sys; sys.path.insert(0, r'%ROOT%'); from ops_watchdog import report; report('store_pc.boot_update', %~1, r'%~2')" >nul 2>&1
exit /b 0
