@echo off
title Depot-Tracker
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo  Virtuelle Umgebung fehlt noch.
    echo  Einmalig in PowerShell ausfuehren:
    echo.
    echo    cd "%~dp0"
    echo    python -m venv .venv
    echo    .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

echo.
echo  Depot-Tracker startet ...
echo  Browser: http://localhost:8501
echo  Fenster offen lassen. Beenden: Strg+C
echo.

".venv\Scripts\python.exe" -m streamlit run "src\app.py"

echo.
echo  Depot-Tracker beendet.
pause
