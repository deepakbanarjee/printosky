@echo off
title Printosky Startup
color 1F

echo.
echo  ╔══════════════════════════════════════════╗
echo  ║         PRINTOSKY  —  Starting           ║
echo  ╚══════════════════════════════════════════╝
echo.

:: Create required folders
if not exist "C:\Printosky\Jobs\Incoming" mkdir "C:\Printosky\Jobs\Incoming"
if not exist "C:\Printosky\Jobs\Archive"  mkdir "C:\Printosky\Jobs\Archive"
if not exist "C:\Printosky\Data"          mkdir "C:\Printosky\Data"

:: Wait for network on boot
timeout /t 5 /nobreak >nul

:: 1. Watcher (Python) — ports 3002 3003
echo  [1/4] Starting Watcher on ports 3002/3003...
start "Printosky Watcher" cmd /k "cd /d C:\printosky_watcher && python watcher.py"
timeout /t 3 /nobreak >nul

:: 2. Print Server (Python) — port 3005
echo  [2/4] Starting Print Server on port 3005...
start "Printosky Print Server" cmd /k "cd /d C:\printosky_watcher && python print_server.py"
timeout /t 2 /nobreak >nul

:: 3. Academic Pipeline Worker (Python) — polls Supabase for generation tasks
echo  [3/4] Starting Academic Pipeline Worker...
start "Printosky Academic Worker" cmd /k "cd /d C:\printosky_watcher && python academic_pipeline_worker.py"
timeout /t 2 /nobreak >nul

:: 4. WhatsApp Bot (Meta Cloud API / Webhook Receiver) — runs in Watcher
echo  [4/4] Meta WhatsApp Webhook active inside Watcher on port 3002
:: Retired Node.js Bot: start "Printosky WhatsApp Bot" cmd /k "cd /d C:\printosky_watcher\whatsapp_capture && node index.js"

echo.
echo  All 4 services started. Check the CMD windows for status.
echo.
timeout /t 3 /nobreak >nul
exit
