@echo off
title Printosky — Restarting Watcher
color 6F

set ROOT=%~dp0
set LOGS=%ROOT%logs
if not exist "%LOGS%" mkdir "%LOGS%"

echo.
echo  Stopping Python (watcher + bot + print server)...
taskkill /F /IM python.exe /T >nul 2>&1
echo  Stopped.
echo.

timeout /t 3 /nobreak >nul

echo  Starting watcher...
start "" /B wscript.exe "%ROOT%run_hidden.vbs" "python ""%ROOT%watcher.py"" >> ""%LOGS%\watcher.log"" 2>&1"
timeout /t 3 /nobreak >nul

echo  Watcher restarted. Logs: %LOGS%\watcher.log
echo.
echo  (Node and Cloudflare tunnel untouched)
echo.
timeout /t 3 /nobreak >nul
exit
