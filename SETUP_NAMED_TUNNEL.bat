@echo off
title Printosky — Named Tunnel Setup (Run Once)
color 0B

echo.
echo  =====================================================
echo   PRINTOSKY NAMED CLOUDFLARE TUNNEL SETUP
echo   Run this ONCE to get a permanent webhook URL.
echo  =====================================================
echo.
echo  Requirements:
echo    - Free Cloudflare account (cloudflare.com)
echo    - cloudflared.exe in this folder
echo.
echo  Download cloudflared.exe if missing:
echo  https://github.com/cloudflare/cloudflared/releases/latest
echo  Get: cloudflared-windows-amd64.exe, rename to cloudflared.exe
echo.

if not exist "%~dp0cloudflared.exe" (
    echo  ERROR: cloudflared.exe not found.
    echo  Place it in: %~dp0
    pause
    exit /b 1
)

echo  Step 1: Login to Cloudflare (browser will open)
echo.
pause
"%~dp0cloudflared.exe" tunnel login
if %errorlevel% neq 0 ( echo Login failed. & pause & exit /b 1 )

echo.
echo  Step 2: Creating named tunnel "printosky"...
"%~dp0cloudflared.exe" tunnel create printosky
echo.

echo  Step 3: Writing config file...
(
echo tunnel: printosky
echo credentials-file: %USERPROFILE%\.cloudflared\printosky.json
echo ingress:
echo   - service: http://localhost:3002
) > "%~dp0tunnel-config.yml"

echo  Config written to tunnel-config.yml
echo.

echo  Step 4: Getting your permanent URL...
echo  Run the tunnel now to see the URL:
echo.
"%~dp0cloudflared.exe" tunnel --config "%~dp0tunnel-config.yml" run --no-autoupdate
echo.
echo  =====================================================
echo   YOUR PERMANENT WEBHOOK URL IS:
echo   https://[tunnel-id].cfargotunnel.com/webhook/razorpay
echo.
echo   Copy the URL shown above and paste it into:
echo   Razorpay Dashboard - Settings - Webhooks - Edit
echo.
echo   This URL NEVER changes on restart.
echo  =====================================================
echo.
pause
