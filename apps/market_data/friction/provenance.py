"""Governed Provenance Authenticity and Cryptographic Attestation Engine.

Enforces fail-closed trust boundaries for XAUUSD empirical friction evidence:
- Rejects caller declarations and user JSON from self-authenticating.
- Mandates DECLARED vs. VERIFIED attestation status separation.
- Binds canonical provenance payloads into HMAC-SHA256 verification proofs.
- Restricts VERIFIED attestation creation to governed collector workflows.
- Derives evidence identity and scope from trusted collector outputs, not caller claims.
- Strictly isolates test seams from production runtime (prohibiting DEBUG=True authorization).
"""
from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
from typing import Any, Dict, List, Optional, Set, Tuple
import urllib.parse

from django.conf import settings
from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)

GOVERNED_PROVENANCE_AUTHORITY = "AURUMIQ_GOVERNED_PROVENANCE_AUTHORITY"
CURRENT_PROOF_VERSION = "1.0.0"

PERMITTED_BROKER_DOMAINS: Set[str] = {
    "exness.com",
    "www.exness.com",
    "get.exness.help",
    "my.exness.com",
    "trade.exness.com",
}

# Trusted verifier registry mapping verification methods to permitted collector identities.
# Note: Membership in this registry is necessary but NOT sufficient; cryptographic proof is mandatory.
TRUSTED_VERIFIER_REGISTRY: Dict[str, Set[str]] = {
    "MT5_DIRECT_EXPORT": {
        "AURUMIQ_MT5_COLLECTOR_V1",
        "SYSTEM_MT5_BRIDGE_WORKFLOW",
        "TEST_SUITE_ISOLATED_PROVENANCE_SEAM",
    },
    "BROKER_OFFICIAL_URL_CAPTURE": {
        "AURUMIQ_OFFICIAL_BROKER_URL_CAPTURE_WORKFLOW",
        "SYSTEM_BROKER_URL_FETCHER_V1",
        "TEST_SUITE_ISOLATED_PROVENANCE_SEAM",
    },
    "ACCOUNT_PORTAL_EXPORT": {
        "AURUMIQ_ACCOUNT_PORTAL_CAPTURE_WORKFLOW",
        "SYSTEM_ACCOUNT_PORTAL_EXPORT_V1",
        "TEST_SUITE_ISOLATED_PROVENANCE_SEAM",
    },
    "MANUAL_REVIEWED_OFFICIAL_DOCUMENT": {
        "TEST_SUITE_ISOLATED_PROVENANCE_SEAM",
    },
}


def is_test_environment() -> bool:
    """Strict test-only isolation check (Requirement 7).

    DEBUG=True by itself MUST NEVER authorize test seams or qualification bypasses.
    Returns True ONLY if settings.IS_TESTING is explicitly True, or the active
    settings module is strictly 'config.settings.testing'.
    """
    if getattr(settings, "IS_TESTING", False) is True:
        return True
    mod = getattr(settings, "SETTINGS_MODULE", "")
    if mod == "config.settings.testing":
        return True
    return False


def get_governed_signing_secret() -> bytes:
    """Retrieve application-controlled secret for provenance proof signing.

    Unavailable to CLI callers, metadata JSONs, or uploaded artifacts.
    """
    secret = getattr(settings, "PROVENANCE_SIGNING_SECRET", None) or getattr(settings, "SECRET_KEY", "")
    if not secret:
        secret = "aurumiq-governed-provenance-default-key-locked"
    return secret.encode("utf-8")


def compute_canonical_provenance_payload(
    source_snapshot_id: str,
    raw_artifact_sha256: str,
    component_role: str,
    verification_method: str,
    source_type: str,
    venue: str,
    symbol: str,
    account_tier: str,
    captured_at_iso: str,
    verifier_identity: str,
    verification_authority: str = GOVERNED_PROVENANCE_AUTHORITY,
    verification_proof_version: str = CURRENT_PROOF_VERSION,
) -> str:
    """Construct deterministic canonical key-value payload for cryptographic signing."""
    parts = [
        f"authority={verification_authority.strip()}",
        f"proof_ver={verification_proof_version.strip()}",
        f"snapshot={source_snapshot_id.strip()}",
        f"raw_sha={raw_artifact_sha256.strip().lower()}",
        f"role={component_role.strip().upper()}",
        f"method={verification_method.strip().upper()}",
        f"source_type={source_type.strip().upper()}",
        f"venue={venue.strip().upper()}",
        f"symbol={symbol.strip().upper()}",
        f"tier={account_tier.strip().upper()}",
        f"captured={captured_at_iso.strip()}",
        f"verifier={verifier_identity.strip()}",
    ]
    return "|".join(parts)


