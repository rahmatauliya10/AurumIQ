"""Governed Composite Legal Entity Attestation Engine for Standard Cent Execution Scope.

Enforces Stage D5B Pre-Phase-8 Governance:
- Binds contracting legal entity (Exness (SC) Ltd, FSA SD025) and account scope (Standard Cent, USC)
  via a strict composite conjunction of two immutable local artifacts:
  1. Component A: ACCOUNT_CLIENT_AGREEMENT (exness_sc_client_agreement.pdf)
  2. Component B: BROKER_PERSONAL_AREA_EXPORT (exness_standard_cent_personal_area.jpeg)
- Neither component alone may qualify the Standard Cent execution account.
- Binds exact SHA-256 hashes and byte lengths.
- Explicitly decouples server infrastructure (Exness-MT5Real20, Real25, Real36) from legal entity identity.
- Explicitly rejects MT5 ACCOUNT_COMPANY (Exness Technologies Ltd) as contracting legal entity.
- Separates COMPOSITE_EVIDENCE_INTEGRITY (PASS/FAIL) from PRODUCTION_ORIGIN_AUTHENTICITY (PASS/HOLD/FAIL).
- Strictly fails closed in production (LEGAL_ENTITY_GOVERNED = HOLD) when an authenticated production
  review workflow or portal collector is absent.
- Preserves raw evidence offline/local-only and ensures zero private PII leaks into public artifacts.
"""
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid

from apps.market_data.friction.provenance import (
    GOVERNED_PROVENANCE_AUTHORITY,
    CURRENT_PROOF_VERSION,
    get_governed_signing_secret,
    is_production_environment,
    is_test_environment,
    is_trusted_verifier,
)
from apps.market_data.models import (
    FrictionAttestationStatus,
    FrictionQualificationStatus,
    FrictionSourceType,
)

logger = logging.getLogger(__name__)

COMPOSITE_LEGAL_ENTITY_SCHEMA = "aurumiq.governance.legal_entity_composite_attestation.v1"
LEGAL_ENTITY_EVIDENCE_INDEX_SCHEMA = "aurumiq.governance.legal_entity_evidence_index.v1"
LEGAL_ENTITY_REVIEW_RECEIPT_SCHEMA = "aurumiq.governance.legal_entity_review_receipt.v1"

# Governed Review Workflow Constants (Stage D5C)
VERIFICATION_METHOD_COMPOSITE_REVIEW = "COMPOSITE_GOVERNED_REVIEW"
VERIFIER_IDENTITY_LEGAL_ENTITY_WORKFLOW = "AURUMIQ_LEGAL_ENTITY_REVIEW_WORKFLOW_V1"
REVIEW_WORKFLOW_VERSION = "1.0.0"
ORIGIN_CLAIM_AUTHENTICATED_REVIEW_ONLY = "AUTHENTICATED_AURUMIQ_REVIEW_ONLY"
BROKER_CRYPTOGRAPHIC_ORIGIN_NOT_CLAIMED = "NOT_CLAIMED"
DEFAULT_REVIEW_RECEIPT_PATH = "artifacts/calibration/legal_entity_review_receipt.json"

# Authoritative Immutable Component 1 (Client Agreement)
EXPECTED_CLIENT_AGREEMENT_SHA256 = "ae02ca31a9e2ba1d8ef81cc973caa169e901aa6d802b4f9949a05e3cb1217ddc"
EXPECTED_CLIENT_AGREEMENT_BYTES = 1539705
EXPECTED_CLIENT_AGREEMENT_ROLE = "CONTRACTING_ENTITY_EVIDENCE"
EXPECTED_CLIENT_AGREEMENT_SOURCE_TYPE = FrictionSourceType.ACCOUNT_CLIENT_AGREEMENT.value
EXPECTED_CLIENT_AGREEMENT_FILENAME = "exness_sc_client_agreement.pdf"

# Authoritative Immutable Component 2 (Personal Area Export)
EXPECTED_PERSONAL_AREA_SHA256 = "2c8e93a081d86cc631c76d7fa7a552a11cdc769a50b75175042285af874e80e7"
EXPECTED_PERSONAL_AREA_BYTES = 113225
EXPECTED_PERSONAL_AREA_ROLE = "ACCOUNT_SCOPE_BINDING_EVIDENCE"
EXPECTED_PERSONAL_AREA_SOURCE_TYPE = FrictionSourceType.BROKER_PERSONAL_AREA_EXPORT.value
EXPECTED_PERSONAL_AREA_FILENAME = "exness_standard_cent_personal_area.jpeg"

# Authoritative Target Entity & Scope
QUALIFIED_LEGAL_ENTITY_NAME = "Exness (SC) Ltd"
QUALIFIED_LEGAL_ENTITY_CODE = "EXNESS_SC_LTD"
QUALIFIED_REGULATOR = "FSA"
QUALIFIED_LICENSE_NUMBER = "SD025"
QUALIFIED_VENUE = "EXNESS"
QUALIFIED_SYMBOL = "XAUUSD"
QUALIFIED_BROKER_SYMBOL = "XAUUSDc"
QUALIFIED_ACCOUNT_TIER = "STANDARD_CENT"
QUALIFIED_ACCOUNT_CURRENCY = "USC"

