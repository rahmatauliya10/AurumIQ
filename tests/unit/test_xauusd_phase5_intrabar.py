"""
Unit tests for XAUUSD Phase 5 side-aware intrabar ambiguity resolution.
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest

from engine.core.types import (
    BarrierHitType,
    CandleData,
    IntrabarPolicy,
    RiskSide,
)
from engine.risk.xauusd_intrabar import SideAwareIntrabarResolver


@pytest.fixture
def resolver():
    return SideAwareIntrabarResolver()


@pytest.mark.unit
def test_non_ambiguous_long_and_short(resolver):
    """Non-ambiguous bars resolve immediately to TP or SL without lower-TF replay."""
    t_open = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    t_close = datetime(2026, 9, 1, 8, 15, 0, tzinfo=timezone.utc)

    # Bar high=2515, low=2502. TP=2510, SL=2495. (Only TP touched for LONG)
    bar_long_tp = CandleData(
        t_open, t_close,
        Decimal("2503.00"), Decimal("2515.00"), Decimal("2502.00"), Decimal("2512.00"),
        Decimal("100.0"), True
    )
    res_l = resolver.resolve(RiskSide.LONG, bar_long_tp, tp_price=Decimal("2510.00"), sl_price=Decimal("2495.00"))
    assert res_l.barrier_hit == BarrierHitType.TP_FIRST
    assert res_l.exit_price == Decimal("2510.00")

    # For SHORT: entry mid 2500, TP=2490, SL=2508.
    # Bar high=2504, low=2485. (Only TP touched for SHORT because low <= 2490 and high < 2508)
    bar_short_tp = CandleData(
        t_open, t_close,
        Decimal("2500.00"), Decimal("2504.00"), Decimal("2485.00"), Decimal("2488.00"),
        Decimal("100.0"), True
    )
    res_s = resolver.resolve(RiskSide.SHORT, bar_short_tp, tp_price=Decimal("2490.00"), sl_price=Decimal("2508.00"))
    assert res_s.barrier_hit == BarrierHitType.TP_FIRST
    assert res_s.exit_price == Decimal("2490.00")


@pytest.mark.unit
def test_ambiguous_conservative_sl_first(resolver):
    """Ambiguous bar touching both TP and SL resolves to SL_FIRST under CONSERVATIVE_SL_FIRST."""
    t_open = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    t_close = datetime(2026, 9, 1, 8, 15, 0, tzinfo=timezone.utc)

    # LONG: TP=2520, SL=2490. Bar high=2525, low=2485 (touches both)
    ambig_bar = CandleData(
        t_open, t_close,
        Decimal("2500.00"), Decimal("2525.00"), Decimal("2485.00"), Decimal("2510.00"),
        Decimal("100.0"), True
    )
    res = resolver.resolve(
        RiskSide.LONG,
        ambig_bar,
        tp_price=Decimal("2520.00"),
        sl_price=Decimal("2490.00"),
        policy=IntrabarPolicy.CONSERVATIVE_SL_FIRST,
    )
    assert res.barrier_hit == BarrierHitType.SL_FIRST
    assert res.exit_price == Decimal("2490.00")
    assert res.policy_applied == IntrabarPolicy.CONSERVATIVE_SL_FIRST


@pytest.mark.unit
def test_ambiguous_worst_case_side_aware(resolver):
    """WORST_CASE applies adverse penalty gap: LONG is stop - gap; SHORT is stop + gap."""
    t_open = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    t_close = datetime(2026, 9, 1, 8, 15, 0, tzinfo=timezone.utc)

    ambig_bar = CandleData(
        t_open, t_close,
        Decimal("2500.00"), Decimal("2525.00"), Decimal("2485.00"), Decimal("2510.00"),
        Decimal("100.0"), True
    )

    # LONG: SL=2490, gap=5 -> exit_price = 2485.00
    res_l = resolver.resolve(
        RiskSide.LONG,
        ambig_bar,
        tp_price=Decimal("2520.00"),
        sl_price=Decimal("2490.00"),
        policy=IntrabarPolicy.WORST_CASE,
        worst_case_adverse_gap=Decimal("5.00"),
    )
    assert res_l.barrier_hit == BarrierHitType.SL_FIRST
    assert res_l.exit_price == Decimal("2485.00")

    # SHORT: SL=2515, gap=5 -> exit_price = 2520.00
    res_s = resolver.resolve(
        RiskSide.SHORT,
        ambig_bar,
        tp_price=Decimal("2490.00"),
        sl_price=Decimal("2515.00"),
        policy=IntrabarPolicy.WORST_CASE,
        worst_case_adverse_gap=Decimal("5.00"),
    )
    assert res_s.barrier_hit == BarrierHitType.SL_FIRST
    assert res_s.exit_price == Decimal("2520.00")


@pytest.mark.unit
def test_worst_case_requires_explicit_gap(resolver):
    """WORST_CASE without explicit non-negative Decimal gap raises ValueError."""
    t_open = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    t_close = datetime(2026, 9, 1, 8, 15, 0, tzinfo=timezone.utc)
    ambig_bar = CandleData(
        t_open, t_close,
        Decimal("2500.00"), Decimal("2525.00"), Decimal("2485.00"), Decimal("2510.00"),
        Decimal("100.0"), True
    )

    with pytest.raises(ValueError, match="worst_case_adverse_gap must be a non-negative finite Decimal"):
        resolver.resolve(
            RiskSide.LONG,
            ambig_bar,
            tp_price=Decimal("2520.00"),
            sl_price=Decimal("2490.00"),
            policy=IntrabarPolicy.WORST_CASE,
            worst_case_adverse_gap=None,
        )


@pytest.mark.unit
def test_naive_fill_timestamp_fails_closed(resolver):
    """Naive fill_timestamp fails closed with UNRESOLVED and informative reason."""
    t_open = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    t_close = datetime(2026, 9, 1, 8, 15, 0, tzinfo=timezone.utc)
    ambig_bar = CandleData(
        t_open, t_close,
        Decimal("2500.00"), Decimal("2525.00"), Decimal("2485.00"), Decimal("2510.00"),
        Decimal("100.0"), True
    )
    naive_fill = datetime(2026, 9, 1, 8, 5, 0)

    res = resolver.resolve(
        RiskSide.LONG,
        ambig_bar,
        tp_price=Decimal("2520.00"),
        sl_price=Decimal("2490.00"),
        fill_timestamp=naive_fill,
    )
    assert res.barrier_hit == BarrierHitType.UNRESOLVED
    assert "must be timezone aware" in res.reasons[0]


@pytest.mark.unit
def test_lower_tf_replay_short_tp_first(resolver):
    """Lower timeframe 1m replay resolves SHORT TP first when early 1m bar touches TP without touching SL."""
    t_open = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    t_close = datetime(2026, 9, 1, 8, 15, 0, tzinfo=timezone.utc)

    # Parent 15m touches both TP=2490 and SL=2510
    parent = CandleData(
        t_open, t_close,
        Decimal("2500.00"), Decimal("2515.00"), Decimal("2485.00"), Decimal("2495.00"),
        Decimal("500.0"), True
    )

    # Generate 15 contiguous 1m bars
    # Bar 0 (8:00 - 8:01): drops to 2488 (touches TP 2490), high is 2502 < SL 2510
    bars_1m = []
    for i in range(15):
        b_open = t_open + timedelta(minutes=i)
        b_close = b_open + timedelta(minutes=1)
        if i == 0:
            b = CandleData(b_open, b_close, Decimal("2500.00"), Decimal("2502.00"), Decimal("2488.00"), Decimal("2490.00"), Decimal("10.0"), True)
        elif i == 10:
            b = CandleData(b_open, b_close, Decimal("2495.00"), Decimal("2515.00"), Decimal("2494.00"), Decimal("2512.00"), Decimal("10.0"), True)
        else:
            b = CandleData(b_open, b_close, Decimal("2495.00"), Decimal("2498.00"), Decimal("2492.00"), Decimal("2496.00"), Decimal("10.0"), True)
        bars_1m.append(b)

    res = resolver.resolve(
        RiskSide.SHORT,
        parent,
        tp_price=Decimal("2490.00"),
        sl_price=Decimal("2510.00"),
        lower_tf_candles_1m=bars_1m,
        parent_timeframe="15m",
        policy=IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
    )
    assert res.barrier_hit == BarrierHitType.TP_FIRST
    assert res.exit_price == Decimal("2490.00")
    assert res.exit_timestamp == bars_1m[0].timestamp_close
