"""
Acceptance Test A19: No Same-Bar Fill & Causal Signal Execution Boundary.
Verifies that backtest execution CANNOT fill at the close price of the signal-generating candle.
Fill must occur at the open of a subsequent bar at or after (signal_generated_at + latency).
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest

from engine.core.types import (
    CandleData,
    EntryExecutionPolicy,
)
from engine.risk.execution import EntryExecutionModel


@pytest.mark.acceptance
def test_a19_no_same_bar_fill():
    model = EntryExecutionModel(latency_seconds=0.0, default_spread_pct=Decimal("0.02"), default_slippage_pct=Decimal("0.01"))

    # Signal generating candle: 10:00 - 10:15
    # Close price = 2500.00
    t0_open = datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc)
    t0_close = datetime(2026, 8, 29, 10, 15, tzinfo=timezone.utc)
    signal_candle = CandleData(
        timestamp_open=t0_open,
        timestamp_close=t0_close,
        open=Decimal("2495.00"),
        high=Decimal("2505.00"),
        low=Decimal("2492.00"),
        close=Decimal("2500.00"),
        volume=Decimal("200.00"),
        is_closed=True,
    )

    # Next candle: 10:15 - 10:30, Open = 2501.50
    t1_open = datetime(2026, 8, 29, 10, 15, tzinfo=timezone.utc)
    t1_close = datetime(2026, 8, 29, 10, 30, tzinfo=timezone.utc)
    next_candle = CandleData(
        timestamp_open=t1_open,
        timestamp_close=t1_close,
        open=Decimal("2501.50"),
        high=Decimal("2512.00"),
        low=Decimal("2501.00"),
        close=Decimal("2510.00"),
        volume=Decimal("250.00"),
        is_closed=True,
    )

    signal_ts = t0_close  # Signal knowable at 10:15:00

    fill_res = model.simulate_next_bar_open(
        signal_generated_at=signal_ts,
        candles=[signal_candle, next_candle],
        timeframe="15m",
        latency_seconds=0.0,
    )

    assert fill_res.is_filled is True
    # Fill CANNOT be at the signal candle close (2500.00)
    assert fill_res.fill_price != signal_candle.close
    # Fill timestamp must be strictly subsequent (t1_open = 10:15:00)
    assert fill_res.fill_timestamp == next_candle.timestamp_open
    assert fill_res.fill_timestamp >= signal_ts
    assert fill_res.policy == EntryExecutionPolicy.NEXT_BAR_OPEN
