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
echo  [1/3] Starting Watcher on ports 3002/3003...
start "Printosky Watcher" cmd /k "cd /d C:\printosky_watcher && python watcher.py"
timeout /t 3 /nobreak >nul

:: 2. Print Server (Python) — port 3005
echo  [2/3] Starting Print Server on port 3005...
start "Printosky Print Server" cmd /k "cd /d C:\printosky_watcher && python print_server.py"
timeout /t 2 /nobreak >nul

:: 3. WhatsApp Bot (Node) — port 3001
echo  [3/3] Starting WhatsApp Bot on port 3001...
start "Printosky WhatsApp Bot" cmd /k "cd /d C:\printosky_watcher\whatsapp_capture && node index.js"

echo.
echo  All 3 services started. Check the CMD windows for status.
echo.
timeout /t 3 /nobreak >nul
exit
