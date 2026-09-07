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
from typing import Any, Dict, List, Optional, Set, Tuple

from apps.market_data.models import (
    FrictionAttestationStatus,
    FrictionBindingRole,
    FrictionComponentType,
    FrictionDistributionSummary,
    FrictionEvidenceDataset,
    FrictionModelVersion,
    FrictionPopulationSemantics,
    FrictionQualificationStatus,
    FrictionSourceProvenanceAttestation,
    FrictionSourceQualificationAssertion,
    FrictionSourceSnapshot,
    FrictionSourceType,
    FrictionVerificationMethod,
    ACCEPTED_VERIFICATION_METHODS,
    QUALIFIED_COMMISSION_SOURCE_TYPES,
    QUALIFIED_CONTRACT_SOURCE_TYPES,
    QUALIFIED_FINANCING_SOURCE_TYPES,
    QUALIFIED_FRICTION_SOURCE_TYPES,
    QUALIFIED_LEGAL_ENTITY_SOURCE_TYPES,
    QUALIFIED_SLIPPAGE_SOURCE_TYPES,
    QUALIFIED_SPREAD_SOURCE_TYPES,
)
from apps.market_data.friction.artifact_parsers import (
    compute_normalized_evidence_hash,
    parse_commission_backing_artifact,
    parse_contract_spec_backing_artifact,
    parse_financing_backing_artifact,
    parse_legal_entity_backing_artifact,
)
from apps.market_data.friction.tick_parser import parse_mt5_tick_export
from apps.market_data.friction.slippage_parser import parse_mt5_execution_telemetry
from apps.market_data.friction.fingerprint import compute_empirical_friction_fingerprint
from apps.market_data.friction.provenance import verify_attestation_authenticity

TRUSTED_PARSERS_BY_ROLE: Dict[str, Set[str]] = {
    "LEGAL_ENTITY": {"parse_legal_entity_backing_artifact"},
    "CONTRACT_SPEC": {"parse_contract_spec_backing_artifact"},
    "COMMISSION": {"parse_commission_backing_artifact"},
    "FINANCING": {"parse_financing_backing_artifact"},
    "SPREAD_DATASET": {"parse_mt5_tick_export"},
    "SLIPPAGE_DATASET": {"parse_mt5_execution_telemetry"},
}
SUPPORTED_PARSER_VERSIONS: Set[str] = {"1.0.0"}


