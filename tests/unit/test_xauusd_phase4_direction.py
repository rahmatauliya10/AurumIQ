"""
Unit tests for Phase 4 XAUUSD Dual-Side Direction Scoring Engine with 1H MTF Isolation.
Covers Task 3 contracts.
"""
from datetime import datetime, timezone
from decimal import Decimal
import pytest

from engine.core.types import (
    BosType,
    FeatureSnapshot,
    RegimeResult,
    RegimeType,
    SignalSide,
    StructureResult,
    StructureType,
    SwingPoint,
    SwingType,
    VolumeEvidenceType,
)
from engine.signals.direction import calculate_direction_score, calculate_xauusd_dual_direction
from engine.signals.profile import (
    Phase4CalibrationStatus,
    Phase4SignalProfile,
    SideDirectionPolicy,
    SideGatePolicy,
    SideTimingPolicy,
    uncalibrated_xauusd_signal_profile,
)


def _make_dummy_feature(
    ema_alignment: int = 1,
    slope: float = 0.1,
    rsi: float = 55.0,
    macd_hist: float = 1.0,
    macd_line: float = 1.0,
    macd_signal: float = 0.5,
    adx: float = 25.0,
    plus_di: float = 28.0,
    minus_di: float = 15.0,
    vol_ratio: float = 1.5,
) -> FeatureSnapshot:
    return FeatureSnapshot(
        timestamp=datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc),
        ema20=Decimal("2500.0"),
        ema50=Decimal("2480.0"),
        ema200=Decimal("2400.0"),
        ema_slope_20=slope,
        ema_alignment=ema_alignment,
        adx=adx,
        plus_di=plus_di,
        minus_di=minus_di,
        rsi14=rsi,
        macd_line=Decimal(str(macd_line)),
        macd_signal=Decimal(str(macd_signal)),
        macd_hist=Decimal(str(macd_hist)),
        roc12=1.5,
        atr14=Decimal("15.0"),
        atr_pct=0.6,
        bb_upper=Decimal("2520.0"),
        bb_middle=Decimal("2500.0"),
        bb_lower=Decimal("2480.0"),
        bb_bandwidth=1.6,
        realized_vol_20=12.0,
        volume_ratio_20=vol_ratio,
        volume_zscore_20=1.2,
        volume_evidence=VolumeEvidenceType.TICK_VOLUME,
        volume_usable=True,
    )


@pytest.mark.unit
def test_uncalibrated_profile_fails_neutral():
    """Verify uncalibrated XAUUSD profile evaluates descriptive evidence but yields total_score=None and is_valid=False."""
    prof = uncalibrated_xauusd_signal_profile()
    regime = RegimeResult(regime=RegimeType.BULL_TREND, confidence=0.9, timestamp=datetime.now(timezone.utc))
    features = _make_dummy_feature()
    res = calculate_xauusd_dual_direction(regime=regime, features_15m=features, structure_15m=None, profile=prof)

    assert res.is_calibrated is False
    assert res.long_direction.is_valid is False
    assert res.long_direction.total_score is None
    assert res.short_direction.is_valid is False
    assert res.short_direction.total_score is None


