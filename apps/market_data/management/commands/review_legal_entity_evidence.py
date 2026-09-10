"""Management command to execute governed compliance review of legal entity evidence.

Stage D5C.1: Implements AURUMIQ_LEGAL_ENTITY_REVIEW_WORKFLOW_V1.
Inspects local raw immutable evidence files:
- Component 1: exness_sc_client_agreement.pdf (ACCOUNT_CLIENT_AGREEMENT)
- Component 2: exness_standard_cent_personal_area.jpeg (BROKER_PERSONAL_AREA_EXPORT)
Recomputes SHA-256 hashes and byte lengths.
Binds canonical review receipt payload and signs with dedicated PROVENANCE_SIGNING_SECRET.
Emits a public-safe review receipt JSON artifact.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import uuid

from django.core.management.base import BaseCommand, CommandError

from apps.market_data.friction.legal_entity import (
    BROKER_CRYPTOGRAPHIC_ORIGIN_NOT_CLAIMED,
    DEFAULT_REVIEW_RECEIPT_PATH,
    EXPECTED_CLIENT_AGREEMENT_BYTES,
    EXPECTED_CLIENT_AGREEMENT_FILENAME,
    EXPECTED_CLIENT_AGREEMENT_ROLE,
    EXPECTED_CLIENT_AGREEMENT_SHA256,
    EXPECTED_CLIENT_AGREEMENT_SOURCE_TYPE,
    EXPECTED_PERSONAL_AREA_BYTES,
    EXPECTED_PERSONAL_AREA_FILENAME,
    EXPECTED_PERSONAL_AREA_ROLE,
    EXPECTED_PERSONAL_AREA_SHA256,
    EXPECTED_PERSONAL_AREA_SOURCE_TYPE,
    LEGAL_ENTITY_REVIEW_RECEIPT_SCHEMA,
    ORIGIN_CLAIM_AUTHENTICATED_REVIEW_ONLY,
    QUALIFIED_ACCOUNT_CURRENCY,
    QUALIFIED_ACCOUNT_TIER,
    QUALIFIED_LEGAL_ENTITY_CODE,
    QUALIFIED_LEGAL_ENTITY_NAME,
    QUALIFIED_LICENSE_NUMBER,
    QUALIFIED_REGULATOR,
    QUALIFIED_SYMBOL,
    QUALIFIED_VENUE,
    REVIEW_WORKFLOW_VERSION,
    SERVER_BINDING_POLICY,
    VERIFICATION_METHOD_COMPOSITE_REVIEW,
    VERIFIER_IDENTITY_LEGAL_ENTITY_WORKFLOW,
    compute_canonical_review_receipt_payload,
    compute_review_receipt_proof,
)
from apps.market_data.friction.provenance import (
    CURRENT_PROOF_VERSION,
    GOVERNED_PROVENANCE_AUTHORITY,
    get_governed_signing_secret,
    is_production_environment,
    is_test_environment,
)


def verify_raw_evidence_artifacts(evidence_dir: Path) -> tuple[str, int, str, int]:
    """Verify local raw evidence artifacts against immutable SHA-256 and byte constants.

    Returns (pdf_sha, pdf_len, jpeg_sha, jpeg_len).
    """
    if not evidence_dir.exists():
        raise CommandError(f"EVIDENCE_DIRECTORY_NOT_FOUND: Evidence directory '{evidence_dir}' does not exist.")

    pdf_path = evidence_dir / EXPECTED_CLIENT_AGREEMENT_FILENAME
    jpeg_path = evidence_dir / EXPECTED_PERSONAL_AREA_FILENAME

    if not pdf_path.exists():
        raise CommandError(
            f"MISSING_RAW_EVIDENCE: Component 1 file '{EXPECTED_CLIENT_AGREEMENT_FILENAME}' missing in {evidence_dir}."
        )
    if not jpeg_path.exists():
        raise CommandError(
            f"MISSING_RAW_EVIDENCE: Component 2 file '{EXPECTED_PERSONAL_AREA_FILENAME}' missing in {evidence_dir}."
        )

    pdf_bytes = pdf_path.read_bytes()
    pdf_sha = hashlib.sha256(pdf_bytes).hexdigest()
    pdf_len = len(pdf_bytes)

    if pdf_sha != EXPECTED_CLIENT_AGREEMENT_SHA256:
        raise CommandError(
            f"EVIDENCE_HASH_MISMATCH: Component 1 ({EXPECTED_CLIENT_AGREEMENT_FILENAME}) SHA-256 "
            f"'{pdf_sha}' does not match expected '{EXPECTED_CLIENT_AGREEMENT_SHA256}'."
        )
    if pdf_len != EXPECTED_CLIENT_AGREEMENT_BYTES:
        raise CommandError(
            f"EVIDENCE_SIZE_MISMATCH: Component 1 ({EXPECTED_CLIENT_AGREEMENT_FILENAME}) size "
            f"{pdf_len} bytes != expected {EXPECTED_CLIENT_AGREEMENT_BYTES} bytes."
        )

    jpeg_bytes = jpeg_path.read_bytes()
    jpeg_sha = hashlib.sha256(jpeg_bytes).hexdigest()
    jpeg_len = len(jpeg_bytes)

    if jpeg_sha != EXPECTED_PERSONAL_AREA_SHA256:
        raise CommandError(
            f"EVIDENCE_HASH_MISMATCH: Component 2 ({EXPECTED_PERSONAL_AREA_FILENAME}) SHA-256 "
            f"'{jpeg_sha}' does not match expected '{EXPECTED_PERSONAL_AREA_SHA256}'."
        )
    if jpeg_len != EXPECTED_PERSONAL_AREA_BYTES:
        raise CommandError(
            f"EVIDENCE_SIZE_MISMATCH: Component 2 ({EXPECTED_PERSONAL_AREA_FILENAME}) size "
            f"{jpeg_len} bytes != expected {EXPECTED_PERSONAL_AREA_BYTES} bytes."
        )

    return (pdf_sha, pdf_len, jpeg_sha, jpeg_len)


class Command(BaseCommand):
    help = "Execute governed compliance review of local raw legal entity evidence and generate signed review receipt."

    def add_arguments(self, parser):
        parser.add_argument(
            "--evidence-dir",
            type=str,
            default="artifacts/calibration/legal_entity_evidence",
            help="Directory containing local raw evidence files (default: artifacts/calibration/legal_entity_evidence)",
        )
        parser.add_argument(
            "--output-receipt",
            type=str,
            default=DEFAULT_REVIEW_RECEIPT_PATH,
            help=f"Path to output signed review receipt JSON (default: {DEFAULT_REVIEW_RECEIPT_PATH})",
        )
        parser.add_argument(
            "--verifier-identity",
            type=str,
            default=VERIFIER_IDENTITY_LEGAL_ENTITY_WORKFLOW,
            help=f"Verifier identity for workflow (default: {VERIFIER_IDENTITY_LEGAL_ENTITY_WORKFLOW})",
        )
        parser.add_argument(
            "--operator-audit-label",
            type=str,
            default=None,
            help="Optional audit metadata label documenting operator review session (not root of trust)",
        )
        parser.add_argument(
            "--signing-secret",
            type=str,
            default=None,
            help="Test-only signing secret override (strictly prohibited outside test environment)",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Overwrite output receipt file if it already exists",
        )

    def handle(self, *args, **options):
        evidence_dir_str = options["evidence_dir"]
        output_receipt_str = options["output_receipt"]
        verifier_identity = options["verifier_identity"]
        operator_label = options.get("operator_audit_label")
        secret_override = options.get("signing_secret")
        force_overwrite = options.get("force", False)

        evidence_dir = Path(evidence_dir_str)
        out_path = Path(output_receipt_str)
        canonical_path = Path(DEFAULT_REVIEW_RECEIPT_PATH)
        is_canonical_output = (
            out_path.resolve() == canonical_path.resolve()
            or (out_path.name == canonical_path.name and "calibration" in str(out_path).replace("\\", "/"))
        )

        # 1. Enforce strict destination & environment boundaries
        if is_canonical_output:
            if not is_production_environment():
                raise CommandError(
                    f"CANONICAL_RECEIPT_REQUIRES_PRODUCTION_CONTEXT: Cannot generate canonical production review receipt "
                    f"('{out_path}') outside strict production settings. "
                    "Canonical production review receipt generation requires active settings 'config.settings.production' "
                    "and DEBUG=False. Development and test runs must output to an isolated non-canonical temporary path."
                )
            if is_test_environment():
                raise CommandError(
                    "CANONICAL_RECEIPT_TEST_ESCAPE_BLOCKED: Cannot generate canonical production review receipt "
                    f"('{out_path}') while running in a test environment. "
                    "Automated tests must output to an isolated temporary receipt path."
                )
            if secret_override:
                raise CommandError(
                    "CANONICAL_RECEIPT_TEST_ESCAPE_BLOCKED: Cannot write to canonical production receipt path "
                    "when using --signing-secret override. Genuine production receipt requires PROVENANCE_SIGNING_SECRET."
                )

        # 2. Resolve and validate signing secret
        signing_secret_bytes: bytes
        if secret_override:
            if not is_test_environment():
                raise CommandError(
                    "SECURITY_VIOLATION: Parameter --signing-secret is strictly prohibited outside test environment."
                )
            signing_secret_bytes = secret_override.encode("utf-8")
        else:
            try:
                signing_secret_bytes = get_governed_signing_secret()
            except RuntimeError as exc:
                raise CommandError(
                    f"PROVENANCE_SIGNING_SECRET_MISSING: Cannot generate production review receipt: {exc}"
                ) from exc

        test_sentinel_key = b"aurumiq-test-only-provenance-key-NOT-FOR-PRODUCTION"
        if is_canonical_output and signing_secret_bytes == test_sentinel_key:
            raise CommandError(
                "TEST_SENTINEL_ESCAPE_BLOCKED: Canonical production review receipt cannot be generated "
                "using the test sentinel provenance key."
            )

        # 3. Read and verify local raw evidence artifacts
        self.stdout.write("Reading and verifying local raw evidence artifacts...")
        pdf_sha, pdf_len, jpeg_sha, jpeg_len = verify_raw_evidence_artifacts(evidence_dir)
        self.stdout.write(self.style.SUCCESS("All local evidence files verified against immutable SHA-256 constants."))

        if out_path.exists() and not force_overwrite:
            raise CommandError(
                f"OUTPUT_EXISTS: Receipt file '{out_path}' already exists. Use --force to overwrite."
            )

        # 3. Build canonical receipt metadata
        receipt_id = f"RECEIPT-LEGAL-{uuid.uuid4()}"
        reviewed_at = datetime.now(timezone.utc).isoformat()

        canonical_payload = compute_canonical_review_receipt_payload(
            schema=LEGAL_ENTITY_REVIEW_RECEIPT_SCHEMA,
            verification_authority=GOVERNED_PROVENANCE_AUTHORITY,
            verification_proof_version=CURRENT_PROOF_VERSION,
            workflow_version=REVIEW_WORKFLOW_VERSION,
            receipt_id=receipt_id,
            reviewed_at_iso=reviewed_at,
            verification_method=VERIFICATION_METHOD_COMPOSITE_REVIEW,
            verifier_identity=verifier_identity,
            legal_entity_name=QUALIFIED_LEGAL_ENTITY_NAME,
            legal_entity_code=QUALIFIED_LEGAL_ENTITY_CODE,
            regulator=QUALIFIED_REGULATOR,
            license_number=QUALIFIED_LICENSE_NUMBER,
            account_tier=QUALIFIED_ACCOUNT_TIER,
            account_currency=QUALIFIED_ACCOUNT_CURRENCY,
            comp1_role=EXPECTED_CLIENT_AGREEMENT_ROLE,
            comp1_source_type=EXPECTED_CLIENT_AGREEMENT_SOURCE_TYPE,
            comp1_sha256=EXPECTED_CLIENT_AGREEMENT_SHA256,
            comp1_bytes=EXPECTED_CLIENT_AGREEMENT_BYTES,
            comp2_role=EXPECTED_PERSONAL_AREA_ROLE,
            comp2_source_type=EXPECTED_PERSONAL_AREA_SOURCE_TYPE,
            comp2_sha256=EXPECTED_PERSONAL_AREA_SHA256,
            comp2_bytes=EXPECTED_PERSONAL_AREA_BYTES,
            server_binding_policy=SERVER_BINDING_POLICY,
            origin_claim=ORIGIN_CLAIM_AUTHENTICATED_REVIEW_ONLY,
            broker_cryptographic_origin=BROKER_CRYPTOGRAPHIC_ORIGIN_NOT_CLAIMED,
        )

        proof = compute_review_receipt_proof(canonical_payload, signing_secret=signing_secret_bytes)

        receipt_dict = {
            "schema": LEGAL_ENTITY_REVIEW_RECEIPT_SCHEMA,
            "receipt_id": receipt_id,
            "reviewed_at": reviewed_at,
            "workflow_version": REVIEW_WORKFLOW_VERSION,
            "verification_authority": GOVERNED_PROVENANCE_AUTHORITY,
            "verification_method": VERIFICATION_METHOD_COMPOSITE_REVIEW,
            "verifier_identity": verifier_identity,
            "verification_proof_version": CURRENT_PROOF_VERSION,
            "target_scope": {
                "venue": QUALIFIED_VENUE,
                "symbol": QUALIFIED_SYMBOL,
                "account_tier": QUALIFIED_ACCOUNT_TIER,
                "account_currency": QUALIFIED_ACCOUNT_CURRENCY,
            },
            "legal_entity": {
                "legal_entity_name": QUALIFIED_LEGAL_ENTITY_NAME,
                "legal_entity_code": QUALIFIED_LEGAL_ENTITY_CODE,
                "regulator": QUALIFIED_REGULATOR,
                "license_number": QUALIFIED_LICENSE_NUMBER,
            },
            "governed_components": {
                "component_1": {
                    "evidence_role": EXPECTED_CLIENT_AGREEMENT_ROLE,
                    "source_type": EXPECTED_CLIENT_AGREEMENT_SOURCE_TYPE,
                    "filename": EXPECTED_CLIENT_AGREEMENT_FILENAME,
                    "sha256": EXPECTED_CLIENT_AGREEMENT_SHA256,
                    "bytes": EXPECTED_CLIENT_AGREEMENT_BYTES,
                },
                "component_2": {
                    "evidence_role": EXPECTED_PERSONAL_AREA_ROLE,
                    "source_type": EXPECTED_PERSONAL_AREA_SOURCE_TYPE,
                    "filename": EXPECTED_PERSONAL_AREA_FILENAME,
                    "sha256": EXPECTED_PERSONAL_AREA_SHA256,
                    "bytes": EXPECTED_PERSONAL_AREA_BYTES,
                },
            },
            "critical_policies": {
                "server_binding_policy": SERVER_BINDING_POLICY,
                "origin_claim": ORIGIN_CLAIM_AUTHENTICATED_REVIEW_ONLY,
                "broker_cryptographic_origin": BROKER_CRYPTOGRAPHIC_ORIGIN_NOT_CLAIMED,
            },
            "verification_proof": proof,
        }

        if operator_label:
            receipt_dict["audit_metadata"] = {
                "operator_audit_label": str(operator_label).strip(),
                "note": "Operator audit label is descriptive audit metadata, not root of cryptographic trust.",
            }

        if out_path.exists() and not force_overwrite:
            raise CommandError(
                f"OUTPUT_FILE_EXISTS: Output receipt '{out_path}' already exists. Use --force to overwrite."
            )

        os.makedirs(out_path.parent, exist_ok=True)
        out_path.write_text(json.dumps(receipt_dict, indent=2), encoding="utf-8")

        self.stdout.write(self.style.SUCCESS(f"Review receipt successfully created: {out_path}"))
        self.stdout.write(f"Receipt ID: {receipt_id}")
        self.stdout.write(f"Verifier Identity: {verifier_identity}")
        self.stdout.write(f"Verification Method: {VERIFICATION_METHOD_COMPOSITE_REVIEW}")
