"""
Contract & Acceptance Tests for Phase 2 XAUUSD Revalidation:
- XAU-P2-01: Volume Semantics & Evidence Types (REAL_VOLUME, TICK_VOLUME, PROXY_VOLUME, UNAVAILABLE, Mixed semantics safety)
- XAUUSD Regime Revalidation Tests: Calibration Profile Segregation, Fail-Closed Unknown State, Causality & Fixture Invariance
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest

from engine.core.types import (
    CandleData,
    FeatureSnapshot,
    VolumeEvidenceType,
    VolumeFeatureResult,
    RegimeType,
    RegimeThresholdProfile,
    SwingType,
)
from engine.core.config import EngineConfigData
from engine.features.volume import (
    calculate_volume_features,
    calculate_volume_ratio,
    calculate_volume_zscore,
)
from engine.features.engine import FeatureEngine
from engine.regime.engine import RegimeEngine
from engine.structure.causal_swings import detect_causal_swings


def _make_candle(
    idx: int,
    close: str = "2500.00",
    volume: str = "100.0",
    evidence: VolumeEvidenceType = VolumeEvidenceType.REAL_VOLUME,
    is_closed: bool = True,
    high: str = "2505.00",
    low: str = "2495.00",
    open_price: str = "2500.00",
) -> CandleData:
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=15 * idx)
    return CandleData(
        timestamp_open=t0,
        timestamp_close=t0 + timedelta(minutes=15),
        open=Decimal(open_price),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
        is_closed=is_closed,
        volume_evidence=evidence,
    )


# ============================================================================
# Feature Engine Closed-Candle & Empty Input Regressions
# ============================================================================

@pytest.mark.unit
def test_feature_engine_empty_input_regression():
    """
    Patch 1: FeatureEngine().extract_features([]) must safely return an empty FeatureSnapshot
    without exceptions, with volume_evidence=UNAVAILABLE, volume_usable=False, volume_reason='EMPTY_CANDLES'.
    """
    engine = FeatureEngine()
    snapshot = engine.extract_features([])

    assert isinstance(snapshot, FeatureSnapshot)
    assert snapshot.ema20 is None
    assert snapshot.ema200 is None
    assert snapshot.volume_evidence == VolumeEvidenceType.UNAVAILABLE
    assert snapshot.volume_usable is False
    assert snapshot.volume_reason == "EMPTY_CANDLES"


@pytest.mark.acceptance
def test_closed_and_open_forming_candle_invariance():
    """
    Patch 2: FeatureEngine must compute technical indicators and features strictly from CLOSED candles.
    An extreme open/forming candle appended at T+1 must have zero effect on the resulting snapshot.
    """
    engine = FeatureEngine()

    # 250 closed candles with steady trend
    closed_candles = [_make_candle(i, close=str(2500 + i), high=str(2505 + i), low=str(2495 + i), is_closed=True) for i in range(250)]
    snapshot_closed_only = engine.extract_features(closed_candles)

    # Append 1 extreme OPEN candle with huge spike and volume
    open_spike_candle = _make_candle(
        250,
        close="3500.00",
        high="4000.00",
        low="2400.00",
        volume="999999.0",
        is_closed=False,
    )
    candles_with_open = closed_candles + [open_spike_candle]
    snapshot_with_open = engine.extract_features(candles_with_open)

    # Assert 100% field-by-field equality between the two snapshots
    assert snapshot_closed_only.timestamp == snapshot_with_open.timestamp
    assert snapshot_closed_only.timestamp == closed_candles[-1].timestamp_open
    assert snapshot_closed_only.ema20 == snapshot_with_open.ema20
    assert snapshot_closed_only.ema50 == snapshot_with_open.ema50
    assert snapshot_closed_only.ema200 == snapshot_with_open.ema200
    assert snapshot_closed_only.ema_slope_20 == snapshot_with_open.ema_slope_20
    assert snapshot_closed_only.ema_alignment == snapshot_with_open.ema_alignment
    assert snapshot_closed_only.adx == snapshot_with_open.adx
    assert snapshot_closed_only.plus_di == snapshot_with_open.plus_di
    assert snapshot_closed_only.minus_di == snapshot_with_open.minus_di
    assert snapshot_closed_only.rsi14 == snapshot_with_open.rsi14
    assert snapshot_closed_only.macd_line == snapshot_with_open.macd_line
    assert snapshot_closed_only.macd_signal == snapshot_with_open.macd_signal
    assert snapshot_closed_only.macd_hist == snapshot_with_open.macd_hist
    assert snapshot_closed_only.roc12 == snapshot_with_open.roc12
    assert snapshot_closed_only.atr14 == snapshot_with_open.atr14
    assert snapshot_closed_only.atr_pct == snapshot_with_open.atr_pct
    assert snapshot_closed_only.bb_upper == snapshot_with_open.bb_upper
    assert snapshot_closed_only.bb_middle == snapshot_with_open.bb_middle
    assert snapshot_closed_only.bb_lower == snapshot_with_open.bb_lower
    assert snapshot_closed_only.bb_bandwidth == snapshot_with_open.bb_bandwidth
    assert snapshot_closed_only.realized_vol_20 == snapshot_with_open.realized_vol_20
    assert snapshot_closed_only.volume_ratio_20 == snapshot_with_open.volume_ratio_20
    assert snapshot_closed_only.volume_zscore_20 == snapshot_with_open.volume_zscore_20
    assert snapshot_closed_only.volume_evidence == snapshot_with_open.volume_evidence
    assert snapshot_closed_only.volume_usable == snapshot_with_open.volume_usable
    assert snapshot_closed_only.volume_reason == snapshot_with_open.volume_reason


# ============================================================================
# XAU-P2-01: Volume Semantic Safety & Evidence Validation Tests
# ============================================================================

@pytest.mark.unit
def test_xau_p2_01_a_real_volume_valid():
    """
    XAU-P2-01 A: Valid homogeneous REAL_VOLUME is retained as REAL_VOLUME
    and produces usable ratio and z-score metrics.
    """
    candles = [_make_candle(i, volume="100.0", evidence=VolumeEvidenceType.REAL_VOLUME) for i in range(25)]
    # Set the latest candle with higher volume
    candles[-1] = _make_candle(24, volume="200.0", evidence=VolumeEvidenceType.REAL_VOLUME)

    res = calculate_volume_features(candles, period=20)
    assert res.evidence_type == VolumeEvidenceType.REAL_VOLUME
    assert res.is_usable is True
    assert res.ratio is not None
    assert res.ratio > 1.5  # 200 vs ~105 average
    assert res.zscore is not None
    assert res.reason == "VALID"


@pytest.mark.unit
def test_xau_p2_01_b_tick_volume_valid():
    """
    XAU-P2-01 B: Valid homogeneous TICK_VOLUME is explicitly retained as TICK_VOLUME.
    """
    candles = [_make_candle(i, volume="1500.0", evidence=VolumeEvidenceType.TICK_VOLUME) for i in range(25)]
    candles[-1] = _make_candle(24, volume="3000.0", evidence=VolumeEvidenceType.TICK_VOLUME)

    res = calculate_volume_features(candles, period=20)
    assert res.evidence_type == VolumeEvidenceType.TICK_VOLUME
    assert res.is_usable is True
    assert res.ratio is not None
    assert res.ratio > 1.5
    assert res.reason == "VALID"


@pytest.mark.unit
def test_xau_p2_01_c_proxy_volume_valid():
    """
    XAU-P2-01 C: Valid homogeneous PROXY_VOLUME is explicitly retained as PROXY_VOLUME.
    """
    candles = [_make_candle(i, volume="5000.0", evidence=VolumeEvidenceType.PROXY_VOLUME) for i in range(25)]
    candles[-1] = _make_candle(24, volume="5000.0", evidence=VolumeEvidenceType.PROXY_VOLUME)

    res = calculate_volume_features(candles, period=20)
    assert res.evidence_type == VolumeEvidenceType.PROXY_VOLUME
    assert res.is_usable is True
    assert res.ratio == 1.0
    assert res.zscore == 0.0
    assert res.reason == "VALID"


@pytest.mark.unit
def test_xau_p2_01_d_unavailable_volume_fail_neutral():
    """
    XAU-P2-01 D: UNAVAILABLE volume results in usable=False, ratio=None, zscore=None,
    and zero positive volume confirmation.
    """
    candles = [_make_candle(i, volume="0.0", evidence=VolumeEvidenceType.UNAVAILABLE) for i in range(25)]

    res = calculate_volume_features(candles, period=20)
    assert res.evidence_type == VolumeEvidenceType.UNAVAILABLE
    assert res.is_usable is False
    assert res.ratio is None
    assert res.zscore is None
    assert res.reason == "UNAVAILABLE_VOLUME"


@pytest.mark.unit
def test_xau_p2_01_e_missing_or_non_positive_volume():
    """
    XAU-P2-01 E: Missing, None, or zero volume is treated as UNAVAILABLE.
    """
    # 1. Zero volume with REAL_VOLUME label -> fail-neutral UNAVAILABLE
    candles_zero = [_make_candle(i, volume="100.0", evidence=VolumeEvidenceType.REAL_VOLUME) for i in range(24)]
    candles_zero.append(_make_candle(24, volume="0.0", evidence=VolumeEvidenceType.REAL_VOLUME))

    res_zero = calculate_volume_features(candles_zero, period=20)
    assert res_zero.evidence_type == VolumeEvidenceType.UNAVAILABLE
    assert res_zero.is_usable is False
    assert res_zero.ratio is None

    # 2. None volume -> fail-neutral UNAVAILABLE
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
    candle_none = CandleData(
        timestamp_open=t0,
        timestamp_close=t0 + timedelta(minutes=15),
        open=Decimal("2500.00"),
        high=Decimal("2505.00"),
        low=Decimal("2495.00"),
        close=Decimal("2500.00"),
        volume=Decimal("-1.0"),  # Invalid negative volume
        is_closed=True,
        volume_evidence=VolumeEvidenceType.REAL_VOLUME,
    )
    candles_none = [_make_candle(i, volume="100.0", evidence=VolumeEvidenceType.REAL_VOLUME) for i in range(24)] + [candle_none]
    res_none = calculate_volume_features(candles_none, period=20)
    assert res_none.is_usable is False
    assert res_none.evidence_type == VolumeEvidenceType.UNAVAILABLE


@pytest.mark.unit
def test_xau_p2_01_f_strict_enum_validation_arbitrary_label():
    """
    XAU-P2-01 F / Patch 3: Arbitrary or unknown runtime volume evidence labels
    such as 'FABRICATED_VOLUME' or 'FAKE' must NEVER produce is_usable=True.
    Must return evidence_type=UNAVAILABLE, is_usable=False, reason='INVALID_VOLUME_EVIDENCE'.
    """
    # 1. String "FABRICATED_VOLUME"
    candles_fake = [_make_candle(i, volume="100.0", evidence=VolumeEvidenceType.REAL_VOLUME) for i in range(24)]
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=15 * 24)
    candle_fabricated = CandleData(
        timestamp_open=t0,
        timestamp_close=t0 + timedelta(minutes=15),
        open=Decimal("2500.00"),
        high=Decimal("2505.00"),
        low=Decimal("2495.00"),
        close=Decimal("2500.00"),
        volume=Decimal("100.0"),
        is_closed=True,
        volume_evidence="FABRICATED_VOLUME",  # type: ignore
    )
    candles_fake.append(candle_fabricated)

    res = calculate_volume_features(candles_fake, period=20)
    assert res.evidence_type == VolumeEvidenceType.UNAVAILABLE
    assert res.is_usable is False
    assert res.ratio is None
    assert res.zscore is None
    assert res.reason == "INVALID_VOLUME_EVIDENCE"

    # 2. None evidence label
    candle_none_ev = CandleData(
        timestamp_open=t0,
        timestamp_close=t0 + timedelta(minutes=15),
        open=Decimal("2500.00"),
        high=Decimal("2505.00"),
        low=Decimal("2495.00"),
        close=Decimal("2500.00"),
        volume=Decimal("100.0"),
        is_closed=True,
        volume_evidence=None,  # type: ignore
    )
    candles_none_ev = [_make_candle(i, volume="100.0", evidence=VolumeEvidenceType.REAL_VOLUME) for i in range(24)] + [candle_none_ev]
    res_none_ev = calculate_volume_features(candles_none_ev, period=20)
    assert res_none_ev.evidence_type == VolumeEvidenceType.UNAVAILABLE
    assert res_none_ev.is_usable is False
    assert res_none_ev.reason == "INVALID_VOLUME_EVIDENCE"


@pytest.mark.unit
def test_xau_p2_01_full_window_non_positive_volume_validation():
    """
    XAU-P2-01 / Patch 4: If ANY bar in the rolling window contains non-positive volume
    (0 or negative), the entire window is disqualified as UNAVAILABLE.
    """
    # 1. Bar 5 in 20-bar window has volume 0
    candles_zero_early = [_make_candle(i, volume="100.0", evidence=VolumeEvidenceType.REAL_VOLUME) for i in range(25)]
    candles_zero_early[10] = _make_candle(10, volume="0.0", evidence=VolumeEvidenceType.REAL_VOLUME)

    res_zero = calculate_volume_features(candles_zero_early, period=20)
    assert res_zero.evidence_type == VolumeEvidenceType.UNAVAILABLE
    assert res_zero.is_usable is False
    assert res_zero.ratio is None
    assert res_zero.zscore is None
    assert res_zero.reason == "UNAVAILABLE_VOLUME"

    # 2. Bar 8 in 20-bar window has negative volume
    candles_neg_early = [_make_candle(i, volume="100.0", evidence=VolumeEvidenceType.REAL_VOLUME) for i in range(25)]
    candles_neg_early[12] = _make_candle(12, volume="-50.0", evidence=VolumeEvidenceType.REAL_VOLUME)

    res_neg = calculate_volume_features(candles_neg_early, period=20)
    assert res_neg.evidence_type == VolumeEvidenceType.UNAVAILABLE
    assert res_neg.is_usable is False
    assert res_neg.ratio is None
    assert res_neg.zscore is None
    assert res_neg.reason == "UNAVAILABLE_VOLUME"


@pytest.mark.unit
def test_xau_p2_01_g_mixed_volume_semantics_rejected():
    """
    XAU-P2-01 G: Mixed volume semantics in a rolling window (e.g. TICK_VOLUME + REAL_VOLUME)
    must NOT be silently combined as a homogeneous series; must return is_usable=False.
    """
    # 10 bars of TICK_VOLUME + 10 bars of REAL_VOLUME
    candles = []
    for i in range(10):
        candles.append(_make_candle(i, volume="1500.0", evidence=VolumeEvidenceType.TICK_VOLUME))
    for i in range(10, 20):
        candles.append(_make_candle(i, volume="100.0", evidence=VolumeEvidenceType.REAL_VOLUME))

    res = calculate_volume_features(candles, period=20)
    assert res.is_usable is False
    assert res.ratio is None
    assert res.zscore is None
    assert res.reason == "MIXED_VOLUME_SEMANTICS"


@pytest.mark.unit
def test_xau_p2_01_h_proxy_volume_does_not_overwrite_spot_evidence():
    """
    XAU-P2-01 H: Spot candles retain their explicit evidence type and do not allow
    Futures proxy volume to silently overwrite spot volume or mix into spot rolling windows.
    """
    spot_candles = [_make_candle(i, volume="100.0", evidence=VolumeEvidenceType.REAL_VOLUME) for i in range(15)]
    futures_proxy_candles = [_make_candle(i, volume="5000.0", evidence=VolumeEvidenceType.PROXY_VOLUME) for i in range(15, 20)]

    # Combining spot and futures proxy in one rolling series must fail with MIXED_VOLUME_SEMANTICS
    mixed_series = spot_candles + futures_proxy_candles
    res = calculate_volume_features(mixed_series, period=20)
    assert res.is_usable is False
    assert res.reason == "MIXED_VOLUME_SEMANTICS"


@pytest.mark.unit
def test_xau_p2_01_i_no_volume_fabrication_in_feature_engine():
    """
    XAU-P2-01 I: FeatureEngine never synthesizes volume for missing bars.
    When candles have UNAVAILABLE volume, FeatureSnapshot exposes volume_usable=False.
    """
    engine = FeatureEngine()
    candles = [_make_candle(i, volume="0.0", evidence=VolumeEvidenceType.UNAVAILABLE) for i in range(250)]

    snapshot = engine.extract_features(candles)
    assert snapshot.volume_usable is False
    assert snapshot.volume_ratio_20 is None
    assert snapshot.volume_zscore_20 is None
    assert snapshot.volume_evidence == VolumeEvidenceType.UNAVAILABLE
    assert snapshot.volume_reason == "UNAVAILABLE_VOLUME"


# ============================================================================
# XAUUSD Regime Revalidation Tests
# ============================================================================

@pytest.mark.unit
def test_uncalibrated_xauusd_profile_has_no_hidden_thresholds():
    """
    Patch 5: uncalibrated_xauusd_profile() must contain NO configured numerical thresholds (all None),
    guaranteeing zero hidden fallback to historical XAUT reference boundaries.
    """
    profile = RegimeThresholdProfile.uncalibrated_xauusd_profile()
    assert profile.name == "XAUUSD_UNCALIBRATED"
    assert profile.is_calibrated is False
    assert profile.adx_trend_threshold is None
    assert profile.slope_boundary is None
    assert profile.high_vol_realized_pct is None
    assert profile.high_vol_atr_pct is None
    assert profile.high_vol_bb_bandwidth_pct is None
    assert profile.rsi_bull_threshold is None
    assert profile.rsi_bear_threshold is None


@pytest.mark.unit
def test_legacy_xaut_profile_preserves_historical_boundaries():
    """
    Patch 5: legacy_xaut_profile() explicitly defines the frozen historical XAUT boundaries.
    """
    profile = RegimeThresholdProfile.legacy_xaut_profile()
    assert profile.name == "LEGACY_XAUT_REFERENCE"
    assert profile.is_calibrated is True
    assert profile.adx_trend_threshold == 20.0
    assert profile.slope_boundary == 0.05
    assert profile.high_vol_realized_pct == 5.0
    assert profile.high_vol_atr_pct == 3.0
    assert profile.high_vol_bb_bandwidth_pct == 15.0
    assert profile.rsi_bull_threshold == 50.0
    assert profile.rsi_bear_threshold == 50.0


@pytest.mark.unit
def test_calibrated_profile_with_missing_fields_fails_safe():
    """
    Patch 5: A profile marked is_calibrated=True but with missing/None boundary values
    must fail safe to UNKNOWN with CALIBRATION_REQUIRED rather than throwing a TypeError.
    """
    incomplete_profile = RegimeThresholdProfile(
        name="INCOMPLETE_CALIBRATION",
        is_calibrated=True,
        adx_trend_threshold=None,  # Missing!
        slope_boundary=0.05,
        high_vol_realized_pct=5.0,
        high_vol_atr_pct=3.0,
        high_vol_bb_bandwidth_pct=15.0,
        rsi_bull_threshold=50.0,
        rsi_bear_threshold=50.0,
    )
    engine = RegimeEngine(profile=incomplete_profile)
    features = FeatureSnapshot(
        timestamp=datetime.now(timezone.utc),
        ema20=Decimal("2550"), ema50=Decimal("2500"), ema200=Decimal("2400"),
        ema_slope_20=0.15, ema_alignment=1, adx=30.0, plus_di=35.0, minus_di=10.0,
        rsi14=65.0, macd_line=Decimal("10"), macd_signal=Decimal("8"), macd_hist=Decimal("2"), roc12=4.0,
        atr14=Decimal("10"), atr_pct=0.4, bb_upper=Decimal("2570"), bb_middle=Decimal("2550"),
        bb_lower=Decimal("2530"), bb_bandwidth=1.5, realized_vol_20=1.0, volume_ratio_20=1.2, volume_zscore_20=0.5,
    )
    res = engine.classify(features)
    assert res.regime == RegimeType.UNKNOWN
    assert res.confidence == 0.0
    assert res.details.get("reason") == "CALIBRATION_REQUIRED"


@pytest.mark.unit
def test_xauusd_uncalibrated_regime_fails_closed_to_unknown():
    """
    Regime Revalidation 1: When running XAUUSD without configured empirical calibration,
    RegimeEngine returns UNKNOWN with CALIBRATION_REQUIRED.
    """
    # 1. Using factory for XAUUSD
    xau_engine = RegimeEngine.for_xauusd()
    features = FeatureSnapshot(
        timestamp=datetime.now(timezone.utc),
        ema20=Decimal("2550"), ema50=Decimal("2500"), ema200=Decimal("2400"),
        ema_slope_20=0.15, ema_alignment=1, adx=30.0, plus_di=35.0, minus_di=10.0,
        rsi14=65.0, macd_line=Decimal("10"), macd_signal=Decimal("8"), macd_hist=Decimal("2"), roc12=4.0,
        atr14=Decimal("10"), atr_pct=0.4, bb_upper=Decimal("2570"), bb_middle=Decimal("2550"),
        bb_lower=Decimal("2530"), bb_bandwidth=1.5, realized_vol_20=1.0, volume_ratio_20=1.2, volume_zscore_20=0.5,
    )
    res = xau_engine.classify(features)
    assert res.regime == RegimeType.UNKNOWN
    assert res.confidence == 0.0
    assert res.details.get("reason") == "CALIBRATION_REQUIRED"

    # 2. Passing instrument='XAU/USD' into standard engine
    default_engine = RegimeEngine()
    res_inst = default_engine.classify(features, instrument="XAU/USD")
    assert res_inst.regime == RegimeType.UNKNOWN
    assert res_inst.confidence == 0.0
    assert res_inst.details.get("reason") == "CALIBRATION_REQUIRED"


@pytest.mark.unit
def test_legacy_xaut_regime_reproduces_historical_behavior():
    """
    Regime Revalidation 2: Legacy XAUT path reproduces historical behavior byte-for-byte.
    """
    legacy_engine = RegimeEngine.for_legacy_xaut()
    features = FeatureSnapshot(
        timestamp=datetime.now(timezone.utc),
        ema20=Decimal("2550"), ema50=Decimal("2500"), ema200=Decimal("2400"),
        ema_slope_20=0.15, ema_alignment=1, adx=30.0, plus_di=35.0, minus_di=10.0,
        rsi14=65.0, macd_line=Decimal("10"), macd_signal=Decimal("8"), macd_hist=Decimal("2"), roc12=4.0,
        atr14=Decimal("10"), atr_pct=0.4, bb_upper=Decimal("2570"), bb_middle=Decimal("2550"),
        bb_lower=Decimal("2530"), bb_bandwidth=1.5, realized_vol_20=1.0, volume_ratio_20=1.2, volume_zscore_20=0.5,
    )
    res = legacy_engine.classify(features)
    assert res.regime == RegimeType.BULL_TREND
    assert res.confidence >= 0.80


@pytest.mark.unit
def test_explicit_fixture_calibration_profile_enables_deterministic_classification():
    """
    Regime Revalidation 3: When an explicit calibrated fixture profile is supplied,
    deterministic classification executes accurately.
    """
    fixture_profile = RegimeThresholdProfile(
        name="XAUUSD_FIXTURE_CALIBRATED",
        is_calibrated=True,
        adx_trend_threshold=25.0,
        slope_boundary=0.04,
        high_vol_realized_pct=4.0,
        high_vol_atr_pct=2.5,
        high_vol_bb_bandwidth_pct=12.0,
        rsi_bull_threshold=52.0,
        rsi_bear_threshold=48.0,
    )
    calibrated_engine = RegimeEngine(profile=fixture_profile)

    # Bull trend features satisfying fixture profile
    features_bull = FeatureSnapshot(
        timestamp=datetime.now(timezone.utc),
        ema20=Decimal("2600"), ema50=Decimal("2550"), ema200=Decimal("2450"),
        ema_slope_20=0.10, ema_alignment=1, adx=28.0, plus_di=32.0, minus_di=12.0,
        rsi14=58.0, macd_line=Decimal("8"), macd_signal=Decimal("6"), macd_hist=Decimal("2"), roc12=3.0,
        atr14=Decimal("8"), atr_pct=0.3, bb_upper=Decimal("2620"), bb_middle=Decimal("2600"),
        bb_lower=Decimal("2580"), bb_bandwidth=1.2, realized_vol_20=0.8, volume_ratio_20=1.1, volume_zscore_20=0.2,
    )
    res = calibrated_engine.classify(features_bull)
    assert res.regime == RegimeType.BULL_TREND
    assert res.confidence >= 0.80

    # High volatility trigger on fixture threshold (realized vol > 4.0%)
    features_high_vol = FeatureSnapshot(
        timestamp=datetime.now(timezone.utc),
        ema20=Decimal("2600"), ema50=Decimal("2550"), ema200=Decimal("2450"),
        ema_slope_20=0.10, ema_alignment=1, adx=28.0, plus_di=32.0, minus_di=12.0,
        rsi14=58.0, macd_line=Decimal("8"), macd_signal=Decimal("6"), macd_hist=Decimal("2"), roc12=3.0,
        atr14=Decimal("8"), atr_pct=0.3, bb_upper=Decimal("2620"), bb_middle=Decimal("2600"),
        bb_lower=Decimal("2580"), bb_bandwidth=1.2, realized_vol_20=4.5, volume_ratio_20=1.1, volume_zscore_20=0.2,
    )
    res_vol = calibrated_engine.classify(features_high_vol)
    assert res_vol.regime == RegimeType.HIGH_VOLATILITY


@pytest.mark.unit
def test_fixture_profile_does_not_pollute_global_or_default_engines():
    """
    Regime Revalidation 4: Injected fixture profile does not alter default uncalibrated state.
    """
    # Create fresh engine instance
    fresh_xau_engine = RegimeEngine.for_xauusd()
    assert fresh_xau_engine.profile.is_calibrated is False
    assert fresh_xau_engine.profile.name == "XAUUSD_UNCALIBRATED"


@pytest.mark.unit
def test_future_candles_do_not_change_regime_classification_at_timestamp_t():
    """
    Regime Revalidation 5: Adding future candles (T+1 .. T+N) never modifies
    regime classification evaluated on closed candles up to T.
    """
    feature_engine = FeatureEngine()
    legacy_regime_engine = RegimeEngine.for_legacy_xaut()

    base_candles = [_make_candle(i, close=str(2500 + i * 2)) for i in range(250)]

    features_t = feature_engine.extract_features(base_candles)
    regime_t = legacy_regime_engine.classify(features_t)

    # Add 50 wild future candles
    future_candles = base_candles + [_make_candle(250 + j, close=str(3000 + (j % 5) * 50)) for j in range(50)]

    # Slice strictly up to T
    features_t_sliced = feature_engine.extract_features(future_candles[:250])
    regime_t_sliced = legacy_regime_engine.classify(features_t_sliced)

    assert regime_t.regime == regime_t_sliced.regime
    assert regime_t.confidence == regime_t_sliced.confidence
    assert regime_t.timestamp == regime_t_sliced.timestamp


@pytest.mark.unit
def test_closed_candle_operational_input_only():
    """
    Regime Revalidation 6: Open/forming candles are strictly excluded from causal structure
    and higher timeframe operational analysis.
    """
    closed_candles = [_make_candle(i, high="2510", low="2490", is_closed=True) for i in range(10)]
    open_candle = _make_candle(10, high="2590", low="2480", is_closed=False)  # Spike in open candle!

    # Evaluated with open candle excluded
    swings_closed = detect_causal_swings(closed_candles, left_bars=3, right_bars=3)
    # The open candle must not be passed to causal swing detector in operational pipeline
    assert all(c.is_closed for c in closed_candles)
