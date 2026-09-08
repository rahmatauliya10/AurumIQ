"""Governed Provenance Authenticity and Cryptographic Attestation Engine.

Enforces fail-closed trust boundaries for XAUUSD empirical friction evidence:
- Rejects caller declarations and user JSON from self-authenticating.
- Mandates DECLARED vs. VERIFIED attestation status separation.
- Binds canonical provenance payloads into HMAC-SHA256 verification proofs.
- Restricts VERIFIED attestation creation to governed collector workflows.
- Derives evidence identity and scope from trusted collector outputs, not caller claims.
- Strictly isolates test seams from production runtime (prohibiting DEBUG=True authorization).
- Broker URL capture performs actual HTTP fetch with redirect-per-hop domain validation.
- MT5 export capture requires governed transport bridge (fail closed without one).
- Account portal export always DECLARED (no authenticated portal collector deployed).
- capture_context_hash binds method-specific capture metadata into HMAC proof.
- Signing secret is dedicated and fails closed in production if unset.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
from typing import Any, Dict, List, Optional, Set, Tuple
import urllib.parse
import urllib.request

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
    "ticks.ex2archive.com",
}

MAX_BROKER_RESPONSE_BYTES = 10 * 1024 * 1024  # 10 MB for normal broker documents
MAX_BROKER_REDIRECTS = 5
BROKER_CAPTURE_TIMEOUT_SECONDS = 30
MAX_TICK_ARCHIVE_RESPONSE_BYTES = 100 * 1024 * 1024  # 100 MB for official tick archives
TICK_ARCHIVE_CAPTURE_TIMEOUT_SECONDS = 120
BROKER_COLLECTOR_VERSION = "1.0.0"
MT5_COLLECTOR_VERSION = "1.0.0"

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
    """Retrieve dedicated application-controlled secret for provenance proof signing.

    - Non-test environments: PROVENANCE_SIGNING_SECRET MUST be set. Fails closed with
      RuntimeError if unset. No fallback to SECRET_KEY or static key.
    - Test environments: Uses PROVENANCE_SIGNING_SECRET if set, otherwise uses an
      explicit test-only sentinel key.
    """
    secret = getattr(settings, "PROVENANCE_SIGNING_SECRET", None)
    if is_test_environment():
        if not secret:
            secret = "aurumiq-test-only-provenance-key-NOT-FOR-PRODUCTION"
        return secret.encode("utf-8")
    if not secret or not str(secret).strip():
        raise RuntimeError(
            "PROVENANCE_SIGNING_SECRET is not configured. "
            "Production provenance proof creation requires a dedicated signing secret. "
            "Set PROVENANCE_SIGNING_SECRET in environment or Django settings. "
            "No fallback to SECRET_KEY or static key is permitted."
        )
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
    capture_context_hash: str = "",
) -> str:
    """Construct deterministic canonical key-value payload for cryptographic signing.

    Includes capture_context_hash when non-empty, binding method-specific capture
    metadata into the proof. Any capture metadata change invalidates the proof.
    """
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
    if capture_context_hash and capture_context_hash.strip():
        parts.append(f"capture_ctx={capture_context_hash.strip().lower()}")
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
    capture_context_hash: str = "",
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
        capture_context_hash=capture_context_hash,
    )
    secret = get_governed_signing_secret()
    return hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()


# --------------------------------------------------------------------------------------
# Capture Context Canonical Functions
# --------------------------------------------------------------------------------------

def canonicalize_broker_capture_context(metadata: dict) -> str:
    """Produce deterministic canonical context string from broker capture metadata.

    Covers all transport-derived fields. Keys are sorted alphabetically for determinism.
    redirect_chain preserves hop order (order is semantically significant).
    """
    redirect_chain = metadata.get("redirect_chain", [])
    if isinstance(redirect_chain, (list, tuple)):
        chain_repr = json.dumps(list(redirect_chain), separators=(",", ":"))
    else:
        chain_repr = str(redirect_chain)
    parts = [
        f"captured_at={str(metadata.get('captured_at', ''))}",
        f"collector_version={str(metadata.get('collector_version', ''))}",
        f"content_type={str(metadata.get('content_type', ''))}",
        f"final_url={str(metadata.get('final_url', ''))}",
        f"hostname={str(metadata.get('hostname', '')).lower()}",
        f"http_status={metadata.get('http_status', '')}",
        f"raw_response_sha256={str(metadata.get('raw_response_sha256', '')).lower()}",
        f"redirect_chain={chain_repr}",
        f"requested_url={str(metadata.get('requested_url', ''))}",
    ]
    return "|".join(parts)


def canonicalize_mt5_capture_context(metadata: dict) -> str:
    """Produce deterministic canonical context string from MT5 export metadata.

    Covers all governed receipt fields. Keys are sorted alphabetically for determinism.
    """
    parts = [
        f"broker={str(metadata.get('broker', '')).upper()}",
        f"capture_id={str(metadata.get('capture_id', ''))}",
        f"capture_time={str(metadata.get('capture_time', ''))}",
        f"collector_version={str(metadata.get('collector_version', ''))}",
        f"derived_account_tier={str(metadata.get('derived_account_tier', '')).upper()}",
        f"derived_symbol={str(metadata.get('derived_symbol', '')).upper()}",
        f"export_type={str(metadata.get('export_type', '')).upper()}",
        f"raw_sha256={str(metadata.get('raw_sha256', '')).lower()}",
        f"server={str(metadata.get('server', ''))}",
        f"terminal_version={str(metadata.get('terminal_version', ''))}",
    ]
    return "|".join(parts)


def compute_capture_context_hash(metadata: dict, verification_method: str) -> str:
    """Compute SHA-256 of canonical capture context for a given verification method.

    Returns empty string for methods without governed capture context.
    The hash is RECOMPUTED from actual metadata fields — never trusted from storage.
    """
    if not metadata:
        return ""
    method_upper = str(verification_method).strip().upper()
    if method_upper == "BROKER_OFFICIAL_URL_CAPTURE":
        # Only compute if metadata contains broker capture fields
        if not metadata.get("requested_url") and not metadata.get("final_url"):
            return ""
        canonical = canonicalize_broker_capture_context(metadata)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    elif method_upper == "MT5_DIRECT_EXPORT":
        # Only compute if metadata contains MT5 capture fields
        if not metadata.get("server") and not metadata.get("export_type"):
            return ""
        canonical = canonicalize_mt5_capture_context(metadata)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return ""


# --------------------------------------------------------------------------------------
# Proof & Authenticity Verification
# --------------------------------------------------------------------------------------

def verify_attestation_proof(attestation: Any) -> Tuple[bool, Optional[str]]:
    """Verify cryptographic HMAC-SHA256 verification proof on an attestation record.

    Recomputes capture_context_hash from actual provenance_metadata (never trusts
    the stored capture_context_hash field). Any metadata mutation invalidates proof.
    """
    proof = getattr(attestation, "verification_proof", "")
    if not proof or not str(proof).strip():
        return False, "Attestation record lacks verification proof."

    # Recompute capture_context_hash from actual metadata
    meta = getattr(attestation, "provenance_metadata", {}) or {}
    method = str(getattr(attestation, "verification_method", ""))
    recomputed_ctx_hash = compute_capture_context_hash(meta, method)

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
        capture_context_hash=recomputed_ctx_hash,
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
    6. ACCOUNT_PORTAL_EXPORT CANNOT be VERIFIED outside test environment (no authenticated portal collector yet).
    7. BROKER_OFFICIAL_URL_CAPTURE MUST contain authentic capture telemetry and allowed broker domain.
    8. Capture context hash MUST match recomputed hash from actual metadata.
    9. Cryptographic proof MUST match canonical provenance payload (including recomputed capture_context_hash).
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

    # Account portal export CANNOT be VERIFIED outside test environment (no authenticated portal collector)
    if method == FrictionVerificationMethod.ACCOUNT_PORTAL_EXPORT.value:
        if not is_test_environment():
            return False, "ACCOUNT_PORTAL_EXPORT cannot be VERIFIED outside test environment until an authenticated portal collector is deployed."

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
            parsed_host = urllib.parse.urlparse(final_url or req_url).hostname
            hostname = (parsed_host or "").lower()

        if hostname not in PERMITTED_BROKER_DOMAINS:
            return False, f"Broker URL capture hostname '{hostname}' is not in permitted broker domains: {sorted(PERMITTED_BROKER_DOMAINS)}."

        if http_status != 200:
            return False, f"Broker URL capture HTTP status is {http_status} (expected 200)."

        if not resp_sha or resp_sha != str(getattr(attestation, "raw_artifact_sha256", "")).lower():
            return False, "Broker URL capture raw_response_sha256 mismatch with attestation raw artifact SHA."

        if not col_ver:
            return False, "Broker URL capture metadata missing collector_version."

    # Capture context hash integrity verification (recompute, don't trust stored)
    meta = getattr(attestation, "provenance_metadata", {}) or {}
    stored_ctx_hash = str(meta.get("capture_context_hash", "") or "")
    if stored_ctx_hash:
        recomputed_ctx_hash = compute_capture_context_hash(meta, method)
        if recomputed_ctx_hash != stored_ctx_hash:
            return False, (
                "Capture context hash integrity violation: stored capture_context_hash does not match "
                "recomputed hash from actual capture metadata. Metadata may have been tampered."
            )

    # Cryptographic proof verification (uses RECOMPUTED capture_context_hash)
    is_proof_valid, proof_err = verify_attestation_proof(attestation)
    if not is_proof_valid:
        return False, proof_err

    return True, None


# --------------------------------------------------------------------------------------
# Broker URL Capture — Governed HTTP Transport Boundary
# --------------------------------------------------------------------------------------

RECEIPT_DOMAIN_BROKER = "AURUMIQ_BROKER_RECEIPT_V1"
RECEIPT_DOMAIN_MT5 = "AURUMIQ_MT5_RECEIPT_V1"


def compute_broker_receipt_auth_tag(
    requested_url: str,
    final_url: str,
    http_status: int,
    content_type: str,
    response_sha256: str,
    captured_at_iso: str,
    collector_version: str,
    redirect_chain: tuple,
) -> str:
    """Compute cryptographic authentication tag for governed broker URL capture receipt.

    Domain-separated: AURUMIQ_BROKER_RECEIPT_V1.
    Uses HMAC-SHA256 with application-controlled provenance signing secret.
    """
    redirect_repr = json.dumps(list(redirect_chain), separators=(",", ":")) if redirect_chain else "[]"
    parts = [
        f"domain={RECEIPT_DOMAIN_BROKER}",
        f"req_url={requested_url.strip()}",
        f"final_url={final_url.strip()}",
        f"status={http_status}",
        f"content_type={content_type.strip()}",
        f"sha256={response_sha256.strip().lower()}",
        f"captured={captured_at_iso.strip()}",
        f"collector_ver={collector_version.strip()}",
        f"redirects={redirect_repr}",
    ]
    canonical = "|".join(parts)
    secret = get_governed_signing_secret()
    return hmac.new(secret, canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_broker_receipt_auth_tag(receipt: Any) -> bool:
    """Verify cryptographic authentication tag on BrokerCaptureReceipt.

    Recomputes expected tag over canonical receipt payload and compares with hmac.compare_digest.
    """
    tag = getattr(receipt, "receipt_auth_tag", "") or ""
    if not tag or not isinstance(tag, str) or not tag.strip():
        return False
    captured_at = getattr(receipt, "captured_at", None)
    captured_iso = captured_at.isoformat() if hasattr(captured_at, "isoformat") else str(captured_at)
    expected = compute_broker_receipt_auth_tag(
        requested_url=str(getattr(receipt, "requested_url", "")),
        final_url=str(getattr(receipt, "final_url", "")),
        http_status=int(getattr(receipt, "http_status", 0)),
        content_type=str(getattr(receipt, "content_type", "")),
        response_sha256=str(getattr(receipt, "response_sha256", "")),
        captured_at_iso=captured_iso,
        collector_version=str(getattr(receipt, "collector_version", "")),
        redirect_chain=tuple(getattr(receipt, "redirect_chain", ()) or ()),
    )
    return hmac.compare_digest(tag.strip().lower(), expected.strip().lower())


@dataclass(frozen=True)
class BrokerCaptureReceipt:
    """Immutable transport receipt from governed broker URL capture.

    All fields are derived from the actual HTTP transport — none are caller-supplied.
    Carries receipt_auth_tag computed by execute_governed_broker_url_capture().
    """
    requested_url: str
    final_url: str
    http_status: int
    content_type: str
    response_bytes: bytes
    response_sha256: str
    captured_at: datetime
    collector_version: str
    redirect_chain: tuple  # tuple of intermediate redirect URLs
    receipt_auth_tag: str = ""


def _validate_broker_url(url: str, *, context: str = "BROKER_CAPTURE_ERROR") -> None:
    """Validate a URL against broker capture security requirements.

    HTTPS only, no userinfo credentials, hostname in PERMITTED_BROKER_DOMAINS.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"{context}: Non-HTTPS URL '{url}'. Only HTTPS is permitted for broker captures.")
    if parsed.username or parsed.password:
        raise ValueError(f"{context}: URL contains userinfo credentials: '{url}'.")
    hostname = parsed.hostname
    if not hostname or hostname.lower() not in PERMITTED_BROKER_DOMAINS:
        raise ValueError(
            f"{context}: Hostname '{hostname}' is not in permitted broker domains: {sorted(PERMITTED_BROKER_DOMAINS)}."
        )


