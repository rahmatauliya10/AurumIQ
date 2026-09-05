"""Idempotent, append-only ingestion engine for XAUUSD empirical friction evidence.

Adheres strictly to Pre-Phase-8 Calibration Hardening Governance:
- Ingests immutable FrictionSourceSnapshot with hardened scope-bound identity (Directive 12).
- Validates temporal sample sufficiency on FrictionEvidenceDataset (Directive 6).
- Validates execution telemetry sample sufficiency on FrictionEvidenceDataset (Directive 7).
- Eliminates all silent evidence defaults (Directive 2).
- Requires genuine source snapshots for legal entity, contract spec, fees, and swaps (Directives 3, 4, 5).
- Enforces mandatory slippage telemetry (Directive 8).
- Prohibits incomplete models from activating as ACTIVE; activates as DRAFT instead (Directive 9).
- Generates immutable dataset and summary bindings with deterministic semantic fingerprint (Directive 11).
"""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from apps.market_data.models import (
    FrictionActivationStatus,
    FrictionBindingRole,
    FrictionComponentType,
    FrictionConditionType,
    FrictionComponentType,
    FrictionConditionType,
    FrictionDistributionSummary,
    FrictionEvidenceDataset,
    FrictionModelActivation,
    FrictionModelDatasetBinding,
    FrictionModelSummaryBinding,
    FrictionModelVersion,
    FrictionPopulationSemantics,
    FrictionSessionType,
    FrictionSourceSnapshot,
    FrictionSourceType,
    QUALIFIED_COMMISSION_SOURCE_TYPES,
    QUALIFIED_CONTRACT_SOURCE_TYPES,
    QUALIFIED_FINANCING_SOURCE_TYPES,
    QUALIFIED_FRICTION_SOURCE_TYPES,
    QUALIFIED_LEGAL_ENTITY_SOURCE_TYPES,
    QUALIFIED_SLIPPAGE_SOURCE_TYPES,
    QUALIFIED_SPREAD_SOURCE_TYPES,
)
from apps.market_data.friction.distribution import (
    compute_distribution_statistics,
    validate_slippage_telemetry_sufficiency,
    validate_spread_dataset_sufficiency,
)
from apps.market_data.friction.fingerprint import compute_empirical_friction_fingerprint
from apps.market_data.friction.validation import validate_friction_model_for_activation

logger = logging.getLogger(__name__)


def resolve_slippage_cost_samples(
    telemetry_records: List[Dict[str, Any]],
    slippage_cost_policy_version: str,
) -> Tuple[List[Decimal], str]:
    """Canonical slippage policy sample resolver and population semantics dispatch.
    
    Directives 2 & 9:
    - ADVERSE_ONLY_P75_P95_V1: extracts 'adverse_only_bps', assigns SLIPPAGE_ADVERSE_ONLY.
    - RAW_SIGNED_DISTRIBUTION_V1: extracts 'signed_slippage_bps', assigns SLIPPAGE_SIGNED.
    - Unknown/unsupported policy: fails closed (ValueError).
    """
    if slippage_cost_policy_version == "ADVERSE_ONLY_P75_P95_V1":
        samples = [Decimal(str(r["adverse_only_bps"])) for r in telemetry_records]
        return samples, FrictionPopulationSemantics.SLIPPAGE_ADVERSE_ONLY.value
    elif slippage_cost_policy_version == "RAW_SIGNED_DISTRIBUTION_V1":
        samples = [Decimal(str(r["signed_slippage_bps"])) for r in telemetry_records]
        return samples, FrictionPopulationSemantics.SLIPPAGE_SIGNED.value
    else:
        raise ValueError(
            f"SLIPPAGE_POLICY_INVALID: Unknown or unsupported slippage cost policy version '{slippage_cost_policy_version}'. "
            f"Must be one of ['ADVERSE_ONLY_P75_P95_V1', 'RAW_SIGNED_DISTRIBUTION_V1']."
        )


