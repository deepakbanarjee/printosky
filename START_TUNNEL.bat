@echo off
:: Printosky Cloudflare Tunnel Launcher
:: Uses named tunnel (permanent URL) if set up, otherwise random URL

if exist "%~dp0tunnel-config.yml" (
    :: Named tunnel — permanent URL, no need to update Razorpay webhook
    "%~dp0cloudflared.exe" tunnel --config "%~dp0tunnel-config.yml" run --no-autoupdate
) else (
    :: Random tunnel fallback — URL changes on every restart
    :: Run SETUP_NAMED_TUNNEL.bat once to get a permanent URL
    "%~dp0cloudflared.exe" tunnel --url http://localhost:3002 --no-autoupdate
)
