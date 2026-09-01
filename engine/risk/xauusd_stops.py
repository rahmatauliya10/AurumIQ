"""
Structure-aware and ATR-guided stop loss calculation for XAUUSD (Phase 5).
Implements explicit, independent LONG and SHORT stop calculation and validity gates.
"""
from decimal import Decimal
from typing import Optional, Tuple

from engine.core.types import StructureZone
from engine.risk.xauusd_policy import SideRiskPolicy


def calculate_long_stops(
    support_zone: Optional[StructureZone],
    entry_min: Decimal,
    entry_mid: Decimal,
    entry_max: Decimal,
    atr14: Decimal,
    policy: SideRiskPolicy,
) -> Tuple[Decimal, Decimal, Decimal, Decimal, bool, Optional[str]]:
    """
    Calculate LONG structure stop, ATR stop guard, and composite stop loss.

    Formulas:
      stop_structure = support_zone.price_low - policy.structure_buffer
      stop_atr = entry_mid - (policy.atr_multiplier * atr14)
      stop_final = min(stop_structure, stop_atr)
      planned_risk = entry_max - stop_final
      stop_distance_atr = planned_risk / atr14

    Validity Gates:
      1. atr14 > 0 and finite
      2. policy is fully configured
      3. stop_final < entry_min
      4. planned_risk > 0
      5. stop_distance_atr <= policy.max_stop_distance_atr
    """
    if not isinstance(atr14, Decimal) or not atr14.is_finite() or atr14 <= Decimal("0"):
        return Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), False, "ATR14 must be strictly positive Decimal."

    if support_zone is None:
        return Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), False, "Missing confirmed active support zone."

    if (
        policy.structure_buffer is None
        or policy.atr_multiplier is None
        or policy.max_stop_distance_atr is None
    ):
        return Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), False, "Incomplete LONG risk policy configuration."

    stop_structure = (support_zone.price_low - policy.structure_buffer).quantize(Decimal("0.01"))
    stop_atr = (entry_mid - (policy.atr_multiplier * atr14)).quantize(Decimal("0.01"))
    stop_final = min(stop_structure, stop_atr)

    planned_risk = entry_max - stop_final
    if planned_risk <= Decimal("0"):
        return stop_structure, stop_atr, stop_final, Decimal("0"), False, "Stop loss must sit strictly below entry zone."

    if stop_final >= entry_min:
        stop_distance_atr = (planned_risk / atr14).quantize(Decimal("0.01"))
        return stop_structure, stop_atr, stop_final, stop_distance_atr, False, "Stop loss must sit strictly below entry_min."

    stop_distance_atr = (planned_risk / atr14).quantize(Decimal("0.01"))

    if stop_distance_atr > policy.max_stop_distance_atr:
        return (
            stop_structure,
            stop_atr,
            stop_final,
            stop_distance_atr,
            False,
            f"Stop distance ({stop_distance_atr} ATR) exceeds maximum allowable threshold ({policy.max_stop_distance_atr} ATR).",
        )

    return stop_structure, stop_atr, stop_final, stop_distance_atr, True, None


def calculate_short_stops(
    resistance_zone: Optional[StructureZone],
    entry_min: Decimal,
    entry_mid: Decimal,
    entry_max: Decimal,
    atr14: Decimal,
    policy: SideRiskPolicy,
) -> Tuple[Decimal, Decimal, Decimal, Decimal, bool, Optional[str]]:
    """
    Calculate SHORT structure stop, ATR stop guard, and composite stop loss.

    Formulas:
      stop_structure = resistance_zone.price_high + policy.structure_buffer
      stop_atr = entry_mid + (policy.atr_multiplier * atr14)
      stop_final = max(stop_structure, stop_atr)
      planned_risk = stop_final - entry_min
      stop_distance_atr = planned_risk / atr14

    Validity Gates:
      1. atr14 > 0 and finite
      2. policy is fully configured
      3. stop_final > entry_max
      4. planned_risk > 0
      5. stop_distance_atr <= policy.max_stop_distance_atr
    """
    if not isinstance(atr14, Decimal) or not atr14.is_finite() or atr14 <= Decimal("0"):
        return Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), False, "ATR14 must be strictly positive Decimal."

    if resistance_zone is None:
        return Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), False, "Missing confirmed active resistance zone."

    if (
        policy.structure_buffer is None
        or policy.atr_multiplier is None
        or policy.max_stop_distance_atr is None
    ):
        return Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), False, "Incomplete SHORT risk policy configuration."

    stop_structure = (resistance_zone.price_high + policy.structure_buffer).quantize(Decimal("0.01"))
    stop_atr = (entry_mid + (policy.atr_multiplier * atr14)).quantize(Decimal("0.01"))
    stop_final = max(stop_structure, stop_atr)

    planned_risk = stop_final - entry_min
    if planned_risk <= Decimal("0"):
        return stop_structure, stop_atr, stop_final, Decimal("0"), False, "Stop loss must sit strictly above entry zone."

    if stop_final <= entry_max:
        stop_distance_atr = (planned_risk / atr14).quantize(Decimal("0.01"))
        return stop_structure, stop_atr, stop_final, stop_distance_atr, False, "Stop loss must sit strictly above entry_max."

    stop_distance_atr = (planned_risk / atr14).quantize(Decimal("0.01"))

    if stop_distance_atr > policy.max_stop_distance_atr:
        return (
            stop_structure,
            stop_atr,
            stop_final,
            stop_distance_atr,
            False,
            f"Stop distance ({stop_distance_atr} ATR) exceeds maximum allowable threshold ({policy.max_stop_distance_atr} ATR).",
        )

    return stop_structure, stop_atr, stop_final, stop_distance_atr, True, None
