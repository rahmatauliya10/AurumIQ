"""
Unit tests for XAUUSD Phase 5 target calculation, deterministic ordering, and RR validation.
"""
from datetime import datetime, timezone
from decimal import Decimal
import pytest

from engine.core.types import StructureZone
from engine.risk.xauusd_policy import SideRiskPolicy
from engine.risk.xauusd_targets import (
    calculate_long_targets,
    calculate_short_targets,
)


@pytest.fixture
def target_policy():
    return SideRiskPolicy(
        structure_buffer=Decimal("1.50"),
        atr_multiplier=Decimal("2.0"),
        max_stop_distance_atr=Decimal("4.0"),
        min_rr_tp1=Decimal("1.80"),
        tp2_atr_multiplier=Decimal("2.5"),
    )


@pytest.mark.unit
def test_long_targets_structural_tp1_and_tp2(target_policy):
    """LONG targets select nearest structural resistance and next farther resistance for TP2."""
    t = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)

    # entry_max = 2505.00, stop_final = 2495.00 -> risk = 10.00
    # min required tp1 = 2505 + 1.80 * 10 = 2523.00
    res1 = StructureZone("RESISTANCE", Decimal("2525.00"), Decimal("2530.00"), t, 2, True)
    res2 = StructureZone("RESISTANCE", Decimal("2540.00"), Decimal("2545.00"), t, 3, True)

    tp1, tp2, rr1, rr2, tp1_fp, tp2_fp, ok, err = calculate_long_targets(
        entry_min=Decimal("2500.00"),
        entry_mid=Decimal("2502.50"),
        entry_max=Decimal("2505.00"),
        stop_final=Decimal("2495.00"),
        structure_15m=None,
        atr14=Decimal("5.00"),
        authoritative_t=t,
        policy=target_policy,
        custom_zones=[res1, res2],
    )
    assert ok is True
    assert tp1 == Decimal("2525.00")
    assert tp2 == Decimal("2540.00")
    assert rr1 == Decimal("2.00")  # (2525 - 2505) / 10 = 20 / 10 = 2.00 >= 1.80
    assert rr2 == Decimal("3.50")  # (2540 - 2505) / 10 = 35 / 10 = 3.50
    assert tp1_fp is not None
    assert tp2_fp is not None


@pytest.mark.unit
def test_short_targets_structural_tp1_and_tp2(target_policy):
    """SHORT targets select nearest structural support and next farther support for TP2."""
    t = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)

    # entry_min = 2500.00, stop_final = 2510.00 -> risk = 10.00
    # min required tp1 = 2500 - 1.80 * 10 = 2482.00
    sup1 = StructureZone("SUPPORT", Decimal("2475.00"), Decimal("2480.00"), t, 2, True)
    sup2 = StructureZone("SUPPORT", Decimal("2460.00"), Decimal("2465.00"), t, 3, True)

    tp1, tp2, rr1, rr2, tp1_fp, tp2_fp, ok, err = calculate_short_targets(
        entry_min=Decimal("2500.00"),
        entry_mid=Decimal("2502.50"),
        entry_max=Decimal("2505.00"),
        stop_final=Decimal("2510.00"),
        structure_15m=None,
        atr14=Decimal("5.00"),
        authoritative_t=t,
        policy=target_policy,
        custom_zones=[sup1, sup2],
    )
    assert ok is True
    # For SHORT, the boundary touched first as price falls is support.price_high = 2480.00
    assert tp1 == Decimal("2480.00")
    assert tp2 == Decimal("2465.00")
    assert rr1 == Decimal("2.00")  # (2500 - 2480) / 10 = 20 / 10 = 2.00 >= 1.80
    assert rr2 == Decimal("3.50")  # (2500 - 2465) / 10 = 35 / 10 = 3.50
    assert tp1_fp is not None
    assert tp2_fp is not None


@pytest.mark.unit
def test_no_structural_tp1_fails_closed(target_policy):
    """Absence of structural target fails closed; no synthetic TP1 is fabricated."""
    t = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    tp1, tp2, rr1, rr2, tp1_fp, tp2_fp, ok, err = calculate_long_targets(
        entry_min=Decimal("2500.00"),
        entry_mid=Decimal("2502.50"),
        entry_max=Decimal("2505.00"),
        stop_final=Decimal("2495.00"),
        structure_15m=None,
        atr14=Decimal("5.00"),
        authoritative_t=t,
        policy=target_policy,
        custom_zones=[],  # No zones available
    )
    assert ok is False
    assert tp1 is None
    assert "Missing confirmed structural resistance target" in err


@pytest.mark.unit
def test_rr_gate_boundary(target_policy):
    """RR gate: exact planned_rr_tp1 == min_rr_tp1 is valid; < min_rr_tp1 is invalid."""
    t = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    # risk = 10.00 -> tp1 = 2505 + 18 = 2523.00 -> exactly 1.80
    res_exact = StructureZone("RESISTANCE", Decimal("2523.00"), Decimal("2525.00"), t, 2, True)

    tp1, _, rr1, _, _, _, ok, _ = calculate_long_targets(
        entry_min=Decimal("2500.00"),
        entry_mid=Decimal("2502.50"),
        entry_max=Decimal("2505.00"),
        stop_final=Decimal("2495.00"),
        structure_15m=None,
        atr14=Decimal("5.00"),
        authoritative_t=t,
        policy=target_policy,
        custom_zones=[res_exact],
    )
    assert ok is True
    assert rr1 == Decimal("1.80")

    # tp1 = 2522.90 -> RR = 17.90 / 10 = 1.79 < 1.80 -> invalid
    res_fail = StructureZone("RESISTANCE", Decimal("2522.90"), Decimal("2525.00"), t, 2, True)
    tp1, _, rr1, _, _, _, ok, err = calculate_long_targets(
        entry_min=Decimal("2500.00"),
        entry_mid=Decimal("2502.50"),
        entry_max=Decimal("2505.00"),
        stop_final=Decimal("2495.00"),
        structure_15m=None,
        atr14=Decimal("5.00"),
        authoritative_t=t,
        policy=target_policy,
        custom_zones=[res_fail],
    )
    assert ok is False
    assert "below minimum required threshold" in err
