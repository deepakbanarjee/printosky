@echo off
title Printosky — Stopping Services
color 4F

echo.
echo  Stopping all Printosky services...
echo.

taskkill /F /IM python.exe /T >nul 2>&1
echo  [1] Python (watcher + bot) stopped.

taskkill /F /IM node.exe /T >nul 2>&1
echo  [2] Node.js (WhatsApp capture) stopped.

taskkill /F /IM cloudflared.exe /T >nul 2>&1
echo  [3] Cloudflare tunnel stopped.

echo.
echo  All services stopped.
echo  Run START_SILENT.bat to restart.
echo.
timeout /t 3 /nobreak >nul
exit
