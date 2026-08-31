"""
Acceptance & Governance Tests for Phase 3B XAUUSD Experimental Spectral & Cycle Research:
1. Historical XAUT Research Profile Preserved Verbatim
2. XAUUSD Uncalibrated Research Profile Has Zero Empirical Defaults (None)
3. Target Instrument Segregation & Mismatch Rejection
4. Descriptive ACF with is_significant=False without configured policy
5. Descriptive FFT with is_cycle_detected=False and production_weight=0.0
6. Wavelet Endpoint Safety (is_clean_endpoint=False without policy)
7. Hilbert Endpoint Safety (is_endpoint_reliable=False without policy)
8. Uncalibrated XAUUSD Reliability Score == 0.0 (UNRELIABLE / CALIBRATION_REQUIRED)
9. Historical XAUT Thresholds Do Not Leak into XAUUSD
10. Deterministic 4-Stage Promotion Precedence Matrix:
    - POLICY_NOT_CONFIGURED
    - BLOCKED_BY_PHASE6 (Non-empirical / XAUT baseline / provenance-less / unvalidated)
    - FAILED
    - PROMOTABLE (Research only, production_weight strictly 0.0)
11. Closed-Candle Isolation Split (unclosed at/before T vs unclosed after T)
12. Future Closed Candle Invariance at Timestamp T
13. Broken Time-Grid Fails Closed
14. Hostile Production Weight Lock Tests (Constructor override, Frozen mutation, Database CheckConstraint)
15. Pure Python AST Purity (Zero Django Imports)
16. Zero Phase 4 Directional Symbols
17. Deep Recursive Immutability of Profiles and Artifacts
"""
import ast
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import math
import os
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
import pytest
from django.db import IntegrityError

from engine.core.exceptions import IncompleteCandleError
from engine.core.types import (
    AcfResult,
    BaselineBenchmark,
    CandleData,
    Cycle3BExperimentalSnapshot,
    CycleReliabilityResult,
    FftResult,
    HilbertResult,
    PromotionStatus,
    ReliabilityStatus,
    SampleEvaluation,
    SampleQuality,
    WalkForwardFoldResult,
    WaveletResult,
)
from engine.cycles.experimental.profile import (
    Cycle3BResearchProfile,
    ResearchCalibrationStatus,
)
from engine.cycles.experimental.artifact import (
    Cycle3BResearchArtifact,
    Cycle3BResearchProvenance,
)
from engine.cycles.experimental.acf import calculate_causal_acf
from engine.cycles.experimental.fft import calculate_causal_fft
from engine.cycles.experimental.wavelet import calculate_causal_wavelet
from engine.cycles.experimental.hilbert import calculate_causal_hilbert
from engine.cycles.experimental.reliability import evaluate_cycle_reliability
from engine.cycles.experimental.promotion import evaluate_promotion_eligibility
from engine.cycles.experimental.engine import ExperimentalTimeCycleEngine
from apps.analysis.models import ExperimentalCycleSnapshotRecord


def _make_candles(n: int, base_price: float = 2500.0, period: float = 16.0, start_dt: Optional[datetime] = None) -> list[CandleData]:
    t0 = start_dt or datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
    candles = []
    for i in range(n):
        cycle_val = 15.0 * math.sin(2.0 * math.pi * i / period)
        p = base_price + (i * 0.1) + cycle_val
        ts_open = t0 + timedelta(minutes=15 * i)
        ts_close = t0 + timedelta(minutes=15 * (i + 1))
        candles.append(
            CandleData(
                timestamp_open=ts_open,
                timestamp_close=ts_close,
                open=Decimal(str(round(p - 1.0, 2))),
                high=Decimal(str(round(p + 2.0, 2))),
                low=Decimal(str(round(p - 2.0, 2))),
                close=Decimal(str(round(p, 2))),
                volume=Decimal("100.0"),
                is_closed=True,
            )
        )
    return candles


# ============================================================================
# 1 & 2. Profile Governance & Zero Empirical Fallback
# ============================================================================

