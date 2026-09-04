"""Deterministic semantic fingerprinting for XAUUSD empirical friction models.

Adheres strictly to Pre-Phase-8 Calibration Hardening Governance (Directive 11):
- Excludes all instance identifiers: DB IDs, row IDs, auto-increments, timestamps, version IDs.
- Binds all material semantics:
  * legal entity source SHA
  * contract source SHA
  * commission source SHA
  * financing source SHA
  * spread raw dataset SHA
  * slippage raw dataset SHA
  * spread distribution
  * slippage distribution
  * contract geometry
  * commission policy
  * financing policy
  * sample sufficiency policy version
  * slippage-mandatory policy version
  * normal/stress selection policy
  * binding roles
  * venue, symbol, account_tier, legal_entity_code
- Identical semantic evidence and policy produces the EXACT SAME fingerprint.
- Any mutation of evidence, distributions, or policy produces a completely DIFFERENT fingerprint.
"""
import hashlib
import json
from decimal import Decimal
from typing import Any, Dict, List, Optional


def _serialize_decimal(val: Any) -> Optional[str]:
    """Format Decimal values consistently for canonical hashing across differing DB column precisions."""
    if val is None:
        return None
    if isinstance(val, (int, float, Decimal)):
        d = Decimal(str(val))
        s = f"{d:f}"
        if "." in s:
            s = s.rstrip("0").rstrip(".")
            if s == "" or s == "-0":
                s = "0"
        return s
    return str(val)


def compute_empirical_friction_fingerprint(
    semantic_versions: Dict[str, str],
    venue: str,
    legal_entity_code: str,
    account_tier: str,
    symbol: str,
    contract_geometry: Dict[str, Any],
    source_snapshot_hashes: List[str],
    dataset_hashes: List[str],
    distribution_summaries: List[Dict[str, Any]],
    calibrated_parameters: Dict[str, Any],
    commission_policy: Dict[str, Any],
    financing_policy: Dict[str, Any],
    bound_binding_roles: Optional[List[str]] = None,
) -> str:
    """Compute deterministic 64-character SHA-256 fingerprint for a friction model."""
    sorted_source_hashes = sorted(set(source_snapshot_hashes))
    sorted_dataset_hashes = sorted(set(dataset_hashes))
    sorted_binding_roles = sorted(set(bound_binding_roles or []))

    sorted_summaries = sorted(
        distribution_summaries,
        key=lambda s: (
            str(s.get("component_type")),
            str(s.get("condition")),
            str(s.get("session")),
            str(s.get("unit")),
        ),
    )

    canonical_payload: Dict[str, Any] = {
        "semantic_versions": {
            "friction_policy_schema_version": str(semantic_versions.get("friction_policy_schema_version", "1.0.0")),
            "distribution_algorithm_version": str(semantic_versions.get("distribution_algorithm_version", "1.0.0")),
            "normalization_version": str(semantic_versions.get("normalization_version", "1.0.0")),
            "commission_formula_version": str(semantic_versions.get("commission_formula_version", "1.0.0")),
            "financing_rule_version": str(semantic_versions.get("financing_rule_version", "1.0.0")),
            "sample_sufficiency_policy_version": str(semantic_versions.get("sample_sufficiency_policy_version", "1.0.0")),
            "slippage_mandatory_policy_version": str(semantic_versions.get("slippage_mandatory_policy_version", "GOVERNED_MANDATORY_V1")),
            "selection_policy_version": str(semantic_versions.get("selection_policy_version", "BASE_P75_STRESS_P95_V1")),
        },
        "venue_and_identity": {
            "venue": str(venue).upper(),
            "legal_entity_code": str(legal_entity_code).upper(),
            "account_tier": str(account_tier).upper(),
            "symbol": str(symbol).upper(),
        },
        "contract_geometry": {
            "digits": None if contract_geometry.get("digits") is None else int(contract_geometry.get("digits")),
            "point_size": _serialize_decimal(contract_geometry.get("point_size")),
            "trade_tick_size": _serialize_decimal(contract_geometry.get("trade_tick_size")),
            "trade_tick_value": _serialize_decimal(contract_geometry.get("trade_tick_value")),
            "contract_size": _serialize_decimal(contract_geometry.get("contract_size")),
            "volume_min": _serialize_decimal(contract_geometry.get("volume_min")),
            "volume_max": _serialize_decimal(contract_geometry.get("volume_max")),
            "volume_step": _serialize_decimal(contract_geometry.get("volume_step")),
        },
        "source_snapshot_hashes": sorted_source_hashes,
        "dataset_hashes": sorted_dataset_hashes,
        "distribution_summaries": [
            {
                "component_type": str(s.get("component_type")),
                "condition": str(s.get("condition")),
                "session": str(s.get("session")),
                "unit": str(s.get("unit")),
                "sample_count": int(s.get("sample_count", 0)),
                "stat_min": _serialize_decimal(s.get("stat_min")),
                "stat_p50": _serialize_decimal(s.get("stat_p50")),
                "stat_p75": _serialize_decimal(s.get("stat_p75")),
                "stat_p90": _serialize_decimal(s.get("stat_p90")),
                "stat_p95": _serialize_decimal(s.get("stat_p95")),
                "stat_p99": _serialize_decimal(s.get("stat_p99")),
                "stat_max": _serialize_decimal(s.get("stat_max")),
                "stat_mean": _serialize_decimal(s.get("stat_mean")),
                "stat_std": _serialize_decimal(s.get("stat_std")),
            }
            for s in sorted_summaries
        ],
        "calibrated_parameters": {
            "base_spread_bps": _serialize_decimal(calibrated_parameters.get("base_spread_bps")),
            "stress_spread_bps": _serialize_decimal(calibrated_parameters.get("stress_spread_bps")),
            "base_slippage_bps": _serialize_decimal(calibrated_parameters.get("base_slippage_bps")),
            "stress_slippage_bps": _serialize_decimal(calibrated_parameters.get("stress_slippage_bps")),
        },
        "commission_policy": {
            "native_commission_usd_per_lot_per_side": _serialize_decimal(
                commission_policy.get("native_commission_usd_per_lot_per_side")
            ),
            "commission_formula": None if commission_policy.get("commission_formula") is None else str(commission_policy.get("commission_formula")),
        },
        "financing_policy": {
            "swap_long_points": _serialize_decimal(financing_policy.get("swap_long_points")),
            "swap_short_points": _serialize_decimal(financing_policy.get("swap_short_points")),
            "rollover_summer_utc_hour": None if financing_policy.get("rollover_summer_utc_hour") is None else int(financing_policy.get("rollover_summer_utc_hour")),
            "rollover_winter_utc_hour": None if financing_policy.get("rollover_winter_utc_hour") is None else int(financing_policy.get("rollover_winter_utc_hour")),
            "triple_swap_weekday": None if financing_policy.get("triple_swap_weekday") is None else str(financing_policy.get("triple_swap_weekday")),
            "actual_account_swap_free_status": None if financing_policy.get("actual_account_swap_free_status") is None else bool(financing_policy.get("actual_account_swap_free_status")),
        },
        "bound_binding_roles": sorted_binding_roles,
    }

    serialized_bytes = json.dumps(canonical_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(serialized_bytes).hexdigest()
