@echo off
setlocal

rem --- Paths
set "SCRIPT_DIR=%~dp0"
set "ROOT=%SCRIPT_DIR%.."
set "APP=%ROOT%\app"
set "PY=%ROOT%\.venv\Scripts\python.exe"
set "URL=http://127.0.0.1:8010/"

rem --- Settings to use (change to marketpos.settings_dev or .settings_prod if you want)
set "DJANGO_SETTINGS_MODULE=marketpos.settings"
set "PYTHONPATH=%APP%"

rem --- Sanity check
if not exist "%PY%" (
  echo [ERROR] Missing venv python at %PY%
  exit /b 1
)

rem --- Start waitress in a new window
start "MarketPOS (server)" "%PY%" -u "%ROOT%\scripts\runwaitress.py"

rem --- Wait up to ~10s for the port to open
for /l %%I in (1,1,10) do (
  powershell -NoProfile -Command "try { $c=New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1',8010); $c.Close(); exit 0 } catch { exit 1 }" >nul 2>&1
  if not errorlevel 1 goto :launch
  timeout /t 1 >nul
)

echo [WARN] Server may not be ready yet; launching browser anyway...

:launch
rem --- Launch Edge as an app window (private mode to avoid stale cookies)
start "" msedge.exe --app=%URL% --no-first-run --disable-features=msEdgeWelcomePage --inprivate

exit /b 0
