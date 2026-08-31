"""
Unit tests for Phase 4 XAUUSD Signal Profile, Normalizer, and Policy Fingerprinting.
Covers Task 2 contracts.
"""
import math
import pytest

from engine.core.types import FeedCriticality
from engine.signals.profile import (
    Phase4CalibrationStatus,
    Phase4FeedPolicy,
    Phase4SignalProfile,
    SideDirectionPolicy,
    SideGatePolicy,
    SideTimingPolicy,
    compute_phase4_policy_fingerprint,
    normalize_xauusd_target,
    uncalibrated_xauusd_signal_profile,
)


@pytest.mark.unit
def test_normalize_xauusd_target():
    """Verify normalize_xauusd_target accepts only XAUUSD and XAU/USD."""
    assert normalize_xauusd_target("XAUUSD") == "XAUUSD"
    assert normalize_xauusd_target("XAU/USD") == "XAUUSD"
    assert normalize_xauusd_target("xauusd") == "XAUUSD"
    assert normalize_xauusd_target("xau/usd") == "XAUUSD"

    # Hostile rejections
    for rejected in ("XAUT", "XAUTUSD", "XAUT/USDT", "XAU", "GOLD", "GOLD_REFERENCE", "", None, 123):
        with pytest.raises(ValueError):
            normalize_xauusd_target(rejected)


@pytest.mark.unit
def test_side_direction_policy_validation():
    """Verify SideDirectionPolicy validation rules."""
    # Unconfigured (defaults to None)
    unconf = SideDirectionPolicy()
    assert unconf.is_configured is False

    # Valid configured (sum == 100.0)
    valid = SideDirectionPolicy(
        weight_regime=15.0,
        weight_trend_1h=10.0,
        weight_trend_4h=10.0,
        weight_trend_1d=10.0,
        weight_structure_bos=20.0,
        weight_pullback=15.0,
        weight_momentum=10.0,
        weight_volume=10.0,
    )
    assert valid.is_configured is True

    # Sum != 100.0 raises ValueError
    with pytest.raises(ValueError, match="sum to exactly 100.0"):
        SideDirectionPolicy(
            weight_regime=10.0,
            weight_trend_1h=10.0,
            weight_trend_4h=10.0,
            weight_trend_1d=10.0,
            weight_structure_bos=10.0,
            weight_pullback=10.0,
            weight_momentum=10.0,
            weight_volume=10.0,
        ).is_configured

    # Negative weight raises ValueError
    with pytest.raises(ValueError, match="Negative direction weight"):
        SideDirectionPolicy(
            weight_regime=-5.0,
            weight_trend_1h=15.0,
            weight_trend_4h=15.0,
            weight_trend_1d=15.0,
            weight_structure_bos=20.0,
            weight_pullback=20.0,
            weight_momentum=10.0,
            weight_volume=10.0,
        ).is_configured

    # Non-finite weight (NaN/Inf) raises ValueError
    with pytest.raises(ValueError, match="Non-finite direction weight"):
        SideDirectionPolicy(
            weight_regime=float("nan"),
            weight_trend_1h=15.0,
            weight_trend_4h=15.0,
            weight_trend_1d=15.0,
            weight_structure_bos=20.0,
            weight_pullback=15.0,
            weight_momentum=10.0,
            weight_volume=10.0,
        ).is_configured


@pytest.mark.unit
def test_side_timing_policy_validation():
    """Verify SideTimingPolicy validation rules (macro safety excluded)."""
    # Unconfigured
    unconf = SideTimingPolicy()
    assert unconf.is_configured is False

    # Valid configured (sum == 100.0 across 5 components)
    valid = SideTimingPolicy(
        weight_entry_zone=25.0,
        weight_reversal_confirmation_15m=25.0,
        weight_momentum_turn_15m_1h=20.0,
        weight_phase3a=20.0,
        weight_volume_response=10.0,
    )
    assert valid.is_configured is True

    # Sum != 100.0 raises ValueError
    with pytest.raises(ValueError, match="sum to exactly 100.0"):
        SideTimingPolicy(
            weight_entry_zone=20.0,
            weight_reversal_confirmation_15m=20.0,
            weight_momentum_turn_15m_1h=20.0,
            weight_phase3a=20.0,
            weight_volume_response=10.0,
        ).is_configured

    # Non-finite weight raises ValueError
    with pytest.raises(ValueError, match="Non-finite timing weight"):
        SideTimingPolicy(
            weight_entry_zone=float("inf"),
            weight_reversal_confirmation_15m=25.0,
            weight_momentum_turn_15m_1h=20.0,
            weight_phase3a=20.0,
            weight_volume_response=10.0,
        ).is_configured


