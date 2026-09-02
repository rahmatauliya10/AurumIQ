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
        quote_rate: Optional[Decimal | float | str] = None,
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
        q = Decimal(str(quote_rate)) if quote_rate is not None else None

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

    @staticmethod
    def create_xauusd_quote_event(
        instrument: str,
        provider: str,
        bid: Decimal | float | str,
        ask: Decimal | float | str,
        source_timestamp: datetime,
        received_timestamp: Optional[datetime] = None,
        sequence_number: Optional[int] = None,
    ) -> LiveQuoteEvent:
        """
        Construct a strict, validated LiveQuoteEvent for active XAUUSD pipeline.
        Strict Invariants:
          - Rejects naive source_timestamp and received_timestamp (never silently coerces).
          - Requires canonical XAUUSD instrument.
          - Rejects non-positive prices and inverted spreads (ask < bid).
        """
        if not instrument or instrument.strip().upper() not in ("XAUUSD", "XAU/USD"):
            raise ValueError(f"Active XAUUSD quote adapter rejects non-XAUUSD instrument: {instrument}")
        norm_inst = "XAUUSD"

        if source_timestamp is None or source_timestamp.tzinfo is None or source_timestamp.tzinfo.utcoffset(source_timestamp) is None:
            raise ValueError("Active XAUUSD quote source_timestamp must be timezone-aware; naive datetime rejected.")

        if received_timestamp is not None:
            if received_timestamp.tzinfo is None or received_timestamp.tzinfo.utcoffset(received_timestamp) is None:
                raise ValueError("Active XAUUSD quote received_timestamp must be timezone-aware; naive datetime rejected.")
            rec_ts = received_timestamp.astimezone(timezone.utc)
        else:
            rec_ts = datetime.now(timezone.utc)

        src_ts = source_timestamp.astimezone(timezone.utc)

        bid_dec = Decimal(str(bid))
        ask_dec = Decimal(str(ask))

        if bid_dec <= 0 or ask_dec <= 0:
            raise ValueError(f"Invalid non-positive quote prices: bid={bid_dec}, ask={ask_dec}")
        if bid_dec > ask_dec:
            raise ValueError(f"Crossed market quote: bid={bid_dec} > ask={ask_dec}")

        event_str = f"QUOTE_XAUUSD_{provider}_{src_ts.isoformat()}_{sequence_number}_{bid_dec}_{ask_dec}"
        event_id = hashlib.sha256(event_str.encode("utf-8")).hexdigest()[:24]

        return LiveQuoteEvent(
            event_id=event_id,
            instrument=norm_inst,
            provider=provider,
            bid=bid_dec,
            ask=ask_dec,
            source_timestamp=src_ts,
            received_timestamp=rec_ts,
            sequence_number=sequence_number,
        )

    @staticmethod
    def create_xauusd_candle_closed_event(
        instrument: str,
        timeframe: str,
        timestamp_open: datetime,
        timestamp_close: datetime,
        open_price: Decimal | float | str,
        high_price: Decimal | float | str,
        low_price: Decimal | float | str,
        close_price: Decimal | float | str,
        volume: Decimal | float | str = Decimal("0"),
        source: str = "primary_xauusd",
        sequence_number: Optional[int] = None,
        is_closed: bool = True,
    ) -> CandleClosedEvent:
        """
        Construct a strict, validated CandleClosedEvent for active XAUUSD pipeline.
        Strict Invariants:
          - Rejects naive timestamp_open and timestamp_close (never silently coerces).
          - Requires canonical XAUUSD instrument.
          - Rejects timeframes other than 15m for decision triggers.
          - Requires is_closed=True.
        """
        if not instrument or instrument.strip().upper() not in ("XAUUSD", "XAU/USD"):
            raise ValueError(f"Active XAUUSD candle adapter rejects non-XAUUSD instrument: {instrument}")
        norm_inst = "XAUUSD"

        if timestamp_open is None or timestamp_open.tzinfo is None or timestamp_open.tzinfo.utcoffset(timestamp_open) is None:
            raise ValueError("Active XAUUSD candle timestamp_open must be timezone-aware; naive datetime rejected.")
        if timestamp_close is None or timestamp_close.tzinfo is None or timestamp_close.tzinfo.utcoffset(timestamp_close) is None:
            raise ValueError("Active XAUUSD candle timestamp_close must be timezone-aware; naive datetime rejected.")

        if timeframe != "15m":
            raise ValueError(f"Active XAUUSD closed-candle decision pipeline requires 15m timeframe trigger, got: {timeframe}")

        if not is_closed:
            raise ValueError("Active XAUUSD decision pipeline requires closed candle (is_closed=True).")

        ts_o = timestamp_open.astimezone(timezone.utc)
        ts_c = timestamp_close.astimezone(timezone.utc)

        o = Decimal(str(open_price))
        h = Decimal(str(high_price))
        l = Decimal(str(low_price))
        c = Decimal(str(close_price))
        v = Decimal(str(volume))

        if o <= 0 or h <= 0 or l <= 0 or c <= 0:
            raise ValueError(f"Invalid non-positive candle prices: O={o}, H={h}, L={l}, C={c}")
        if h < l or h < o or h < c or l > o or l > c:
            raise ValueError(f"Invalid candle geometry: O={o}, H={h}, L={l}, C={c}")

        event_str = f"CANDLE_XAUUSD_{timeframe}_{ts_c.isoformat()}_{source}_{c}_{is_closed}"
        event_id = hashlib.sha256(event_str.encode("utf-8")).hexdigest()[:24]

        return CandleClosedEvent(
            event_id=event_id,
            instrument=norm_inst,
            timeframe=timeframe,
            timestamp_open=ts_o,
            timestamp_close=ts_c,
            open=o,
            high=h,
            low=l,
            close=c,
            volume=v,
            quote_rate=None,
            source=source,
            sequence_number=sequence_number,
            is_closed=is_closed,
        )