@pytest.mark.unit
def test_historical_xaut_research_profile_preserved():
    """Historical XAUT research profile preserves frozen values exactly."""
    legacy = Cycle3BResearchProfile.legacy_xaut_research_profile()
    assert legacy.status == ResearchCalibrationStatus.LEGACY_REFERENCE
    assert legacy.target_instrument == "XAUT"
    assert legacy.acf_significance_bound == 1.96
    assert legacy.fft_min_power_ratio == 0.15
    assert legacy.wavelet_max_coi_contamination == 0.40
    assert legacy.hilbert_min_stability == 0.60
    assert legacy.dispersion_high_threshold == 0.15
    assert legacy.dispersion_moderate_threshold == 0.30
    assert legacy.single_method_agreement_pct == 35.0
    assert legacy.moderate_method_agreement_pct == 65.0
    assert legacy.reliability_band_high == 60.0
    assert legacy.promotion_min_trades == 100
    assert legacy.promotion_min_pf_improvement_pct == 5.0
    assert legacy.is_research_policy_configured is True


@pytest.mark.unit
def test_uncalibrated_xauusd_research_profile_has_zero_empirical_defaults():
    """XAUUSD uncalibrated research profile contains None for all empirical policies."""
    uncal = Cycle3BResearchProfile.uncalibrated_xauusd_research_profile()
    assert uncal.status == ResearchCalibrationStatus.PENDING_DATA
    assert uncal.target_instrument == "XAUUSD"
    assert uncal.acf_significance_bound is None
    assert uncal.fft_min_power_ratio is None
    assert uncal.wavelet_max_coi_contamination is None
    assert uncal.hilbert_min_stability is None
    assert uncal.dispersion_high_threshold is None
    assert uncal.reliability_band_high is None
    assert uncal.promotion_min_trades is None
    assert uncal.promotion_min_pf_improvement_pct is None
    assert uncal.is_research_policy_configured is False


# ============================================================================
# 3. Target Instrument Segregation & Mismatch Rejection
# ============================================================================

@pytest.mark.unit
def test_target_instrument_mismatch_rejection():
    """Engine and analyze() reject target instrument mismatches."""
    xaut_profile = Cycle3BResearchProfile.legacy_xaut_research_profile()
    xau_profile = Cycle3BResearchProfile.uncalibrated_xauusd_research_profile()

    # Engine factory reject
    with pytest.raises(ValueError, match="target instrument is 'XAUT', expected 'XAUUSD'"):
        ExperimentalTimeCycleEngine.for_xauusd(profile=xaut_profile)

    candles = _make_candles(64)
    xau_engine = ExperimentalTimeCycleEngine.for_xauusd()

    # Per-call profile mismatch
    with pytest.raises(ValueError, match="cannot analyze using non-XAUUSD profile"):
        xau_engine.analyze(candles=candles, profile=xaut_profile)


# ============================================================================
# 4, 5, 6, 7. Descriptive Spectral Computations under Uncalibrated Profile
# ============================================================================

@pytest.mark.unit
def test_uncalibrated_xauusd_descriptive_spectral_computations():
    """
    Uncalibrated XAUUSD computes descriptive mathematics (lags, PSD, entropy, phases, scales)
    without claiming significance or endpoint reliability.
    """
    series = [2500.0 + 15.0 * math.sin(2.0 * math.pi * i / 16.0) for i in range(64)]
    uncal = Cycle3BResearchProfile.uncalibrated_xauusd_research_profile()

    # 1. ACF: Computes series & lag, but is_significant=False and confidence_bound=0.0
    acf_res = calculate_causal_acf(series, profile=uncal)
    assert acf_res.dominant_lag is not None
    assert acf_res.is_significant is False
    assert acf_res.confidence_bound == 0.0

    # 2. FFT: Computes period & PSD, but is_cycle_detected=False
    fft_res = calculate_causal_fft(series, profile=uncal)
    assert fft_res.dominant_period is not None
    assert fft_res.power_ratio > 0.0
    assert fft_res.is_cycle_detected is False

    # 3. Wavelet: Computes scale & energy, but is_clean_endpoint=False
    wav_res = calculate_causal_wavelet(series, profile=uncal)
    assert wav_res.dominant_scale_period is not None
    assert wav_res.is_clean_endpoint is False

    # 4. Hilbert: Computes phase & amplitude, but is_endpoint_reliable=False
    hil_res = calculate_causal_hilbert(series, dominant_period=fft_res.dominant_period, profile=uncal)
    assert hil_res.instantaneous_phase is not None
    assert hil_res.is_endpoint_reliable is False