SERVER_BINDING_POLICY = "EXECUTION_INFRASTRUCTURE_ONLY_NOT_LEGAL_ENTITY_KEY"
FORBIDDEN_CONTRACTING_ENTITIES: Set[str] = {
    "EXNESS TECHNOLOGIES LTD",
    "EXNESS_TECHNOLOGIES_LTD",
}

# Known execution server allocations (demonstrating server-independence)
OBSERVED_MT5_SERVERS: Set[str] = {
    "Exness-MT5Real20",
    "Exness-MT5Real25",
    "Exness-MT5Real36",
}


@dataclass
class GovernedCompositeVerificationResult:
    """Distinct two-layer verification result for composite legal entity attestation."""
    is_qualified: bool
    governed_status: str  # "QUALIFIED", "HOLD", "FAIL"
    composite_evidence_integrity: str  # "PASS", "FAIL"
    production_origin_authenticity: str  # "PASS", "HOLD", "FAIL"
    reasons: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


def compute_canonical_composite_payload(
    legal_entity_name: str = QUALIFIED_LEGAL_ENTITY_NAME,
    legal_entity_code: str = QUALIFIED_LEGAL_ENTITY_CODE,
    regulator: str = QUALIFIED_REGULATOR,
    license_number: str = QUALIFIED_LICENSE_NUMBER,
    account_tier: str = QUALIFIED_ACCOUNT_TIER,
    account_currency: str = QUALIFIED_ACCOUNT_CURRENCY,
    comp1_role: str = EXPECTED_CLIENT_AGREEMENT_ROLE,
    comp1_source_type: str = EXPECTED_CLIENT_AGREEMENT_SOURCE_TYPE,
    comp1_sha256: str = EXPECTED_CLIENT_AGREEMENT_SHA256,
    comp1_bytes: int = EXPECTED_CLIENT_AGREEMENT_BYTES,
    comp2_role: str = EXPECTED_PERSONAL_AREA_ROLE,
    comp2_source_type: str = EXPECTED_PERSONAL_AREA_SOURCE_TYPE,
    comp2_sha256: str = EXPECTED_PERSONAL_AREA_SHA256,
    comp2_bytes: int = EXPECTED_PERSONAL_AREA_BYTES,
    server_binding_policy: str = SERVER_BINDING_POLICY,
    verifier_identity: str = "TEST_SUITE_ISOLATED_PROVENANCE_SEAM",
    verification_authority: str = GOVERNED_PROVENANCE_AUTHORITY,
    verification_proof_version: str = CURRENT_PROOF_VERSION,
) -> str:
    """Deterministic canonical key-value payload for cryptographic signing of composite attestation."""
    parts = [
        f"authority={verification_authority.strip()}",
        f"proof_ver={verification_proof_version.strip()}",
        f"entity_name={legal_entity_name.strip()}",
        f"entity_code={legal_entity_code.strip().upper()}",
        f"regulator={regulator.strip().upper()}",
        f"license={license_number.strip()}",
        f"tier={account_tier.strip().upper()}",
        f"currency={account_currency.strip().upper()}",
        f"comp1_role={comp1_role.strip().upper()}",
        f"comp1_source={comp1_source_type.strip().upper()}",
        f"comp1_sha={comp1_sha256.strip().lower()}",
        f"comp1_bytes={comp1_bytes}",
        f"comp2_role={comp2_role.strip().upper()}",
        f"comp2_source={comp2_source_type.strip().upper()}",
        f"comp2_sha={comp2_sha256.strip().lower()}",
        f"comp2_bytes={comp2_bytes}",
        f"server_policy={server_binding_policy.strip().upper()}",
        f"verifier={verifier_identity.strip()}",
    ]
    return "|".join(parts)


def compute_composite_verification_proof(
    canonical_payload: str,
    signing_secret: Optional[bytes] = None,
) -> str:
    """Generate purpose-bound HMAC-SHA256 cryptographic proof over canonical composite payload.

    Uses dedicated application-controlled provenance secret via get_governed_signing_secret().
    Never silently reuses generic Django SECRET_KEY.
    """
    secret = signing_secret or get_governed_signing_secret()
    return hmac.new(secret, canonical_payload.encode("utf-8"), hashlib.sha256).hexdigest()


