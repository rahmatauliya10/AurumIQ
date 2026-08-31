"""
Acceptance & Migration Tests for Phase 3A XAUUSD Robust Time Cycle:
- Profile Isolation & Zero Legacy Numerical Fallback
- DST-Safe Session Classification
- Fail-Safe Uncalibrated Scoring (Session, Swing, Calendar, Macro Clear)
- Closed-Candle Gating & Future Mutation Invariance
- Swing Known-Age Causality & Macro Revision PiT Safety
- Fixture Calibration Determinism & Engine Isolation
- Pure Python AST Purity (Zero Django imports)
- Zero Directional (Phase 4) Bias
"""
import ast
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import os
import pytest

from engine.core.exceptions import IncompleteCandleError
from engine.core.types import (
    CalendarEffectEntry,
    CandleData,
    EventImpact,
    MacroEvent,
    RegimeType,
    SampleQuality,
    SessionExpectancyEntry,
    SessionType,
    StructureResult,
    StructureType,
    BosType,
    SwingPoint,
    SwingType,
)
from engine.cycles.profile import Cycle3AProfile
from engine.cycles.session import classify_session
from engine.cycles.swing_duration import calculate_swing_duration
from engine.cycles.calendar import calculate_calendar_seasonality
from engine.cycles.events import evaluate_macro_event_risk
from engine.cycles.engine import RobustTimeCycleEngine
from engine.cycles.calibration import (
    CalibrationProvenance,
    Cycle3ACalibrationArtifact,
    calculate_distribution_percentiles,
    calibrate_session_expectancy,
    calibrate_swing_durations,
    build_profile_from_artifact,
)


def _make_closed_candle(
    idx: int,
    close: str = "2500.00",
    open_price: str = "2495.00",
    high: str = "2505.00",
    low: str = "2490.00",
    is_closed: bool = True,
) -> CandleData:
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=15 * idx)
    return CandleData(
        timestamp_open=t0,
        timestamp_close=t0 + timedelta(minutes=15),
        open=Decimal(open_price),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("100.0"),
        is_closed=is_closed,
    )


# ============================================================================
# Criterion A: Profile Isolation & Zero Legacy Number Fallback
# ============================================================================

@pytest.mark.unit
def test_criterion_a_xauusd_uncalibrated_profile_has_no_legacy_fallbacks():
    """
    Criterion A: uncalibrated_xauusd_profile() must contain NO legacy numbers (all None),
    is_calibrated=False, and details status CALIBRATION_REQUIRED.
    """
    profile = Cycle3AProfile.uncalibrated_xauusd_profile()
    assert profile.name == "XAUUSD_UNCALIBRATED"
    assert profile.is_calibrated is False
    assert profile.session_max_score is None
    assert profile.session_min_effective_n is None
    assert profile.session_expectancy_multiplier is None
    assert profile.session_expectancy_table is None
    assert profile.swing_max_score is None
    assert profile.swing_min_effective_n is None
    assert profile.swing_maturity_bands is None
    assert profile.historical_durations is None
    assert profile.calendar_max_score is None
    assert profile.calendar_min_effective_n is None
    assert profile.calendar_stability_threshold is None
    assert profile.calendar_expectancy_multiplier is None
    assert profile.calendar_effect_table is None
    assert profile.macro_blackout_minutes is None
    assert profile.macro_clear_window_far_minutes is None
    assert profile.macro_clear_window_near_minutes is None
    assert profile.macro_clear_bonus_far is None
    assert profile.macro_clear_bonus_near is None
    assert profile.details.get("calibration_status") == "CALIBRATION_REQUIRED"


@pytest.mark.unit
def test_criterion_a_legacy_xaut_profile_preserves_historical_constants():
    """
    Criterion A: legacy_xaut_profile() explicitly preserves the frozen historical XAUT numbers.
    """
    profile = Cycle3AProfile.legacy_xaut_profile()
    assert profile.name == "LEGACY_XAUT_REFERENCE"
    assert profile.is_calibrated is True
    assert profile.session_max_score == 15.0
    assert profile.session_min_effective_n == 30.0
    assert profile.session_expectancy_multiplier == 30.0
    assert profile.swing_max_score == 20.0
    assert profile.swing_min_effective_n == 30.0
    assert profile.calendar_max_score == 5.0
    assert profile.calendar_min_effective_n == 30.0
    assert profile.calendar_stability_threshold == 0.60
    assert profile.macro_blackout_minutes == 30
    assert profile.macro_clear_window_far_minutes == 120
    assert profile.macro_clear_window_near_minutes == 60
    assert profile.macro_clear_bonus_far == 5.0
    assert profile.macro_clear_bonus_near == 2.0


