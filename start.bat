@echo off
:: P1 Alert Listener – Windows launcher
:: Double-click this file to start the app, or place a shortcut in the Startup folder.

cd /d "%~dp0"

:: Use pythonw to suppress the console window (use python.exe to keep it for debugging)
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" main.py
) else (
    echo Virtual environment not found. Run setup first:
    echo   python -m venv .venv
    echo   .venv\Scripts\pip install -r requirements.txt
    pause
)
