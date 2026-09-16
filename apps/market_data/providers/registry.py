"""Central Registry for Multi-Exchange Providers and Fallback Management."""
from typing import Dict
import structlog
from .base import MarketDataProvider
from .xauusd_secondary import SecondaryXauUsdSpotProvider
from .twelve_data import TwelveDataProvider

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

    def unregister(self, provider_id: str) -> None:
        """Unregister a provider instance if present."""
        self._providers.pop(provider_id.lower(), None)

    def all_providers(self) -> list[MarketDataProvider]:
        """Return list of all registered provider instances."""
        return list(self._providers.values())


# Global registry singleton with current active XAUUSD default providers.
# Legacy providers (BinanceProvider, OKXProvider, GoldReferenceProvider, UsdtUsdRateProvider)
# are preserved for historical compatibility and must be registered explicitly in test/audit scope.
registry = ProviderRegistry()
registry.register(TwelveDataProvider())
registry.register(SecondaryXauUsdSpotProvider())


CANONICAL_SOURCE_ALIASES: Dict[str, str] = {
    "twelve_data": "twelve_data_xauusd",
    "twelve_data_xauusd": "twelve_data_xauusd",
}


def normalize_canonical_source(source: str) -> str:
    """
    Resolve source string or alias to authoritative canonical provider identity.
    Preserves strict source governance without requiring database row mutations.
    """
    s = str(source or "").strip().lower()
    return CANONICAL_SOURCE_ALIASES.get(s, s)


def get_canonical_source_aliases(provider_id: str) -> list[str]:
    """
    Return all valid source string aliases that resolve to the given canonical provider.
    Ensures legacy aliases (e.g. 'twelve_data') match authoritative provider (e.g. 'twelve_data_xauusd').
    """
    pid = str(provider_id or "").strip().lower()
    canonical = normalize_canonical_source(pid)
    aliases = [k for k, v in CANONICAL_SOURCE_ALIASES.items() if v == canonical]
    if pid not in aliases:
        aliases.append(pid)
    return aliases
