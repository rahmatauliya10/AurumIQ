"""
Unit tests for XAUUSD Phase 5 risk policy, profile, and calibration governance.
"""
from decimal import Decimal
import pytest

from engine.core.types import EntryExecutionPolicy, Phase5CalibrationStatus
from engine.risk.xauusd_policy import (
    SideRiskPolicy,
    XauUsdExecutionPolicy,
    XauUsdRiskProfile,
    uncalibrated_xauusd_risk_profile,
)


@pytest.mark.unit
def test_uncalibrated_profile_defaults():
    """Uncalibrated XAUUSD profile defaults all empirical numerics to None."""
    profile = uncalibrated_xauusd_risk_profile()
    assert profile.name == "XAUUSD_UNCALIBRATED"
    assert profile.target_instrument == "XAUUSD"
    assert profile.calibration_status == Phase5CalibrationStatus.PENDING_PHASE6
    assert profile.is_production_authorized is False

    # All long numerics None
    assert profile.long_risk_policy.structure_buffer is None
    assert profile.long_risk_policy.atr_multiplier is None
    assert profile.long_risk_policy.max_stop_distance_atr is None
    assert profile.long_risk_policy.min_rr_tp1 is None
    assert profile.long_risk_policy.tp2_atr_multiplier is None
    assert profile.long_risk_policy.is_configured is False

    # All short numerics None
    assert profile.short_risk_policy.structure_buffer is None
    assert profile.short_risk_policy.is_configured is False

    # Execution policy
    assert profile.long_execution_policy.latency_seconds is None
    assert profile.long_execution_policy.is_configured_for(EntryExecutionPolicy.MARKET_AFTER_SIGNAL) is False


@pytest.mark.unit
def test_side_risk_policy_configured():
    """SideRiskPolicy is configured when core 4 fields are positive finite Decimals."""
    policy = SideRiskPolicy(
        structure_buffer=Decimal("1.50"),
        atr_multiplier=Decimal("2.0"),
        max_stop_distance_atr=Decimal("4.0"),
        min_rr_tp1=Decimal("1.80"),
    )
    assert policy.is_configured is True
    # tp2_atr_multiplier is optional
    assert policy.tp2_atr_multiplier is None

    policy_with_tp2 = SideRiskPolicy(
        structure_buffer=Decimal("1.50"),
        atr_multiplier=Decimal("2.0"),
        max_stop_distance_atr=Decimal("4.0"),
        min_rr_tp1=Decimal("1.80"),
        tp2_atr_multiplier=Decimal("2.5"),
    )
    assert policy_with_tp2.is_configured is True


@pytest.mark.unit
def test_side_risk_policy_invalid_numerics():
    """Negative or zero numerics fail completeness check."""
    assert SideRiskPolicy(
        structure_buffer=Decimal("-1.0"),
        atr_multiplier=Decimal("2.0"),
        max_stop_distance_atr=Decimal("4.0"),
        min_rr_tp1=Decimal("1.80"),
    ).is_configured is False

    assert SideRiskPolicy(
        structure_buffer=Decimal("1.0"),
        atr_multiplier=Decimal("0.0"),
        max_stop_distance_atr=Decimal("4.0"),
        min_rr_tp1=Decimal("1.80"),
    ).is_configured is False


@pytest.mark.unit
def test_execution_policy_modes():
    """Execution policy completeness is mode-specific."""
    market_policy = XauUsdExecutionPolicy(
        latency_seconds=2.0,
        slippage_pct=Decimal("0.01"),
    )
    assert market_policy.is_configured_for(EntryExecutionPolicy.MARKET_AFTER_SIGNAL) is True
    assert market_policy.is_configured_for(EntryExecutionPolicy.LIMIT_ZONE) is True
    # NEXT_BAR_OPEN requires synthetic_spread_pct
    assert market_policy.is_configured_for(EntryExecutionPolicy.NEXT_BAR_OPEN) is False

    next_bar_policy = XauUsdExecutionPolicy(
        latency_seconds=2.0,
        synthetic_spread_pct=Decimal("0.02"),
        slippage_pct=Decimal("0.01"),
    )
    assert next_bar_policy.is_configured_for(EntryExecutionPolicy.NEXT_BAR_OPEN) is True


@pytest.mark.unit
def test_production_authority_blocked():
    """Attempting to initialize profile with is_production_authorized=True raises ValueError."""
    with pytest.raises(ValueError, match="production authority is blocked"):
        XauUsdRiskProfile(is_production_authorized=True)


@pytest.mark.unit
def test_target_instrument_enforced():
    """Non-XAUUSD target raises ValueError."""
    with pytest.raises(ValueError, match="target must be 'XAUUSD'"):
        XauUsdRiskProfile(target_instrument="XAUT")
