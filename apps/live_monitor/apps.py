"""Django AppConfig for Live Monitor."""
from django.apps import AppConfig


class LiveMonitorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.live_monitor"
    verbose_name = "Live Monitor & Execution Readiness"