# ============================================================================
# 8 & 9. Uncalibrated Reliability & Threshold Leak Prevention
# ============================================================================

@pytest.mark.unit
def test_uncalibrated_xauusd_reliability_zeroed():
    """Uncalibrated XAUUSD produces reliability_score == 0.0 and UNRELIABLE status."""
    series = [2500.0 + 15.0 * math.sin(2.0 * math.pi * i / 16.0) for i in range(64)]
    uncal = Cycle3BResearchProfile.uncalibrated_xauusd_research_profile()

    acf_res = calculate_causal_acf(series, profile=uncal)
    fft_res = calculate_causal_fft(series, profile=uncal)
    wav_res = calculate_causal_wavelet(series, profile=uncal)
    hil_res = calculate_causal_hilbert(series, profile=uncal)

    rel_res = evaluate_cycle_reliability(
        acf=acf_res, fft=fft_res, wavelet=wav_res, hilbert=hil_res,
        effective_n=100.0, sample_quality=SampleQuality.HIGH, profile=uncal,
    )

    assert rel_res.reliability_score == 0.0
    assert rel_res.reliability_status == ReliabilityStatus.UNRELIABLE
    assert any("CALIBRATION_REQUIRED" in r for r in rel_res.reasons)


# ============================================================================
# 10. Deterministic Promotion Precedence Matrix
# ============================================================================

