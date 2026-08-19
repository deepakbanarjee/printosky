@echo off
:: Undo SETUP_AUTO_UPDATE.bat -- removes the scheduled task.
:: This PC goes back to needing PULL_UPDATE.bat run by hand.

echo Removing PrintoskyAutoUpdate scheduled task...
schtasks /delete /tn "PrintoskyAutoUpdate" /f

if %errorlevel% == 0 (
    echo  Done. This PC will no longer auto-update.
) else (
    echo  Nothing to remove, or failed -- right-click and Run as Administrator.
)
pause
