"""
Deterministic target calculation (TP1/TP2) and Reward-to-Risk (RR) validation for XAUUSD (Phase 5).
Implements total deterministic target ordering, strictly-beyond TP2 rule, and structural-only TP1 gating.
"""
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from engine.core.types import StructureResult, StructureZone
from engine.risk.xauusd_fingerprints import (
    canonical_utc_timestamp,
    compute_zone_fingerprint,
)
from engine.risk.xauusd_policy import SideRiskPolicy


def _collect_pit_zones(
    structure_15m: Optional[StructureResult],
    structure_4h: Optional[StructureResult],
    authoritative_t: datetime,
) -> List[StructureZone]:
    """
    Collect, PIT-validate, and deduplicate zones from 15m and 4H structures.
    """
    eligible_zones: List[StructureZone] = []

    if structure_15m is not None and structure_15m.timestamp <= authoritative_t:
        for z in structure_15m.zones:
            if z.created_at <= authoritative_t and z.is_active:
                eligible_zones.append(z)

    if structure_4h is not None and structure_4h.timestamp <= authoritative_t:
        for z in structure_4h.zones:
            if z.created_at <= authoritative_t and z.is_active:
                eligible_zones.append(z)

    # Deduplicate deterministically by zone_fingerprint
    unique_by_fp: Dict[str, StructureZone] = {}
    for zone in eligible_zones:
        fp = compute_zone_fingerprint(zone)
        if fp not in unique_by_fp:
            unique_by_fp[fp] = zone

    return list(unique_by_fp.values())


def calculate_long_targets(
    entry_min: Decimal,
    entry_mid: Decimal,
    entry_max: Decimal,
    stop_final: Decimal,
    structure_15m: Optional[StructureResult],
    atr14: Decimal,
    authoritative_t: datetime,
    policy: SideRiskPolicy,
    structure_4h: Optional[StructureResult] = None,
) -> Tuple[
    Optional[Decimal],
    Optional[Decimal],
    Optional[Decimal],
    Optional[Decimal],
    Optional[str],
    Optional[str],
    bool,
    Optional[str],
]:
    """
    Calculate LONG TP1, optional TP2, and conservative Reward-to-Risk.

    Formulas:
      planned_risk = entry_max - stop_final
      planned_rr_tp1 = (tp1 - entry_max) / planned_risk

    Strict Invariants:
      1. TP1 must be nearest confirmed structural resistance strictly above entry_max.
      2. No synthetic structural TP1 fabrication allowed.
      3. Total deterministic ordering: price_low ASC, created_at ASC, price_high ASC, zone_fp ASC.
      4. TP2 must be strictly beyond TP1 (candidate.price_low > tp1).
      5. planned_rr_tp1 >= min_rr_tp1 (unrounded comparison, equality is valid).
    """
    planned_risk = entry_max - stop_final
    if planned_risk <= Decimal("0"):
        return None, None, None, None, None, None, False, "Invalid risk distance (stop >= entry)."

    if policy.min_rr_tp1 is None:
        return None, None, None, None, None, None, False, "Incomplete LONG risk policy (min_rr_tp1 is None)."

    # Gather candidate zones strictly from PIT StructureResults
    candidates = _collect_pit_zones(structure_15m, structure_4h, authoritative_t)

    # Filter to valid resistances above entry_max
    long_candidates = [
        z for z in candidates
        if z.zone_type == "RESISTANCE" and z.price_low > entry_max
    ]

    # Total deterministic sorting: price_low ASC, created_at ASC, price_high ASC, zone_fp ASC
    long_candidates.sort(key=lambda z: (
        z.price_low,
        canonical_utc_timestamp(z.created_at),
        z.price_high,
        compute_zone_fingerprint(z),
    ))

    if not long_candidates:
        return None, None, None, None, None, None, False, "Missing confirmed structural resistance target above entry zone."

    tp1_zone = long_candidates[0]
    tp1 = tp1_zone.price_low
    tp1_zone_fp = compute_zone_fingerprint(tp1_zone)

    planned_rr_tp1 = (tp1 - entry_max) / planned_risk

    # TP2 Resolution: must be strictly beyond TP1
    tp2: Optional[Decimal] = None
    tp2_zone_fp: Optional[str] = None

    for candidate in long_candidates[1:]:
        cand_price = candidate.price_low
        if cand_price > tp1:
            tp2 = cand_price
            tp2_zone_fp = compute_zone_fingerprint(candidate)
            break

    # Optional synthetic TP2 fallback if configured and strictly beyond TP1
    if tp2 is None and policy.tp2_atr_multiplier is not None:
        syn_tp2 = entry_mid + (policy.tp2_atr_multiplier * atr14)
        if syn_tp2 > tp1:
            tp2 = syn_tp2
            tp2_zone_fp = None

    planned_rr_tp2: Optional[Decimal] = None
    if tp2 is not None:
        planned_rr_tp2 = (tp2 - entry_max) / planned_risk

    # Minimum RR Gate (unrounded raw comparison)
    if planned_rr_tp1 < policy.min_rr_tp1:
        return (
            tp1,
            tp2,
            planned_rr_tp1,
            planned_rr_tp2,
            tp1_zone_fp,
            tp2_zone_fp,
            False,
            f"Nearest confirmed resistance at {tp1} yields RR {planned_rr_tp1} below minimum required threshold {policy.min_rr_tp1}.",
        )

    return tp1, tp2, planned_rr_tp1, planned_rr_tp2, tp1_zone_fp, tp2_zone_fp, True, None


