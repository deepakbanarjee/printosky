@echo off
:: PRINTOSKY AUTO-START SETUP
:: Run this ONCE to make Printosky pull the latest code and start
:: automatically on Windows login.
::
:: The chain is: login -> 15s network delay -> BOOT_PRINTOSKY.bat,
:: which pulls (AUTO_UPDATE.bat) and THEN starts (START_SILENT.bat).
:: Update-before-start means the day's first boot picks up whatever
:: is on the branch, with no restart and no risk to a print in
:: progress. See docs/AUTO_UPDATE.md.

echo Setting up Printosky to update + auto-start on Windows login...

set SCRIPT_DIR=%~dp0

:: Write boot delay VBS — waits 15s after login for network to connect
:: (the pull needs it), then runs the update + start sequence hidden.
(
echo WScript.Sleep 15000
echo Dim shell : Set shell = CreateObject("WScript.Shell"^)
echo shell.Run "cmd /c """ & "%SCRIPT_DIR%BOOT_PRINTOSKY.bat" & """", 0, False
) > "%SCRIPT_DIR%boot_delay.vbs"

:: Register boot_delay.vbs to run on login
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" ^
    /v "PrintoskyTracker" ^
    /t REG_SZ ^
    /d "wscript.exe \"%SCRIPT_DIR%boot_delay.vbs\"" ^
    /f

if %errorlevel% == 0 (
    echo.
    echo  SUCCESS: on every Windows login Printosky will now pull the
    echo  latest code, then start all services silently.
    echo  15s delay on boot to allow network connection first.
    echo.
    echo  Logs:  logs\boot.log         (the boot sequence^)
    echo         logs\auto_update.log  (what was pulled^)
    echo         logs\watcher.log etc. (each service^)
    echo.
    echo  A failed pull also alerts via ops_watchdog -- you do not have
    echo  to read a log to find out the PC is on stale code.
    echo.
    echo  To disable: run REMOVE_AUTOSTART.bat
) else (
    echo  Failed. Right-click this file and Run as Administrator.
)
pause
