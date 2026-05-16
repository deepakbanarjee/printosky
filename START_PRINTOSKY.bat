@echo off
title Printosky Startup
color 1F

:: Use the directory THIS .bat lives in - never hardcode C:\printosky_watcher.
:: %~dp0 expands to the BAT's own folder (with trailing backslash).
set REPO_DIR=%~dp0
:: Strip trailing backslash for cleaner echo output
if "%REPO_DIR:~-1%"=="\" set REPO_DIR=%REPO_DIR:~0,-1%

echo.
echo  +==========================================+
echo  ^|         PRINTOSKY  -  Starting           ^|
echo  +==========================================+
echo   repo: %REPO_DIR%
echo.

:: Create required folders (only if missing)
if not exist "C:\Printosky\Jobs\Incoming" mkdir "C:\Printosky\Jobs\Incoming"
if not exist "C:\Printosky\Jobs\Archive"  mkdir "C:\Printosky\Jobs\Archive"
if not exist "C:\Printosky\Data"          mkdir "C:\Printosky\Data"

:: Wait for network on boot
timeout /t 5 /nobreak >nul

:: 1. Watcher (Python) -- ports 3002/3003
:: `start /D <dir>` sets the working directory for the new window cleanly,
:: which avoids the nested-quote escape hell that `cd /d && python` causes.
echo  [1/4] Starting Watcher on ports 3002/3003...
start "Printosky Watcher" /D "%REPO_DIR%" cmd /k python watcher.py
timeout /t 3 /nobreak >nul

:: 2. Print Server (Python) -- port 3005
echo  [2/4] Starting Print Server on port 3005...
start "Printosky Print Server" /D "%REPO_DIR%" cmd /k python print_server.py
timeout /t 2 /nobreak >nul

:: 3. Academic Pipeline Worker (Python) -- polls Supabase for generation tasks
echo  [3/4] Starting Academic Pipeline Worker...
start "Printosky Academic Worker" /D "%REPO_DIR%" cmd /k python academic_pipeline_worker.py
timeout /t 2 /nobreak >nul

:: 4. WhatsApp Bot (Meta Cloud API / Webhook Receiver) -- runs inside Watcher
echo  [4/4] Meta WhatsApp Webhook active inside Watcher on port 3002

echo.
echo  All services started. Check the CMD windows for status.
echo.
timeout /t 3 /nobreak >nul
exit
