@echo off
:: ============================================================
::  PRINTOSKY BOOT SEQUENCE
::  Registered by SETUP_AUTOSTART.bat to run once per login.
::
::  1. Pull the latest code (AUTO_UPDATE.bat)
::  2. Start every service silently (START_SILENT.bat)
::
::  In that order deliberately: at boot nothing is running yet, so
::  the update needs no stop/restart and cannot interrupt a print.
::  The freshly pulled code is simply what gets launched.
::
::  Everything runs hidden. Detail goes to logs\ -- boot.log for
::  this sequence, auto_update.log for the pull, and one log per
::  service. A failed pull also alerts via ops_watchdog, so a store
::  PC quietly running stale code is not something you find out
::  about by remembering to read a log file.
:: ============================================================

set ROOT=%~dp0
if "%ROOT:~-1%"=="\" set ROOT=%ROOT:~0,-1%
cd /d "%ROOT%"

set LOGS=%ROOT%\logs
if not exist "%LOGS%" mkdir "%LOGS%"
set BOOTLOG=%LOGS%\boot.log

echo. >> "%BOOTLOG%"
echo ===== %date% %time% - boot sequence start ===== >> "%BOOTLOG%"

:: 1. Update. Never fatal -- a store that cannot reach GitHub must
::    still open for business on the code it already has.
echo %date% %time% - pulling latest code... >> "%BOOTLOG%"
call "%ROOT%\AUTO_UPDATE.bat"
echo %date% %time% - pull step finished (exit %errorlevel%^) >> "%BOOTLOG%"

:: 2. Start services. Spawned in its own shell because START_SILENT.bat
::    ends in a bare `exit`, which would otherwise terminate this script
::    before the closing log line.
echo %date% %time% - starting services silently... >> "%BOOTLOG%"
start "" cmd /c "%ROOT%\START_SILENT.bat"

echo %date% %time% - boot sequence done >> "%BOOTLOG%"
exit /b 0
