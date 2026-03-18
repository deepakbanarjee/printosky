@echo off
title Printosky — Service Status
color 1F

echo.
echo  ============================================================
echo   PRINTOSKY SERVICE STATUS
echo  ============================================================
echo.

:: Check Python (watcher)
tasklist /FI "IMAGENAME eq python.exe" 2>nul | find /I "python.exe" >nul
if %errorlevel% equ 0 (
    echo  [OK] Watcher ^(Python^) is RUNNING
) else (
    echo  [!!] Watcher ^(Python^) is STOPPED
)

:: Check Node (WhatsApp)
tasklist /FI "IMAGENAME eq node.exe" 2>nul | find /I "node.exe" >nul
if %errorlevel% equ 0 (
    echo  [OK] WhatsApp capture ^(Node^) is RUNNING
) else (
    echo  [!!] WhatsApp capture ^(Node^) is STOPPED
)

:: Check cloudflared
tasklist /FI "IMAGENAME eq cloudflared.exe" 2>nul | find /I "cloudflared.exe" >nul
if %errorlevel% equ 0 (
    echo  [OK] Cloudflare tunnel is RUNNING
) else (
    echo  [!!] Cloudflare tunnel is STOPPED
)

echo.
echo  ============================================================
echo   RECENT LOG TAILS
echo  ============================================================

set LOGS=%~dp0logs

if exist "%LOGS%\watcher.log" (
    echo.
    echo  -- watcher.log (last 5 lines) --
    powershell -command "Get-Content '%LOGS%\watcher.log' -Tail 5"
) else (
    echo  No watcher.log found yet.
)

if exist "%LOGS%\tunnel.log" (
    echo.
    echo  -- tunnel.log (last 3 lines) --
    powershell -command "Get-Content '%LOGS%\tunnel.log' -Tail 3"
)

echo.
pause
