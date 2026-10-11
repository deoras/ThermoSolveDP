@echo off
REM Builds a standalone ThermoSolveDP (dist\ThermoSolveDP\ThermoSolveDP.exe) on this Windows PC.
cd /d "%~dp0"
if not exist .venv (
    py -3 -m venv .venv || python -m venv .venv
)
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt "pyinstaller>=6.6" || goto :fail
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
.venv\Scripts\python -m PyInstaller --noconfirm --clean --log-level WARN packaging\ThermoSolveDP.spec || goto :fail
echo Checking the program works (solving all built-in examples)...
dist\ThermoSolveDP\ThermoSolveDP.exe --selftest
set RESULT=%ERRORLEVEL%
type dist\ThermoSolveDP\selftest.log
del dist\ThermoSolveDP\selftest.log
if not "%RESULT%"=="0" goto :fail
echo.
echo Done: dist\ThermoSolveDP\ThermoSolveDP.exe  (keep the whole dist\ThermoSolveDP folder together)
pause
exit /b 0
:fail
echo Build failed - please send the messages above.
pause
exit /b 1
