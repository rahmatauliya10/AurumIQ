"""ATR-normalized support and resistance bounding zones."""
from decimal import Decimal
from typing import List, Optional, Sequence, Tuple
from engine.core.types import StructureZone, SwingPoint, SwingType


def cluster_structure_zones(
    swings: Sequence[SwingPoint],
    atr: Optional[Decimal],
    zone_atr_factor: Decimal = Decimal("0.5"),
    max_zones: int = 5,
) -> Tuple[StructureZone, ...]:
    """
    Cluster confirmed swing points into ATR-normalized Support and Resistance bounding zones.
    """
    if not swings or not atr or atr <= 0:
        return ()

    tolerance = atr * zone_atr_factor
    support_swings = [s for s in swings if s.swing_type == SwingType.LOW]
    resistance_swings = [s for s in swings if s.swing_type == SwingType.HIGH]

    zones: List[StructureZone] = []

    # 1. Process Resistance Zones (from Swing Highs)
    for s in resistance_swings[-8:]:
        # Check if overlaps with an existing resistance zone
        merged = False
        for idx, z in enumerate(zones):
            if z.zone_type == "RESISTANCE" and abs(s.price - ((z.price_low + z.price_high) / 2)) <= tolerance:
                new_low = min(z.price_low, s.price - (tolerance / 2))
                new_high = max(z.price_high, s.price + (tolerance / 2))
                zones[idx] = StructureZone(
                    zone_type="RESISTANCE",
                    price_low=new_low.quantize(Decimal("0.00000001")),
                    price_high=new_high.quantize(Decimal("0.00000001")),
                    created_at=z.created_at,
                    touches=z.touches + 1,
                    is_active=True,
                )
                merged = True
                break
        if not merged:
            zones.append(
                StructureZone(
                    zone_type="RESISTANCE",
                    price_low=(s.price - (tolerance / 2)).quantize(Decimal("0.00000001")),
                    price_high=(s.price + (tolerance / 2)).quantize(Decimal("0.00000001")),
                    created_at=s.timestamp,
                    touches=1,
                    is_active=True,
                )
            )

    # 2. Process Support Zones (from Swing Lows)
    for s in support_swings[-8:]:
        merged = False
        for idx, z in enumerate(zones):
            if z.zone_type == "SUPPORT" and abs(s.price - ((z.price_low + z.price_high) / 2)) <= tolerance:
                new_low = min(z.price_low, s.price - (tolerance / 2))
                new_high = max(z.price_high, s.price + (tolerance / 2))
                zones[idx] = StructureZone(
                    zone_type="SUPPORT",
                    price_low=new_low.quantize(Decimal("0.00000001")),
                    price_high=new_high.quantize(Decimal("0.00000001")),
                    created_at=z.created_at,
                    touches=z.touches + 1,
                    is_active=True,
                )
                merged = True
                break
        if not merged:
            zones.append(
                StructureZone(
                    zone_type="SUPPORT",
                    price_low=(s.price - (tolerance / 2)).quantize(Decimal("0.00000001")),
                    price_high=(s.price + (tolerance / 2)).quantize(Decimal("0.00000001")),
                    created_at=s.timestamp,
                    touches=1,
                    is_active=True,
                )
            )

    # Order by touches and recency, take up to max_zones
    zones.sort(key=lambda z: (z.touches, z.created_at), reverse=True)
    return tuple(zones[:max_zones])
