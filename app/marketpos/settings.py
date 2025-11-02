from pathlib import Path

# ---- Paths
BASE_DIR = Path(__file__).resolve().parent.parent        # C:\MarketAutomationSystem\app
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
STATIC_ROOT_DIR = BASE_DIR / "staticfiles"
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)              # ensure folder exists
DB_PATH = DATA_DIR / "pos.db"

# ---- Core
SECRET_KEY = "dev-only-change-before-shipping"
DEBUG = True
ALLOWED_HOSTS = ["127.0.0.1", "localhost" , "testserver"]

# ---- Apps
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # project apps (can stay even if empty for now)
    "accounts",
    "catalog",
    "billing",
    "ledger",
    "debts",
    "io_ops",
    "printing",
    "backups_app",
    "core",
    "notifications",
]

# ---- Middleware / WSGI / URLs
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    'whitenoise.middleware.WhiteNoiseMiddleware',
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.RequireLoginMiddleware",
]
ROOT_URLCONF = "marketpos.urls"
WSGI_APPLICATION = "marketpos.wsgi.application"

# ---- Templates
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [TEMPLATES_DIR],
    "APP_DIRS": True,
    "OPTIONS": {
        "context_processors": [
            "django.template.context_processors.debug",
            "django.template.context_processors.request",
            "django.contrib.auth.context_processors.auth",
            "django.contrib.messages.context_processors.messages",
        ],
    },
}]

# ---- Language / Time
LANGUAGE_CODE = "ar"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# ---- Database (SQLite at app/data/pos.db)
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(DB_PATH),
    }
}

# ---- Static files
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / 'staticfiles'                                              # where 'collectstatic' will gather files for packaging
STATICFILES_DIRS = [STATIC_DIR]              # <-- correct: app/static
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# ---- Misc
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"

SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_SAVE_EVERY_REQUEST = False
