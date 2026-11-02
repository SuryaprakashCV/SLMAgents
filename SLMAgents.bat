@echo off
REM SLMAgents 2.0 — Windows Double-Clickable Desktop App Launcher
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PYTHON_BIN=.venv\Scripts\python.exe"
) else (
    where python >nul 2>nul
    if %ERRORLEVEL% neq 0 (
        echo [SLMAgents] Python 3.10+ not found. Please install Python from https://www.python.org/downloads/windows/
        pause
        exit /b 1
    )
    echo [SLMAgents] Creating local Python virtual environment (.venv)...
    python -m venv .venv
    set "PYTHON_BIN=.venv\Scripts\python.exe"
    echo [SLMAgents] Installing pure-CPU runtime dependencies...
    "%PYTHON_BIN%" -m pip install --upgrade pip
    "%PYTHON_BIN%" -m pip install -r requirements.txt
)

set "PYTHONPATH=%cd%"
"%PYTHON_BIN%" desktop_app.py %*
endlocal
