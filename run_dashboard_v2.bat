@echo off
echo.
echo  CoS Dashboard - starting on port 8000
echo.
cd /d "%~dp0"
.venv\Scripts\python.exe dashboard_v2/app.py
pause