def validate_source_qualification_assertion(
    snapshot: Optional[FrictionSourceSnapshot],
    assertion: Optional[FrictionSourceQualificationAssertion],
    expected_component_role: str,
    expected_parser: Optional[str] = None,
    model_version: Optional[FrictionModelVersion] = None,
    expected_symbol: str = "XAUUSD",
    expected_account_tier: str = "STANDARD",
    expected_venue: str = "EXNESS",
) -> Tuple[bool, List[str], Optional[Dict[str, Any]]]:
    """Independently verify the integrity of a FrictionSourceQualificationAssertion.

    Hardened qualification assertion validation (Directives §5 & §7):
    A qualification assertion MUST NOT be trusted merely because qualification_status == QUALIFIED.
    This function independently verifies:
    1. assertion.source_snapshot == snapshot
    2. assertion.component_role == expected_component_role
    3. assertion.qualification_status == QUALIFIED
    4. assertion.raw_artifact_sha256 == snapshot.raw_payload_bytes_sha256
    5. assertion.raw_artifact_sha256 == sha256(snapshot.raw_content)
    6. assertion.parser_name belongs to strict trusted allowlist for the role
    7. assertion.parser_version is supported
    8. assertion.normalized_evidence_hash == independently recomputed normalized evidence hash
       produced from snapshot.raw_content by that exact trusted parser and version
    9. parsed normalized fields match model fields used for qualification (if model_version provided).
    """
    reasons: List[str] = []
    if snapshot is None:
        return False, ["Snapshot is missing."], None
    if assertion is None:
        return False, ["Qualification assertion is missing."], None

    # 1. Source Snapshot binding
    if assertion.source_snapshot_id != snapshot.snapshot_id:
        reasons.append(
            f"Assertion source_snapshot '{assertion.source_snapshot_id}' does not match snapshot '{snapshot.snapshot_id}'."
        )

    # 2. Component role match
    if assertion.component_role != expected_component_role:
        reasons.append(
            f"Assertion component_role '{assertion.component_role}' does not match expected role '{expected_component_role}'."
        )

    # 3. Status is QUALIFIED
    if assertion.qualification_status != FrictionQualificationStatus.QUALIFIED.value:
        reasons.append(
            f"Assertion qualification_status is '{assertion.qualification_status}', expected '{FrictionQualificationStatus.QUALIFIED.value}'."
        )

    # 4 & 5. Raw artifact SHA256 integrity
    if assertion.raw_artifact_sha256 != snapshot.raw_payload_bytes_sha256:
        reasons.append(
            f"Assertion raw_artifact_sha256 '{assertion.raw_artifact_sha256}' does not match snapshot.raw_payload_bytes_sha256 '{snapshot.raw_payload_bytes_sha256}'."
        )

    if not snapshot.raw_content:
        reasons.append("Snapshot raw_content is empty or null.")
    else:
        computed_raw_sha = hashlib.sha256(snapshot.raw_content).hexdigest()
        if assertion.raw_artifact_sha256 != computed_raw_sha:
            reasons.append(
                f"Assertion raw_artifact_sha256 '{assertion.raw_artifact_sha256}' does not match computed sha256 of snapshot.raw_content '{computed_raw_sha}'."
            )

    # 6. Strict trusted parser allowlist
    trusted_parsers = TRUSTED_PARSERS_BY_ROLE.get(expected_component_role, set())
    if assertion.parser_name not in trusted_parsers:
        reasons.append(
            f"Assertion parser_name '{assertion.parser_name}' is not in trusted allowlist for role '{expected_component_role}': {sorted(trusted_parsers)}."
        )
    if expected_parser and assertion.parser_name != expected_parser:
        reasons.append(
            f"Assertion parser_name '{assertion.parser_name}' does not match expected parser '{expected_parser}'."
        )

    # 7. Parser version supported
    if not assertion.parser_version or assertion.parser_version not in SUPPORTED_PARSER_VERSIONS:
        reasons.append(
            f"Assertion parser_version '{assertion.parser_version}' is not supported (supported: {sorted(SUPPORTED_PARSER_VERSIONS)})."
        )

    if reasons:
        return False, reasons, None

    # 8. Independent recomputation of normalized evidence
    parsed_data: Dict[str, Any] = {}
    try:
        if expected_component_role == "LEGAL_ENTITY":
            parsed_data = parse_legal_entity_backing_artifact(
                snapshot.raw_content,
                parser_version=assertion.parser_version,
            )
            recomputed_norm_hash = compute_normalized_evidence_hash(parsed_data)

        elif expected_component_role == "CONTRACT_SPEC":
            parsed_data = parse_contract_spec_backing_artifact(
                snapshot.raw_content,
                expected_symbol=expected_symbol,
                parser_version=assertion.parser_version,
            )
            recomputed_norm_hash = compute_normalized_evidence_hash(parsed_data)

        elif expected_component_role == "COMMISSION":
            parsed_data = parse_commission_backing_artifact(
                snapshot.raw_content,
                expected_symbol=expected_symbol,
                expected_account_tier=expected_account_tier,
                parser_version=assertion.parser_version,
            )
            recomputed_norm_hash = compute_normalized_evidence_hash(parsed_data)

        elif expected_component_role == "FINANCING":
            parsed_data = parse_financing_backing_artifact(
                snapshot.raw_content,
                expected_symbol=expected_symbol,
                parser_version=assertion.parser_version,
            )
            recomputed_norm_hash = compute_normalized_evidence_hash(parsed_data)

        elif expected_component_role == "SPREAD_DATASET":
            ticks_data, summary = parse_mt5_tick_export(
                snapshot.raw_content,
                expected_symbol=expected_symbol,
            )
            norm_rows = [
                f"{t['timestamp'].astimezone(timezone.utc).isoformat()}|{t['bid']}|{t['ask']}|{t.get('spread_bps', '')}"
                for t in ticks_data
            ]
            raw_ds_sha = hashlib.sha256("\n".join(norm_rows).encode("utf-8")).hexdigest()
            parsed_data = {
                "raw_dataset_sha256": raw_ds_sha,
                "sample_count": len(ticks_data),
                "symbol": summary["symbol"],
            }
            recomputed_norm_hash = compute_normalized_evidence_hash({"raw_dataset_sha256": raw_ds_sha})

        elif expected_component_role == "SLIPPAGE_DATASET":
            telemetry_records, summary = parse_mt5_execution_telemetry(
                snapshot.raw_content,
                expected_venue=expected_venue,
                expected_symbol=expected_symbol,
                expected_account_tier=expected_account_tier,
            )
            norm_rows = [
                f"{r['side']}|{r['order_type']}|{r['reference_bid']}|{r['reference_ask']}|{r['executed_fill_price']}|{r['signed_slippage_bps']}|{r['volume_lots']}|{r['latency_ms']}"
                for r in telemetry_records
            ]
            raw_ds_sha = hashlib.sha256("\n".join(norm_rows).encode("utf-8")).hexdigest()
            parsed_data = {
                "raw_dataset_sha256": raw_ds_sha,
                "sample_count": len(telemetry_records),
            }
            recomputed_norm_hash = compute_normalized_evidence_hash({"raw_dataset_sha256": raw_ds_sha})
        else:
            reasons.append(f"Unknown component role '{expected_component_role}'.")
            return False, reasons, None

    except Exception as exc:
        reasons.append(f"Independent parser recomputation failed on raw artifact: {exc}")
        return False, reasons, None

    expected_hash = (parsed_data.get("normalized_evidence_hash") if parsed_data else None) or recomputed_norm_hash
    if assertion.normalized_evidence_hash != expected_hash:
        reasons.append(
            f"Assertion normalized_evidence_hash '{assertion.normalized_evidence_hash}' does not match independently recomputed hash '{expected_hash}'."
        )

    # 9. Match parsed fields against model_version fields if model_version is provided
    if model_version is not None:
        if expected_component_role == "LEGAL_ENTITY":
            if model_version.legal_entity_name and parsed_data.get("legal_entity_name") != model_version.legal_entity_name:
                reasons.append(
                    f"Parsed legal_entity_name '{parsed_data.get('legal_entity_name')}' does not match model '{model_version.legal_entity_name}'."
                )
            if model_version.legal_entity_code and parsed_data.get("legal_entity_code") != model_version.legal_entity_code:
                reasons.append(
                    f"Parsed legal_entity_code '{parsed_data.get('legal_entity_code')}' does not match model '{model_version.legal_entity_code}'."
                )
            if model_version.regulator and parsed_data.get("regulator") != model_version.regulator:
                reasons.append(
                    f"Parsed regulator '{parsed_data.get('regulator')}' does not match model '{model_version.regulator}'."
                )
            if model_version.license_number and parsed_data.get("license_number") != model_version.license_number:
                reasons.append(
                    f"Parsed license_number '{parsed_data.get('license_number')}' does not match model '{model_version.license_number}'."
                )

        elif expected_component_role == "CONTRACT_SPEC":
            geom_checks = [
                ("digits", model_version.digits, int(parsed_data["digits"])),
                ("point_size", model_version.point_size, Decimal(str(parsed_data["point_size"]))),
                ("trade_tick_size", model_version.trade_tick_size, Decimal(str(parsed_data["trade_tick_size"]))),
                ("trade_tick_value", model_version.trade_tick_value, Decimal(str(parsed_data["trade_tick_value"]))),
                ("contract_size", model_version.contract_size, Decimal(str(parsed_data["contract_size"]))),
                ("volume_min", model_version.volume_min, Decimal(str(parsed_data["volume_min"]))),
                ("volume_max", model_version.volume_max, Decimal(str(parsed_data["volume_max"]))),
                ("volume_step", model_version.volume_step, Decimal(str(parsed_data["volume_step"]))),
            ]
            for f_name, model_val, parsed_val in geom_checks:
                if model_val is not None and model_val != parsed_val:
                    reasons.append(
                        f"Parsed {f_name} '{parsed_val}' does not match model '{model_val}'."
                    )

        elif expected_component_role == "COMMISSION":
            if model_version.native_commission_usd_per_lot_per_side is not None:
                parsed_comm = Decimal(str(parsed_data["native_commission_usd_per_lot_per_side"]))
                if model_version.native_commission_usd_per_lot_per_side != parsed_comm:
                    reasons.append(
                        f"Parsed commission '{parsed_comm}' does not match model '{model_version.native_commission_usd_per_lot_per_side}'."
                    )
            if model_version.commission_formula and parsed_data.get("commission_formula") != model_version.commission_formula:
                reasons.append(
                    f"Parsed commission_formula '{parsed_data.get('commission_formula')}' does not match model '{model_version.commission_formula}'."
                )

        elif expected_component_role == "FINANCING":
            if model_version.swap_long_points is not None:
                if Decimal(str(parsed_data["swap_long_points"])) != model_version.swap_long_points:
                    reasons.append(
                        f"Parsed swap_long_points '{parsed_data['swap_long_points']}' does not match model '{model_version.swap_long_points}'."
                    )
            if model_version.swap_short_points is not None:
                if Decimal(str(parsed_data["swap_short_points"])) != model_version.swap_short_points:
                    reasons.append(
                        f"Parsed swap_short_points '{parsed_data['swap_short_points']}' does not match model '{model_version.swap_short_points}'."
                    )
            if model_version.rollover_summer_utc_hour is not None:
                if int(parsed_data["rollover_summer_utc_hour"]) != model_version.rollover_summer_utc_hour:
                    reasons.append(
                        f"Parsed rollover_summer_utc_hour '{parsed_data['rollover_summer_utc_hour']}' does not match model '{model_version.rollover_summer_utc_hour}'."
                    )
            if model_version.rollover_winter_utc_hour is not None:
                if int(parsed_data["rollover_winter_utc_hour"]) != model_version.rollover_winter_utc_hour:
                    reasons.append(
                        f"Parsed rollover_winter_utc_hour '{parsed_data['rollover_winter_utc_hour']}' does not match model '{model_version.rollover_winter_utc_hour}'."
                    )
            if model_version.triple_swap_weekday is not None:
                if str(parsed_data["triple_swap_weekday"]).upper() != str(model_version.triple_swap_weekday).upper():
                    reasons.append(
                        f"Parsed triple_swap_weekday '{parsed_data['triple_swap_weekday']}' does not match model '{model_version.triple_swap_weekday}'."
                    )
            if model_version.actual_account_swap_free_status is not None and parsed_data.get("actual_account_swap_free_status") is not None:
                if parsed_data.get("actual_account_swap_free_status") != model_version.actual_account_swap_free_status:
                    reasons.append(
                        f"Parsed actual_account_swap_free_status '{parsed_data.get('actual_account_swap_free_status')}' does not match model '{model_version.actual_account_swap_free_status}'."
                    )
            if model_version.swap_free_available_for_account_type is not None and parsed_data.get("swap_free_available_for_account_type") is not None:
                if parsed_data.get("swap_free_available_for_account_type") != model_version.swap_free_available_for_account_type:
                    reasons.append(
                        f"Parsed swap_free_available_for_account_type '{parsed_data.get('swap_free_available_for_account_type')}' does not match model '{model_version.swap_free_available_for_account_type}'."
                    )

    # 10. Immutable Provenance Attestation verification (Directive 2, 3, 4, 7)
    # Hard-gate qualification requires PARSER VALID + RAW SHA VALID + PROVENANCE ATTESTATION VALID = QUALIFIED.
    linked_att = assertion.provenance_attestation
    if linked_att is None:
        linked_att = FrictionSourceProvenanceAttestation.objects.filter(
            source_snapshot_id=snapshot.snapshot_id,
            component_role=expected_component_role,
            raw_artifact_sha256=assertion.raw_artifact_sha256,
        ).first()

    if linked_att is None:
        reasons.append(
            f"Assertion lacks a valid FrictionSourceProvenanceAttestation for role '{expected_component_role}' and raw SHA '{assertion.raw_artifact_sha256}'."
        )
    else:
        if linked_att.attestation_status != FrictionAttestationStatus.VERIFIED.value:
            reasons.append(
                f"Attestation status is '{linked_att.attestation_status}'; only independently VERIFIED attestations may satisfy hard qualification."
            )
        is_auth, auth_err = verify_attestation_authenticity(linked_att)
        if not is_auth:
            reasons.append(
                f"Attestation authenticity verification failed: {auth_err}"
            )
        if linked_att.source_snapshot_id != snapshot.snapshot_id:
            reasons.append(
                f"Attestation source_snapshot '{linked_att.source_snapshot_id}' does not match snapshot '{snapshot.snapshot_id}'."
            )
        if linked_att.component_role != expected_component_role:
            reasons.append(
                f"Attestation component_role '{linked_att.component_role}' does not match expected role '{expected_component_role}'."
            )
        if linked_att.raw_artifact_sha256 != assertion.raw_artifact_sha256:
            reasons.append(
                f"Attestation raw_artifact_sha256 '{linked_att.raw_artifact_sha256}' does not match assertion raw SHA '{assertion.raw_artifact_sha256}'."
            )
        if linked_att.raw_artifact_sha256 != snapshot.raw_payload_bytes_sha256:
            reasons.append(
                f"Attestation raw_artifact_sha256 '{linked_att.raw_artifact_sha256}' does not match snapshot raw SHA '{snapshot.raw_payload_bytes_sha256}'."
            )
        if snapshot.raw_content:
            computed_raw_sha = hashlib.sha256(snapshot.raw_content).hexdigest()
            if linked_att.raw_artifact_sha256 != computed_raw_sha:
                reasons.append(
                    f"Attestation raw_artifact_sha256 '{linked_att.raw_artifact_sha256}' does not match computed SHA of snapshot content '{computed_raw_sha}'."
                )
        if linked_att.verification_method not in ACCEPTED_VERIFICATION_METHODS:
            reasons.append(
                f"Attestation verification_method '{linked_att.verification_method}' is not an accepted method: {sorted(ACCEPTED_VERIFICATION_METHODS)}."
            )
        if not linked_att.verifier_identity or not str(linked_att.verifier_identity).strip():
            reasons.append("Attestation verifier_identity is empty.")
        if linked_att.venue and expected_venue and linked_att.venue.upper() != expected_venue.upper():
            reasons.append(
                f"Attestation venue '{linked_att.venue}' does not match expected venue '{expected_venue}'."
            )
        if linked_att.symbol and expected_symbol and linked_att.symbol.upper() != expected_symbol.upper():
            reasons.append(
                f"Attestation symbol '{linked_att.symbol}' does not match expected symbol '{expected_symbol}'."
            )
        if linked_att.account_tier and expected_account_tier and linked_att.account_tier.upper() != expected_account_tier.upper():
            reasons.append(
                f"Attestation account_tier '{linked_att.account_tier}' does not match expected account tier '{expected_account_tier}'."
            )

    return len(reasons) == 0, reasons, parsed_data


