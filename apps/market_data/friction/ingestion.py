"""Idempotent, append-only ingestion engine for XAUUSD empirical friction evidence.

Adheres strictly to Pre-Phase-8 Calibration Governance:
- Ingests immutable FrictionSourceSnapshot payloads.
- Validates temporal sample sufficiency on FrictionEvidenceDataset.
- Derives FrictionDistributionSummary percentiles.
- Generates immutable bindings (FrictionModelDatasetBinding, FrictionModelSummaryBinding).
- Computes deterministic semantic fingerprint and persists FrictionModelVersion.
- Creates append-only FrictionModelActivation record.
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
    FrictionDistributionSummary,
    FrictionEvidenceDataset,
    FrictionModelActivation,
    FrictionModelDatasetBinding,
    FrictionModelSummaryBinding,
    FrictionModelVersion,
    FrictionSessionType,
    FrictionSourceSnapshot,
)
from apps.market_data.friction.distribution import (
    compute_distribution_statistics,
    validate_spread_dataset_sufficiency,
)
from apps.market_data.friction.fingerprint import compute_empirical_friction_fingerprint

logger = logging.getLogger(__name__)


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
) -> Tuple[FrictionSourceSnapshot, bool]:
    """Ingest an immutable FrictionSourceSnapshot (idempotent on snapshot_id)."""
    raw_sha = hashlib.sha256(raw_content).hexdigest()
    snapshot_id = hashlib.sha256(f"{source_name}:{raw_sha}".encode()).hexdigest()

    existing = FrictionSourceSnapshot.objects.filter(snapshot_id=snapshot_id).first()
    if existing:
        return existing, False

    snapshot = FrictionSourceSnapshot.objects.create(
        snapshot_id=snapshot_id,
        source_url=source_url,
        source_name=source_name,
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
    """Ingest FrictionEvidenceDataset after verifying temporal sample sufficiency."""
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


def build_and_bind_friction_model_version(
    legal_entity_snapshot: FrictionSourceSnapshot,
    contract_spec_snapshot: FrictionSourceSnapshot,
    fee_schedule_snapshot: FrictionSourceSnapshot,
    swap_spec_snapshot: FrictionSourceSnapshot,
    evidence_dataset: FrictionEvidenceDataset,
    spread_ticks_bps: List[Decimal],
    legal_entity_info: Dict[str, str],
    contract_geometry: Dict[str, Any],
    commission_policy: Dict[str, Any],
    financing_policy: Dict[str, Any],
    venue: str = "EXNESS",
    symbol: str = "XAUUSD",
    account_tier: str = "STANDARD",
    model_version_id: Optional[str] = None,
    activation_reason: str = "Pre-Phase-8 Empirical Friction Calibration Baseline",
    effective_from: Optional[datetime] = None,
) -> Tuple[FrictionModelVersion, FrictionModelActivation]:
    """Calculate distribution statistics, build immutable bindings, and seal FrictionModelVersion."""
    # 1. Compute distribution statistics across spread ticks
    stats = compute_distribution_statistics(spread_ticks_bps)

    summary_id = hashlib.sha256(
        f"{evidence_dataset.dataset_id}:SPREAD:NORMAL:ALL:{stats['stat_p75']}".encode()
    ).hexdigest()

    summary = FrictionDistributionSummary.objects.filter(summary_id=summary_id).first()
    if not summary:
        summary = FrictionDistributionSummary.objects.create(
            summary_id=summary_id,
            evidence_dataset=evidence_dataset,
            component_type=FrictionComponentType.SPREAD,
            condition=FrictionConditionType.NORMAL,
            session=FrictionSessionType.ALL,
            unit="BPS",
            sample_count=int(stats["sample_count"]),
            stat_min=stats["stat_min"],
            stat_p50=stats["stat_p50"],
            stat_p75=stats["stat_p75"],
            stat_p90=stats["stat_p90"],
            stat_p95=stats["stat_p95"],
            stat_p99=stats["stat_p99"],
            stat_max=stats["stat_max"],
            stat_mean=stats["stat_mean"],
            stat_std=stats["stat_std"],
        )

    # Base spread from empirical p75; stress spread from empirical p95
    base_spread = stats["stat_p75"]
    stress_spread = stats["stat_p95"]

    semantic_versions = {
        "friction_policy_schema_version": "1.0.0",
        "distribution_algorithm_version": "1.0.0",
        "normalization_version": "1.0.0",
        "commission_formula_version": "1.0.0",
        "financing_rule_version": "1.0.0",
    }

    calibrated_params = {
        "base_spread_bps": base_spread,
        "stress_spread_bps": stress_spread,
        "base_slippage_bps": None,
        "stress_slippage_bps": None,
    }

    source_hashes = [
        legal_entity_snapshot.raw_payload_bytes_sha256,
        contract_spec_snapshot.raw_payload_bytes_sha256,
        fee_schedule_snapshot.raw_payload_bytes_sha256,
        swap_spec_snapshot.raw_payload_bytes_sha256,
        evidence_dataset.source_snapshot.raw_payload_bytes_sha256,
    ]
    dataset_hashes = [evidence_dataset.raw_dataset_sha256]
    bound_roles = [
        FrictionBindingRole.PRIMARY_SPREAD_SAMPLE,
        FrictionBindingRole.NORMAL_SPREAD_DISTRIBUTION,
    ]

    summaries_dict = [
        {
            "component_type": str(summary.component_type),
            "condition": str(summary.condition),
            "session": str(summary.session),
            "unit": str(summary.unit),
            "sample_count": summary.sample_count,
            "stat_min": summary.stat_min,
            "stat_p50": summary.stat_p50,
            "stat_p75": summary.stat_p75,
            "stat_p90": summary.stat_p90,
            "stat_p95": summary.stat_p95,
            "stat_p99": summary.stat_p99,
            "stat_max": summary.stat_max,
            "stat_mean": summary.stat_mean,
            "stat_std": summary.stat_std,
        }
    ]

    fingerprint = compute_empirical_friction_fingerprint(
        semantic_versions=semantic_versions,
        venue=venue,
        legal_entity_code=legal_entity_info["legal_entity_code"],
        account_tier=account_tier,
        symbol=symbol,
        contract_geometry=contract_geometry,
        source_snapshot_hashes=source_hashes,
        dataset_hashes=dataset_hashes,
        distribution_summaries=summaries_dict,
        calibrated_parameters=calibrated_params,
        commission_policy=commission_policy,
        financing_policy=financing_policy,
        bound_binding_roles=bound_roles,
    )

    ver_id = model_version_id or f"{venue}_{symbol}_{account_tier}_EMPIRICAL_V1"

    model_ver = FrictionModelVersion.objects.filter(model_version_id=ver_id).first()
    if not model_ver:
        model_ver = FrictionModelVersion.objects.create(
            model_version_id=ver_id,
            venue=venue.upper(),
            symbol=symbol.upper(),
            account_tier=account_tier.upper(),
            legal_entity_code=legal_entity_info["legal_entity_code"],
            legal_entity_name=legal_entity_info["legal_entity_name"],
            regulator=legal_entity_info["regulator"],
            license_number=legal_entity_info["license_number"],
            legal_entity_source_snapshot=legal_entity_snapshot,
            contract_spec_source_snapshot=contract_spec_snapshot,
            fee_schedule_source_snapshot=fee_schedule_snapshot,
            swap_spec_source_snapshot=swap_spec_snapshot,
            digits=int(contract_geometry.get("digits", 2)),
            point_size=Decimal(str(contract_geometry.get("point_size", "0.01"))),
            trade_tick_size=Decimal(str(contract_geometry.get("trade_tick_size", "0.01"))),
            trade_tick_value=Decimal(str(contract_geometry.get("trade_tick_value", "1.00"))),
            contract_size=Decimal(str(contract_geometry.get("contract_size", "100.0"))),
            volume_min=Decimal(str(contract_geometry.get("volume_min", "0.01"))),
            volume_max=Decimal(str(contract_geometry.get("volume_max", "200.0"))),
            volume_step=Decimal(str(contract_geometry.get("volume_step", "0.01"))),
            native_commission_usd_per_lot_per_side=Decimal(
                str(commission_policy.get("native_commission_usd_per_lot_per_side", "0.00"))
            ),
            commission_formula=str(commission_policy.get("commission_formula", "DYNAMIC_NOTIONAL_BPS")),
            swap_long_points=Decimal(str(financing_policy.get("swap_long_points", "0.00"))),
            swap_short_points=Decimal(str(financing_policy.get("swap_short_points", "0.00"))),
            rollover_summer_utc_hour=int(financing_policy.get("rollover_summer_utc_hour", 21)),
            rollover_winter_utc_hour=int(financing_policy.get("rollover_winter_utc_hour", 22)),
            triple_swap_weekday=str(financing_policy.get("triple_swap_weekday", "WEDNESDAY")),
            swap_free_available_for_account_type=bool(
                financing_policy.get("swap_free_available_for_account_type", False)
            ),
            actual_account_swap_free_status=bool(
                financing_policy.get("actual_account_swap_free_status", False)
            ),
            base_spread_bps=base_spread,
            stress_spread_bps=stress_spread,
            base_slippage_bps=None,
            stress_slippage_bps=None,
            friction_policy_schema_version="1.0.0",
            distribution_algorithm_version="1.0.0",
            normalization_version="1.0.0",
            commission_formula_version="1.0.0",
            financing_rule_version="1.0.0",
            empirical_friction_evidence_fingerprint=fingerprint,
        )

    # 2. Bind dataset
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

    # 3. Bind summary
    sum_bind_id = hashlib.sha256(
        f"{model_ver.model_version_id}:{summary.summary_id}:{FrictionBindingRole.NORMAL_SPREAD_DISTRIBUTION}".encode()
    ).hexdigest()
    if not FrictionModelSummaryBinding.objects.filter(binding_id=sum_bind_id).exists():
        FrictionModelSummaryBinding.objects.create(
            binding_id=sum_bind_id,
            friction_model_version=model_ver,
            distribution_summary=summary,
            binding_role=FrictionBindingRole.NORMAL_SPREAD_DISTRIBUTION,
        )

    # 4. Activate model version (append-only)
    act_id = hashlib.sha256(f"{model_ver.model_version_id}:ACTIVE".encode()).hexdigest()
    activation = FrictionModelActivation.objects.filter(activation_id=act_id).first()
    if not activation:
        now_utc = datetime.now(timezone.utc)
        eff_from = effective_from or now_utc
        activation = FrictionModelActivation.objects.create(
            activation_id=act_id,
            friction_model_version=model_ver,
            known_at=now_utc,
            effective_from=eff_from,
            effective_to=None,
            activation_status=FrictionActivationStatus.ACTIVE,
            source_or_reason=activation_reason,
        )

    return model_ver, activation