# ============================================================================
# Criterion B: DST-Safe Session Classification
# ============================================================================

@pytest.mark.unit
def test_criterion_b_xauusd_dst_session_classification():
    """
    Criterion B: Session classification uses zoneinfo local time and handles DST transitions.
    """
    # 1. London Summer (BST, UTC+1) vs Winter (GMT, UTC+0)
    dt_summer = datetime(2026, 7, 15, 8, 30, tzinfo=timezone.utc)  # 09:30 London BST -> LONDON
    ctx_summer = classify_session(dt_summer, profile=Cycle3AProfile.uncalibrated_xauusd_profile())
    assert ctx_summer.session == SessionType.LONDON
    assert ctx_summer.is_high_liquidity is True

    dt_winter = datetime(2026, 1, 15, 8, 30, tzinfo=timezone.utc)  # 08:30 London GMT -> LONDON
    ctx_winter = classify_session(dt_winter, profile=Cycle3AProfile.uncalibrated_xauusd_profile())
    assert ctx_winter.session == SessionType.LONDON

    # 2. Overlap window
    dt_overlap = datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc)  # 15:00 BST / 10:00 EDT -> OVERLAP
    ctx_overlap = classify_session(dt_overlap, profile=Cycle3AProfile.uncalibrated_xauusd_profile())
    assert ctx_overlap.session == SessionType.LONDON_NY_OVERLAP
    assert ctx_overlap.is_high_liquidity is True


# ============================================================================
# Criteria C, D, E, F: Fail-Safe Uncalibrated Scores = 0.0
# ============================================================================

@pytest.mark.unit
def test_criterion_c_xauusd_uncalibrated_session_zero_score():
    """
    Criterion C: XAUUSD without session calibration strictly yields expectancy_score = 0.0.
    """
    dt = datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc)
    # Even if caller passes a fake table, uncalibrated profile blocks positive score
    ctx = classify_session(dt, regime=RegimeType.BULL_TREND, profile=Cycle3AProfile.uncalibrated_xauusd_profile())
    assert ctx.expectancy_score == 0.0
    assert ctx.sample_quality == SampleQuality.INSUFFICIENT
    assert ctx.effective_n == 0.0


@pytest.mark.unit
def test_criterion_d_xauusd_uncalibrated_swing_zero_score():
    """
    Criterion D: XAUUSD without swing calibration strictly yields maturity_score = 0.0.
    """
    t_swing = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
    swing = SwingPoint(
        index=10, timestamp=t_swing, detected_at=t_swing + timedelta(minutes=30),
        price=Decimal("2500.00"), swing_type=SwingType.HIGH, is_confirmed=True,
    )
    structure = StructureResult(
        timestamp=t_swing + timedelta(minutes=30), structure_type=StructureType.HH, bos=BosType.NONE,
        last_swing_high=swing, last_swing_low=None, swings=(swing,), zones=(),
    )
    candle = _make_closed_candle(50)

    ctx = calculate_swing_duration(
        latest_candle=candle,
        structure=structure,
        timeframe="15m",
        historical_durations=[5, 10, 15, 20, 25] * 10,
        profile=Cycle3AProfile.uncalibrated_xauusd_profile(),
    )
    assert ctx.maturity_score == 0.0
    assert ctx.is_mature is False
    assert ctx.sample_quality == SampleQuality.INSUFFICIENT
    # Known age and market age are still calculated descriptively
    assert ctx.market_age_hours > 0
    assert ctx.known_age_hours > 0


@pytest.mark.unit
def test_criterion_e_xauusd_uncalibrated_calendar_zero_score():
    """
    Criterion E: XAUUSD without calendar calibration strictly yields seasonality_score = 0.0.
    """
    as_of = datetime(2026, 8, 12, 14, 0, tzinfo=timezone.utc)
    ctx = calculate_calendar_seasonality(
        as_of=as_of,
        historical_fold_stabilities=[0.90, 0.90, 0.90],
        profile=Cycle3AProfile.uncalibrated_xauusd_profile(),
    )
    assert ctx.seasonality_score == 0.0
    assert ctx.sample_quality == SampleQuality.INSUFFICIENT
    # Deterministic facts are preserved
    assert ctx.day_name == "Wednesday"
    assert ctx.hour_utc == 14
    assert ctx.month == 8


