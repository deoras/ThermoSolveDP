@echo off
REM ThermoSolveDP launcher for Windows: creates a virtual environment on first run.
cd /d "%~dp0"
if not exist .venv (
    echo Creating virtual environment...
    py -3 -m venv .venv || python -m venv .venv
    .venv\Scripts\python -m pip install --upgrade pip
    .venv\Scripts\python -m pip install -r requirements.txt
)
.venv\Scripts\python main.py %*
if errorlevel 1 pause