@pytest.mark.unit
def test_dual_side_direction_independent_evaluation():
    """Verify Long and Short direction are evaluated independently and Short != 100 - Long."""
    configured_profile = Phase4SignalProfile(
        target_instrument="XAUUSD",
        calibration_status=Phase4CalibrationStatus.CANDIDATE_NOT_FROZEN,
        long_direction=SideDirectionPolicy(
            weight_regime=15.0,
            weight_trend_1h=10.0,
            weight_trend_4h=10.0,
            weight_trend_1d=10.0,
            weight_structure_bos=20.0,
            weight_pullback=15.0,
            weight_momentum=10.0,
            weight_volume=10.0,
        ),
        short_direction=SideDirectionPolicy(
            weight_regime=15.0,
            weight_trend_1h=10.0,
            weight_trend_4h=10.0,
            weight_trend_1d=10.0,
            weight_structure_bos=20.0,
            weight_pullback=15.0,
            weight_momentum=10.0,
            weight_volume=10.0,
        ),
        long_timing=SideTimingPolicy(
            weight_entry_zone=25.0,
            weight_reversal_confirmation_15m=25.0,
            weight_momentum_turn_15m_1h=20.0,
            weight_phase3a=20.0,
            weight_volume_response=10.0,
        ),
        short_timing=SideTimingPolicy(
            weight_entry_zone=25.0,
            weight_reversal_confirmation_15m=25.0,
            weight_momentum_turn_15m_1h=20.0,
            weight_phase3a=20.0,
            weight_volume_response=10.0,
        ),
        long_gate=SideGatePolicy(70.0, 75.0, 70.0, 80.0, 80.0),
        short_gate=SideGatePolicy(70.0, 75.0, 70.0, 80.0, 80.0),
    )

    regime = RegimeResult(regime=RegimeType.BULL_TREND, confidence=1.0, timestamp=datetime.now(timezone.utc))
    features_15m = _make_dummy_feature(ema_alignment=1, slope=0.1, rsi=55.0, macd_hist=1.0)
    features_1h = _make_dummy_feature(ema_alignment=1, slope=0.1)
    features_4h = _make_dummy_feature(ema_alignment=1, slope=0.1)
    features_1d = _make_dummy_feature(ema_alignment=1, slope=0.1)

    now = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    sw_high = SwingPoint(1, now, now, Decimal("2510.0"), SwingType.HIGH, True)
    sw_low = SwingPoint(0, now, now, Decimal("2490.0"), SwingType.LOW, True)
    struct = StructureResult(now, StructureType.HH, BosType.BULLISH, sw_high, sw_low, (sw_low, sw_high), ())

    res = calculate_xauusd_dual_direction(
        regime=regime,
        features_15m=features_15m,
        structure_15m=struct,
        features_1h=features_1h,
        features_4h=features_4h,
        features_1d=features_1d,
        profile=configured_profile,
    )

    assert res.is_calibrated is True
    assert res.long_direction.is_valid is True
    assert res.short_direction.is_valid is True

    long_score = res.long_direction.total_score
    short_score = res.short_direction.total_score

    assert long_score > 70.0
    assert short_score <= 35.0
    # Invariant: Short is NOT 100 - Long
    assert short_score != (100.0 - long_score)


@pytest.mark.unit
def test_1h_isolation_no_timeframe_substitution():
    """Verify missing 1H MTF evidence receives 0.0 pts and does NOT borrow 15m evidence."""
    configured_profile = Phase4SignalProfile(
        target_instrument="XAUUSD",
        calibration_status=Phase4CalibrationStatus.CANDIDATE_NOT_FROZEN,
        long_direction=SideDirectionPolicy(
            weight_regime=15.0,
            weight_trend_1h=15.0,
            weight_trend_4h=10.0,
            weight_trend_1d=10.0,
            weight_structure_bos=20.0,
            weight_pullback=10.0,
            weight_momentum=10.0,
            weight_volume=10.0,
        ),
        short_direction=SideDirectionPolicy(
            weight_regime=15.0,
            weight_trend_1h=15.0,
            weight_trend_4h=10.0,
            weight_trend_1d=10.0,
            weight_structure_bos=20.0,
            weight_pullback=10.0,
            weight_momentum=10.0,
            weight_volume=10.0,
        ),
        long_timing=SideTimingPolicy(25.0, 25.0, 20.0, 20.0, 10.0),
        short_timing=SideTimingPolicy(25.0, 25.0, 20.0, 20.0, 10.0),
        long_gate=SideGatePolicy(70.0, 75.0, 70.0, 80.0, 80.0),
        short_gate=SideGatePolicy(70.0, 75.0, 70.0, 80.0, 80.0),
    )

    regime = RegimeResult(regime=RegimeType.BULL_TREND, confidence=1.0, timestamp=datetime.now(timezone.utc))
    features_15m = _make_dummy_feature(ema_alignment=1)  # Bullish 15m

    # Call with features_1h = None
    res = calculate_xauusd_dual_direction(
        regime=regime,
        features_15m=features_15m,
        structure_15m=None,
        features_1h=None,
        profile=configured_profile,
    )

    comp_1h = [c for c in res.long_direction.components if c.name == "1H Trend Alignment"][0]
    assert comp_1h.score == 0.0
    assert comp_1h.is_available is False
    assert "unavailable" in comp_1h.reason.lower()


@pytest.mark.unit
def test_historical_xaut_direction_score_preserved():
    """Verify historical calculate_direction_score remains 100% frozen and operational."""
    res = calculate_direction_score(
        regime=None,
        features_15m=None,
        structure_15m=None,
    )
    assert res.max_score == 100.0
    assert res.total_score == 0.0
    assert len(res.components) == 7
