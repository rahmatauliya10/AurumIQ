"""Core dataclasses and value objects for the pure Python engine."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class CandleData:
    """Immutable OHLCV candle object strictly decoupled from Django."""
    timestamp_open: datetime
    timestamp_close: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    is_closed: bool
    source_id: str = "default"
    quote_rate: Decimal = Decimal("1.0")
    close_usd: Optional[Decimal] = None
