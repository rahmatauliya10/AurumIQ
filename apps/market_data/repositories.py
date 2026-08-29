"""Django ORM implementation of pure engine CandleRepository Protocol."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from django.db.models import Q
import structlog
from engine.core.interfaces import CandleRepository
from engine.core.types import CandleData
from apps.market_data.models import MarketCandle, CandleQualityFlag
from apps.instruments.models import Instrument

logger = structlog.get_logger(__name__)


class DjangoCandleRepository(CandleRepository):
    """
    Decoupled repository adapter that bridges Django MarketCandle models to
    the pure framework-independent CandleData Protocol.
    """

    def __init__(self, source_filter: Optional[str] = None):
        self.source_filter = source_filter

    def _to_candle_data(self, candle: MarketCandle) -> CandleData:
        """Convert ORM model instance to immutable engine CandleData dataclass."""
        return CandleData(
            timestamp_open=candle.timestamp_open,
            timestamp_close=candle.timestamp_close,
            open=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            volume=candle.volume,
            is_closed=candle.is_closed,
            source_id=candle.source,
            quote_rate=candle.quote_rate,
            close_usd=candle.close_usd,
        )

    def get_first_bar_open_after(
        self, timestamp: datetime, timeframe: str, instrument_symbol: Optional[str] = None
    ) -> Optional[CandleData]:
        """Fetch the first candle whose open timestamp >= the specified timestamp."""
        qs = MarketCandle.objects.filter(
            timeframe=timeframe,
            timestamp_open__gte=timestamp,
            is_closed=True,
        ).exclude(data_quality_flag=CandleQualityFlag.QUARANTINED)

        if instrument_symbol:
            parts = instrument_symbol.split("/")
            if len(parts) == 2:
                qs = qs.filter(
                    instrument__base_asset__code=parts[0],
                    instrument__quote_asset__code=parts[1],
                )

        if self.source_filter:
            qs = qs.filter(source=self.source_filter)

        candle = qs.order_by("timestamp_open").first()
        return self._to_candle_data(candle) if candle else None

    def load_window(
        self, instrument: str, timeframe: str, end_at: datetime, bars: int
    ) -> list[CandleData]:
        """Load a causal window of candles strictly up to and including end_at."""
        parts = instrument.split("/")
        qs = MarketCandle.objects.filter(
            timeframe=timeframe,
            timestamp_open__lte=end_at,
            is_closed=True,
        ).exclude(data_quality_flag=CandleQualityFlag.QUARANTINED)

        if len(parts) == 2:
            qs = qs.filter(
                instrument__base_asset__code=parts[0],
                instrument__quote_asset__code=parts[1],
            )

        if self.source_filter:
            qs = qs.filter(source=self.source_filter)

        # Order descending to slice the most recent `bars`, then reverse for chronological ascending order
        orm_candles = list(qs.order_by("-timestamp_open")[:bars])
        orm_candles.reverse()

        return [self._to_candle_data(c) for c in orm_candles]

    def load_resolution_candles(
        self,
        start: datetime,
        end: datetime,
        timeframe: str = "1m",
        instrument_symbol: Optional[str] = None,
    ) -> list[CandleData]:
        """Load lower-timeframe resolution candles for intrabar replay."""
        qs = MarketCandle.objects.filter(
            timeframe=timeframe,
            timestamp_open__gte=start,
            timestamp_open__lte=end,
            is_closed=True,
        ).exclude(data_quality_flag=CandleQualityFlag.QUARANTINED)

        if instrument_symbol:
            parts = instrument_symbol.split("/")
            if len(parts) == 2:
                qs = qs.filter(
                    instrument__base_asset__code=parts[0],
                    instrument__quote_asset__code=parts[1],
                )

        if self.source_filter:
            qs = qs.filter(source=self.source_filter)

        orm_candles = qs.order_by("timestamp_open")
        return [self._to_candle_data(c) for c in orm_candles]
