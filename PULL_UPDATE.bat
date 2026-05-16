@echo off
title Printosky - Pull Latest Update
color 2F

:: Use the directory THIS .bat lives in - never hardcode C:\printosky_watcher.
set REPO_DIR=%~dp0
if "%REPO_DIR:~-1%"=="\" set REPO_DIR=%REPO_DIR:~0,-1%
cd /d "%REPO_DIR%"

echo.
echo  +==========================================+
echo  ^|      PRINTOSKY  -  Pull Latest Code      ^|
echo  +==========================================+
echo   repo: %REPO_DIR%
echo.

:: Show current state
echo  Current branch and status:
git branch --show-current
git status --short
echo.

:: Detect the checked-out branch and reset to its upstream
for /f "delims=" %%b in ('git rev-parse --abbrev-ref HEAD') do set BRANCH=%%b

echo  [1/3] Fetching from origin...
git fetch origin
echo.

echo  [2/3] Resetting %BRANCH% to origin/%BRANCH% (HARD reset, no merge)...
git reset --hard origin/%BRANCH%
echo.

echo  [3/3] Done. Current version:
git log --oneline -3
echo.

echo  ============================================
echo   Update complete. Run START_PRINTOSKY.bat
echo   to restart services with the new code.
echo  ============================================
echo.
pause