@pytest.mark.unit
def test_side_gate_policy_validation():
    """Verify SideGatePolicy monotonicity and range validation."""
    # Unconfigured
    unconf = SideGatePolicy()
    assert unconf.is_configured is False

    # Valid monotonic
    valid = SideGatePolicy(
        threshold_watch_direction=70.0,
        threshold_ready_direction=75.0,
        threshold_ready_timing=70.0,
        threshold_window_direction=80.0,
        threshold_window_timing=80.0,
    )
    assert valid.is_configured is True

    # Non-monotonic direction (watch > ready) raises ValueError
    with pytest.raises(ValueError, match="monotonic"):
        SideGatePolicy(
            threshold_watch_direction=78.0,
            threshold_ready_direction=75.0,
            threshold_ready_timing=70.0,
            threshold_window_direction=80.0,
            threshold_window_timing=80.0,
        ).is_configured

    # Non-monotonic timing (ready > window) raises ValueError
    with pytest.raises(ValueError, match="monotonic"):
        SideGatePolicy(
            threshold_watch_direction=70.0,
            threshold_ready_direction=75.0,
            threshold_ready_timing=85.0,
            threshold_window_direction=80.0,
            threshold_window_timing=80.0,
        ).is_configured

    # Out-of-range threshold raises ValueError
    with pytest.raises(ValueError, match="within"):
        SideGatePolicy(
            threshold_watch_direction=70.0,
            threshold_ready_direction=75.0,
            threshold_ready_timing=70.0,
            threshold_window_direction=105.0,
            threshold_window_timing=80.0,
        ).is_configured


@pytest.mark.unit
def test_phase4_signal_profile_target_invariant_rejection():
    """Verify XAUUSD cannot be instantiated with LEGACY_REFERENCE calibration status."""
    with pytest.raises(ValueError, match="LEGACY_REFERENCE"):
        Phase4SignalProfile(
            target_instrument="XAUUSD",
            calibration_status=Phase4CalibrationStatus.LEGACY_REFERENCE,
        )

    with pytest.raises(ValueError, match="LEGACY_REFERENCE"):
        Phase4SignalProfile(
            target_instrument="XAU/USD",
            calibration_status=Phase4CalibrationStatus.LEGACY_REFERENCE,
        )


@pytest.mark.unit
def test_uncalibrated_xauusd_signal_profile_factory():
    """Verify uncalibrated_xauusd_signal_profile factory properties."""
    prof = uncalibrated_xauusd_signal_profile()
    assert prof.target_instrument == "XAUUSD"
    assert prof.calibration_status == Phase4CalibrationStatus.PENDING_PHASE6
    assert prof.is_fully_configured is False
    assert prof.is_production_authorized is False
    assert prof.feed_policy.primary_15m == FeedCriticality.CRITICAL
    assert prof.feed_policy.macro_blackout == FeedCriticality.CRITICAL


@pytest.mark.unit
def test_compute_phase4_policy_fingerprint():
    """Verify deterministic policy fingerprinting and immutability."""
    prof1 = uncalibrated_xauusd_signal_profile()
    prof2 = uncalibrated_xauusd_signal_profile()
    fp1 = compute_phase4_policy_fingerprint(prof1)
    fp2 = compute_phase4_policy_fingerprint(prof2)
    assert fp1 == fp2
    assert len(fp1) == 64

    # Deep immutability of details
    prof = Phase4SignalProfile(
        target_instrument="XAUUSD",
        details={"key": "val", "nested": {"a": 1}},
    )
    with pytest.raises((TypeError, AttributeError)):
        prof.details["key"] = "new_val"  # MappingProxyType prevents mutation
