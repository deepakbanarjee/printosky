@echo off
:: pdf_bw.bat — Drag a PDF onto this file to convert it to clean B&W.
:: Output saved as <original_name>_BW.pdf in the same folder.

if "%~1"=="" (
    echo Drag a PDF file onto this script to convert it to B&W.
    pause
    exit /b 1
)

echo Converting: %~nx1
python "%~dp0pdf_bw.py" "%~1"

if errorlevel 1 (
    echo.
    echo Conversion failed.
) else (
    echo.
    echo Done.
)
pause
