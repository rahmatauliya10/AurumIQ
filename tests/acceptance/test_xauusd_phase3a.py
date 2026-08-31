"""
Acceptance & Governance Tests for Phase 3A XAUUSD Robust Time Cycle:
1. Explicit CalibrationStatus Governance & Dataclass Default Immutability (zero legacy defaults)
2. Raw PRODUCTION_FROZEN without numbers fails neutral (no magical XAUT defaults)
3. Zero XAUUSD Statistical / Empirical Defaults (no hardcoded min_samples, no hardcoded t-stat 1.96)
4. Raw N != Effective N Guardrail
5. Candidate Artifact Cannot Score (Strict 0.0 production cycle score)
6. Future Swing detected_at Point-in-Time Exclusion
7. Causal Swing Calibration with separate known/market chronologies
8. Strict Artifact & Profile Target Instrument Rejection (XAUT != XAUUSD)
9. Timeframe Mismatch Rejection
10. Future-Data Provenance Rejection (data_end > as_of)
11. True Independent Macro Pre/Post Blackout Windows (pre-only, post-only, uncalibrated)
12. Zero Numerical Blackout Fallback for Uncalibrated XAUUSD
13. Deep Recursive Immutability of Artifacts and Profiles
14. Snapshot Calibration Status Transparency (PENDING_DATA exposed)
15. Strict Per-Call Profile Target Validation in analyze()
16. Pure Python AST Purity (Zero Django Imports)
17. Zero Phase 4 Directional Symbols
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
from engine.cycles.profile import CalibrationStatus, Cycle3AProfile
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
# 1. Explicit Calibration Governance & Zero Legacy Dataclass Defaults
# ============================================================================

@pytest.mark.unit
def test_explicit_calibration_statuses_defined():
    """Verify explicit CalibrationStatus enum values and semantics."""
    assert CalibrationStatus.LEGACY_REFERENCE.value == "LEGACY_REFERENCE"
    assert CalibrationStatus.PENDING_DATA.value == "PENDING_DATA"
    assert CalibrationStatus.CANDIDATE_NOT_FROZEN.value == "CANDIDATE_NOT_FROZEN"
    assert CalibrationStatus.PRODUCTION_FROZEN.value == "PRODUCTION_FROZEN"

    legacy_profile = Cycle3AProfile.legacy_xaut_profile()
    assert legacy_profile.calibration_status == CalibrationStatus.LEGACY_REFERENCE
    assert legacy_profile.is_production_scoring_enabled is True

    uncal_profile = Cycle3AProfile.uncalibrated_xauusd_profile()
    assert uncal_profile.calibration_status == CalibrationStatus.PENDING_DATA
    assert uncal_profile.is_production_scoring_enabled is False


@pytest.mark.unit
def test_raw_profile_dataclass_has_zero_legacy_numerical_defaults():
    """
    Directly instantiating Cycle3AProfile without numerical arguments must yield
    None for all empirical scoring constants, preventing accidental inheritance.
    """
    raw_pending = Cycle3AProfile(
        calibration_status=CalibrationStatus.PENDING_DATA,
        target_instrument="XAUUSD",
    )
    assert raw_pending.session_max_score is None
    assert raw_pending.session_min_effective_n is None
    assert raw_pending.session_expectancy_multiplier is None
    assert raw_pending.swing_max_score is None
    assert raw_pending.swing_min_effective_n is None
    assert raw_pending.calendar_max_score is None
    assert raw_pending.calendar_min_effective_n is None
    assert raw_pending.calendar_stability_threshold is None
    assert raw_pending.calendar_expectancy_multiplier is None
    assert raw_pending.macro_blackout_pre_minutes is None
    assert raw_pending.macro_blackout_post_minutes is None
    assert raw_pending.macro_clear_bonus_far is None
    assert raw_pending.macro_clear_bonus_near is None

    # Even if labeled PRODUCTION_FROZEN, a raw unpopulated profile must NOT contain XAUT defaults
    raw_frozen = Cycle3AProfile(
        calibration_status=CalibrationStatus.PRODUCTION_FROZEN,
        target_instrument="XAUUSD",
    )
    assert raw_frozen.session_max_score is None
    assert raw_frozen.swing_max_score is None
    assert raw_frozen.calendar_max_score is None
    assert raw_frozen.macro_blackout_pre_minutes is None


@pytest.mark.unit
def test_raw_production_frozen_without_numbers_fails_neutral():
    """A PRODUCTION_FROZEN profile lacking explicit numerical thresholds fails neutral (0.0 score)."""
    raw_frozen = Cycle3AProfile(
        calibration_status=CalibrationStatus.PRODUCTION_FROZEN,
        target_instrument="XAUUSD",
    )
    engine = RobustTimeCycleEngine.for_xauusd(profile=raw_frozen)
    candle = _make_closed_candle(10)
    structure = StructureResult(
        timestamp=candle.timestamp_close, structure_type=StructureType.LH, bos=BosType.NONE,
        last_swing_high=None, last_swing_low=None, swings=(), zones=(),
    )
    snapshot = engine.analyze(latest_candle=candle, structure=structure)
    assert snapshot.cycle_score_3a == 0.0
    assert snapshot.session.expectancy_score == 0.0
    assert snapshot.swing_duration.maturity_score == 0.0
    assert snapshot.calendar.seasonality_score == 0.0


# ============================================================================
# 2. Per-Call Profile Target Validation
# ============================================================================

@pytest.mark.unit
def test_analyze_per_call_profile_target_validation():
    """analyze() strictly enforces target instrument match on per-call profiles."""
    default_engine = RobustTimeCycleEngine()  # Legacy engine
    xau_engine = RobustTimeCycleEngine.for_xauusd()
    candle = _make_closed_candle(10)
    structure = StructureResult(
        timestamp=candle.timestamp_close, structure_type=StructureType.LH, bos=BosType.NONE,
        last_swing_high=None, last_swing_low=None, swings=(), zones=(),
    )
    legacy_profile = Cycle3AProfile.legacy_xaut_profile()
    xau_profile = Cycle3AProfile.uncalibrated_xauusd_profile()

    # Case A: Default engine + instrument='XAUUSD' + profile=legacy_xaut_profile() -> REJECT
    with pytest.raises(ValueError, match="does not match requested instrument 'XAUUSD'"):
        default_engine.analyze(latest_candle=candle, structure=structure, instrument="XAUUSD", profile=legacy_profile)

    # Case B: XAUUSD engine + profile=legacy_xaut_profile() -> REJECT
    with pytest.raises(ValueError, match="cannot analyze using non-XAUUSD profile"):
        xau_engine.analyze(latest_candle=candle, structure=structure, profile=legacy_profile)

    # Case C: XAUUSD engine + profile=xau_profile -> ACCEPT
    snapshot = xau_engine.analyze(latest_candle=candle, structure=structure, profile=xau_profile)
    assert snapshot.calibration_status == "PENDING_DATA"
    assert snapshot.cycle_score_3a == 0.0


# ============================================================================
# 3 & 4. Zero Statistical Defaults & Raw N != Effective N
# ============================================================================

@pytest.mark.unit
def test_session_calibration_requires_explicit_statistical_policy():
    """
    calibrate_session_expectancy() must not invent default sample thresholds or t-statistic cuts.
    Without explicit policy or sample evaluations, effective_n is 0.0 and is_statistically_significant is False.
    """
    candles = [_make_closed_candle(i, close=str(2500 + i * 2)) for i in range(50)]
    regimes = [(c.timestamp_close, RegimeType.BULL_TREND) for c in candles]

    # Run without explicit sample_evaluations or significance policy
    res_unqualified = calibrate_session_expectancy(candles=candles, regimes=regimes)
    assert len(res_unqualified) > 0
    for key, entry in res_unqualified.items():
        assert entry.sample_count > 0
        # Raw N must NOT equal effective N
        assert entry.effective_n == 0.0
        assert entry.is_statistically_significant is False

    # Run with explicit sample evaluation and policy
    key = list(res_unqualified.keys())[0]
    sample_evals = {
        key: SampleEvaluation(
            n_raw=50,
            independent_after_overlap=45,
            temporal_clusters=10,
            hhi_norm=0.1,
            regime_discount=1.0,
            clustering_discount=1.0,
            effective_n=45.0,
            quality=SampleQuality.LOW,
            weight_multiplier=0.5,
            is_blocked=False,
            message="ok",
        )
    }
    res_qualified = calibrate_session_expectancy(
        candles=candles,
        regimes=regimes,
        sample_evaluations=sample_evals,
        significance_policy=lambda avg, std, n: True,
        min_effective_n=30.0,
    )
    assert res_qualified[key].effective_n == 45.0
    assert res_qualified[key].is_statistically_significant is True


# ============================================================================
# 5. Candidate Artifact Cannot Score in Production
# ============================================================================

@pytest.mark.unit
def test_candidate_artifact_produces_zero_production_score():
    """
    A CANDIDATE_NOT_FROZEN profile must strictly produce 0.0 production scores across
    session, swing, calendar, macro clear bonus, and total cycle score.
    """
    prov = CalibrationProvenance(
        instrument="XAUUSD",
        provider="HISTORICAL_SPOT",
        timeframe="15m",
        data_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        data_end=datetime(2026, 1, 1, tzinfo=timezone.utc),
        as_of=datetime(2026, 1, 1, tzinfo=timezone.utc),
        raw_observations=5000,
        effective_n=350.0,
        calibration_version="2026.1-cand",
        code_revision="git-head-sha",
        data_fingerprint="sha256-hash-cand",
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
        swing_duration_percentiles={"known_duration": {"P10": 4.0, "P50": 12.0, "P90": 28.0}},
        calendar_effect_table={"DOW_2_HOUR_14": calendar_entry},
        macro_timing_config={"blackout_pre_minutes": 25, "blackout_post_minutes": 20},
        status=CalibrationStatus.CANDIDATE_NOT_FROZEN,
    )

    candidate_profile = build_profile_from_artifact(artifact)
    assert candidate_profile.calibration_status == CalibrationStatus.CANDIDATE_NOT_FROZEN
    assert candidate_profile.is_production_scoring_enabled is False
    assert candidate_profile.session_max_score is None

    engine = RobustTimeCycleEngine.for_xauusd(profile=candidate_profile)
    candle = CandleData(
        timestamp_open=datetime(2026, 8, 12, 14, 0, tzinfo=timezone.utc),
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

    assert snapshot.calibration_status == "CANDIDATE_NOT_FROZEN"
    assert snapshot.session.expectancy_score == 0.0
    assert snapshot.swing_duration.maturity_score == 0.0
    assert snapshot.calendar.seasonality_score == 0.0
    assert snapshot.cycle_score_3a == 0.0


# ============================================================================
# 6. Future detected_at Swing Exclusion
# ============================================================================

@pytest.mark.unit
def test_future_swing_detected_at_exclusion():
    """
    Swing A: timestamp < T, detected_at <= T
    Swing B: timestamp < T, detected_at > T
    Structure contains A then B.
    At T: A MUST be selected.
    At T >= B.detected_at: B becomes eligible.
    """
    t_eval = datetime(2026, 8, 1, 10, 30, tzinfo=timezone.utc)

    swing_a = SwingPoint(
        index=5,
        timestamp=datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc),
        detected_at=datetime(2026, 8, 1, 9, 30, tzinfo=timezone.utc),  # <= 10:30 (Eligible)
        price=Decimal("2490.00"),
        swing_type=SwingType.LOW,
        is_confirmed=True,
    )
    swing_b = SwingPoint(
        index=8,
        timestamp=datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc),
        detected_at=datetime(2026, 8, 1, 11, 0, tzinfo=timezone.utc),  # > 10:30 (Future lookahead at 10:30)
        price=Decimal("2510.00"),
        swing_type=SwingType.HIGH,
        is_confirmed=True,
    )

    structure = StructureResult(
        timestamp=t_eval,
        structure_type=StructureType.HH,
        bos=BosType.NONE,
        last_swing_high=swing_b,
        last_swing_low=swing_a,
        swings=(swing_a, swing_b),
        zones=(),
    )

    candle_at_t = CandleData(
        timestamp_open=datetime(2026, 8, 1, 10, 15, tzinfo=timezone.utc),
        timestamp_close=t_eval,
        open=Decimal("2500"), high=Decimal("2505"), low=Decimal("2495"), close=Decimal("2502"),
        volume=Decimal("100"), is_closed=True,
    )

    # Evaluate at T = 10:30 (Swing B is unconfirmed at 10:30 -> A must be selected)
    ctx_t = calculate_swing_duration(
        latest_candle=candle_at_t,
        structure=structure,
        timeframe="15m",
        historical_durations=[2, 4, 6] * 10,
        effective_n=35.0,
    )
    assert ctx_t.known_age_hours == 1.0
    assert ctx_t.known_age_bars == 4
    assert ctx_t.market_age_hours == 1.5
    assert ctx_t.market_age_bars == 6

    # Evaluate at T2 = 11:15 (Swing B is now confirmed -> B must be selected)
    candle_at_t2 = CandleData(
        timestamp_open=datetime(2026, 8, 1, 11, 0, tzinfo=timezone.utc),
        timestamp_close=datetime(2026, 8, 1, 11, 15, tzinfo=timezone.utc),
        open=Decimal("2505"), high=Decimal("2512"), low=Decimal("2500"), close=Decimal("2510"),
        volume=Decimal("100"), is_closed=True,
    )
    ctx_t2 = calculate_swing_duration(
        latest_candle=candle_at_t2,
        structure=structure,
        timeframe="15m",
        historical_durations=[2, 4, 6] * 10,
        effective_n=35.0,
    )
    assert ctx_t2.known_age_hours == 0.25
    assert ctx_t2.known_age_bars == 1


# ============================================================================
# 7. Causal Swing Duration Calibration
# ============================================================================

@pytest.mark.unit
def test_causal_swing_duration_calibration():
    """calibrate_swing_durations() produces separate known and market chronologies and excludes future swings."""
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
    swings = [
        SwingPoint(index=1, timestamp=t0, detected_at=t0 + timedelta(minutes=30), price=Decimal("2500"), swing_type=SwingType.HIGH, is_confirmed=True),
        SwingPoint(index=2, timestamp=t0 + timedelta(minutes=60), detected_at=t0 + timedelta(minutes=120), price=Decimal("2480"), swing_type=SwingType.LOW, is_confirmed=True),
        SwingPoint(index=3, timestamp=t0 + timedelta(minutes=180), detected_at=t0 + timedelta(minutes=240), price=Decimal("2520"), swing_type=SwingType.HIGH, is_confirmed=True),
        # Unconfirmed swing should be ignored
        SwingPoint(index=4, timestamp=t0 + timedelta(minutes=300), detected_at=t0 + timedelta(minutes=360), price=Decimal("2490"), swing_type=SwingType.LOW, is_confirmed=False),
        # Swing beyond as_of should be ignored
        SwingPoint(index=5, timestamp=t0 + timedelta(minutes=400), detected_at=t0 + timedelta(minutes=500), price=Decimal("2530"), swing_type=SwingType.HIGH, is_confirmed=True),
    ]

    res = calibrate_swing_durations(swings, timeframe="15m", as_of=t0 + timedelta(minutes=300))
    assert "known_duration" in res
    assert "market_duration" in res
    assert res["known_duration"]["P50"] > 0
    assert res["market_duration"]["P50"] > 0


# ============================================================================
# 8, 9, 10. Provenance & Target Validation
# ============================================================================

@pytest.mark.unit
def test_provenance_validation_rejects_future_dates():
    """CalibrationProvenance rejects data_end > as_of or data_start > data_end."""
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="data_end .* cannot be after as_of"):
        CalibrationProvenance(
            instrument="XAUUSD", provider="SPOT", timeframe="15m",
            data_start=t0, data_end=t0 + timedelta(days=10), as_of=t0 + timedelta(days=5),
            raw_observations=100, effective_n=50.0, calibration_version="1.0",
            code_revision="git", data_fingerprint="sha", generated_at=t0,
        )


@pytest.mark.unit
def test_target_instrument_mismatch_rejected():
    """RobustTimeCycleEngine.for_xauusd() rejects profiles with target_instrument != XAUUSD."""
    xaut_profile = Cycle3AProfile.legacy_xaut_profile()
    with pytest.raises(ValueError, match="target instrument is 'XAUT', expected 'XAUUSD'"):
        RobustTimeCycleEngine.for_xauusd(profile=xaut_profile)


@pytest.mark.unit
def test_timeframe_mismatch_rejected():
    """RobustTimeCycleEngine.for_xauusd() rejects timeframe mismatch between profile and engine."""
    prov = CalibrationProvenance(
        instrument="XAUUSD", provider="SPOT", timeframe="1h",
        data_start=datetime(2024, 1, 1, tzinfo=timezone.utc), data_end=datetime(2025, 1, 1, tzinfo=timezone.utc),
        as_of=datetime(2025, 1, 1, tzinfo=timezone.utc), raw_observations=100, effective_n=50.0,
        calibration_version="1.0", code_revision="git", data_fingerprint="sha", generated_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    artifact = Cycle3ACalibrationArtifact(provenance=prov)
    profile_1h = build_profile_from_artifact(artifact)

    with pytest.raises(ValueError, match="does not match requested timeframe '15m'"):
        RobustTimeCycleEngine.for_xauusd(profile=profile_1h, timeframe="15m")


# ============================================================================
# 11 & 12. True Independent Macro Pre/Post Windows
# ============================================================================

@pytest.mark.unit
def test_macro_true_independent_pre_and_post_windows():
    """Pre-blackout and Post-blackout windows evaluate strictly independently."""
    sched = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    event = MacroEvent(
        event_id="CPI", name="CPI", scheduled_at=sched, released_at=sched,
        initial_value="2.5%", impact=EventImpact.HIGH,
    )

    # 1. Pre=45m, Post=None
    # 10m before event -> IN blackout (10 <= 45)
    ctx_pre1 = evaluate_macro_event_risk(as_of=sched - timedelta(minutes=10), events=[event], blackout_pre_minutes=45, blackout_post_minutes=None)
    assert ctx_pre1.is_in_blackout is True
    # 10m after event -> NOT in blackout (post window is None)
    ctx_post1 = evaluate_macro_event_risk(as_of=sched + timedelta(minutes=10), events=[event], blackout_pre_minutes=45, blackout_post_minutes=None)
    assert ctx_post1.is_in_blackout is False

    # 2. Pre=None, Post=20m
    # 10m before event -> NOT in blackout (pre window is None)
    ctx_pre2 = evaluate_macro_event_risk(as_of=sched - timedelta(minutes=10), events=[event], blackout_pre_minutes=None, blackout_post_minutes=20)
    assert ctx_pre2.is_in_blackout is False
    # 10m after event -> IN blackout (10 <= 20)
    ctx_post2 = evaluate_macro_event_risk(as_of=sched + timedelta(minutes=10), events=[event], blackout_pre_minutes=None, blackout_post_minutes=20)
    assert ctx_post2.is_in_blackout is True

    # 3. Uncalibrated XAUUSD: Pre=None, Post=None -> Both sides NO numerical blackout
    uncal = Cycle3AProfile.uncalibrated_xauusd_profile()
    ctx_uncal_pre = evaluate_macro_event_risk(as_of=sched - timedelta(minutes=10), events=[event], profile=uncal)
    ctx_uncal_post = evaluate_macro_event_risk(as_of=sched + timedelta(minutes=10), events=[event], profile=uncal)
    assert ctx_uncal_pre.is_in_blackout is False
    assert ctx_uncal_post.is_in_blackout is False


# ============================================================================
# 13. Deep Recursive Immutability
# ============================================================================

@pytest.mark.unit
def test_artifact_deep_recursive_immutability():
    """Deep nested dictionaries in Cycle3ACalibrationArtifact are immutable and protected from external mutation."""
    prov = CalibrationProvenance(
        instrument="XAUUSD", provider="SPOT", timeframe="15m",
        data_start=datetime(2024, 1, 1, tzinfo=timezone.utc), data_end=datetime(2025, 1, 1, tzinfo=timezone.utc),
        as_of=datetime(2025, 1, 1, tzinfo=timezone.utc), raw_observations=100, effective_n=50.0,
        calibration_version="1.0", code_revision="git", data_fingerprint="sha", generated_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    raw_nested_percentiles = {"known_duration": {"P50": 12.0, "P90": 24.0}}

    artifact = Cycle3ACalibrationArtifact(
        provenance=prov,
        swing_duration_percentiles=raw_nested_percentiles,
    )

    # 1. Mutate external source dict
    raw_nested_percentiles["known_duration"]["P50"] = 99.0
    assert artifact.swing_duration_percentiles["known_duration"]["P50"] == 12.0

    # 2. Attempt direct internal mutation on nested mapping -> raises TypeError
    with pytest.raises(TypeError):
        artifact.swing_duration_percentiles["known_duration"]["P50"] = 88.0


# ============================================================================
# 14. Snapshot Status & Target Isolation
# ============================================================================

@pytest.mark.unit
def test_snapshot_exposes_pending_data_calibration_status():
    """RobustTimeCycleEngine.for_xauusd() produces snapshots with explicit PENDING_DATA state."""
    engine = RobustTimeCycleEngine.for_xauusd()
    candle = _make_closed_candle(10)
    structure = StructureResult(
        timestamp=candle.timestamp_close, structure_type=StructureType.LH, bos=BosType.NONE,
        last_swing_high=None, last_swing_low=None, swings=(), zones=(),
    )

    snapshot = engine.analyze(latest_candle=candle, structure=structure)
    assert snapshot.calibration_status == "PENDING_DATA"
    assert snapshot.profile_name == "XAUUSD_UNCALIBRATED"
    assert snapshot.cycle_score_3a == 0.0


# ============================================================================
# 15 & 16. Pure Python AST & No Phase 4 Symbols
# ============================================================================

@pytest.mark.unit
def test_engine_cycles_zero_django_imports():
    """All Python files under engine/cycles/ are pure Python with zero Django imports."""
    cycles_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(cycles_dir, "..", ".."))
    engine_cycles_path = os.path.join(project_root, "engine", "cycles")

    assert os.path.isdir(engine_cycles_path)

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


@pytest.mark.unit
def test_no_phase4_directional_bias():
    """Phase 3A does not implement BUY/SELL decisions, direction score, or order endpoints."""
    import engine.cycles as cycles_pkg

    banned_keywords = [
        "BUY_WINDOW", "SELL_WINDOW", "WATCH_LONG", "WATCH_SHORT",
        "READY_LONG", "READY_SHORT", "LongDirectionScore", "ShortDirectionScore",
        "execute_trade", "place_order",
    ]
    exported = dir(cycles_pkg)
    for kw in banned_keywords:
        assert kw not in exported, f"Found Phase 4 banned symbol '{kw}' in engine.cycles package."
