"""
Unit tests for XAUUSD Phase 5 target calculation, deterministic ordering, and RR validation.
"""
from datetime import datetime, timezone
from decimal import Decimal
import pytest

from engine.core.types import StructureResult, StructureType, StructureZone
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
    struct = StructureResult(t, StructureType.HH, None, None, None, (), (res1, res2))

    tp1, tp2, rr1, rr2, tp1_fp, tp2_fp, ok, err = calculate_long_targets(
        entry_min=Decimal("2500.00"),
        entry_mid=Decimal("2502.50"),
        entry_max=Decimal("2505.00"),
        stop_final=Decimal("2495.00"),
        structure_15m=struct,
        atr14=Decimal("5.00"),
        authoritative_t=t,
        policy=target_policy,
    )
    assert ok is True
    assert tp1 == Decimal("2525.00")
    assert tp2 == Decimal("2540.00")
    assert rr1 == Decimal("2.0")  # (2525 - 2505) / 10 = 20 / 10 = 2.0 >= 1.80
    assert rr2 == Decimal("3.5")  # (2540 - 2505) / 10 = 35 / 10 = 3.5
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
    struct = StructureResult(t, StructureType.LL, None, None, None, (), (sup1, sup2))

    tp1, tp2, rr1, rr2, tp1_fp, tp2_fp, ok, err = calculate_short_targets(
        entry_min=Decimal("2500.00"),
        entry_mid=Decimal("2502.50"),
        entry_max=Decimal("2505.00"),
        stop_final=Decimal("2510.00"),
        structure_15m=struct,
        atr14=Decimal("5.00"),
        authoritative_t=t,
        policy=target_policy,
    )
    assert ok is True
    # For SHORT, the boundary touched first as price falls is support.price_high = 2480.00
    assert tp1 == Decimal("2480.00")
    assert tp2 == Decimal("2465.00")
    assert rr1 == Decimal("2.0")  # (2500 - 2480) / 10 = 20 / 10 = 2.0 >= 1.80
    assert rr2 == Decimal("3.5")  # (2500 - 2465) / 10 = 35 / 10 = 3.5
    assert tp1_fp is not None
    assert tp2_fp is not None


@pytest.mark.unit
def test_no_structural_tp1_fails_closed(target_policy):
    """Absence of structural target fails closed; no synthetic TP1 is fabricated."""
    t = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    empty_struct = StructureResult(t, StructureType.HH, None, None, None, (), ())
    tp1, tp2, rr1, rr2, tp1_fp, tp2_fp, ok, err = calculate_long_targets(
        entry_min=Decimal("2500.00"),
        entry_mid=Decimal("2502.50"),
        entry_max=Decimal("2505.00"),
        stop_final=Decimal("2495.00"),
        structure_15m=empty_struct,
        atr14=Decimal("5.00"),
        authoritative_t=t,
        policy=target_policy,
    )
    assert ok is False
    assert tp1 is None
    assert "Missing confirmed structural resistance target" in err


@pytest.mark.unit
def test_near_boundary_unrounded_rr_proof_a_and_c(target_policy):
    """
    Near-boundary regression proof:
      Proof A: raw RR = 1.799... with min_rr_tp1 = 1.80 MUST BE INVALID.
               (Under old round-to-1.80, 1.7999 rounded to 1.80 and passed).
      Proof C: raw RR exactly 1.80 MUST BE VALID.
    """
    t = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    # entry_max = 2505.00, stop_final = 2495.00 -> planned_risk = 10.00

    # Proof A: tp1 = 2522.999 -> raw RR = (2522.999 - 2505.00) / 10 = 17.999 / 10 = 1.7999
    # Under unrounded math: 1.7999 < 1.80 -> INVALID.
    res_a = StructureZone("RESISTANCE", Decimal("2522.999"), Decimal("2525.00"), t, 2, True)
    struct_a = StructureResult(t, StructureType.HH, None, None, None, (), (res_a,))
    tp1_a, _, rr_a, _, _, _, ok_a, err_a = calculate_long_targets(
        entry_min=Decimal("2500.00"),
        entry_mid=Decimal("2502.50"),
        entry_max=Decimal("2505.00"),
        stop_final=Decimal("2495.00"),
        structure_15m=struct_a,
        atr14=Decimal("5.00"),
        authoritative_t=t,
        policy=target_policy,
    )
    assert ok_a is False
    assert rr_a == Decimal("1.7999")
    assert "below minimum required threshold" in err_a

    # Proof C: tp1 = 2523.00 -> raw RR = (2523.00 - 2505.00) / 10 = 18.00 / 10 = 1.80 exactly -> VALID
    res_c = StructureZone("RESISTANCE", Decimal("2523.00"), Decimal("2525.00"), t, 2, True)
    struct_c = StructureResult(t, StructureType.HH, None, None, None, (), (res_c,))
    tp1_c, _, rr_c, _, _, _, ok_c, err_c = calculate_long_targets(
        entry_min=Decimal("2500.00"),
        entry_mid=Decimal("2502.50"),
        entry_max=Decimal("2505.00"),
        stop_final=Decimal("2495.00"),
        structure_15m=struct_c,
        atr14=Decimal("5.00"),
        authoritative_t=t,
        policy=target_policy,
    )
    assert ok_c is True
    assert rr_c == Decimal("1.8")
    assert err_c is None


@pytest.mark.unit
def test_naive_target_structure_or_zone_created_at_rejected(target_policy):
    """Naive target StructureResult timestamp or naive zone created_at cannot contribute target evidence."""
    t = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    t_naive = datetime(2026, 9, 1, 8, 0, 0)

    # 1. Naive StructureResult timestamp contributes 0 zones
    res_aware = StructureZone("RESISTANCE", Decimal("2530.00"), Decimal("2535.00"), t, 2, True)
    struct_naive_ts = StructureResult(t_naive, StructureType.HH, None, None, None, (), (res_aware,))
    tp1, _, _, _, _, _, ok, err = calculate_long_targets(
        Decimal("2500.00"), Decimal("2502.50"), Decimal("2505.00"), Decimal("2495.00"),
        struct_naive_ts, Decimal("5.00"), t, target_policy
    )
    assert ok is False
    assert tp1 is None
    assert "Missing confirmed structural resistance target" in err

    # 2. Naive target zone created_at is ignored
    res_naive = StructureZone("RESISTANCE", Decimal("2530.00"), Decimal("2535.00"), t_naive, 2, True)
    struct_naive_zone = StructureResult(t, StructureType.HH, None, None, None, (), (res_naive,))
    tp1_z, _, _, _, _, _, ok_z, err_z = calculate_long_targets(
        Decimal("2500.00"), Decimal("2502.50"), Decimal("2505.00"), Decimal("2495.00"),
        struct_naive_zone, Decimal("5.00"), t, target_policy
    )
    assert ok_z is False
    assert tp1_z is None
    assert "Missing confirmed structural resistance target" in err_z

