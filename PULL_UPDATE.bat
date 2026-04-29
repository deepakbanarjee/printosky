@echo off
title Printosky — Pull Latest Update
color 2F

echo.
echo  ╔══════════════════════════════════════════╗
echo  ║      PRINTOSKY  —  Pull Latest Code      ║
echo  ╚══════════════════════════════════════════╝
echo.

cd /d C:\printosky_watcher

:: Show current state
echo  Current branch and status:
git branch
git status --short
echo.

:: Fetch all remote changes
echo  [1/3] Fetching from origin...
git fetch origin
echo.

:: Hard reset to remote — no conflicts, no prompts
echo  [2/3] Resetting to origin/sprint/session-9...
git checkout sprint/session-9
git reset --hard origin/sprint/session-9
echo.

:: Show what we're now at
echo  [3/3] Done. Current version:
git log --oneline -3
echo.

echo  ============================================
echo   Update complete. Run START_PRINTOSKY.bat
echo   to restart services with the new code.
echo  ============================================
echo.
pause
