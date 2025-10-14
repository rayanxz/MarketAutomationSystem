# scripts/runwaitress.py
import os, sys, pathlib
from waitress import serve
from django.core.wsgi import get_wsgi_application

ROOT = pathlib.Path(__file__).resolve().parents[1]   # C:\MarketAutomationSystem
APP_DIR = ROOT / "app"                               # where marketpos/ lives
sys.path.insert(0, str(APP_DIR))                     # make "app" importable

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "marketpos.settings")

application = get_wsgi_application()

if __name__ == "__main__":
    print("Using settings:", os.environ["DJANGO_SETTINGS_MODULE"], flush=True)
    print("Import path[0]:", sys.path[0], flush=True)
    print("Serving on http://127.0.0.1:8010", flush=True)
    serve(application, listen="127.0.0.1:8010")