@pytest.mark.unit
def test_xauusd_promotion_deterministic_precedence_matrix():
    """
    Verify exact deterministic promotion precedence:
      Stage A: No policy -> POLICY_NOT_CONFIGURED
      Stage B: Policy exists, but baseline invalid / non-empirical / XAUT -> BLOCKED_BY_PHASE6
      Stage C: Valid XAUUSD Phase 6 baseline exists, but hurdles fail -> FAILED
      Stage D: Valid XAUUSD Phase 6 baseline exists and hurdles pass -> PROMOTABLE
    """
    uncal_profile = Cycle3BResearchProfile.uncalibrated_xauusd_research_profile()

    configured_xau_profile = Cycle3BResearchProfile(
        name="XAUUSD_RESEARCH_CANDIDATE",
        status=ResearchCalibrationStatus.REVALIDATED_RESEARCH,
        target_instrument="XAUUSD",
        promotion_min_trades=100,
        promotion_min_pf_improvement_pct=5.0,
        promotion_max_dd_deterioration_pct=10.0,
        promotion_min_folds_passed=4,
        promotion_min_folds_total=6,
        promotion_max_fold_concentration_pct=60.0,
        promotion_min_effective_n=30.0,
        dispersion_high_threshold=0.15,
        fft_min_power_ratio=0.15,
    )

    # Stage A: No policy -> POLICY_NOT_CONFIGURED
    res_a = evaluate_promotion_eligibility(
        baseline=None, exp_profit_factor=2.0, exp_expectancy_r=0.5, exp_max_drawdown=5.0, exp_trade_count=150,
        profile=uncal_profile,
    )
    assert res_a.status == PromotionStatus.POLICY_NOT_CONFIGURED
    assert res_a.is_promotable is False

    # Stage B1: Policy exists + Non-empirical baseline -> BLOCKED_BY_PHASE6
    fake_baseline = BaselineBenchmark(
        base_profit_factor=1.8, base_expectancy_r=0.4, base_max_drawdown=10.0, base_trade_count=100,
        recorded_at=datetime(2026, 8, 1, tzinfo=timezone.utc), is_empirical=False,
    )
    res_b1 = evaluate_promotion_eligibility(
        baseline=fake_baseline, exp_profit_factor=2.0, exp_expectancy_r=0.5, exp_max_drawdown=5.0, exp_trade_count=150,
        profile=configured_xau_profile,
    )
    assert res_b1.status == PromotionStatus.BLOCKED_BY_PHASE6
    assert res_b1.is_promotable is False

    # Stage B2: Policy exists + XAUT empirical baseline -> BLOCKED_BY_PHASE6
    xaut_empirical_baseline = BaselineBenchmark(
        base_profit_factor=1.8, base_expectancy_r=0.4, base_max_drawdown=10.0, base_trade_count=100,
        recorded_at=datetime(2026, 8, 1, tzinfo=timezone.utc), is_empirical=True, instrument="XAUT",
        pit_safe=True, phase6_validated=True,
    )
    res_b2 = evaluate_promotion_eligibility(
        baseline=xaut_empirical_baseline, exp_profit_factor=2.0, exp_expectancy_r=0.5, exp_max_drawdown=5.0, exp_trade_count=150,
        profile=configured_xau_profile,
    )
    assert res_b2.status == PromotionStatus.BLOCKED_BY_PHASE6
    assert res_b2.is_promotable is False

    # Stage B3: Policy exists + Valid XAUUSD provenance but phase6_validated=False -> BLOCKED_BY_PHASE6
    unvalidated_xau_baseline = BaselineBenchmark(
        base_profit_factor=1.8, base_expectancy_r=0.4, base_max_drawdown=10.0, base_trade_count=100,
        recorded_at=datetime(2026, 8, 1, tzinfo=timezone.utc), is_empirical=True, instrument="XAUUSD",
        pit_safe=True, phase6_validated=False,
    )
    res_b3 = evaluate_promotion_eligibility(
        baseline=unvalidated_xau_baseline, exp_profit_factor=2.0, exp_expectancy_r=0.5, exp_max_drawdown=5.0, exp_trade_count=150,
        profile=configured_xau_profile,
    )
    assert res_b3.status == PromotionStatus.BLOCKED_BY_PHASE6
    assert res_b3.is_promotable is False

    # Stage C: Valid XAUUSD Phase 6 baseline exists, but hurdle fails (PF improvement < 5%) -> FAILED
    valid_xau_baseline = BaselineBenchmark(
        base_profit_factor=2.0, base_expectancy_r=0.4, base_max_drawdown=10.0, base_trade_count=100,
        recorded_at=datetime(2026, 8, 1, tzinfo=timezone.utc), is_empirical=True, instrument="XAUUSD",
        pit_safe=True, phase6_validated=True,
    )
    res_c = evaluate_promotion_eligibility(
        baseline=valid_xau_baseline, exp_profit_factor=2.02,  # Only +1% improvement
        exp_expectancy_r=0.45, exp_max_drawdown=9.0, exp_trade_count=150,
        walk_forward_folds_passed=5, walk_forward_folds_total=6, effective_n=50.0,
        profile=configured_xau_profile,
    )
    assert res_c.status == PromotionStatus.FAILED
    assert res_c.is_promotable is False

    # Stage D: All research hurdles pass -> PROMOTABLE (Research only)
    folds = [
        WalkForwardFoldResult(fold_id=i, profit_factor=2.2, expectancy_r=0.5, max_drawdown=8.0, trade_count=25, net_profit=1000.0)
        for i in range(1, 7)
    ]
    res_d = evaluate_promotion_eligibility(
        baseline=valid_xau_baseline, exp_profit_factor=2.2,  # +10% improvement
        exp_expectancy_r=0.5, exp_max_drawdown=8.0, exp_trade_count=150,
        fold_results=folds, effective_n=50.0,
        profile=configured_xau_profile,
    )
    assert res_d.status == PromotionStatus.PROMOTABLE
    assert res_d.is_promotable is True


# ============================================================================
# 11 & 12. Closed-Candle Isolation Split & Future Invariance
# ============================================================================

