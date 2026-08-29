"""Abstract Base Class and Dataclasses for Market Data Providers."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, AsyncIterator, Optional, Tuple, Dict


@dataclass(frozen=True)
class RawCandle:
    """Immutable raw candlestick received from a data provider."""
    symbol: str
    timeframe: str
    timestamp_open: datetime
    timestamp_close: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    is_closed: bool
    source: str


@dataclass(frozen=True)
class ProviderHealth:
    """Point-in-time health state of a provider."""
    provider_id: str
    status: str  # HEALTHY, DEGRADED, UNHEALTHY, QUARANTINED, UNKNOWN
    latency_ms: Optional[int]
    checked_at: datetime
    error_message: str = ""


@dataclass(frozen=True)
class TickerSnapshot:
    """Instantaneous ticker snapshot for spread & boundary monitoring."""
    symbol: str
    bid: Decimal
    ask: Decimal
    last: Decimal
    timestamp: datetime


class MarketDataProvider(ABC):
    """Abstract interface for all exchange and market reference providers."""

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Unique lowercase provider code (e.g. 'binance', 'okx', 'gold_reference')."""
        ...

    @abstractmethod
    def fetch_candles(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[RawCandle]:
        """Fetch historical/recent closed candles within [start, end] window."""
        ...

    @abstractmethod
    def health_check(self) -> ProviderHealth:
        """Probe provider API endpoint and return health diagnostic."""
        ...

    def check_symbol_status(self, symbol: str) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Verify real-time symbol listing status on exchange (e.g. TRADING vs HALTED/BREAK).
        Returns (is_active_and_tradable, status_string, metadata_dict).
        """
        return True, "ACTIVE", {}

    def fetch_ticker(self, symbol: str) -> Optional[TickerSnapshot]:
        """Optional ticker polling for real-time spread analysis."""
        return None

    def map_timeframe(self, timeframe: str) -> str:
        """Map standard internal timeframe (1m, 5m, 15m, 1h, 4h, 1d) to exchange interval."""
        return timeframe