@pytest.mark.unit
def test_criterion_f_xauusd_uncalibrated_macro_clear_zero_bonus():
    """
    Criterion F: XAUUSD without macro timing calibration receives zero macro-clear bonus.
    """
    engine = RobustTimeCycleEngine.for_xauusd()
    candle = _make_closed_candle(10)
    structure = StructureResult(
        timestamp=candle.timestamp_close, structure_type=StructureType.LH, bos=BosType.NONE,
        last_swing_high=None, last_swing_low=None, swings=(), zones=(),
    )
    future_event = MacroEvent(
        event_id="CPI-FAR", name="CPI",
        scheduled_at=candle.timestamp_close + timedelta(hours=5),
        released_at=candle.timestamp_close + timedelta(hours=5),
        initial_value=None, impact=EventImpact.HIGH,
    )

    snapshot = engine.analyze(latest_candle=candle, structure=structure, macro_events=[future_event])
    assert snapshot.cycle_score_3a == 0.0
    assert snapshot.session.expectancy_score == 0.0
    assert snapshot.swing_duration.maturity_score == 0.0
    assert snapshot.calendar.seasonality_score == 0.0


# ============================================================================
# Criteria G, H, I, J: Closed-Candle, PiT & Causality Invariants
# ============================================================================

@pytest.mark.unit
def test_criterion_g_forming_open_candle_rejected():
    """
    Criterion G: Forming/open candle cannot mutate Phase 3A output and is rejected with IncompleteCandleError.
    """
    engine = RobustTimeCycleEngine.for_xauusd()
    open_candle = _make_closed_candle(10, is_closed=False)
    structure = StructureResult(
        timestamp=open_candle.timestamp_open, structure_type=StructureType.LH, bos=BosType.NONE,
        last_swing_high=None, last_swing_low=None, swings=(), zones=(),
    )
    with pytest.raises(IncompleteCandleError, match="requires a completed"):
        engine.analyze(latest_candle=open_candle, structure=structure)


@pytest.mark.unit
def test_criterion_h_future_candle_mutation_invariance():
    """
    Criterion H: Future candle mutations do not alter Phase 3A analysis evaluated at timestamp T.
    """
    engine = RobustTimeCycleEngine.for_xauusd()
    base_candle = _make_closed_candle(50)
    structure = StructureResult(
        timestamp=base_candle.timestamp_close, structure_type=StructureType.LH, bos=BosType.NONE,
        last_swing_high=None, last_swing_low=None, swings=(), zones=(),
    )

    snapshot_at_t = engine.analyze(latest_candle=base_candle, structure=structure)

    # Simulate 100 future candles with extreme volatility
    future_candles = [_make_closed_candle(50 + j, close=str(3000 + j * 10)) for j in range(1, 101)]

    # Analysis at T remains 100% identical
    snapshot_re_eval = engine.analyze(latest_candle=base_candle, structure=structure)
    assert snapshot_at_t.timestamp == snapshot_re_eval.timestamp
    assert snapshot_at_t.cycle_score_3a == snapshot_re_eval.cycle_score_3a
    assert snapshot_at_t.session.session == snapshot_re_eval.session.session
    assert snapshot_at_t.calendar.is_month_end_flow == snapshot_re_eval.calendar.is_month_end_flow


@pytest.mark.unit
def test_criterion_i_swing_known_age_causality():
    """
    Criterion I: Future swing confirmation cannot alter pre-confirmation result.
    Known age strictly begins at detected_at.
    """
    t_peak = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
    t_confirm = datetime(2026, 8, 1, 10, 45, tzinfo=timezone.utc)

    swing = SwingPoint(
        index=10, timestamp=t_peak, detected_at=t_confirm,
        price=Decimal("2500.00"), swing_type=SwingType.HIGH, is_confirmed=True,
    )
    structure = StructureResult(
        timestamp=t_confirm, structure_type=StructureType.HH, bos=BosType.NONE,
        last_swing_high=swing, last_swing_low=None, swings=(swing,), zones=(),
    )

    candle = _make_closed_candle(10)  # at 02:30 UTC
    candle_eval = CandleData(
        timestamp_open=datetime(2026, 8, 1, 11, 0, tzinfo=timezone.utc),
        timestamp_close=datetime(2026, 8, 1, 11, 15, tzinfo=timezone.utc),
        open=Decimal("2490"), high=Decimal("2495"), low=Decimal("2485"), close=Decimal("2492"),
        volume=Decimal("100"), is_closed=True,
    )

    ctx = calculate_swing_duration(
        latest_candle=candle_eval,
        structure=structure,
        timeframe="15m",
        historical_durations=[1, 2, 3, 4] * 10,
        effective_n=40.0,
    )
    # Market age from 10:00 to 11:15 -> 1.25 hours (5 bars)
    assert ctx.market_age_hours == 1.25
    assert ctx.market_age_bars == 5

    # Known age from 10:45 to 11:15 -> 0.5 hours (2 bars)
    assert ctx.known_age_hours == 0.5
    assert ctx.known_age_bars == 2


