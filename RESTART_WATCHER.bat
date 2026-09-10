@echo off
title Printosky — Restarting Services
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

:: The taskkill above stops EVERY python.exe -- five services, not one:
:: watcher, print_server, store_puller, academic worker, transcription worker.
:: This script used to restart only the watcher, and watcher.py does not start
:: the others (grep it: no reference to store_puller anywhere). So "restart the
:: watcher" left the store with no job puller and no workers, silently, until
:: someone noticed paid jobs had stopped printing -- a recovery script that
:: quietly made things worse. Restart everything it killed. Node and the
:: Cloudflare tunnel are untouched, because the taskkill never stopped them.

echo  Starting watcher...
start "" /B wscript.exe "%ROOT%run_hidden.vbs" "python ""%ROOT%watcher.py"" >> ""%LOGS%\watcher.log"" 2>&1"
timeout /t 3 /nobreak >nul

echo  Starting print server...
start "" /B wscript.exe "%ROOT%run_hidden.vbs" "python ""%ROOT%print_server.py"" >> ""%LOGS%\print_server.log"" 2>&1"
timeout /t 2 /nobreak >nul

echo  Starting store job puller...
start "" /B wscript.exe "%ROOT%run_hidden.vbs" "python ""%ROOT%store_puller.py"" >> ""%LOGS%\store_puller.log"" 2>&1"
timeout /t 2 /nobreak >nul

echo  Starting academic pipeline worker...
start "" /B wscript.exe "%ROOT%run_hidden.vbs" "python ""%ROOT%academic_pipeline_worker.py"" >> ""%LOGS%\academic_worker.log"" 2>&1"
timeout /t 2 /nobreak >nul

echo  Starting cloud transcription worker...
start "" /B wscript.exe "%ROOT%run_hidden.vbs" "python ""%ROOT%tools\cloud_transcription_worker.py"" >> ""%LOGS%\transcription_worker.log"" 2>&1"
timeout /t 2 /nobreak >nul

echo.
echo  All five Python services restarted. Logs: %LOGS%\
echo.
echo  (Node and Cloudflare tunnel untouched)
echo.
timeout /t 3 /nobreak >nul
exit
