"""
Unit and targeted tests for Phase 3A: Robust Time Cycle Intelligence subsystem.
Covers P3A-01 through P3A-18.
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest

from engine.core.exceptions import IncompleteCandleError
from engine.core.types import (
    CalendarEffectEntry,
    CandleData,
    EventImpact,
    MacroEvent,
    RegimeType,
    SampleEvaluation,
    SampleQuality,
    SessionExpectancyEntry,
    SessionType,
    StructureResult,
    StructureType,
    BosType,
    SwingPoint,
    SwingType,
)
from engine.cycles.session import classify_session
from engine.cycles.swing_duration import calculate_swing_duration, timeframe_to_seconds, ALLOWED_TIMEFRAMES
from engine.cycles.events import evaluate_macro_event_risk
from engine.cycles.calendar import calculate_calendar_seasonality
from engine.cycles.engine import RobustTimeCycleEngine
from engine.cycles.benchmark import record_baseline_benchmark


@pytest.mark.unit
def test_p3a_01_session_progress_and_liquidity():
    """P3A-01: Verify session progress percentage and liquidity flags."""
    # 10:30 UTC in winter (10:30 London local) -> LONDON session
    dt = datetime(2026, 1, 15, 10, 30, tzinfo=timezone.utc)
    ctx = classify_session(dt)
    assert ctx.session == SessionType.LONDON
    assert ctx.is_high_liquidity is True
    # Progress: 10.5 from 8.0 to 13.0 -> (2.5 / 5.0) * 100 = 50.0%
    assert ctx.progress_pct == 50.0
    # No historical table provided -> expectancy_score is 0.0
    assert ctx.expectancy_score == 0.0


@pytest.mark.unit
def test_p3a_06_session_sample_guard():
    """
    P3A-06: Session Sample Guard.
    No historical session statistics -> expectancy_score = 0, INSUFFICIENT_DATA.
    Positive score is only unlocked when effective_n >= 30.
    """
    dt = datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc)  # London/NY Overlap

    # 1. No historical table -> 0.0 score, INSUFFICIENT
    ctx_no_data = classify_session(dt, regime=RegimeType.BULL_TREND, expectancy_table=None)
    assert ctx_no_data.expectancy_score == 0.0
    assert ctx_no_data.sample_quality == SampleQuality.INSUFFICIENT
    assert ctx_no_data.effective_n == 0.0

    # 2. Table with small sample (effective_n = 18 < 30) -> blocked to 0.0
    small_sample_entry = SessionExpectancyEntry(
        session=SessionType.LONDON_NY_OVERLAP,
        regime=RegimeType.BULL_TREND,
        sample_count=20,
        effective_n=18.0,
        win_rate=0.65,
        expectancy_r=0.45,
        is_statistically_significant=True,
    )
    table_small = {(SessionType.LONDON_NY_OVERLAP, RegimeType.BULL_TREND): small_sample_entry}
    ctx_small = classify_session(dt, regime=RegimeType.BULL_TREND, expectancy_table=table_small)
    assert ctx_small.expectancy_score == 0.0
    assert ctx_small.sample_quality == SampleQuality.INSUFFICIENT


@pytest.mark.unit
def test_p3a_14_session_significance_gate():
    """
    P3A-14: Session Significance Gate.
    High N + positive expectancy, but statistically insignificant -> expectancy_score = 0.0.
    """
    dt = datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc)  # London/NY Overlap

    # High sample (effective_n = 500), positive expectancy (+0.20R), but is_statistically_significant = False
    insignificant_entry = SessionExpectancyEntry(
        session=SessionType.LONDON_NY_OVERLAP,
        regime=RegimeType.BULL_TREND,
        sample_count=600,
        effective_n=500.0,
        win_rate=0.52,
        expectancy_r=0.20,
        is_statistically_significant=False,
    )
    table_insignificant = {(SessionType.LONDON_NY_OVERLAP, RegimeType.BULL_TREND): insignificant_entry}
    ctx_insig = classify_session(dt, regime=RegimeType.BULL_TREND, expectancy_table=table_insignificant)
    assert ctx_insig.expectancy_score == 0.0
    assert ctx_insig.sample_quality == SampleQuality.INSUFFICIENT

    # When statistically significant -> full positive score unlocked
    significant_entry = SessionExpectancyEntry(
        session=SessionType.LONDON_NY_OVERLAP,
        regime=RegimeType.BULL_TREND,
        sample_count=600,
        effective_n=500.0,
        win_rate=0.58,
        expectancy_r=0.20,
        is_statistically_significant=True,
    )
    table_sig = {(SessionType.LONDON_NY_OVERLAP, RegimeType.BULL_TREND): significant_entry}
    ctx_sig = classify_session(dt, regime=RegimeType.BULL_TREND, expectancy_table=table_sig)
    assert ctx_sig.sample_quality == SampleQuality.HIGH
    assert ctx_sig.expectancy_score == 6.0  # 0.20 * 30 * 1.0 = 6.0


@pytest.mark.unit
def test_p3a_07_swing_knowable_age():
    """
    P3A-07: Swing Knowable Age.
    Scoring age starts strictly from detected_at (confirmation timestamp),
    not the formation timestamp when the swing peak occurred.
    """
    formation_time = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
    confirmed_time = datetime(2026, 8, 1, 10, 45, tzinfo=timezone.utc)

    swing = SwingPoint(
        index=10, timestamp=formation_time, detected_at=confirmed_time,
        price=Decimal("2500.00"), swing_type=SwingType.HIGH, is_confirmed=True,
    )
    structure = StructureResult(
        timestamp=confirmed_time, structure_type=StructureType.HH, bos=BosType.NONE,
        last_swing_high=swing, last_swing_low=None, swings=(swing,), zones=(),
    )

    current_time = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    candle = CandleData(
        timestamp_open=current_time - timedelta(minutes=15), timestamp_close=current_time,
        open=Decimal("2490"), high=Decimal("2495"), low=Decimal("2485"), close=Decimal("2492"),
        volume=Decimal("100"), is_closed=True,
    )

    ctx = calculate_swing_duration(
        latest_candle=candle, structure=structure, timeframe="15m",
        historical_durations=[1, 2, 3, 4, 5] * 10,
        effective_n=40.0,
    )

    # Market age: from 10:00 to 12:00 -> 2.0 hours (8 bars of 15m)
    assert ctx.market_age_hours == 2.0
    assert ctx.market_age_bars == 8

    # Knowable age: from 10:45 to 12:00 -> 1.25 hours (5 bars of 15m)
    assert ctx.known_age_hours == 1.25
    assert ctx.known_age_bars == 5


@pytest.mark.unit
def test_p3a_08_timeframe_safe_swing_duration():
    """
    P3A-08: Timeframe-Safe Swing Duration & Strict Whitelist Validation.
    No hardcoded 900 seconds. 1H candle is 1 bar, not 4 bars.
    """
    assert timeframe_to_seconds("1m") == 60
    assert timeframe_to_seconds("5m") == 300
    assert timeframe_to_seconds("15m") == 900
    assert timeframe_to_seconds("1h") == 3600
    assert timeframe_to_seconds("4h") == 14400
    assert timeframe_to_seconds("1d") == 86400
    assert timeframe_to_seconds("1w") == 604800

    # Whitelist enforcement: typos and unauthorized intervals must raise ValueError
    with pytest.raises(ValueError, match="Unsupported timeframe"):
        timeframe_to_seconds("15mn")
    with pytest.raises(ValueError, match="Unsupported timeframe"):
        timeframe_to_seconds("0m")
    with pytest.raises(ValueError, match="Unsupported timeframe"):
        timeframe_to_seconds("37h")
    with pytest.raises(ValueError, match="Unsupported timeframe"):
        timeframe_to_seconds("invalid")

    confirmed_time = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
    swing = SwingPoint(
        index=10, timestamp=confirmed_time, detected_at=confirmed_time,
        price=Decimal("2500.00"), swing_type=SwingType.HIGH, is_confirmed=True,
    )
    structure = StructureResult(
        timestamp=confirmed_time, structure_type=StructureType.HH, bos=BosType.NONE,
        last_swing_high=swing, last_swing_low=None, swings=(swing,), zones=(),
    )

    current_time = datetime(2026, 8, 1, 14, 0, tzinfo=timezone.utc)
    candle = CandleData(
        timestamp_open=current_time - timedelta(hours=1), timestamp_close=current_time,
        open=Decimal("2490"), high=Decimal("2495"), low=Decimal("2485"), close=Decimal("2492"),
        volume=Decimal("100"), is_closed=True,
    )

    # 1H: 4 hours = 4 bars
    ctx_1h = calculate_swing_duration(candle, structure, timeframe="1h", historical_durations=[1, 2, 3, 4] * 10, effective_n=40.0)
    assert ctx_1h.known_age_bars == 4
    assert ctx_1h.known_age_hours == 4.0

    # 15m: 4 hours = 16 bars
    ctx_15m = calculate_swing_duration(candle, structure, timeframe="15m", historical_durations=[1, 2, 3, 4] * 10, effective_n=40.0)
    assert ctx_15m.known_age_bars == 16


@pytest.mark.unit
def test_p3a_15_swing_effective_sample_guard():
    """
    P3A-15: Swing Effective Sample Guard.
    Raw N = 100, but effective_n = 18 (discounted by overlap/clustering) -> maturity_score = 0.0 (INSUFFICIENT).
    """
    swing_time = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
    swing = SwingPoint(
        index=10, timestamp=swing_time, detected_at=swing_time,
        price=Decimal("2500.00"), swing_type=SwingType.HIGH, is_confirmed=True,
    )
    structure = StructureResult(
        timestamp=swing_time, structure_type=StructureType.HH, bos=BosType.NONE,
        last_swing_high=swing, last_swing_low=None, swings=(swing,), zones=(),
    )
    candle = CandleData(
        timestamp_open=swing_time + timedelta(hours=5), timestamp_close=swing_time + timedelta(hours=5, minutes=15),
        open=Decimal("2480"), high=Decimal("2485"), low=Decimal("2475"), close=Decimal("2480"),
        volume=Decimal("100"), is_closed=True,
    )

    raw_durations = [5, 10, 15, 20, 25] * 20  # Raw N = 100

    # 1. When effective_n is 18.0 (< 30) -> maturity_score is strictly blocked to 0.0
    ctx_insuf = calculate_swing_duration(
        latest_candle=candle,
        structure=structure,
        timeframe="15m",
        historical_durations=raw_durations,
        effective_n=18.0,
    )
    assert ctx_insuf.effective_n == 18.0
    assert ctx_insuf.maturity_score == 0.0
    assert ctx_insuf.sample_quality == SampleQuality.INSUFFICIENT

    # 2. When effective_n is 120.0 (>= 100) -> Full maturity score allowed
    ctx_robust = calculate_swing_duration(
        latest_candle=candle,
        structure=structure,
        timeframe="15m",
        historical_durations=raw_durations,
        effective_n=120.0,
    )
    assert ctx_robust.sample_quality == SampleQuality.HIGH
    assert ctx_robust.maturity_score > 0.0


@pytest.mark.unit
def test_p3a_18_effective_n_metadata_required():
    """
    P3A-18: Unknown Effective-N Must Fail Closed.
    If raw_n = 100, but sample_eval = None and effective_n = None,
    engine must NOT assume effective_n = 100. It must fail-closed to effective_n = 0.0 and maturity_score = 0.0.
    """
    swing_time = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
    swing = SwingPoint(
        index=10, timestamp=swing_time, detected_at=swing_time,
        price=Decimal("2500.00"), swing_type=SwingType.HIGH, is_confirmed=True,
    )
    structure = StructureResult(
        timestamp=swing_time, structure_type=StructureType.HH, bos=BosType.NONE,
        last_swing_high=swing, last_swing_low=None, swings=(swing,), zones=(),
    )
    candle = CandleData(
        timestamp_open=swing_time + timedelta(hours=5), timestamp_close=swing_time + timedelta(hours=5, minutes=15),
        open=Decimal("2480"), high=Decimal("2485"), low=Decimal("2475"), close=Decimal("2480"),
        volume=Decimal("100"), is_closed=True,
    )

    raw_durations = [5, 10, 15, 20, 25] * 20  # Raw N = 100

    # 1. Caller provides 100 observations, but effective_n=None and sample_eval=None
    ctx_unknown_eff_n = calculate_swing_duration(
        latest_candle=candle,
        structure=structure,
        timeframe="15m",
        historical_durations=raw_durations,
        effective_n=None,
        sample_eval=None,
    )
    # Must fail closed
    assert ctx_unknown_eff_n.effective_n == 0.0
    assert ctx_unknown_eff_n.sample_quality == SampleQuality.INSUFFICIENT
    assert ctx_unknown_eff_n.maturity_score == 0.0
    # Descriptive percentile is still knowable
    assert ctx_unknown_eff_n.pullback_age_percentile is not None

    # 2. When SampleEvaluation provides certified effective_n = 80.0
    certified_eval = SampleEvaluation(
        n_raw=100,
        independent_after_overlap=90,
        temporal_clusters=10,
        hhi_norm=0.05,
        regime_discount=1.0,
        clustering_discount=0.88,
        effective_n=80.0,
        quality=SampleQuality.MEDIUM,
        weight_multiplier=0.8,
        is_blocked=False,
        message="Sample validated",
    )
    ctx_certified = calculate_swing_duration(
        latest_candle=candle,
        structure=structure,
        timeframe="15m",
        historical_durations=raw_durations,
        sample_eval=certified_eval,
    )
    assert ctx_certified.effective_n == 80.0
    assert ctx_certified.sample_quality == SampleQuality.MEDIUM
    assert ctx_certified.maturity_score > 0.0


@pytest.mark.unit
def test_p3a_10_calendar_no_evidence_gate_and_month_end():
    """
    P3A-10: Calendar No-Evidence Gate.
    No historical folds -> stability = 0.0, seasonality score = 0.0.
    Accurate month-end flow based on exact days in month.
    """
    as_of = datetime(2026, 8, 12, 14, 0, tzinfo=timezone.utc)

    # 1. No evidence -> 0.0 score
    ctx_no_data = calculate_calendar_seasonality(as_of, historical_fold_stabilities=None)
    assert ctx_no_data.stability_score == 0.0
    assert ctx_no_data.seasonality_score == 0.0
    assert ctx_no_data.sample_quality == SampleQuality.INSUFFICIENT

    # 2. Month-End accuracy check:
    dt_aug28 = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    dt_aug29 = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
    assert calculate_calendar_seasonality(dt_aug28, [0.8] * 4).is_month_end_flow is False
    assert calculate_calendar_seasonality(dt_aug29, [0.8] * 4).is_month_end_flow is True

    # February in non-leap year (28 days). Feb 25 -> 3 (False). Feb 26 -> 2 (True).
    dt_feb25 = datetime(2025, 2, 25, 12, 0, tzinfo=timezone.utc)
    dt_feb26 = datetime(2025, 2, 26, 12, 0, tzinfo=timezone.utc)
    assert calculate_calendar_seasonality(dt_feb25, [0.8] * 4).is_month_end_flow is False
    assert calculate_calendar_seasonality(dt_feb26, [0.8] * 4).is_month_end_flow is True


@pytest.mark.unit
def test_p3a_16_calendar_empirical_effect_gate():
    """
    P3A-16: Calendar Empirical Effect Gate.
    Stable folds alone without empirical effect table -> seasonality_score = 0.0.
    Positive score requires statistically significant empirical edge.
    """
    as_of = datetime(2026, 8, 12, 14, 0, tzinfo=timezone.utc)  # Wednesday (DOW 2) 14:00 UTC

    # 1. Folds are stable (0.85), but no empirical effect table -> score is strictly 0.0
    ctx_no_effect = calculate_calendar_seasonality(
        as_of=as_of,
        historical_fold_stabilities=[0.85, 0.85, 0.85],
        calendar_effect_table=None,
    )
    assert ctx_no_effect.stability_score == 0.85
    assert ctx_no_effect.seasonality_score == 0.0
    assert ctx_no_effect.sample_quality == SampleQuality.INSUFFICIENT

    # 2. Effect table with statistically significant positive expectancy -> score granted
    effect_entry = CalendarEffectEntry(
        bucket="DOW_2_HOUR_14",
        sample_count=120,
        effective_n=100.0,
        win_rate=0.62,
        expectancy_r=0.35,
        stability=0.85,
        is_statistically_significant=True,
    )
    table = {"DOW_2_HOUR_14": effect_entry}
    ctx_valid = calculate_calendar_seasonality(
        as_of=as_of,
        historical_fold_stabilities=[0.85, 0.85, 0.85],
        calendar_effect_table=table,
    )
    assert ctx_valid.sample_quality == SampleQuality.HIGH
    assert ctx_valid.seasonality_score == float(round(0.35 * 10.0 * 0.85 * 1.0, 2))  # 2.97


@pytest.mark.unit
def test_p3a_11_future_revision_timestamp_cannot_create_pre_revision_blackout():
    """
    P3A-11: Future Revision Timestamp Safety.
    An unscheduled revision published at 10:20 is UNKNOWN at 10:00.
    It cannot create a blackout at 10:00.
    """
    nfp_event = MacroEvent(
        event_id="NFP-TEST",
        name="US NFP",
        scheduled_at=datetime(2026, 8, 1, 12, 30, tzinfo=timezone.utc),
        released_at=datetime(2026, 8, 1, 12, 30, tzinfo=timezone.utc),
        initial_value="+100K",
        revised_at=datetime(2026, 9, 4, 12, 30, tzinfo=timezone.utc),
        revised_value="+80K",
        impact=EventImpact.HIGH,
    )
    events = [nfp_event]

    # Pre-revision (at 12:10 UTC, 20m before revision published) -> NOT in blackout
    ctx_pre_rev = evaluate_macro_event_risk(
        datetime(2026, 9, 4, 12, 10, tzinfo=timezone.utc),
        events=events,
        blackout_minutes=30,
    )
    assert ctx_pre_rev.is_in_blackout is False

    # Post-revision (at 12:35 UTC, 5m after revision published) -> IN blackout
    ctx_post_rev = evaluate_macro_event_risk(
        datetime(2026, 9, 4, 12, 35, tzinfo=timezone.utc),
        events=events,
        blackout_minutes=30,
    )
    assert ctx_post_rev.is_in_blackout is True
    assert ctx_post_rev.point_in_time_value == "+80K"


@pytest.mark.unit
def test_p3a_12_missing_macro_feed_safety():
    """
    P3A-12: Missing Macro Feed Safety.
    No macro calendar data -> is_feed_healthy = False, macro_clear_bonus = 0.0.
    """
    engine = RobustTimeCycleEngine(cycle_version="3.0.0-3A", blackout_minutes=30)
    candle = CandleData(
        timestamp_open=datetime(2026, 8, 12, 13, 45, tzinfo=timezone.utc),
        timestamp_close=datetime(2026, 8, 12, 14, 0, tzinfo=timezone.utc),
        open=Decimal("2500"), high=Decimal("2510"), low=Decimal("2495"), close=Decimal("2505"),
        volume=Decimal("150"), is_closed=True,
    )
    structure = StructureResult(
        timestamp=candle.timestamp_close, structure_type=StructureType.LH, bos=BosType.NONE,
        last_swing_high=None, last_swing_low=None, swings=(), zones=(),
    )

    # Missing macro events (macro_events = None)
    snapshot_empty = engine.analyze(latest_candle=candle, structure=structure, macro_events=None)
    assert snapshot_empty.macro_event.is_feed_healthy is False
    assert snapshot_empty.cycle_score_3a == 0.0

    # Healthy macro feed with event > 120m away
    future_event = MacroEvent(
        event_id="CPI-FAR", name="CPI",
        scheduled_at=datetime(2026, 8, 12, 18, 0, tzinfo=timezone.utc),
        released_at=datetime(2026, 8, 12, 18, 0, tzinfo=timezone.utc),
        initial_value=None, impact=EventImpact.HIGH,
    )
    snapshot_healthy = engine.analyze(latest_candle=candle, structure=structure, macro_events=[future_event])
    assert snapshot_healthy.macro_event.is_feed_healthy is True
    assert snapshot_healthy.macro_event.minutes_to_next_event == 240
    assert snapshot_healthy.cycle_score_3a == 5.0


@pytest.mark.unit
def test_p3a_17_closed_candle_analysis_gate():
    """
    P3A-17: Closed Candle Analysis Gate.
    RobustTimeCycleEngine must reject analysis if latest_candle is unclosed (is_closed=False).
    """
    engine = RobustTimeCycleEngine(cycle_version="3.0.0-3A")
    open_candle = CandleData(
        timestamp_open=datetime(2026, 8, 12, 14, 0, tzinfo=timezone.utc),
        timestamp_close=datetime(2026, 8, 12, 14, 15, tzinfo=timezone.utc),
        open=Decimal("2500"), high=Decimal("2510"), low=Decimal("2495"), close=Decimal("2505"),
        volume=Decimal("150"), is_closed=False,
    )
    structure = StructureResult(
        timestamp=open_candle.timestamp_open, structure_type=StructureType.LH, bos=BosType.NONE,
        last_swing_high=None, last_swing_low=None, swings=(), zones=(),
    )

    with pytest.raises(IncompleteCandleError, match="requires a completed"):
        engine.analyze(latest_candle=open_candle, structure=structure)