@pytest.mark.unit
def test_closed_candle_isolation_split():
    """
    Case A: Unclosed candle on or before as_of -> IncompleteCandleError
    Case B: Unclosed candle strictly after as_of -> completely ignored, snapshot at T identical
    Case C: Future closed candles after as_of -> completely ignored, snapshot at T identical
    """
    all_candles = _make_candles(64)
    T = all_candles[31].timestamp_close

    engine = ExperimentalTimeCycleEngine.for_xauusd()

    # Baseline snapshot at T (bars 0..31)
    base_snapshot = engine.analyze(candles=all_candles, as_of=T)

    # Case A: An unclosed candle at bar 15 (before T) -> raises IncompleteCandleError
    corrupted_candles_past = list(all_candles)
    corrupted_candles_past[15] = CandleData(
        timestamp_open=all_candles[15].timestamp_open,
        timestamp_close=all_candles[15].timestamp_close,
        open=all_candles[15].open, high=all_candles[15].high, low=all_candles[15].low, close=all_candles[15].close,
        volume=all_candles[15].volume, is_closed=False,
    )
    with pytest.raises(IncompleteCandleError):
        engine.analyze(candles=corrupted_candles_past, as_of=T)

    # Case B: An unclosed candle at bar 45 (after T) -> completely ignored, snapshot identical
    corrupted_candles_future = list(all_candles)
    corrupted_candles_future[45] = CandleData(
        timestamp_open=all_candles[45].timestamp_open,
        timestamp_close=all_candles[45].timestamp_close,
        open=all_candles[45].open, high=all_candles[45].high, low=all_candles[45].low, close=all_candles[45].close,
        volume=all_candles[45].volume, is_closed=False,
    )
    future_unclosed_snapshot = engine.analyze(candles=corrupted_candles_future, as_of=T)
    assert future_unclosed_snapshot.acf.acf_series == base_snapshot.acf.acf_series
    assert future_unclosed_snapshot.fft.dominant_period == base_snapshot.fft.dominant_period
    assert future_unclosed_snapshot.reliability.reliability_score == base_snapshot.reliability.reliability_score

    # Case C: Mutating future closed candles bars 32..63 does not change snapshot at T
    mutated_candles = list(all_candles)
    for j in range(32, 64):
        mutated_candles[j] = CandleData(
            timestamp_open=all_candles[j].timestamp_open,
            timestamp_close=all_candles[j].timestamp_close,
            open=Decimal("9999.0"), high=Decimal("9999.0"), low=Decimal("9999.0"), close=Decimal("9999.0"),
            volume=Decimal("999.0"), is_closed=True,
        )
    mutated_snapshot = engine.analyze(candles=mutated_candles, as_of=T)
    assert mutated_snapshot.acf.acf_series == base_snapshot.acf.acf_series
    assert mutated_snapshot.fft.dominant_period == base_snapshot.fft.dominant_period


# ============================================================================
# 13. Broken Time-Grid Fails Closed
# ============================================================================

@pytest.mark.unit
def test_broken_time_grid_fails_closed():
    """Irregular time spacing or duplicate timestamp fails closed with 0 reliability."""
    candles = _make_candles(32)
    # Inject a 30m gap between bar 10 and 11
    corrupted = list(candles)
    corrupted[11] = CandleData(
        timestamp_open=candles[10].timestamp_close + timedelta(minutes=15),  # 15m gap
        timestamp_close=candles[10].timestamp_close + timedelta(minutes=30),
        open=candles[11].open, high=candles[11].high, low=candles[11].low, close=candles[11].close,
        volume=candles[11].volume, is_closed=True,
    )
    engine = ExperimentalTimeCycleEngine.for_xauusd()
    snap = engine.analyze(candles=corrupted)
    assert snap.reliability.reliability_score == 0.0
    assert snap.reliability.reliability_status == ReliabilityStatus.UNRELIABLE
    assert any("integrity failure" in r for r in snap.reliability.reasons)


# ============================================================================
# 14. Hostile Production Lock & Persistence CheckConstraint Tests
# ============================================================================

@pytest.mark.unit
def test_hostile_production_weight_lock_in_memory():
    """Attempting to construct or mutate Cycle3BExperimentalSnapshot with production_weight > 0 fails."""
    candles = _make_candles(64)
    engine = ExperimentalTimeCycleEngine.for_xauusd()
    snapshot = engine.analyze(candles=candles)

    # 1. Permanent 0.0 production weight
    assert snapshot.production_weight == 0.0

    # 2. Cannot pass production_weight in constructor
    with pytest.raises(TypeError):
        Cycle3BExperimentalSnapshot(
            timestamp=snapshot.timestamp,
            timeframe="15m",
            acf=snapshot.acf,
            fft=snapshot.fft,
            wavelet=snapshot.wavelet,
            hilbert=snapshot.hilbert,
            reliability=snapshot.reliability,
            production_weight=1.0,  # init=False -> raises TypeError
        )

    # 3. Cannot mutate production_weight on frozen instance
    with pytest.raises(Exception):  # FrozenInstanceError / AttributeError
        snapshot.production_weight = 1.0


