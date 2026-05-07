@echo off
:: pdf_tools_server.bat — Launch the PDF tools server (port 3006).
:: Run this before opening printosky.com/bw-converter on the store PC.

echo Starting PDF Tools Server on port 3006...
echo.
python "%~dp0pdf_tools_server.py"
pause
