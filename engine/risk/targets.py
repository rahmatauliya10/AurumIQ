"""Target calculation (TP1/TP2) and Reward-to-Risk (RR) validation (Phase 5)."""
from decimal import Decimal
from typing import List, Optional, Tuple
from engine.core.types import StructureZone, StructureResult


def calculate_targets(
    entry_max: Decimal,
    entry_mid: Decimal,
    stop_final: Decimal,
    structure_15m: Optional[StructureResult],
    atr14: float,
    structure_4h: Optional[StructureResult] = None,
    min_rr_tp1: Decimal = Decimal("1.80"),
    tp2_atr_multiplier: Decimal = Decimal("2.5"),
) -> Tuple[Decimal, Decimal, Decimal, Decimal, bool, Optional[str]]:
    """
    Calculate TP1 (nearest meaningful confirmed resistance) and TP2 (expansion target).

    Formulas:
      risk = entry_max - stop_final
      rr_tp1 = (tp1 - entry_max) / risk
      rr_tp2 = (tp2 - entry_max) / risk

    Strict Invariants:
      1. Nearest resistance cannot be skipped or cherry-picked to artificially inflate RR.
      2. If rr_tp1 < min_rr_tp1 (1.80) -> Risk plan is rejected (is_valid = False).
      3. TP2 cannot rescue an invalid TP1.
    """
    risk = entry_max - stop_final
    if risk <= Decimal("0"):
        return Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), False, "Invalid risk distance (stop >= entry)."

    atr_dec = Decimal(str(round(atr14, 8))) if atr14 > 0 else Decimal("1.0")

    # Gather candidate confirmed resistances known at evaluation timestamp
    candidates: List[StructureZone] = []

    if structure_15m is not None:
        for zone in structure_15m.zones:
            if zone.zone_type == "RESISTANCE" and zone.price_low > entry_max:
                if not any(c.price_low == zone.price_low for c in candidates):
                    candidates.append(zone)

    if structure_4h is not None:
        for zone in structure_4h.zones:
            if zone.zone_type == "RESISTANCE" and zone.price_low > entry_max:
                if not any(c.price_low == zone.price_low for c in candidates):
                    candidates.append(zone)

    # Sort strictly by price_low ascending to get nearest resistance
    candidates.sort(key=lambda z: z.price_low)

    if candidates:
        nearest_res = candidates[0]
        tp1 = nearest_res.price_low.quantize(Decimal("0.01"))
        rr_tp1 = ((tp1 - entry_max) / risk).quantize(Decimal("0.01"))

        if len(candidates) > 1:
            tp2 = candidates[1].price_low.quantize(Decimal("0.01"))
        else:
            tp2 = (entry_mid + (tp2_atr_multiplier * atr_dec)).quantize(Decimal("0.01"))
            if tp2 <= tp1:
                tp2 = (tp1 + (Decimal("1.0") * atr_dec)).quantize(Decimal("0.01"))

        rr_tp2 = ((tp2 - entry_max) / risk).quantize(Decimal("0.01"))

        # Hard Gate A07: Nearest resistance must yield RR >= 1.80
        if rr_tp1 < min_rr_tp1:
            return (
                tp1,
                tp2,
                rr_tp1,
                rr_tp2,
                False,
                f"Nearest confirmed resistance at {tp1} yields RR {rr_tp1:.2f} below minimum required threshold {min_rr_tp1:.2f}.",
            )

        return tp1, tp2, rr_tp1, rr_tp2, True, None

    # Fallback when no structural resistance exists above entry
    tp1 = (entry_max + (min_rr_tp1 * risk)).quantize(Decimal("0.01"))
    rr_tp1 = min_rr_tp1
    tp2 = (entry_mid + (tp2_atr_multiplier * atr_dec)).quantize(Decimal("0.01"))
    if tp2 <= tp1:
        tp2 = (tp1 + (Decimal("1.0") * atr_dec)).quantize(Decimal("0.01"))
    rr_tp2 = ((tp2 - entry_max) / risk).quantize(Decimal("0.01"))

    return tp1, tp2, rr_tp1, rr_tp2, True, None
