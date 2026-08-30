"""Central Registry for Multi-Exchange Providers and Fallback Management."""
from typing import Optional, Dict
import structlog
from .base import MarketDataProvider
from .binance import BinanceProvider
from .okx import OKXProvider
from .gold_reference import GoldReferenceProvider
from .usdt_usd import UsdtUsdRateProvider
from .xauusd_spot import XauUsdSpotProvider
from .xauusd_secondary import SecondaryXauUsdSpotProvider

logger = structlog.get_logger(__name__)


class ProviderRegistry:
    """Registry managing instantiated data providers with fallback resolution."""

    def __init__(self):
        self._providers: Dict[str, MarketDataProvider] = {}

    def register(self, provider: MarketDataProvider) -> None:
        """Register a provider instance."""
        self._providers[provider.provider_id.lower()] = provider
        logger.info("provider_registered", provider_id=provider.provider_id)

    def get(self, provider_id: str) -> MarketDataProvider:
        """Retrieve a registered provider by ID."""
        pid = provider_id.lower()
        if pid not in self._providers:
            raise KeyError(f"Market data provider '{provider_id}' is not registered.")
        return self._providers[pid]

    def has(self, provider_id: str) -> bool:
        """Check if a provider ID is registered."""
        return provider_id.lower() in self._providers

    def all_providers(self) -> list[MarketDataProvider]:
        """Return list of all registered provider instances."""
        return list(self._providers.values())


import os


def get_configured_gold_reference_url() -> Optional[str]:
    """Resolve canonical gold reference URL from Django settings or environment."""
    try:
        from django.conf import settings
        return getattr(settings, "GOLD_REFERENCE_URL", os.environ.get("GOLD_REFERENCE_URL"))
    except Exception:
        return os.environ.get("GOLD_REFERENCE_URL")


# Global registry singleton with default providers
registry = ProviderRegistry()
registry.register(BinanceProvider())
registry.register(OKXProvider())
registry.register(GoldReferenceProvider(canonical_url=get_configured_gold_reference_url()))
registry.register(UsdtUsdRateProvider())
registry.register(XauUsdSpotProvider())
registry.register(SecondaryXauUsdSpotProvider())
