"""
Unit tests for Phase 4 XAUUSD Dual-Side Timing Scoring Engine & Phase 3A Profile Authority.
Covers Task 4 contracts.
"""
from datetime import datetime, timezone
from decimal import Decimal
import pytest

from engine.core.types import (
    CalendarSeasonalityContext,
    CandleData,
    Cycle3ASnapshot,
    FeatureSnapshot,
    MacroEventContext,
    SessionContext,
    SessionType,
    SignalSide,
    StructureResult,
    StructureType,
    SwingDurationContext,
    VolumeEvidenceType,
)
from engine.cycles.profile import CalibrationStatus as Cycle3ACalibrationStatus, Cycle3AProfile
from engine.signals.profile import (
    Phase4CalibrationStatus,
    Phase4SignalProfile,
    SideDirectionPolicy,
    SideGatePolicy,
    SideTimingPolicy,
    uncalibrated_xauusd_signal_profile,
)
from engine.signals.timing import (
    calculate_timing_score,
    calculate_xauusd_dual_timing,
    extract_xauusd_phase3a_score,
)


def _make_dummy_candle(
    open_p: float = 2500.0,
    high_p: float = 2505.0,
    low_p: float = 2495.0,
    close_p: float = 2504.0,
    volume: float = 1000.0,
) -> CandleData:
    now = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    return CandleData(
        timestamp_open=now,
        timestamp_close=now,
        open=Decimal(str(open_p)),
        high=Decimal(str(high_p)),
        low=Decimal(str(low_p)),
        close=Decimal(str(close_p)),
        volume=Decimal(str(volume)),
        is_closed=True,
    )


def _make_dummy_feature(rsi: float = 50.0) -> FeatureSnapshot:
    now = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    return FeatureSnapshot(
        timestamp=now,
        ema20=Decimal("2500.0"),
        ema50=Decimal("2480.0"),
        ema200=Decimal("2400.0"),
        ema_slope_20=0.1,
        ema_alignment=1,
        adx=25.0,
        plus_di=25.0,
        minus_di=15.0,
        rsi14=rsi,
        macd_line=Decimal("1.0"),
        macd_signal=Decimal("0.5"),
        macd_hist=Decimal("0.5"),
        roc12=1.0,
        atr14=Decimal("15.0"),
        atr_pct=0.6,
        bb_upper=Decimal("2520.0"),
        bb_middle=Decimal("2500.0"),
        bb_lower=Decimal("2480.0"),
        bb_bandwidth=1.6,
        realized_vol_20=12.0,
        volume_ratio_20=1.5,
        volume_zscore_20=1.2,
        volume_evidence=VolumeEvidenceType.TICK_VOLUME,
        volume_usable=True,
    )


def _make_dummy_cycle_3a(
    score: float = 85.0,
    profile_name: str = "XAUUSD_FROZEN_v1",
    calibration_status: str = Cycle3ACalibrationStatus.PRODUCTION_FROZEN.value,
) -> Cycle3ASnapshot:
    now = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    session = SessionContext(
        session=SessionType.LONDON_NY_OVERLAP,
        progress_pct=50.0,
        is_high_liquidity=True,
        local_times={},
    )
    swing = SwingDurationContext(
        market_age_bars=12,
        market_age_hours=3.0,
        known_age_bars=12,
        known_age_hours=3.0,
        pullback_age_percentile=50.0,
        is_mature=True,
    )
    macro = MacroEventContext(
        is_in_blackout=False,
        minutes_to_next_event=120,
        is_feed_healthy=True,
    )
    cal = CalendarSeasonalityContext(
        day_of_week=0,
        day_name="Monday",
        hour_utc=12,
        month=8,
        is_month_end_flow=True,
        stability_score=80.0,
    )
    return Cycle3ASnapshot(
        timestamp=now,
        session=session,
        swing_duration=swing,
        macro_event=macro,
        calendar=cal,
        is_blocked_by_event=False,
        cycle_score_3a=score,
        profile_name=profile_name,
        calibration_status=calibration_status,
    )


@pytest.mark.unit
def test_uncalibrated_profile_timing_fails_neutral():
    """Verify uncalibrated XAUUSD profile yields is_valid=False and total_score=None."""
    prof = uncalibrated_xauusd_signal_profile()
    candle = _make_dummy_candle()
    features = _make_dummy_feature()
    res = calculate_xauusd_dual_timing(candle_15m=candle, features_15m=features, structure_15m=None, profile=prof)

    assert res.is_calibrated is False
    assert res.long_timing.is_valid is False
    assert res.long_timing.total_score is None
    assert res.short_timing.is_valid is False
    assert res.short_timing.total_score is None


