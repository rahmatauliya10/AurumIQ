"""App configuration for market data domain."""
from django.apps import AppConfig


class MarketDataConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.market_data"
    verbose_name = "Market Data Engine"