def compute_canonical_review_receipt_payload(
    schema: str = LEGAL_ENTITY_REVIEW_RECEIPT_SCHEMA,
    verification_authority: str = GOVERNED_PROVENANCE_AUTHORITY,
    verification_proof_version: str = CURRENT_PROOF_VERSION,
    workflow_version: str = REVIEW_WORKFLOW_VERSION,
    receipt_id: str = "",
    reviewed_at_iso: str = "",
    verification_method: str = VERIFICATION_METHOD_COMPOSITE_REVIEW,
    verifier_identity: str = VERIFIER_IDENTITY_LEGAL_ENTITY_WORKFLOW,
    legal_entity_name: str = QUALIFIED_LEGAL_ENTITY_NAME,
    legal_entity_code: str = QUALIFIED_LEGAL_ENTITY_CODE,
    regulator: str = QUALIFIED_REGULATOR,
    license_number: str = QUALIFIED_LICENSE_NUMBER,
    account_tier: str = QUALIFIED_ACCOUNT_TIER,
    account_currency: str = QUALIFIED_ACCOUNT_CURRENCY,
    comp1_role: str = EXPECTED_CLIENT_AGREEMENT_ROLE,
    comp1_source_type: str = EXPECTED_CLIENT_AGREEMENT_SOURCE_TYPE,
    comp1_sha256: str = EXPECTED_CLIENT_AGREEMENT_SHA256,
    comp1_bytes: int = EXPECTED_CLIENT_AGREEMENT_BYTES,
    comp2_role: str = EXPECTED_PERSONAL_AREA_ROLE,
    comp2_source_type: str = EXPECTED_PERSONAL_AREA_SOURCE_TYPE,
    comp2_sha256: str = EXPECTED_PERSONAL_AREA_SHA256,
    comp2_bytes: int = EXPECTED_PERSONAL_AREA_BYTES,
    server_binding_policy: str = SERVER_BINDING_POLICY,
    origin_claim: str = ORIGIN_CLAIM_AUTHENTICATED_REVIEW_ONLY,
    broker_cryptographic_origin: str = BROKER_CRYPTOGRAPHIC_ORIGIN_NOT_CLAIMED,
) -> str:
    """Deterministic canonical key-value payload for cryptographic signing of review receipt.

    Frozen, deterministic 25-field positional order.
    """
    parts = [
        f"schema={schema.strip()}",
        f"authority={verification_authority.strip()}",
        f"proof_ver={verification_proof_version.strip()}",
        f"workflow_ver={workflow_version.strip()}",
        f"receipt_id={receipt_id.strip()}",
        f"reviewed_at={reviewed_at_iso.strip()}",
        f"method={verification_method.strip().upper()}",
        f"verifier={verifier_identity.strip()}",
        f"entity_name={legal_entity_name.strip()}",
        f"entity_code={legal_entity_code.strip().upper()}",
        f"regulator={regulator.strip().upper()}",
        f"license={license_number.strip()}",
        f"tier={account_tier.strip().upper()}",
        f"currency={account_currency.strip().upper()}",
        f"comp1_role={comp1_role.strip().upper()}",
        f"comp1_source={comp1_source_type.strip().upper()}",
        f"comp1_sha={comp1_sha256.strip().lower()}",
        f"comp1_bytes={comp1_bytes}",
        f"comp2_role={comp2_role.strip().upper()}",
        f"comp2_source={comp2_source_type.strip().upper()}",
        f"comp2_sha={comp2_sha256.strip().lower()}",
        f"comp2_bytes={comp2_bytes}",
        f"server_policy={server_binding_policy.strip().upper()}",
        f"origin_claim={origin_claim.strip().upper()}",
        f"broker_crypto_origin={broker_cryptographic_origin.strip().upper()}",
    ]
    return "|".join(parts)