def ingest_friction_source_snapshot(
    source_url: str,
    source_name: str,
    venue: str,
    symbol: str,
    account_tier: str,
    retrieved_at: datetime,
    known_at: datetime,
    raw_content: bytes,
    effective_from: Optional[datetime] = None,
    effective_to: Optional[datetime] = None,
    http_status: int = 200,
    metadata: Optional[Dict[str, Any]] = None,
    source_type: str = FrictionSourceType.USER_PROVIDED_UNVERIFIED,
    source_origin: str = "",
    collection_methodology: str = "",
    original_filename: str = "",
) -> Tuple[FrictionSourceSnapshot, bool]:
    """Ingest an immutable FrictionSourceSnapshot with hardened scope-bound snapshot_id (Directive 12)."""
    raw_sha = hashlib.sha256(raw_content).hexdigest()
    # Bind source_name, venue, symbol, account_tier, and raw SHA-256 to prevent cross-scope collisions
    snapshot_id = hashlib.sha256(
        f"{source_name.upper()}:{venue.upper()}:{symbol.upper()}:{account_tier.upper()}:{raw_sha}".encode()
    ).hexdigest()

    existing = FrictionSourceSnapshot.objects.filter(snapshot_id=snapshot_id).first()
    if existing:
        return existing, False

    snapshot = FrictionSourceSnapshot.objects.create(
        snapshot_id=snapshot_id,
        source_url=source_url,
        source_name=source_name,
        source_type=source_type,
        source_origin=source_origin,
        collection_methodology=collection_methodology,
        original_filename=original_filename,
        venue=venue.upper(),
        symbol=symbol.upper(),
        account_tier=account_tier.upper(),
        retrieved_at=retrieved_at,
        known_at=known_at,
        effective_from=effective_from,
        effective_to=effective_to,
        http_status=http_status,
        raw_payload_bytes_sha256=raw_sha,
        raw_content=raw_content,
        metadata=metadata or {},
    )
    return snapshot, True


def ingest_friction_evidence_dataset(
    source_snapshot: FrictionSourceSnapshot,
    venue: str,
    account_tier: str,
    symbol: str,
    sample_start: datetime,
    sample_end: datetime,
    ticks_data: List[Dict[str, Any]],
    source_units: str = "POINTS",
    collection_methodology: str = "MT5_TERMINAL_TICK_EXPORT",
) -> Tuple[FrictionEvidenceDataset, bool]:
    """Ingest spread FrictionEvidenceDataset after verifying temporal sample sufficiency (Directive 6)."""
    is_valid, errors = validate_spread_dataset_sufficiency(ticks_data)
    if not is_valid:
        raise ValueError(f"Spread dataset sufficiency validation failed: {'; '.join(errors)}")

    distinct_dates = len(set(t["timestamp"].astimezone(timezone.utc).date() for t in ticks_data))
    session_counts: Dict[str, int] = {"ASIAN": 0, "LONDON": 0, "NEW_YORK": 0, "ROLLOVER": 0}
    for t in ticks_data:
        hr = t["timestamp"].astimezone(timezone.utc).hour
        if 0 <= hr < 8:
            session_counts["ASIAN"] += 1
        elif 8 <= hr < 13:
            session_counts["LONDON"] += 1
        elif 13 <= hr < 21:
            session_counts["NEW_YORK"] += 1
        else:
            session_counts["ROLLOVER"] += 1

    # Serialize normalized ticks to get deterministic dataset sha256
    norm_rows = [
        f"{t['timestamp'].astimezone(timezone.utc).isoformat()}|{t['bid']}|{t['ask']}|{t.get('spread_bps', '')}"
        for t in ticks_data
    ]
    raw_ds_sha = hashlib.sha256("\n".join(norm_rows).encode("utf-8")).hexdigest()
    dataset_id = hashlib.sha256(f"{source_snapshot.snapshot_id}:{raw_ds_sha}".encode()).hexdigest()

    existing = FrictionEvidenceDataset.objects.filter(dataset_id=dataset_id).first()
    if existing:
        return existing, False

    dataset = FrictionEvidenceDataset.objects.create(
        dataset_id=dataset_id,
        source_snapshot=source_snapshot,
        venue=venue.upper(),
        account_tier=account_tier.upper(),
        symbol=symbol.upper(),
        sample_start=sample_start,
        sample_end=sample_end,
        sample_count=len(ticks_data),
        distinct_trading_days=distinct_dates,
        session_counts=session_counts,
        source_units=source_units,
        raw_dataset_sha256=raw_ds_sha,
        collection_methodology=collection_methodology,
    )
    return dataset, True


