@echo off
setlocal
cd /d "%~dp0"

echo.
echo  Chief of Staff dashboard - starting on port 8000
echo.

rem Prefer the project venv; fall back to whatever Python is on PATH.
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

if not defined PY (
    where py >nul 2>&1 && set "PY=py -3"
)
if not defined PY (
    where python >nul 2>&1 && set "PY=python"
)

if not defined PY (
    echo  ERROR: No Python interpreter found.
    echo.
    echo  Install Python 3.10+ and create the environment:
    echo.
    echo      python -m venv .venv
    echo      .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

rem Verify Flask is importable before launching, so a missing dependency
rem produces a useful message instead of a traceback.
%PY% -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Dependencies are not installed for %PY%.
    echo.
    echo  Run:
    echo.
    echo      python -m venv .venv
    echo      .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

%PY% dashboard_v2\app.py
if errorlevel 1 (
    echo.
    echo  The dashboard exited with an error.
)
pause
