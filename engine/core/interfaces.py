"""Protocol interfaces for decoupled repositories. Strictly no Django ORM imports (R9)."""
from typing import Protocol, runtime_checkable
from datetime import datetime
from .types import CandleData


@runtime_checkable
class CandleRepository(Protocol):
    """Protocol for accessing historical candles in point-in-time isolation."""

    def get_first_bar_open_after(
        self, timestamp: datetime, timeframe: str
    ) -> CandleData | None:
        """Fetch the first candle whose open timestamp >= the specified timestamp."""
        ...

    def load_window(
        self, instrument: str, timeframe: str, end_at: datetime, bars: int
    ) -> list[CandleData]:
        """Load a causal window of candles strictly up to and including end_at."""
        ...

    def load_resolution_candles(
        self, start: datetime, end: datetime, timeframe: str = "1m"
    ) -> list[CandleData]:
        """Load lower-timeframe resolution candles for intrabar replay."""
        ...