def ingest_friction_telemetry_dataset(
    source_snapshot: FrictionSourceSnapshot,
    venue: str,
    account_tier: str,
    symbol: str,
    sample_start: datetime,
    sample_end: datetime,
    telemetry_records: List[Dict[str, Any]],
    source_units: str = "BPS",
    collection_methodology: str = "MT5_EXECUTION_TELEMETRY_EXPORT",
) -> Tuple[FrictionEvidenceDataset, bool]:
    """Ingest slippage telemetry FrictionEvidenceDataset after verifying sample sufficiency (Directive 7)."""
    is_valid, errors = validate_slippage_telemetry_sufficiency(telemetry_records)
    if not is_valid:
        raise ValueError(f"Execution telemetry sufficiency validation failed: {'; '.join(errors)}")

    distinct_dates = len(set(r["fill_timestamp"].astimezone(timezone.utc).date() for r in telemetry_records))
    session_counts: Dict[str, int] = {"ASIAN": 0, "LONDON": 0, "NEW_YORK": 0, "ROLLOVER": 0}
    for r in telemetry_records:
        hr = r["fill_timestamp"].astimezone(timezone.utc).hour
        if 0 <= hr < 8:
            session_counts["ASIAN"] += 1
        elif 8 <= hr < 13:
            session_counts["LONDON"] += 1
        elif 13 <= hr < 21:
            session_counts["NEW_YORK"] += 1
        else:
            session_counts["ROLLOVER"] += 1

    norm_rows = [
        f"{r['side']}|{r['order_type']}|{r['reference_bid']}|{r['reference_ask']}|{r['executed_fill_price']}|{r['signed_slippage_bps']}|{r['volume_lots']}|{r['latency_ms']}"
        for r in telemetry_records
    ]
    raw_ds_sha = hashlib.sha256("\n".join(norm_rows).encode("utf-8")).hexdigest()
    dataset_id = hashlib.sha256(f"{source_snapshot.snapshot_id}:{raw_ds_sha}".encode()).hexdigest()

    existing = FrictionEvidenceDataset.objects.filter(dataset_id=dataset_id).first()
    if existing:
        return existing, False

    dataset = FrictionEvidenceDataset.objects.create(
        dataset_id=dataset_id,
        source_snapshot=source_snapshot,
        venue=venue.upper(),
        account_tier=account_tier.upper(),
        symbol=symbol.upper(),
        sample_start=sample_start,
        sample_end=sample_end,
        sample_count=len(telemetry_records),
        distinct_trading_days=distinct_dates,
        session_counts=session_counts,
        source_units=source_units,
        raw_dataset_sha256=raw_ds_sha,
        collection_methodology=collection_methodology,
    )
    return dataset, True


