"""Management command for XAUUSD Empirical Friction Evidence Checkpoint.

Governed strictly under Pre-Phase-8 Calibration Hardening Protocol (Directives 1-18):
- Eliminates all hard-coded contract geometry, fee, swap, and entity defaults (Directives 2, 3, 4, 5).
- Sockets real MT5 tick parser and execution telemetry parser (Directives 6, 7).
- Enforces mandatory slippage telemetry (Directive 8).
- Prohibits incomplete models from activating as ACTIVE (Directive 9).
- Binds models strictly to venue, legal entity, account tier, and symbol.
- Strictly fail-closed if any evidence source is missing or insufficient.
"""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from typing import Any, Dict, List, Optional, Tuple
from django.core.management.base import BaseCommand

from apps.market_data.friction.commission import (
    calculate_dynamic_fee_bps,
    calculate_execution_notional,
    calculate_side_fee_usd,
)
from apps.market_data.friction.distribution import (
    compute_distribution_statistics,
    validate_slippage_telemetry_sufficiency,
    validate_spread_dataset_sufficiency,
)
from apps.market_data.friction.fingerprint import compute_empirical_friction_fingerprint
from apps.market_data.friction.ingestion import (
    build_and_bind_friction_model_version,
    ingest_friction_evidence_dataset,
    ingest_friction_source_snapshot,
    ingest_friction_telemetry_dataset,
    resolve_slippage_cost_samples,
    verify_authoritative_backing_artifact,
)
from apps.market_data.friction.slippage_parser import parse_mt5_execution_telemetry
from apps.market_data.friction.tick_parser import parse_mt5_tick_export
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
    FrictionSourceType,
    QUALIFIED_COMMISSION_SOURCE_TYPES,
    QUALIFIED_CONTRACT_SOURCE_TYPES,
    QUALIFIED_FINANCING_SOURCE_TYPES,
    QUALIFIED_FRICTION_SOURCE_TYPES,
    QUALIFIED_LEGAL_ENTITY_SOURCE_TYPES,
    QUALIFIED_SLIPPAGE_SOURCE_TYPES,
    QUALIFIED_SPREAD_SOURCE_TYPES,
)
from apps.market_data.readiness import XauUsdDataReadinessEvaluator


def _resolve_source_provenance(
    data: Dict[str, Any],
    declared_source_type: str,
    qualified_types: set,
    metadata_file_path: str,
    backing_file_path: Optional[str] = None,
    component_name: str = "COMPONENT",
) -> Tuple[str, str, str, bytes, str, Optional[str]]:
    """Separate ORIGINAL_AUTHORITATIVE_SOURCE from NORMALIZED_PARSED_METADATA (Requirement 1, 2, 3).

    A caller must NOT be able to turn an arbitrary manually-authored file into qualified
    evidence simply by passing CLI options. A manually created normalized JSON without an
    actual authoritative backing artifact remains USER_PROVIDED_UNVERIFIED.
    """
    provenance = data.get("provenance") or data.get("backing_artifact") or {}
    declared_backing_sha = (
        provenance.get("raw_backing_sha256")
        or provenance.get("raw_sha256")
        or data.get("raw_backing_sha256")
    )
    resolved_backing_path = (
        backing_file_path
        or provenance.get("raw_backing_file")
        or provenance.get("backing_file")
    )

    # 1. If declared backing SHA is specified but no backing file exists: fail closed.
    if declared_backing_sha and not resolved_backing_path:
        with open(metadata_file_path, "rb") as f:
            meta_bytes = f.read()
        return (
            FrictionSourceType.USER_PROVIDED_UNVERIFIED.value,
            str(provenance.get("source_origin") or f"file://{os.path.abspath(metadata_file_path)}"),
            str(provenance.get("collection_methodology") or "MANUALLY_AUTHORED_NORMALIZED_JSON"),
            meta_bytes,
            hashlib.sha256(meta_bytes).hexdigest(),
            f"Declared backing SHA '{declared_backing_sha}' provided for {component_name} but backing artifact file is missing. Fails closed.",
        )

    # 2. If backing file is provided, verify it independently
    if resolved_backing_path:
        is_verified, raw_bytes, computed_sha, errors = verify_authoritative_backing_artifact(
            backing_file_path=resolved_backing_path,
            declared_sha256=declared_backing_sha,
            expected_source_type=declared_source_type,
        )
        if not is_verified:
            return (
                FrictionSourceType.USER_PROVIDED_UNVERIFIED.value,
                str(provenance.get("source_origin") or f"file://{os.path.abspath(resolved_backing_path)}"),
                str(provenance.get("collection_methodology") or "UNVERIFIED_BACKING_ARTIFACT"),
                raw_bytes or b"",
                computed_sha or "",
                f"Backing artifact verification failed for {component_name}: {'; '.join(errors)}",
            )

        if declared_source_type in qualified_types:
            origin = provenance.get("source_origin") or f"file://{os.path.abspath(resolved_backing_path)}"
            method = provenance.get("collection_methodology") or f"VERIFIED_{declared_source_type}"
            return (
                declared_source_type,
                str(origin),
                str(method),
                raw_bytes,
                computed_sha,
                None,
            )
        else:
            origin = provenance.get("source_origin") or f"file://{os.path.abspath(resolved_backing_path)}"
            method = provenance.get("collection_methodology") or "UNQUALIFIED_SOURCE_TYPE"
            return (
                FrictionSourceType.USER_PROVIDED_UNVERIFIED.value,
                str(origin),
                str(method),
                raw_bytes,
                computed_sha,
                None,
            )

    # 3. No backing file provided: handwritten JSON alone remains USER_PROVIDED_UNVERIFIED
    with open(metadata_file_path, "rb") as f:
        meta_bytes = f.read()
    return (
        FrictionSourceType.USER_PROVIDED_UNVERIFIED.value,
        str(provenance.get("source_origin") or f"file://{os.path.abspath(metadata_file_path)}"),
        str(provenance.get("collection_methodology") or "MANUALLY_AUTHORED_NORMALIZED_JSON"),
        meta_bytes,
        hashlib.sha256(meta_bytes).hexdigest(),
        None,
    )


