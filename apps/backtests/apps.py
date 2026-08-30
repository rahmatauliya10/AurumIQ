"""AppConfig for backtests app (Phase 6)."""
from django.apps import AppConfig


class BacktestsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.backtests"
    verbose_name = "Backtest Lab & Robustness Validation"
