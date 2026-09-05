"""Testing settings for pytest suite."""
from .base import *  # noqa: F403

DEBUG = False
IS_TESTING = True
SECRET_KEY = "test-secret-key-only-used-for-automated-pytest-suites"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-cache",
    }
}

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# Quiet logging during tests
LOGGING["root"]["level"] = "WARNING"  # noqa: F405
LOGGING["loggers"]["django"]["level"] = "WARNING"  # noqa: F405
LOGGING["loggers"]["engine"]["level"] = "WARNING"  # noqa: F405
LOGGING["loggers"]["apps"]["level"] = "WARNING"  # noqa: F405

# TEST_ONLY: Explicit XAUUSD live monitor configuration for test suite.
# These are NOT production defaults — production MUST configure via environment.
XAUUSD_LIVE_QUOTE_TTL_SECONDS = 60
XAUUSD_QUOTE_STALE_SECONDS = 45
XAUUSD_QUOTE_FUTURE_SKEW_SECONDS = 60

# TEST_ONLY: Target execution configuration for test suite
XAUUSD_EXECUTION_VENUE = "EXNESS"
XAUUSD_EXECUTION_ACCOUNT_TIER = "STANDARD"
XAUUSD_EXECUTION_LEGAL_ENTITY_CODE = "EXNESS_SC_LTD"

