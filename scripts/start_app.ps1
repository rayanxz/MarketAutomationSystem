$env:DJANGO_SETTINGS_MODULE="marketpos.settings"
& ..\.venv\Scripts\Activate.ps1
python ..\app\manage.py collectstatic --noinput
Start-Process -NoNewWindow python -ArgumentList "scripts\run_waitress.py"
Start-Sleep -Seconds 2
Start-Process msedge.exe -ArgumentList "--app=http://127.0.0.1:8010","--no-first-run","--disable-features=msEdgeWelcomePage"
