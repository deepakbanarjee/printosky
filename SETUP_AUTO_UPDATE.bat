@echo off
:: PRINTOSKY AUTO UPDATE SETUP
:: Run this ONCE to make the store PC check for new Printosky code
:: every 15 minutes and pull + restart automatically, with nobody
:: needing to drive over or remote in.
::
:: To change the interval: edit /mo 15 below and re-run this file --
:: /f overwrites the existing scheduled task.
:: To undo: run REMOVE_AUTO_UPDATE.bat

set SCRIPT_DIR=%~dp0
if "%SCRIPT_DIR:~-1%"=="\" set SCRIPT_DIR=%SCRIPT_DIR:~0,-1%

echo Registering PrintoskyAutoUpdate to run every 15 minutes...

schtasks /create /tn "PrintoskyAutoUpdate" ^
    /tr "\"%SCRIPT_DIR%\AUTO_UPDATE.bat\"" ^
    /sc minute /mo 15 /rl highest /f

if %errorlevel% == 0 (
    echo.
    echo  SUCCESS: this PC will check for updates every 15 minutes and
    echo  auto pull + restart when new code lands on the branch it has
    echo  checked out. Log: logs\auto_update.log
    echo.
    echo  A restart takes a few seconds and interrupts any print in
    echo  progress at that moment -- pick your interval/timing with that
    echo  in mind. See docs/AUTO_UPDATE.md.
    echo.
    echo  To disable: run REMOVE_AUTO_UPDATE.bat
) else (
    echo  Failed. Right-click this file and Run as Administrator.
)
pause
