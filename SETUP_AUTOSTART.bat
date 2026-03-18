@echo off
:: PRINTOSKY AUTO-START SETUP
:: Run this ONCE to make Printosky start automatically on Windows login

echo Setting up Printosky to auto-start on Windows login...

set SCRIPT_DIR=%~dp0

:: Write boot delay VBS — waits 15s after login for network to connect
(
echo WScript.Sleep 15000
echo Dim shell : Set shell = CreateObject("WScript.Shell"^)
echo shell.Run "cmd /c """ & "%SCRIPT_DIR%START_SILENT.bat" & """", 0, False
) > "%SCRIPT_DIR%boot_delay.vbs"

:: Register boot_delay.vbs to run on login
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" ^
    /v "PrintoskyTracker" ^
    /t REG_SZ ^
    /d "wscript.exe \"%SCRIPT_DIR%boot_delay.vbs\"" ^
    /f

if %errorlevel% == 0 (
    echo.
    echo  SUCCESS: Printosky will now auto-start on every Windows login.
    echo  15s delay on boot to allow network connection first.
    echo.
    echo  To disable: run REMOVE_AUTOSTART.bat
) else (
    echo  Failed. Right-click this file and Run as Administrator.
)
pause
