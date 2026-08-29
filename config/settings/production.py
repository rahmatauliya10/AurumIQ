"""Production settings. Strictly enforces security."""
import os
from .base import *  # noqa: F403

DEBUG = False

# Ensure secret key is provided and not default
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY or "insecure" in SECRET_KEY:
    raise ValueError("DJANGO_SECRET_KEY must be securely configured in production.")

ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",")
if not ALLOWED_HOSTS or ALLOWED_HOSTS == [""]:
    raise ValueError("DJANGO_ALLOWED_HOSTS must be explicitly defined in production.")

# Security headers & cookies
SECURE_SSL_REDIRECT = os.environ.get("SECURE_SSL_REDIRECT", "True").lower() == "true"
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# JSON structured logging in production
LOGGING["handlers"]["console"]["formatter"] = "json"  # noqa: F405
