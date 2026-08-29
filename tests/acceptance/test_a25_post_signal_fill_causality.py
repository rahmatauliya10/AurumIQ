"""
Acceptance Test A25: Post-Signal Market Quote Execution Causality.
Verifies that MARKET_AFTER_SIGNAL execution policy fills at the first available quote
occurring on or after (signal_generated_at + latency), uses the actual ASK quote,
and does NOT double-count the spread.
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest

from engine.core.types import (
    EntryExecutionPolicy,
    QuoteData,
)
from engine.risk.execution import EntryExecutionModel


@pytest.mark.acceptance
def test_a25_post_signal_fill_causality():
    model = EntryExecutionModel(latency_seconds=2.0, default_slippage_pct=Decimal("0.01"))

    signal_ts = datetime(2026, 8, 29, 10, 15, 0, tzinfo=timezone.utc)

    # Quotes stream:
    # Q0: 10:15:01 (Before latency boundary 10:15:02) -> Ineligible!
    # Q1: 10:15:02 (Exact boundary) -> First eligible quote
    # Q2: 10:15:03 -> Later quote
    q0 = QuoteData(signal_ts + timedelta(seconds=1), Decimal("2500.00"), Decimal("2500.20"))
    q1 = QuoteData(signal_ts + timedelta(seconds=2), Decimal("2500.50"), Decimal("2500.70"))
    q2 = QuoteData(signal_ts + timedelta(seconds=3), Decimal("2501.00"), Decimal("2501.20"))

    fill_res = model.simulate_market_after_signal(
        signal_generated_at=signal_ts,
        quotes=[q0, q1, q2],
        latency_seconds=2.0,
        slippage_pct=Decimal("0.01"),  # 0.01% slippage = 2500.70 * 0.0001 = 0.25
    )

    assert fill_res.is_filled is True
    # Must fill using q1 (10:15:02), not q0 (10:15:01)
    assert fill_res.fill_timestamp == q1.timestamp

    # Raw ASK is 2500.70. Slippage = 0.25 -> final = 2500.95
    # Spread amount must be 0.00 (ASK already includes bid-ask spread, avoiding double-count)
    assert fill_res.spread_amount == Decimal("0.00")
    assert fill_res.slippage_amount == Decimal("0.25")
    assert fill_res.fill_price == Decimal("2500.95")
    assert fill_res.policy == EntryExecutionPolicy.MARKET_AFTER_SIGNAL