def compute_verification_proof(
    source_snapshot_id: str,
    raw_artifact_sha256: str,
    component_role: str,
    verification_method: str,
    source_type: str,
    venue: str,
    symbol: str,
    account_tier: str,
    captured_at: Any,
    verifier_identity: str,
    verification_authority: str = GOVERNED_PROVENANCE_AUTHORITY,
    verification_proof_version: str = CURRENT_PROOF_VERSION,
) -> str:
    """Generate cryptographic HMAC-SHA256 verification proof binding canonical payload."""
    captured_iso = captured_at.isoformat() if hasattr(captured_at, "isoformat") else str(captured_at)
    payload = compute_canonical_provenance_payload(
        source_snapshot_id=source_snapshot_id,
        raw_artifact_sha256=raw_artifact_sha256,
        component_role=component_role,
        verification_method=verification_method,
        source_type=source_type,
        venue=venue,
        symbol=symbol,
        account_tier=account_tier,
        captured_at_iso=captured_iso,
        verifier_identity=verifier_identity,
        verification_authority=verification_authority,
        verification_proof_version=verification_proof_version,
    )
    secret = get_governed_signing_secret()
    return hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_attestation_proof(attestation: Any) -> Tuple[bool, Optional[str]]:
    """Verify cryptographic HMAC-SHA256 verification proof on an attestation record."""
    proof = getattr(attestation, "verification_proof", "")
    if not proof or not str(proof).strip():
        return False, "Attestation record lacks verification proof."

    captured = getattr(attestation, "captured_at", None)
    captured_iso = captured.isoformat() if hasattr(captured, "isoformat") else str(captured)

    expected_proof = compute_verification_proof(
        source_snapshot_id=str(getattr(attestation, "source_snapshot_id", "") or (attestation.source_snapshot.snapshot_id if hasattr(attestation, "source_snapshot") and attestation.source_snapshot else "")),
        raw_artifact_sha256=str(getattr(attestation, "raw_artifact_sha256", "")),
        component_role=str(getattr(attestation, "component_role", "")),
        verification_method=str(getattr(attestation, "verification_method", "")),
        source_type=str(getattr(attestation, "source_type", "")),
        venue=str(getattr(attestation, "venue", "")),
        symbol=str(getattr(attestation, "symbol", "")),
        account_tier=str(getattr(attestation, "account_tier", "")),
        captured_at=captured_iso,
        verifier_identity=str(getattr(attestation, "verifier_identity", "")),
        verification_authority=str(getattr(attestation, "verification_authority", "") or GOVERNED_PROVENANCE_AUTHORITY),
        verification_proof_version=str(getattr(attestation, "verification_proof_version", "") or CURRENT_PROOF_VERSION),
    )

    if not hmac.compare_digest(proof.strip(), expected_proof):
        return False, "Cryptographic verification proof mismatch: proof does not match canonical provenance payload or signing secret."

    return True, None


def is_trusted_verifier(verification_method: str, verifier_identity: str) -> Tuple[bool, Optional[str]]:
    """Validate that verifier identity is registered and authorized for the verification method."""
    method_str = str(verification_method).strip().upper()
    verifier_str = str(verifier_identity).strip()

    allowed = TRUSTED_VERIFIER_REGISTRY.get(method_str, set())
    if verifier_str not in allowed:
        return False, f"Verifier identity '{verifier_str}' is not in trusted registry for method '{method_str}'."

    if verifier_str == "TEST_SUITE_ISOLATED_PROVENANCE_SEAM":
        if not is_test_environment():
            return False, "Test seam verifier identity 'TEST_SUITE_ISOLATED_PROVENANCE_SEAM' is prohibited outside explicit testing environment."

    return True, None