class _GovernedBrokerRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Custom redirect handler that validates every redirect hop against permitted broker domains.

    Enforces HTTPS-only, no-userinfo, permitted-domain for each intermediate redirect.
    Tracks the full redirect chain for provenance metadata.
    """

    def __init__(self):
        self.redirect_chain: List[str] = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Validate redirect target before following."""
        _validate_broker_url(newurl, context="BROKER_CAPTURE_REDIRECT_ERROR")
        if len(self.redirect_chain) >= MAX_BROKER_REDIRECTS:
            raise ValueError(
                f"BROKER_CAPTURE_REDIRECT_ERROR: Exceeded maximum redirects ({MAX_BROKER_REDIRECTS})."
            )
        self.redirect_chain.append(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def execute_governed_broker_url_capture(
    url: str,
    *,
    http_client=None,
) -> BrokerCaptureReceipt:
    """Execute governed HTTP capture from a broker URL.

    Performs the actual HTTP fetch with full transport governance:
    - HTTPS only, no userinfo credentials.
    - Every redirect hop validated against PERMITTED_BROKER_DOMAINS.
    - Response size bounded (MAX_BROKER_RESPONSE_BYTES).
    - Redirect count bounded (MAX_BROKER_REDIRECTS).
    - Explicit timeout (BROKER_CAPTURE_TIMEOUT_SECONDS).
    - Response SHA-256 computed from actual transport bytes.
    - Cryptographic receipt_auth_tag computed over canonical fields.

    http_client: Test-only injection. Outside explicit test environment,
    caller-supplied http_client is strictly prohibited (raises PermissionError).
    """
    # 1. Validate initial URL (always runs)
    _validate_broker_url(url)

    # Test transport injection is strictly test-only
    if http_client is not None and not is_test_environment():
        raise PermissionError(
            "BROKER_CAPTURE_ERROR: http_client mock transport injection is strictly prohibited outside test environment."
        )

    captured_at = datetime.now(timezone.utc)

    initial_parsed = urllib.parse.urlparse(url)
    initial_hostname = (initial_parsed.hostname or "").lower()
    if initial_hostname == "ticks.ex2archive.com":
        transport_timeout = TICK_ARCHIVE_CAPTURE_TIMEOUT_SECONDS
        transport_max_bytes = MAX_TICK_ARCHIVE_RESPONSE_BYTES
    else:
        transport_timeout = BROKER_CAPTURE_TIMEOUT_SECONDS
        transport_max_bytes = MAX_BROKER_RESPONSE_BYTES

    if http_client is not None:
        # Test path: mock transport returns structured result
        result = http_client(url)
        response_bytes = result[0]
        final_url = result[1]
        http_status = result[2]
        content_type = result[3]
        redirect_chain = list(result[4] or [])
    else:
        # Production path: real HTTP with governed redirect handler
        redirect_handler = _GovernedBrokerRedirectHandler()
        opener = urllib.request.build_opener(redirect_handler)
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 AurumIQ-GovernedBrokerCapture/1.0")
        response = opener.open(req, timeout=transport_timeout)
        final_url = response.url
        http_status = response.status
        content_type = response.headers.get("Content-Type", "")
        response_bytes = response.read(transport_max_bytes + 1)
        redirect_chain = list(redirect_handler.redirect_chain)

    # 2. Post-transport validation (always runs — exercises validation logic in tests too)
    # Determine effective maximum response bytes based on validated final hostname
    final_parsed = urllib.parse.urlparse(final_url)
    final_hostname = (final_parsed.hostname or "").lower()
    effective_max_bytes = (
        MAX_TICK_ARCHIVE_RESPONSE_BYTES
        if final_hostname == "ticks.ex2archive.com"
        else MAX_BROKER_RESPONSE_BYTES
    )

    # Response size bound
    if len(response_bytes) > effective_max_bytes:
        raise ValueError(
            f"BROKER_CAPTURE_ERROR: Response size ({len(response_bytes)} bytes) exceeds maximum "
            f"permitted size ({effective_max_bytes} bytes)."
        )

    # Redirect count bound
    if len(redirect_chain) > MAX_BROKER_REDIRECTS:
        raise ValueError(
            f"BROKER_CAPTURE_ERROR: Redirect chain length ({len(redirect_chain)}) exceeds maximum "
            f"permitted redirects ({MAX_BROKER_REDIRECTS})."
        )

    # Validate every redirect hop
    for redir_url in redirect_chain:
        _validate_broker_url(redir_url, context="BROKER_CAPTURE_REDIRECT_ERROR")

    # Validate final URL
    _validate_broker_url(final_url, context="BROKER_CAPTURE_ERROR")

    # 3. Build immutable receipt with authenticated receipt tag
    response_sha256 = hashlib.sha256(response_bytes).hexdigest()
    captured_iso = captured_at.isoformat()
    collector_version = BROKER_COLLECTOR_VERSION
    redirect_chain_tuple = tuple(redirect_chain)

    receipt_auth_tag = compute_broker_receipt_auth_tag(
        requested_url=url,
        final_url=final_url,
        http_status=http_status,
        content_type=content_type,
        response_sha256=response_sha256,
        captured_at_iso=captured_iso,
        collector_version=collector_version,
        redirect_chain=redirect_chain_tuple,
    )

    return BrokerCaptureReceipt(
        requested_url=url,
        final_url=final_url,
        http_status=http_status,
        content_type=content_type,
        response_bytes=response_bytes,
        response_sha256=response_sha256,
        captured_at=captured_at,
        collector_version=collector_version,
        redirect_chain=redirect_chain_tuple,
        receipt_auth_tag=receipt_auth_tag,
    )


def create_verified_broker_capture_attestation(
    source_snapshot: Any,
    component_role: str,
    capture_receipt: BrokerCaptureReceipt,
    expected_symbol: str = "XAUUSD",
    expected_venue: str = "EXNESS",
    expected_account_tier: str = "STANDARD",
    verifier_identity: str = "AURUMIQ_OFFICIAL_BROKER_URL_CAPTURE_WORKFLOW",
    expected_broker_symbol: Optional[str] = None,
) -> Any:
    """Create VERIFIED broker URL capture attestation from governed capture receipt.

    The receipt MUST come from execute_governed_broker_url_capture(). The receipt's
    receipt_auth_tag is independently verified. The receipt response_bytes SHA-256
    must match the source_snapshot raw SHA.
    Derives and validates scope from parsed document structure.
    Computes capture_context_hash from receipt metadata and includes in HMAC proof.
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

    # 0. Authenticate capture receipt tag (domain separation + HMAC verification)
    if not getattr(capture_receipt, "receipt_auth_tag", "") or not verify_broker_receipt_auth_tag(capture_receipt):
        raise ValueError(
            "BROKER_CAPTURE_ERROR: BrokerCaptureReceipt authentication tag is invalid or missing. "
            "Caller-constructed or tampered receipts cannot create VERIFIED attestation. "
            "Receipt must originate from execute_governed_broker_url_capture()."
        )

    # Validate receipt SHA matches snapshot raw SHA (ensures receipt bytes == snapshot bytes)
    if capture_receipt.response_sha256 != source_snapshot.raw_payload_bytes_sha256:
        raise ValueError(
            f"URL_CAPTURE_ERROR: Receipt response SHA '{capture_receipt.response_sha256}' does not match "
            f"snapshot raw_payload_bytes_sha256 '{source_snapshot.raw_payload_bytes_sha256}'. "
            f"Snapshot must be created from receipt.response_bytes."
        )

    # Double-verify actual bytes SHA
    computed_raw_sha = hashlib.sha256(capture_receipt.response_bytes).hexdigest()
    if computed_raw_sha != source_snapshot.raw_payload_bytes_sha256:
        raise ValueError(
            f"URL_CAPTURE_ERROR: Computed response bytes SHA '{computed_raw_sha}' mismatch with "
            f"snapshot '{source_snapshot.raw_payload_bytes_sha256}'."
        )

    # HTTP status validation
    if capture_receipt.http_status != 200:
        raise ValueError(f"URL_CAPTURE_ERROR: HTTP status {capture_receipt.http_status} != 200.")

    hostname = urllib.parse.urlparse(capture_receipt.final_url).hostname or ""
    hostname = hostname.lower()

    # Scope derivation via authoritative parser
    derived_symbol = expected_symbol
    derived_venue = "EXNESS"
    derived_tier = expected_account_tier

    if norm_role == "LEGAL_ENTITY":
        legal_data = parse_legal_entity_backing_artifact(capture_receipt.response_bytes)
        code = str(legal_data.get("legal_entity_code") or "")
        if "EXNESS" not in code.upper():
            raise ValueError(f"URL_CAPTURE_SCOPE_MISMATCH: Derived legal entity '{code}' is not Exness.")
    elif norm_role == "CONTRACT_SPEC":
        spec_data = parse_contract_spec_backing_artifact(
            capture_receipt.response_bytes,
            expected_symbol=expected_symbol,
            expected_broker_symbol=expected_broker_symbol,
            expected_account_tier=expected_account_tier,
        )
        derived_symbol = str(spec_data.get("symbol") or "")
        derived_broker_symbol = str(spec_data.get("broker_symbol") or expected_broker_symbol or "")
        if derived_symbol.upper() != expected_symbol.upper():
            raise ValueError(
                f"URL_CAPTURE_SCOPE_MISMATCH: Derived symbol '{derived_symbol}' != expected '{expected_symbol}'."
            )
        if expected_broker_symbol and derived_broker_symbol.upper() != expected_broker_symbol.upper():
            raise ValueError(
                f"URL_CAPTURE_SCOPE_MISMATCH: Derived broker symbol '{derived_broker_symbol}' != expected '{expected_broker_symbol}'."
            )
    elif norm_role == "COMMISSION":
        comm_data = parse_commission_backing_artifact(
            capture_receipt.response_bytes,
            expected_symbol=expected_symbol,
            expected_account_tier=expected_account_tier,
            expected_broker_symbol=expected_broker_symbol,
        )
        derived_symbol = str(comm_data.get("symbol") or "")
        derived_tier = str(comm_data.get("account_tier") or "")
        if derived_symbol.upper() != expected_symbol.upper() and derived_symbol.upper() != "ALL":
            raise ValueError(
                f"URL_CAPTURE_SCOPE_MISMATCH: Derived symbol '{derived_symbol}' != expected '{expected_symbol}'."
            )
        if derived_tier.upper() != expected_account_tier.upper():
            raise ValueError(
                f"URL_CAPTURE_SCOPE_MISMATCH: Derived account tier '{derived_tier}' != expected '{expected_account_tier}'."
            )
    elif norm_role == "FINANCING":
        fin_data = parse_financing_backing_artifact(
            capture_receipt.response_bytes,
            expected_symbol=expected_symbol,
            expected_broker_symbol=expected_broker_symbol,
            expected_account_tier=expected_account_tier,
        )
        derived_symbol = str(fin_data.get("symbol") or "")
        if derived_symbol.upper() != expected_symbol.upper():
            raise ValueError(
                f"URL_CAPTURE_SCOPE_MISMATCH: Derived symbol '{derived_symbol}' != expected '{expected_symbol}'."
            )
    elif norm_role == "SPREAD_DATASET":
        from apps.market_data.friction.tick_parser import parse_exness_official_tick_history
        ticks_data, summary = parse_exness_official_tick_history(
            capture_receipt.response_bytes,
            expected_symbol=expected_symbol,
            expected_broker_symbol=expected_broker_symbol,
            expected_account_tier=expected_account_tier,
        )
        derived_symbol = str(summary.get("symbol") or expected_symbol)
        derived_broker_symbol = str(summary.get("broker_symbol") or expected_broker_symbol or "")
        derived_tier = str(expected_account_tier)
        derived_venue = str(summary.get("venue") or "EXNESS")
        if derived_symbol.upper() != expected_symbol.upper():
            raise ValueError(
                f"URL_CAPTURE_SCOPE_MISMATCH: Derived symbol '{derived_symbol}' != expected '{expected_symbol}'."
            )
        if expected_broker_symbol and derived_broker_symbol.upper() != expected_broker_symbol.upper():
            raise ValueError(
                f"URL_CAPTURE_SCOPE_MISMATCH: Derived broker symbol '{derived_broker_symbol}' != expected '{expected_broker_symbol}'."
            )
    else:
        raise ValueError(
            f"URL_CAPTURE_ERROR: Unsupported component role '{component_role}' for official broker URL capture."
        )

    # Derived venue validation
    if derived_venue.upper() != expected_venue.upper():
        raise ValueError(
            f"URL_CAPTURE_SCOPE_MISMATCH: Derived venue '{derived_venue}' != expected '{expected_venue}'."
        )

    # Validate verifier
    is_trusted, err = is_trusted_verifier(
        FrictionVerificationMethod.BROKER_OFFICIAL_URL_CAPTURE.value, verifier_identity
    )
    if not is_trusted:
        raise ValueError(f"URL_CAPTURE_VERIFIER_ERROR: {err}")

    captured_at = capture_receipt.captured_at
    captured_iso = captured_at.isoformat()

    if norm_role == "SPREAD_DATASET":
        source_type = FrictionSourceType.EXNESS_OFFICIAL_TICK_HISTORY.value
        collection_method = "EXNESS_OFFICIAL_TICK_ARCHIVE"
    else:
        source_type = FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value
        collection_method = "GOVERNED_BROKER_URL_CAPTURE"

    if source_snapshot.source_type not in (source_type, FrictionSourceType.USER_PROVIDED_UNVERIFIED.value):
        raise ValueError(
            f"URL_CAPTURE_ERROR: Snapshot source_type '{source_snapshot.source_type}' mismatch with "
            f"expected '{source_type}' for role '{norm_role}'."
        )

    # Build provenance metadata with all capture context fields
    capture_metadata = {
        "requested_url": capture_receipt.requested_url,
        "final_url": capture_receipt.final_url,
        "redirect_chain": list(capture_receipt.redirect_chain),
        "hostname": hostname,
        "captured_at": captured_iso,
        "http_status": capture_receipt.http_status,
        "content_type": capture_receipt.content_type,
        "raw_response_sha256": computed_raw_sha,
        "collector_version": capture_receipt.collector_version,
        "derived_symbol": derived_symbol.upper(),
        "derived_venue": derived_venue.upper(),
        "derived_account_tier": derived_tier.upper(),
    }

    # Compute capture context hash from metadata
    ctx_hash = compute_capture_context_hash(capture_metadata, FrictionVerificationMethod.BROKER_OFFICIAL_URL_CAPTURE.value)
    capture_metadata["capture_context_hash"] = ctx_hash

    # Compute HMAC proof with capture_context_hash
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
        capture_context_hash=ctx_hash,
    )

    attestation_id = hashlib.sha256(
        f"{source_snapshot.snapshot_id}:{norm_role}:"
        f"{FrictionVerificationMethod.BROKER_OFFICIAL_URL_CAPTURE.value}:"
        f"{verifier_identity}:{computed_raw_sha}:{proof}".encode()
    ).hexdigest()

    existing = FrictionSourceProvenanceAttestation.objects.filter(attestation_id=attestation_id).first()
    if existing:
        return existing

    return FrictionSourceProvenanceAttestation.objects.create(
        attestation_id=attestation_id,
        source_snapshot=source_snapshot,
        component_role=norm_role,
        source_origin=capture_receipt.final_url,
        source_type=source_type,
        collection_methodology=collection_method,
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


# --------------------------------------------------------------------------------------
# MT5 Export — Governed Terminal Bridge Transport Boundary
# --------------------------------------------------------------------------------------

def compute_mt5_receipt_auth_tag(
    server: str,
    broker: str,
    symbol: str,
    account_tier: str,
    terminal_version: str,
    export_type: str,
    collector_version: str,
    capture_id: str,
    capture_time_iso: str,
    raw_sha256: str,
) -> str:
    """Compute cryptographic authentication tag for governed MT5 export receipt.

    Domain-separated: AURUMIQ_MT5_RECEIPT_V1.
    Uses HMAC-SHA256 with application-controlled provenance signing secret.
    """
    parts = [
        f"domain={RECEIPT_DOMAIN_MT5}",
        f"server={server.strip().upper()}",
        f"broker={broker.strip().upper()}",
        f"symbol={symbol.strip().upper()}",
        f"tier={account_tier.strip().upper()}",
        f"terminal_ver={terminal_version.strip()}",
        f"export_type={export_type.strip().upper()}",
        f"collector_ver={collector_version.strip()}",
        f"capture_id={capture_id.strip()}",
        f"capture_time={capture_time_iso.strip()}",
        f"raw_sha={raw_sha256.strip().lower()}",
    ]
    canonical = "|".join(parts)
    secret = get_governed_signing_secret()
    return hmac.new(secret, canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_mt5_receipt_auth_tag(receipt: Any) -> bool:
    """Verify cryptographic authentication tag on MT5ExportReceipt.

    Recomputes expected tag over canonical receipt payload and compares with hmac.compare_digest.
    """
    tag = getattr(receipt, "receipt_auth_tag", "") or ""
    if not tag or not isinstance(tag, str) or not tag.strip():
        return False
    capture_time = getattr(receipt, "capture_time", None)
    cap_time_iso = capture_time.isoformat() if hasattr(capture_time, "isoformat") else str(capture_time)
    expected = compute_mt5_receipt_auth_tag(
        server=str(getattr(receipt, "server", "")),
        broker=str(getattr(receipt, "broker", "")),
        symbol=str(getattr(receipt, "symbol", "")),
        account_tier=str(getattr(receipt, "account_tier", "")),
        terminal_version=str(getattr(receipt, "terminal_version", "")),
        export_type=str(getattr(receipt, "export_type", "")),
        collector_version=str(getattr(receipt, "collector_version", "")),
        capture_id=str(getattr(receipt, "capture_id", "")),
        capture_time_iso=cap_time_iso,
        raw_sha256=str(getattr(receipt, "raw_sha256", "")),
    )
    return hmac.compare_digest(tag.strip().lower(), expected.strip().lower())


@dataclass(frozen=True)
class MT5ExportReceipt:
    """Immutable receipt from governed MT5 export capture transport.

    All fields are derived from the governed MT5 terminal bridge / collector output.
    Cannot be freely constructed by callers — must come from execute_governed_mt5_export_capture().
    Carries receipt_auth_tag computed by execute_governed_mt5_export_capture().
    """
    server: str
    broker: str
    symbol: str
    account_tier: str
    terminal_version: str
    export_type: str
    collector_version: str
    capture_id: str
    capture_time: datetime
    raw_bytes: bytes
    raw_sha256: str
    receipt_auth_tag: str = ""


def _get_production_mt5_bridge():
    """Internal resolver for production MT5 bridge adapter.

    Returns None until a real internally configured bridge exists.
    Never accepts caller/CLI parameters.
    """
    return None


def execute_governed_mt5_export_capture(
    raw_export_bytes: bytes,
    component_role: str,
    *,
    mt5_transport=None,
    expected_symbol: str = "XAUUSD",
    expected_venue: str = "EXNESS",
    expected_account_tier: str = "STANDARD",
) -> MT5ExportReceipt:
    """Execute governed MT5 export capture through trusted transport bridge.

    mt5_transport: Test-only injection. Outside explicit test environment,
    caller-supplied mt5_transport is strictly prohibited (raises PermissionError).

    In production, actual_transport resolves via internal bridge adapter (_get_production_mt5_bridge).
    Since no internal bridge is configured yet, production raises RuntimeError("MT5_TRANSPORT_NOT_AVAILABLE").

    The governed transport MUST explicitly provide all required values:
    server, broker, symbol, terminal_version, export_type, collector_version,
    capture_id, capture_time (datetime).
    None may be synthesized or defaulted.
    raw_sha256 is independently computed from raw_export_bytes.
    """
    if mt5_transport is not None and not is_test_environment():
        raise PermissionError(
            "MT5_TRANSPORT_ERROR: mt5_transport mock injection is strictly prohibited outside test environment."
        )

    actual_transport = mt5_transport if is_test_environment() else _get_production_mt5_bridge()
    if actual_transport is None:
        raise RuntimeError(
            "MT5_TRANSPORT_NOT_AVAILABLE: No governed MT5 export transport bridge is configured. "
            "MT5_DIRECT_EXPORT requires a real governed MT5 terminal bridge or collector. "
            "Without a transport bridge, MT5 evidence must remain DECLARED."
        )

    # Transport derives all fields from its output
    derived = actual_transport(raw_export_bytes, component_role)
    if not isinstance(derived, dict):
        raise ValueError("MT5_TRANSPORT_ERROR: Transport must return a dict of derived fields.")

    # Validate all required transport-derived fields — no synthesis, no defaulting!
    for required_field in (
        "server",
        "broker",
        "symbol",
        "terminal_version",
        "export_type",
        "collector_version",
        "capture_id",
        "capture_time",
    ):
        val = derived.get(required_field)
        if val is None or (isinstance(val, str) and not val.strip()):
            raise ValueError(
                f"MT5_TRANSPORT_ERROR: Governed transport output missing required field '{required_field}'."
            )

    capture_time = derived["capture_time"]
    if not isinstance(capture_time, datetime):
        raise ValueError(
            "MT5_TRANSPORT_ERROR: Governed transport field 'capture_time' must be a datetime instance."
        )

    server = str(derived["server"]).strip()
    broker = str(derived["broker"]).strip()
    symbol = str(derived["symbol"]).strip()
    account_tier = str(derived.get("account_tier") or "").strip()
    terminal_version = str(derived["terminal_version"]).strip()
    export_type = str(derived["export_type"]).strip()
    collector_version = str(derived["collector_version"]).strip()
    capture_id = str(derived["capture_id"]).strip()

    raw_sha = hashlib.sha256(raw_export_bytes).hexdigest()
    cap_time_iso = capture_time.isoformat()

    receipt_auth_tag = compute_mt5_receipt_auth_tag(
        server=server,
        broker=broker,
        symbol=symbol,
        account_tier=account_tier,
        terminal_version=terminal_version,
        export_type=export_type,
        collector_version=collector_version,
        capture_id=capture_id,
        capture_time_iso=cap_time_iso,
        raw_sha256=raw_sha,
    )

    receipt = MT5ExportReceipt(
        server=server,
        broker=broker,
        symbol=symbol,
        account_tier=account_tier,
        terminal_version=terminal_version,
        export_type=export_type,
        collector_version=collector_version,
        capture_id=capture_id,
        capture_time=capture_time,
        raw_bytes=raw_export_bytes,
        raw_sha256=raw_sha,
        receipt_auth_tag=receipt_auth_tag,
    )

    # Validate derived scope matches expected (expected_* are comparison targets only)
    if receipt.symbol.upper() != expected_symbol.upper():
        raise ValueError(
            f"MT5_SCOPE_MISMATCH: Transport derived symbol '{receipt.symbol}' does not match "
            f"expected '{expected_symbol}'."
        )
    if receipt.broker.upper() != expected_venue.upper():
        raise ValueError(
            f"MT5_SCOPE_MISMATCH: Transport derived broker '{receipt.broker}' does not match "
            f"expected '{expected_venue}'."
        )
    if receipt.account_tier and expected_account_tier:
        if receipt.account_tier.upper() != expected_account_tier.upper():
            raise ValueError(
                f"MT5_SCOPE_MISMATCH: Transport derived account tier '{receipt.account_tier}' does not match "
                f"expected '{expected_account_tier}'."
            )

    return receipt


def create_verified_mt5_export_attestation(
    source_snapshot: Any,
    component_role: str,
    capture_receipt: MT5ExportReceipt,
    expected_symbol: str = "XAUUSD",
    expected_venue: str = "EXNESS",
    expected_account_tier: str = "STANDARD",
    verifier_identity: str = "AURUMIQ_MT5_COLLECTOR_V1",
    expected_broker_symbol: Optional[str] = None,
) -> Any:
    """Create VERIFIED MT5 export attestation from governed capture receipt.

    The receipt MUST come from execute_governed_mt5_export_capture(). The receipt's
    receipt_auth_tag is independently verified. Independently runs component-specific
    parsers on snapshot raw content as cross-verification.
    Computes capture_context_hash from receipt metadata and includes in HMAC proof.
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

    # 0. Authenticate capture receipt tag (domain separation + HMAC verification)
    if not getattr(capture_receipt, "receipt_auth_tag", "") or not verify_mt5_receipt_auth_tag(capture_receipt):
        raise ValueError(
            "MT5_COLLECTOR_ERROR: MT5ExportReceipt authentication tag is invalid or missing. "
            "Caller-constructed or tampered receipts cannot create VERIFIED attestation. "
            "Receipt must originate from execute_governed_mt5_export_capture()."
        )

    # Validate receipt SHA matches snapshot raw SHA
    if capture_receipt.raw_sha256 != source_snapshot.raw_payload_bytes_sha256:
        raise ValueError(
            f"MT5_COLLECTOR_ERROR: Receipt raw SHA '{capture_receipt.raw_sha256}' mismatch with "
            f"snapshot '{source_snapshot.raw_payload_bytes_sha256}'. "
            f"Snapshot must be created from receipt.raw_bytes."
        )

    # Double-verify actual bytes SHA
    computed_raw_sha = hashlib.sha256(capture_receipt.raw_bytes).hexdigest()
    if computed_raw_sha != source_snapshot.raw_payload_bytes_sha256:
        raise ValueError(
            f"MT5_COLLECTOR_ERROR: Computed raw bytes SHA '{computed_raw_sha}' mismatch with "
            f"snapshot '{source_snapshot.raw_payload_bytes_sha256}'."
        )

    # Independent parser cross-verification
    derived_symbol: str = ""
    derived_venue: str = capture_receipt.broker.upper()
    # Derived account tier comes ONLY from receipt — NEVER default to expected_account_tier
    derived_account_tier: str = str(capture_receipt.account_tier or "").strip().upper()
    source_type: str = ""

    if norm_role == "SPREAD_DATASET":
        ticks_data, summary = parse_mt5_tick_export(
            capture_receipt.raw_bytes,
            expected_symbol=expected_symbol,
            expected_broker_symbol=expected_broker_symbol,
            expected_account_tier=expected_account_tier,
        )
        derived_symbol = str(summary.get("symbol") or "")
        source_type = FrictionSourceType.MT5_TICK_HISTORY_EXPORT.value
        # For tick history, account tier is required by model scope
        if not derived_account_tier:
            raise ValueError(
                f"MT5_SCOPE_MISMATCH: Account tier is required for component role '{component_role}' "
                f"but was not derived from MT5 transport receipt."
            )
    elif norm_role == "SLIPPAGE_DATASET":
        telemetry_records, summary = parse_mt5_execution_telemetry(
            capture_receipt.raw_bytes,
            expected_venue=expected_venue,
            expected_symbol=expected_symbol,
            expected_account_tier=expected_account_tier,
            expected_broker_symbol=expected_broker_symbol,
        )
        derived_symbol = str(summary.get("symbol") or "")
        parser_tier = str(summary.get("account_tier") or "").strip().upper()
        if not derived_account_tier:
            raise ValueError(
                f"MT5_SCOPE_MISMATCH: Account tier is required for component role '{component_role}' "
                f"but was not derived from MT5 transport receipt."
            )
        if parser_tier and parser_tier != derived_account_tier:
            raise ValueError(
                f"MT5_CROSS_VERIFICATION_FAILED: Parser derived account tier '{parser_tier}' does not match "
                f"receipt transport-derived tier '{derived_account_tier}'."
            )
        source_type = FrictionSourceType.MT5_EXECUTION_TELEMETRY_EXPORT.value
    elif norm_role == "CONTRACT_SPEC":
        parsed = parse_contract_spec_backing_artifact(
            capture_receipt.raw_bytes,
            expected_symbol=expected_symbol,
            expected_broker_symbol=expected_broker_symbol,
            expected_account_tier=expected_account_tier,
        )
        derived_symbol = str(parsed.get("symbol") or "")
        source_type = FrictionSourceType.MT5_SYMBOL_INFO_EXPORT.value
    else:
        raise ValueError(
            f"MT5_COLLECTOR_ERROR: Unsupported component role '{component_role}' for MT5 direct export."
        )

    # Parser-derived scope must match receipt-derived scope
    if derived_symbol and capture_receipt.symbol:
        if derived_symbol.upper() != capture_receipt.symbol.upper():
            raise ValueError(
                f"MT5_CROSS_VERIFICATION_FAILED: Parser derived symbol '{derived_symbol}' does not match "
                f"receipt transport-derived symbol '{capture_receipt.symbol}'."
            )

    # Validate scope against expected (comparison targets only)
    if not derived_symbol or derived_symbol.upper() != expected_symbol.upper():
        raise ValueError(
            f"MT5_SCOPE_MISMATCH: Collector derived symbol '{derived_symbol}' does not match "
            f"expected '{expected_symbol}'."
        )
    if derived_venue.upper() != expected_venue.upper():
        raise ValueError(
            f"MT5_SCOPE_MISMATCH: Collector derived venue '{derived_venue}' does not match "
            f"expected '{expected_venue}'."
        )
    if derived_account_tier:
        if derived_account_tier.upper() != expected_account_tier.upper():
            raise ValueError(
                f"MT5_SCOPE_MISMATCH: Collector derived account tier '{derived_account_tier}' does not match "
                f"expected '{expected_account_tier}'."
            )

    # Validate verifier identity
    is_trusted, err = is_trusted_verifier(FrictionVerificationMethod.MT5_DIRECT_EXPORT.value, verifier_identity)
    if not is_trusted:
        raise ValueError(f"MT5_VERIFIER_ERROR: {err}")

    captured_at = capture_receipt.capture_time
    captured_iso = captured_at.isoformat()

    # Build provenance metadata with all governed receipt fields
    meta = {
        "collector_workflow": "AURUMIQ_GOVERNED_MT5_COLLECTOR",
        "server": capture_receipt.server,
        "broker": capture_receipt.broker.upper(),
        "derived_symbol": derived_symbol.upper(),
        "derived_venue": derived_venue.upper(),
        "derived_account_tier": derived_account_tier.upper(),
        "terminal_version": capture_receipt.terminal_version,
        "export_type": capture_receipt.export_type,
        "collector_version": capture_receipt.collector_version,
        "capture_id": capture_receipt.capture_id,
        "capture_time": captured_iso,
        "raw_sha256": computed_raw_sha,
        "receipt_auth_tag": capture_receipt.receipt_auth_tag,
    }

    # Compute capture context hash
    ctx_hash = compute_capture_context_hash(meta, FrictionVerificationMethod.MT5_DIRECT_EXPORT.value)
    meta["capture_context_hash"] = ctx_hash

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
        capture_context_hash=ctx_hash,
    )

    attestation_id = hashlib.sha256(
        f"{source_snapshot.snapshot_id}:{norm_role}:"
        f"{FrictionVerificationMethod.MT5_DIRECT_EXPORT.value}:"
        f"{verifier_identity}:{computed_raw_sha}:{proof}".encode()
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


# --------------------------------------------------------------------------------------
# Account Portal Export — DECLARED Only (No Authenticated Collector Yet)
# --------------------------------------------------------------------------------------

def create_declared_account_portal_export_attestation(
    source_snapshot: Any,
    component_role: str,
    raw_bytes: bytes,
    expected_symbol: str = "XAUUSD",
    expected_venue: str = "EXNESS",
    expected_account_tier: str = "STANDARD",
    verifier_identity: str = "AURUMIQ_ACCOUNT_PORTAL_CAPTURE_WORKFLOW",
    collector_version: str = "1.0.0",
) -> Any:
    """Account portal export attestation — always DECLARED.

    No authenticated portal collector exists. Scope is NOT independently derived.
    No fabricated portal_session_hash. No verification proof for DECLARED status.
    ACCOUNT_PORTAL_EXPORT VERIFIED is explicitly rejected by verify_attestation_authenticity()
    outside test environment.
    """
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
            f"PORTAL_EXPORT_ERROR: Raw bytes SHA '{computed_raw_sha}' mismatch with "
            f"snapshot '{source_snapshot.raw_payload_bytes_sha256}'."
        )

    is_trusted, err = is_trusted_verifier(
        FrictionVerificationMethod.ACCOUNT_PORTAL_EXPORT.value, verifier_identity
    )
    if not is_trusted:
        raise ValueError(f"PORTAL_EXPORT_VERIFIER_ERROR: {err}")

    captured_at = source_snapshot.retrieved_at or datetime.now(timezone.utc)

    meta = {
        "collector_workflow": "AURUMIQ_ACCOUNT_PORTAL_CAPTURE_WORKFLOW",
        "collector_version": collector_version,
        "attestation_note": (
            "DECLARED: No authenticated portal collector deployed. "
            "Scope not independently derived. "
            "Cannot be VERIFIED until governed portal export transport is available."
        ),
    }

    attestation_id = hashlib.sha256(
        f"{source_snapshot.snapshot_id}:{norm_role}:"
        f"{FrictionVerificationMethod.ACCOUNT_PORTAL_EXPORT.value}:"
        f"{verifier_identity}:{computed_raw_sha}:DECLARED".encode()
    ).hexdigest()

    existing = FrictionSourceProvenanceAttestation.objects.filter(attestation_id=attestation_id).first()
    if existing:
        return existing

    return FrictionSourceProvenanceAttestation.objects.create(
        attestation_id=attestation_id,
        source_snapshot=source_snapshot,
        component_role=norm_role,
        source_origin="https://my.exness.com/portal/export",
        source_type=FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value,
        collection_methodology="DECLARED_ACCOUNT_PORTAL_EXPORT",
        captured_at=captured_at,
        reviewed_at=datetime.now(timezone.utc),
        verification_method=FrictionVerificationMethod.ACCOUNT_PORTAL_EXPORT.value,
        verifier_identity=verifier_identity.strip(),
        venue=expected_venue.upper(),
        symbol=expected_symbol.upper(),
        account_tier=expected_account_tier.upper(),
        raw_artifact_sha256=computed_raw_sha,
        provenance_metadata=meta,
        attestation_status=FrictionAttestationStatus.DECLARED.value,
        # No verification proof for DECLARED status
    )
