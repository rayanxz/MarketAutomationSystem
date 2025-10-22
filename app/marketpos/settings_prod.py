import os
from .settings import *  # base

DEBUG = False
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

# Runtime locations under ProgramData (Windows-friendly and survives updates)
PROGRAM_DATA = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
APP_DATA_DIR = os.path.join(PROGRAM_DATA, "MarketPOS")
os.makedirs(APP_DATA_DIR, exist_ok=True)
os.makedirs(os.path.join(APP_DATA_DIR, "data"), exist_ok=True)
os.makedirs(os.path.join(APP_DATA_DIR, "logs"), exist_ok=True)
os.makedirs(os.path.join(APP_DATA_DIR, "backups"), exist_ok=True)

# Move SQLite to %ProgramData%\MarketPOS\data\pos.db in prod
DATABASES["default"]["NAME"] = os.path.join(APP_DATA_DIR, "data", "pos.db")

# Static files collected here before packaging
STATIC_ROOT = BASE_DIR / "staticfiles"  # keep this; collectstatic writes here

# Minimal rotating file log (good enough for first packaging)
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": os.path.join(APP_DATA_DIR, "logs", "app.log"),
            "maxBytes": 1_000_000,
            "backupCount": 3,
            "encoding": "utf-8",
        },
        "console": {"class": "logging.StreamHandler"},
    },
    "root": {"handlers": ["file", "console"], "level": "INFO"},
}


SESSION_SAVE_EVERY_REQUEST = False
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"

