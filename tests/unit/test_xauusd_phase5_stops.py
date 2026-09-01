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
    assert dist_atr == Decimal("4.00")

    # Exceeding threshold by 0.01 ATR fails
    excessive_policy = SideRiskPolicy(
        structure_buffer=Decimal("15.06"),  # 2500 - 15.06 = 2484.94 -> risk 20.06 -> 4.01 ATR
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