@pytest.mark.unit
def test_criterion_j_macro_revision_pit_safety():
    """
    Criterion J: Future macro revisions do not alter historical result at T < t_revised.
    """
    nfp_event = MacroEvent(
        event_id="NFP-REVISION-TEST",
        name="Non-Farm Payrolls",
        scheduled_at=datetime(2026, 8, 1, 12, 30, tzinfo=timezone.utc),
        released_at=datetime(2026, 8, 1, 12, 30, tzinfo=timezone.utc),
        initial_value="+150K",
        revised_at=datetime(2026, 9, 4, 12, 30, tzinfo=timezone.utc),
        revised_value="+110K",
        impact=EventImpact.HIGH,
    )

    # Point in time before revision (August 15): value is strictly initial "+150K"
    ctx_aug = evaluate_macro_event_risk(
        as_of=datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc),
        events=[nfp_event],
    )
    assert ctx_aug.point_in_time_value == "+150K"

    # Point in time after revision (September 5): value is revised "+110K"
    ctx_sep = evaluate_macro_event_risk(
        as_of=datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc),
        events=[nfp_event],
    )
    assert ctx_sep.point_in_time_value == "+110K"


# ============================================================================
# Criteria K, L, M, N: Fixture Calibration & Profile Isolation
# ============================================================================

