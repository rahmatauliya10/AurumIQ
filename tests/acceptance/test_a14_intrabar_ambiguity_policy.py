"""
Acceptance Test A14: Intrabar Ambiguity Policy & Conservative SL First Fallback.
Verifies that when a single candle touches both TP and SL, the engine applies
the selected policy and safely falls back to CONSERVATIVE_SL_FIRST when lower-TF data is missing.
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest

from engine.core.types import (
    BarrierHitType,
    CandleData,
    IntrabarPolicy,
)
from engine.risk.intrabar import IntrabarResolver


@pytest.mark.acceptance
def test_a14_intrabar_ambiguity_policy():
    resolver = IntrabarResolver()

    base_time = datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc)
    close_time = base_time + timedelta(minutes=15)

    # Parent 15m candle: Low = 2490 (SL is 2495), High = 2530 (TP is 2520) -> Ambiguous
    parent_candle = CandleData(
        timestamp_open=base_time,
        timestamp_close=close_time,
        open=Decimal("2505.00"),
        high=Decimal("2530.00"),
        low=Decimal("2490.00"),
        close=Decimal("2515.00"),
        volume=Decimal("500.00"),
        is_closed=True,
    )

    tp = Decimal("2520.00")
    sl = Decimal("2495.00")

    # 1. Policy: CONSERVATIVE_SL_FIRST
    res_sl = resolver.resolve(
        parent_candle=parent_candle,
        tp_price=tp,
        sl_price=sl,
        policy=IntrabarPolicy.CONSERVATIVE_SL_FIRST,
    )
    assert res_sl.barrier_hit == BarrierHitType.SL_FIRST
    assert res_sl.exit_price == sl
    assert res_sl.policy_applied == IntrabarPolicy.CONSERVATIVE_SL_FIRST

    # 2. Policy: WORST_CASE (SL + adverse gap penalty)
    res_wc = resolver.resolve(
        parent_candle=parent_candle,
        tp_price=tp,
        sl_price=sl,
        policy=IntrabarPolicy.WORST_CASE,
        worst_case_adverse_gap=Decimal("5.00"),
    )
    assert res_wc.barrier_hit == BarrierHitType.SL_FIRST
    assert res_wc.exit_price == Decimal("2490.00")  # 2495 - 5
    assert res_wc.policy_applied == IntrabarPolicy.WORST_CASE

    # 3. Policy: SKIP_AMBIGUOUS
    res_skip = resolver.resolve(
        parent_candle=parent_candle,
        tp_price=tp,
        sl_price=sl,
        policy=IntrabarPolicy.SKIP_AMBIGUOUS,
    )
    assert res_skip.barrier_hit == BarrierHitType.SKIPPED
    assert res_skip.policy_applied == IntrabarPolicy.SKIP_AMBIGUOUS

    # 4. Policy: LOWER_TIMEFRAME_REPLAY with missing lower-TF data -> Falls back to CONSERVATIVE_SL_FIRST
    res_missing_fallback = resolver.resolve(
        parent_candle=parent_candle,
        tp_price=tp,
        sl_price=sl,
        lower_tf_candles_1m=None,
        lower_tf_candles_5m=None,
        policy=IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
    )
    assert res_missing_fallback.barrier_hit == BarrierHitType.SL_FIRST
    assert res_missing_fallback.exit_price == sl
    assert res_missing_fallback.policy_applied == IntrabarPolicy.CONSERVATIVE_SL_FIRST
