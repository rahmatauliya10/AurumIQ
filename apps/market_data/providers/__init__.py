"""Market data provider adapters package."""
from .base import MarketDataProvider, RawCandle, ProviderHealth, TickerSnapshot
from .binance import BinanceProvider
from .okx import OKXProvider
from .gold_reference import GoldReferenceProvider
from .usdt_usd import UsdtUsdRateProvider
from .twelve_data import TwelveDataProvider
from .registry import (
    ProviderRegistry,
    CANONICAL_SOURCE_ALIASES,
    normalize_canonical_source,
    get_canonical_source_aliases,
)

__all__ = [
    "MarketDataProvider",
    "RawCandle",
    "ProviderHealth",
    "TickerSnapshot",
    "BinanceProvider",
    "OKXProvider",
    "GoldReferenceProvider",
    "UsdtUsdRateProvider",
    "TwelveDataProvider",
    "ProviderRegistry",
    "CANONICAL_SOURCE_ALIASES",
    "normalize_canonical_source",
    "get_canonical_source_aliases",
]
