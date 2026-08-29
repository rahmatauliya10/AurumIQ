"""Unit tests for Phase 3A: Robust Time Cycle Intelligence subsystem."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest

from engine.core.types import (
    CandleData,
    EventImpact,
    MacroEvent,
    SessionType,
    StructureResult,
    StructureType,
    BosType,
    SwingPoint,
    SwingType,
)
from engine.cycles.session import classify_session
from engine.cycles.swing_duration import calculate_swing_duration
from engine.cycles.events import evaluate_macro_event_risk
from engine.cycles.calendar import calculate_calendar_seasonality
from engine.cycles.engine import RobustTimeCycleEngine
from engine.cycles.benchmark import record_baseline_benchmark


@pytest.mark.unit
def test_cycles_session_progress_and_liquidity():
    """Verify session progress percentage and liquidity flags."""
    # 10:30 UTC in winter (10:30 London local) -> LONDON session
    dt = datetime(2026, 1, 15, 10, 30, tzinfo=timezone.utc)
    ctx = classify_session(dt)
    assert ctx.session == SessionType.LONDON
    assert ctx.is_high_liquidity is True
    # Progress: 10.5 from 8.0 to 13.0 -> (2.5 / 5.0) * 100 = 50.0%
    assert ctx.progress_pct == 50.0
    assert ctx.expectancy_score == 12.0


@pytest.mark.unit
def test_cycles_swing_duration_maturity_ranking():
    """Verify causal pullback duration percentiles and is_mature flag."""
    swing_time = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
    swing = SwingPoint(
        index=10,
        timestamp=swing_time,
        detected_at=swing_time + timedelta(minutes=45),
        price=Decimal("2500.00"),
        swing_type=SwingType.HIGH,
        is_confirmed=True,
    )
    structure = StructureResult(
        timestamp=swing_time,
        structure_type=StructureType.HH,
        bos=BosType.NONE,
        last_swing_high=swing,
        last_swing_low=None,
        swings=(swing,),
        zones=(),
    )

    # 1. Young move (4 bars elapsed, 1 hour)
    candle_young = CandleData(
        timestamp_open=swing_time + timedelta(minutes=45),
        timestamp_close=swing_time + timedelta(hours=1),
        open=Decimal("2490"), high=Decimal("2495"), low=Decimal("2485"), close=Decimal("2492"),
        volume=Decimal("100"), is_closed=True,
    )
    dur_young = calculate_swing_duration(candle_young, structure)
    assert dur_young.bars_since_last_swing == 4
    assert dur_young.hours_since_last_swing == 1.0
    assert dur_young.is_mature is False

    # 2. Mature move (30 bars elapsed, 7.5 hours -> falls into ~P75)
    candle_mature = CandleData(
        timestamp_open=swing_time + timedelta(minutes=435),
        timestamp_close=swing_time + timedelta(hours=7, minutes=30),
        open=Decimal("2460"), high=Decimal("2465"), low=Decimal("2455"), close=Decimal("2458"),
        volume=Decimal("100"), is_closed=True,
    )
    dur_mature = calculate_swing_duration(candle_mature, structure)
    assert dur_mature.bars_since_last_swing == 30
    assert dur_mature.hours_since_last_swing == 7.5
    assert dur_mature.pullback_age_percentile >= 65.0
    assert dur_mature.maturity_score >= 15.0


@pytest.mark.unit
def test_cycles_calendar_seasonality_stability_filter():
    """Verify that unstable historical folds collapse seasonality score to 0.0."""
    as_of = datetime(2026, 8, 12, 14, 0, tzinfo=timezone.utc)  # Wednesday 14:00 UTC

    # 1. Stable folds (average stability 0.85 >= 0.60)
    ctx_stable = calculate_calendar_seasonality(as_of, historical_fold_stabilities=[0.8, 0.9, 0.85])
    assert ctx_stable.stability_score >= 0.80
    assert ctx_stable.seasonality_score > 0.0

    # 2. Unstable folds (average stability 0.40 < 0.60)
    ctx_unstable = calculate_calendar_seasonality(as_of, historical_fold_stabilities=[0.3, 0.5, 0.4])
    assert ctx_unstable.stability_score == 0.40
    assert ctx_unstable.seasonality_score == 0.0


@pytest.mark.unit
def test_cycles_robust_engine_end_to_end():
    """Verify full RobustTimeCycleEngine end-to-end execution and snapshot generation."""
    engine = RobustTimeCycleEngine(cycle_version="3.0.0-3A", blackout_minutes=30)

    candle = CandleData(
        timestamp_open=datetime(2026, 8, 12, 13, 45, tzinfo=timezone.utc),
        timestamp_close=datetime(2026, 8, 12, 14, 0, tzinfo=timezone.utc),
        open=Decimal("2500"), high=Decimal("2510"), low=Decimal("2495"), close=Decimal("2505"),
        volume=Decimal("150"), is_closed=True,
    )
    swing = SwingPoint(
        index=5,
        timestamp=datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc),
        detected_at=datetime(2026, 8, 12, 8, 45, tzinfo=timezone.utc),
        price=Decimal("2520"),
        swing_type=SwingType.HIGH,
        is_confirmed=True,
    )
    structure = StructureResult(
        timestamp=candle.timestamp_close,
        structure_type=StructureType.LH,
        bos=BosType.NONE,
        last_swing_high=swing,
        last_swing_low=None,
        swings=(swing,),
        zones=(),
    )

    # 1. Normal trading window (no macro blackout)
    snapshot = engine.analyze(latest_candle=candle, structure=structure)
    assert snapshot.cycle_version == "3.0.0-3A"
    assert snapshot.session.session == SessionType.LONDON_NY_OVERLAP
    assert snapshot.is_blocked_by_event is False
    assert snapshot.cycle_score_3a > 0.0

    # 2. Blackout window active (CPI event at 14:15 UTC -> 15 min away)
    cpi = MacroEvent(
        event_id="CPI-TEST",
        name="CPI",
        scheduled_at=datetime(2026, 8, 12, 14, 15, tzinfo=timezone.utc),
        released_at=datetime(2026, 8, 12, 14, 15, tzinfo=timezone.utc),
        initial_value="2.5%",
        impact=EventImpact.HIGH,
    )
    snapshot_blackout = engine.analyze(latest_candle=candle, structure=structure, macro_events=[cpi])
    assert snapshot_blackout.is_blocked_by_event is True
    assert snapshot_blackout.cycle_score_3a == 0.0


@pytest.mark.unit
def test_cycles_baseline_benchmark_recording():
    """Verify BaselineBenchmark recorder for Phase 3A hurdle."""
    benchmark = record_baseline_benchmark(
        profit_factor=1.85,
        expectancy_r=0.42,
        max_drawdown_pct=8.5,
        trade_count=120,
    )
    assert benchmark.base_profit_factor == 1.85
    assert benchmark.base_expectancy_r == 0.42
    assert benchmark.base_max_drawdown == 8.5
    assert benchmark.base_trade_count == 120
    assert benchmark.recorded_at is not None
