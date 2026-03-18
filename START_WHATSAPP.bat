@echo off
title Printosky WhatsApp Capture
color 0A
echo.
echo  ==========================================
echo   PRINTOSKY WHATSAPP AUTO-CAPTURE
echo   Monitoring: 8943232033 (Oxygen)
echo  ==========================================
echo.

cd /d "%~dp0whatsapp_capture"

:: Check Node is installed
where node >nul 2>&1
if %errorlevel% neq 0 (
    echo  ERROR: Node.js is not installed!
    echo  Please run INSTALL_NODEJS.bat first.
    echo.
    pause
    exit /b 1
)

:: Install packages if node_modules missing
if not exist "node_modules" (
    echo  First run: installing packages (takes 2-3 minutes)...
    echo.
    call npm install
    echo.
    echo  Packages installed!
    echo.
)

echo  Starting WhatsApp capture...
echo  If this is the first time: scan the QR code with your phone.
echo  After that: runs automatically, no QR needed.
echo.
node index.js
pause
