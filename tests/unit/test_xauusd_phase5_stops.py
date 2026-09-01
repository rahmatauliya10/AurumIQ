"""
Unit tests for XAUUSD Phase 5 side-aware stop calculations.
"""
from datetime import datetime, timezone
from decimal import Decimal
import pytest

from engine.core.types import StructureZone
from engine.risk.xauusd_policy import SideRiskPolicy
from engine.risk.xauusd_stops import (
    calculate_long_stops,
    calculate_short_stops,
)


@pytest.fixture
def valid_policy():
    return SideRiskPolicy(
        structure_buffer=Decimal("1.50"),
        atr_multiplier=Decimal("2.0"),
        max_stop_distance_atr=Decimal("4.0"),
        min_rr_tp1=Decimal("1.80"),
    )


@pytest.mark.unit
def test_long_stop_structure_and_atr(valid_policy):
    """LONG stop sits below both structure and ATR guards; stop_final < entry_min."""
    ts = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    support = StructureZone("SUPPORT", Decimal("2500.00"), Decimal("2505.00"), ts, 2, True)

    # entry: min=2500, mid=2502.50, max=2505
    # structure stop = 2500.00 - 1.50 = 2498.50
    # atr stop = 2502.50 - (2.0 * 5.0) = 2492.50
    # stop_final = min(2498.50, 2492.50) = 2492.50
    # planned risk = 2505.00 - 2492.50 = 12.50
    # stop_distance_atr = 12.50 / 5.0 = 2.50 ATR <= 4.0 ATR
    stop_struct, stop_atr, stop_final, stop_dist_atr, ok, err = calculate_long_stops(
        support_zone=support,
        entry_min=Decimal("2500.00"),
        entry_mid=Decimal("2502.50"),
        entry_max=Decimal("2505.00"),
        atr14=Decimal("5.00"),
        policy=valid_policy,
    )
    assert ok is True
    assert err is None
    assert stop_struct == Decimal("2498.50")
    assert stop_atr == Decimal("2492.50")
    assert stop_final == Decimal("2492.50")
    assert stop_dist_atr == Decimal("2.50")
    assert stop_final < Decimal("2500.00")


@pytest.mark.unit
def test_short_stop_structure_and_atr(valid_policy):
    """SHORT stop sits above both structure and ATR guards; stop_final > entry_max."""
    ts = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    resistance = StructureZone("RESISTANCE", Decimal("2500.00"), Decimal("2505.00"), ts, 2, True)

    # entry: min=2500, mid=2502.50, max=2505
    # structure stop = 2505.00 + 1.50 = 2506.50
    # atr stop = 2502.50 + (2.0 * 5.0) = 2512.50
    # stop_final = max(2506.50, 2512.50) = 2512.50
    # planned risk = 2512.50 - 2500.00 = 12.50
    # stop_distance_atr = 12.50 / 5.0 = 2.50 ATR <= 4.0 ATR
    stop_struct, stop_atr, stop_final, stop_dist_atr, ok, err = calculate_short_stops(
        resistance_zone=resistance,
        entry_min=Decimal("2500.00"),
        entry_mid=Decimal("2502.50"),
        entry_max=Decimal("2505.00"),
        atr14=Decimal("5.00"),
        policy=valid_policy,
    )
    assert ok is True
    assert err is None
    assert stop_struct == Decimal("2506.50")
    assert stop_atr == Decimal("2512.50")
    assert stop_final == Decimal("2512.50")
    assert stop_dist_atr == Decimal("2.50")
    assert stop_final > Decimal("2505.00")


@pytest.mark.unit
def test_stop_distance_atr_boundary(valid_policy):
    """Exact stop_distance_atr == max_stop_distance_atr is valid; > max is invalid."""
    ts = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    support = StructureZone("SUPPORT", Decimal("2500.00"), Decimal("2505.00"), ts, 2, True)

    # Max allowed stop distance = 4.0 * 5.0 = 20.00 -> stop_final = 2505.00 - 20.00 = 2485.00
    tight_policy = SideRiskPolicy(
        structure_buffer=Decimal("15.00"),  # 2500 - 15 = 2485.00
        atr_multiplier=Decimal("3.5"),      # 2502.50 - 17.50 = 2485.00
        max_stop_distance_atr=Decimal("4.0"),
        min_rr_tp1=Decimal("1.80"),
    )
    _, _, _, dist_atr, ok, err = calculate_long_stops(
        support_zone=support,
        entry_min=Decimal("2500.00"),
        entry_mid=Decimal("2502.50"),
        entry_max=Decimal("2505.00"),
        atr14=Decimal("5.00"),
        policy=tight_policy,
    )
    assert ok is True
    assert dist_atr == Decimal("4.0")

    # Exceeding threshold by 0.01 ATR fails
    excessive_policy = SideRiskPolicy(
        structure_buffer=Decimal("15.06"),  # 2500 - 15.06 = 2484.94 -> risk 20.06 -> 4.012 ATR
        atr_multiplier=Decimal("3.5"),
        max_stop_distance_atr=Decimal("4.0"),
        min_rr_tp1=Decimal("1.80"),
    )
    _, _, _, dist_atr, ok, err = calculate_long_stops(
        support_zone=support,
        entry_min=Decimal("2500.00"),
        entry_mid=Decimal("2502.50"),
        entry_max=Decimal("2505.00"),
        atr14=Decimal("5.00"),
        policy=excessive_policy,
    )
    assert ok is False
    assert "exceeds maximum allowable threshold" in err