def build_and_bind_friction_model_version(
    legal_entity_snapshot: Optional[FrictionSourceSnapshot],
    contract_spec_snapshot: Optional[FrictionSourceSnapshot],
    fee_schedule_snapshot: Optional[FrictionSourceSnapshot],
    swap_spec_snapshot: Optional[FrictionSourceSnapshot],
    evidence_dataset: Optional[FrictionEvidenceDataset],
    spread_ticks_bps: Optional[List[Decimal]],
    legal_entity_info: Optional[Dict[str, str]] = None,
    contract_geometry: Optional[Dict[str, Any]] = None,
    commission_policy: Optional[Dict[str, Any]] = None,
    financing_policy: Optional[Dict[str, Any]] = None,
    telemetry_dataset: Optional[FrictionEvidenceDataset] = None,
    slippage_records_bps: Optional[List[Decimal]] = None,
    venue: str = "EXNESS",
    symbol: str = "XAUUSD",
    account_tier: str = "STANDARD",
    model_version_id: Optional[str] = None,
    activation_reason: str = "Pre-Phase-8 Empirical Friction Calibration Baseline",
    effective_from: Optional[datetime] = None,
    slippage_cost_policy_version: str = "ADVERSE_ONLY_P75_P95_V1",
    known_at: Optional[datetime] = None,
    telemetry_records: Optional[List[Dict[str, Any]]] = None,
    slippage_population_semantics: Optional[str] = None,
) -> Tuple[FrictionModelVersion, FrictionModelActivation]:
    """Calculate distributions, create bindings, and resolve activation.
    
    Directives 8 & 9 Enforcement:
    - If any required evidence (legal entity, contract spec, commission, financing,
      spread dataset, or slippage telemetry) is missing, model is incomplete.
    - An incomplete model CANNOT receive an ACTIVE activation. It receives DRAFT status.
    - Zero silent defaults are injected. Missing fields remain None.
    """
    legal_info = legal_entity_info or {}
    geom = contract_geometry or {}
    comm = commission_policy or {}
    fin = financing_policy or {}

    # Canonical slippage policy dispatch (Directives 2 & 9)
    pop_semantics: Optional[str] = None
    slip_samples: Optional[List[Decimal]] = None
    if telemetry_records:
        slip_samples, pop_semantics = resolve_slippage_cost_samples(telemetry_records, slippage_cost_policy_version)
    elif slippage_records_bps is not None:
        if slippage_cost_policy_version not in ("ADVERSE_ONLY_P75_P95_V1", "RAW_SIGNED_DISTRIBUTION_V1"):
            raise ValueError(
                f"SLIPPAGE_POLICY_INVALID: Unknown or unsupported slippage cost policy version '{slippage_cost_policy_version}'."
            )
        if slippage_cost_policy_version == "ADVERSE_ONLY_P75_P95_V1":
            if any(Decimal(str(x)) < Decimal("0") for x in slippage_records_bps):
                raise ValueError("ADVERSE_ONLY policy cannot consume signed sample list containing negative values")
            if slippage_population_semantics and slippage_population_semantics != FrictionPopulationSemantics.SLIPPAGE_ADVERSE_ONLY.value:
                raise ValueError(f"Population semantics mismatch: policy {slippage_cost_policy_version} cannot be {slippage_population_semantics}")
            pop_semantics = FrictionPopulationSemantics.SLIPPAGE_ADVERSE_ONLY.value
        else:
            if slippage_population_semantics and slippage_population_semantics != FrictionPopulationSemantics.SLIPPAGE_SIGNED.value:
                raise ValueError(f"Population semantics mismatch: policy {slippage_cost_policy_version} cannot be {slippage_population_semantics}")
            pop_semantics = FrictionPopulationSemantics.SLIPPAGE_SIGNED.value
        slip_samples = [Decimal(str(x)) for x in slippage_records_bps]

    # Check evidence completeness (Directive 8)
    has_legal = bool(legal_entity_snapshot and legal_info.get("legal_entity_code"))
    has_contract = bool(contract_spec_snapshot and geom.get("contract_size") is not None)
    has_comm = bool(fee_schedule_snapshot and comm.get("native_commission_usd_per_lot_per_side") is not None)
    has_swap = bool(swap_spec_snapshot and fin.get("swap_long_points") is not None)
    has_spread = bool(evidence_dataset and spread_ticks_bps and len(spread_ticks_bps) >= 1000)
    has_slippage = bool(telemetry_dataset and slip_samples and len(slip_samples) >= 30)

    is_complete = all([
        has_legal,
        has_contract,
        has_comm,
        has_swap,
        has_spread,
        has_slippage,
    ])

    # 1. Compute spread statistics if provided
    base_spread: Optional[Decimal] = None
    stress_spread: Optional[Decimal] = None
    spread_summary: Optional[FrictionDistributionSummary] = None

    if spread_ticks_bps and evidence_dataset:
        spread_stats = compute_distribution_statistics(spread_ticks_bps)
        base_spread = spread_stats["stat_p75"]
        stress_spread = spread_stats["stat_p95"]

        spread_summary_id = hashlib.sha256(
            f"{evidence_dataset.dataset_id}:SPREAD:NORMAL:ALL:{base_spread}:{FrictionPopulationSemantics.SPREAD_BPS.value}".encode()
        ).hexdigest()

        spread_summary = FrictionDistributionSummary.objects.filter(summary_id=spread_summary_id).first()
        if not spread_summary:
            spread_summary = FrictionDistributionSummary.objects.create(
                summary_id=spread_summary_id,
                evidence_dataset=evidence_dataset,
                component_type=FrictionComponentType.SPREAD,
                condition=FrictionConditionType.NORMAL,
                session=FrictionSessionType.ALL,
                unit="BPS",
                population_semantics=FrictionPopulationSemantics.SPREAD_BPS.value,
                sample_count=int(spread_stats["sample_count"]),
                stat_min=spread_stats["stat_min"],
                stat_p50=spread_stats["stat_p50"],
                stat_p75=spread_stats["stat_p75"],
                stat_p90=spread_stats["stat_p90"],
                stat_p95=spread_stats["stat_p95"],
                stat_p99=spread_stats["stat_p99"],
                stat_max=spread_stats["stat_max"],
                stat_mean=spread_stats["stat_mean"],
                stat_std=spread_stats["stat_std"],
            )

    # 2. Compute slippage statistics if provided
    base_slippage: Optional[Decimal] = None
    stress_slippage: Optional[Decimal] = None
    slippage_summary: Optional[FrictionDistributionSummary] = None

    if slip_samples and telemetry_dataset:
        slip_stats = compute_distribution_statistics(slip_samples)
        base_slippage = slip_stats["stat_p75"]
        stress_slippage = slip_stats["stat_p95"]

        slip_summary_id = hashlib.sha256(
            f"{telemetry_dataset.dataset_id}:SLIPPAGE:NORMAL:ALL:{base_slippage}:{pop_semantics}".encode()
        ).hexdigest()

        slippage_summary = FrictionDistributionSummary.objects.filter(summary_id=slip_summary_id).first()
        if not slippage_summary:
            slippage_summary = FrictionDistributionSummary.objects.create(
                summary_id=slip_summary_id,
                evidence_dataset=telemetry_dataset,
                component_type=FrictionComponentType.SLIPPAGE,
                condition=FrictionConditionType.NORMAL,
                session=FrictionSessionType.ALL,
                unit="BPS",
                population_semantics=pop_semantics or FrictionPopulationSemantics.UNKNOWN.value,
                sample_count=int(slip_stats["sample_count"]),
                stat_min=slip_stats["stat_min"],
                stat_p50=slip_stats["stat_p50"],
                stat_p75=slip_stats["stat_p75"],
                stat_p90=slip_stats["stat_p90"],
                stat_p95=slip_stats["stat_p95"],
                stat_p99=slip_stats["stat_p99"],
                stat_max=slip_stats["stat_max"],
                stat_mean=slip_stats["stat_mean"],
                stat_std=slip_stats["stat_std"],
            )

    semantic_versions = {
        "friction_policy_schema_version": "1.0.0",
        "distribution_algorithm_version": "1.0.0",
        "normalization_version": "1.0.0",
        "commission_formula_version": "1.0.0",
        "financing_rule_version": "1.0.0",
        "sample_sufficiency_policy_version": "1.0.0",
        "slippage_mandatory_policy_version": "GOVERNED_MANDATORY_V1",
        "selection_policy_version": "BASE_P75_STRESS_P95_V1",
        "slippage_cost_policy_version": slippage_cost_policy_version,
    }

    calibrated_params = {
        "base_spread_bps": base_spread,
        "stress_spread_bps": stress_spread,
        "base_slippage_bps": base_slippage,
        "stress_slippage_bps": stress_slippage,
    }

    source_hashes: List[str] = []
    if legal_entity_snapshot:
        source_hashes.append(legal_entity_snapshot.raw_payload_bytes_sha256)
    if contract_spec_snapshot:
        source_hashes.append(contract_spec_snapshot.raw_payload_bytes_sha256)
    if fee_schedule_snapshot:
        source_hashes.append(fee_schedule_snapshot.raw_payload_bytes_sha256)
    if swap_spec_snapshot:
        source_hashes.append(swap_spec_snapshot.raw_payload_bytes_sha256)
    if evidence_dataset and evidence_dataset.source_snapshot:
        source_hashes.append(evidence_dataset.source_snapshot.raw_payload_bytes_sha256)
    if telemetry_dataset and telemetry_dataset.source_snapshot:
        source_hashes.append(telemetry_dataset.source_snapshot.raw_payload_bytes_sha256)

    dataset_hashes: List[str] = []
    if evidence_dataset:
        dataset_hashes.append(evidence_dataset.raw_dataset_sha256)
    if telemetry_dataset:
        dataset_hashes.append(telemetry_dataset.raw_dataset_sha256)

    bound_roles: List[str] = []
    summaries_dict: List[Dict[str, Any]] = []

    if spread_summary:
        bound_roles.append(FrictionBindingRole.PRIMARY_SPREAD_SAMPLE)
        bound_roles.append(FrictionBindingRole.NORMAL_SPREAD_DISTRIBUTION)
        summaries_dict.append({
            "component_type": str(spread_summary.component_type),
            "condition": str(spread_summary.condition),
            "session": str(spread_summary.session),
            "unit": str(spread_summary.unit),
            "population_semantics": str(spread_summary.population_semantics),
            "sample_count": spread_summary.sample_count,
            "stat_min": spread_summary.stat_min,
            "stat_p50": spread_summary.stat_p50,
            "stat_p75": spread_summary.stat_p75,
            "stat_p90": spread_summary.stat_p90,
            "stat_p95": spread_summary.stat_p95,
            "stat_p99": spread_summary.stat_p99,
            "stat_max": spread_summary.stat_max,
            "stat_mean": spread_summary.stat_mean,
            "stat_std": spread_summary.stat_std,
        })

    if slippage_summary:
        bound_roles.append(FrictionBindingRole.PRIMARY_TELEMETRY_SAMPLE)
        bound_roles.append(FrictionBindingRole.NORMAL_SLIPPAGE_DISTRIBUTION)
        summaries_dict.append({
            "component_type": str(slippage_summary.component_type),
            "condition": str(slippage_summary.condition),
            "session": str(slippage_summary.session),
            "unit": str(slippage_summary.unit),
            "population_semantics": str(slippage_summary.population_semantics),
            "sample_count": slippage_summary.sample_count,
            "stat_min": slippage_summary.stat_min,
            "stat_p50": slippage_summary.stat_p50,
            "stat_p75": slippage_summary.stat_p75,
            "stat_p90": slippage_summary.stat_p90,
            "stat_p95": slippage_summary.stat_p95,
            "stat_p99": slippage_summary.stat_p99,
            "stat_max": slippage_summary.stat_max,
            "stat_mean": slippage_summary.stat_mean,
            "stat_std": slippage_summary.stat_std,
        })

    source_types: List[str] = []
    if legal_entity_snapshot:
        source_types.append(legal_entity_snapshot.source_type)
    if contract_spec_snapshot:
        source_types.append(contract_spec_snapshot.source_type)
    if fee_schedule_snapshot:
        source_types.append(fee_schedule_snapshot.source_type)
    if swap_spec_snapshot:
        source_types.append(swap_spec_snapshot.source_type)
    if evidence_dataset and evidence_dataset.source_snapshot:
        source_types.append(evidence_dataset.source_snapshot.source_type)
    if telemetry_dataset and telemetry_dataset.source_snapshot:
        source_types.append(telemetry_dataset.source_snapshot.source_type)

    source_evidence: Dict[str, Dict[str, str]] = {}
    if legal_entity_snapshot:
        source_evidence["LEGAL_ENTITY"] = {
            "sha256": legal_entity_snapshot.raw_payload_bytes_sha256,
            "source_type": legal_entity_snapshot.source_type,
        }
    if contract_spec_snapshot:
        source_evidence["CONTRACT_SPEC"] = {
            "sha256": contract_spec_snapshot.raw_payload_bytes_sha256,
            "source_type": contract_spec_snapshot.source_type,
        }
    if fee_schedule_snapshot:
        source_evidence["COMMISSION"] = {
            "sha256": fee_schedule_snapshot.raw_payload_bytes_sha256,
            "source_type": fee_schedule_snapshot.source_type,
        }
    if swap_spec_snapshot:
        source_evidence["FINANCING"] = {
            "sha256": swap_spec_snapshot.raw_payload_bytes_sha256,
            "source_type": swap_spec_snapshot.source_type,
        }
    if evidence_dataset and evidence_dataset.source_snapshot:
        source_evidence["SPREAD_DATASET"] = {
            "sha256": evidence_dataset.source_snapshot.raw_payload_bytes_sha256,
            "source_type": evidence_dataset.source_snapshot.source_type,
        }
    if telemetry_dataset and telemetry_dataset.source_snapshot:
        source_evidence["SLIPPAGE_DATASET"] = {
            "sha256": telemetry_dataset.source_snapshot.raw_payload_bytes_sha256,
            "source_type": telemetry_dataset.source_snapshot.source_type,
        }

    fingerprint: Optional[str] = None
    if is_complete:
        fingerprint = compute_empirical_friction_fingerprint(
            semantic_versions=semantic_versions,
            venue=venue,
            legal_entity_code=str(legal_info.get("legal_entity_code", "")),
            account_tier=account_tier,
            symbol=symbol,
            contract_geometry=geom,
            source_snapshot_hashes=source_hashes,
            source_types=source_types,
            dataset_hashes=dataset_hashes,
            distribution_summaries=summaries_dict,
            calibrated_parameters=calibrated_params,
            commission_policy=comm,
            financing_policy=fin,
            bound_binding_roles=bound_roles,
            slippage_cost_policy_version=slippage_cost_policy_version,
            source_evidence=source_evidence,
        )

    ver_id = model_version_id or f"{venue}_{symbol}_{account_tier}_EMPIRICAL_{'V1' if is_complete else 'DRAFT'}"

    # Safe parsing helper without silent fallback defaults (Directive 2)
    def _opt_dec(val: Any) -> Optional[Decimal]:
        if val is None or str(val).strip() == "":
            return None
        return Decimal(str(val))

    def _opt_int(val: Any) -> Optional[int]:
        if val is None or str(val).strip() == "":
            return None
        return int(val)

    def _opt_bool(val: Any) -> Optional[bool]:
        if val is None:
            return None
        return bool(val)

    model_ver = FrictionModelVersion.objects.filter(model_version_id=ver_id).first()
    if not model_ver:
        model_ver = FrictionModelVersion.objects.create(
            model_version_id=ver_id,
            venue=venue.upper(),
            symbol=symbol.upper(),
            account_tier=account_tier.upper(),
            legal_entity_code=legal_info.get("legal_entity_code", ""),
            legal_entity_name=legal_info.get("legal_entity_name", ""),
            regulator=legal_info.get("regulator", ""),
            license_number=legal_info.get("license_number", ""),
            legal_entity_source_snapshot=legal_entity_snapshot,
            contract_spec_source_snapshot=contract_spec_snapshot,
            fee_schedule_source_snapshot=fee_schedule_snapshot,
            swap_spec_source_snapshot=swap_spec_snapshot,
            digits=_opt_int(geom.get("digits")),
            point_size=_opt_dec(geom.get("point_size")),
            trade_tick_size=_opt_dec(geom.get("trade_tick_size")),
            trade_tick_value=_opt_dec(geom.get("trade_tick_value")),
            contract_size=_opt_dec(geom.get("contract_size")),
            volume_min=_opt_dec(geom.get("volume_min")),
            volume_max=_opt_dec(geom.get("volume_max")),
            volume_step=_opt_dec(geom.get("volume_step")),
            native_commission_usd_per_lot_per_side=_opt_dec(comm.get("native_commission_usd_per_lot_per_side")),
            commission_formula=comm.get("commission_formula"),
            swap_long_points=_opt_dec(fin.get("swap_long_points")),
            swap_short_points=_opt_dec(fin.get("swap_short_points")),
            rollover_summer_utc_hour=_opt_int(fin.get("rollover_summer_utc_hour")),
            rollover_winter_utc_hour=_opt_int(fin.get("rollover_winter_utc_hour")),
            triple_swap_weekday=fin.get("triple_swap_weekday"),
            swap_free_available_for_account_type=_opt_bool(fin.get("swap_free_available_for_account_type")),
            actual_account_swap_free_status=_opt_bool(fin.get("actual_account_swap_free_status")),
            base_spread_bps=base_spread,
            stress_spread_bps=stress_spread,
            base_slippage_bps=base_slippage,
            stress_slippage_bps=stress_slippage,
            friction_policy_schema_version="1.0.0",
            distribution_algorithm_version="1.0.0",
            normalization_version="1.0.0",
            commission_formula_version="1.0.0",
            financing_rule_version="1.0.0",
            slippage_cost_policy_version=slippage_cost_policy_version,
            empirical_friction_evidence_fingerprint=fingerprint,
        )

    # Bind spread dataset and summary
    if evidence_dataset:
        ds_bind_id = hashlib.sha256(
            f"{model_ver.model_version_id}:{evidence_dataset.dataset_id}:{FrictionBindingRole.PRIMARY_SPREAD_SAMPLE}".encode()
        ).hexdigest()
        if not FrictionModelDatasetBinding.objects.filter(binding_id=ds_bind_id).exists():
            FrictionModelDatasetBinding.objects.create(
                binding_id=ds_bind_id,
                friction_model_version=model_ver,
                evidence_dataset=evidence_dataset,
                binding_role=FrictionBindingRole.PRIMARY_SPREAD_SAMPLE,
            )

    if spread_summary:
        sum_bind_id = hashlib.sha256(
            f"{model_ver.model_version_id}:{spread_summary.summary_id}:{FrictionBindingRole.NORMAL_SPREAD_DISTRIBUTION}".encode()
        ).hexdigest()
        if not FrictionModelSummaryBinding.objects.filter(binding_id=sum_bind_id).exists():
            FrictionModelSummaryBinding.objects.create(
                binding_id=sum_bind_id,
                friction_model_version=model_ver,
                distribution_summary=spread_summary,
                binding_role=FrictionBindingRole.NORMAL_SPREAD_DISTRIBUTION,
            )

    # Bind slippage dataset and summary
    if telemetry_dataset:
        telem_bind_id = hashlib.sha256(
            f"{model_ver.model_version_id}:{telemetry_dataset.dataset_id}:{FrictionBindingRole.PRIMARY_TELEMETRY_SAMPLE}".encode()
        ).hexdigest()
        if not FrictionModelDatasetBinding.objects.filter(binding_id=telem_bind_id).exists():
            FrictionModelDatasetBinding.objects.create(
                binding_id=telem_bind_id,
                friction_model_version=model_ver,
                evidence_dataset=telemetry_dataset,
                binding_role=FrictionBindingRole.PRIMARY_TELEMETRY_SAMPLE,
            )

    if slippage_summary:
        slip_bind_id = hashlib.sha256(
            f"{model_ver.model_version_id}:{slippage_summary.summary_id}:{FrictionBindingRole.NORMAL_SLIPPAGE_DISTRIBUTION}".encode()
        ).hexdigest()
        if not FrictionModelSummaryBinding.objects.filter(binding_id=slip_bind_id).exists():
            FrictionModelSummaryBinding.objects.create(
                binding_id=slip_bind_id,
                friction_model_version=model_ver,
                distribution_summary=slippage_summary,
                binding_role=FrictionBindingRole.NORMAL_SLIPPAGE_DISTRIBUTION,
            )

    # 4. Resolve activation status using canonical validator (Directives 5 & 6)
    target_entity = legal_info.get("legal_entity_code", "")
    val_res = validate_friction_model_for_activation(
        model_version=model_ver,
        target_venue=venue,
        target_symbol=symbol,
        target_account_tier=account_tier,
        target_legal_entity_code=target_entity,
        slippage_cost_policy_version=slippage_cost_policy_version,
    )

    act_status = FrictionActivationStatus.ACTIVE if val_res.is_valid else FrictionActivationStatus.DRAFT
    final_reason = activation_reason if val_res.is_valid else f"DRAFT: {'; '.join(val_res.reasons)}"

    act_id = hashlib.sha256(f"{model_ver.model_version_id}:{act_status}".encode()).hexdigest()
    activation = FrictionModelActivation.objects.filter(activation_id=act_id).first()
    if not activation:
        now_utc = datetime.now(timezone.utc)
        eff_from = effective_from or now_utc
        k_at = known_at or eff_from
        activation = FrictionModelActivation.objects.create(
            activation_id=act_id,
            friction_model_version=model_ver,
            known_at=k_at,
            effective_from=eff_from,
            effective_to=None,
            activation_status=act_status,
            source_or_reason=final_reason,
        )

    return model_ver, activation
