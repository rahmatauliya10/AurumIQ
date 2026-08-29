"""Causal entry execution simulation with latency, spread, and slippage modeling (Phase 5)."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional, Sequence

from engine.core.types import (
    CandleData,
    EntryExecutionPolicy,
    FillResult,
    QuoteData,
)


class EntryExecutionModel:
    """
    Simulates point-in-time entry order execution avoiding same-bar and look-ahead bias.

    Strict Invariants:
      1. Earliest execution timestamp is strictly: signal_generated_at + latency_seconds (A19, A27).
      2. NEXT_BAR_OPEN: fill price and fill timestamp strictly belong to the subsequent bar (c.timestamp_open >= earliest_exec_ts).
      3. MARKET_AFTER_SIGNAL: uses actual first ASK quote >= earliest_exec_ts. ASK already includes spread (no double-count) (A25).
      4. LIMIT_ZONE: order activates strictly at earliest_exec_ts; pre-activation touches are ignored.
    """

    def __init__(
        self,
        latency_seconds: float = 2.0,
        default_spread_pct: Decimal = Decimal("0.02"),
        default_slippage_pct: Decimal = Decimal("0.01"),
    ):
        self.latency_seconds = latency_seconds
        self.default_spread_pct = default_spread_pct
        self.default_slippage_pct = default_slippage_pct

    def simulate_next_bar_open(
        self,
        signal_generated_at: datetime,
        candles: Sequence[CandleData],
        timeframe: str = "15m",
        latency_seconds: Optional[float] = None,
        spread_pct: Optional[Decimal] = None,
        slippage_pct: Optional[Decimal] = None,
    ) -> FillResult:
        """Simulate fill on the open of the first subsequent bar >= earliest_exec_ts (A19, A27)."""
        lat = latency_seconds if latency_seconds is not None else self.latency_seconds
        sig_ts_utc = signal_generated_at.astimezone(timezone.utc) if signal_generated_at.tzinfo else signal_generated_at.replace(tzinfo=timezone.utc)
        earliest_exec_ts = sig_ts_utc + timedelta(seconds=lat)

        eligible_bars = [
            c for c in candles
            if (c.timestamp_open.astimezone(timezone.utc) if c.timestamp_open.tzinfo else c.timestamp_open.replace(tzinfo=timezone.utc)) >= earliest_exec_ts
        ]

        if not eligible_bars:
            return FillResult(
                fill_price=Decimal("0"),
                fill_timestamp=sig_ts_utc,
                policy=EntryExecutionPolicy.NEXT_BAR_OPEN,
                latency_seconds=lat,
                spread_amount=Decimal("0"),
                slippage_amount=Decimal("0"),
                is_filled=False,
                reasons=("No eligible subsequent bar open found on or after earliest_exec_ts.",),
            )

        first_bar = eligible_bars[0]
        raw_fill = first_bar.open
        sp_pct = spread_pct if spread_pct is not None else self.default_spread_pct
        sl_pct = slippage_pct if slippage_pct is not None else self.default_slippage_pct

        spread_amount = (raw_fill * (sp_pct / Decimal("100"))).quantize(Decimal("0.01"))
        slippage_amount = (raw_fill * (sl_pct / Decimal("100"))).quantize(Decimal("0.01"))
        final_price = (raw_fill + spread_amount + slippage_amount).quantize(Decimal("0.01"))

        return FillResult(
            fill_price=final_price,
            fill_timestamp=first_bar.timestamp_open,
            policy=EntryExecutionPolicy.NEXT_BAR_OPEN,
            latency_seconds=lat,
            spread_amount=spread_amount,
            slippage_amount=slippage_amount,
            is_filled=True,
            reasons=(f"Filled at next bar open {first_bar.timestamp_open.isoformat()} with modeled spread and slippage.",),
        )

    def simulate_market_after_signal(
        self,
        signal_generated_at: datetime,
        quotes: Sequence[QuoteData],
        latency_seconds: Optional[float] = None,
        slippage_pct: Optional[Decimal] = None,
    ) -> FillResult:
        """
        Simulate market order execution at first available quote >= earliest_exec_ts (A25).
        Note: The actual ASK quote already contains exchange spread, so spread_amount is 0.0.
        """
        lat = latency_seconds if latency_seconds is not None else self.latency_seconds
        sig_ts_utc = signal_generated_at.astimezone(timezone.utc) if signal_generated_at.tzinfo else signal_generated_at.replace(tzinfo=timezone.utc)
        earliest_exec_ts = sig_ts_utc + timedelta(seconds=lat)

        eligible_quotes = [
            q for q in quotes
            if (q.timestamp.astimezone(timezone.utc) if q.timestamp.tzinfo else q.timestamp.replace(tzinfo=timezone.utc)) >= earliest_exec_ts
        ]

        if not eligible_quotes:
            return FillResult(
                fill_price=Decimal("0"),
                fill_timestamp=sig_ts_utc,
                policy=EntryExecutionPolicy.MARKET_AFTER_SIGNAL,
                latency_seconds=lat,
                spread_amount=Decimal("0"),
                slippage_amount=Decimal("0"),
                is_filled=False,
                reasons=("No market quotes available on or after earliest_exec_ts.",),
            )

        # Sort chronologically to get strictly first quote
        eligible_quotes.sort(key=lambda q: q.timestamp)
        first_quote = eligible_quotes[0]
        raw_fill = first_quote.ask

        sl_pct = slippage_pct if slippage_pct is not None else self.default_slippage_pct
        slippage_amount = (raw_fill * (sl_pct / Decimal("100"))).quantize(Decimal("0.01"))
        final_price = (raw_fill + slippage_amount).quantize(Decimal("0.01"))

        return FillResult(
            fill_price=final_price,
            fill_timestamp=first_quote.timestamp,
            policy=EntryExecutionPolicy.MARKET_AFTER_SIGNAL,
            latency_seconds=lat,
            spread_amount=Decimal("0.00"),  # Spread already embedded in ASK quote
            slippage_amount=slippage_amount,
            is_filled=True,
            reasons=(f"Filled at market ASK quote @ {first_quote.timestamp.isoformat()}.",),
        )

    def simulate_limit_zone(
        self,
        signal_generated_at: datetime,
        limit_price: Decimal,
        quotes: Optional[Sequence[QuoteData]] = None,
        lower_tf_candles: Optional[Sequence[CandleData]] = None,
        candles: Optional[Sequence[CandleData]] = None,
        latency_seconds: Optional[float] = None,
        slippage_pct: Optional[Decimal] = None,
    ) -> FillResult:
        """
        Simulate limit order fill occurring only after post-activation touches (P5-19, P5-27).
        """
        lat = latency_seconds if latency_seconds is not None else self.latency_seconds
        sig_ts_utc = signal_generated_at.astimezone(timezone.utc) if signal_generated_at.tzinfo else signal_generated_at.replace(tzinfo=timezone.utc)
        earliest_exec_ts = sig_ts_utc + timedelta(seconds=lat)

        # 1. Quote-level resolution
        if quotes is not None:
            eligible_quotes = [
                q for q in quotes
                if (q.timestamp.astimezone(timezone.utc) if q.timestamp.tzinfo else q.timestamp.replace(tzinfo=timezone.utc)) >= earliest_exec_ts
            ]
            eligible_quotes.sort(key=lambda q: q.timestamp)
            for q in eligible_quotes:
                if q.ask <= limit_price:
                    raw_fill = min(limit_price, q.ask)
                    sl_pct = slippage_pct if slippage_pct is not None else Decimal("0.00")
                    slippage_amount = (raw_fill * (sl_pct / Decimal("100"))).quantize(Decimal("0.01"))
                    final_price = (raw_fill + slippage_amount).quantize(Decimal("0.01"))
                    return FillResult(
                        fill_price=final_price,
                        fill_timestamp=q.timestamp,
                        policy=EntryExecutionPolicy.LIMIT_ZONE,
                        latency_seconds=lat,
                        spread_amount=Decimal("0.00"),
                        slippage_amount=slippage_amount,
                        is_filled=True,
                        reasons=(f"Limit filled via post-activation quote @ {q.timestamp.isoformat()}.",),
                    )

        # 2. Candle-level resolution
        candle_source = lower_tf_candles if lower_tf_candles is not None else candles
        if candle_source is not None:
            has_midbar = False
            eligible_bars = []
            for c in candle_source:
                c_open = c.timestamp_open.astimezone(timezone.utc) if c.timestamp_open.tzinfo else c.timestamp_open.replace(tzinfo=timezone.utc)
                c_close = c.timestamp_close.astimezone(timezone.utc) if c.timestamp_close.tzinfo else c.timestamp_close.replace(tzinfo=timezone.utc)
                if c_open < earliest_exec_ts < c_close:
                    has_midbar = True
                elif c_open >= earliest_exec_ts:
                    eligible_bars.append(c)

            eligible_bars.sort(key=lambda c: c.timestamp_open)
            for bar in eligible_bars:
                if bar.low <= limit_price:
                    return FillResult(
                        fill_price=limit_price,
                        fill_timestamp=bar.timestamp_open,
                        policy=EntryExecutionPolicy.LIMIT_ZONE,
                        latency_seconds=lat,
                        spread_amount=Decimal("0.00"),
                        slippage_amount=Decimal("0.00"),
                        is_filled=True,
                        reasons=(f"Limit touched on bar {bar.timestamp_open.isoformat()}.",),
                    )

            if has_midbar and not eligible_bars:
                return FillResult(
                    fill_price=Decimal("0"),
                    fill_timestamp=sig_ts_utc,
                    policy=EntryExecutionPolicy.LIMIT_ZONE,
                    latency_seconds=lat,
                    spread_amount=Decimal("0.00"),
                    slippage_amount=Decimal("0.00"),
                    is_filled=False,
                    reasons=("Cannot infer limit fill from mid-bar parent candle without intrabar timestamps.",),
                )

        return FillResult(
            fill_price=Decimal("0"),
            fill_timestamp=sig_ts_utc,
            policy=EntryExecutionPolicy.LIMIT_ZONE,
            latency_seconds=lat,
            spread_amount=Decimal("0.00"),
            slippage_amount=Decimal("0.00"),
            is_filled=False,
            reasons=("Limit price not touched after activation timestamp.",),
        )