@pytest.mark.unit
def test_near_boundary_unrounded_stop_distance_proof_b_and_d():
    """
    Near-boundary regression proof:
      Proof B: raw stop_distance_atr = 4.004... with max_stop_distance_atr = 4.0 MUST BE INVALID.
               (Under old round-to-4.00, 4.004 would round to 4.00 and erroneously pass).
      Proof D: raw stop_distance_atr exactly 4.0 MUST BE VALID.
    """
    ts = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    support = StructureZone("SUPPORT", Decimal("2500.00"), Decimal("2505.00"), ts, 2, True)

    policy = SideRiskPolicy(
        structure_buffer=Decimal("1.00"),
        atr_multiplier=Decimal("1.0"),
        max_stop_distance_atr=Decimal("4.0"),
        min_rr_tp1=Decimal("1.80"),
    )

    # Proof B: planned risk = 20.02, atr14 = 5.0 -> raw stop_distance = 20.02 / 5.0 = 4.004
    # With unrounded comparison: 4.004 > 4.0 -> INVALID.
    # (Old code rounded 4.004 to 4.00, which equaled 4.00 and passed).
    entry_max_b = Decimal("2505.02")
    entry_mid_b = Decimal("2502.51")
    entry_min_b = Decimal("2500.00")
    # support.price_low - buffer = 2500.00 - 15.00 = 2485.00
    policy_b = SideRiskPolicy(
        structure_buffer=Decimal("15.00"),
        atr_multiplier=Decimal("1.0"),
        max_stop_distance_atr=Decimal("4.0"),
        min_rr_tp1=Decimal("1.80"),
    )
    # stop_final = 2485.00, planned_risk = 2505.02 - 2485.00 = 20.02, atr = 5.0 -> 4.004
    _, _, _, dist_b, ok_b, err_b = calculate_long_stops(
        support_zone=support,
        entry_min=entry_min_b,
        entry_mid=entry_mid_b,
        entry_max=entry_max_b,
        atr14=Decimal("5.0"),
        policy=policy_b,
    )
    assert ok_b is False
    assert dist_b == Decimal("4.004")
    assert "exceeds maximum allowable threshold" in err_b

    # Proof D: planned risk = 20.00, atr14 = 5.0 -> raw stop_distance = 20.00 / 5.0 = 4.0 exactly -> VALID
    entry_max_d = Decimal("2505.00")
    entry_mid_d = Decimal("2502.50")
    entry_min_d = Decimal("2500.00")
    _, _, _, dist_d, ok_d, err_d = calculate_long_stops(
        support_zone=support,
        entry_min=entry_min_d,
        entry_mid=entry_mid_d,
        entry_max=entry_max_d,
        atr14=Decimal("5.0"),
        policy=policy_b,
    )
    assert ok_d is True
    assert dist_d == Decimal("4.0")
    assert err_d is None


@pytest.mark.unit
def test_long_stop_atr_strictly_derives_from_entry_mid_not_entry_min(valid_policy):
    """
    Explicit contract proof: LONG stop_atr is derived strictly from entry_mid,
    and would fail if entry_min or entry_max were substituted.
    """
    ts = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    support = StructureZone("SUPPORT", Decimal("2500.00"), Decimal("2510.00"), ts, 2, True)

    entry_min = Decimal("2500.00")
    entry_max = Decimal("2510.00")
    entry_mid = Decimal("2505.00")
    assert entry_min != entry_mid
    assert entry_max != entry_mid

    _, stop_atr, _, _, ok, _ = calculate_long_stops(
        support_zone=support,
        entry_min=entry_min,
        entry_mid=entry_mid,
        entry_max=entry_max,
        atr14=Decimal("4.00"),
        policy=valid_policy,
    )
    assert ok is True
    assert stop_atr == Decimal("2497.00")
    assert stop_atr != entry_min - (valid_policy.atr_multiplier * Decimal("4.00"))
    assert stop_atr != entry_max - (valid_policy.atr_multiplier * Decimal("4.00"))


@pytest.mark.unit
def test_short_stop_atr_strictly_derives_from_entry_mid_not_entry_max(valid_policy):
    """
    Explicit contract proof: SHORT stop_atr is derived strictly from entry_mid,
    and would fail if entry_max or entry_min were substituted.
    """
    ts = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    resistance = StructureZone("RESISTANCE", Decimal("2500.00"), Decimal("2510.00"), ts, 2, True)

    entry_min = Decimal("2500.00")
    entry_max = Decimal("2510.00")
    entry_mid = Decimal("2505.00")
    assert entry_min != entry_mid
    assert entry_max != entry_mid

    _, stop_atr, _, _, ok, _ = calculate_short_stops(
        resistance_zone=resistance,
        entry_min=entry_min,
        entry_mid=entry_mid,
        entry_max=entry_max,
        atr14=Decimal("4.00"),
        policy=valid_policy,
    )
    assert ok is True
    assert stop_atr == Decimal("2513.00")
    assert stop_atr != entry_max + (valid_policy.atr_multiplier * Decimal("4.00"))
    assert stop_atr != entry_min + (valid_policy.atr_multiplier * Decimal("4.00"))
