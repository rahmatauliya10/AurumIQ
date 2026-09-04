"""Base Django settings for XAUT Signal Intelligence."""
from pathlib import Path
import environ
import structlog

# Base directory
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Environ setup
env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_SECRET_KEY=(str, "insecure-secret-key-for-scaffolding-override-in-env"),
    DJANGO_ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1", "web"]),
    DATABASE_URL=(str, "postgres://xaut_user:xaut_password@localhost:5432/xaut_db"),
    REDIS_URL=(str, "redis://localhost:6379/0"),
    CELERY_BROKER_URL=(str, "redis://localhost:6379/1"),
    CELERY_RESULT_BACKEND=(str, "redis://localhost:6379/2"),
    ENGINE_VERSION=(str, "1.0.0"),
    FEATURE_VERSION=(str, "feat-v1"),
    ACTIVE_CONFIG_VERSION=(str, "cfg-2026-001"),
    LOG_LEVEL=(str, "INFO"),
    LOG_FORMAT=(str, "console"),
)

# Read .env if present
env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(str(env_file))

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS")

# Application definition
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "django_celery_beat",
    "django_celery_results",
]

LOCAL_APPS = [
    "apps.accounts.apps.AccountsConfig",
    "apps.instruments.apps.InstrumentsConfig",
    "apps.market_data.apps.MarketDataConfig",
    "apps.analysis.apps.AnalysisConfig",
    "apps.signals.apps.SignalsConfig",
    "apps.backtests.apps.BacktestsConfig",
    "apps.live_monitor.apps.LiveMonitorConfig",
    "apps.alerts.apps.AlertsConfig",
    "apps.dashboard.apps.DashboardConfig",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Database
DATABASES = {
    "default": env.db("DATABASE_URL", default="postgres://xaut_user:xaut_password@localhost:5432/xaut_db"),
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Internationalization
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Django REST Framework
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
}

# Celery Configuration
CELERY_BROKER_URL = env("CELERY_BROKER_URL")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "UTC"
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60  # 30 minutes

# 5 Named Celery Queues
CELERY_TASK_QUEUES = {
    "market_data": {
        "exchange": "market_data",
        "routing_key": "market_data.#",
    },
    "analysis": {
        "exchange": "analysis",
        "routing_key": "analysis.#",
    },
    "backtest": {
        "exchange": "backtest",
        "routing_key": "backtest.#",
    },
    "machine_learning": {
        "exchange": "machine_learning",
        "routing_key": "machine_learning.#",
    },
    "maintenance": {
        "exchange": "maintenance",
        "routing_key": "maintenance.#",
    },
    "alerts": {
        "exchange": "alerts",
        "routing_key": "alerts.#",
    },
}

CELERY_TASK_DEFAULT_QUEUE = "maintenance"

# Redis Cache
REDIS_URL = env("REDIS_URL")
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

# Application Constants & Versions
ENGINE_VERSION = env("ENGINE_VERSION")
FEATURE_VERSION = env("FEATURE_VERSION")
ACTIVE_CONFIG_VERSION = env("ACTIVE_CONFIG_VERSION")
GOLD_REFERENCE_URL = env("GOLD_REFERENCE_URL", default=None)

# Structured Logging Configuration
LOG_LEVEL = env("LOG_LEVEL")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {process:d} {thread:d} {message}",
            "style": "{",
        },
        "json": {
            "()": "structlog.stdlib.ProcessorFormatter",
            "processor": structlog.processors.JSONRenderer(),
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose" if env("LOG_FORMAT") == "console" else "json",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": LOG_LEVEL,
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "engine": {
            "handlers": ["console"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
        "apps": {
            "handlers": ["console"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
    },
}

# Authentication URLs
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/dashboard/"
LOGOUT_REDIRECT_URL = "/accounts/login/"

# ============================================================
# XAUUSD Live Monitor Configuration (Fail-Closed)
# ============================================================
# These MUST be explicitly configured in environment.
# Missing configuration -> NOT_CONFIGURED / fail-closed.
# .env.example contains example values only.

# Redis quote cache TTL (seconds) — no default
_xauusd_ttl_raw = env("XAUUSD_LIVE_QUOTE_TTL_SECONDS", default=None)
XAUUSD_LIVE_QUOTE_TTL_SECONDS = int(_xauusd_ttl_raw) if _xauusd_ttl_raw is not None else None

# Quote staleness threshold (seconds) — no default
_xauusd_stale_raw = env("XAUUSD_QUOTE_STALE_SECONDS", default=None)
XAUUSD_QUOTE_STALE_SECONDS = int(_xauusd_stale_raw) if _xauusd_stale_raw is not None else None

# Future-skew rejection threshold (seconds) — no default
_xauusd_skew_raw = env("XAUUSD_QUOTE_FUTURE_SKEW_SECONDS", default=None)
XAUUSD_QUOTE_FUTURE_SKEW_SECONDS = int(_xauusd_skew_raw) if _xauusd_skew_raw is not None else None

# Alert transport configuration — NOT_CONFIGURED by default
ALERT_WEBHOOK_URL = env("ALERT_WEBHOOK_URL", default=None)
ALERT_TELEGRAM_BOT_TOKEN = env("ALERT_TELEGRAM_BOT_TOKEN", default=None)
ALERT_TELEGRAM_CHAT_ID = env("ALERT_TELEGRAM_CHAT_ID", default=None)

# ============================================================
# XAUUSD Empirical Friction Execution Target Configuration
# ============================================================
XAUUSD_EXECUTION_VENUE = env("XAUUSD_EXECUTION_VENUE", default="EXNESS")
XAUUSD_EXECUTION_ACCOUNT_TIER = env("XAUUSD_EXECUTION_ACCOUNT_TIER", default="STANDARD")
XAUUSD_EXECUTION_LEGAL_ENTITY_CODE = env("XAUUSD_EXECUTION_LEGAL_ENTITY_CODE", default=None)