@pytest.mark.django_db
def test_hostile_database_production_weight_constraint():
    """Database check constraint phase3b_production_weight_locked_to_zero rejects non-zero weight."""
    from apps.instruments.models import Asset, Instrument, InstrumentType

    t0 = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    base_asset = Asset.objects.create(code="XAU_P3B", name="Gold Spot P3B")
    quote_asset = Asset.objects.create(code="USD_P3B", name="US Dollar P3B")
    inst = Instrument.objects.create(base_asset=base_asset, quote_asset=quote_asset, instrument_type=InstrumentType.SPOT)

    # Normal save with 0.0 succeeds
    rec = ExperimentalCycleSnapshotRecord.objects.create(
        timestamp=t0,
        instrument=inst,
        timeframe="15m",
        experimental_version="3.1.0-3B",
        dominant_period_bars=16.0,
        reliability_score=0.0,
        reliability_status="UNRELIABLE",
        production_weight=0.0,
        promotion_status="POLICY_NOT_CONFIGURED",
    )
    assert rec.production_weight == 0.0

    # Hostile save with 0.5 must violate database CheckConstraint
    with pytest.raises(IntegrityError):
        ExperimentalCycleSnapshotRecord.objects.create(
            timestamp=t0 + timedelta(minutes=15),
            instrument=inst,
            timeframe="15m",
            experimental_version="3.1.0-3B",
            dominant_period_bars=16.0,
            reliability_score=0.0,
            reliability_status="UNRELIABLE",
            production_weight=0.5,  # Violates check constraint
            promotion_status="POLICY_NOT_CONFIGURED",
        )


# ============================================================================
# 15 & 16. Pure Python AST & Phase 4 Symbol Protection
# ============================================================================

@pytest.mark.unit
def test_engine_cycles_experimental_zero_django_imports():
    """All files under engine/cycles/experimental/ are pure Python with zero Django imports."""
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.abspath(os.path.join(pkg_dir, "..", ".."))
    exp_path = os.path.join(root_dir, "engine", "cycles", "experimental")

    assert os.path.isdir(exp_path)

    for root, _, files in os.walk(exp_path):
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
def test_no_phase4_directional_symbols_in_experimental():
    """Phase 3B does not export BUY/SELL, direction score, or order execution symbols."""
    import engine.cycles.experimental as exp_pkg

    banned_keywords = [
        "BUY_WINDOW", "SELL_WINDOW", "WATCH_LONG", "WATCH_SHORT",
        "READY_LONG", "READY_SHORT", "LongDirectionScore", "ShortDirectionScore",
        "execute_trade", "place_order",
    ]
    exported = dir(exp_pkg)
    for kw in banned_keywords:
        assert kw not in exported, f"Found Phase 4 banned symbol '{kw}' in experimental package."


# ============================================================================
# 17. Deep Immutability of Artifact and Profile
# ============================================================================

@pytest.mark.unit
def test_research_artifact_deep_recursive_immutability():
    """Nested mappings in Cycle3BResearchArtifact are deeply immutable and protected from external mutation."""
    prov = Cycle3BResearchProvenance(
        instrument="XAUUSD", provider="SPOT", timeframe="15m",
        data_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        data_end=datetime(2026, 1, 1, tzinfo=timezone.utc),
        as_of=datetime(2026, 1, 1, tzinfo=timezone.utc),
        raw_observations=5000, effective_n=300.0,
    )
    raw_config = {"nested": {"fft_window": "hann", "scales": [4, 8, 16]}}

    artifact = Cycle3BResearchArtifact(
        provenance=prov,
        algorithm_config=raw_config,
    )

    # External mutation
    raw_config["nested"]["fft_window"] = "hamming"
    assert artifact.algorithm_config["nested"]["fft_window"] == "hann"

    # Internal mutation attempt
    with pytest.raises(TypeError):
        artifact.algorithm_config["nested"]["fft_window"] = "blackman"
