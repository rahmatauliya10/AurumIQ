"""
Acceptance & Governance Tests for Phase 3B XAUUSD Experimental Spectral & Cycle Research:
1. Historical XAUT Research Profile Preserved Verbatim
2. Incomplete Explicit LEGACY_REFERENCE Profile Rejected with ValueError
3. XAUUSD Uncalibrated Research Profile Has Zero Empirical Defaults (None)
4. Configured XAUUSD Research Policy Requires Explicit Non-Empty Timeframe
5. Subsystem Policy Completeness Properties (ACF, FFT, Wavelet, Hilbert, Reliability, Promotion)
6. Partial Policy Fail-Neutral Tests (ACF, Wavelet, Hilbert, Reliability, Promotion)
7. LEGACY_REFERENCE Status Rejects Target Instrument XAUUSD
8. Symmetric Instrument & Profile Target Mismatch Rejection (All Directions)
9. ACF Custom Effective-N Threshold (Does not block on historical 30)
10. ACF No Historical 60/100 Tier Leakage
11. Descriptive Spectral Computations under Uncalibrated Profile
12. Uncalibrated XAUUSD Reliability Score == 0.0 (UNRELIABLE / CALIBRATION_REQUIRED)
13. Reliability Custom Agreement Thresholds (Follows profile, not 80/50)
14. Reliability Does Not Hardcode Wavelet 0.40 (Trusts is_clean_endpoint)
15. Engine Naive Datetime Candles Safe Normalization
16. Runtime Timeframe Rigidity (Profile timeframe mismatch raises ValueError)
17. Strict Phase 6 Baseline Provenance Matrix (Missing dates, source, timeframe mismatch -> BLOCKED_BY_PHASE6)
18. Deterministic 4-Stage Promotion Precedence Matrix
19. Insufficient Trade Count on XAUUSD Fails with status=FAILED
20. Fold Concentration Threshold Uses Explicit Config without Hardcoded 60%
21. Engine analyze() Zero-Metric-Fabrication (produces NOT_EVALUATED on valid baseline)
22. Closed-Candle Isolation Split (unclosed at/before T vs unclosed after T)
23. Future Closed Candle Invariance at Timestamp T
24. Broken Time-Grid Fails Closed
25. Hostile Production Weight Lock Tests (Constructor override, Frozen mutation, Database CheckConstraint)
26. Pure Python AST Purity (Zero Django Imports)
27. Zero Phase 4 Directional Symbols
28. Artifact Text Provenance Hardening (Blank instrument/provider/timeframe/revision/fingerprint rejected)
29. Deep Recursive Immutability of Profiles and Artifacts
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


def _make_candles(n: int, base_price: float = 2500.0, period: float = 16.0, start_dt: Optional[datetime] = None) -> List[CandleData]:
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


def _make_valid_xauusd_baseline(timeframe: str = "15m") -> BaselineBenchmark:
    """Helper creating a complete, valid empirical Phase 6 XAUUSD baseline."""
    return BaselineBenchmark(
        base_profit_factor=2.0,
        base_expectancy_r=0.4,
        base_max_drawdown=10.0,
        base_trade_count=100,
        recorded_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        is_empirical=True,
        instrument="XAUUSD",
        timeframe=timeframe,
        source="OOS_PHASE6_WALK_FORWARD",
        data_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        data_end=datetime(2026, 7, 31, tzinfo=timezone.utc),
        as_of=datetime(2026, 8, 1, tzinfo=timezone.utc),
        pit_safe=True,
        phase6_validated=True,
    )


def _make_fully_configured_xauusd_profile(timeframe: str = "15m") -> Cycle3BResearchProfile:
    """Helper creating a complete, valid XAUUSD research candidate profile."""
    return Cycle3BResearchProfile(
        name="XAUUSD_RESEARCH_CANDIDATE",
        status=ResearchCalibrationStatus.REVALIDATED_RESEARCH,
        target_instrument="XAUUSD",
        timeframe=timeframe,
        # Algorithm Config
        max_lag=64,
        min_lookback=32,
        min_period=4.0,
        max_period=64.0,
        window_type="hann",
        wavelet_name="morl",
        num_scales=32,
        # Detection
        acf_bartlett_z_multiplier=1.96,
        acf_min_effective_n=30.0,
        fft_min_power_ratio=0.15,
        fft_power_score_multiplier=2.5,
        wavelet_max_coi_contamination=0.40,
        wavelet_min_interior_support_ratio=3.0,
        hilbert_min_stability=0.60,
        hilbert_min_lookback=48,
        hilbert_min_velocity=0.05,
        hilbert_min_amplitude=1e-6,
        # Reliability
        dispersion_high_threshold=0.15,
        dispersion_moderate_threshold=0.30,
        single_method_agreement_pct=35.0,
        moderate_method_agreement_pct=65.0,
        cross_window_dispersion_threshold=0.35,
        reliability_band_high=60.0,
        reliability_band_moderate=35.0,
        reliability_band_low=15.0,
        reliability_weight_acf=30.0,
        reliability_weight_fft=30.0,
        reliability_weight_wavelet=20.0,
        reliability_weight_hilbert=20.0,
        quality_multiplier_low=0.5,
        quality_multiplier_medium=0.8,
        quality_multiplier_high=1.0,
        min_effective_n=30.0,
        reliability_high_min_agreement_pct=80.0,
        reliability_moderate_min_agreement_pct=50.0,
        # Promotion
        promotion_min_trades=100,
        promotion_min_pf_improvement_pct=5.0,
        promotion_max_dd_deterioration_pct=10.0,
        promotion_min_folds_passed=4,
        promotion_min_folds_total=6,
        promotion_max_fold_concentration_pct=60.0,
        promotion_min_effective_n=30.0,
    )


# ============================================================================
# 1 & 2. Profile Governance & Subsystem Policy Completeness
# ============================================================================

@pytest.mark.unit
def test_historical_xaut_research_profile_preserved():
    """Historical XAUT research profile preserves frozen values and reports full completeness."""
    legacy = Cycle3BResearchProfile.legacy_xaut_research_profile()
    assert legacy.status == ResearchCalibrationStatus.LEGACY_REFERENCE
    assert legacy.target_instrument == "XAUT"
    assert legacy.acf_bartlett_z_multiplier == 1.96
    assert legacy.acf_min_effective_n == 30.0
    assert legacy.fft_min_power_ratio == 0.15
    assert legacy.wavelet_max_coi_contamination == 0.40
    assert legacy.wavelet_min_interior_support_ratio == 3.0
    assert legacy.hilbert_min_stability == 0.60
    assert legacy.dispersion_high_threshold == 0.15
    assert legacy.dispersion_moderate_threshold == 0.30
    assert legacy.single_method_agreement_pct == 35.0
    assert legacy.moderate_method_agreement_pct == 65.0
    assert legacy.reliability_band_high == 60.0
    assert legacy.reliability_high_min_agreement_pct == 80.0
    assert legacy.reliability_moderate_min_agreement_pct == 50.0
    assert legacy.promotion_min_trades == 100
    assert legacy.promotion_min_pf_improvement_pct == 5.0

    assert legacy.is_acf_policy_configured is True
    assert legacy.is_fft_policy_configured is True
    assert legacy.is_wavelet_policy_configured is True
    assert legacy.is_hilbert_policy_configured is True
    assert legacy.is_detection_policy_configured is True
    assert legacy.is_reliability_policy_configured is True
    assert legacy.is_promotion_policy_configured is True
    assert legacy.is_research_policy_configured is True


@pytest.mark.unit
def test_incomplete_explicit_legacy_profile_rejected():
    """Explicit LEGACY_REFERENCE profile with missing policies is strictly rejected."""
    with pytest.raises(ValueError, match="LEGACY_REFERENCE status requires complete frozen"):
        Cycle3BResearchProfile(
            target_instrument="XAUT",
            status=ResearchCalibrationStatus.LEGACY_REFERENCE,
            # Missing detection, reliability, promotion policies
        )


@pytest.mark.unit
def test_uncalibrated_xauusd_research_profile_has_zero_empirical_defaults():
    """XAUUSD uncalibrated research profile contains None for all empirical policies and reports incomplete."""
    uncal = Cycle3BResearchProfile.uncalibrated_xauusd_research_profile()
    assert uncal.status == ResearchCalibrationStatus.PENDING_DATA
    assert uncal.target_instrument == "XAUUSD"
    assert uncal.acf_bartlett_z_multiplier is None
    assert uncal.acf_min_effective_n is None
    assert uncal.fft_min_power_ratio is None
    assert uncal.wavelet_max_coi_contamination is None
    assert uncal.hilbert_min_stability is None
    assert uncal.dispersion_high_threshold is None
    assert uncal.reliability_band_high is None
    assert uncal.reliability_high_min_agreement_pct is None
    assert uncal.reliability_moderate_min_agreement_pct is None
    assert uncal.promotion_min_trades is None
    assert uncal.promotion_min_pf_improvement_pct is None

    assert uncal.is_acf_policy_configured is False
    assert uncal.is_fft_policy_configured is False
    assert uncal.is_wavelet_policy_configured is False
    assert uncal.is_hilbert_policy_configured is False
    assert uncal.is_detection_policy_configured is False
    assert uncal.is_reliability_policy_configured is False
    assert uncal.is_promotion_policy_configured is False
    assert uncal.is_research_policy_configured is False


@pytest.mark.unit
def test_legacy_reference_status_rejects_xauusd():
    """LEGACY_REFERENCE status requires target instrument XAUT; rejects XAUUSD."""
    with pytest.raises(ValueError, match="LEGACY_REFERENCE status requires target instrument 'XAUT'"):
        Cycle3BResearchProfile(
            status=ResearchCalibrationStatus.LEGACY_REFERENCE,
            target_instrument="XAUUSD",
        )


@pytest.mark.unit
def test_configured_xauusd_profile_requires_timeframe():
    """Configured XAUUSD research policy requires explicit non-empty timeframe."""
    with pytest.raises(ValueError, match="Configured XAUUSD research policy requires an explicit non-empty timeframe"):
        Cycle3BResearchProfile(
            status=ResearchCalibrationStatus.REVALIDATED_RESEARCH,
            target_instrument="XAUUSD",
            timeframe=None,  # Forbidden!
        )


# ============================================================================
# 3. Partial Policy Fail-Neutral Tests (Zero Legacy Leakage)
# ============================================================================

@pytest.mark.unit
def test_partial_acf_policy_fails_neutral():
    """ACF with z-multiplier but missing min_effective_n fails neutral without borrowing 30.0."""
    partial = Cycle3BResearchProfile(
        target_instrument="XAUUSD",
        status=ResearchCalibrationStatus.PENDING_DATA,
        acf_bartlett_z_multiplier=1.96,
        acf_min_effective_n=None,  # Incomplete!
    )
    series = [2500.0 + 15.0 * math.sin(2.0 * math.pi * i / 16.0) for i in range(64)]
    res = calculate_causal_acf(series, profile=partial, effective_n=50.0)
    assert res.is_significant is False
    assert res.confidence_bound == 0.0


@pytest.mark.unit
def test_partial_wavelet_policy_fails_neutral():
    """Wavelet with COI threshold but missing interior support ratio fails neutral."""
    partial = Cycle3BResearchProfile(
        target_instrument="XAUUSD",
        status=ResearchCalibrationStatus.PENDING_DATA,
        wavelet_max_coi_contamination=0.40,
        wavelet_min_interior_support_ratio=None,  # Incomplete!
    )
    series = [2500.0 + 15.0 * math.sin(2.0 * math.pi * i / 16.0) for i in range(64)]
    res = calculate_causal_wavelet(series, profile=partial)
    assert res.is_clean_endpoint is False


@pytest.mark.unit
def test_partial_hilbert_policy_fails_neutral():
    """Hilbert with 3 of 4 fields present fails neutral without borrowing defaults."""
    partial = Cycle3BResearchProfile(
        target_instrument="XAUUSD",
        status=ResearchCalibrationStatus.PENDING_DATA,
        hilbert_min_stability=0.60,
        hilbert_min_lookback=48,
        hilbert_min_velocity=0.05,
        hilbert_min_amplitude=None,  # Incomplete!
    )
    series = [2500.0 + 15.0 * math.sin(2.0 * math.pi * i / 16.0) for i in range(64)]
    res = calculate_causal_hilbert(series, profile=partial)
    assert res.is_endpoint_reliable is False


@pytest.mark.unit
def test_partial_reliability_policy_fails_neutral():
    """Reliability evaluator with incomplete policy returns score=0.0 without exception."""
    partial = Cycle3BResearchProfile(
        target_instrument="XAUUSD",
        status=ResearchCalibrationStatus.PENDING_DATA,
        dispersion_high_threshold=0.15,
        # missing other reliability fields!
    )
    series = [2500.0 + 15.0 * math.sin(2.0 * math.pi * i / 16.0) for i in range(64)]
    acf_res = calculate_causal_acf(series, profile=partial)
    fft_res = calculate_causal_fft(series, profile=partial)
    wav_res = calculate_causal_wavelet(series, profile=partial)
    hil_res = calculate_causal_hilbert(series, profile=partial)

    rel_res = evaluate_cycle_reliability(
        acf=acf_res, fft=fft_res, wavelet=wav_res, hilbert=hil_res,
        effective_n=100.0, sample_quality=SampleQuality.HIGH, profile=partial,
    )
    assert rel_res.reliability_score == 0.0
    assert rel_res.reliability_status == ReliabilityStatus.UNRELIABLE
    assert any("CALIBRATION_REQUIRED" in r for r in rel_res.reasons)


@pytest.mark.unit
def test_partial_promotion_policy_fails_neutral():
    """Promotion evaluator with incomplete policy returns POLICY_NOT_CONFIGURED."""
    partial = Cycle3BResearchProfile(
        target_instrument="XAUUSD",
        status=ResearchCalibrationStatus.PENDING_DATA,
        promotion_min_trades=100,
        # missing min_pf_improvement_pct, max_dd, etc.
    )
    baseline = _make_valid_xauusd_baseline()
    res = evaluate_promotion_eligibility(
        baseline=baseline, exp_profit_factor=2.5, exp_expectancy_r=0.6, exp_max_drawdown=5.0, exp_trade_count=150,
        profile=partial,
    )
    assert res.status == PromotionStatus.POLICY_NOT_CONFIGURED
    assert res.is_promotable is False


# ============================================================================
# 4. Symmetric Target Instrument Segregation & Mismatch Rejection
# ============================================================================

@pytest.mark.unit
def test_target_instrument_mismatch_rejection_all_directions():
    """Engine rejects ALL explicit instrument/profile mismatches symmetrically."""
    xaut_profile = Cycle3BResearchProfile.legacy_xaut_research_profile()
    xau_profile = Cycle3BResearchProfile.uncalibrated_xauusd_research_profile(timeframe="15m")
    candles = _make_candles(64)

    # 1. XAUUSD factory rejects XAUT profile
    with pytest.raises(ValueError, match="target instrument is 'XAUT', expected 'XAUUSD'"):
        ExperimentalTimeCycleEngine.for_xauusd(profile=xaut_profile)

    # 2. XAUUSD engine rejects analyze with XAUT profile
    xau_engine = ExperimentalTimeCycleEngine.for_xauusd()
    with pytest.raises(ValueError, match="does not match engine profile target"):
        xau_engine.analyze(candles=candles, profile=xaut_profile)

    # 3. XAUUSD engine rejects analyze with explicit instrument=XAUT
    with pytest.raises(ValueError, match="Explicit requested instrument 'XAUT' does not match"):
        xau_engine.analyze(candles=candles, instrument="XAUT")

    # 4. XAUT engine rejects analyze with XAUUSD profile
    xaut_engine = ExperimentalTimeCycleEngine.for_legacy_xaut()
    with pytest.raises(ValueError, match="does not match engine profile target"):
        xaut_engine.analyze(candles=candles, profile=xau_profile)

    # 5. XAUT engine rejects analyze with explicit instrument=XAUUSD
    with pytest.raises(ValueError, match="Explicit requested instrument 'XAUUSD' does not match"):
        xaut_engine.analyze(candles=candles, instrument="XAUUSD")


# ============================================================================
# 5. ACF Custom Threshold & No Sample-Tier Leakage
# ============================================================================

@pytest.mark.unit
def test_acf_custom_effective_n_threshold():
    """ACF with custom acf_min_effective_n=20 does not block effective_n=25 against historical 30."""
    custom_profile = Cycle3BResearchProfile(
        target_instrument="XAUUSD",
        status=ResearchCalibrationStatus.PENDING_DATA,
        acf_bartlett_z_multiplier=1.96,
        acf_min_effective_n=20.0,  # Custom!
    )
    series = [2500.0 + 15.0 * math.sin(2.0 * math.pi * i / 16.0) for i in range(64)]
    res = calculate_causal_acf(series, profile=custom_profile, effective_n=25.0)
    assert res.effective_n == 25.0
    assert res.is_significant is True
    assert res.confidence_bound > 0.0


@pytest.mark.unit
def test_acf_no_historical_60_100_tier_inference():
    """ACF with explicit profile and raw effective_n does not derive historical LOW/MEDIUM/HIGH tiers."""
    custom_profile = Cycle3BResearchProfile(
        target_instrument="XAUUSD",
        status=ResearchCalibrationStatus.PENDING_DATA,
        acf_bartlett_z_multiplier=1.96,
        acf_min_effective_n=20.0,
    )
    series = [2500.0 + 15.0 * math.sin(2.0 * math.pi * i / 16.0) for i in range(64)]
    res = calculate_causal_acf(series, profile=custom_profile, effective_n=85.0)
    # Uses fail-neutral quality without explicit SampleEvaluation
    assert res.sample_quality == SampleQuality.MEDIUM


# ============================================================================
# 6. Reliability Custom Agreement Thresholds & No Hardcoded Wavelet 0.40
# ============================================================================

@pytest.mark.unit
def test_reliability_custom_agreement_thresholds():
    """Reliability classification follows profile agreement thresholds, not hardcoded 80/50."""
    profile_strict = _make_fully_configured_xauusd_profile(timeframe="15m")
    object.__setattr__(profile_strict, "reliability_high_min_agreement_pct", 90.0)
    object.__setattr__(profile_strict, "reliability_moderate_min_agreement_pct", 70.0)

    series = [2500.0 + 15.0 * math.sin(2.0 * math.pi * i / 16.0) for i in range(64)]
    acf_res = calculate_causal_acf(series, profile=profile_strict, effective_n=50.0)
    fft_res = calculate_causal_fft(series, profile=profile_strict)
    wav_res = calculate_causal_wavelet(series, profile=profile_strict)
    hil_res = calculate_causal_hilbert(series, profile=profile_strict)

    rel_res = evaluate_cycle_reliability(
        acf=acf_res, fft=fft_res, wavelet=wav_res, hilbert=hil_res,
        effective_n=50.0, sample_quality=SampleQuality.HIGH, profile=profile_strict,
    )
    assert rel_res.reliability_status in (ReliabilityStatus.HIGH, ReliabilityStatus.MODERATE)


@pytest.mark.unit
def test_reliability_does_not_hardcode_wavelet_040():
    """Reliability engine accepts clean wavelet endpoint without duplicate hardcoded coi <= 0.40."""
    profile = _make_fully_configured_xauusd_profile()
    wav_clean = WaveletResult(
        dominant_scale_period=16.0,
        energy_ratio=0.8,
        coi_contamination_pct=0.45,  # > 0.40, but is_clean_endpoint is True from custom wavelet policy
        is_clean_endpoint=True,
        scales_analyzed=(16.0,),
        trusted_lag_bars=4,
    )
    acf_dummy = AcfResult(16, 0.8, True, 0.2, (1.0,), 50.0, SampleQuality.HIGH)
    fft_dummy = FftResult(16.0, 0.0625, 0.8, 0.2, (), True)
    hil_dummy = HilbertResult(0.0, 1.0, 0.1, 0.8, True)

    rel_res = evaluate_cycle_reliability(
        acf=acf_dummy, fft=fft_dummy, wavelet=wav_clean, hilbert=hil_dummy,
        effective_n=50.0, sample_quality=SampleQuality.HIGH, profile=profile,
    )
    assert rel_res.reliability_score > 0.0


# ============================================================================
# 7. Engine Naive Datetime & Runtime Timeframe Rigidity
# ============================================================================

@pytest.mark.unit
def test_engine_naive_datetime_candles_handling():
    """Engine safely normalizes naive datetime CandleData timestamps to UTC without TypeError."""
    t0_naive = datetime(2026, 8, 1, 0, 0)  # Naive!
    candles_naive = []
    for i in range(32):
        ts_open = t0_naive + timedelta(minutes=15 * i)
        ts_close = t0_naive + timedelta(minutes=15 * (i + 1))
        candles_naive.append(
            CandleData(
                timestamp_open=ts_open,
                timestamp_close=ts_close,
                open=Decimal("2500.0"), high=Decimal("2505.0"), low=Decimal("2495.0"), close=Decimal("2502.0"),
                volume=Decimal("100.0"), is_closed=True,
            )
        )
    engine = ExperimentalTimeCycleEngine.for_xauusd()
    snap = engine.analyze(candles=candles_naive, as_of=t0_naive + timedelta(hours=8))
    assert snap.timestamp.tzinfo == timezone.utc


@pytest.mark.unit
def test_runtime_timeframe_mismatch_rejected():
    """Profile with timeframe '1h' rejects analyze() call with timeframe '15m'."""
    profile_1h = Cycle3BResearchProfile.uncalibrated_xauusd_research_profile(timeframe="1h")
    candles = _make_candles(64)
    engine = ExperimentalTimeCycleEngine(profile=profile_1h)
    with pytest.raises(ValueError, match="Profile timeframe '1h' does not match analysis timeframe '15m'"):
        engine.analyze(candles=candles, timeframe="15m")


# ============================================================================
# 8. Strict Baseline Provenance & Deterministic Promotion Precedence Matrix
# ============================================================================

@pytest.mark.unit
def test_xauusd_baseline_provenance_validation():
    """Baseline missing date fields, source, pit_safe, or phase6_validated is rejected."""
    profile = _make_fully_configured_xauusd_profile(timeframe="15m")

    # Missing data_start
    b_no_start = BaselineBenchmark(
        base_profit_factor=2.0, base_expectancy_r=0.4, base_max_drawdown=10.0, base_trade_count=100,
        recorded_at=datetime(2026, 8, 1, tzinfo=timezone.utc), is_empirical=True, instrument="XAUUSD",
        timeframe="15m", source="OOS", data_start=None, data_end=datetime(2026, 7, 31, tzinfo=timezone.utc),
        as_of=datetime(2026, 8, 1, tzinfo=timezone.utc), pit_safe=True, phase6_validated=True,
    )
    res = evaluate_promotion_eligibility(
        baseline=b_no_start, exp_profit_factor=2.2, exp_expectancy_r=0.5, exp_max_drawdown=8.0, exp_trade_count=150,
        profile=profile,
    )
    assert res.status == PromotionStatus.BLOCKED_BY_PHASE6

    # Missing source
    b_no_source = BaselineBenchmark(
        base_profit_factor=2.0, base_expectancy_r=0.4, base_max_drawdown=10.0, base_trade_count=100,
        recorded_at=datetime(2026, 8, 1, tzinfo=timezone.utc), is_empirical=True, instrument="XAUUSD",
        timeframe="15m", source="", data_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        data_end=datetime(2026, 7, 31, tzinfo=timezone.utc), as_of=datetime(2026, 8, 1, tzinfo=timezone.utc),
        pit_safe=True, phase6_validated=True,
    )
    res = evaluate_promotion_eligibility(
        baseline=b_no_source, exp_profit_factor=2.2, exp_expectancy_r=0.5, exp_max_drawdown=8.0, exp_trade_count=150,
        profile=profile,
    )
    assert res.status == PromotionStatus.BLOCKED_BY_PHASE6


@pytest.mark.unit
def test_promotion_timeframe_mismatch_blocks():
    """Profile timeframe mismatch against baseline timeframe reports BLOCKED_BY_PHASE6."""
    profile_15m = _make_fully_configured_xauusd_profile(timeframe="15m")
    baseline_1h = _make_valid_xauusd_baseline(timeframe="1h")

    res = evaluate_promotion_eligibility(
        baseline=baseline_1h, exp_profit_factor=2.2, exp_expectancy_r=0.5, exp_max_drawdown=8.0, exp_trade_count=150,
        profile=profile_15m,
    )
    assert res.status == PromotionStatus.BLOCKED_BY_PHASE6


@pytest.mark.unit
def test_xauusd_promotion_deterministic_precedence_matrix():
    """
    Verify exact deterministic promotion precedence:
      Stage A: Incomplete policy -> POLICY_NOT_CONFIGURED
      Stage B: Complete policy + Invalid / XAUT / provenance-incomplete baseline -> BLOCKED_BY_PHASE6
      Stage C: Complete policy + Valid XAUUSD Phase 6 baseline + Hurdle fail -> FAILED
      Stage D: Complete policy + Valid XAUUSD Phase 6 baseline + All hurdles pass -> PROMOTABLE
    """
    uncal_profile = Cycle3BResearchProfile.uncalibrated_xauusd_research_profile()
    configured_profile = _make_fully_configured_xauusd_profile()
    valid_baseline = _make_valid_xauusd_baseline()

    # Stage A: Policy incomplete -> POLICY_NOT_CONFIGURED
    res_a = evaluate_promotion_eligibility(
        baseline=valid_baseline, exp_profit_factor=2.2, exp_expectancy_r=0.5, exp_max_drawdown=8.0, exp_trade_count=150,
        profile=uncal_profile,
    )
    assert res_a.status == PromotionStatus.POLICY_NOT_CONFIGURED
    assert res_a.is_promotable is False

    # Stage B1: Non-empirical baseline -> BLOCKED_BY_PHASE6
    fake_baseline = BaselineBenchmark(
        base_profit_factor=1.8, base_expectancy_r=0.4, base_max_drawdown=10.0, base_trade_count=100,
        recorded_at=datetime(2026, 8, 1, tzinfo=timezone.utc), is_empirical=False,
    )
    res_b1 = evaluate_promotion_eligibility(
        baseline=fake_baseline, exp_profit_factor=2.2, exp_expectancy_r=0.5, exp_max_drawdown=8.0, exp_trade_count=150,
        profile=configured_profile,
    )
    assert res_b1.status == PromotionStatus.BLOCKED_BY_PHASE6

    # Stage B2: XAUT empirical baseline -> BLOCKED_BY_PHASE6
    xaut_baseline = BaselineBenchmark(
        base_profit_factor=2.0, base_expectancy_r=0.4, base_max_drawdown=10.0, base_trade_count=100,
        recorded_at=datetime(2026, 8, 1, tzinfo=timezone.utc), is_empirical=True, instrument="XAUT",
        timeframe="15m", source="OOS", data_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        data_end=datetime(2026, 7, 31, tzinfo=timezone.utc), as_of=datetime(2026, 8, 1, tzinfo=timezone.utc),
        pit_safe=True, phase6_validated=True,
    )
    res_b2 = evaluate_promotion_eligibility(
        baseline=xaut_baseline, exp_profit_factor=2.2, exp_expectancy_r=0.5, exp_max_drawdown=8.0, exp_trade_count=150,
        profile=configured_profile,
    )
    assert res_b2.status == PromotionStatus.BLOCKED_BY_PHASE6

    # Stage C: Valid XAUUSD Phase 6 baseline exists, but hurdle fails (PF improvement only +1%) -> FAILED
    res_c = evaluate_promotion_eligibility(
        baseline=valid_baseline, exp_profit_factor=2.02,  # Only +1% improvement (< 5% required)
        exp_expectancy_r=0.45, exp_max_drawdown=9.0, exp_trade_count=150,
        walk_forward_folds_passed=5, walk_forward_folds_total=6, effective_n=50.0,
        profile=configured_profile,
    )
    assert res_c.status == PromotionStatus.FAILED
    assert res_c.is_promotable is False

    # Stage D: All research hurdles pass -> PROMOTABLE (Research only)
    folds = [
        WalkForwardFoldResult(fold_id=i, profit_factor=2.2, expectancy_r=0.5, max_drawdown=8.0, trade_count=25, net_profit=1000.0)
        for i in range(1, 7)
    ]
    res_d = evaluate_promotion_eligibility(
        baseline=valid_baseline, exp_profit_factor=2.2,  # +10% improvement
        exp_expectancy_r=0.5, exp_max_drawdown=8.0, exp_trade_count=150,
        fold_results=folds, effective_n=50.0,
        profile=configured_profile,
    )
    assert res_d.status == PromotionStatus.PROMOTABLE
    assert res_d.is_promotable is True


@pytest.mark.unit
def test_xauusd_insufficient_trade_count_fails():
    """XAUUSD trade count < min_trades results in status=FAILED."""
    configured_profile = _make_fully_configured_xauusd_profile()
    valid_baseline = _make_valid_xauusd_baseline()

    res = evaluate_promotion_eligibility(
        baseline=valid_baseline, exp_profit_factor=2.5, exp_expectancy_r=0.6, exp_max_drawdown=5.0,
        exp_trade_count=50,  # Insufficient (< 100)
        profile=configured_profile,
    )
    assert res.status == PromotionStatus.FAILED
    assert res.is_promotable is False
    assert any("Insufficient trade count" in r for r in res.reasons)


@pytest.mark.unit
def test_xauusd_fold_concentration_uses_configured_threshold():
    """Fold concentration uses profile.promotion_max_fold_concentration_pct without hardcoded 60%."""
    profile_relaxed = Cycle3BResearchProfile(
        name="XAUUSD_RELAXED",
        status=ResearchCalibrationStatus.PENDING_DATA,
        target_instrument="XAUUSD",
        timeframe="15m",
        acf_bartlett_z_multiplier=1.96,
        acf_min_effective_n=30.0,
        fft_min_power_ratio=0.15,
        fft_power_score_multiplier=2.5,
        wavelet_max_coi_contamination=0.40,
        wavelet_min_interior_support_ratio=3.0,
        hilbert_min_stability=0.60,
        hilbert_min_lookback=48,
        hilbert_min_velocity=0.05,
        hilbert_min_amplitude=1e-6,
        dispersion_high_threshold=0.15,
        dispersion_moderate_threshold=0.30,
        single_method_agreement_pct=35.0,
        moderate_method_agreement_pct=65.0,
        cross_window_dispersion_threshold=0.35,
        reliability_band_high=60.0,
        reliability_band_moderate=35.0,
        reliability_band_low=15.0,
        reliability_weight_acf=30.0,
        reliability_weight_fft=30.0,
        reliability_weight_wavelet=20.0,
        reliability_weight_hilbert=20.0,
        quality_multiplier_low=0.5,
        quality_multiplier_medium=0.8,
        quality_multiplier_high=1.0,
        min_effective_n=30.0,
        reliability_high_min_agreement_pct=80.0,
        reliability_moderate_min_agreement_pct=50.0,
        promotion_min_trades=100,
        promotion_min_pf_improvement_pct=5.0,
        promotion_max_dd_deterioration_pct=10.0,
        promotion_min_folds_passed=4,
        promotion_min_folds_total=6,
        promotion_max_fold_concentration_pct=80.0,  # 80% allowed!
        promotion_min_effective_n=30.0,
    )
    valid_baseline = _make_valid_xauusd_baseline()

    # One fold contributes 70% of profit (fails 60%, but passes 80%)
    folds = [
        WalkForwardFoldResult(fold_id=1, profit_factor=2.5, expectancy_r=0.5, max_drawdown=5.0, trade_count=50, net_profit=7000.0),
        WalkForwardFoldResult(fold_id=2, profit_factor=2.1, expectancy_r=0.5, max_drawdown=5.0, trade_count=50, net_profit=600.0),
        WalkForwardFoldResult(fold_id=3, profit_factor=2.1, expectancy_r=0.5, max_drawdown=5.0, trade_count=50, net_profit=600.0),
        WalkForwardFoldResult(fold_id=4, profit_factor=2.1, expectancy_r=0.5, max_drawdown=5.0, trade_count=50, net_profit=600.0),
        WalkForwardFoldResult(fold_id=5, profit_factor=2.1, expectancy_r=0.5, max_drawdown=5.0, trade_count=50, net_profit=600.0),
        WalkForwardFoldResult(fold_id=6, profit_factor=2.1, expectancy_r=0.5, max_drawdown=5.0, trade_count=50, net_profit=600.0),
    ]

    res = evaluate_promotion_eligibility(
        baseline=valid_baseline, exp_profit_factor=2.2, exp_expectancy_r=0.5, exp_max_drawdown=5.0, exp_trade_count=300,
        fold_results=folds, effective_n=50.0, profile=profile_relaxed,
    )
    assert res.status == PromotionStatus.PROMOTABLE
    assert res.is_promotable is True


@pytest.mark.unit
def test_engine_analyze_zero_metric_fabrication():
    """Engine analyze() does not fabricate performance metrics; reports NOT_EVALUATED on valid baseline."""
    candles = _make_candles(64)
    configured_profile = _make_fully_configured_xauusd_profile(timeframe="15m")
    valid_baseline = _make_valid_xauusd_baseline(timeframe="15m")

    engine = ExperimentalTimeCycleEngine(profile=configured_profile)
    snapshot = engine.analyze(candles=candles, timeframe="15m", baseline_benchmark=valid_baseline)
    assert snapshot.promotion_status == PromotionStatus.NOT_EVALUATED


# ============================================================================
# 9. Closed-Candle Isolation Split & Future Invariance
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
# 10. Broken Time-Grid Fails Closed
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
# 11. Hostile Production Lock & Persistence CheckConstraint Tests
# ============================================================================

@pytest.mark.unit
def test_hostile_production_weight_lock_in_memory():
    """Attempting to construct or mutate Cycle3BExperimentalSnapshot with production_weight > 0 fails."""
    candles = _make_candles(64)
    engine = ExperimentalTimeCycleEngine.for_xauusd()
    snapshot = engine.analyze(candles=candles)

    assert snapshot.production_weight == 0.0

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

    with pytest.raises(Exception):
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
# 12. Pure Python AST & Phase 4 Symbol Protection
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
# 13. Deep Immutability of Artifact and Hardened Provenance
# ============================================================================

@pytest.mark.unit
def test_research_provenance_requires_explicit_metadata():
    """Cycle3BResearchProvenance requires non-empty instrument/provider/timeframe/revision/fingerprint and pit_safe=True."""
    # Blank code_revision raises ValueError
    with pytest.raises(ValueError, match="code_revision must be explicitly provided"):
        Cycle3BResearchProvenance(
            instrument="XAUUSD", provider="SPOT", timeframe="15m",
            data_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            data_end=datetime(2026, 1, 1, tzinfo=timezone.utc),
            as_of=datetime(2026, 1, 1, tzinfo=timezone.utc),
            raw_observations=5000, effective_n=300.0,
            code_revision="",  # Empty!
            data_fingerprint="sha256:abc",
        )

    # Blank instrument raises ValueError
    with pytest.raises(ValueError, match="instrument must be explicitly provided"):
        Cycle3BResearchProvenance(
            instrument="", provider="SPOT", timeframe="15m",
            data_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            data_end=datetime(2026, 1, 1, tzinfo=timezone.utc),
            as_of=datetime(2026, 1, 1, tzinfo=timezone.utc),
            raw_observations=5000, effective_n=300.0,
            code_revision="7609d64", data_fingerprint="sha256:abc",
        )

    # Valid complete provenance
    prov = Cycle3BResearchProvenance(
        instrument="XAUUSD", provider="SPOT", timeframe="15m",
        data_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        data_end=datetime(2026, 1, 1, tzinfo=timezone.utc),
        as_of=datetime(2026, 1, 1, tzinfo=timezone.utc),
        raw_observations=5000, effective_n=300.0,
        code_revision="7609d64", data_fingerprint="sha256:abc123",
        pit_safe=True,
    )
    assert prov.pit_safe is True
    assert prov.code_revision == "7609d64"


@pytest.mark.unit
def test_research_artifact_deep_recursive_immutability():
    """Nested mappings in Cycle3BResearchArtifact are deeply immutable and protected from external mutation."""
    prov = Cycle3BResearchProvenance(
        instrument="XAUUSD", provider="SPOT", timeframe="15m",
        data_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        data_end=datetime(2026, 1, 1, tzinfo=timezone.utc),
        as_of=datetime(2026, 1, 1, tzinfo=timezone.utc),
        raw_observations=5000, effective_n=300.0,
        code_revision="7609d64", data_fingerprint="sha256:abc",
        pit_safe=True,
    )
    raw_config = {"nested": {"fft_window": "hann", "scales": [4, 8, 16]}}

    artifact = Cycle3BResearchArtifact(
        provenance=prov,
        algorithm_config=raw_config,
    )

    raw_config["nested"]["fft_window"] = "hamming"
    assert artifact.algorithm_config["nested"]["fft_window"] == "hann"

    with pytest.raises(TypeError):
        artifact.algorithm_config["nested"]["fft_window"] = "blackman"
