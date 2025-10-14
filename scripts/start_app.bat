@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "ROOT=%SCRIPT_DIR%.."
set "PY=%ROOT%\.venv\Scripts\python.exe"
set "URL=http://127.0.0.1:8010/"

if not exist "%PY%" (
  echo [ERROR] Missing venv python at %PY%
  exit /b 1
)

rem --- start waitress in a separate window (or /min to keep it small)
start "MarketPOS (server)" "%PY%" -u "%ROOT%\scripts\runwaitress.py"

rem small delay so server is listening
timeout /t 2 >nul

rem --- Launch as a chrome-less app window (Edge):
start "" "msedge.exe" --app=%URL% --no-first-run --disable-features=msEdgeWelcomePage

rem If you use Chrome instead, comment Edge line and uncomment the next one:
rem start "" "chrome.exe" --app=%URL% --no-first-run --disable-features=ChromeWhatsNewUI

exit /b 0