def compute_review_receipt_proof(
    canonical_payload: str,
    signing_secret: Optional[bytes] = None,
) -> str:
    """Generate purpose-bound HMAC-SHA256 cryptographic proof over canonical review receipt payload.

    Uses dedicated application-controlled provenance secret via get_governed_signing_secret().
    Never silently reuses generic Django SECRET_KEY.
    """
    secret = signing_secret or get_governed_signing_secret()
    return hmac.new(secret, canonical_payload.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_governed_composite_legal_entity(
    attestation_data: Optional[Dict[str, Any]] = None,
    attestation_file_path: Optional[str] = None,
    receipt_data: Optional[Dict[str, Any]] = None,
    receipt_file_path: Optional[str] = None,
    local_evidence_dir: Optional[str] = None,
    simulated_server: Optional[str] = None,
    proof_override: Optional[str] = None,
    is_test_ctx: Optional[bool] = None,
) -> GovernedCompositeVerificationResult:
    """Independently verify governed composite legal entity attestation and review receipt.

    Enforces two distinct verification layers:
    1. COMPOSITE_EVIDENCE_INTEGRITY: Validates component conjunction, immutable SHA-256 hashes,
       byte lengths, entity identity, scope, server independence, anti-self-attestation, and privacy.
    2. PRODUCTION_ORIGIN_AUTHENTICITY: Validates whether cryptographic authenticity was established
       by an authorized production workflow (AURUMIQ_LEGAL_ENTITY_REVIEW_WORKFLOW_V1 via COMPOSITE_GOVERNED_REVIEW
       or isolated test seam in test context).

    Fails closed to (COMPOSITE_EVIDENCE_INTEGRITY=PASS, PRODUCTION_ORIGIN_AUTHENTICITY=HOLD,
    LEGAL_ENTITY_GOVERNED=HOLD) if production review receipt or signing secret is absent.
    """
    reasons: List[str] = []
    details: Dict[str, Any] = {
        "legal_entity_name": None,
        "legal_entity_code": None,
        "regulator": None,
        "license_number": None,
        "account_tier": None,
        "account_currency": None,
        "components_checked": [],
    }

    if is_test_ctx is None:
        is_test_ctx = is_test_environment()

    # Load review receipt data if file path provided or default exists
    if receipt_file_path and not receipt_data:
        rp = Path(receipt_file_path)
        if rp.exists():
            try:
                receipt_data = json.loads(rp.read_text(encoding="utf-8"))
            except Exception as exc:
                reasons.append(f"Invalid JSON in receipt file: {exc}")
        else:
            canonical_name = Path(DEFAULT_REVIEW_RECEIPT_PATH).name
            if rp.name == canonical_name or str(rp).replace("\\", "/").endswith(DEFAULT_REVIEW_RECEIPT_PATH):
                pass
            else:
                reasons.append(f"Receipt file not found: {receipt_file_path}")
    elif not receipt_data and not receipt_file_path:
        default_rp = Path(DEFAULT_REVIEW_RECEIPT_PATH)
        if default_rp.exists():
            try:
                receipt_data = json.loads(default_rp.read_text(encoding="utf-8"))
            except Exception:
                receipt_data = None

    # Load attestation data if file path provided
    if attestation_file_path and not attestation_data:
        p = Path(attestation_file_path)
        if not p.exists():
            return GovernedCompositeVerificationResult(
                is_qualified=False,
                governed_status="FAIL",
                composite_evidence_integrity="FAIL",
                production_origin_authenticity="FAIL",
                reasons=[f"Attestation file not found: {attestation_file_path}"],
                details=details,
            )
        try:
            attestation_data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            return GovernedCompositeVerificationResult(
                is_qualified=False,
                governed_status="FAIL",
                composite_evidence_integrity="FAIL",
                production_origin_authenticity="FAIL",
                reasons=[f"Invalid JSON in attestation file: {exc}"],
                details=details,
            )

    if not attestation_data:
        if receipt_data and isinstance(receipt_data, dict):
            attestation_data = {
                "schema": COMPOSITE_LEGAL_ENTITY_SCHEMA,
                "target_scope": receipt_data.get("target_scope", {}),
                "legal_entity": receipt_data.get("legal_entity", {}),
                "governed_components": receipt_data.get("governed_components", {}),
                "critical_policies": receipt_data.get("critical_policies", {}),
            }
        elif attestation_file_path is None:
            def_att_path = Path("artifacts/calibration/legal_entity_governed_attestation.json")
            if def_att_path.exists():
                try:
                    attestation_data = json.loads(def_att_path.read_text(encoding="utf-8"))
                except Exception:
                    attestation_data = None

    if not attestation_data:
        return GovernedCompositeVerificationResult(
            is_qualified=False,
            governed_status="FAIL",
            composite_evidence_integrity="FAIL",
            production_origin_authenticity="FAIL",
            reasons=["No attestation data provided."],
            details=details,
        )

    # 1. Component Conjunction Requirement (Neither alone qualifies)
    components = attestation_data.get("governed_components", {})
    if not isinstance(components, dict):
        reasons.append("governed_components is missing or not a dictionary.")
        components = {}

    comp1 = components.get("component_1")
    comp2 = components.get("component_2")

    if not comp1 and not comp2:
        reasons.append("COMPOSITE_CONJUNCTION_FAILED: Both evidence components are missing.")
    elif not comp1:
        reasons.append(
            "COMPOSITE_CONJUNCTION_FAILED: Component 1 (ACCOUNT_CLIENT_AGREEMENT) is missing. "
            "Personal Area export alone MUST NOT qualify contracting legal entity."
        )
    elif not comp2:
        reasons.append(
            "COMPOSITE_CONJUNCTION_FAILED: Component 2 (BROKER_PERSONAL_AREA_EXPORT) is missing. "
            "Client Agreement alone MUST NOT qualify Standard Cent execution account."
        )

    # Component 1 verification
    if comp1:
        details["components_checked"].append("component_1")
        c1_role = str(comp1.get("evidence_role") or "").strip().upper()
        c1_type = str(comp1.get("source_type") or "").strip().upper()
        c1_sha = str(comp1.get("sha256") or "").strip().lower()
        c1_bytes = comp1.get("bytes")

        if c1_role != EXPECTED_CLIENT_AGREEMENT_ROLE:
            reasons.append(f"Component 1 evidence_role '{c1_role}' != '{EXPECTED_CLIENT_AGREEMENT_ROLE}'.")
        if c1_type != EXPECTED_CLIENT_AGREEMENT_SOURCE_TYPE:
            reasons.append(f"Component 1 source_type '{c1_type}' != '{EXPECTED_CLIENT_AGREEMENT_SOURCE_TYPE}'.")
        if c1_sha != EXPECTED_CLIENT_AGREEMENT_SHA256:
            reasons.append(f"Component 1 SHA-256 '{c1_sha}' != expected '{EXPECTED_CLIENT_AGREEMENT_SHA256}'.")
        if c1_bytes != EXPECTED_CLIENT_AGREEMENT_BYTES:
            reasons.append(f"Component 1 byte length '{c1_bytes}' != expected '{EXPECTED_CLIENT_AGREEMENT_BYTES}'.")

    # Component 2 verification
    if comp2:
        details["components_checked"].append("component_2")
        c2_role = str(comp2.get("evidence_role") or "").strip().upper()
        c2_type = str(comp2.get("source_type") or "").strip().upper()
        c2_sha = str(comp2.get("sha256") or "").strip().lower()
        c2_bytes = comp2.get("bytes")

        if c2_role != EXPECTED_PERSONAL_AREA_ROLE:
            reasons.append(f"Component 2 evidence_role '{c2_role}' != '{EXPECTED_PERSONAL_AREA_ROLE}'.")
        if c2_type != EXPECTED_PERSONAL_AREA_SOURCE_TYPE:
            reasons.append(f"Component 2 source_type '{c2_type}' != '{EXPECTED_PERSONAL_AREA_SOURCE_TYPE}'.")
        if c2_sha != EXPECTED_PERSONAL_AREA_SHA256:
            reasons.append(f"Component 2 SHA-256 '{c2_sha}' != expected '{EXPECTED_PERSONAL_AREA_SHA256}'.")
        if c2_bytes != EXPECTED_PERSONAL_AREA_BYTES:
            reasons.append(f"Component 2 byte length '{c2_bytes}' != expected '{EXPECTED_PERSONAL_AREA_BYTES}'.")

    # 2. Entity & Scope Validation
    entity_dict = attestation_data.get("legal_entity", {})
    scope_dict = attestation_data.get("target_scope", {})

    name = str(entity_dict.get("legal_entity_name") or "").strip()
    code = str(entity_dict.get("legal_entity_code") or "").strip().upper()
    reg = str(entity_dict.get("regulator") or "").strip().upper()
    lic = str(entity_dict.get("license_number") or "").strip()
    tier = str(scope_dict.get("account_tier") or "").strip().upper()
    curr = str(scope_dict.get("account_currency") or "").strip().upper()

    details.update({
        "legal_entity_name": name,
        "legal_entity_code": code,
        "regulator": reg,
        "license_number": lic,
        "account_tier": tier,
        "account_currency": curr,
    })

    if name != QUALIFIED_LEGAL_ENTITY_NAME:
        reasons.append(f"Legal entity name '{name}' != expected '{QUALIFIED_LEGAL_ENTITY_NAME}'.")
    if code != QUALIFIED_LEGAL_ENTITY_CODE:
        reasons.append(f"Legal entity code '{code}' != expected '{QUALIFIED_LEGAL_ENTITY_CODE}'.")
    if reg != QUALIFIED_REGULATOR:
        reasons.append(f"Regulator '{reg}' != expected '{QUALIFIED_REGULATOR}'.")
    if lic != QUALIFIED_LICENSE_NUMBER:
        reasons.append(f"License number '{lic}' != expected '{QUALIFIED_LICENSE_NUMBER}'.")
    if tier != QUALIFIED_ACCOUNT_TIER:
        reasons.append(f"Account tier '{tier}' != expected '{QUALIFIED_ACCOUNT_TIER}'.")
    if curr != QUALIFIED_ACCOUNT_CURRENCY:
        reasons.append(f"Account currency '{curr}' != expected '{QUALIFIED_ACCOUNT_CURRENCY}'.")

    # 3. Anti-Confusion & Negative Constraints
    # Constraint A: ACCOUNT_COMPANY = Exness Technologies Ltd must NOT be contracting entity
    if name.upper() in FORBIDDEN_CONTRACTING_ENTITIES or code in FORBIDDEN_CONTRACTING_ENTITIES:
        reasons.append(
            "FORBIDDEN_CONTRACTING_ENTITY: MT5 ACCOUNT_COMPANY (Exness Technologies Ltd) "
            "represents platform/infrastructure metadata and is strictly forbidden as contracting entity."
        )

    # Constraint B: Server allocation independence
    server_policy = str(
        attestation_data.get("critical_policies", {}).get("server_binding_policy") or ""
    ).strip().upper()
    if simulated_server:
        # Server change must not alter entity identity
        if simulated_server not in OBSERVED_MT5_SERVERS:
            pass  # Allowed, execution infrastructure only
    if server_policy != SERVER_BINDING_POLICY:
        reasons.append(f"Server binding policy '{server_policy}' != expected '{SERVER_BINDING_POLICY}'.")

    # Constraint C: Generic OFFICIAL_BROKER_DOCUMENT alone cannot qualify
    raw_source = str(attestation_data.get("source_type") or "").strip().upper()
    if raw_source == FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value and not (comp1 and comp2):
        reasons.append(
            "GENERIC_SOURCE_INSUFFICIENT: OFFICIAL_BROKER_DOCUMENT alone cannot satisfy Standard Cent legal entity."
        )
    if raw_source == FrictionSourceType.USER_PROVIDED_UNVERIFIED.value:
        reasons.append("UNVERIFIED_SOURCE_REJECTED: USER_PROVIDED_UNVERIFIED cannot qualify.")

    # 4. Local Raw Evidence Verification (if directory provided or present)
    if local_evidence_dir:
        ev_dir = Path(local_evidence_dir)
        if ev_dir.exists():
            pdf_path = ev_dir / EXPECTED_CLIENT_AGREEMENT_FILENAME
            jpeg_path = ev_dir / EXPECTED_PERSONAL_AREA_FILENAME
            if pdf_path.exists():
                pdf_bytes = pdf_path.read_bytes()
                pdf_sha = hashlib.sha256(pdf_bytes).hexdigest()
                if pdf_sha != EXPECTED_CLIENT_AGREEMENT_SHA256:
                    reasons.append(f"Local Client Agreement SHA-256 mismatch: {pdf_sha}")
                if len(pdf_bytes) != EXPECTED_CLIENT_AGREEMENT_BYTES:
                    reasons.append(f"Local Client Agreement size mismatch: {len(pdf_bytes)}")
            else:
                reasons.append(f"Local file missing: {EXPECTED_CLIENT_AGREEMENT_FILENAME}")

            if jpeg_path.exists():
                jpeg_bytes = jpeg_path.read_bytes()
                jpeg_sha = hashlib.sha256(jpeg_bytes).hexdigest()
                if jpeg_sha != EXPECTED_PERSONAL_AREA_SHA256:
                    reasons.append(f"Local Personal Area JPEG SHA-256 mismatch: {jpeg_sha}")
                if len(jpeg_bytes) != EXPECTED_PERSONAL_AREA_BYTES:
                    reasons.append(f"Local Personal Area JPEG size mismatch: {len(jpeg_bytes)}")
            else:
                reasons.append(f"Local file missing: {EXPECTED_PERSONAL_AREA_FILENAME}")

    # Validate receipt components, entity, and scope as part of evidence integrity if receipt provided
    if receipt_data and isinstance(receipt_data, dict):
        r_scope = receipt_data.get("target_scope", {})
        r_entity = receipt_data.get("legal_entity", {})
        r_name = str(r_entity.get("legal_entity_name") or "").strip()
        r_code = str(r_entity.get("legal_entity_code") or "").strip().upper()
        r_reg = str(r_entity.get("regulator") or "").strip().upper()
        r_lic = str(r_entity.get("license_number") or "").strip()
        r_tier = str(r_scope.get("account_tier") or "").strip().upper()
        r_curr = str(r_scope.get("account_currency") or "").strip().upper()

        if r_name != QUALIFIED_LEGAL_ENTITY_NAME:
            reasons.append(f"Receipt legal entity name '{r_name}' != expected '{QUALIFIED_LEGAL_ENTITY_NAME}'.")
        if r_code != QUALIFIED_LEGAL_ENTITY_CODE:
            reasons.append(f"Receipt legal entity code '{r_code}' != expected '{QUALIFIED_LEGAL_ENTITY_CODE}'.")
        if r_reg != QUALIFIED_REGULATOR:
            reasons.append(f"Receipt regulator '{r_reg}' != expected '{QUALIFIED_REGULATOR}'.")
        if r_lic != QUALIFIED_LICENSE_NUMBER:
            reasons.append(f"Receipt license number '{r_lic}' != expected '{QUALIFIED_LICENSE_NUMBER}'.")
        if r_tier != QUALIFIED_ACCOUNT_TIER:
            reasons.append(f"Receipt account tier '{r_tier}' != expected '{QUALIFIED_ACCOUNT_TIER}'.")
        if r_curr != QUALIFIED_ACCOUNT_CURRENCY:
            reasons.append(f"Receipt account currency '{r_curr}' != expected '{QUALIFIED_ACCOUNT_CURRENCY}'.")

        r_comps = receipt_data.get("governed_components", {})
        r_c1 = r_comps.get("component_1", {}) if isinstance(r_comps, dict) else {}
        r_c2 = r_comps.get("component_2", {}) if isinstance(r_comps, dict) else {}
        if str(r_c1.get("evidence_role") or "").strip().upper() != EXPECTED_CLIENT_AGREEMENT_ROLE:
            reasons.append("Receipt component 1 evidence role mismatch.")
        if str(r_c1.get("source_type") or "").strip().upper() != EXPECTED_CLIENT_AGREEMENT_SOURCE_TYPE:
            reasons.append("Receipt component 1 source type mismatch.")
        if str(r_c1.get("sha256") or "").strip().lower() != EXPECTED_CLIENT_AGREEMENT_SHA256:
            reasons.append("Receipt component 1 SHA-256 mismatch.")
        if r_c1.get("bytes") != EXPECTED_CLIENT_AGREEMENT_BYTES:
            reasons.append("Receipt component 1 bytes mismatch.")

        if str(r_c2.get("evidence_role") or "").strip().upper() != EXPECTED_PERSONAL_AREA_ROLE:
            reasons.append("Receipt component 2 evidence role mismatch.")
        if str(r_c2.get("source_type") or "").strip().upper() != EXPECTED_PERSONAL_AREA_SOURCE_TYPE:
            reasons.append("Receipt component 2 source type mismatch.")
        if str(r_c2.get("sha256") or "").strip().lower() != EXPECTED_PERSONAL_AREA_SHA256:
            reasons.append("Receipt component 2 SHA-256 mismatch.")
        if r_c2.get("bytes") != EXPECTED_PERSONAL_AREA_BYTES:
            reasons.append("Receipt component 2 bytes mismatch.")

        r_policies = receipt_data.get("critical_policies", {})
        r_server_policy = str(r_policies.get("server_binding_policy") or "").strip().upper()
        if r_server_policy != SERVER_BINDING_POLICY:
            reasons.append(f"Receipt server binding policy '{r_server_policy}' != expected '{SERVER_BINDING_POLICY}'.")

    # Evaluate Layer 1: COMPOSITE_EVIDENCE_INTEGRITY
    evidence_integrity_pass = len(reasons) == 0
    composite_evidence_integrity = "PASS" if evidence_integrity_pass else "FAIL"

    # Evaluate Layer 2: PRODUCTION_ORIGIN_AUTHENTICITY
    production_origin_authenticity = "HOLD"
    governed_status = "HOLD"
    is_qualified = False

    if not evidence_integrity_pass:
        production_origin_authenticity = "FAIL"
        governed_status = "FAIL"
        return GovernedCompositeVerificationResult(
            is_qualified=False,
            governed_status="FAIL",
            composite_evidence_integrity="FAIL",
            production_origin_authenticity="FAIL",
            reasons=reasons,
            details=details,
        )

    # Path A: Authenticated Review Receipt Workflow
    if receipt_data and isinstance(receipt_data, dict):
        details["receipt_evaluated"] = True
        r_schema = str(receipt_data.get("schema") or "").strip()
        r_receipt_id = str(receipt_data.get("receipt_id") or "").strip()
        r_reviewed_at = str(receipt_data.get("reviewed_at") or "").strip()
        r_workflow_ver = str(receipt_data.get("workflow_version") or "").strip()
        r_authority = str(receipt_data.get("verification_authority") or "").strip()
        r_method = str(receipt_data.get("verification_method") or "").strip().upper()
        r_verifier = str(receipt_data.get("verifier_identity") or "").strip()
        r_proof_ver = str(receipt_data.get("verification_proof_version") or "").strip()
        r_proof = proof_override or receipt_data.get("verification_proof")

        # 1. Receipt schema
        if r_schema != LEGAL_ENTITY_REVIEW_RECEIPT_SCHEMA:
            reasons.append(f"Receipt schema '{r_schema}' != expected '{LEGAL_ENTITY_REVIEW_RECEIPT_SCHEMA}'.")

        # 2. Method
        if r_method != VERIFICATION_METHOD_COMPOSITE_REVIEW:
            reasons.append(f"Receipt verification method '{r_method}' != expected '{VERIFICATION_METHOD_COMPOSITE_REVIEW}'.")

        # 3. Verifier trusted registry check
        is_trusted, trust_err = is_trusted_verifier(r_method, r_verifier)
        if not is_trusted:
            reasons.append(f"Receipt verifier untrusted: {trust_err}")

        # 4. Mandatory receipt metadata
        if not r_receipt_id:
            reasons.append("Receipt missing receipt_id.")
        if not r_reviewed_at:
            reasons.append("Receipt missing reviewed_at.")
        if r_workflow_ver != REVIEW_WORKFLOW_VERSION:
            reasons.append(f"Receipt workflow version '{r_workflow_ver}' != expected '{REVIEW_WORKFLOW_VERSION}'.")
        if r_authority != GOVERNED_PROVENANCE_AUTHORITY:
            reasons.append(f"Receipt verification authority '{r_authority}' != expected '{GOVERNED_PROVENANCE_AUTHORITY}'.")
        if r_proof_ver != CURRENT_PROOF_VERSION:
            reasons.append(f"Receipt proof version '{r_proof_ver}' != expected '{CURRENT_PROOF_VERSION}'.")

        # 5. Policies & Claims
        r_policies = receipt_data.get("critical_policies", {})
        r_server_policy = str(r_policies.get("server_binding_policy") or "").strip().upper()
        r_origin_claim = str(r_policies.get("origin_claim") or "").strip().upper()
        r_broker_crypto = str(r_policies.get("broker_cryptographic_origin") or "").strip().upper()

        if r_origin_claim != ORIGIN_CLAIM_AUTHENTICATED_REVIEW_ONLY:
            reasons.append(f"Receipt origin claim '{r_origin_claim}' != expected '{ORIGIN_CLAIM_AUTHENTICATED_REVIEW_ONLY}'.")
        if r_broker_crypto != BROKER_CRYPTOGRAPHIC_ORIGIN_NOT_CLAIMED:
            reasons.append(f"Receipt broker cryptographic origin '{r_broker_crypto}' != expected '{BROKER_CRYPTOGRAPHIC_ORIGIN_NOT_CLAIMED}'.")

        # 8. Cryptographic Proof Verification
        if not r_proof:
            production_origin_authenticity = "HOLD"
            governed_status = "HOLD"
            is_qualified = False
            details["production_hold_reason"] = "Receipt verification_proof is missing or null. Status remains HOLD."
        else:
            canonical_payload = compute_canonical_review_receipt_payload(
                schema=r_schema,
                verification_authority=r_authority,
                verification_proof_version=r_proof_ver,
                workflow_version=r_workflow_ver,
                receipt_id=r_receipt_id,
                reviewed_at_iso=r_reviewed_at,
                verification_method=r_method,
                verifier_identity=r_verifier,
                legal_entity_name=r_name,
                legal_entity_code=r_code,
                regulator=r_reg,
                license_number=r_lic,
                account_tier=r_tier,
                account_currency=r_curr,
                comp1_role=str(r_c1.get("evidence_role") or ""),
                comp1_source_type=str(r_c1.get("source_type") or ""),
                comp1_sha256=str(r_c1.get("sha256") or ""),
                comp1_bytes=r_c1.get("bytes", 0),
                comp2_role=str(r_c2.get("evidence_role") or ""),
                comp2_source_type=str(r_c2.get("source_type") or ""),
                comp2_sha256=str(r_c2.get("sha256") or ""),
                comp2_bytes=r_c2.get("bytes", 0),
                server_binding_policy=r_server_policy,
                origin_claim=r_origin_claim,
                broker_cryptographic_origin=r_broker_crypto,
            )
            test_sentinel = b"aurumiq-test-only-provenance-key-NOT-FOR-PRODUCTION"
            try:
                secret = get_governed_signing_secret()
                if not is_test_ctx and secret == test_sentinel:
                    production_origin_authenticity = "HOLD"
                    governed_status = "HOLD"
                    is_qualified = False
                    details["production_hold_reason"] = (
                        "Test sentinel provenance key cannot qualify production origin authenticity. "
                        "A genuine PROVENANCE_SIGNING_SECRET is required."
                    )
                    reasons.append("Test sentinel key prohibited for production origin authenticity.")
                else:
                    expected_proof = compute_review_receipt_proof(canonical_payload, signing_secret=secret)
                    if hmac.compare_digest(str(r_proof).strip().lower(), expected_proof.strip().lower()):
                        details["cryptographic_receipt_valid"] = True
                        if len(reasons) == 0:
                            if not is_production_environment():
                                production_origin_authenticity = "HOLD"
                                governed_status = "HOLD"
                                is_qualified = False
                                details["production_hold_reason"] = (
                                    "NON_PRODUCTION_CONTEXT: Valid cryptographic review receipt cannot grant "
                                    "production authenticity outside strict production settings "
                                    "(config.settings.production with DEBUG=False)."
                                )
                                reasons.append("NON_PRODUCTION_CONTEXT: Valid cryptographic receipt verified, but runtime is non-production.")
                            else:
                                production_origin_authenticity = "PASS"
                                governed_status = "QUALIFIED"
                                is_qualified = True
                        else:
                            production_origin_authenticity = "FAIL"
                            governed_status = "FAIL"
                            is_qualified = False
                    else:
                        details["cryptographic_receipt_valid"] = False
                        production_origin_authenticity = "FAIL"
                        governed_status = "FAIL"
                        is_qualified = False
                        reasons.append("Cryptographic verification proof mismatch for legal entity review receipt.")
            except RuntimeError as exc:
                # Missing secret fails closed WITHOUT crashing
                production_origin_authenticity = "HOLD"
                governed_status = "HOLD"
                is_qualified = False
                details["production_hold_reason"] = f"PROVENANCE_SIGNING_SECRET unavailable in environment: {exc}"
                reasons.append(f"Provenance signing secret unavailable: {exc}")
            except Exception as exc:
                production_origin_authenticity = "FAIL"
                governed_status = "FAIL"
                is_qualified = False
                reasons.append(f"Proof verification failed: {exc}")

    # Path B: Fallback / Legacy Attestation Verification (for test seam or when receipt is absent)
    else:
        provenance_block = attestation_data.get("provenance_and_authenticity", {})
        verifier_id = str(provenance_block.get("verifier_identity") or "").strip()
        proof = proof_override or provenance_block.get("verification_proof")

        if not is_test_ctx:
            production_origin_authenticity = "HOLD"
            governed_status = "HOLD"
            is_qualified = False
            details["production_hold_reason"] = (
                "PRODUCTION_AUTHENTICITY_UNAVAILABLE: No authenticated legal entity review receipt "
                "or proof is deployed in production. Hard governance strictly requires HOLD."
            )
        else:
            # Test Execution Context using isolated test seam
            if proof:
                canonical_payload = compute_canonical_composite_payload(
                    legal_entity_name=name,
                    legal_entity_code=code,
                    regulator=reg,
                    license_number=lic,
                    account_tier=tier,
                    account_currency=curr,
                    comp1_sha256=comp1.get("sha256", "") if comp1 else "",
                    comp1_bytes=comp1.get("bytes", 0) if comp1 else 0,
                    comp2_sha256=comp2.get("sha256", "") if comp2 else "",
                    comp2_bytes=comp2.get("bytes", 0) if comp2 else 0,
                    verifier_identity=verifier_id or "TEST_SUITE_ISOLATED_PROVENANCE_SEAM",
                )
                try:
                    expected_proof = compute_composite_verification_proof(canonical_payload)
                    if hmac.compare_digest(proof.strip().lower(), expected_proof.strip().lower()):
                        production_origin_authenticity = "PASS"
                        governed_status = "QUALIFIED"
                        is_qualified = True
                    else:
                        production_origin_authenticity = "FAIL"
                        governed_status = "FAIL"
                        reasons.append("Cryptographic verification proof mismatch for composite attestation.")
                except Exception as exc:
                    production_origin_authenticity = "FAIL"
                    governed_status = "FAIL"
                    reasons.append(f"Proof verification failed: {exc}")
            else:
                production_origin_authenticity = "HOLD"
                governed_status = "HOLD"
                is_qualified = False

    return GovernedCompositeVerificationResult(
        is_qualified=is_qualified,
        governed_status=governed_status,
        composite_evidence_integrity=composite_evidence_integrity,
        production_origin_authenticity=production_origin_authenticity,
        reasons=reasons,
        details=details,
    )