def _get_assertion_meta(
    snapshot: Optional[FrictionSourceSnapshot],
    role: str,
    model_version: Optional[FrictionModelVersion] = None,
) -> Tuple[str, str, str, str, str]:
    if not snapshot:
        return "", "", "", "", ""
    assertion = snapshot.qualification_assertions.filter(
        component_role=role,
        qualification_status=FrictionQualificationStatus.QUALIFIED.value,
    ).order_by("-asserted_at").first()
    if assertion:
        is_valid, _, _ = validate_source_qualification_assertion(
            snapshot=snapshot,
            assertion=assertion,
            expected_component_role=role,
            model_version=model_version,
            expected_symbol=model_version.symbol if model_version else "XAUUSD",
            expected_account_tier=model_version.account_tier if model_version else "STANDARD",
            expected_venue=model_version.venue if model_version else "EXNESS",
        )
        if is_valid:
            att = assertion.provenance_attestation
            att_id = att.attestation_id if att else ""
            v_method = att.verification_method if att else ""
            return assertion.parser_name, assertion.parser_version, assertion.normalized_evidence_hash, att_id, v_method
    return "", "", "", "", ""


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
    if legal_snap.source_type not in QUALIFIED_LEGAL_ENTITY_SOURCE_TYPES:
        return FrictionValidationResult(
            is_valid=False,
            status="EMPIRICAL_FRICTION_INVALID",
            reasons=[f"Legal entity source provenance '{legal_snap.source_type}' is unverified; hard readiness requires qualified legal entity provenance."],
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
    legal_assertion = legal_snap.qualification_assertions.filter(
        component_role="LEGAL_ENTITY",
    ).order_by("-asserted_at").first()
    is_valid_assert, assert_errors, _ = validate_source_qualification_assertion(
        snapshot=legal_snap,
        assertion=legal_assertion,
        expected_component_role="LEGAL_ENTITY",
        expected_parser="parse_legal_entity_backing_artifact",
        model_version=model_version,
    )
    if not is_valid_assert:
        return FrictionValidationResult(
            is_valid=False,
            status="LEGAL_ENTITY_EVIDENCE_MISSING",
            reasons=[f"Legal entity snapshot lacks a valid authoritative qualification assertion: {'; '.join(assert_errors)}"],
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
    if contract_snap.source_type not in QUALIFIED_CONTRACT_SOURCE_TYPES:
        return FrictionValidationResult(
            is_valid=False,
            status="EMPIRICAL_FRICTION_INVALID",
            reasons=[f"Contract specification source provenance '{contract_snap.source_type}' is unverified; hard readiness requires qualified contract provenance."],
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
    contract_assertion = contract_snap.qualification_assertions.filter(
        component_role="CONTRACT_SPEC",
    ).order_by("-asserted_at").first()
    is_valid_assert, assert_errors, _ = validate_source_qualification_assertion(
        snapshot=contract_snap,
        assertion=contract_assertion,
        expected_component_role="CONTRACT_SPEC",
        expected_parser="parse_contract_spec_backing_artifact",
        model_version=model_version,
        expected_symbol=model_version.symbol,
    )
    if not is_valid_assert:
        return FrictionValidationResult(
            is_valid=False,
            status="CONTRACT_SPEC_EVIDENCE_MISSING",
            reasons=[f"Contract specification snapshot lacks a valid authoritative qualification assertion: {'; '.join(assert_errors)}"],
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
    if fee_snap.source_type not in QUALIFIED_COMMISSION_SOURCE_TYPES:
        return FrictionValidationResult(
            is_valid=False,
            status="EMPIRICAL_FRICTION_INVALID",
            reasons=[f"Fee schedule source provenance '{fee_snap.source_type}' is unverified; hard readiness requires qualified commission provenance."],
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
    fee_assertion = fee_snap.qualification_assertions.filter(
        component_role="COMMISSION",
    ).order_by("-asserted_at").first()
    is_valid_assert, assert_errors, _ = validate_source_qualification_assertion(
        snapshot=fee_snap,
        assertion=fee_assertion,
        expected_component_role="COMMISSION",
        expected_parser="parse_commission_backing_artifact",
        model_version=model_version,
        expected_symbol=model_version.symbol,
        expected_account_tier=model_version.account_tier,
    )
    if not is_valid_assert:
        return FrictionValidationResult(
            is_valid=False,
            status="COMMISSION_EVIDENCE_MISSING",
            reasons=[f"Fee schedule snapshot lacks a valid authoritative qualification assertion: {'; '.join(assert_errors)}"],
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
    if swap_snap.source_type not in QUALIFIED_FINANCING_SOURCE_TYPES:
        return FrictionValidationResult(
            is_valid=False,
            status="EMPIRICAL_FRICTION_INVALID",
            reasons=[f"Swap specification source provenance '{swap_snap.source_type}' is unverified; hard readiness requires qualified financing provenance."],
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
    swap_assertion = swap_snap.qualification_assertions.filter(
        component_role="FINANCING",
    ).order_by("-asserted_at").first()
    is_valid_assert, assert_errors, _ = validate_source_qualification_assertion(
        snapshot=swap_snap,
        assertion=swap_assertion,
        expected_component_role="FINANCING",
        expected_parser="parse_financing_backing_artifact",
        model_version=model_version,
        expected_symbol=model_version.symbol,
    )
    if not is_valid_assert:
        return FrictionValidationResult(
            is_valid=False,
            status="FINANCING_EVIDENCE_MISSING",
            reasons=[f"Financing specification snapshot lacks a valid authoritative qualification assertion: {'; '.join(assert_errors)}"],
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
    spread_snap = spread_ds.source_snapshot
    if not spread_snap:
        return FrictionValidationResult(
            is_valid=False,
            status="SPREAD_EMPIRICAL_EVIDENCE_MISSING",
            reasons=["Spread dataset source snapshot is missing."],
            details=details,
        )
    if spread_snap.source_type not in QUALIFIED_SPREAD_SOURCE_TYPES:
        return FrictionValidationResult(
            is_valid=False,
            status="SPREAD_EMPIRICAL_EVIDENCE_INVALID",
            reasons=[f"Spread dataset source provenance '{spread_snap.source_type}' is unverified; hard readiness requires qualified spread provenance."],
            details=details,
        )
    if not spread_snap.raw_content or hashlib.sha256(spread_snap.raw_content).hexdigest() != spread_snap.raw_payload_bytes_sha256:
        return FrictionValidationResult(
            is_valid=False,
            status="SPREAD_EMPIRICAL_EVIDENCE_MISSING",
            reasons=["Spread dataset source payload content or SHA-256 verification failed."],
            details=details,
        )
    if spread_snap.venue != model_version.venue or spread_snap.symbol != model_version.symbol or spread_snap.account_tier != model_version.account_tier:
        return FrictionValidationResult(
            is_valid=False,
            status="EMPIRICAL_FRICTION_INVALID",
            reasons=["Spread dataset source snapshot scope does not match model scope."],
            details=details,
        )
    spread_assertion = spread_snap.qualification_assertions.filter(
        component_role="SPREAD_DATASET",
    ).order_by("-asserted_at").first()
    is_valid_assert, assert_errors, _ = validate_source_qualification_assertion(
        snapshot=spread_snap,
        assertion=spread_assertion,
        expected_component_role="SPREAD_DATASET",
        expected_parser="parse_mt5_tick_export",
        model_version=model_version,
        expected_symbol=model_version.symbol,
    )
    if not is_valid_assert:
        return FrictionValidationResult(
            is_valid=False,
            status="SPREAD_EMPIRICAL_EVIDENCE_INVALID",
            reasons=[f"Spread dataset source snapshot lacks a valid authoritative qualification assertion: {'; '.join(assert_errors)}"],
            details=details,
        )

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
    spread_pop = getattr(spread_sum, "population_semantics", None)
    if spread_pop != FrictionPopulationSemantics.SPREAD_BPS.value:
        return FrictionValidationResult(
            is_valid=False,
            status="SPREAD_EMPIRICAL_EVIDENCE_INVALID",
            reasons=[f"Spread distribution summary population semantics is '{spread_pop}', expected '{FrictionPopulationSemantics.SPREAD_BPS.value}'."],
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

    # 7. Slippage Telemetry & Summary Sufficiency (Directives 7, 8, 10, 11, 12)
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
    telem_snap = telem_ds.source_snapshot
    if not telem_snap:
        return FrictionValidationResult(
            is_valid=False,
            status="SLIPPAGE_EMPIRICAL_EVIDENCE_MISSING",
            reasons=["Execution slippage telemetry dataset source snapshot is missing."],
            details=details,
        )
    if telem_snap.source_type not in QUALIFIED_SLIPPAGE_SOURCE_TYPES:
        return FrictionValidationResult(
            is_valid=False,
            status="SLIPPAGE_EMPIRICAL_EVIDENCE_INVALID",
            reasons=[f"Execution slippage telemetry source provenance '{telem_snap.source_type}' is unverified; hard readiness requires qualified telemetry provenance."],
            details=details,
        )
    if not telem_snap.raw_content or hashlib.sha256(telem_snap.raw_content).hexdigest() != telem_snap.raw_payload_bytes_sha256:
        return FrictionValidationResult(
            is_valid=False,
            status="SLIPPAGE_EMPIRICAL_EVIDENCE_MISSING",
            reasons=["Execution slippage telemetry payload content or SHA-256 verification failed."],
            details=details,
        )
    if telem_snap.venue != model_version.venue or telem_snap.symbol != model_version.symbol or telem_snap.account_tier != model_version.account_tier:
        return FrictionValidationResult(
            is_valid=False,
            status="EMPIRICAL_FRICTION_INVALID",
            reasons=["Execution slippage telemetry source snapshot scope does not match model scope."],
            details=details,
        )
    telem_assertion = telem_snap.qualification_assertions.filter(
        component_role="SLIPPAGE_DATASET",
    ).order_by("-asserted_at").first()
    is_valid_assert, assert_errors, _ = validate_source_qualification_assertion(
        snapshot=telem_snap,
        assertion=telem_assertion,
        expected_component_role="SLIPPAGE_DATASET",
        expected_parser="parse_mt5_execution_telemetry",
        model_version=model_version,
        expected_symbol=model_version.symbol,
        expected_account_tier=model_version.account_tier,
        expected_venue=model_version.venue,
    )
    if not is_valid_assert:
        return FrictionValidationResult(
            is_valid=False,
            status="SLIPPAGE_EMPIRICAL_EVIDENCE_INVALID",
            reasons=[f"Execution slippage telemetry source snapshot lacks a valid authoritative qualification assertion: {'; '.join(assert_errors)}"],
            details=details,
        )

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

    # Validate slippage cost policy version against distribution summary population semantics (Directive 12)
    model_policy = getattr(model_version, "slippage_cost_policy_version", slippage_cost_policy_version)
    slip_pop = getattr(slip_sum, "population_semantics", None)

    if slip_pop in (FrictionPopulationSemantics.UNKNOWN.value, None, "", "LEGACY_UNSPECIFIED"):
        return FrictionValidationResult(
            is_valid=False,
            status="SLIPPAGE_EMPIRICAL_EVIDENCE_INVALID",
            reasons=["Execution slippage distribution summary has UNKNOWN or unspecified population semantics."],
            details=details,
        )

    if model_policy == "ADVERSE_ONLY_P75_P95_V1":
        if slip_pop != FrictionPopulationSemantics.SLIPPAGE_ADVERSE_ONLY.value:
            return FrictionValidationResult(
                is_valid=False,
                status="SLIPPAGE_EMPIRICAL_EVIDENCE_INVALID",
                reasons=[f"Slippage policy 'ADVERSE_ONLY_P75_P95_V1' requires SLIPPAGE_ADVERSE_ONLY summary, got '{slip_pop}'."],
                details=details,
            )
    elif model_policy == "RAW_SIGNED_DISTRIBUTION_V1":
        if slip_pop != FrictionPopulationSemantics.SLIPPAGE_SIGNED.value:
            return FrictionValidationResult(
                is_valid=False,
                status="SLIPPAGE_EMPIRICAL_EVIDENCE_INVALID",
                reasons=[f"Slippage policy 'RAW_SIGNED_DISTRIBUTION_V1' requires SLIPPAGE_SIGNED summary, got '{slip_pop}'."],
                details=details,
            )
    else:
        return FrictionValidationResult(
            is_valid=False,
            status="SLIPPAGE_EMPIRICAL_EVIDENCE_INVALID",
            reasons=[f"Unknown or unsupported slippage cost policy '{model_policy}'."],
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
    for db in dataset_bindings:
        if db.evidence_dataset.source_snapshot and db.evidence_dataset.source_snapshot.source_type not in source_types:
            source_types.append(db.evidence_dataset.source_snapshot.source_type)

    source_evidence: Dict[str, Dict[str, str]] = {}
    if model_version.legal_entity_source_snapshot:
        p_name, p_ver, norm_hash, att_id, v_method = _get_assertion_meta(model_version.legal_entity_source_snapshot, "LEGAL_ENTITY", model_version=model_version)
        if not norm_hash:
            return FrictionValidationResult(
                is_valid=False,
                status="LEGAL_ENTITY_EVIDENCE_MISSING",
                reasons=["Legal entity assertion failed integrity validation for fingerprint binding."],
                details=details,
            )
        source_evidence["LEGAL_ENTITY"] = {
            "sha256": model_version.legal_entity_source_snapshot.raw_payload_bytes_sha256,
            "source_type": model_version.legal_entity_source_snapshot.source_type,
            "parser_name": p_name,
            "parser_version": p_ver,
            "normalized_evidence_hash": norm_hash,
            "attestation_id": att_id,
            "verification_method": v_method,
        }
    if model_version.contract_spec_source_snapshot:
        p_name, p_ver, norm_hash, att_id, v_method = _get_assertion_meta(model_version.contract_spec_source_snapshot, "CONTRACT_SPEC", model_version=model_version)
        if not norm_hash:
            return FrictionValidationResult(
                is_valid=False,
                status="CONTRACT_SPEC_EVIDENCE_MISSING",
                reasons=["Contract specification assertion failed integrity validation for fingerprint binding."],
                details=details,
            )
        source_evidence["CONTRACT_SPEC"] = {
            "sha256": model_version.contract_spec_source_snapshot.raw_payload_bytes_sha256,
            "source_type": model_version.contract_spec_source_snapshot.source_type,
            "parser_name": p_name,
            "parser_version": p_ver,
            "normalized_evidence_hash": norm_hash,
            "attestation_id": att_id,
            "verification_method": v_method,
        }
    if model_version.fee_schedule_source_snapshot:
        p_name, p_ver, norm_hash, att_id, v_method = _get_assertion_meta(model_version.fee_schedule_source_snapshot, "COMMISSION", model_version=model_version)
        if not norm_hash:
            return FrictionValidationResult(
                is_valid=False,
                status="COMMISSION_EVIDENCE_MISSING",
                reasons=["Fee schedule assertion failed integrity validation for fingerprint binding."],
                details=details,
            )
        source_evidence["COMMISSION"] = {
            "sha256": model_version.fee_schedule_source_snapshot.raw_payload_bytes_sha256,
            "source_type": model_version.fee_schedule_source_snapshot.source_type,
            "parser_name": p_name,
            "parser_version": p_ver,
            "normalized_evidence_hash": norm_hash,
            "attestation_id": att_id,
            "verification_method": v_method,
        }
    if model_version.swap_spec_source_snapshot:
        p_name, p_ver, norm_hash, att_id, v_method = _get_assertion_meta(model_version.swap_spec_source_snapshot, "FINANCING", model_version=model_version)
        if not norm_hash:
            return FrictionValidationResult(
                is_valid=False,
                status="FINANCING_EVIDENCE_MISSING",
                reasons=["Financing assertion failed integrity validation for fingerprint binding."],
                details=details,
            )
        source_evidence["FINANCING"] = {
            "sha256": model_version.swap_spec_source_snapshot.raw_payload_bytes_sha256,
            "source_type": model_version.swap_spec_source_snapshot.source_type,
            "parser_name": p_name,
            "parser_version": p_ver,
            "normalized_evidence_hash": norm_hash,
            "attestation_id": att_id,
            "verification_method": v_method,
        }
    for db in dataset_bindings:
        if db.binding_role == FrictionBindingRole.PRIMARY_SPREAD_SAMPLE and db.evidence_dataset.source_snapshot:
            p_name, p_ver, norm_hash, att_id, v_method = _get_assertion_meta(db.evidence_dataset.source_snapshot, "SPREAD_DATASET", model_version=model_version)
            if not norm_hash:
                return FrictionValidationResult(
                    is_valid=False,
                    status="SPREAD_EMPIRICAL_EVIDENCE_INVALID",
                    reasons=["Spread dataset assertion failed integrity validation for fingerprint binding."],
                    details=details,
                )
            source_evidence["SPREAD_DATASET"] = {
                "sha256": db.evidence_dataset.source_snapshot.raw_payload_bytes_sha256,
                "source_type": db.evidence_dataset.source_snapshot.source_type,
                "parser_name": p_name,
                "parser_version": p_ver,
                "normalized_evidence_hash": norm_hash,
                "attestation_id": att_id,
                "verification_method": v_method,
            }
        elif db.binding_role in (FrictionBindingRole.PRIMARY_TELEMETRY_SAMPLE, FrictionBindingRole.TELEMETRY_SAMPLE) and db.evidence_dataset.source_snapshot:
            p_name, p_ver, norm_hash, att_id, v_method = _get_assertion_meta(db.evidence_dataset.source_snapshot, "SLIPPAGE_DATASET", model_version=model_version)
            if not norm_hash:
                return FrictionValidationResult(
                    is_valid=False,
                    status="SLIPPAGE_EMPIRICAL_EVIDENCE_INVALID",
                    reasons=["Execution slippage telemetry assertion failed integrity validation for fingerprint binding."],
                    details=details,
                )
            source_evidence["SLIPPAGE_DATASET"] = {
                "sha256": db.evidence_dataset.source_snapshot.raw_payload_bytes_sha256,
                "source_type": db.evidence_dataset.source_snapshot.source_type,
                "parser_name": p_name,
                "parser_version": p_ver,
                "normalized_evidence_hash": norm_hash,
                "attestation_id": att_id,
                "verification_method": v_method,
            }

    dataset_hashes = [db.evidence_dataset.raw_dataset_sha256 for db in dataset_bindings]
    bound_roles = [db.binding_role for db in dataset_bindings] + [sb.binding_role for sb in summary_bindings]

    summaries_dict = [
        {
            "component_type": sb.distribution_summary.component_type,
            "condition": sb.distribution_summary.condition,
            "session": sb.distribution_summary.session,
            "unit": sb.distribution_summary.unit,
            "population_semantics": getattr(sb.distribution_summary, "population_semantics", ""),
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
            "parser_version": getattr(model_version, "parser_version", "1.0.0"),
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
        source_evidence=source_evidence,
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
        slippage_cost_policy_version=getattr(model_version, "slippage_cost_policy_version", slippage_cost_policy_version),
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
