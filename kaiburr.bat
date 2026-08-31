@echo off
setlocal

:: Get the directory of this script
set "ROOT=%~dp0"
cd /d "%ROOT%"

echo ==================================================
echo  Kaiburr Bootstrapper
echo ==================================================

:: Check Python version
for /f "tokens=2 delims= " %%i in ('python --version 2^>^&1') do set python_version=%%i
echo [*] Detected Python version: %python_version%

echo %python_version% | findstr /R "^3\.1[1-9]" > nul
if %errorlevel% neq 0 (
    echo [ERROR] Python 3.11+ is required but not found.
    echo Please install Python 3.11 or higher and try again.
    pause
    exit /b 1
)

:: Create Virtual Environment if it doesn't exist
if not exist "venv\Scripts\activate.bat" (
    echo [*] Creating virtual environment...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

:: Launch core_launcher.py using the virtual environment's python
echo [*] Launching Kaiburr Core...
"venv\Scripts\python.exe" core_launcher.py

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Kaiburr exited with an error.
    pause
)
