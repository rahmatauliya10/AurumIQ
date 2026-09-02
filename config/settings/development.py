"""Development settings."""
import os
from .base import *  # noqa: F403

DEBUG = True

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "web", "0.0.0.0"]

# Ensure logging outputs readable console logs in dev
LOGGING["handlers"]["console"]["formatter"] = "verbose"  # noqa: F405

if "DATABASE_URL" not in os.environ:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",  # noqa: F405
        }
    }

