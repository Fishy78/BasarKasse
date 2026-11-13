import os
from pathlib import Path
from urllib.parse import unquote, urlparse

from django.core.exceptions import ImproperlyConfigured


BASE_DIR = Path(__file__).resolve().parent.parent

# fail-closed: DEBUG und SECRET_KEY müssen in Production explizit gesetzt werden
_django_debug = os.environ.get("DJANGO_DEBUG", "").lower()
if _django_debug not in ("0", "1"):
    raise ImproperlyConfigured(
        "DJANGO_DEBUG muss explizit auf '0' (Production) oder '1' (Entwicklung) gesetzt sein. "
        "Kein Standardwert für Production-Sicherheit."
    )
DEBUG = _django_debug == "1"

_django_secret_key = os.environ.get("DJANGO_SECRET_KEY")
if not _django_secret_key:
    if DEBUG:
        SECRET_KEY = "local-development-key-change-me-local-dev-only"
    else:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY muss für Production gesetzt sein. "
            "Setze die Umgebungsvariable mit einem starken, random generierten Schlüssel."
        )
else:
    SECRET_KEY = _django_secret_key

# ALLOWED_HOSTS: In Production ohne explizite Konfiguration wildcard erlauben (vertraue dem Reverse-Proxy)
# In Entwicklung auf localhost beschränken. Mit DJANGO_ALLOWED_HOSTS kann explizit konfiguriert werden.
_allowed_hosts_env = os.environ.get("DJANGO_ALLOWED_HOSTS")
if _allowed_hosts_env:
    ALLOWED_HOSTS = [h.strip() for h in _allowed_hosts_env.split(",")]
elif DEBUG:
    # Entwicklung: nur localhost/127.0.0.1
    ALLOWED_HOSTS = ["localhost", "127.0.0.1", "*.local"]
else:
    # Production ohne explizite Hosts: vertraue dem Reverse-Proxy (Host-Header-basiert)
    # HINWEIS: Nur sicher, wenn Reverse-Proxy Host-Header validiert!
    ALLOWED_HOSTS = ["*"]

if not DEBUG and SECRET_KEY == "local-development-key-change-me-local-dev-only":
    raise ImproperlyConfigured("DJANGO_SECRET_KEY muss in Production konfiguriert sein.")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "core.middleware.AdminDefaultLocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.middleware.CookieConsentMiddleware",
    "core.middleware.ConsentMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "core.middleware.CashierReceiptMiddleware",
    "core.admin_middleware.AdminDebugOnlyMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "basar.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
                "core.context_processors.global_design",
            ],
        },
    },
]
WSGI_APPLICATION = "basar.wsgi.application"
ASGI_APPLICATION = "basar.asgi.application"

database_url = os.environ.get("DATABASE_URL")
if database_url:
    parsed_database = urlparse(database_url)
    if parsed_database.scheme in {"postgres", "postgresql"}:
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": parsed_database.path.lstrip("/"),
                "USER": unquote(parsed_database.username or ""),
                "PASSWORD": unquote(parsed_database.password or ""),
                "HOST": parsed_database.hostname or "",
                "PORT": parsed_database.port or "",
                "CONN_MAX_AGE": 60,
            }
        }
    elif parsed_database.scheme == "sqlite" and DEBUG:
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": parsed_database.path or BASE_DIR / "db.sqlite3",
            }
        }
    else:
        raise ValueError("DATABASE_URL must use PostgreSQL or SQLite.")
else:
    if not DEBUG:
        raise ImproperlyConfigured("DATABASE_URL must be configured outside development.")
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_USER_MODEL = "core.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "core.password_validation.GlobalPasswordValidator"},
]
LANGUAGE_CODE = "de"
LANGUAGES = [
    ("de", "Deutsch"),
    ("en", "English"),
    ("fr", "Français"),
    ("es", "Español"),
    ("pt", "Português"),
    ("ja", "日本語"),
    ("zh-hans", "简体中文"),
    ("tr", "Türkçe"),
]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "Europe/Berlin"
USE_I18N = True
USE_TZ = True

# Automatische Test-Erkennung (wird gesetzt wenn manage.py test läuft)
import sys
TESTING = 'test' in sys.argv

STATIC_URL = "static/"
STATIC_ROOT = Path(os.environ.get("DJANGO_STATIC_ROOT", BASE_DIR / "staticfiles"))
MEDIA_URL = "media/"
MEDIA_ROOT = Path(os.environ.get("DJANGO_MEDIA_ROOT", BASE_DIR / "media"))

# Private Media: Settlements sind sensible Finanzdaten und dürfen nicht öffentlich zugänglich sein
PRIVATE_MEDIA_ROOT = Path(os.environ.get("DJANGO_PRIVATE_MEDIA_ROOT", BASE_DIR / "private_media"))
PRIVATE_MEDIA_URL = "private_media/"  # Wird nicht durch Django-Static bedient; nur über autorisierte Views

# Settlement-PDFs werden im privaten Verzeichnis gespeichert
SETTLEMENTS_STORAGE_PATH = "settlements/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "home"
EMAIL_BACKEND = os.environ.get(
    "DJANGO_EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend"
    if DEBUG
    else "django.core.mail.backends.smtp.EmailBackend",
)
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "noreply@basar.local")
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "1") == "1"
CSRF_TRUSTED_ORIGINS = [
    origin for origin in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if origin
]
# The application only accepts connections from the loopback-bound reverse proxy.
# Therefore NGINX's HTTPS indication can be trusted.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# Transport-Sicherung: HSTS nur bei HTTPS (Production)
if not DEBUG:
    SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = os.environ.get("SECURE_HSTS_INCLUDE_SUBDOMAINS", "1") == "1"
    SECURE_HSTS_PRELOAD = os.environ.get("SECURE_HSTS_PRELOAD", "1") == "1"

# PostgreSQL: TLS-Optionen für Datenbankverbindung
if database_url and "postgresql" in database_url:
    db_sslmode = os.environ.get("DATABASE_SSLMODE", "prefer" if not DEBUG else "allow")
    db_sslcert = os.environ.get("DATABASE_SSLCERT")
    db_sslkey = os.environ.get("DATABASE_SSLKEY")
    db_sslrootcert = os.environ.get("DATABASE_SSLROOTCERT")
    
    if DATABASES["default"]:
        DATABASES["default"]["OPTIONS"] = {}
        DATABASES["default"]["OPTIONS"]["sslmode"] = db_sslmode
        if db_sslcert:
            DATABASES["default"]["OPTIONS"]["sslcert"] = db_sslcert
        if db_sslkey:
            DATABASES["default"]["OPTIONS"]["sslkey"] = db_sslkey
        if db_sslrootcert:
            DATABASES["default"]["OPTIONS"]["sslrootcert"] = db_sslrootcert