def calculate_short_targets(
    entry_min: Decimal,
    entry_mid: Decimal,
    entry_max: Decimal,
    stop_final: Decimal,
    structure_15m: Optional[StructureResult],
    atr14: Decimal,
    authoritative_t: datetime,
    policy: SideRiskPolicy,
    structure_4h: Optional[StructureResult] = None,
) -> Tuple[
    Optional[Decimal],
    Optional[Decimal],
    Optional[Decimal],
    Optional[Decimal],
    Optional[str],
    Optional[str],
    bool,
    Optional[str],
]:
    """
    Calculate SHORT TP1, optional TP2, and conservative Reward-to-Risk.

    Formulas:
      planned_risk = stop_final - entry_min
      planned_rr_tp1 = (entry_min - tp1) / planned_risk

    Strict Invariants:
      1. TP1 must be nearest confirmed structural support strictly below entry_min.
      2. No synthetic structural TP1 fabrication allowed.
      3. Total deterministic ordering: price_high DESC, created_at ASC, price_low DESC, zone_fp ASC.
      4. TP2 must be strictly beyond TP1 (candidate.price_high < tp1).
      5. planned_rr_tp1 >= min_rr_tp1 (unrounded comparison, equality is valid).
    """
    planned_risk = stop_final - entry_min
    if planned_risk <= Decimal("0"):
        return None, None, None, None, None, None, False, "Invalid risk distance (stop <= entry)."

    if policy.min_rr_tp1 is None:
        return None, None, None, None, None, None, False, "Incomplete SHORT risk policy (min_rr_tp1 is None)."

    # Gather candidate zones strictly from PIT StructureResults
    candidates = _collect_pit_zones(structure_15m, structure_4h, authoritative_t)

    # Filter to valid supports below entry_min
    short_candidates = [
        z for z in candidates
        if z.zone_type == "SUPPORT" and z.price_high < entry_min
    ]

    # Total deterministic sorting: price_high DESC, created_at ASC, price_low DESC, zone_fp ASC
    short_candidates.sort(key=lambda z: (
        -z.price_high,
        canonical_utc_timestamp(z.created_at),
        -z.price_low,
        compute_zone_fingerprint(z),
    ))

    if not short_candidates:
        return None, None, None, None, None, None, False, "Missing confirmed structural support target below entry zone."

    tp1_zone = short_candidates[0]
    tp1 = tp1_zone.price_high
    tp1_zone_fp = compute_zone_fingerprint(tp1_zone)

    planned_rr_tp1 = (entry_min - tp1) / planned_risk

    # TP2 Resolution: must be strictly beyond TP1
    tp2: Optional[Decimal] = None
    tp2_zone_fp: Optional[str] = None

    for candidate in short_candidates[1:]:
        cand_price = candidate.price_high
        if cand_price < tp1:
            tp2 = cand_price
            tp2_zone_fp = compute_zone_fingerprint(candidate)
            break

    # Optional synthetic TP2 fallback if configured and strictly beyond TP1
    if tp2 is None and policy.tp2_atr_multiplier is not None:
        syn_tp2 = entry_mid - (policy.tp2_atr_multiplier * atr14)
        if syn_tp2 < tp1:
            tp2 = syn_tp2
            tp2_zone_fp = None

    planned_rr_tp2: Optional[Decimal] = None
    if tp2 is not None:
        planned_rr_tp2 = (entry_min - tp2) / planned_risk

    # Minimum RR Gate (unrounded raw comparison)
    if planned_rr_tp1 < policy.min_rr_tp1:
        return (
            tp1,
            tp2,
            planned_rr_tp1,
            planned_rr_tp2,
            tp1_zone_fp,
            tp2_zone_fp,
            False,
            f"Nearest confirmed support at {tp1} yields RR {planned_rr_tp1} below minimum required threshold {policy.min_rr_tp1}.",
        )

    return tp1, tp2, planned_rr_tp1, planned_rr_tp2, tp1_zone_fp, tp2_zone_fp, True, None
