"""Structure-aware and ATR-guided stop loss calculation (Phase 5)."""
from decimal import Decimal
from typing import Optional, Tuple
from engine.core.types import StructureZone


def calculate_stops(
    support_zone: Optional[StructureZone],
    entry_min: Decimal,
    entry_mid: Decimal,
    entry_max: Decimal,
    atr14: float,
    structure_buffer: Decimal = Decimal("1.0"),
    atr_multiplier: Decimal = Decimal("2.0"),
    max_stop_distance_atr: Decimal = Decimal("4.0"),
) -> Tuple[Decimal, Decimal, Decimal, Decimal, bool, Optional[str]]:
    """
    Calculate structure stop, ATR stop guard, and final composite stop loss.

    Formulas:
      stop_structure = support_zone.price_low - structure_buffer
      stop_atr = entry_mid - (atr_multiplier * ATR14)
      stop_final = min(stop_structure, stop_atr)
      stop_distance_atr = (entry_max - stop_final) / ATR14

    Returns:
      (stop_structure, stop_atr, stop_final, stop_distance_atr, is_valid, error_reason)
    """
    if atr14 <= 0.0:
        return Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), False, "ATR14 must be strictly positive."

    atr_dec = Decimal(str(round(atr14, 8)))

    if support_zone is None:
        return Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), False, "Missing confirmed active support zone."

    stop_structure = (support_zone.price_low - structure_buffer).quantize(Decimal("0.01"))
    stop_atr = (entry_mid - (atr_multiplier * atr_dec)).quantize(Decimal("0.01"))

    # Final stop sits strictly below both structural invalidation and ATR guard
    stop_final = min(stop_structure, stop_atr)

    stop_distance = entry_max - stop_final
    if stop_distance <= Decimal("0"):
        return stop_structure, stop_atr, stop_final, Decimal("0"), False, "Stop loss must sit strictly below entry zone."

    stop_distance_atr = (stop_distance / atr_dec).quantize(Decimal("0.01"))

    if stop_final >= entry_min:
        return stop_structure, stop_atr, stop_final, stop_distance_atr, False, "Stop loss must sit strictly below entry_min."

    if stop_distance_atr > max_stop_distance_atr:
        return (
            stop_structure,
            stop_atr,
            stop_final,
            stop_distance_atr,
            False,
            f"Stop distance ({stop_distance_atr} ATR) exceeds maximum allowable threshold ({max_stop_distance_atr} ATR).",
        )

    return stop_structure, stop_atr, stop_final, stop_distance_atr, True, None