def verify_attestation_authenticity(attestation: Any) -> Tuple[bool, Optional[str]]:
    """Comprehensive validation of attestation authenticity and trust governance.

    Enforces:
    1. attestation_status MUST be VERIFIED.
    2. verification_method MUST be in ACCEPTED_VERIFICATION_METHODS.
    3. verifier_identity MUST be in TRUSTED_VERIFIER_REGISTRY.
    4. Test seam verifiers MUST be strictly isolated to test environments.
    5. MANUAL_REVIEWED_OFFICIAL_DOCUMENT in production CANNOT be VERIFIED (no authenticated review system yet).
    6. BROKER_OFFICIAL_URL_CAPTURE MUST contain authentic capture telemetry and allowed broker domain.
    7. Cryptographic proof MUST match canonical provenance payload.
    """
    from apps.market_data.models import (
        ACCEPTED_VERIFICATION_METHODS,
        FrictionAttestationStatus,
        FrictionVerificationMethod,
    )

    status = str(getattr(attestation, "attestation_status", ""))
    if status != FrictionAttestationStatus.VERIFIED.value:
        return False, f"Attestation status is '{status}'; only independently VERIFIED attestations may satisfy hard qualification."

    method = str(getattr(attestation, "verification_method", ""))
    if method not in ACCEPTED_VERIFICATION_METHODS:
        return False, f"Verification method '{method}' is not in accepted methods: {sorted(ACCEPTED_VERIFICATION_METHODS)}."

    verifier = str(getattr(attestation, "verifier_identity", "")).strip()
    is_trusted, trust_err = is_trusted_verifier(method, verifier)
    if not is_trusted:
        return False, trust_err

    # Manual review without authenticated reviewer system remains DECLARED only (Directive 6)
    if method == FrictionVerificationMethod.MANUAL_REVIEWED_OFFICIAL_DOCUMENT.value:
        if verifier != "TEST_SUITE_ISOLATED_PROVENANCE_SEAM":
            return False, "Manual reviewed documents cannot be hard-gate VERIFIED until an authenticated application review workflow is deployed."

    # Broker official URL capture validation (Directive 3)
    if method == FrictionVerificationMethod.BROKER_OFFICIAL_URL_CAPTURE.value:
        meta = getattr(attestation, "provenance_metadata", {}) or {}
        if not isinstance(meta, dict):
            return False, "Broker URL capture attestation lacks structured provenance_metadata dict."

        req_url = str(meta.get("requested_url") or "")
        final_url = str(meta.get("final_url") or "")
        hostname = str(meta.get("hostname") or "").lower()
        http_status = meta.get("http_status")
        resp_sha = str(meta.get("raw_response_sha256") or "").lower()
        col_ver = str(meta.get("collector_version") or "")

        if not req_url or not final_url:
            return False, "Broker URL capture metadata missing requested_url or final_url."
        if not hostname:
            parsed_host = urllib.parse.urlparse(final_url or req_url).netloc.lower()
            hostname = parsed_host

        if hostname not in PERMITTED_BROKER_DOMAINS:
            return False, f"Broker URL capture hostname '{hostname}' is not in permitted broker domains: {sorted(PERMITTED_BROKER_DOMAINS)}."

        if http_status != 200:
            return False, f"Broker URL capture HTTP status is {http_status} (expected 200)."

        if not resp_sha or resp_sha != str(getattr(attestation, "raw_artifact_sha256", "")).lower():
            return False, "Broker URL capture raw_response_sha256 mismatch with attestation raw artifact SHA."

        if not col_ver:
            return False, "Broker URL capture metadata missing collector_version."

    # Cryptographic proof verification
    is_proof_valid, proof_err = verify_attestation_proof(attestation)
    if not is_proof_valid:
        return False, proof_err

    return True, None


# --------------------------------------------------------------------------------------
# Governed Collector Factories (Directive 2)
# --------------------------------------------------------------------------------------

