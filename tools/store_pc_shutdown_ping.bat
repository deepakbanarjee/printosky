@echo off
REM ===========================================================================
REM Printosky store-PC clean-shutdown ping.
REM
REM Run this as a Windows *shutdown script* (or a Task Scheduler task triggered
REM on shutdown). It tells the cloud the PC is shutting down CLEANLY, so the
REM closing WhatsApp reads "closed cleanly" instead of "went offline
REM unexpectedly". It is best-effort only — if it fails (network already down,
REM power cut), the heartbeat watcher still detects the PC is gone and sends the
REM closing message; this ping only adds the clean-vs-crash wording.
REM
REM Requires CRON_SECRET in the machine environment (same value as the Vercel
REM CRON_SECRET). Setup steps: docs/STORE_PC_SHUTDOWN_PING.md
REM ===========================================================================
setlocal

if "%CRON_SECRET%"=="" (
  echo [shutdown-ping] CRON_SECRET not set - skipping.
  exit /b 0
)

REM -m 8 caps the call at 8s so it never stalls shutdown.
curl -s -m 8 -H "Authorization: Bearer %CRON_SECRET%" ^
  "https://printosky.vercel.app/cron/pc-shutdown" >nul 2>&1

exit /b 0
