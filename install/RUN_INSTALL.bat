@echo off
REM Double-click this to run the store-PC installer (install\bootstrap.ps1).
REM Same script INSTALL.md documents running via PowerShell directly --
REM this just wraps it so it can be double-clicked instead of typed.
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0bootstrap.ps1"
pause