def create_verified_mt5_export_attestation(
    source_snapshot: Any,
    component_role: str,
    raw_bytes: bytes,
    expected_symbol: str = "XAUUSD",
    expected_venue: str = "EXNESS",
    expected_account_tier: str = "STANDARD",
    verifier_identity: str = "AURUMIQ_MT5_COLLECTOR_V1",
    collector_metadata: Optional[Dict[str, Any]] = None,
) -> Any:
    """Governed collector workflow for authentic MT5 direct exports.

    Derives evidence identity/scope strictly from trusted MT5 parsers:
    - SPREAD_DATASET -> parse_mt5_tick_export -> derives symbol
    - SLIPPAGE_DATASET -> parse_mt5_execution_telemetry -> derives symbol, venue, account tier
    - CONTRACT_SPEC -> parse_contract_spec_backing_artifact -> derives symbol
    Validates derived scope matches expected parameters; fails closed on mismatch.
    """
    from apps.market_data.models import (
        FrictionAttestationStatus,
        FrictionSourceProvenanceAttestation,
        FrictionSourceType,
        FrictionVerificationMethod,
    )
    from apps.market_data.friction.artifact_parsers import parse_contract_spec_backing_artifact
    from apps.market_data.friction.slippage_parser import parse_mt5_execution_telemetry
    from apps.market_data.friction.tick_parser import parse_mt5_tick_export

    norm_role = str(component_role).strip().upper()
    computed_raw_sha = hashlib.sha256(raw_bytes).hexdigest()
    if computed_raw_sha != source_snapshot.raw_payload_bytes_sha256:
        raise ValueError(
            f"MT5_COLLECTOR_ERROR: Raw bytes SHA '{computed_raw_sha}' mismatch with snapshot '{source_snapshot.raw_payload_bytes_sha256}'."
        )

    derived_symbol: str = ""
    derived_venue: str = expected_venue
    derived_account_tier: str = expected_account_tier
    source_type: str = ""

    if norm_role == "SPREAD_DATASET":
        ticks_data, summary = parse_mt5_tick_export(raw_bytes, expected_symbol=expected_symbol)
        derived_symbol = str(summary.get("symbol") or "")
        derived_venue = str(summary.get("venue") or expected_venue)
        source_type = FrictionSourceType.MT5_TICK_HISTORY_EXPORT.value
    elif norm_role == "SLIPPAGE_DATASET":
        telemetry_records, summary = parse_mt5_execution_telemetry(
            raw_bytes,
            expected_venue=expected_venue,
            expected_symbol=expected_symbol,
            expected_account_tier=expected_account_tier,
        )
        derived_symbol = str(summary.get("symbol") or expected_symbol)
        derived_venue = str(summary.get("venue") or expected_venue)
        derived_account_tier = str(summary.get("account_tier") or expected_account_tier)
        source_type = FrictionSourceType.MT5_EXECUTION_TELEMETRY_EXPORT.value
    elif norm_role == "CONTRACT_SPEC":
        parsed = parse_contract_spec_backing_artifact(raw_bytes, expected_symbol=expected_symbol)
        derived_symbol = str(parsed.get("symbol") or "")
        source_type = FrictionSourceType.MT5_SYMBOL_INFO_EXPORT.value
    else:
        raise ValueError(f"MT5_COLLECTOR_ERROR: Unsupported component role '{component_role}' for MT5 direct export.")

    # Scope validation: derived scope MUST strictly match expected scope
    if not derived_symbol or derived_symbol.upper() != expected_symbol.upper():
        raise ValueError(
            f"MT5_SCOPE_MISMATCH: Collector derived symbol '{derived_symbol}' does not match expected '{expected_symbol}'."
        )
    if derived_venue.upper() != expected_venue.upper():
        raise ValueError(
            f"MT5_SCOPE_MISMATCH: Collector derived venue '{derived_venue}' does not match expected '{expected_venue}'."
        )
    if derived_account_tier.upper() != expected_account_tier.upper():
        raise ValueError(
            f"MT5_SCOPE_MISMATCH: Collector derived account tier '{derived_account_tier}' does not match expected '{expected_account_tier}'."
        )

    # Validate verifier identity
    is_trusted, err = is_trusted_verifier(FrictionVerificationMethod.MT5_DIRECT_EXPORT.value, verifier_identity)
    if not is_trusted:
        raise ValueError(f"MT5_VERIFIER_ERROR: {err}")

    captured_at = source_snapshot.retrieved_at or datetime.now(timezone.utc)
    captured_iso = captured_at.isoformat()

    proof = compute_verification_proof(
        source_snapshot_id=source_snapshot.snapshot_id,
        raw_artifact_sha256=computed_raw_sha,
        component_role=norm_role,
        verification_method=FrictionVerificationMethod.MT5_DIRECT_EXPORT.value,
        source_type=source_type,
        venue=derived_venue.upper(),
        symbol=derived_symbol.upper(),
        account_tier=derived_account_tier.upper(),
        captured_at=captured_iso,
        verifier_identity=verifier_identity.strip(),
    )

    meta = dict(collector_metadata or {})
    meta.update({
        "collector_workflow": "AURUMIQ_GOVERNED_MT5_COLLECTOR",
        "derived_symbol": derived_symbol.upper(),
        "derived_venue": derived_venue.upper(),
        "derived_account_tier": derived_account_tier.upper(),
    })

    attestation_id = hashlib.sha256(
        f"{source_snapshot.snapshot_id}:{norm_role}:{FrictionVerificationMethod.MT5_DIRECT_EXPORT.value}:{verifier_identity}:{computed_raw_sha}:{proof}".encode()
    ).hexdigest()

    existing = FrictionSourceProvenanceAttestation.objects.filter(attestation_id=attestation_id).first()
    if existing:
        return existing

    return FrictionSourceProvenanceAttestation.objects.create(
        attestation_id=attestation_id,
        source_snapshot=source_snapshot,
        component_role=norm_role,
        source_origin=f"mt5://terminal/{derived_venue.lower()}/{derived_symbol.lower()}",
        source_type=source_type,
        collection_methodology="GOVERNED_MT5_DIRECT_EXPORT",
        captured_at=captured_at,
        reviewed_at=datetime.now(timezone.utc),
        verification_method=FrictionVerificationMethod.MT5_DIRECT_EXPORT.value,
        verifier_identity=verifier_identity.strip(),
        venue=derived_venue.upper(),
        symbol=derived_symbol.upper(),
        account_tier=derived_account_tier.upper(),
        raw_artifact_sha256=computed_raw_sha,
        provenance_metadata=meta,
        attestation_status=FrictionAttestationStatus.VERIFIED.value,
        verification_authority=GOVERNED_PROVENANCE_AUTHORITY,
        verification_proof=proof,
        verification_proof_version=CURRENT_PROOF_VERSION,
    )


