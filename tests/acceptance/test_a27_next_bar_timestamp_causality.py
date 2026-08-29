"""
Acceptance Test A27: Next-Bar Timestamp and Price Causality.
Verifies that NEXT_BAR_OPEN fill price and fill timestamp strictly belong to the same
first eligible bar whose timestamp_open >= (signal_generated_at + latency).
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
def test_a27_next_bar_timestamp_causality():
    model = EntryExecutionModel(latency_seconds=3.0, default_spread_pct=Decimal("0.02"), default_slippage_pct=Decimal("0.01"))

    # Signal timestamp at 10:15:00. Latency = 3s -> earliest_exec_ts = 10:15:03
    signal_ts = datetime(2026, 8, 29, 10, 15, 0, tzinfo=timezone.utc)

    # Bars sequence:
    # Bar 0: 10:00 - 10:15 (Signal candle)
    # Bar 1: 10:15 - 10:30, open = 2550.00, open_ts = 10:15:00
    # Earliest exec ts (10:15:03) falls inside Bar 1's interval [10:15, 10:30).
    # Since Bar 1 open is 10:15:00, if latency requires >= 10:15:03,
    # the next strictly executable bar open is Bar 2 (10:30:00).
    bar0 = CandleData(signal_ts - timedelta(minutes=15), signal_ts, Decimal("2545.00"), Decimal("2555.00"), Decimal("2540.00"), Decimal("2550.00"), Decimal("100"), True)
    bar1 = CandleData(signal_ts, signal_ts + timedelta(minutes=15), Decimal("2550.00"), Decimal("2560.00"), Decimal("2548.00"), Decimal("2558.00"), Decimal("120"), True)
    bar2 = CandleData(signal_ts + timedelta(minutes=15), signal_ts + timedelta(minutes=30), Decimal("2558.50"), Decimal("2570.00"), Decimal("2555.00"), Decimal("2568.00"), Decimal("150"), True)

    # Test with standard latency (latency=0s -> fills at bar1 open 10:15:00)
    res_immediate = model.simulate_next_bar_open(
        signal_generated_at=signal_ts,
        candles=[bar0, bar1, bar2],
        latency_seconds=0.0,
    )
    assert res_immediate.is_filled is True
    assert res_immediate.fill_timestamp == bar1.timestamp_open
    # Spread = 2550 * 0.0002 = 0.51, Slippage = 2550 * 0.0001 = 0.26 -> 2550.77
    assert res_immediate.fill_price == Decimal("2550.77")

    # Test with latency exceeding bar1 open (latency=5s -> earliest_ts = 10:15:05 -> fills at bar2 open 10:30:00)
    res_delayed = model.simulate_next_bar_open(
        signal_generated_at=signal_ts,
        candles=[bar0, bar1, bar2],
        latency_seconds=5.0,  # 5s latency skips bar1 open (10:15:00)
    )
    assert res_delayed.is_filled is True
    assert res_delayed.fill_timestamp == bar2.timestamp_open
    # Bar2 open = 2558.50. Spread = 0.51, Slippage = 0.26 -> 2559.27
    assert res_delayed.fill_price == Decimal("2559.27")
