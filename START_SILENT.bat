@echo off
title Printosky — Starting...
color 1F

:: ============================================================
::  PRINTOSKY SILENT LAUNCHER
::  Starts all services in background — no open terminals
::  Double-click once. Everything runs silently.
::  Check LOGS folder for output.
:: ============================================================

set ROOT=%~dp0
set LOGS=%ROOT%logs
if not exist "%LOGS%" mkdir "%LOGS%"

:: Create job folders
if not exist "C:\Printosky\Jobs\Incoming" mkdir "C:\Printosky\Jobs\Incoming"
if not exist "C:\Printosky\Jobs\Archive"  mkdir "C:\Printosky\Jobs\Archive"
if not exist "C:\Printosky\Data"          mkdir "C:\Printosky\Data"

echo.
echo  Starting Printosky services silently...
echo  Logs: %LOGS%\
echo.

:: ----------------------------------------------------------
:: 1. Watcher (Python) — all bot + webhook + print servers
:: ----------------------------------------------------------
echo  [1/3] Starting watcher + bot + print server...
start "" /B wscript.exe "%ROOT%run_hidden.vbs" "python \"%ROOT%watcher.py\" >> \"%LOGS%\watcher.log\" 2>&1"
timeout /t 3 /nobreak >nul
echo  Watcher started.

:: ----------------------------------------------------------
:: 2. WhatsApp Capture (Node)
:: ----------------------------------------------------------
echo  [2/3] Starting WhatsApp capture...
where node >nul 2>&1
if %errorlevel% equ 0 (
    if exist "%ROOT%whatsapp_capture\node_modules" (
        start "" /B wscript.exe "%ROOT%run_hidden.vbs" "node \"%ROOT%whatsapp_capture\index.js\" >> \"%LOGS%\whatsapp.log\" 2>&1"
        echo  WhatsApp capture started.
    ) else (
        echo  WhatsApp packages missing. Run START_WHATSAPP.bat first.
    )
) else (
    echo  Node.js not found. Skipping WhatsApp capture.
)

:: ----------------------------------------------------------
:: 3. Print Server (Python) — admin panel backend, port 3005
:: ----------------------------------------------------------
echo  [3/3] Starting print server on port 3005...
start "" /B wscript.exe "%ROOT%run_hidden.vbs" "python \"%ROOT%print_server.py\" >> \"%LOGS%\print_server.log\" 2>&1"
echo  Print server started.

:: NOTE: Cloudflare tunnel is managed by Windows Task Scheduler (PrintoskyTunnel).
:: It starts automatically at login — no need to start it here.

echo.
echo  ============================================================
echo   All services started in background.
echo.
echo   Logs:     %LOGS%\
echo   Jobs:     C:\Printosky\Jobs\Incoming\
echo   Watcher:  watcher.log
echo   WhatsApp: whatsapp.log
echo   Tunnel:   tunnel.log
echo  ============================================================
echo.
echo  To stop all: run STOP_PRINTOSKY.bat
echo  To check status: run STATUS_PRINTOSKY.bat
echo.
timeout /t 5 /nobreak >nul
exit
