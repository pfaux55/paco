@echo off
setlocal
cd /d %~dp0\..

set "VENV_DIR=.venv-win"
set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
  echo Creating Windows virtual environment...
  python -m venv "%VENV_DIR%" || exit /b 1
)

"%PYTHON_EXE%" -m pip show miniaudio pypdf >nul 2>nul
if errorlevel 1 (
  echo Installing Windows runtime dependencies...
  "%PYTHON_EXE%" -m pip install --upgrade pip || exit /b 1
  "%PYTHON_EXE%" -m pip install -r requirements.txt || exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\ensure_ollama.ps1"
if errorlevel 1 exit /b 1

"%PYTHON_EXE%" run_assistant.py
