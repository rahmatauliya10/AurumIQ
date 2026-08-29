"""
Acceptance Test A22: 15m Intrabar Ambiguity 1m/5m Resolution Replay.
Verifies that when a 15m candle is ambiguous (High >= TP and Low <= SL),
chronological lower-timeframe 1m/5m replay correctly identifies whether TP or SL was hit first.
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
def test_a22_15m_ambiguity_lower_tf_replay():
    resolver = IntrabarResolver()

    t_15m_open = datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc)
    t_15m_close = datetime(2026, 8, 29, 10, 15, tzinfo=timezone.utc)

    parent_15m = CandleData(
        timestamp_open=t_15m_open,
        timestamp_close=t_15m_close,
        open=Decimal("2500.00"),
        high=Decimal("2525.00"),  # Touches TP=2520
        low=Decimal("2485.00"),   # Touches SL=2490
        close=Decimal("2510.00"),
        volume=Decimal("1000.00"),
        is_closed=True,
    )

    tp = Decimal("2520.00")
    sl = Decimal("2490.00")

    # Case 1: 1m Chronology where TP is hit at minute 4 (10:04), and SL is hit at minute 11 (10:11)
    candles_1m_tp_first = []
    for i in range(15):
        m_open = t_15m_open + timedelta(minutes=i)
        m_close = t_15m_open + timedelta(minutes=i + 1)
        if i == 3:  # Minute 4: Spikes to 2522 (touches TP)
            c = CandleData(m_open, m_close, Decimal("2505.00"), Decimal("2522.00"), Decimal("2504.00"), Decimal("2520.00"), Decimal("50.0"), True)
        elif i == 10:  # Minute 11: Drops to 2486 (touches SL)
            c = CandleData(m_open, m_close, Decimal("2510.00"), Decimal("2512.00"), Decimal("2486.00"), Decimal("2488.00"), Decimal("60.0"), True)
        else:
            c = CandleData(m_open, m_close, Decimal("2500.00"), Decimal("2505.00"), Decimal("2498.00"), Decimal("2502.00"), Decimal("20.0"), True)
        candles_1m_tp_first.append(c)

    res1 = resolver.resolve(
        parent_candle=parent_15m,
        tp_price=tp,
        sl_price=sl,
        lower_tf_candles_1m=candles_1m_tp_first,
        policy=IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
    )

    assert res1.barrier_hit == BarrierHitType.TP_FIRST
    assert res1.exit_price == tp
    assert res1.exit_timestamp == t_15m_open + timedelta(minutes=4)

    # Case 2: 1m Chronology where SL is hit at minute 2 (10:02), and TP is hit at minute 9 (10:09)
    candles_1m_sl_first = []
    for i in range(15):
        m_open = t_15m_open + timedelta(minutes=i)
        m_close = t_15m_open + timedelta(minutes=i + 1)
        if i == 1:  # Minute 2: Dips to 2488 (touches SL)
            c = CandleData(m_open, m_close, Decimal("2500.00"), Decimal("2501.00"), Decimal("2488.00"), Decimal("2492.00"), Decimal("80.0"), True)
        elif i == 8:  # Minute 9: Surges to 2524 (touches TP)
            c = CandleData(m_open, m_close, Decimal("2512.00"), Decimal("2524.00"), Decimal("2510.00"), Decimal("2520.00"), Decimal("90.0"), True)
        else:
            c = CandleData(m_open, m_close, Decimal("2498.00"), Decimal("2504.00"), Decimal("2496.00"), Decimal("2500.00"), Decimal("20.0"), True)
        candles_1m_sl_first.append(c)

    res2 = resolver.resolve(
        parent_candle=parent_15m,
        tp_price=tp,
        sl_price=sl,
        lower_tf_candles_1m=candles_1m_sl_first,
        policy=IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
    )

    assert res2.barrier_hit == BarrierHitType.SL_FIRST
    assert res2.exit_price == sl
    assert res2.exit_timestamp == t_15m_open + timedelta(minutes=2)