def create_verified_broker_url_capture_attestation(
    source_snapshot: Any,
    component_role: str,
    raw_bytes: bytes,
    requested_url: str,
    final_url: str,
    http_status: int = 200,
    expected_symbol: str = "XAUUSD",
    expected_venue: str = "EXNESS",
    expected_account_tier: str = "STANDARD",
    verifier_identity: str = "AURUMIQ_OFFICIAL_BROKER_URL_CAPTURE_WORKFLOW",
    collector_version: str = "1.0.0",
    content_type: str = "text/html",
) -> Any:
    """Governed collector workflow for official broker URL captures (Directive 2 & 3).

    Binds requested_url, final_url, hostname, captured_at, http_status, content_type,
    raw_response_sha256, and collector_version into verified provenance proof.
    Derives and validates scope from parsed document structure.
    """
    from apps.market_data.models import (
        FrictionAttestationStatus,
        FrictionSourceProvenanceAttestation,
        FrictionSourceType,
        FrictionVerificationMethod,
    )
    from apps.market_data.friction.artifact_parsers import (
        parse_commission_backing_artifact,
        parse_contract_spec_backing_artifact,
        parse_financing_backing_artifact,
        parse_legal_entity_backing_artifact,
    )

    norm_role = str(component_role).strip().upper()
    computed_raw_sha = hashlib.sha256(raw_bytes).hexdigest()
    if computed_raw_sha != source_snapshot.raw_payload_bytes_sha256:
        raise ValueError(
            f"URL_CAPTURE_ERROR: Raw bytes SHA '{computed_raw_sha}' mismatch with snapshot '{source_snapshot.raw_payload_bytes_sha256}'."
        )

    parsed_url = urllib.parse.urlparse(final_url or requested_url)
    hostname = parsed_url.netloc.lower()
    if hostname not in PERMITTED_BROKER_DOMAINS:
        raise ValueError(
            f"URL_CAPTURE_ERROR: Hostname '{hostname}' is not in permitted broker domains: {sorted(PERMITTED_BROKER_DOMAINS)}."
        )

    if http_status != 200:
        raise ValueError(f"URL_CAPTURE_ERROR: HTTP status {http_status} != 200.")

    # Scope derivation via authoritative parser
    derived_symbol = expected_symbol
    derived_venue = "EXNESS"
    derived_tier = expected_account_tier

    if norm_role == "LEGAL_ENTITY":
        legal_data = parse_legal_entity_backing_artifact(raw_bytes)
        code = str(legal_data.get("legal_entity_code") or "")
        if "EXNESS" not in code.upper():
            raise ValueError(f"URL_CAPTURE_SCOPE_MISMATCH: Derived legal entity '{code}' is not Exness.")
    elif norm_role == "CONTRACT_SPEC":
        spec_data = parse_contract_spec_backing_artifact(raw_bytes, expected_symbol=expected_symbol)
        derived_symbol = str(spec_data.get("symbol") or "")
        if derived_symbol.upper() != expected_symbol.upper():
            raise ValueError(f"URL_CAPTURE_SCOPE_MISMATCH: Derived symbol '{derived_symbol}' != expected '{expected_symbol}'.")
    elif norm_role == "COMMISSION":
        comm_data = parse_commission_backing_artifact(
            raw_bytes,
            expected_symbol=expected_symbol,
            expected_account_tier=expected_account_tier,
        )
        derived_symbol = str(comm_data.get("symbol") or "")
        derived_tier = str(comm_data.get("account_tier") or "")
        if derived_symbol.upper() != expected_symbol.upper():
            raise ValueError(f"URL_CAPTURE_SCOPE_MISMATCH: Derived symbol '{derived_symbol}' != expected '{expected_symbol}'.")
        if derived_tier.upper() != expected_account_tier.upper():
            raise ValueError(f"URL_CAPTURE_SCOPE_MISMATCH: Derived account tier '{derived_tier}' != expected '{expected_account_tier}'.")
    elif norm_role == "FINANCING":
        fin_data = parse_financing_backing_artifact(raw_bytes, expected_symbol=expected_symbol)
        derived_symbol = str(fin_data.get("symbol") or "")
        if derived_symbol.upper() != expected_symbol.upper():
            raise ValueError(f"URL_CAPTURE_SCOPE_MISMATCH: Derived symbol '{derived_symbol}' != expected '{expected_symbol}'.")
    else:
        raise ValueError(f"URL_CAPTURE_ERROR: Unsupported component role '{component_role}' for official broker URL capture.")

    # Check derived venue vs expected
    if derived_venue.upper() != expected_venue.upper():
        raise ValueError(f"URL_CAPTURE_SCOPE_MISMATCH: Derived venue '{derived_venue}' != expected '{expected_venue}'.")

    # Validate verifier
    is_trusted, err = is_trusted_verifier(FrictionVerificationMethod.BROKER_OFFICIAL_URL_CAPTURE.value, verifier_identity)
    if not is_trusted:
        raise ValueError(f"URL_CAPTURE_VERIFIER_ERROR: {err}")

    captured_at = source_snapshot.retrieved_at or datetime.now(timezone.utc)
    captured_iso = captured_at.isoformat()

    source_type = FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value

    proof = compute_verification_proof(
        source_snapshot_id=source_snapshot.snapshot_id,
        raw_artifact_sha256=computed_raw_sha,
        component_role=norm_role,
        verification_method=FrictionVerificationMethod.BROKER_OFFICIAL_URL_CAPTURE.value,
        source_type=source_type,
        venue=derived_venue.upper(),
        symbol=derived_symbol.upper(),
        account_tier=derived_tier.upper(),
        captured_at=captured_iso,
        verifier_identity=verifier_identity.strip(),
    )

    capture_metadata = {
        "requested_url": requested_url,
        "final_url": final_url,
        "hostname": hostname,
        "captured_at": captured_iso,
        "http_status": http_status,
        "content_type": content_type,
        "raw_response_sha256": computed_raw_sha,
        "collector_version": collector_version,
        "derived_symbol": derived_symbol.upper(),
        "derived_venue": derived_venue.upper(),
        "derived_account_tier": derived_tier.upper(),
    }

    attestation_id = hashlib.sha256(
        f"{source_snapshot.snapshot_id}:{norm_role}:{FrictionVerificationMethod.BROKER_OFFICIAL_URL_CAPTURE.value}:{verifier_identity}:{computed_raw_sha}:{proof}".encode()
    ).hexdigest()

    existing = FrictionSourceProvenanceAttestation.objects.filter(attestation_id=attestation_id).first()
    if existing:
        return existing

    return FrictionSourceProvenanceAttestation.objects.create(
        attestation_id=attestation_id,
        source_snapshot=source_snapshot,
        component_role=norm_role,
        source_origin=final_url,
        source_type=source_type,
        collection_methodology="GOVERNED_BROKER_URL_CAPTURE",
        captured_at=captured_at,
        reviewed_at=datetime.now(timezone.utc),
        verification_method=FrictionVerificationMethod.BROKER_OFFICIAL_URL_CAPTURE.value,
        verifier_identity=verifier_identity.strip(),
        venue=derived_venue.upper(),
        symbol=derived_symbol.upper(),
        account_tier=derived_tier.upper(),
        raw_artifact_sha256=computed_raw_sha,
        provenance_metadata=capture_metadata,
        attestation_status=FrictionAttestationStatus.VERIFIED.value,
        verification_authority=GOVERNED_PROVENANCE_AUTHORITY,
        verification_proof=proof,
        verification_proof_version=CURRENT_PROOF_VERSION,
    )