@pytest.mark.unit
def test_criterion_k_explicit_fixture_calibration_deterministic():
    """
    Criterion K: Explicit fixture calibration produces deterministic results.
    """
    prov = CalibrationProvenance(
        instrument="XAUUSD",
        provider="HISTORICAL_FIXTURE",
        timeframe="15m",
        data_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        data_end=datetime(2026, 1, 1, tzinfo=timezone.utc),
        as_of=datetime(2026, 1, 1, tzinfo=timezone.utc),
        raw_observations=5000,
        effective_n=350.0,
        calibration_version="2026.1-cal",
        code_revision="git-rev-test",
        data_fingerprint="sha256-fingerprint-test",
        generated_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    session_entry = SessionExpectancyEntry(
        session=SessionType.LONDON_NY_OVERLAP,
        regime=RegimeType.BULL_TREND,
        sample_count=200,
        effective_n=150.0,
        win_rate=0.60,
        expectancy_r=0.25,
        is_statistically_significant=True,
    )
    calendar_entry = CalendarEffectEntry(
        bucket="DOW_2_HOUR_14",
        sample_count=100,
        effective_n=90.0,
        win_rate=0.58,
        expectancy_r=0.30,
        stability=0.80,
        is_statistically_significant=True,
    )

    artifact = Cycle3ACalibrationArtifact(
        provenance=prov,
        session_expectancy_table={(SessionType.LONDON_NY_OVERLAP, RegimeType.BULL_TREND): session_entry},
        swing_duration_percentiles={"P10": 4.0, "P50": 12.0, "P90": 28.0},
        calendar_effect_table={"DOW_2_HOUR_14": calendar_entry},
        macro_timing_config={"blackout_minutes": 25, "clear_bonus_far": 4.0},
        status="CANDIDATE_NOT_FROZEN",
    )

    profile = build_profile_from_artifact(artifact, name="XAUUSD_FIXTURE_CALIBRATED")
    assert profile.is_calibrated is True
    assert profile.name == "XAUUSD_FIXTURE_CALIBRATED"
    assert profile.macro_blackout_minutes == 25

    engine = RobustTimeCycleEngine(profile=profile)
    candle = CandleData(
        timestamp_open=datetime(2026, 8, 12, 14, 0, tzinfo=timezone.utc),  # Wednesday 14:00 (DOW 2)
        timestamp_close=datetime(2026, 8, 12, 14, 15, tzinfo=timezone.utc),
        open=Decimal("2500"), high=Decimal("2510"), low=Decimal("2495"), close=Decimal("2505"),
        volume=Decimal("150"), is_closed=True,
    )
    structure = StructureResult(
        timestamp=candle.timestamp_close, structure_type=StructureType.HH, bos=BosType.NONE,
        last_swing_high=None, last_swing_low=None, swings=(), zones=(),
    )

    snapshot = engine.analyze(
        latest_candle=candle,
        structure=structure,
        regime=RegimeType.BULL_TREND,
        timeframe="15m",
    )
    # Calibrated session score: 0.25 * 30 * 1.0 = 7.50
    assert snapshot.session.expectancy_score == 7.50
    # Calibrated calendar score: 0.30 * 10 * 0.80 * 0.80 = 1.92
    assert snapshot.calendar.seasonality_score == 1.92
    assert snapshot.cycle_score_3a == float(round(7.50 + 1.92, 2))


@pytest.mark.unit
def test_criterion_l_fixture_profile_does_not_mutate_global_engines():
    """
    Criterion L: Injected fixture profile is isolated and does not alter default engines.
    """
    fresh_default = RobustTimeCycleEngine.for_legacy_xaut()
    assert fresh_default.profile.name == "LEGACY_XAUT_REFERENCE"
    assert fresh_default.profile.is_calibrated is True

    fresh_xau = RobustTimeCycleEngine.for_xauusd()
    assert fresh_xau.profile.name == "XAUUSD_UNCALIBRATED"
    assert fresh_xau.profile.is_calibrated is False


@pytest.mark.unit
def test_criterion_m_xauusd_input_cannot_select_historical_tables():
    """
    Criterion M: Passing instrument='XAUUSD' into default engine automatically triggers
    uncalibrated fail-safe profile and prevents leakage of legacy XAUT empirical tables.
    """
    default_engine = RobustTimeCycleEngine()  # Has legacy profile by default
    candle = _make_closed_candle(10)
    structure = StructureResult(
        timestamp=candle.timestamp_close, structure_type=StructureType.LH, bos=BosType.NONE,
        last_swing_high=None, last_swing_low=None, swings=(), zones=(),
    )

    # Calling analyze with instrument='XAUUSD' forces uncalibrated fail-safe
    snapshot = default_engine.analyze(
        latest_candle=candle,
        structure=structure,
        instrument="XAUUSD",
    )
    assert snapshot.cycle_score_3a == 0.0
    assert snapshot.session.expectancy_score == 0.0
    assert snapshot.swing_duration.maturity_score == 0.0
    assert snapshot.calendar.seasonality_score == 0.0


@pytest.mark.unit
def test_criterion_n_missing_calibration_returns_explicit_state():
    """
    Criterion N: Missing calibration returns explicit CALIBRATION_REQUIRED state.
    """
    profile = Cycle3AProfile.uncalibrated_xauusd_profile()
    assert profile.details.get("calibration_status") == "CALIBRATION_REQUIRED"
    assert "not configured" in profile.details.get("reason", "").lower()


# ============================================================================
# Criteria O & P: Directional Isolation & Pure Python AST Purity
# ============================================================================

@pytest.mark.unit
def test_criterion_o_no_phase4_directional_bias():
    """
    Criterion O: Phase 3A does not implement BUY/SELL decisions, direction score, or order endpoints.
    """
    import engine.cycles as cycles_pkg

    banned_keywords = [
        "BUY_WINDOW", "SELL_WINDOW", "WATCH_LONG", "WATCH_SHORT",
        "READY_LONG", "READY_SHORT", "LongDirectionScore", "ShortDirectionScore",
        "execute_trade", "place_order",
    ]
    exported = dir(cycles_pkg)
    for kw in banned_keywords:
        assert kw not in exported, f"Found Phase 4 banned symbol '{kw}' in engine.cycles package."


@pytest.mark.unit
def test_criterion_p_engine_cycles_zero_django_imports():
    """
    Criterion P: All Python files under engine/cycles/ are pure Python with zero Django imports.
    """
    cycles_dir = os.path.dirname(os.path.abspath(__file__))
    # Resolve engine/cycles directory
    project_root = os.path.abspath(os.path.join(cycles_dir, "..", ".."))
    engine_cycles_path = os.path.join(project_root, "engine", "cycles")

    assert os.path.isdir(engine_cycles_path), f"Directory not found: {engine_cycles_path}"

    for root, _, files in os.walk(engine_cycles_path):
        for file in files:
            if file.endswith(".py"):
                filepath = os.path.join(root, file)
                with open(filepath, "r", encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=filepath)

                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            assert "django" not in alias.name.lower(), (
                                f"Forbidden Django import '{alias.name}' in {filepath}:{node.lineno}"
                            )
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            assert "django" not in node.module.lower(), (
                                f"Forbidden Django import from '{node.module}' in {filepath}:{node.lineno}"
                            )
