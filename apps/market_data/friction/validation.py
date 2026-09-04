"""Canonical evidence completeness validator for XAUUSD empirical friction models.

Adheres strictly to Pre-Phase-8 Calibration Hardening Governance (Directive 5):
- Centralizes one authoritative validator for both model activation and readiness evaluation.
- Validates all 8 non-negotiable criteria:
    1. Exact target scope (venue, symbol, account_tier, legal_entity_code).
    2. Legal entity provenance snapshot, SHA-256, and qualified source type.
    3. Contract specification snapshot, geometry parameters, SHA-256, and qualified source type.
    4. Commission fee schedule snapshot, native fee, SHA-256, and qualified source type.
    5. Financing swap snapshot, swap points, rollover schedule, SHA-256, and qualified source type.
    6. Spread dataset & summary sufficiency (N >= 1000, days >= 5, session counts, p75 > 0, p95 >= p75).
    7. Slippage telemetry dataset & summary sufficiency (N >= 30, non-negative p75, p95 >= p75, model match).
    8. Cryptographic semantic fingerprint recomputation and integrity.
- Rejects unverified user sources (USER_PROVIDED_UNVERIFIED never satisfies hard readiness).
- Authorizes FrictionActivationStatus.ACTIVE ONLY when all gates pass.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
from typing import Any, Dict, List, Optional

from apps.market_data.models import (
    FrictionBindingRole,
    FrictionComponentType,
    FrictionDistributionSummary,
    FrictionEvidenceDataset,
    FrictionModelVersion,
    FrictionSourceSnapshot,
    FrictionSourceType,
    QUALIFIED_FRICTION_SOURCE_TYPES,
)
from apps.market_data.friction.fingerprint import compute_empirical_friction_fingerprint


@dataclass
class FrictionValidationResult:
    is_valid: bool
    status: str
    reasons: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


def validate_friction_model_for_activation(
    model_version: FrictionModelVersion,
    target_venue: str = "EXNESS",
    target_symbol: str = "XAUUSD",
    target_account_tier: str = "STANDARD",
    target_legal_entity_code: Optional[str] = None,
    slippage_cost_policy_version: str = "ADVERSE_ONLY_P75_P95_V1",
) -> FrictionValidationResult:
    """Canonical validator for empirical friction models.
    
    Returns FrictionValidationResult(is_valid, status, reasons, details).
    """
    reasons: List[str] = []
    details: Dict[str, Any] = {
        "model_version_id": model_version.model_version_id,
        "venue": model_version.venue,
        "symbol": model_version.symbol,
        "account_tier": model_version.account_tier,
        "legal_entity_code": model_version.legal_entity_code,
    }

    # 1. Scope Validation (Directive 2 & 5)
    if not target_legal_entity_code:
        return FrictionValidationResult(
            is_valid=False,
            status="LEGAL_ENTITY_EVIDENCE_MISSING",
            reasons=["Target execution legal entity code is not configured or established."],
            details=details,
        )

    if (
        model_version.venue != target_venue.upper()
        or model_version.symbol != target_symbol.upper()
        or model_version.account_tier != target_account_tier.upper()
        or model_version.legal_entity_code != target_legal_entity_code.upper()
    ):
        return FrictionValidationResult(
            is_valid=False,
            status="EMPIRICAL_FRICTION_INVALID",
            reasons=[
                f"Model scope '{model_version.venue}:{model_version.symbol}:{model_version.account_tier}:{model_version.legal_entity_code}' "
                f"does not match target '{target_venue.upper()}:{target_symbol.upper()}:{target_account_tier.upper()}:{target_legal_entity_code.upper()}'."
            ],
            details=details,
        )

    # 2. Legal Entity Provenance (Directives 7, 8, 10)
    legal_snap = model_version.legal_entity_source_snapshot
    if not legal_snap or not model_version.legal_entity_code:
        return FrictionValidationResult(
            is_valid=False,
            status="LEGAL_ENTITY_EVIDENCE_MISSING",
            reasons=["Legal entity provenance snapshot or legal entity code is missing."],
            details=details,
        )
    if (
        legal_snap.venue != model_version.venue
        or legal_snap.symbol != model_version.symbol
        or legal_snap.account_tier != model_version.account_tier
    ):
        return FrictionValidationResult(
            is_valid=False,
            status="EMPIRICAL_FRICTION_INVALID",
            reasons=["Legal entity snapshot scope does not match model scope."],
            details=details,
        )
    if legal_snap.source_type not in QUALIFIED_FRICTION_SOURCE_TYPES:
        return FrictionValidationResult(
            is_valid=False,
            status="EMPIRICAL_FRICTION_INVALID",
            reasons=[f"Legal entity source provenance '{legal_snap.source_type}' is unverified; hard readiness requires qualified broker provenance."],
            details=details,
        )
    if (
        not legal_snap.raw_content
        or hashlib.sha256(legal_snap.raw_content).hexdigest() != legal_snap.raw_payload_bytes_sha256
    ):
        return FrictionValidationResult(
            is_valid=False,
            status="LEGAL_ENTITY_EVIDENCE_MISSING",
            reasons=["Legal entity payload content or SHA-256 verification failed."],
            details=details,
        )
    if not (model_version.legal_entity_name and model_version.regulator and model_version.license_number):
        return FrictionValidationResult(
            is_valid=False,
            status="LEGAL_ENTITY_EVIDENCE_MISSING",
            reasons=["Legal entity metadata fields (name, regulator, license) are incomplete."],
            details=details,
        )

    # 3. Contract Specification Provenance (Directives 4, 7, 9, 10)
    contract_snap = model_version.contract_spec_source_snapshot
    if not contract_snap:
        return FrictionValidationResult(
            is_valid=False,
            status="CONTRACT_SPEC_EVIDENCE_MISSING",
            reasons=["Contract specification source snapshot is missing."],
            details=details,
        )
    if (
        contract_snap.venue != model_version.venue
        or contract_snap.symbol != model_version.symbol
        or contract_snap.account_tier != model_version.account_tier
    ):
        return FrictionValidationResult(
            is_valid=False,
            status="EMPIRICAL_FRICTION_INVALID",
            reasons=["Contract specification snapshot scope does not match model scope."],
            details=details,
        )
    if contract_snap.source_type not in QUALIFIED_FRICTION_SOURCE_TYPES:
        return FrictionValidationResult(
            is_valid=False,
            status="EMPIRICAL_FRICTION_INVALID",
            reasons=[f"Contract specification source provenance '{contract_snap.source_type}' is unverified; hard readiness requires qualified broker provenance."],
            details=details,
        )
    if (
        not contract_snap.raw_content
        or hashlib.sha256(contract_snap.raw_content).hexdigest() != contract_snap.raw_payload_bytes_sha256
    ):
        return FrictionValidationResult(
            is_valid=False,
            status="CONTRACT_SPEC_EVIDENCE_MISSING",
            reasons=["Contract specification payload content or SHA-256 verification failed."],
            details=details,
        )
    geom_fields = (
        model_version.digits,
        model_version.point_size,
        model_version.trade_tick_size,
        model_version.trade_tick_value,
        model_version.contract_size,
        model_version.volume_min,
        model_version.volume_max,
        model_version.volume_step,
    )
    if any(x is None for x in geom_fields):
        return FrictionValidationResult(
            is_valid=False,
            status="CONTRACT_SPEC_EVIDENCE_MISSING",
            reasons=["Contract geometry parameters are incomplete (contains null values without evidence)."],
            details=details,
        )

    # 4. Commission Provenance (Directives 5, 7, 10)
    fee_snap = model_version.fee_schedule_source_snapshot
    if not fee_snap:
        return FrictionValidationResult(
            is_valid=False,
            status="COMMISSION_EVIDENCE_MISSING",
            reasons=["Fee schedule source snapshot is missing."],
            details=details,
        )
    if (
        fee_snap.venue != model_version.venue
        or fee_snap.symbol != model_version.symbol
        or fee_snap.account_tier != model_version.account_tier
    ):
        return FrictionValidationResult(
            is_valid=False,
            status="EMPIRICAL_FRICTION_INVALID",
            reasons=["Fee schedule snapshot scope does not match model scope."],
            details=details,
        )
    if fee_snap.source_type not in QUALIFIED_FRICTION_SOURCE_TYPES:
        return FrictionValidationResult(
            is_valid=False,
            status="EMPIRICAL_FRICTION_INVALID",
            reasons=[f"Fee schedule source provenance '{fee_snap.source_type}' is unverified; hard readiness requires qualified broker provenance."],
            details=details,
        )
    if (
        not fee_snap.raw_content
        or hashlib.sha256(fee_snap.raw_content).hexdigest() != fee_snap.raw_payload_bytes_sha256
    ):
        return FrictionValidationResult(
            is_valid=False,
            status="COMMISSION_EVIDENCE_MISSING",
            reasons=["Fee schedule payload content or SHA-256 verification failed."],
            details=details,
        )
    if model_version.native_commission_usd_per_lot_per_side is None or not model_version.commission_formula:
        return FrictionValidationResult(
            is_valid=False,
            status="COMMISSION_EVIDENCE_MISSING",
            reasons=["Commission parameters are missing (native fee is null)."],
            details=details,
        )

    # 5. Financing / Swap Provenance (Directives 3, 7, 10)
    swap_snap = model_version.swap_spec_source_snapshot
    if not swap_snap:
        return FrictionValidationResult(
            is_valid=False,
            status="FINANCING_EVIDENCE_MISSING",
            reasons=["Financing/swap specification source snapshot is missing."],
            details=details,
        )
    if (
        swap_snap.venue != model_version.venue
        or swap_snap.symbol != model_version.symbol
        or swap_snap.account_tier != model_version.account_tier
    ):
        return FrictionValidationResult(
            is_valid=False,
            status="EMPIRICAL_FRICTION_INVALID",
            reasons=["Swap specification snapshot scope does not match model scope."],
            details=details,
        )
    if swap_snap.source_type not in QUALIFIED_FRICTION_SOURCE_TYPES:
        return FrictionValidationResult(
            is_valid=False,
            status="EMPIRICAL_FRICTION_INVALID",
            reasons=[f"Swap specification source provenance '{swap_snap.source_type}' is unverified; hard readiness requires qualified broker provenance."],
            details=details,
        )
    if (
        not swap_snap.raw_content
        or hashlib.sha256(swap_snap.raw_content).hexdigest() != swap_snap.raw_payload_bytes_sha256
    ):
        return FrictionValidationResult(
            is_valid=False,
            status="FINANCING_EVIDENCE_MISSING",
            reasons=["Swap specification payload content or SHA-256 verification failed."],
            details=details,
        )
    swap_fields = (
        model_version.swap_long_points,
        model_version.swap_short_points,
        model_version.rollover_summer_utc_hour,
        model_version.rollover_winter_utc_hour,
        model_version.triple_swap_weekday,
    )
    if any(x is None or str(x).strip() == "" for x in swap_fields):
        return FrictionValidationResult(
            is_valid=False,
            status="FINANCING_EVIDENCE_MISSING",
            reasons=["Financing parameters are incomplete (swap rates or rollover hours null)."],
            details=details,
        )

    # Bindings inspection
    dataset_bindings = list(model_version.dataset_bindings.select_related("evidence_dataset__source_snapshot").all())
    summary_bindings = list(model_version.summary_bindings.select_related("distribution_summary").all())

    if not dataset_bindings or not summary_bindings:
        return FrictionValidationResult(
            is_valid=False,
            status="EMPIRICAL_FRICTION_INCOMPLETE",
            reasons=["Friction model has no bound datasets or distribution summaries."],
            details=details,
        )

    # Check dataset scope
    for db in dataset_bindings:
        if (
            db.evidence_dataset.venue != model_version.venue
            or db.evidence_dataset.symbol != model_version.symbol
            or db.evidence_dataset.account_tier != model_version.account_tier
        ):
            return FrictionValidationResult(
                is_valid=False,
                status="EMPIRICAL_FRICTION_INVALID",
                reasons=["Bound dataset venue/symbol/account_tier does not match model."],
                details=details,
            )

    # 6. Spread Dataset & Summary Sufficiency (Directive 6 & 10)
    primary_ds_binding = next(
        (b for b in dataset_bindings if b.binding_role == FrictionBindingRole.PRIMARY_SPREAD_SAMPLE),
        None,
    )
    if not primary_ds_binding:
        return FrictionValidationResult(
            is_valid=False,
            status="SPREAD_EMPIRICAL_EVIDENCE_MISSING",
            reasons=["No PRIMARY_SPREAD_SAMPLE dataset bound to model."],
            details=details,
        )

    spread_ds = primary_ds_binding.evidence_dataset
    if spread_ds.sample_count < 1000:
        return FrictionValidationResult(
            is_valid=False,
            status="SPREAD_EMPIRICAL_EVIDENCE_MISSING",
            reasons=[f"Spread sample count insufficient: {spread_ds.sample_count} < 1000 required."],
            details=details,
        )
    if spread_ds.distinct_trading_days < 5:
        return FrictionValidationResult(
            is_valid=False,
            status="SPREAD_EMPIRICAL_EVIDENCE_MISSING",
            reasons=[f"Spread distinct trading days insufficient: {spread_ds.distinct_trading_days} < 5 required."],
            details=details,
        )

    sess = spread_ds.session_counts or {}
    for req_sess, min_cnt in [("ASIAN", 100), ("LONDON", 100), ("NEW_YORK", 100), ("ROLLOVER", 30)]:
        if sess.get(req_sess, 0) < min_cnt:
            return FrictionValidationResult(
                is_valid=False,
                status="SPREAD_EMPIRICAL_EVIDENCE_MISSING",
                reasons=[f"Spread session count insufficient for {req_sess}: {sess.get(req_sess, 0)} < {min_cnt} required."],
                details=details,
            )

    spread_sum_binding = next(
        (b for b in summary_bindings if b.binding_role == FrictionBindingRole.NORMAL_SPREAD_DISTRIBUTION),
        None,
    )
    if not spread_sum_binding:
        return FrictionValidationResult(
            is_valid=False,
            status="SPREAD_EMPIRICAL_EVIDENCE_MISSING",
            reasons=["No NORMAL_SPREAD_DISTRIBUTION summary bound to model."],
            details=details,
        )

    spread_sum = spread_sum_binding.distribution_summary
    if spread_sum.unit != "BPS":
        return FrictionValidationResult(
            is_valid=False,
            status="SPREAD_EMPIRICAL_EVIDENCE_INVALID",
            reasons=[f"Spread distribution unit is '{spread_sum.unit}', expected 'BPS'."],
            details=details,
        )
    if spread_sum.stat_p75 <= Decimal("0"):
        return FrictionValidationResult(
            is_valid=False,
            status="SPREAD_EMPIRICAL_EVIDENCE_INVALID",
            reasons=[f"Spread p75 is non-positive: {spread_sum.stat_p75}."],
            details=details,
        )
    if spread_sum.stat_p95 < spread_sum.stat_p75:
        return FrictionValidationResult(
            is_valid=False,
            status="SPREAD_EMPIRICAL_EVIDENCE_INVALID",
            reasons=[f"Spread distribution inverted: p95 ({spread_sum.stat_p95}) < p75 ({spread_sum.stat_p75})."],
            details=details,
        )
    if (
        model_version.base_spread_bps != spread_sum.stat_p75
        or model_version.stress_spread_bps != spread_sum.stat_p95
    ):
        return FrictionValidationResult(
            is_valid=False,
            status="SPREAD_EMPIRICAL_EVIDENCE_INVALID",
            reasons=["Model spread bps does not match bound distribution p75/p95."],
            details=details,
        )

    # 7. Slippage Telemetry & Summary Sufficiency (Directives 7, 8, 10, 11)
    telem_ds_binding = next(
        (b for b in dataset_bindings if b.binding_role in (
            FrictionBindingRole.PRIMARY_TELEMETRY_SAMPLE,
            FrictionBindingRole.TELEMETRY_SAMPLE,
        )),
        None,
    )
    if not telem_ds_binding:
        return FrictionValidationResult(
            is_valid=False,
            status="SLIPPAGE_EMPIRICAL_EVIDENCE_MISSING",
            reasons=["Execution slippage telemetry dataset binding is missing (MANDATORY)."],
            details=details,
        )

    telem_ds = telem_ds_binding.evidence_dataset
    if telem_ds.sample_count < 30:
        return FrictionValidationResult(
            is_valid=False,
            status="SLIPPAGE_EMPIRICAL_EVIDENCE_MISSING",
            reasons=[f"Execution slippage telemetry sample count insufficient: {telem_ds.sample_count} < 30 required."],
            details=details,
        )

    slip_sum_binding = next(
        (b for b in summary_bindings if b.binding_role in (
            FrictionBindingRole.NORMAL_SLIPPAGE_DISTRIBUTION,
            FrictionBindingRole.SLIPPAGE_DISTRIBUTION,
        )),
        None,
    )
    if not slip_sum_binding:
        return FrictionValidationResult(
            is_valid=False,
            status="SLIPPAGE_EMPIRICAL_EVIDENCE_MISSING",
            reasons=["Execution slippage distribution summary binding is missing (MANDATORY)."],
            details=details,
        )

    slip_sum = slip_sum_binding.distribution_summary
    if slip_sum.unit != "BPS":
        return FrictionValidationResult(
            is_valid=False,
            status="SLIPPAGE_EMPIRICAL_EVIDENCE_INVALID",
            reasons=[f"Slippage distribution unit is '{slip_sum.unit}', expected 'BPS'."],
            details=details,
        )
    if slip_sum.stat_p75 < Decimal("0"):
        return FrictionValidationResult(
            is_valid=False,
            status="SLIPPAGE_EMPIRICAL_EVIDENCE_INVALID",
            reasons=[f"Conservative slippage cost model cannot be negative: p75={slip_sum.stat_p75}."],
            details=details,
        )
    if slip_sum.stat_p95 < slip_sum.stat_p75:
        return FrictionValidationResult(
            is_valid=False,
            status="SLIPPAGE_EMPIRICAL_EVIDENCE_INVALID",
            reasons=[f"Slippage distribution inverted: p95 ({slip_sum.stat_p95}) < p75 ({slip_sum.stat_p75})."],
            details=details,
        )
    if (
        model_version.base_slippage_bps != slip_sum.stat_p75
        or model_version.stress_slippage_bps != slip_sum.stat_p95
    ):
        return FrictionValidationResult(
            is_valid=False,
            status="SLIPPAGE_EMPIRICAL_EVIDENCE_INVALID",
            reasons=["Model slippage bps does not match bound distribution p75/p95."],
            details=details,
        )

    # 8. Cryptographic Semantic Fingerprint Integrity (Directive 11)
    source_snapshots = [
        model_version.legal_entity_source_snapshot,
        model_version.contract_spec_source_snapshot,
        model_version.fee_schedule_source_snapshot,
        model_version.swap_spec_source_snapshot,
    ]
    source_hashes = [s.raw_payload_bytes_sha256 for s in source_snapshots if s]
    for db in dataset_bindings:
        if db.evidence_dataset.source_snapshot and db.evidence_dataset.source_snapshot.raw_payload_bytes_sha256 not in source_hashes:
            source_hashes.append(db.evidence_dataset.source_snapshot.raw_payload_bytes_sha256)
    source_types = [s.source_type for s in source_snapshots if s]

    dataset_hashes = [db.evidence_dataset.raw_dataset_sha256 for db in dataset_bindings]
    bound_roles = [db.binding_role for db in dataset_bindings] + [sb.binding_role for sb in summary_bindings]

    summaries_dict = [
        {
            "component_type": sb.distribution_summary.component_type,
            "condition": sb.distribution_summary.condition,
            "session": sb.distribution_summary.session,
            "unit": sb.distribution_summary.unit,
            "sample_count": sb.distribution_summary.sample_count,
            "stat_min": sb.distribution_summary.stat_min,
            "stat_p50": sb.distribution_summary.stat_p50,
            "stat_p75": sb.distribution_summary.stat_p75,
            "stat_p90": sb.distribution_summary.stat_p90,
            "stat_p95": sb.distribution_summary.stat_p95,
            "stat_p99": sb.distribution_summary.stat_p99,
            "stat_max": sb.distribution_summary.stat_max,
            "stat_mean": sb.distribution_summary.stat_mean,
            "stat_std": sb.distribution_summary.stat_std,
        }
        for sb in summary_bindings
    ]

    recomputed_fp = compute_empirical_friction_fingerprint(
        semantic_versions={
            "friction_policy_schema_version": model_version.friction_policy_schema_version,
            "distribution_algorithm_version": model_version.distribution_algorithm_version,
            "normalization_version": model_version.normalization_version,
            "commission_formula_version": model_version.commission_formula_version,
            "financing_rule_version": model_version.financing_rule_version,
            "sample_sufficiency_policy_version": "1.0.0",
            "slippage_mandatory_policy_version": "GOVERNED_MANDATORY_V1",
            "selection_policy_version": "BASE_P75_STRESS_P95_V1",
            "slippage_cost_policy_version": getattr(model_version, "slippage_cost_policy_version", slippage_cost_policy_version),
        },
        venue=model_version.venue,
        legal_entity_code=model_version.legal_entity_code,
        account_tier=model_version.account_tier,
        symbol=model_version.symbol,
        contract_geometry={
            "digits": model_version.digits,
            "point_size": model_version.point_size,
            "trade_tick_size": model_version.trade_tick_size,
            "trade_tick_value": model_version.trade_tick_value,
            "contract_size": model_version.contract_size,
            "volume_min": model_version.volume_min,
            "volume_max": model_version.volume_max,
            "volume_step": model_version.volume_step,
        },
        source_snapshot_hashes=source_hashes,
        source_types=source_types,
        dataset_hashes=dataset_hashes,
        distribution_summaries=summaries_dict,
        calibrated_parameters={
            "base_spread_bps": model_version.base_spread_bps,
            "stress_spread_bps": model_version.stress_spread_bps,
            "base_slippage_bps": model_version.base_slippage_bps,
            "stress_slippage_bps": model_version.stress_slippage_bps,
        },
        commission_policy={
            "native_commission_usd_per_lot_per_side": model_version.native_commission_usd_per_lot_per_side,
            "commission_formula": model_version.commission_formula,
        },
        financing_policy={
            "swap_long_points": model_version.swap_long_points,
            "swap_short_points": model_version.swap_short_points,
            "rollover_summer_utc_hour": model_version.rollover_summer_utc_hour,
            "rollover_winter_utc_hour": model_version.rollover_winter_utc_hour,
            "triple_swap_weekday": model_version.triple_swap_weekday,
            "swap_free_available_for_account_type": model_version.swap_free_available_for_account_type,
            "actual_account_swap_free_status": model_version.actual_account_swap_free_status,
        },
        bound_binding_roles=bound_roles,
    )

    if model_version.empirical_friction_evidence_fingerprint != recomputed_fp:
        return FrictionValidationResult(
            is_valid=False,
            status="EMPIRICAL_FRICTION_INVALID",
            reasons=[
                f"Friction model fingerprint mismatch: stored '{model_version.empirical_friction_evidence_fingerprint}' "
                f"!= recomputed '{recomputed_fp}'."
            ],
            details=details,
        )

    # All 8 gates passed!
    details["fingerprint"] = recomputed_fp
    return FrictionValidationResult(
        is_valid=True,
        status="EMPIRICAL_FRICTION_CONFIGURED",
        reasons=["All 8 empirical friction evidence gates passed and cryptographic fingerprint verified."],
        details=details,
    )