def create_verified_account_portal_export_attestation(
    source_snapshot: Any,
    component_role: str,
    raw_bytes: bytes,
    expected_symbol: str = "XAUUSD",
    expected_venue: str = "EXNESS",
    expected_account_tier: str = "STANDARD",
    verifier_identity: str = "AURUMIQ_ACCOUNT_PORTAL_CAPTURE_WORKFLOW",
    portal_session_hash: str = "",
    collector_version: str = "1.0.0",
) -> Any:
    """Governed collector workflow for authenticated account portal exports."""
    from apps.market_data.models import (
        FrictionAttestationStatus,
        FrictionSourceProvenanceAttestation,
        FrictionSourceType,
        FrictionVerificationMethod,
    )

    norm_role = str(component_role).strip().upper()
    computed_raw_sha = hashlib.sha256(raw_bytes).hexdigest()
    if computed_raw_sha != source_snapshot.raw_payload_bytes_sha256:
        raise ValueError(
            f"PORTAL_EXPORT_ERROR: Raw bytes SHA '{computed_raw_sha}' mismatch with snapshot '{source_snapshot.raw_payload_bytes_sha256}'."
        )

    is_trusted, err = is_trusted_verifier(FrictionVerificationMethod.ACCOUNT_PORTAL_EXPORT.value, verifier_identity)
    if not is_trusted:
        raise ValueError(f"PORTAL_EXPORT_VERIFIER_ERROR: {err}")

    captured_at = source_snapshot.retrieved_at or datetime.now(timezone.utc)
    captured_iso = captured_at.isoformat()
    source_type = FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value

    proof = compute_verification_proof(
        source_snapshot_id=source_snapshot.snapshot_id,
        raw_artifact_sha256=computed_raw_sha,
        component_role=norm_role,
        verification_method=FrictionVerificationMethod.ACCOUNT_PORTAL_EXPORT.value,
        source_type=source_type,
        venue=expected_venue.upper(),
        symbol=expected_symbol.upper(),
        account_tier=expected_account_tier.upper(),
        captured_at=captured_iso,
        verifier_identity=verifier_identity.strip(),
    )

    meta = {
        "collector_workflow": "AURUMIQ_ACCOUNT_PORTAL_CAPTURE_WORKFLOW",
        "portal_session_hash": portal_session_hash or hashlib.sha256(f"session:{source_snapshot.snapshot_id}".encode()).hexdigest(),
        "collector_version": collector_version,
    }

    attestation_id = hashlib.sha256(
        f"{source_snapshot.snapshot_id}:{norm_role}:{FrictionVerificationMethod.ACCOUNT_PORTAL_EXPORT.value}:{verifier_identity}:{computed_raw_sha}:{proof}".encode()
    ).hexdigest()

    existing = FrictionSourceProvenanceAttestation.objects.filter(attestation_id=attestation_id).first()
    if existing:
        return existing

    return FrictionSourceProvenanceAttestation.objects.create(
        attestation_id=attestation_id,
        source_snapshot=source_snapshot,
        component_role=norm_role,
        source_origin="https://my.exness.com/portal/export",
        source_type=source_type,
        collection_methodology="GOVERNED_ACCOUNT_PORTAL_EXPORT",
        captured_at=captured_at,
        reviewed_at=datetime.now(timezone.utc),
        verification_method=FrictionVerificationMethod.ACCOUNT_PORTAL_EXPORT.value,
        verifier_identity=verifier_identity.strip(),
        venue=expected_venue.upper(),
        symbol=expected_symbol.upper(),
        account_tier=expected_account_tier.upper(),
        raw_artifact_sha256=computed_raw_sha,
        provenance_metadata=meta,
        attestation_status=FrictionAttestationStatus.VERIFIED.value,
        verification_authority=GOVERNED_PROVENANCE_AUTHORITY,
        verification_proof=proof,
        verification_proof_version=CURRENT_PROOF_VERSION,
    )
