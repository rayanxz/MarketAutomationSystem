@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "ROOT=%SCRIPT_DIR%.."

if not exist "%ROOT%\.venv\Scripts\python.exe" (
  echo [ERROR] %ROOT%\.venv not found
  exit /b 1
)

set "PY=%ROOT%\.venv\Scripts\python.exe"

rem Make imports work from root
set "PYTHONPATH=%ROOT%\app"

start "" "http://127.0.0.1:8000/"
"%PY%" app\manage.py runserver 127.0.0.1:8000