@pytest.mark.unit
def test_phase3a_target_authority_guard():
    """Verify extract_xauusd_phase3a_score strictly requires XAUUSD PRODUCTION_FROZEN Cycle3AProfile."""
    snap = _make_dummy_cycle_3a(
        score=85.0,
        profile_name="XAUUSD_FROZEN_v1",
        calibration_status=Cycle3ACalibrationStatus.PRODUCTION_FROZEN.value,
    )

    # 1. Matching frozen profile with explicit timeframe -> returns score
    frozen_prof = Cycle3AProfile(
        name="XAUUSD_FROZEN_v1",
        target_instrument="XAUUSD",
        timeframe="15m",
        calibration_status=Cycle3ACalibrationStatus.PRODUCTION_FROZEN,
    )
    assert extract_xauusd_phase3a_score(snap, frozen_prof, decision_timeframe="15m") == 85.0

    # 2. Profile target mismatch (e.g. XAUT) -> returns 0.0
    xaut_prof = Cycle3AProfile(
        name="XAUT_FROZEN_v1",
        target_instrument="XAUT",
        calibration_status=Cycle3ACalibrationStatus.PRODUCTION_FROZEN,
    )
    assert extract_xauusd_phase3a_score(snap, xaut_prof) == 0.0

    # 3. Profile not frozen (e.g. CANDIDATE_NOT_FROZEN) -> returns 0.0
    cand_prof = Cycle3AProfile(
        name="XAUUSD_CANDIDATE_v1",
        target_instrument="XAUUSD",
        calibration_status=Cycle3ACalibrationStatus.CANDIDATE_NOT_FROZEN,
    )
    assert extract_xauusd_phase3a_score(snap, cand_prof) == 0.0

    # 4. Profile timeframe mismatch (e.g. 4h vs decision 15m) -> returns 0.0
    tf_mismatch_prof = Cycle3AProfile(
        name="XAUUSD_FROZEN_v1",
        target_instrument="XAUUSD",
        timeframe="4h",
        calibration_status=Cycle3ACalibrationStatus.PRODUCTION_FROZEN,
    )
    assert extract_xauusd_phase3a_score(snap, tf_mismatch_prof, decision_timeframe="15m") == 0.0

    # 5. Profile timeframe is None or empty -> returns 0.0
    tf_none_prof = Cycle3AProfile(
        name="XAUUSD_FROZEN_v1",
        target_instrument="XAUUSD",
        timeframe=None,
        calibration_status=Cycle3ACalibrationStatus.PRODUCTION_FROZEN,
    )
    assert extract_xauusd_phase3a_score(snap, tf_none_prof, decision_timeframe="15m") == 0.0

    tf_empty_prof = Cycle3AProfile(
        name="XAUUSD_FROZEN_v1",
        target_instrument="XAUUSD",
        timeframe="   ",
        calibration_status=Cycle3ACalibrationStatus.PRODUCTION_FROZEN,
    )
    assert extract_xauusd_phase3a_score(snap, tf_empty_prof, decision_timeframe="15m") == 0.0

    # 6. Decision timeframe empty -> returns 0.0
    assert extract_xauusd_phase3a_score(snap, frozen_prof, decision_timeframe="") == 0.0
    assert extract_xauusd_phase3a_score(snap, frozen_prof, decision_timeframe="  ") == 0.0

    # 7. Profile is None -> returns 0.0
    assert extract_xauusd_phase3a_score(snap, None) == 0.0


@pytest.mark.unit
def test_macro_safety_excluded_from_timing_score():
    """Verify macro blackout is NOT a component of timing score (macro is hard-gate only)."""
    configured_profile = Phase4SignalProfile(
        target_instrument="XAUUSD",
        calibration_status=Phase4CalibrationStatus.CANDIDATE_NOT_FROZEN,
        long_direction=SideDirectionPolicy(15.0, 10.0, 10.0, 10.0, 20.0, 15.0, 10.0, 10.0),
        short_direction=SideDirectionPolicy(15.0, 10.0, 10.0, 10.0, 20.0, 15.0, 10.0, 10.0),
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

    candle = _make_dummy_candle(open_p=2500.0, high_p=2505.0, low_p=2490.0, close_p=2504.0)  # Bullish rejection
    features_15m = _make_dummy_feature(rsi=52.0)
    features_1h = _make_dummy_feature(rsi=55.0)

    res = calculate_xauusd_dual_timing(
        candle_15m=candle,
        features_15m=features_15m,
        structure_15m=None,
        features_1h=features_1h,
        cycle_3a=None,
        profile=configured_profile,
    )

    # Invariant: Timing components contain exactly 5 components, none of which is Macro Safety
    comp_names = [c.name for c in res.long_timing.components]
    assert len(comp_names) == 5
    assert "Macro Safety & Blackout Clearance" not in comp_names
    assert "Macro Safety" not in comp_names


@pytest.mark.unit
def test_historical_xaut_timing_score_preserved():
    """Verify historical calculate_timing_score remains 100% frozen and operational."""
    res = calculate_timing_score(
        latest_closed_candle=None,
        features_15m=None,
        structure_15m=None,
    )
    assert res.max_score == 100.0
    assert res.total_score == 0.0
    assert len(res.components) == 6