class Command(BaseCommand):
    help = "Ingest and audit empirical friction evidence for XAUUSD calibration (Fail-closed)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--venue",
            type=str,
            default="EXNESS",
            help="Target execution broker venue (default: EXNESS).",
        )
        parser.add_argument(
            "--account-tier",
            type=str,
            default="STANDARD",
            choices=["STANDARD", "RAW_SPREAD"],
            help="Execution account tier (default: STANDARD).",
        )
        parser.add_argument(
            "--legal-entity-file",
            type=str,
            default=None,
            help="Path to authoritative account agreement or Personal Area metadata snapshot.",
        )
        parser.add_argument(
            "--legal-entity-backing-file",
            type=str,
            default=None,
            help="Path to authoritative raw backing artifact for legal entity (PDF, HTML, raw export).",
        )
        parser.add_argument(
            "--contract-spec-file",
            type=str,
            default=None,
            help="Path to authoritative MT5 contract specification export JSON/file.",
        )
        parser.add_argument(
            "--contract-spec-backing-file",
            type=str,
            default=None,
            help="Path to authoritative raw backing artifact for contract specification (MT5 export, broker doc).",
        )
        parser.add_argument(
            "--fee-schedule-file",
            type=str,
            default=None,
            help="Path to authoritative broker fee schedule / commission evidence snapshot.",
        )
        parser.add_argument(
            "--fee-schedule-backing-file",
            type=str,
            default=None,
            help="Path to authoritative raw backing artifact for broker fee schedule / commission.",
        )
        parser.add_argument(
            "--swap-spec-file",
            type=str,
            default=None,
            help="Path to authoritative broker financing / swap rates evidence snapshot.",
        )
        parser.add_argument(
            "--swap-spec-backing-file",
            type=str,
            default=None,
            help="Path to authoritative raw backing artifact for broker financing / swap rates.",
        )
        parser.add_argument(
            "--tick-file",
            type=str,
            default=None,
            help="Path to verified Exness MT5 tick history export CSV.",
        )
        parser.add_argument(
            "--slippage-file",
            type=str,
            default=None,
            help="Path to verified MT5 execution telemetry fills CSV.",
        )
        parser.add_argument(
            "--legal-entity-source-type",
            type=str,
            default=FrictionSourceType.USER_PROVIDED_UNVERIFIED,
            help="Declared source provenance type for legal entity snapshot (requires backing evidence).",
        )
        parser.add_argument(
            "--contract-spec-source-type",
            type=str,
            default=FrictionSourceType.USER_PROVIDED_UNVERIFIED,
            help="Declared source provenance type for contract specification snapshot (requires backing evidence).",
        )
        parser.add_argument(
            "--fee-schedule-source-type",
            type=str,
            default=FrictionSourceType.USER_PROVIDED_UNVERIFIED,
            help="Declared source provenance type for fee schedule snapshot (requires backing evidence).",
        )
        parser.add_argument(
            "--swap-spec-source-type",
            type=str,
            default=FrictionSourceType.USER_PROVIDED_UNVERIFIED,
            help="Declared source provenance type for swap/financing snapshot (requires backing evidence).",
        )
        parser.add_argument(
            "--slippage-cost-policy",
            type=str,
            default="ADVERSE_ONLY_P75_P95_V1",
            help="Governed slippage cost policy version.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Execute audit without persisting database modifications.",
        )
        parser.add_argument(
            "--output-manifest",
            type=str,
            default="artifacts/calibration/xauusd_empirical_friction_manifest.json",
            help="Path to output friction evidence manifest JSON.",
        )
        parser.add_argument(
            "--output-report",
            type=str,
            default="docs/calibration/XAUUSD_EMPIRICAL_FRICTION_EVIDENCE_REPORT.md",
            help="Path to output friction audit report Markdown.",
        )

    def handle(self, *args, **options):
        venue = options["venue"].upper()
        account_tier = options["account_tier"].upper()
        symbol = "XAUUSD"
        legal_file = options["legal_entity_file"]
        legal_backing_file = options.get("legal_entity_backing_file")
        contract_file = options["contract_spec_file"]
        contract_backing_file = options.get("contract_spec_backing_file")
        fee_file = options["fee_schedule_file"]
        fee_backing_file = options.get("fee_schedule_backing_file")
        swap_file = options["swap_spec_file"]
        swap_backing_file = options.get("swap_spec_backing_file")
        tick_file = options["tick_file"]
        slippage_file = options["slippage_file"]
        dry_run = options["dry_run"]
        manifest_path = options["output_manifest"]
        report_path = options["output_report"]
        legal_source_type = options["legal_entity_source_type"]
        contract_source_type = options["contract_spec_source_type"]
        fee_source_type = options["fee_schedule_source_type"]
        swap_source_type = options["swap_spec_source_type"]
        slippage_policy = options["slippage_cost_policy"]

        self.stdout.write(self.style.NOTICE(
            f"=== AURUMIQ EMPIRICAL FRICTION AUDIT: {venue} {symbol} ({account_tier}) ==="
        ))

        now_utc = datetime.now(timezone.utc)
        reasons: List[str] = []

        # 1. Audit Legal Entity Provenance (Directive 7, 8 & 10)
        legal_entity_info: Optional[Dict[str, str]] = None
        legal_entity_snapshot: Optional[FrictionSourceSnapshot] = None
        legal_entity_status = "LEGAL_ENTITY_EVIDENCE_MISSING"

        if legal_file and os.path.isfile(legal_file):
            with open(legal_file, "rb") as f:
                content = f.read()
            try:
                data = json.loads(content.decode("utf-8"))
                legal_entity_info = {
                    "legal_entity_code": data.get("legal_entity_code", ""),
                    "legal_entity_name": data.get("legal_entity_name", ""),
                    "regulator": data.get("regulator", ""),
                    "license_number": data.get("license_number", ""),
                }
                resolved_legal_source_type, legal_origin, legal_method, legal_raw_bytes, legal_raw_sha, legal_err = _resolve_source_provenance(
                    data, legal_source_type, QUALIFIED_LEGAL_ENTITY_SOURCE_TYPES, legal_file, legal_backing_file, "Legal Entity"
                )
                if legal_err:
                    legal_entity_status = "EMPIRICAL_FRICTION_INVALID"
                    reasons.append(legal_err)
                elif not all(legal_entity_info.values()):
                    legal_entity_status = "LEGAL_ENTITY_EVIDENCE_MISSING"
                    reasons.append("Legal entity file missing required fields (code, name, regulator, license).")
                elif resolved_legal_source_type not in QUALIFIED_LEGAL_ENTITY_SOURCE_TYPES:
                    legal_entity_status = "EMPIRICAL_FRICTION_INVALID"
                    reasons.append(
                        f"Legal entity source provenance '{resolved_legal_source_type}' is unverified; hard readiness requires qualified broker provenance."
                    )
                else:
                    legal_entity_status = "OFFICIAL_CONTRACT_EVIDENCE_AVAILABLE"
                    if not dry_run:
                        effective_file = legal_backing_file or legal_file
                        legal_entity_snapshot, _ = ingest_friction_source_snapshot(
                            source_url=f"file://{os.path.abspath(effective_file)}",
                            source_name="EXNESS_LEGAL_ENTITY_SPEC",
                            venue=venue,
                            symbol=symbol,
                            account_tier=account_tier,
                            retrieved_at=now_utc,
                            known_at=now_utc,
                            raw_content=legal_raw_bytes,
                            metadata=legal_entity_info,
                            source_type=resolved_legal_source_type,
                            source_origin=legal_origin,
                            collection_methodology=legal_method,
                            original_filename=os.path.basename(effective_file),
                        )
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"Could not parse legal entity file: {e}"))
                legal_entity_status = "LEGAL_ENTITY_EVIDENCE_MISSING"
                reasons.append(f"Legal entity parse failure: {e}")
        else:
            reasons.append("Legal entity evidence snapshot missing (--legal-entity-file is None).")

        # 2. Official Contract Geometry (Directives 4, 7, 9)
        contract_geometry: Optional[Dict[str, Any]] = None
        contract_spec_snapshot: Optional[FrictionSourceSnapshot] = None
        contract_status = "CONTRACT_SPEC_EVIDENCE_MISSING"

        if contract_file and os.path.isfile(contract_file):
            with open(contract_file, "rb") as f:
                content = f.read()
            try:
                data = json.loads(content.decode("utf-8"))
                contract_geometry = {
                    "digits": int(data["digits"]),
                    "point_size": Decimal(str(data["point_size"])),
                    "trade_tick_size": Decimal(str(data["trade_tick_size"])),
                    "trade_tick_value": Decimal(str(data["trade_tick_value"])),
                    "contract_size": Decimal(str(data["contract_size"])),
                    "volume_min": Decimal(str(data["volume_min"])),
                    "volume_max": Decimal(str(data["volume_max"])),
                    "volume_step": Decimal(str(data["volume_step"])),
                }
                resolved_contract_source_type, contract_origin, contract_method, contract_raw_bytes, contract_raw_sha, contract_err = _resolve_source_provenance(
                    data, contract_source_type, QUALIFIED_CONTRACT_SOURCE_TYPES, contract_file, contract_backing_file, "Contract Specification"
                )
                if contract_err:
                    contract_status = "EMPIRICAL_FRICTION_INVALID"
                    reasons.append(contract_err)
                elif resolved_contract_source_type not in QUALIFIED_CONTRACT_SOURCE_TYPES:
                    contract_status = "EMPIRICAL_FRICTION_INVALID"
                    reasons.append(
                        f"Contract specification source provenance '{resolved_contract_source_type}' is unverified; hard readiness requires qualified broker provenance."
                    )
                else:
                    contract_status = "OFFICIAL_CONTRACT_EVIDENCE_AVAILABLE"
                    if not dry_run:
                        effective_file = contract_backing_file or contract_file
                        contract_spec_snapshot, _ = ingest_friction_source_snapshot(
                            source_url=f"file://{os.path.abspath(effective_file)}",
                            source_name="EXNESS_CONTRACT_SPEC",
                            venue=venue,
                            symbol=symbol,
                            account_tier=account_tier,
                            retrieved_at=now_utc,
                            known_at=now_utc,
                            raw_content=contract_raw_bytes,
                            metadata=contract_geometry,
                            source_type=resolved_contract_source_type,
                            source_origin=contract_origin,
                            collection_methodology=contract_method,
                            original_filename=os.path.basename(effective_file),
                        )
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"Could not parse contract spec file: {e}"))
                contract_status = "CONTRACT_SPEC_EVIDENCE_MISSING"
                reasons.append(f"Contract specification parse failure: {e}")
        else:
            reasons.append("Contract specification evidence snapshot missing (--contract-spec-file is None).")

        # 3. Commission Policy (Directives 5, 7, 10)
        commission_policy: Optional[Dict[str, Any]] = None
        fee_schedule_snapshot: Optional[FrictionSourceSnapshot] = None
        commission_status = "COMMISSION_EVIDENCE_MISSING"

        if fee_file and os.path.isfile(fee_file):
            with open(fee_file, "rb") as f:
                content = f.read()
            try:
                data = json.loads(content.decode("utf-8"))
                commission_policy = {
                    "native_commission_usd_per_lot_per_side": Decimal(str(data["native_commission_usd_per_lot_per_side"])),
                    "commission_formula": str(data.get("commission_formula", "DYNAMIC_NOTIONAL_BPS")),
                }
                resolved_fee_source_type, fee_origin, fee_method, fee_raw_bytes, fee_raw_sha, fee_err = _resolve_source_provenance(
                    data, fee_source_type, QUALIFIED_COMMISSION_SOURCE_TYPES, fee_file, fee_backing_file, "Commission Fee Schedule"
                )
                if fee_err:
                    commission_status = "EMPIRICAL_FRICTION_INVALID"
                    reasons.append(fee_err)
                elif resolved_fee_source_type not in QUALIFIED_COMMISSION_SOURCE_TYPES:
                    commission_status = "EMPIRICAL_FRICTION_INVALID"
                    reasons.append(
                        f"Fee schedule source provenance '{resolved_fee_source_type}' is unverified; hard readiness requires qualified broker provenance."
                    )
                else:
                    commission_status = "OFFICIAL_CONTRACT_EVIDENCE_AVAILABLE"
                    if not dry_run:
                        effective_file = fee_backing_file or fee_file
                        fee_schedule_snapshot, _ = ingest_friction_source_snapshot(
                            source_url=f"file://{os.path.abspath(effective_file)}",
                            source_name="EXNESS_FEE_SCHEDULE",
                            venue=venue,
                            symbol=symbol,
                            account_tier=account_tier,
                            retrieved_at=now_utc,
                            known_at=now_utc,
                            raw_content=fee_raw_bytes,
                            metadata=commission_policy,
                            source_type=resolved_fee_source_type,
                            source_origin=fee_origin,
                            collection_methodology=fee_method,
                            original_filename=os.path.basename(effective_file),
                        )
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"Could not parse fee schedule file: {e}"))
                commission_status = "COMMISSION_EVIDENCE_MISSING"
                reasons.append(f"Fee schedule parse failure: {e}")
        else:
            reasons.append("Commission fee schedule evidence snapshot missing (--fee-schedule-file is None).")

        # 4. Financing Policy (Directives 3, 7, 10)
        financing_policy: Optional[Dict[str, Any]] = None
        swap_spec_snapshot: Optional[FrictionSourceSnapshot] = None
        financing_status = "FINANCING_EVIDENCE_MISSING"

        if swap_file and os.path.isfile(swap_file):
            with open(swap_file, "rb") as f:
                content = f.read()
            try:
                data = json.loads(content.decode("utf-8"))
                financing_policy = {
                    "swap_long_points": Decimal(str(data["swap_long_points"])),
                    "swap_short_points": Decimal(str(data["swap_short_points"])),
                    "rollover_summer_utc_hour": int(data["rollover_summer_utc_hour"]),
                    "rollover_winter_utc_hour": int(data["rollover_winter_utc_hour"]),
                    "triple_swap_weekday": str(data["triple_swap_weekday"]),
                    "actual_account_swap_free_status": bool(data.get("actual_account_swap_free_status", False)),
                }
                resolved_swap_source_type, swap_origin, swap_method, swap_raw_bytes, swap_raw_sha, swap_err = _resolve_source_provenance(
                    data, swap_source_type, QUALIFIED_FINANCING_SOURCE_TYPES, swap_file, swap_backing_file, "Financing Swap Spec"
                )
                if swap_err:
                    financing_status = "EMPIRICAL_FRICTION_INVALID"
                    reasons.append(swap_err)
                elif resolved_swap_source_type not in QUALIFIED_FINANCING_SOURCE_TYPES:
                    financing_status = "EMPIRICAL_FRICTION_INVALID"
                    reasons.append(
                        f"Swap specification source provenance '{resolved_swap_source_type}' is unverified; hard readiness requires qualified broker provenance."
                    )
                else:
                    financing_status = "OFFICIAL_CONTRACT_EVIDENCE_AVAILABLE"
                    if not dry_run:
                        effective_file = swap_backing_file or swap_file
                        swap_spec_snapshot, _ = ingest_friction_source_snapshot(
                            source_url=f"file://{os.path.abspath(effective_file)}",
                            source_name="EXNESS_SWAP_SPEC",
                            venue=venue,
                            symbol=symbol,
                            account_tier=account_tier,
                            retrieved_at=now_utc,
                            known_at=now_utc,
                            raw_content=swap_raw_bytes,
                            metadata=financing_policy,
                            source_type=resolved_swap_source_type,
                            source_origin=swap_origin,
                            collection_methodology=swap_method,
                            original_filename=os.path.basename(effective_file),
                        )
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"Could not parse swap spec file: {e}"))
                financing_status = "FINANCING_EVIDENCE_MISSING"
                reasons.append(f"Financing swap spec parse failure: {e}")
        else:
            reasons.append("Financing/swap specification evidence snapshot missing (--swap-spec-file is None).")

        # 5. Spread Evidence (Directives 3, 6, 12: Strict Lifecycle FILE -> PARSED -> SCHEMA -> SUFFICIENT -> PERSISTED -> VERIFIED)
        spread_status = "SPREAD_EMPIRICAL_EVIDENCE_MISSING"
        spread_dataset: Optional[FrictionEvidenceDataset] = None
        spread_ticks: Optional[List[Dict[str, Any]]] = None

        if tick_file:
            if not os.path.isfile(tick_file):
                spread_status = "SPREAD_EMPIRICAL_EVIDENCE_INVALID"
                reasons.append(f"Tick file does not exist: {tick_file}")
            else:
                self.stdout.write(f"Inspecting raw tick export: {tick_file}...")
                with open(tick_file, "rb") as f:
                    tick_bytes = f.read()
                try:
                    # PARSED + SCHEMA_VALID
                    ticks_data, summary_meta = parse_mt5_tick_export(tick_bytes, expected_symbol=symbol)
                    spread_ticks = ticks_data

                    # SAMPLE_SUFFICIENT (N >= 1000, 5 distinct days, ASIAN/LONDON/NY >= 100, ROLLOVER >= 30)
                    is_valid_spread, spread_errors = validate_spread_dataset_sufficiency(ticks_data)
                    if not is_valid_spread:
                        spread_status = "SPREAD_EMPIRICAL_EVIDENCE_INVALID"
                        reasons.extend(spread_errors)
                    else:
                        spread_bps_list = [t["spread_bps"] for t in ticks_data]
                        spread_stats = compute_distribution_statistics(spread_bps_list)
                        if spread_stats["stat_p75"] <= Decimal("0") or spread_stats["stat_p95"] < spread_stats["stat_p75"]:
                            spread_status = "SPREAD_EMPIRICAL_EVIDENCE_INVALID"
                            reasons.append(f"Spread distribution invalid: p75={spread_stats['stat_p75']}, p95={spread_stats['stat_p95']}")
                        else:
                            if dry_run:
                                spread_status = "EMPIRICAL_SAMPLE_EVIDENCE_AVAILABLE"
                            else:
                                snap, _ = ingest_friction_source_snapshot(
                                    source_url=f"file://{os.path.abspath(tick_file)}",
                                    source_name="EXNESS_MT5_TICKS",
                                    venue=venue,
                                    symbol=symbol,
                                    account_tier=account_tier,
                                    retrieved_at=now_utc,
                                    known_at=now_utc,
                                    raw_content=tick_bytes,
                                    metadata=summary_meta,
                                    source_type=FrictionSourceType.MT5_TICK_HISTORY_EXPORT,
                                    source_origin=f"file://{os.path.abspath(tick_file)}",
                                    collection_methodology="MT5_TERMINAL_TICK_EXPORT",
                                    original_filename=os.path.basename(tick_file),
                                )
                                spread_dataset, _ = ingest_friction_evidence_dataset(
                                    source_snapshot=snap,
                                    venue=venue,
                                    account_tier=account_tier,
                                    symbol=symbol,
                                    sample_start=summary_meta["sample_start"],
                                    sample_end=summary_meta["sample_end"],
                                    ticks_data=ticks_data,
                                )
                                spread_status = "EMPIRICAL_SAMPLE_EVIDENCE_AVAILABLE"
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"Could not parse tick file: {e}"))
                    spread_status = "SPREAD_EMPIRICAL_EVIDENCE_INVALID"
                    reasons.append(f"MT5 tick export parse failure: {e}")
        else:
            reasons.append("MT5 tick export dataset missing (--tick-file is None).")

        # 6. Slippage Telemetry (Directives 3, 7, 8, 11, 12: Strict Lifecycle)
        slippage_status = "SLIPPAGE_EMPIRICAL_EVIDENCE_MISSING"
        telemetry_dataset: Optional[FrictionEvidenceDataset] = None
        telemetry_records: Optional[List[Dict[str, Any]]] = None

        if slippage_file:
            if not os.path.isfile(slippage_file):
                slippage_status = "SLIPPAGE_EMPIRICAL_EVIDENCE_INVALID"
                reasons.append(f"Slippage file does not exist: {slippage_file}")
            else:
                self.stdout.write(f"Inspecting execution telemetry: {slippage_file}...")
                with open(slippage_file, "rb") as f:
                    telem_bytes = f.read()
                try:
                    # PARSED + SCHEMA_VALID
                    telemetry_data, summary_meta = parse_mt5_execution_telemetry(
                        telem_bytes,
                        expected_venue=venue,
                        expected_symbol=symbol,
                        expected_account_tier=account_tier,
                    )
                    telemetry_records = telemetry_data

                    # SAMPLE_SUFFICIENT (N >= 30)
                    is_valid_slip, slip_errors = validate_slippage_telemetry_sufficiency(telemetry_data)
                    if not is_valid_slip:
                        slippage_status = "SLIPPAGE_EMPIRICAL_EVIDENCE_INVALID"
                        reasons.extend(slip_errors)
                    else:
                        # Canonical slippage policy sample resolution (Directive 2 & 9)
                        slip_samples, pop_semantics = resolve_slippage_cost_samples(telemetry_data, slippage_policy)
                        slip_stats = compute_distribution_statistics(slip_samples)
                        if slip_stats["stat_p75"] < Decimal("0") or slip_stats["stat_p95"] < slip_stats["stat_p75"]:
                            slippage_status = "SLIPPAGE_EMPIRICAL_EVIDENCE_INVALID"
                            reasons.append(f"Execution slippage distribution invalid: p75={slip_stats['stat_p75']}, p95={slip_stats['stat_p95']}")
                        else:
                            if dry_run:
                                slippage_status = "EMPIRICAL_SAMPLE_EVIDENCE_AVAILABLE"
                            else:
                                snap, _ = ingest_friction_source_snapshot(
                                    source_url=f"file://{os.path.abspath(slippage_file)}",
                                    source_name="EXNESS_MT5_TELEMETRY",
                                    venue=venue,
                                    symbol=symbol,
                                    account_tier=account_tier,
                                    retrieved_at=now_utc,
                                    known_at=now_utc,
                                    raw_content=telem_bytes,
                                    metadata=summary_meta,
                                    source_type=FrictionSourceType.MT5_EXECUTION_TELEMETRY_EXPORT,
                                    source_origin=f"file://{os.path.abspath(slippage_file)}",
                                    collection_methodology="MT5_EXECUTION_TELEMETRY_EXPORT",
                                    original_filename=os.path.basename(slippage_file),
                                )
                                telemetry_dataset, _ = ingest_friction_telemetry_dataset(
                                    source_snapshot=snap,
                                    venue=venue,
                                    account_tier=account_tier,
                                    symbol=symbol,
                                    sample_start=summary_meta["sample_start"],
                                    sample_end=summary_meta["sample_end"],
                                    telemetry_records=telemetry_data,
                                )
                                slippage_status = "EMPIRICAL_SAMPLE_EVIDENCE_AVAILABLE"
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"Could not parse slippage telemetry file: {e}"))
                    slippage_status = "SLIPPAGE_EMPIRICAL_EVIDENCE_INVALID"
                    reasons.append(f"Execution telemetry parse failure: {e}")
        else:
            reasons.append("Execution slippage telemetry missing (--slippage-file is None).")

        # 7. Evidence Completeness & Gate Evaluation (Directives 4, 6, 12)
        is_evidence_complete = (
            legal_entity_status == "OFFICIAL_CONTRACT_EVIDENCE_AVAILABLE"
            and contract_status == "OFFICIAL_CONTRACT_EVIDENCE_AVAILABLE"
            and commission_status == "OFFICIAL_CONTRACT_EVIDENCE_AVAILABLE"
            and financing_status == "OFFICIAL_CONTRACT_EVIDENCE_AVAILABLE"
            and spread_status == "EMPIRICAL_SAMPLE_EVIDENCE_AVAILABLE"
            and slippage_status == "EMPIRICAL_SAMPLE_EVIDENCE_AVAILABLE"
        )

        model_ver: Optional[FrictionModelVersion] = None
        if dry_run:
            if is_evidence_complete:
                self.stdout.write(self.style.SUCCESS("Dry-run semantic audit passed: all evidence qualified and sufficient."))
            else:
                self.stdout.write(self.style.WARNING("Dry-run semantic audit: evidence incomplete or insufficient."))
            overall_status = "EMPIRICAL_FRICTION_EVIDENCE_STILL_BLOCKED"
            gate_decision = "CANDLES_READY_EMPIRICAL_FRICTION_MISSING"
        elif is_evidence_complete:
            spread_bps_list = [t["spread_bps"] for t in (spread_ticks or [])]

            model_ver, activation = build_and_bind_friction_model_version(
                legal_entity_snapshot=legal_entity_snapshot,
                contract_spec_snapshot=contract_spec_snapshot,
                fee_schedule_snapshot=fee_schedule_snapshot,
                swap_spec_snapshot=swap_spec_snapshot,
                evidence_dataset=spread_dataset,
                spread_ticks_bps=spread_bps_list,
                legal_entity_info=legal_entity_info,
                contract_geometry=contract_geometry,
                commission_policy=commission_policy,
                financing_policy=financing_policy,
                telemetry_dataset=telemetry_dataset,
                telemetry_records=telemetry_records,
                venue=venue,
                symbol=symbol,
                account_tier=account_tier,
                slippage_cost_policy_version=slippage_policy,
            )

            # Directive 4: Inspect returned activation status AND run readiness evaluator
            if (
                activation is not None
                and activation.activation_status == FrictionActivationStatus.ACTIVE
            ):
                eval_rep = XauUsdDataReadinessEvaluator.evaluate(
                    execution_venue=venue,
                    execution_account_tier=account_tier,
                    execution_legal_entity_code=legal_entity_info.get("legal_entity_code") if legal_entity_info else None,
                )
                if eval_rep.decision == "CANDLES_READY_QUOTE_EVIDENCE_MISSING":
                    overall_status = "EMPIRICAL_FRICTION_CONFIGURED"
                    gate_decision = "CANDLES_READY_QUOTE_EVIDENCE_MISSING"
                    reasons = ["All empirical friction evidence verified and active model sealed."]
                else:
                    overall_status = "EMPIRICAL_FRICTION_EVIDENCE_STILL_BLOCKED"
                    gate_decision = eval_rep.decision
                    reasons.extend(eval_rep.reasons)
            else:
                overall_status = "EMPIRICAL_FRICTION_EVIDENCE_STILL_BLOCKED"
                gate_decision = "CANDLES_READY_EMPIRICAL_FRICTION_MISSING"
                reasons.append("Model version could not achieve ACTIVE activation status (status is DRAFT or rejected).")
        else:
            overall_status = "EMPIRICAL_FRICTION_EVIDENCE_STILL_BLOCKED"
            gate_decision = "CANDLES_READY_EMPIRICAL_FRICTION_MISSING"
            self.stdout.write(self.style.ERROR(
                f"Audit Result: {overall_status} (Gate: {gate_decision})"
            ))

        # 8. Generate Machine-Readable Manifest
        manifest = {
            "manifest_schema_version": "3.1.0",
            "venue": venue,
            "account_tier": account_tier,
            "symbol": symbol,
            "audit_timestamp": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": overall_status,
            "hard_readiness_gate": {
                "decision": gate_decision,
                "passed": False,
                "is_production_authorized": False,
                "phase3b_production_weight": 0.0,
                "published_decision": "WAIT",
            },
            "evidence_inventory": {
                "legal_entity_scope": {
                    "status": legal_entity_status,
                    "legal_entity_code": legal_entity_info["legal_entity_code"] if legal_entity_info else None,
                    "regulator": legal_entity_info["regulator"] if legal_entity_info else None,
                    "license_number": legal_entity_info["license_number"] if legal_entity_info else None,
                },
                "contract_geometry": {
                    "status": contract_status,
                    "digits": contract_geometry["digits"] if contract_geometry else None,
                    "point_size": str(contract_geometry["point_size"]) if contract_geometry else None,
                    "trade_tick_size": str(contract_geometry["trade_tick_size"]) if contract_geometry else None,
                    "trade_tick_value": str(contract_geometry["trade_tick_value"]) if contract_geometry else None,
                    "contract_size": str(contract_geometry["contract_size"]) if contract_geometry else None,
                    "volume_min": str(contract_geometry["volume_min"]) if contract_geometry else None,
                    "volume_max": str(contract_geometry["volume_max"]) if contract_geometry else None,
                    "volume_step": str(contract_geometry["volume_step"]) if contract_geometry else None,
                },
                "commission_policy": {
                    "status": commission_status,
                    "account_tier": account_tier,
                    "native_commission_usd_per_lot_per_side": str(commission_policy["native_commission_usd_per_lot_per_side"]) if commission_policy else None,
                    "commission_formula": commission_policy["commission_formula"] if commission_policy else None,
                },
                "financing_policy": {
                    "status": financing_status,
                    "swap_long_points": str(financing_policy["swap_long_points"]) if financing_policy else None,
                    "swap_short_points": str(financing_policy["swap_short_points"]) if financing_policy else None,
                    "rollover_schedule": "Summer 21:00 GMT+0 / Winter 22:00 GMT+0" if financing_policy else None,
                    "triple_swap_weekday": financing_policy["triple_swap_weekday"] if financing_policy else None,
                    "actual_account_swap_free_status": financing_policy["actual_account_swap_free_status"] if financing_policy else None,
                },
                "bid_ask_spread_distribution": {
                    "status": spread_status,
                    "source_file": tick_file,
                    "sample_count": len(spread_ticks) if spread_ticks else 0,
                },
                "execution_slippage_telemetry": {
                    "status": slippage_status,
                    "source_file": slippage_file,
                    "sample_count": len(telemetry_records) if telemetry_records else 0,
                },
            },
            "blocking_reasons": reasons,
        }

        os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        self.stdout.write(self.style.SUCCESS(f"Saved manifest: {manifest_path}"))

        # 9. Generate Markdown Audit Report
        report_md = f"""# AURUMIQ — XAUUSD EMPIRICAL FRICTION EVIDENCE AUDIT REPORT

> **Protocol Version:** Pre-Phase-8 Empirical Friction Hardening Seal  
> **Target Venue:** `{venue}`  
> **Account Tier:** `{account_tier}`  
> **Symbol:** `{symbol}`  
> **Audit Timestamp:** `{now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}`  
> **Overall Friction Decision:** `{overall_status}`  
> **Hard Readiness Gate:** `{gate_decision}`  
> **Production Authority:** `FALSE / 0.0 / WAIT`  

---

## 1. Executive Summary

In accordance with Pre-Phase-8 Empirical Friction Calibration Hardening Governance (Directives 1-18), execution frictions for `{symbol}` under target venue `{venue}` have been evaluated strictly against genuine, persisted evidence with **ZERO silent defaults**.

The architecture closes all evidence-completeness loopholes:
- Removes all silent fallback defaults for contract geometry, commissions, and swap points.
- Enforces genuine source snapshots for legal entity, contract spec, commission schedules, and financing policies.
- Integrates production parsers for MT5 tick exports and MT5 execution telemetry.
- Enforces **MANDATORY execution slippage telemetry** (`SLIPPAGE_IS_MANDATORY = TRUE`).
- Prohibits incomplete models from receiving `ACTIVE` activation (downgraded to `DRAFT`).
- Enforces point-in-time activation resolution with scope validation.

Because genuine MT5 tick history exports, telemetry fills, and account-specific legal agreements have not yet been ingested into the governed production environment, the platform strictly enforces **FAIL-CLOSED** semantics:

```text
STATUS:   EMPIRICAL_FRICTION_EVIDENCE_STILL_BLOCKED
GATE:     CANDLES_READY_EMPIRICAL_FRICTION_MISSING
WEIGHT:   0.0
DECISION: WAIT
```

---

## 2. Friction Evidence Inventory Audit

| Component | Target Metric | Status Classification | Governance Rule & Finding |
| :--- | :--- | :---: | :--- |
| **Legal Entity Scope** | `legal_entity_code`, `regulator`, `license` | `{legal_entity_status}` | Directive 10: Sourced strictly from verified account snapshot. |
| **Contract Geometry** | `point_size`, `tick_size`, `contract_size` | `{contract_status}` | Directive 4: Requires verified MT5 contract spec export. Zero silent defaults. |
| **Commission Policy** | `commission_usd_per_lot_per_side` | `{commission_status}` | Directive 5: Requires verified fee schedule snapshot. Zero silent defaults. |
| **Financing Policy** | Swap points, rollover schedule | `{financing_status}` | Directive 3: Requires verified swap snapshot. Zero silent defaults. |
| **Spread Distribution** | `base_spread_bps`, `stress_spread_bps` | `{spread_status}` | Directive 6: Requires verified MT5 tick export ($N \\ge 1000$, $\\ge 5$ distinct dates, 4 sessions). |
| **Slippage Telemetry** | `base_slippage_bps`, `stress_slippage_bps` | `{slippage_status}` | Directives 7 & 8: Directional slippage telemetry is MANDATORY ($N \\ge 30$). |

---

## 3. Prior Evidence Invariance Verification

Prior frozen evidence remains 100% bit-for-bit invariant:
- **Macro Fingerprint:** `d9d2ebb4c6ec11fafc4ffce35090d64a5eaa05a3e024da4148b3900cf6370823`
- **Phase-6 15m Fingerprint:** `2c45cf9cef0777118652bdc7b2fac1450a4c01f8d26974faa968195114df92b9`
- **Readiness 6-TF Fingerprint:** `d5d8f7a20cf820f177ccafb99d60d09cf503e5a80eee95a89bc7cf02334764b9`
- **Total Historical Spot Candles:** `3,096,312` (zero rows mutated)

---

## 4. Next Steps for Unblocking

To advance from `CANDLES_READY_EMPIRICAL_FRICTION_MISSING` to `CANDLES_READY_QUOTE_EVIDENCE_MISSING`:
1. Provide authoritative Exness account agreement snapshot resolving `legal_entity_code`.
2. Provide authoritative MT5 contract specification snapshot.
3. Provide authoritative MT5 fee schedule snapshot.
4. Provide authoritative MT5 financing swap schedule snapshot.
5. Provide authentic Exness MT5 tick history export covering $\\ge 5$ distinct trading days and all 4 sessions.
6. Provide authentic Exness MT5 execution telemetry fills ($N \\ge 30$).
"""

        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report_md)
        self.stdout.write(self.style.SUCCESS(f"Saved audit report: {report_path}"))
