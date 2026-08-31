"""
Hostile Negative and Edge-Case Suite for Phase 4 XAUUSD.
Covers Task 11 contracts.
"""
import math
import pytest

from engine.cycles.profile import CalibrationStatus, Cycle3AProfile
from engine.signals.profile import (
    Phase4CalibrationStatus,
    Phase4SignalProfile,
    SideDirectionPolicy,
    SideGatePolicy,
    SideTimingPolicy,
    normalize_xauusd_target,
    uncalibrated_xauusd_signal_profile,
)
from engine.signals.timing import extract_xauusd_phase3a_score


@pytest.mark.unit
def test_invalid_target_instruments_rejected():
    """Verify normalize_xauusd_target strictly rejects non-XAUUSD instruments."""
    invalid_targets = ["XAUT", "GOLD", "XAU", "BTCUSD", "ETHUSD", "XAUTUSDT", "", "USDXAU"]
    for sym in invalid_targets:
        with pytest.raises(ValueError):
            normalize_xauusd_target(sym)

    assert normalize_xauusd_target("XAUUSD") == "XAUUSD"
    assert normalize_xauusd_target("XAU/USD") == "XAUUSD"
    assert normalize_xauusd_target("xauusd") == "XAUUSD"


@pytest.mark.unit
def test_invalid_weights_and_thresholds_rejected():
    """Verify policies reject non-finite values, invalid sums, and non-monotonic thresholds."""
    # NaN in direction
    with pytest.raises(ValueError):
        _ = SideDirectionPolicy(float("nan"), 10.0, 10.0, 10.0, 20.0, 15.0, 10.0, 10.0).is_configured

    # Infinity in direction
    with pytest.raises(ValueError):
        _ = SideDirectionPolicy(15.0, float("inf"), 10.0, 10.0, 20.0, 15.0, 10.0, 10.0).is_configured

    # Sum != 100.0 in direction
    with pytest.raises(ValueError):
        _ = SideDirectionPolicy(20.0, 20.0, 20.0, 20.0, 20.0, 20.0, 20.0, 20.0).is_configured

    # Sum != 100.0 in timing
    with pytest.raises(ValueError):
        _ = SideTimingPolicy(30.0, 30.0, 30.0, 30.0, 30.0).is_configured

    # Non-monotonic gate thresholds (watch > ready)
    with pytest.raises(ValueError):
        _ = SideGatePolicy(
            threshold_watch_direction=80.0,
            threshold_ready_direction=70.0,
            threshold_ready_timing=70.0,
            threshold_window_direction=80.0,
            threshold_window_timing=80.0,
        ).is_configured


@pytest.mark.unit
def test_uncalibrated_profile_is_not_production_authorized():
    """Verify is_production_authorized is strictly False for all Phase 4 profiles."""
    p_uncal = uncalibrated_xauusd_signal_profile()
    assert p_uncal.is_production_authorized is False

    p_cand = Phase4SignalProfile(
        target_instrument="XAUUSD",
        calibration_status=Phase4CalibrationStatus.CANDIDATE_NOT_FROZEN,
        long_direction=SideDirectionPolicy(15.0, 10.0, 10.0, 10.0, 20.0, 15.0, 10.0, 10.0),
        short_direction=SideDirectionPolicy(15.0, 10.0, 10.0, 10.0, 20.0, 15.0, 10.0, 10.0),
        long_timing=SideTimingPolicy(25.0, 25.0, 20.0, 20.0, 10.0),
        short_timing=SideTimingPolicy(25.0, 25.0, 20.0, 20.0, 10.0),
        long_gate=SideGatePolicy(70.0, 75.0, 70.0, 80.0, 80.0),
        short_gate=SideGatePolicy(70.0, 75.0, 70.0, 80.0, 80.0),
    )
    assert p_cand.is_production_authorized is False


@pytest.mark.unit
def test_unfrozen_phase3a_profile_scores_zero():
    """Verify extract_xauusd_phase3a_score awards 0.0 pts if Phase 3A profile is not frozen for XAUUSD."""
    from datetime import datetime, timezone
    from engine.core.types import (
        CalendarSeasonalityContext,
        Cycle3ASnapshot,
        MacroEventContext,
        SampleQuality,
        SessionContext,
        SessionType,
        SwingDurationContext,
    )

    now = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    dummy_session = SessionContext(SessionType.LONDON, 0.5, 0.5, 0.5, 0.5, True)
    dummy_swing = SwingDurationContext(10, 15.0, 0.8, 0.8, SampleQuality.HIGH, True)
    dummy_macro = MacroEventContext(False, 180, 180, None, None, True)
    dummy_cal = CalendarSeasonalityContext(0, "Monday", 12, 8, False, 0.8, 0.8, SampleQuality.HIGH, 100.0)

    dummy_cycle_3a = Cycle3ASnapshot(
        timestamp=now,
        session=dummy_session,
        swing_duration=dummy_swing,
        macro_event=dummy_macro,
        calendar=dummy_cal,
        is_blocked_by_event=False,
        cycle_score_3a=85.0,
    )

    # Missing profile
    score_no_prof = extract_xauusd_phase3a_score(dummy_cycle_3a, None, 10.0)
    assert score_no_prof == 0.0

    # Uncalibrated profile
    unfrozen_prof = Cycle3AProfile(
        target_instrument="XAUUSD",
        calibration_status=CalibrationStatus.PENDING_DATA,
    )
    score_unfrozen = extract_xauusd_phase3a_score(dummy_cycle_3a, unfrozen_prof, 10.0)
    assert score_unfrozen == 0.0

    # Mismatched instrument profile
    mismatched_prof = Cycle3AProfile(
        target_instrument="XAUT",
        calibration_status=CalibrationStatus.PRODUCTION_FROZEN,
    )
    score_mismatched = extract_xauusd_phase3a_score(dummy_cycle_3a, mismatched_prof, 10.0)
    assert score_mismatched == 0.0



