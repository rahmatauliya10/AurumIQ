"""Public Market Data Adapter (P7-C7).

Converts raw public exchange/feed data into typed LiveQuoteEvent and CandleClosedEvent.
Strictly non-executing: Zero trading endpoints, order execution, or account API capabilities.
"""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional
import hashlib
import structlog

from apps.live_monitor.types import CandleClosedEvent, LiveQuoteEvent

logger = structlog.get_logger(__name__)


class PublicMarketDataAdapter:
    """
    Adapter converting canonical public market data from AurumIQ providers
    into typed Phase 7 live monitoring events.
    """

    @staticmethod
    def create_quote_event(
        instrument: str,
        provider: str,
        bid: Decimal | float | str,
        ask: Decimal | float | str,
        source_timestamp: datetime,
        received_timestamp: Optional[datetime] = None,
        sequence_number: Optional[int] = None,
    ) -> LiveQuoteEvent:
        """Construct a validated LiveQuoteEvent from public market quote."""
        bid_dec = Decimal(str(bid))
        ask_dec = Decimal(str(ask))

        if bid_dec <= 0 or ask_dec <= 0:
            raise ValueError(f"Invalid non-positive quote prices: bid={bid_dec}, ask={ask_dec}")
        if bid_dec > ask_dec:
            raise ValueError(f"Crossed market quote: bid={bid_dec} > ask={ask_dec}")

        src_ts = (
            source_timestamp.astimezone(timezone.utc)
            if source_timestamp.tzinfo
            else source_timestamp.replace(tzinfo=timezone.utc)
        )
        rec_ts = (
            received_timestamp.astimezone(timezone.utc)
            if received_timestamp and received_timestamp.tzinfo
            else (
                received_timestamp.replace(tzinfo=timezone.utc)
                if received_timestamp
                else datetime.now(timezone.utc)
            )
        )

        event_str = f"QUOTE_{instrument}_{provider}_{src_ts.isoformat()}_{sequence_number}_{bid_dec}_{ask_dec}"
        event_id = hashlib.sha256(event_str.encode("utf-8")).hexdigest()[:24]

        return LiveQuoteEvent(
            event_id=event_id,
            instrument=instrument,
            provider=provider,
            bid=bid_dec,
            ask=ask_dec,
            source_timestamp=src_ts,
            received_timestamp=rec_ts,
            sequence_number=sequence_number,
        )

    @staticmethod
    def create_candle_closed_event(
        instrument: str,
        timeframe: str,
        timestamp_open: datetime,
        timestamp_close: datetime,
        open_price: Decimal | float | str,
        high_price: Decimal | float | str,
        low_price: Decimal | float | str,
        close_price: Decimal | float | str,
        volume: Decimal | float | str = Decimal("0"),
        quote_rate: Decimal | float | str = Decimal("1.0"),
        source: str = "binance",
        sequence_number: Optional[int] = None,
        is_closed: bool = True,
    ) -> CandleClosedEvent:
        """Construct a validated CandleClosedEvent from public candle ingestion."""
        o = Decimal(str(open_price))
        h = Decimal(str(high_price))
        l = Decimal(str(low_price))
        c = Decimal(str(close_price))
        v = Decimal(str(volume))
        q = Decimal(str(quote_rate))

        ts_o = (
            timestamp_open.astimezone(timezone.utc)
            if timestamp_open.tzinfo
            else timestamp_open.replace(tzinfo=timezone.utc)
        )
        ts_c = (
            timestamp_close.astimezone(timezone.utc)
            if timestamp_close.tzinfo
            else timestamp_close.replace(tzinfo=timezone.utc)
        )

        event_str = f"CANDLE_{instrument}_{timeframe}_{ts_c.isoformat()}_{source}_{c}_{is_closed}"
        event_id = hashlib.sha256(event_str.encode("utf-8")).hexdigest()[:24]

        return CandleClosedEvent(
            event_id=event_id,
            instrument=instrument,
            timeframe=timeframe,
            timestamp_open=ts_o,
            timestamp_close=ts_c,
            open=o,
            high=h,
            low=l,
            close=c,
            volume=v,
            quote_rate=q,
            source=source,
            sequence_number=sequence_number,
            is_closed=is_closed,
        )
