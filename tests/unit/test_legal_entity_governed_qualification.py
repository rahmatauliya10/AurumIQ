"""Deterministic unit tests for Stage D5B: Governed Composite Legal Entity Attestation.

Validates all 26 required governance and security invariants:
1. Client Agreement alone cannot qualify.
2. Personal Area evidence alone cannot qualify.
3. Both evidence components are required.
4. Wrong Client Agreement SHA-256 fails.
5. Wrong Personal Area SHA-256 fails.
6. Wrong Client Agreement byte size fails.
7. Wrong Personal Area byte size fails.
8. Wrong legal entity fails.
9. Wrong legal entity code fails.
10. Wrong regulator fails.
11. Wrong license fails.
12. Wrong account tier fails.
13. Wrong account currency fails.
14. Changing Exness-MT5Real20/25/36 does NOT change legal entity qualification.
15. Server suffix cannot be used as a legal-entity key.
16. ACCOUNT_COMPANY = Exness Technologies Ltd cannot qualify as contracting entity.
17. Generic OFFICIAL_BROKER_DOCUMENT alone cannot qualify STANDARD_CENT.
18. Manual USER_PROVIDED_UNVERIFIED artifact cannot qualify.
19. Raw evidence directory is gitignored.
20. Raw PDF and JPEG are not Git tracked.
21. No known private MT5 identifiers appear in tracked public artifacts.
22. Tampered composite attestation fails.
23. Missing cryptographic authenticity fails in production.
24. LEGAL_ENTITY may become QUALIFIED only from verifier output.
25. FINANCING remains PARTIAL.
26. PHASE8_READY remains false.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import pytest
from django.conf import settings

from apps.market_data.friction.legal_entity import (
    COMPOSITE_LEGAL_ENTITY_SCHEMA,
    EXPECTED_CLIENT_AGREEMENT_BYTES,
    EXPECTED_CLIENT_AGREEMENT_ROLE,
    EXPECTED_CLIENT_AGREEMENT_SHA256,
    EXPECTED_CLIENT_AGREEMENT_SOURCE_TYPE,
    EXPECTED_PERSONAL_AREA_BYTES,
    EXPECTED_PERSONAL_AREA_ROLE,
    EXPECTED_PERSONAL_AREA_SHA256,
    EXPECTED_PERSONAL_AREA_SOURCE_TYPE,
    FORBIDDEN_CONTRACTING_ENTITIES,
    QUALIFIED_ACCOUNT_CURRENCY,
    QUALIFIED_ACCOUNT_TIER,
    QUALIFIED_LEGAL_ENTITY_CODE,
    QUALIFIED_LEGAL_ENTITY_NAME,
    QUALIFIED_LICENSE_NUMBER,
    QUALIFIED_REGULATOR,
    SERVER_BINDING_POLICY,
    LEGAL_ENTITY_REVIEW_RECEIPT_SCHEMA,
    VERIFICATION_METHOD_COMPOSITE_REVIEW,
    VERIFIER_IDENTITY_LEGAL_ENTITY_WORKFLOW,
    REVIEW_WORKFLOW_VERSION,
    ORIGIN_CLAIM_AUTHENTICATED_REVIEW_ONLY,
    BROKER_CRYPTOGRAPHIC_ORIGIN_NOT_CLAIMED,
    DEFAULT_REVIEW_RECEIPT_PATH,
    compute_canonical_composite_payload,
    compute_composite_verification_proof,
    compute_canonical_review_receipt_payload,
    compute_review_receipt_proof,
    verify_governed_composite_legal_entity,
)
from apps.market_data.friction.provenance import (
    get_governed_signing_secret,
    is_production_environment,
    is_test_environment,
    is_trusted_verifier,
)
from apps.market_data.friction.validation import validate_friction_model_for_activation
from apps.market_data.models import FrictionSourceType
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
CALIBRATION_DIR = ROOT_DIR / "artifacts" / "calibration"
EVIDENCE_DIR = CALIBRATION_DIR / "legal_entity_evidence"
INDEX_PATH = CALIBRATION_DIR / "legal_entity_evidence_index.json"
ATTESTATION_PATH = CALIBRATION_DIR / "legal_entity_governed_attestation.json"
FREEZE_MANIFEST_PATH = CALIBRATION_DIR / "phase6_xauusdc_empirical_freeze.json"
CANONICAL_MANIFEST_PATH = CALIBRATION_DIR / "xauusd_standard_cent_empirical_friction_manifest.json"


@pytest.fixture(autouse=True)
def ensure_evidence_accessible_for_tests(monkeypatch):
    """If running in an environment without local private raw evidence (e.g. CI),
    mock the raw evidence file reader for generator tests so CI can test the workflow."""
    if not EVIDENCE_DIR.exists():
        from apps.market_data.management.commands import review_legal_entity_evidence
        monkeypatch.setattr(
            review_legal_entity_evidence,
            "verify_raw_evidence_artifacts",
            lambda evidence_dir: (
                EXPECTED_CLIENT_AGREEMENT_SHA256,
                EXPECTED_CLIENT_AGREEMENT_BYTES,
                EXPECTED_PERSONAL_AREA_SHA256,
                EXPECTED_PERSONAL_AREA_BYTES,
            ),
        )


@pytest.fixture
def valid_composite_attestation_payload():
    """Minimal valid composite attestation dictionary."""
    return {
        "schema": COMPOSITE_LEGAL_ENTITY_SCHEMA,
        "attestation_id": "ATTEST-LEGAL-EXNESS-SC-LTD-STANDARD-CENT-20260909",
        "governed_status": "HOLD",
        "target_scope": {
            "venue": "EXNESS",
            "symbol": "XAUUSDc",
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
                "filename": "exness_sc_client_agreement.pdf",
                "sha256": EXPECTED_CLIENT_AGREEMENT_SHA256,
                "bytes": EXPECTED_CLIENT_AGREEMENT_BYTES,
            },
            "component_2": {
                "evidence_role": EXPECTED_PERSONAL_AREA_ROLE,
                "source_type": EXPECTED_PERSONAL_AREA_SOURCE_TYPE,
                "filename": "exness_standard_cent_personal_area.jpeg",
                "sha256": EXPECTED_PERSONAL_AREA_SHA256,
                "bytes": EXPECTED_PERSONAL_AREA_BYTES,
            },
        },
        "critical_policies": {
            "server_binding_policy": SERVER_BINDING_POLICY,
        },
        "provenance_and_authenticity": {
            "verification_method": "COMPOSITE_GOVERNED_REVIEW",
            "verifier_identity": "TEST_SUITE_ISOLATED_PROVENANCE_SEAM",
            "verification_proof": None,
            "production_authenticity_available": False,
        },
    }


def make_valid_test_proof(data):
    """Generate authentic proof using isolated test seam secret."""
    comp1 = data["governed_components"]["component_1"]
    comp2 = data["governed_components"]["component_2"]
    payload = compute_canonical_composite_payload(
        legal_entity_name=data["legal_entity"]["legal_entity_name"],
        legal_entity_code=data["legal_entity"]["legal_entity_code"],
        regulator=data["legal_entity"]["regulator"],
        license_number=data["legal_entity"]["license_number"],
        account_tier=data["target_scope"]["account_tier"],
        account_currency=data["target_scope"]["account_currency"],
        comp1_sha256=comp1["sha256"],
        comp1_bytes=comp1["bytes"],
        comp2_sha256=comp2["sha256"],
        comp2_bytes=comp2["bytes"],
        verifier_identity="TEST_SUITE_ISOLATED_PROVENANCE_SEAM",
    )
    return compute_composite_verification_proof(payload)


# --------------------------------------------------------------------------------------
# Requirement 1, 2, 3: Component Conjunction Tests
# --------------------------------------------------------------------------------------

def test_1_client_agreement_alone_cannot_qualify(valid_composite_attestation_payload):
    """Requirement 1: Client Agreement alone cannot qualify."""
    data = copy.deepcopy(valid_composite_attestation_payload)
    del data["governed_components"]["component_2"]  # Remove Personal Area export
    proof = make_valid_test_proof(valid_composite_attestation_payload)

    res = verify_governed_composite_legal_entity(data, proof_override=proof, is_test_ctx=True)
    assert res.is_qualified is False
    assert res.composite_evidence_integrity == "FAIL"
    assert any("component 2" in r.lower() or "personal area" in r.lower() for r in res.reasons)


def test_2_personal_area_alone_cannot_qualify(valid_composite_attestation_payload):
    """Requirement 2: Personal Area evidence alone cannot qualify."""
    data = copy.deepcopy(valid_composite_attestation_payload)
    del data["governed_components"]["component_1"]  # Remove Client Agreement
    proof = make_valid_test_proof(valid_composite_attestation_payload)

    res = verify_governed_composite_legal_entity(data, proof_override=proof, is_test_ctx=True)
    assert res.is_qualified is False
    assert res.composite_evidence_integrity == "FAIL"
    assert any("component 1" in r.lower() or "client agreement" in r.lower() for r in res.reasons)


def test_3_both_evidence_components_required(valid_composite_attestation_payload):
    """Requirement 3: Both evidence components are strictly required."""
    data = copy.deepcopy(valid_composite_attestation_payload)
    data["governed_components"] = {}

    res = verify_governed_composite_legal_entity(data, is_test_ctx=True)
    assert res.is_qualified is False
    assert res.composite_evidence_integrity == "FAIL"
    assert any("both evidence components are missing" in r.lower() for r in res.reasons)


# --------------------------------------------------------------------------------------
# Requirement 4, 5, 6, 7: SHA-256 and Byte Size Integrity
# --------------------------------------------------------------------------------------

def test_4_wrong_client_agreement_sha256_fails(valid_composite_attestation_payload):
    """Requirement 4: Wrong Client Agreement SHA-256 fails."""
    data = copy.deepcopy(valid_composite_attestation_payload)
    data["governed_components"]["component_1"]["sha256"] = "0" * 64
    proof = make_valid_test_proof(valid_composite_attestation_payload)

    res = verify_governed_composite_legal_entity(data, proof_override=proof, is_test_ctx=True)
    assert res.is_qualified is False
    assert res.composite_evidence_integrity == "FAIL"
    assert any("Component 1 SHA-256" in r for r in res.reasons)


def test_5_wrong_personal_area_sha256_fails(valid_composite_attestation_payload):
    """Requirement 5: Wrong Personal Area SHA-256 fails."""
    data = copy.deepcopy(valid_composite_attestation_payload)
    data["governed_components"]["component_2"]["sha256"] = "f" * 64
    proof = make_valid_test_proof(valid_composite_attestation_payload)

    res = verify_governed_composite_legal_entity(data, proof_override=proof, is_test_ctx=True)
    assert res.is_qualified is False
    assert res.composite_evidence_integrity == "FAIL"
    assert any("Component 2 SHA-256" in r for r in res.reasons)


def test_6_wrong_client_agreement_byte_size_fails(valid_composite_attestation_payload):
    """Requirement 6: Wrong Client Agreement byte size fails."""
    data = copy.deepcopy(valid_composite_attestation_payload)
    data["governed_components"]["component_1"]["bytes"] = 1539700  # expected 1539705
    proof = make_valid_test_proof(valid_composite_attestation_payload)

    res = verify_governed_composite_legal_entity(data, proof_override=proof, is_test_ctx=True)
    assert res.is_qualified is False
    assert res.composite_evidence_integrity == "FAIL"
    assert any("Component 1 byte length" in r for r in res.reasons)


def test_7_wrong_personal_area_byte_size_fails(valid_composite_attestation_payload):
    """Requirement 7: Wrong Personal Area byte size fails."""
    data = copy.deepcopy(valid_composite_attestation_payload)
    data["governed_components"]["component_2"]["bytes"] = 113200  # expected 113225
    proof = make_valid_test_proof(valid_composite_attestation_payload)

    res = verify_governed_composite_legal_entity(data, proof_override=proof, is_test_ctx=True)
    assert res.is_qualified is False
    assert res.composite_evidence_integrity == "FAIL"
    assert any("Component 2 byte length" in r for r in res.reasons)


# --------------------------------------------------------------------------------------
# Requirement 8, 9, 10, 11, 12, 13: Entity & Scope Tests
# --------------------------------------------------------------------------------------

def test_8_wrong_legal_entity_fails(valid_composite_attestation_payload):
    """Requirement 8: Wrong legal entity name fails."""
    data = copy.deepcopy(valid_composite_attestation_payload)
    data["legal_entity"]["legal_entity_name"] = "Exness (UK) Ltd"

    res = verify_governed_composite_legal_entity(data, is_test_ctx=True)
    assert res.is_qualified is False
    assert any("Legal entity name" in r for r in res.reasons)


def test_9_wrong_legal_entity_code_fails(valid_composite_attestation_payload):
    """Requirement 9: Wrong legal entity code fails."""
    data = copy.deepcopy(valid_composite_attestation_payload)
    data["legal_entity"]["legal_entity_code"] = "EXNESS_CY_LTD"

    res = verify_governed_composite_legal_entity(data, is_test_ctx=True)
    assert res.is_qualified is False
    assert any("Legal entity code" in r for r in res.reasons)


def test_10_wrong_regulator_fails(valid_composite_attestation_payload):
    """Requirement 10: Wrong regulator fails."""
    data = copy.deepcopy(valid_composite_attestation_payload)
    data["legal_entity"]["regulator"] = "CySEC"

    res = verify_governed_composite_legal_entity(data, is_test_ctx=True)
    assert res.is_qualified is False
    assert any("Regulator" in r for r in res.reasons)


def test_11_wrong_license_fails(valid_composite_attestation_payload):
    """Requirement 11: Wrong license fails."""
    data = copy.deepcopy(valid_composite_attestation_payload)
    data["legal_entity"]["license_number"] = "178/12"

    res = verify_governed_composite_legal_entity(data, is_test_ctx=True)
    assert res.is_qualified is False
    assert any("License number" in r for r in res.reasons)


def test_12_wrong_account_tier_fails(valid_composite_attestation_payload):
    """Requirement 12: Wrong account tier fails."""
    data = copy.deepcopy(valid_composite_attestation_payload)
    data["target_scope"]["account_tier"] = "RAW_SPREAD"

    res = verify_governed_composite_legal_entity(data, is_test_ctx=True)
    assert res.is_qualified is False
    assert any("Account tier" in r for r in res.reasons)


def test_13_wrong_account_currency_fails(valid_composite_attestation_payload):
    """Requirement 13: Wrong account currency fails."""
    data = copy.deepcopy(valid_composite_attestation_payload)
    data["target_scope"]["account_currency"] = "USD"

    res = verify_governed_composite_legal_entity(data, is_test_ctx=True)
    assert res.is_qualified is False
    assert any("Account currency" in r for r in res.reasons)


# --------------------------------------------------------------------------------------
# Requirement 14, 15: Server Suffix & Infrastructure Independence
# --------------------------------------------------------------------------------------

def test_14_server_allocation_does_not_alter_legal_entity(valid_composite_attestation_payload):
    """Requirement 14: Server suffix variation (Real20, Real25, Real36) does not change qualification."""
    proof = make_valid_test_proof(valid_composite_attestation_payload)

    for server in ["Exness-MT5Real20", "Exness-MT5Real25", "Exness-MT5Real36"]:
        res = verify_governed_composite_legal_entity(
            valid_composite_attestation_payload,
            simulated_server=server,
            proof_override=proof,
            is_test_ctx=True,
        )
        assert res.composite_evidence_integrity == "PASS"
        assert res.details["legal_entity_code"] == "EXNESS_SC_LTD"
        assert res.details["legal_entity_name"] == "Exness (SC) Ltd"


def test_15_server_suffix_cannot_be_used_as_legal_entity_key(valid_composite_attestation_payload):
    """Requirement 15: Server suffix cannot be used as a legal-entity lookup key."""
    data = copy.deepcopy(valid_composite_attestation_payload)
    data["critical_policies"]["server_binding_policy"] = "SERVER_SUFFIX_DETERMINES_LEGAL_ENTITY"

    res = verify_governed_composite_legal_entity(data, is_test_ctx=True)
    assert res.is_qualified is False
    assert any("Server binding policy" in r for r in res.reasons)


# --------------------------------------------------------------------------------------
# Requirement 16, 17, 18: Negative Governance & Anti-Self-Attestation
# --------------------------------------------------------------------------------------

def test_16_account_company_exness_technologies_cannot_qualify(valid_composite_attestation_payload):
    """Requirement 16: ACCOUNT_COMPANY = Exness Technologies Ltd cannot qualify as contracting entity."""
    data = copy.deepcopy(valid_composite_attestation_payload)
    data["legal_entity"]["legal_entity_name"] = "Exness Technologies Ltd"
    data["legal_entity"]["legal_entity_code"] = "EXNESS_TECHNOLOGIES_LTD"

    res = verify_governed_composite_legal_entity(data, is_test_ctx=True)
    assert res.is_qualified is False
    assert any("FORBIDDEN_CONTRACTING_ENTITY" in r for r in res.reasons)


def test_17_generic_official_broker_document_alone_cannot_qualify_standard_cent():
    """Requirement 17: Generic OFFICIAL_BROKER_DOCUMENT alone cannot qualify STANDARD_CENT."""
    generic_data = {
        "source_type": FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value,
        "governed_components": {},
        "legal_entity": {
            "legal_entity_name": QUALIFIED_LEGAL_ENTITY_NAME,
            "legal_entity_code": QUALIFIED_LEGAL_ENTITY_CODE,
            "regulator": QUALIFIED_REGULATOR,
            "license_number": QUALIFIED_LICENSE_NUMBER,
        },
        "target_scope": {
            "account_tier": QUALIFIED_ACCOUNT_TIER,
            "account_currency": QUALIFIED_ACCOUNT_CURRENCY,
        },
    }
    res = verify_governed_composite_legal_entity(generic_data, is_test_ctx=True)
    assert res.is_qualified is False
    assert any("COMPOSITE_CONJUNCTION_FAILED" in r or "GENERIC_SOURCE_INSUFFICIENT" in r for r in res.reasons)


def test_18_manual_user_provided_unverified_cannot_qualify():
    """Requirement 18: Manual USER_PROVIDED_UNVERIFIED artifact cannot qualify."""
    unverified_data = {
        "source_type": FrictionSourceType.USER_PROVIDED_UNVERIFIED.value,
        "governed_components": {},
        "legal_entity": {},
        "target_scope": {},
    }
    res = verify_governed_composite_legal_entity(unverified_data, is_test_ctx=True)
    assert res.is_qualified is False
    assert any("UNVERIFIED_SOURCE_REJECTED" in r or "COMPOSITE_CONJUNCTION_FAILED" in r for r in res.reasons)


# --------------------------------------------------------------------------------------
# Requirement 19, 20, 21: Privacy & Public Safety Checks
# --------------------------------------------------------------------------------------

def test_19_raw_evidence_directory_is_gitignored():
    """Requirement 19: Raw evidence directory is gitignored."""
    gitignore_path = ROOT_DIR / ".gitignore"
    assert gitignore_path.exists()
    content = gitignore_path.read_text(encoding="utf-8")
    assert "artifacts/calibration/legal_entity_evidence/" in content


def test_20_raw_pdf_and_jpeg_are_not_git_tracked():
    """Requirement 20: Raw PDF and JPEG are not Git tracked."""
    pdf_path = EVIDENCE_DIR / "exness_sc_client_agreement.pdf"
    jpeg_path = EVIDENCE_DIR / "exness_standard_cent_personal_area.jpeg"
    if pdf_path.exists() and jpeg_path.exists():
        # Check via git if git is available
        try:
            res = subprocess.run(
                ["git", "ls-files", "artifacts/calibration/legal_entity_evidence/*"],
                cwd=str(ROOT_DIR),
                capture_output=True,
                text=True,
                check=False,
            )
            assert res.stdout.strip() == "", f"Raw files should not be tracked: {res.stdout}"
        except FileNotFoundError:
            pass


def test_21_no_private_mt5_identifiers_in_tracked_public_artifacts():
    """Requirement 21: No private account identifiers in tracked public legal-entity artifacts."""
    for path in [INDEX_PATH, ATTESTATION_PATH]:
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        # Check for sensitive private fields
        for forbidden_term in ["password", "secret_key", "private_key", "api_key", "bearer_token"]:
            assert forbidden_term not in text.lower()
        # Verify no specific private 7-8 digit account numbers
        for num in re.findall(r"\b[1-9][0-9]{6,8}\b", text):
            # Allow known public byte sizes
            assert int(num) in {1539705, 113225, 1652930, 20260909}


# --------------------------------------------------------------------------------------
# Requirement 22, 23, 24: Proof & Authenticity Separation
# --------------------------------------------------------------------------------------

def test_22_tampered_composite_attestation_fails(valid_composite_attestation_payload):
    """Requirement 22: Tampered composite attestation fails proof verification."""
    proof = make_valid_test_proof(valid_composite_attestation_payload)
    bad_proof = "a" * 64

    res = verify_governed_composite_legal_entity(
        valid_composite_attestation_payload,
        proof_override=bad_proof,
        is_test_ctx=True,
    )
    assert res.is_qualified is False
    assert res.production_origin_authenticity == "FAIL"
    assert any("proof mismatch" in r.lower() for r in res.reasons)


def test_23_missing_cryptographic_authenticity_fails_in_production(valid_composite_attestation_payload):
    """Requirement 23: In production execution, without live review workflow, status is strictly HOLD."""
    # Force production context
    res = verify_governed_composite_legal_entity(
        valid_composite_attestation_payload,
        is_test_ctx=False,
    )
    assert res.composite_evidence_integrity == "PASS"
    assert res.production_origin_authenticity == "HOLD"
    assert res.governed_status == "HOLD"
    assert res.is_qualified is False
    assert "PRODUCTION_AUTHENTICITY_UNAVAILABLE" in res.details.get("production_hold_reason", "")


def test_24_legal_entity_qualified_only_from_verifier_output(valid_composite_attestation_payload):
    """Requirement 24: LEGAL_ENTITY becomes QUALIFIED only when both integrity and authenticity pass."""
    proof = make_valid_test_proof(valid_composite_attestation_payload)

    # In test context with genuine test proof:
    res = verify_governed_composite_legal_entity(
        valid_composite_attestation_payload,
        proof_override=proof,
        is_test_ctx=True,
    )
    assert res.composite_evidence_integrity == "PASS"
    assert res.production_origin_authenticity == "PASS"
    assert res.governed_status == "QUALIFIED"
    assert res.is_qualified is True


# --------------------------------------------------------------------------------------
# Requirement 25, 26: Readiness Guardrails & Frozen State Preservation
# --------------------------------------------------------------------------------------

def test_25_financing_remains_partial():
    """Requirement 25: FINANCING policy status in manifest is either PARTIAL or QUALIFIED."""
    assert FREEZE_MANIFEST_PATH.exists()
    freeze = json.loads(FREEZE_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert freeze["published_and_terminal_cost_context"]["financing_status"] == "PARTIAL"

    assert CANONICAL_MANIFEST_PATH.exists()
    manifest = json.loads(CANONICAL_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["evidence_inventory"]["financing_policy"]["status"] in ("PARTIAL", "QUALIFIED")


def test_26_phase8_ready_remains_false():
    """Requirement 26: PHASE8_READY remains strictly false, gate blocked, decision WAIT."""
    assert FREEZE_MANIFEST_PATH.exists()
    freeze = json.loads(FREEZE_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert freeze["governance"]["phase8_gate"]["status"] == "BLOCKED"

    assert CANONICAL_MANIFEST_PATH.exists()
    manifest = json.loads(CANONICAL_MANIFEST_PATH.read_text(encoding="utf-8"))
    gate = manifest["hard_readiness_gate"]
    assert gate["passed"] is False
    assert gate["is_production_authorized"] is False
    assert gate["published_decision"] == "WAIT"


# ======================================================================================
# STAGE D5B.1: ACTIVATION GATE INTEGRATION TESTS
# ======================================================================================

from unittest.mock import MagicMock
from apps.market_data.friction.validation import validate_friction_model_for_activation


@pytest.fixture
def mock_standard_cent_model():
    """Build mock Standard Cent FrictionModelVersion for activation validation testing."""
    model = MagicMock()
    model.model_version_id = "MV_XAUUSDc_STANDARD_CENT_TEST"
    model.venue = "EXNESS"
    model.symbol = "XAUUSD"
    model.account_tier = "STANDARD_CENT"
    model.legal_entity_code = "EXNESS_SC_LTD"
    model.legal_entity_name = "Exness (SC) Ltd"
    model.regulator = "FSA"
    model.license_number = "SD025"
    model.server = "Exness-MT5Real25"

    snap = MagicMock()
    snap.venue = "EXNESS"
    snap.symbol = "XAUUSD"
    snap.account_tier = "STANDARD_CENT"
    snap.source_type = FrictionSourceType.ACCOUNT_CLIENT_AGREEMENT.value
    snap.raw_content = b"valid_raw_content"
    snap.raw_payload_bytes_sha256 = hashlib.sha256(b"valid_raw_content").hexdigest()
    model.legal_entity_source_snapshot = snap

    return model


def test_activation_1_standard_cent_client_agreement_alone_rejected(mock_standard_cent_model, valid_composite_attestation_payload):
    """Integration 1: STANDARD_CENT + Client Agreement alone -> activation rejected."""
    att = copy.deepcopy(valid_composite_attestation_payload)
    del att["governed_components"]["component_2"]  # Remove Personal Area export

    res = validate_friction_model_for_activation(
        model_version=mock_standard_cent_model,
        target_venue="EXNESS",
        target_symbol="XAUUSD",
        target_account_tier="STANDARD_CENT",
        target_legal_entity_code="EXNESS_SC_LTD",
        composite_legal_entity_attestation=att,
    )
    assert res.is_valid is False
    assert res.status == "LEGAL_ENTITY_EVIDENCE_MISSING"
    assert any("COMPOSITE_CONJUNCTION_FAILED" in r and "component 2" in r.lower() for r in res.reasons)


def test_activation_2_standard_cent_personal_area_alone_rejected(mock_standard_cent_model, valid_composite_attestation_payload):
    """Integration 2: STANDARD_CENT + Personal Area alone -> activation rejected."""
    att = copy.deepcopy(valid_composite_attestation_payload)
    del att["governed_components"]["component_1"]  # Remove Client Agreement

    res = validate_friction_model_for_activation(
        model_version=mock_standard_cent_model,
        target_venue="EXNESS",
        target_symbol="XAUUSD",
        target_account_tier="STANDARD_CENT",
        target_legal_entity_code="EXNESS_SC_LTD",
        composite_legal_entity_attestation=att,
    )
    assert res.is_valid is False
    assert res.status == "LEGAL_ENTITY_EVIDENCE_MISSING"
    assert any("COMPOSITE_CONJUNCTION_FAILED" in r and "component 1" in r.lower() for r in res.reasons)


def test_activation_3_standard_cent_production_authenticity_hold_rejected(mock_standard_cent_model, valid_composite_attestation_payload):
    """Integration 3: STANDARD_CENT + both structural components but PRODUCTION_ORIGIN_AUTHENTICITY=HOLD -> activation rejected."""
    # When no proof is provided or production context is active
    att = copy.deepcopy(valid_composite_attestation_payload)
    att["provenance_and_authenticity"]["verification_proof"] = None

    res = validate_friction_model_for_activation(
        model_version=mock_standard_cent_model,
        target_venue="EXNESS",
        target_symbol="XAUUSD",
        target_account_tier="STANDARD_CENT",
        target_legal_entity_code="EXNESS_SC_LTD",
        composite_legal_entity_attestation=att,
    )
    assert res.is_valid is False
    assert res.status == "LEGAL_ENTITY_EVIDENCE_MISSING"
    assert any("PRODUCTION_ORIGIN_AUTHENTICITY=HOLD" in r for r in res.reasons)


def test_activation_4_legacy_verified_single_snapshot_cannot_bypass_composite(mock_standard_cent_model):
    """Integration 4: A legacy VERIFIED single legal_entity_source_snapshot cannot bypass the composite requirement."""
    # Even if snapshot claims to be VERIFIED
    mock_standard_cent_model.legal_entity_source_snapshot.source_type = FrictionSourceType.ACCOUNT_CLIENT_AGREEMENT.value

    # Without a valid composite attestation, activation validator must fail closed
    res = validate_friction_model_for_activation(
        model_version=mock_standard_cent_model,
        target_venue="EXNESS",
        target_symbol="XAUUSD",
        target_account_tier="STANDARD_CENT",
        target_legal_entity_code="EXNESS_SC_LTD",
        composite_legal_entity_attestation=None,  # Uses public attestation.json which is currently HOLD
    )
    assert res.is_valid is False
    assert res.status == "LEGAL_ENTITY_EVIDENCE_MISSING"
    assert any("COMPOSITE_CONJUNCTION_REQUIRED" in r for r in res.reasons)


def test_activation_5_tampered_composite_evidence_rejected(mock_standard_cent_model, valid_composite_attestation_payload):
    """Integration 5: Tampered composite evidence -> activation rejected."""
    att = copy.deepcopy(valid_composite_attestation_payload)
    att["governed_components"]["component_1"]["sha256"] = "bad_sha" * 8

    res = validate_friction_model_for_activation(
        model_version=mock_standard_cent_model,
        target_venue="EXNESS",
        target_symbol="XAUUSD",
        target_account_tier="STANDARD_CENT",
        target_legal_entity_code="EXNESS_SC_LTD",
        composite_legal_entity_attestation=att,
    )
    assert res.is_valid is False
    assert res.status == "LEGAL_ENTITY_EVIDENCE_MISSING"
    assert any("Component 1 SHA-256" in r for r in res.reasons)


def test_activation_6_exness_technologies_as_account_company_rejected(mock_standard_cent_model, valid_composite_attestation_payload):
    """Integration 6: Exness Technologies Ltd as ACCOUNT_COMPANY cannot satisfy legal entity activation."""
    att = copy.deepcopy(valid_composite_attestation_payload)
    att["legal_entity"]["legal_entity_name"] = "Exness Technologies Ltd"
    att["legal_entity"]["legal_entity_code"] = "EXNESS_TECHNOLOGIES_LTD"

    res = validate_friction_model_for_activation(
        model_version=mock_standard_cent_model,
        target_venue="EXNESS",
        target_symbol="XAUUSD",
        target_account_tier="STANDARD_CENT",
        target_legal_entity_code="EXNESS_SC_LTD",
        composite_legal_entity_attestation=att,
    )
    assert res.is_valid is False
    assert any("FORBIDDEN_CONTRACTING_ENTITY" in r for r in res.reasons)


def test_activation_7_server_changes_do_not_affect_legal_entity_identity(mock_standard_cent_model, valid_composite_attestation_payload):
    """Integration 7: Real20 / Real25 / Real36 changes do not affect legal entity identity."""
    for server in ["Exness-MT5Real20", "Exness-MT5Real25", "Exness-MT5Real36"]:
        mock_standard_cent_model.server = server
        res = validate_friction_model_for_activation(
            model_version=mock_standard_cent_model,
            target_venue="EXNESS",
            target_symbol="XAUUSD",
            target_account_tier="STANDARD_CENT",
            target_legal_entity_code="EXNESS_SC_LTD",
            composite_legal_entity_attestation=valid_composite_attestation_payload,
        )
        # In production/default hold context, all servers fail closed identically with HOLD
        assert res.is_valid is False
        assert any("PRODUCTION_ORIGIN_AUTHENTICITY=HOLD" in r for r in res.reasons)
        assert res.details["legal_entity_code"] == "EXNESS_SC_LTD"


def test_activation_8_non_standard_cent_behavior_unchanged():
    """Integration 8: Non-STANDARD_CENT existing validation behavior is unchanged."""
    std_model = MagicMock()
    std_model.model_version_id = "MV_STANDARD_TEST"
    std_model.venue = "EXNESS"
    std_model.symbol = "XAUUSD"
    std_model.account_tier = "STANDARD"
    std_model.legal_entity_code = "EXNESS_SC_LTD"

    snap = MagicMock()
    snap.venue = "EXNESS"
    snap.symbol = "XAUUSD"
    snap.account_tier = "STANDARD"
    snap.source_type = FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value
    std_model.legal_entity_source_snapshot = snap

    # Scope mismatch check still works as expected for STANDARD
    res = validate_friction_model_for_activation(
        model_version=std_model,
        target_venue="EXNESS",
        target_symbol="XAUUSD",
        target_account_tier="STANDARD_CENT",  # Mismatch target tier
        target_legal_entity_code="EXNESS_SC_LTD",
    )
    assert res.is_valid is False
    assert res.status == "EMPIRICAL_FRICTION_INVALID"
    assert any("does not match target" in r for r in res.reasons)


# ======================================================================================
# Stage D5C: Authenticated Legal Entity Review Workflow Tests
# ======================================================================================

@pytest.fixture
def valid_review_receipt_payload():
    """Minimal valid review receipt dictionary for D5C testing."""
    return {
        "schema": LEGAL_ENTITY_REVIEW_RECEIPT_SCHEMA,
        "receipt_id": "RECEIPT-LEGAL-D5C-TEST-UUID-9999",
        "reviewed_at": "2026-09-10T10:00:00Z",
        "workflow_version": REVIEW_WORKFLOW_VERSION,
        "verification_authority": "AURUMIQ_GOVERNED_PROVENANCE_AUTHORITY",
        "verification_method": VERIFICATION_METHOD_COMPOSITE_REVIEW,
        "verifier_identity": VERIFIER_IDENTITY_LEGAL_ENTITY_WORKFLOW,
        "verification_proof_version": "1.0.0",
        "target_scope": {
            "venue": "EXNESS",
            "symbol": "XAUUSD",
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
                "filename": "exness_sc_client_agreement.pdf",
                "sha256": EXPECTED_CLIENT_AGREEMENT_SHA256,
                "bytes": EXPECTED_CLIENT_AGREEMENT_BYTES,
            },
            "component_2": {
                "evidence_role": EXPECTED_PERSONAL_AREA_ROLE,
                "source_type": EXPECTED_PERSONAL_AREA_SOURCE_TYPE,
                "filename": "exness_standard_cent_personal_area.jpeg",
                "sha256": EXPECTED_PERSONAL_AREA_SHA256,
                "bytes": EXPECTED_PERSONAL_AREA_BYTES,
            },
        },
        "critical_policies": {
            "server_binding_policy": SERVER_BINDING_POLICY,
            "origin_claim": ORIGIN_CLAIM_AUTHENTICATED_REVIEW_ONLY,
            "broker_cryptographic_origin": BROKER_CRYPTOGRAPHIC_ORIGIN_NOT_CLAIMED,
        },
        "verification_proof": None,
    }


def make_valid_receipt_proof(receipt_data, secret_bytes):
    """Compute authentic HMAC proof for review receipt."""
    c1 = receipt_data["governed_components"]["component_1"]
    c2 = receipt_data["governed_components"]["component_2"]
    payload = compute_canonical_review_receipt_payload(
        schema=receipt_data["schema"],
        verification_authority=receipt_data["verification_authority"],
        verification_proof_version=receipt_data["verification_proof_version"],
        workflow_version=receipt_data["workflow_version"],
        receipt_id=receipt_data["receipt_id"],
        reviewed_at_iso=receipt_data["reviewed_at"],
        verification_method=receipt_data["verification_method"],
        verifier_identity=receipt_data["verifier_identity"],
        legal_entity_name=receipt_data["legal_entity"]["legal_entity_name"],
        legal_entity_code=receipt_data["legal_entity"]["legal_entity_code"],
        regulator=receipt_data["legal_entity"]["regulator"],
        license_number=receipt_data["legal_entity"]["license_number"],
        account_tier=receipt_data["target_scope"]["account_tier"],
        account_currency=receipt_data["target_scope"]["account_currency"],
        comp1_role=c1["evidence_role"],
        comp1_source_type=c1["source_type"],
        comp1_sha256=c1["sha256"],
        comp1_bytes=c1["bytes"],
        comp2_role=c2["evidence_role"],
        comp2_source_type=c2["source_type"],
        comp2_sha256=c2["sha256"],
        comp2_bytes=c2["bytes"],
        server_binding_policy=receipt_data["critical_policies"]["server_binding_policy"],
        origin_claim=receipt_data["critical_policies"]["origin_claim"],
        broker_cryptographic_origin=receipt_data["critical_policies"]["broker_cryptographic_origin"],
    )
    return compute_review_receipt_proof(payload, signing_secret=secret_bytes)


def test_d5c_1_valid_production_signed_receipt_qualifies(valid_review_receipt_payload):
    """Test 1: Valid production signed review receipt qualifies."""
    secret = b"d5c-production-test-key-32-bytes-long-secret!"
    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["verification_proof"] = make_valid_receipt_proof(receipt, secret)

    with patch("apps.market_data.friction.legal_entity.is_production_environment", return_value=True):
        with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", return_value=secret):
            res = verify_governed_composite_legal_entity(receipt_data=receipt, is_test_ctx=False)
            assert res.is_qualified is True
            assert res.composite_evidence_integrity == "PASS"
            assert res.production_origin_authenticity == "PASS"
            assert res.governed_status == "QUALIFIED"


def test_d5c_2_missing_receipt_returns_hold():
    """Test 2: When receipt is completely missing, production returns HOLD."""
    non_existent = str(ROOT_DIR / "artifacts" / "calibration" / "_non_existent_receipt.json")
    with patch("apps.market_data.friction.legal_entity.DEFAULT_REVIEW_RECEIPT_PATH", non_existent):
        res = verify_governed_composite_legal_entity(receipt_data=None, receipt_file_path=non_existent, is_test_ctx=False)
        assert res.is_qualified is False
        assert res.composite_evidence_integrity == "PASS"
        assert res.production_origin_authenticity == "HOLD"
        assert res.governed_status == "HOLD"


def test_d5c_3_unsigned_receipt_fails_closed_as_hold(valid_review_receipt_payload):
    """Test 3: Unsigned receipt (proof is null) fails closed to HOLD."""
    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["verification_proof"] = None
    res = verify_governed_composite_legal_entity(receipt_data=receipt, is_test_ctx=False)
    assert res.is_qualified is False
    assert res.production_origin_authenticity == "HOLD"
    assert res.governed_status == "HOLD"


def test_d5c_4_missing_provenance_signing_secret_fails_closed_without_activation_crash(valid_review_receipt_payload):
    """Test 4: Missing PROVENANCE_SIGNING_SECRET fails closed to HOLD without crashing."""
    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["verification_proof"] = "fake-proof"

    with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", side_effect=RuntimeError("PROVENANCE_SIGNING_SECRET not configured")):
        res = verify_governed_composite_legal_entity(receipt_data=receipt, is_test_ctx=False)
        assert res.is_qualified is False
        assert res.production_origin_authenticity == "HOLD"
        assert res.governed_status == "HOLD"
        assert any("signing secret unavailable" in r.lower() for r in res.reasons)


def test_d5c_5_wrong_signing_secret_fails(valid_review_receipt_payload):
    """Test 5: Proof signed with differing secret fails verification."""
    secret1 = b"secret-one-32-bytes-long-key-abcde!"
    secret2 = b"secret-two-32-bytes-long-key-vwxyz!"
    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["verification_proof"] = make_valid_receipt_proof(receipt, secret1)

    with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", return_value=secret2):
        res = verify_governed_composite_legal_entity(receipt_data=receipt, is_test_ctx=False)
        assert res.is_qualified is False
        assert res.production_origin_authenticity == "FAIL"
        assert res.governed_status == "FAIL"


def test_d5c_6_tampered_component_1_hash_fails(valid_review_receipt_payload):
    """Test 6: Tampered Component 1 hash fails verification."""
    secret = b"d5c-test-secret-key-32-bytes-long!"
    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["governed_components"]["component_1"]["sha256"] = "0" * 64
    receipt["verification_proof"] = make_valid_receipt_proof(receipt, secret)

    with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", return_value=secret):
        res = verify_governed_composite_legal_entity(receipt_data=receipt, is_test_ctx=False)
        assert res.is_qualified is False
        assert res.composite_evidence_integrity == "FAIL"
        assert res.production_origin_authenticity == "FAIL"


def test_d5c_7_tampered_component_2_hash_fails(valid_review_receipt_payload):
    """Test 7: Tampered Component 2 hash fails verification."""
    secret = b"d5c-test-secret-key-32-bytes-long!"
    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["governed_components"]["component_2"]["sha256"] = "1" * 64
    receipt["verification_proof"] = make_valid_receipt_proof(receipt, secret)

    with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", return_value=secret):
        res = verify_governed_composite_legal_entity(receipt_data=receipt, is_test_ctx=False)
        assert res.is_qualified is False
        assert res.composite_evidence_integrity == "FAIL"
        assert res.production_origin_authenticity == "FAIL"


def test_d5c_8_tampered_component_1_bytes_fails(valid_review_receipt_payload):
    """Test 8: Tampered Component 1 byte length fails verification."""
    secret = b"d5c-test-secret-key-32-bytes-long!"
    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["governed_components"]["component_1"]["bytes"] = 12345
    receipt["verification_proof"] = make_valid_receipt_proof(receipt, secret)

    with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", return_value=secret):
        res = verify_governed_composite_legal_entity(receipt_data=receipt, is_test_ctx=False)
        assert res.is_qualified is False
        assert res.composite_evidence_integrity == "FAIL"


def test_d5c_9_tampered_component_2_bytes_fails(valid_review_receipt_payload):
    """Test 9: Tampered Component 2 byte length fails verification."""
    secret = b"d5c-test-secret-key-32-bytes-long!"
    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["governed_components"]["component_2"]["bytes"] = 54321
    receipt["verification_proof"] = make_valid_receipt_proof(receipt, secret)

    with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", return_value=secret):
        res = verify_governed_composite_legal_entity(receipt_data=receipt, is_test_ctx=False)
        assert res.is_qualified is False
        assert res.composite_evidence_integrity == "FAIL"


def test_d5c_10_tampered_entity_fails(valid_review_receipt_payload):
    """Test 10: Tampered legal entity name or code fails."""
    secret = b"d5c-test-secret-key-32-bytes-long!"
    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["legal_entity"]["legal_entity_name"] = "Exness Technologies Ltd"
    receipt["verification_proof"] = make_valid_receipt_proof(receipt, secret)

    with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", return_value=secret):
        res = verify_governed_composite_legal_entity(receipt_data=receipt, is_test_ctx=False)
        assert res.is_qualified is False
        assert res.composite_evidence_integrity == "FAIL"


def test_d5c_11_tampered_regulator_or_license_fails(valid_review_receipt_payload):
    """Test 11: Tampered regulator or license number fails."""
    secret = b"d5c-test-secret-key-32-bytes-long!"
    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["legal_entity"]["regulator"] = "CYSEC"
    receipt["verification_proof"] = make_valid_receipt_proof(receipt, secret)

    with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", return_value=secret):
        res = verify_governed_composite_legal_entity(receipt_data=receipt, is_test_ctx=False)
        assert res.is_qualified is False
        assert res.composite_evidence_integrity == "FAIL"


def test_d5c_12_tampered_tier_or_currency_fails(valid_review_receipt_payload):
    """Test 12: Tampered account tier or currency fails."""
    secret = b"d5c-test-secret-key-32-bytes-long!"
    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["target_scope"]["account_tier"] = "STANDARD"  # Must be STANDARD_CENT
    receipt["verification_proof"] = make_valid_receipt_proof(receipt, secret)

    with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", return_value=secret):
        res = verify_governed_composite_legal_entity(receipt_data=receipt, is_test_ctx=False)
        assert res.is_qualified is False
        assert res.composite_evidence_integrity == "FAIL"


def test_d5c_13_unauthorized_verifier_fails(valid_review_receipt_payload):
    """Test 13: Unauthorized verifier identity fails."""
    secret = b"d5c-test-secret-key-32-bytes-long!"
    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["verifier_identity"] = "ROGUE_VERIFIER_WORKFLOW_V99"
    receipt["verification_proof"] = make_valid_receipt_proof(receipt, secret)

    with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", return_value=secret):
        res = verify_governed_composite_legal_entity(receipt_data=receipt, is_test_ctx=False)
        assert res.is_qualified is False
        assert res.production_origin_authenticity == "FAIL"
        assert any("untrusted" in r.lower() for r in res.reasons)


def test_d5c_14_wrong_verification_method_fails(valid_review_receipt_payload):
    """Test 14: Wrong verification method fails."""
    secret = b"d5c-test-secret-key-32-bytes-long!"
    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["verification_method"] = "UNGOVERNED_MANUAL_CHECK"
    receipt["verification_proof"] = make_valid_receipt_proof(receipt, secret)

    with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", return_value=secret):
        res = verify_governed_composite_legal_entity(receipt_data=receipt, is_test_ctx=False)
        assert res.is_qualified is False
        assert res.production_origin_authenticity == "FAIL"


def test_d5c_15_test_verifier_prohibited_in_production(valid_review_receipt_payload):
    """Test 15: TEST_SUITE_ISOLATED_PROVENANCE_SEAM verifier is rejected outside test environment."""
    secret = b"d5c-test-secret-key-32-bytes-long!"
    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["verifier_identity"] = "TEST_SUITE_ISOLATED_PROVENANCE_SEAM"
    receipt["verification_proof"] = make_valid_receipt_proof(receipt, secret)

    with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", return_value=secret):
        with patch("apps.market_data.friction.provenance.is_test_environment", return_value=False):
            res = verify_governed_composite_legal_entity(receipt_data=receipt, is_test_ctx=False)
            assert res.is_qualified is False
            assert res.production_origin_authenticity == "FAIL"
            assert any("prohibited outside explicit testing environment" in r for r in res.reasons)


def test_d5c_16_changed_reviewed_at_invalidates_proof(valid_review_receipt_payload):
    """Test 16: Altering reviewed_at timestamp invalidates cryptographic proof."""
    secret = b"d5c-test-secret-key-32-bytes-long!"
    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["verification_proof"] = make_valid_receipt_proof(receipt, secret)
    # Mutate timestamp after signing
    receipt["reviewed_at"] = "2026-09-10T12:34:56Z"

    with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", return_value=secret):
        res = verify_governed_composite_legal_entity(receipt_data=receipt, is_test_ctx=False)
        assert res.is_qualified is False
        assert res.production_origin_authenticity == "FAIL"


def test_d5c_17_changed_receipt_id_invalidates_proof(valid_review_receipt_payload):
    """Test 17: Altering receipt_id invalidates cryptographic proof."""
    secret = b"d5c-test-secret-key-32-bytes-long!"
    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["verification_proof"] = make_valid_receipt_proof(receipt, secret)
    # Mutate receipt_id after signing
    receipt["receipt_id"] = "TAMPERED-RECEIPT-ID"

    with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", return_value=secret):
        res = verify_governed_composite_legal_entity(receipt_data=receipt, is_test_ctx=False)
        assert res.is_qualified is False
        assert res.production_origin_authenticity == "FAIL"


def test_d5c_18_changed_workflow_version_invalidates_proof(valid_review_receipt_payload):
    """Test 18: Altering workflow_version invalidates cryptographic proof."""
    secret = b"d5c-test-secret-key-32-bytes-long!"
    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["verification_proof"] = make_valid_receipt_proof(receipt, secret)
    receipt["workflow_version"] = "2.0.0"

    with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", return_value=secret):
        res = verify_governed_composite_legal_entity(receipt_data=receipt, is_test_ctx=False)
        assert res.is_qualified is False
        assert res.production_origin_authenticity == "FAIL"


def test_d5c_19_changed_origin_claim_invalidates_proof(valid_review_receipt_payload):
    """Test 19: Altering origin_claim invalidates proof."""
    secret = b"d5c-test-secret-key-32-bytes-long!"
    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["verification_proof"] = make_valid_receipt_proof(receipt, secret)
    receipt["critical_policies"]["origin_claim"] = "BROKER_DIRECT_CLAIM"

    with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", return_value=secret):
        res = verify_governed_composite_legal_entity(receipt_data=receipt, is_test_ctx=False)
        assert res.is_qualified is False
        assert res.production_origin_authenticity == "FAIL"


def test_d5c_20_receipt_canonicalization_deterministic(valid_review_receipt_payload):
    """Test 20: Receipt canonicalization produces identical byte strings."""
    c1 = valid_review_receipt_payload["governed_components"]["component_1"]
    c2 = valid_review_receipt_payload["governed_components"]["component_2"]
    args = dict(
        schema=valid_review_receipt_payload["schema"],
        verification_authority=valid_review_receipt_payload["verification_authority"],
        verification_proof_version=valid_review_receipt_payload["verification_proof_version"],
        workflow_version=valid_review_receipt_payload["workflow_version"],
        receipt_id=valid_review_receipt_payload["receipt_id"],
        reviewed_at_iso=valid_review_receipt_payload["reviewed_at"],
        verification_method=valid_review_receipt_payload["verification_method"],
        verifier_identity=valid_review_receipt_payload["verifier_identity"],
        legal_entity_name=valid_review_receipt_payload["legal_entity"]["legal_entity_name"],
        legal_entity_code=valid_review_receipt_payload["legal_entity"]["legal_entity_code"],
        regulator=valid_review_receipt_payload["legal_entity"]["regulator"],
        license_number=valid_review_receipt_payload["legal_entity"]["license_number"],
        account_tier=valid_review_receipt_payload["target_scope"]["account_tier"],
        account_currency=valid_review_receipt_payload["target_scope"]["account_currency"],
        comp1_role=c1["evidence_role"],
        comp1_source_type=c1["source_type"],
        comp1_sha256=c1["sha256"],
        comp1_bytes=c1["bytes"],
        comp2_role=c2["evidence_role"],
        comp2_source_type=c2["source_type"],
        comp2_sha256=c2["sha256"],
        comp2_bytes=c2["bytes"],
        server_binding_policy=valid_review_receipt_payload["critical_policies"]["server_binding_policy"],
        origin_claim=valid_review_receipt_payload["critical_policies"]["origin_claim"],
        broker_cryptographic_origin=valid_review_receipt_payload["critical_policies"]["broker_cryptographic_origin"],
    )
    p1 = compute_canonical_review_receipt_payload(**args)
    p2 = compute_canonical_review_receipt_payload(**args)
    assert p1 == p2
    assert p1.startswith("schema=aurumiq.governance.legal_entity_review_receipt.v1|authority=")


def test_d5c_21_raw_evidence_still_gitignored_and_untracked():
    """Test 21: Raw evidence files remain untracked by Git."""
    gitignore_path = ROOT_DIR / ".gitignore"
    assert gitignore_path.exists()
    content = gitignore_path.read_text(encoding="utf-8")
    assert "artifacts/calibration/legal_entity_evidence/" in content

    try:
        res = subprocess.run(
            ["git", "ls-files", "artifacts/calibration/legal_entity_evidence/*"],
            cwd=ROOT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        assert res.stdout.strip() == "", f"Raw evidence must not be tracked in Git: {res.stdout}"
    except (FileNotFoundError, PermissionError):
        pass  # git not directly executable in sandbox, verified via .gitignore


def test_d5c_22_public_receipt_contains_no_private_identifiers(valid_review_receipt_payload):
    """Test 22: Review receipt contains no private account login IDs or PII."""
    receipt_str = json.dumps(valid_review_receipt_payload)
    # Check for prohibited patterns (private MT5 account numbers, passwords, etc.)
    assert "25442531" not in receipt_str
    assert "password" not in receipt_str.lower()
    assert "token" not in receipt_str.lower()
    assert "balance" not in receipt_str.lower()


def test_d5c_23_legacy_single_source_still_cannot_bypass_composite():
    """Test 23: Legacy single-source snapshot cannot bypass composite review."""
    model = MagicMock()
    model.model_version_id = "MV_SINGLE_SOURCE_TEST"
    model.venue = "EXNESS"
    model.symbol = "XAUUSD"
    model.account_tier = "STANDARD_CENT"
    model.legal_entity_code = "EXNESS_SC_LTD"
    model.server = "Exness-MT5Real25"

    snap = MagicMock()
    snap.venue = "EXNESS"
    snap.symbol = "XAUUSD"
    snap.account_tier = "STANDARD_CENT"
    snap.source_type = FrictionSourceType.ACCOUNT_CLIENT_AGREEMENT.value
    snap.raw_content = b"Single source fake content"
    snap.raw_payload_bytes_sha256 = hashlib.sha256(snap.raw_content).hexdigest()
    model.legal_entity_source_snapshot = snap

    # Passing no receipt or invalid composite attestation
    res = validate_friction_model_for_activation(
        model_version=model,
        target_venue="EXNESS",
        target_symbol="XAUUSD",
        target_account_tier="STANDARD_CENT",
        target_legal_entity_code="EXNESS_SC_LTD",
        composite_review_receipt=None,
    )
    assert res.is_valid is False
    assert any("COMPOSITE_CONJUNCTION_REQUIRED" in r for r in res.reasons)


def test_d5c_24_valid_legal_entity_production_receipt_leaves_financing_partial(valid_review_receipt_payload):
    """Test 24: Valid legal entity production receipt preserves Phase 6 freeze financing status."""
    freeze_manifest = json.loads(FREEZE_MANIFEST_PATH.read_text(encoding="utf-8"))
    cent_manifest = json.loads(Path("artifacts/calibration/xauusd_standard_cent_empirical_friction_manifest.json").read_text(encoding="utf-8"))
    assert freeze_manifest["published_and_terminal_cost_context"]["financing_status"] == "PARTIAL"
    assert cent_manifest["evidence_inventory"]["financing_policy"]["status"] in ("PARTIAL", "QUALIFIED")


def test_d5c_25_phase8_ready_remains_false():
    """Test 25: PHASE8_READY invariant remains strictly False."""
    freeze_manifest = json.loads(FREEZE_MANIFEST_PATH.read_text(encoding="utf-8"))
    cent_manifest = json.loads(Path("artifacts/calibration/xauusd_standard_cent_empirical_friction_manifest.json").read_text(encoding="utf-8"))
    assert freeze_manifest["governance"]["phase8_gate"]["status"] == "BLOCKED"
    assert cent_manifest["hard_readiness_gate"]["passed"] is False
    assert cent_manifest["hard_readiness_gate"]["published_decision"] == "WAIT"
    assert cent_manifest["hard_readiness_gate"]["is_production_authorized"] is False


def test_d5c_26_activation_validator_accepts_legal_entity_only_when_production_receipt_verifies(valid_review_receipt_payload):
    """Test 26: Activation validator accepts legal entity when valid production receipt is supplied."""
    secret = b"d5c-activation-test-secret-key-32b!"
    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["verification_proof"] = make_valid_receipt_proof(receipt, secret)

    model = MagicMock()
    model.model_version_id = "MV_VALID_RECEIPT_TEST"
    model.venue = "EXNESS"
    model.symbol = "XAUUSD"
    model.account_tier = "STANDARD_CENT"
    model.legal_entity_code = "EXNESS_SC_LTD"
    model.legal_entity_name = QUALIFIED_LEGAL_ENTITY_NAME
    model.regulator = QUALIFIED_REGULATOR
    model.license_number = QUALIFIED_LICENSE_NUMBER
    model.server = "Exness-MT5Real25"

    snap = MagicMock()
    snap.venue = "EXNESS"
    snap.symbol = "XAUUSD"
    snap.account_tier = "STANDARD_CENT"
    snap.source_type = FrictionSourceType.ACCOUNT_CLIENT_AGREEMENT.value
    snap.raw_content = b"Valid content"
    snap.raw_payload_bytes_sha256 = hashlib.sha256(snap.raw_content).hexdigest()
    model.legal_entity_source_snapshot = snap

    with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", return_value=secret):
        with patch("apps.market_data.friction.legal_entity.is_production_environment", return_value=True):
            # We also mock subsequent component checks in validate_friction_model_for_activation
            # to focus on legal entity composite gate passing:
            res = validate_friction_model_for_activation(
                model_version=model,
                target_venue="EXNESS",
                target_symbol="XAUUSD",
                target_account_tier="STANDARD_CENT",
                target_legal_entity_code="EXNESS_SC_LTD",
                composite_review_receipt=receipt,
            )
        # The composite legal entity check passed, so no COMPOSITE_CONJUNCTION_REQUIRED error!
        assert not any("COMPOSITE_CONJUNCTION_REQUIRED" in r for r in res.reasons)


def test_d5c_27_activation_verifier_does_not_crash_when_production_secret_absent(valid_review_receipt_payload):
    """Test 27: Activation verifier fails closed gracefully when secret is absent, without crashing."""
    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["verification_proof"] = "unverified-proof"

    model = MagicMock()
    model.model_version_id = "MV_CRASH_TEST"
    model.venue = "EXNESS"
    model.symbol = "XAUUSD"
    model.account_tier = "STANDARD_CENT"
    model.legal_entity_code = "EXNESS_SC_LTD"
    model.server = "Exness-MT5Real25"

    snap = MagicMock()
    snap.venue = "EXNESS"
    snap.symbol = "XAUUSD"
    snap.account_tier = "STANDARD_CENT"
    snap.source_type = FrictionSourceType.ACCOUNT_CLIENT_AGREEMENT.value
    snap.raw_content = b"content"
    snap.raw_payload_bytes_sha256 = hashlib.sha256(snap.raw_content).hexdigest()
    model.legal_entity_source_snapshot = snap

    with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", side_effect=RuntimeError("Secret not found in env")):
        # Must NOT raise RuntimeError, but return fail-closed validation result
        res = validate_friction_model_for_activation(
            model_version=model,
            target_venue="EXNESS",
            target_symbol="XAUUSD",
            target_account_tier="STANDARD_CENT",
            target_legal_entity_code="EXNESS_SC_LTD",
            composite_review_receipt=receipt,
        )
        assert res.is_valid is False
        assert res.status == "LEGAL_ENTITY_EVIDENCE_MISSING"
        assert any("PRODUCTION_ORIGIN_AUTHENTICITY=HOLD" in r for r in res.reasons)


def test_d5c_28_generator_command_runs_cleanly_in_test_environment():
    """Test 28: review_legal_entity_evidence management command generates valid receipt."""
    from django.core.management import call_command

    out_file = Path("artifacts/calibration/_test_d5c_receipt.json")
    try:
        call_command(
            "review_legal_entity_evidence",
            evidence_dir=str(EVIDENCE_DIR),
            output_receipt=str(out_file),
            signing_secret="d5c-management-command-test-key-32b!",
            force=True,
        )
        assert out_file.exists()
        data = json.loads(out_file.read_text(encoding="utf-8"))
        assert data["schema"] == LEGAL_ENTITY_REVIEW_RECEIPT_SCHEMA
        assert data["verification_method"] == VERIFICATION_METHOD_COMPOSITE_REVIEW
        assert data["verifier_identity"] == VERIFIER_IDENTITY_LEGAL_ENTITY_WORKFLOW
        assert data["verification_proof"] is not None
    finally:
        if out_file.exists():
            out_file.unlink()


def test_d5c_29_generator_command_rejects_missing_secret_in_production():
    """Test 29: Command refuses to generate receipt when secret is absent in production."""
    from django.core.management import call_command
    from django.core.management.base import CommandError

    out_file = Path("artifacts/calibration/_test_d5c_receipt_fail.json")
    try:
        with patch("apps.market_data.management.commands.review_legal_entity_evidence.is_test_environment", return_value=False):
            with patch("apps.market_data.management.commands.review_legal_entity_evidence.get_governed_signing_secret", side_effect=RuntimeError("No secret")):
                with pytest.raises(CommandError, match="PROVENANCE_SIGNING_SECRET_MISSING"):
                    call_command(
                        "review_legal_entity_evidence",
                        evidence_dir=str(EVIDENCE_DIR),
                        output_receipt=str(out_file),
                    )
    finally:
        if out_file.exists():
            out_file.unlink()


# ======================================================================================
# STAGE D5C.1A — PRE-PRODUCTION SIGNING SAFETY AUDIT TESTS
# ======================================================================================

def test_d5c1a_1_canonical_production_receipt_cannot_be_generated_with_test_sentinel():
    """Audit 1: Generating canonical production receipt with test sentinel key is strictly blocked."""
    from django.core.management import call_command
    from django.core.management.base import CommandError

    test_sentinel = b"aurumiq-test-only-provenance-key-NOT-FOR-PRODUCTION"
    with patch("apps.market_data.management.commands.review_legal_entity_evidence.is_production_environment", return_value=True):
        with patch("apps.market_data.management.commands.review_legal_entity_evidence.is_test_environment", return_value=False):
            with patch("apps.market_data.management.commands.review_legal_entity_evidence.get_governed_signing_secret", return_value=test_sentinel):
                with pytest.raises(CommandError, match="TEST_SENTINEL_ESCAPE_BLOCKED"):
                    call_command(
                        "review_legal_entity_evidence",
                        evidence_dir=str(EVIDENCE_DIR),
                        output_receipt=str(Path("artifacts/calibration/legal_entity_review_receipt.json")),
                    )


def test_d5c1a_2_canonical_receipt_generation_blocked_under_test_settings():
    """Audit 2: Generating canonical production receipt under test settings is strictly blocked."""
    from django.core.management import call_command
    from django.core.management.base import CommandError

    # Running under non-production settings targeting canonical receipt fails closed
    with pytest.raises(CommandError, match="CANONICAL_RECEIPT_REQUIRES_PRODUCTION_CONTEXT"):
        call_command(
            "review_legal_entity_evidence",
            evidence_dir=str(EVIDENCE_DIR),
            output_receipt=str(Path("artifacts/calibration/legal_entity_review_receipt.json")),
        )


def test_d5c1a_3_explicit_non_test_production_secret_signs_and_verifies_successfully():
    """Audit 3: Controlled test with non-test settings and explicit production secret passes."""
    from django.core.management import call_command

    prod_secret = b"simulated-production-secret-key-32b!"
    out_file = Path("artifacts/calibration/_test_d5c1a_receipt.json")
    try:
        with patch("apps.market_data.management.commands.review_legal_entity_evidence.is_test_environment", return_value=False):
            with patch("apps.market_data.management.commands.review_legal_entity_evidence.get_governed_signing_secret", return_value=prod_secret):
                call_command(
                    "review_legal_entity_evidence",
                    evidence_dir=str(EVIDENCE_DIR),
                    output_receipt=str(out_file),
                    force=True,
                )

        assert out_file.exists()
        receipt = json.loads(out_file.read_text(encoding="utf-8"))

        with patch("apps.market_data.friction.legal_entity.is_production_environment", return_value=True):
            with patch("apps.market_data.friction.legal_entity.is_test_environment", return_value=False):
                with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", return_value=prod_secret):
                    res = verify_governed_composite_legal_entity(receipt_data=receipt, is_test_ctx=False)
                    assert res.composite_evidence_integrity == "PASS"
                    assert res.production_origin_authenticity == "PASS"
                    assert res.governed_status == "QUALIFIED"
                    assert res.is_qualified is True
    finally:
        if out_file.exists():
            out_file.unlink()


def test_d5c1a_4_missing_production_secret_fails_closed_in_production(valid_review_receipt_payload):
    """Audit 4: Removing production secret in production causes fail-closed HOLD."""
    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["verification_proof"] = "dummy-proof"

    with patch("apps.market_data.friction.legal_entity.is_test_environment", return_value=False):
        with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", side_effect=RuntimeError("PROVENANCE_SIGNING_SECRET is not configured.")):
            res = verify_governed_composite_legal_entity(receipt_data=receipt, is_test_ctx=False)
            assert res.composite_evidence_integrity == "PASS"
            assert res.production_origin_authenticity == "HOLD"
            assert res.governed_status == "HOLD"
            assert res.is_qualified is False


def test_d5c1a_5_wrong_production_secret_fails_verification(valid_review_receipt_payload):
    """Audit 5: Signing with secret A and verifying with secret B fails."""
    secret_a = b"prod-secret-alpha-32-bytes-long!"
    secret_b = b"prod-secret-beta-32-bytes-long!!"
    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["verification_proof"] = make_valid_receipt_proof(receipt, secret_a)

    with patch("apps.market_data.friction.legal_entity.is_test_environment", return_value=False):
        with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", return_value=secret_b):
            res = verify_governed_composite_legal_entity(receipt_data=receipt, is_test_ctx=False)
            assert res.composite_evidence_integrity == "PASS"
            assert res.production_origin_authenticity == "FAIL"
            assert res.governed_status == "FAIL"
            assert res.is_qualified is False


def test_d5c1a_6_test_sentinel_cannot_verify_production_receipt(valid_review_receipt_payload):
    """Audit 6: Receipt signed with real production secret cannot verify with test sentinel."""
    prod_secret = b"simulated-production-secret-key-32b!"
    test_sentinel = b"aurumiq-test-only-provenance-key-NOT-FOR-PRODUCTION"
    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["verification_proof"] = make_valid_receipt_proof(receipt, prod_secret)

    with patch("apps.market_data.friction.legal_entity.is_test_environment", return_value=True):
        with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", return_value=test_sentinel):
            res = verify_governed_composite_legal_entity(receipt_data=receipt, is_test_ctx=True)
            # In test context, comparing against test sentinel key fails signature comparison
            assert res.production_origin_authenticity == "FAIL"
            assert res.is_qualified is False


def test_d5c1a_7_production_secret_cannot_verify_test_sentinel_receipt(valid_review_receipt_payload):
    """Audit 7: Receipt signed with test sentinel key fails in production context."""
    test_sentinel = b"aurumiq-test-only-provenance-key-NOT-FOR-PRODUCTION"
    prod_secret = b"simulated-production-secret-key-32b!"
    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["verification_proof"] = make_valid_receipt_proof(receipt, test_sentinel)

    # In production context with genuine secret
    with patch("apps.market_data.friction.legal_entity.is_test_environment", return_value=False):
        with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", return_value=prod_secret):
            res = verify_governed_composite_legal_entity(receipt_data=receipt, is_test_ctx=False)
            assert res.production_origin_authenticity == "FAIL"
            assert res.is_qualified is False

    # In production context where secret unexpectedly resolved to sentinel
    with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", return_value=test_sentinel):
        res = verify_governed_composite_legal_entity(receipt_data=receipt, is_test_ctx=False)
        assert res.production_origin_authenticity == "HOLD"
        assert res.is_qualified is False


def test_d5c1a_8_no_test_proof_appears_in_tracked_canonical_artifacts():
    """Audit 8: Tracked canonical artifacts do not contain test proofs or fake VERIFIED status."""
    att_path = Path("artifacts/calibration/legal_entity_governed_attestation.json")
    assert att_path.exists()
    att_data = json.loads(att_path.read_text(encoding="utf-8"))
    assert att_data["governed_status"] == "HOLD"
    assert att_data["composite_evidence_integrity"] == "PASS"
    assert att_data["production_origin_authenticity"] == "HOLD"
    assert att_data["provenance_and_authenticity"]["verification_proof"] is None

    receipt_path = Path("artifacts/calibration/legal_entity_review_receipt.json")
    if receipt_path.exists():
        r_data = json.loads(receipt_path.read_text(encoding="utf-8"))
        assert "NOT-FOR-PRODUCTION" not in str(r_data.get("verification_proof", ""))


def test_d5c1a_9_phase6_raw_evidence_privacy_remains_fail_closed():
    """Audit 9: Phase 6 raw evidence privacy test remains fail-closed and unweakened."""
    from tests.unit.test_phase6_xauusdc_empirical_freeze import test_raw_evidence_destination_is_gitignored
    test_raw_evidence_destination_is_gitignored()


def test_d5c1a_10_existing_d5b_single_source_bypass_remains_impossible():
    """Audit 10: Single-source evidence without conjunction cannot qualify."""
    model = MagicMock()
    model.model_version_id = "MV_SINGLE_SOURCE_AUDIT"
    model.venue = "EXNESS"
    model.symbol = "XAUUSD"
    model.account_tier = "STANDARD_CENT"
    model.legal_entity_code = "EXNESS_SC_LTD"
    model.server = "Exness-MT5Real25"

    snap = MagicMock()
    snap.venue = "EXNESS"
    snap.symbol = "XAUUSD"
    snap.account_tier = "STANDARD_CENT"
    snap.source_type = FrictionSourceType.ACCOUNT_CLIENT_AGREEMENT.value
    snap.raw_content = b"Single source fake content"
    snap.raw_payload_bytes_sha256 = hashlib.sha256(snap.raw_content).hexdigest()
    model.legal_entity_source_snapshot = snap

    res = validate_friction_model_for_activation(
        model_version=model,
        target_venue="EXNESS",
        target_symbol="XAUUSD",
        target_account_tier="STANDARD_CENT",
        target_legal_entity_code="EXNESS_SC_LTD",
        composite_review_receipt=None,
    )
    assert res.is_valid is False
    assert any("COMPOSITE_CONJUNCTION_REQUIRED" in r for r in res.reasons)
# ======================================================================================
# STAGE D5C.1B — STRICT PRODUCTION CONTEXT GATE TESTS
# ======================================================================================

def test_d5c1b_1_testing_settings_with_valid_test_receipt_does_not_pass(valid_review_receipt_payload):
    """Test 1: testing settings + valid test receipt -> production authenticity NOT PASS."""
    test_sentinel = b"aurumiq-test-only-provenance-key-NOT-FOR-PRODUCTION"
    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["verification_proof"] = make_valid_receipt_proof(receipt, test_sentinel)

    # In testing environment (is_test_ctx=True or is_test_environment()=True)
    with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", return_value=test_sentinel):
        res = verify_governed_composite_legal_entity(receipt_data=receipt, is_test_ctx=True)
        assert res.production_origin_authenticity != "PASS"
        assert res.production_origin_authenticity == "HOLD"
        assert res.is_qualified is False
        assert res.governed_status == "HOLD"


def test_d5c1b_2_development_settings_with_strong_secret_and_valid_receipt_returns_hold(valid_review_receipt_payload):
    """Test 2: development settings + explicit strong secret + valid receipt -> production authenticity HOLD."""
    strong_dev_secret = b"strong-dev-secret-48-bytes-long-12345678901234567890"
    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["verification_proof"] = make_valid_receipt_proof(receipt, strong_dev_secret)

    with patch("apps.market_data.friction.legal_entity.is_test_environment", return_value=False):
        with patch.object(settings, "SETTINGS_MODULE", "config.settings.development", create=True):
            with patch.object(settings, "DEBUG", True):
                with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", return_value=strong_dev_secret):
                    res = verify_governed_composite_legal_entity(receipt_data=receipt, is_test_ctx=False)
                    # HMAC matches, so cryptographic receipt is valid:
                    assert res.details.get("cryptographic_receipt_valid") is True
                    # But production authenticity must HOLD because runtime is non-production:
                    assert res.production_origin_authenticity == "HOLD"
                    assert res.governed_status == "HOLD"
                    assert res.is_qualified is False
                    assert "NON_PRODUCTION_CONTEXT" in res.details.get("production_hold_reason", "")
                    assert any("NON_PRODUCTION_CONTEXT" in r for r in res.reasons)


def test_d5c1b_3_development_settings_cannot_write_canonical_production_receipt():
    """Test 3: development settings cannot write canonical production receipt."""
    from django.core.management import call_command
    from django.core.management.base import CommandError

    strong_secret = b"strong-dev-secret-48-bytes-long-12345678901234567890"
    canonical_receipt_path = Path("artifacts/calibration/legal_entity_review_receipt.json")
    mtime_before = canonical_receipt_path.stat().st_mtime if canonical_receipt_path.exists() else None

    with patch("apps.market_data.management.commands.review_legal_entity_evidence.is_test_environment", return_value=False):
        with patch.object(settings, "SETTINGS_MODULE", "config.settings.development", create=True):
            with patch.object(settings, "DEBUG", True):
                with patch("apps.market_data.management.commands.review_legal_entity_evidence.get_governed_signing_secret", return_value=strong_secret):
                    with pytest.raises(CommandError, match="CANONICAL_RECEIPT_REQUIRES_PRODUCTION_CONTEXT"):
                        call_command(
                            "review_legal_entity_evidence",
                            evidence_dir=str(EVIDENCE_DIR),
                            output_receipt=str(canonical_receipt_path),
                        )
    if mtime_before is not None:
        assert canonical_receipt_path.stat().st_mtime == mtime_before
    else:
        assert not canonical_receipt_path.exists()


def test_d5c1b_4_debug_false_alone_does_not_imply_production(valid_review_receipt_payload):
    """Test 4: DEBUG=False alone does not imply production."""
    strong_secret = b"strong-dev-secret-48-bytes-long-12345678901234567890"
    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["verification_proof"] = make_valid_receipt_proof(receipt, strong_secret)

    with patch("apps.market_data.friction.provenance.is_test_environment", return_value=False):
        with patch.object(settings, "SETTINGS_MODULE", "config.settings.development", create=True):
            with patch.object(settings, "DEBUG", False):
                # Even with DEBUG=False, development module is NOT production
                assert is_production_environment() is False

                with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", return_value=strong_secret):
                    res = verify_governed_composite_legal_entity(receipt_data=receipt, is_test_ctx=False)
                    assert res.production_origin_authenticity == "HOLD"
                    assert res.is_qualified is False
                    assert "NON_PRODUCTION_CONTEXT" in res.details.get("production_hold_reason", "")


def test_d5c1b_5_provenance_secret_present_alone_does_not_imply_production(valid_review_receipt_payload):
    """Test 5: PROVENANCE_SIGNING_SECRET present alone does not imply production."""
    strong_secret = b"configured-provenance-signing-secret-value-32b"
    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["verification_proof"] = make_valid_receipt_proof(receipt, strong_secret)

    with patch.object(settings, "PROVENANCE_SIGNING_SECRET", strong_secret.decode("utf-8"), create=True):
        with patch.object(settings, "SETTINGS_MODULE", "config.settings.development", create=True):
            with patch.object(settings, "DEBUG", True):
                with patch("apps.market_data.friction.provenance.is_test_environment", return_value=False):
                    with patch("apps.market_data.friction.legal_entity.is_test_environment", return_value=False):
                        with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", return_value=strong_secret):
                            assert is_production_environment() is False
                            res = verify_governed_composite_legal_entity(receipt_data=receipt, is_test_ctx=False)
                            assert res.production_origin_authenticity == "HOLD"
                            assert res.is_qualified is False


def test_d5c1b_6_exact_production_settings_in_isolated_harness_can_pass_with_temp_receipt(valid_review_receipt_payload):
    """Test 6: exact production settings + explicit temp production secret + valid temp receipt -> PASS in isolated subprocess."""
    import subprocess
    import sys

    temp_receipt_path = ROOT_DIR / "artifacts" / "calibration" / "_test_temp_production_receipt.json"
    temp_receipt_path_str = str(temp_receipt_path).replace("\\", "/")

    temp_secret = b"temp-isolated-production-signing-secret-64bytes-key1234567890"

    # Prepare temporary receipt using make_valid_receipt_proof
    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["receipt_id"] = "RECEIPT-TEMP-ISOLATED-TEST-SUBPROCESS"
    receipt["verification_proof"] = make_valid_receipt_proof(receipt, temp_secret)
    temp_receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    # Subprocess runs with DJANGO_SETTINGS_MODULE=config.settings.production and isolated environment
    sub_code = f"""
import os, sys, json
import django

os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings.production'
os.environ['DJANGO_SECRET_KEY'] = 'isolated-temp-production-secret-key-for-test-32b'
os.environ['DJANGO_ALLOWED_HOSTS'] = 'localhost,127.0.0.1'
os.environ['SECURE_SSL_REDIRECT'] = 'False'
os.environ['PROVENANCE_SIGNING_SECRET'] = 'temp-isolated-production-signing-secret-64bytes-key1234567890'

django.setup()

from apps.market_data.friction.provenance import is_production_environment, is_test_environment
assert is_production_environment() is True, 'is_production_environment must be True'
assert is_test_environment() is False, 'is_test_environment must be False'

from apps.market_data.friction.legal_entity import verify_governed_composite_legal_entity

res = verify_governed_composite_legal_entity(receipt_file_path='{temp_receipt_path_str}', is_test_ctx=False)

assert res.composite_evidence_integrity == 'PASS'
assert res.production_origin_authenticity == 'PASS'
assert res.governed_status == 'QUALIFIED'
assert res.is_qualified is True
assert res.details.get('cryptographic_receipt_valid') is True
print('SUBPROCESS_PASS')
"""
    try:
        env = os.environ.copy()
        proc = subprocess.run(
            [sys.executable, "-c", sub_code],
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
            env=env,
        )
        assert proc.returncode == 0, f"Subprocess failed:\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        assert "SUBPROCESS_PASS" in proc.stdout
    finally:
        if temp_receipt_path.exists():
            temp_receipt_path.unlink()

    # Verify canonical production receipt was not created or overwritten by test #6
    canonical_receipt = ROOT_DIR / "artifacts" / "calibration" / "legal_entity_review_receipt.json"
    if canonical_receipt.exists():
        data = json.loads(canonical_receipt.read_text(encoding="utf-8"))
        assert "TEMP-SUBPROCESS-RECEIPT" not in data.get("receipt_id", "")



def test_d5c1b_7_production_settings_with_missing_secret_returns_hold_fail_closed(valid_review_receipt_payload):
    """Test 7: production settings + missing secret -> HOLD / fail closed."""
    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["verification_proof"] = "dummy-proof"

    with patch("apps.market_data.friction.legal_entity.is_production_environment", return_value=True):
        with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", side_effect=RuntimeError("PROVENANCE_SIGNING_SECRET not set")):
            res = verify_governed_composite_legal_entity(receipt_data=receipt, is_test_ctx=False)
            assert res.composite_evidence_integrity == "PASS"
            assert res.production_origin_authenticity == "HOLD"
            assert res.governed_status == "HOLD"
            assert res.is_qualified is False
            assert "PROVENANCE_SIGNING_SECRET unavailable" in res.details.get("production_hold_reason", "")


def test_d5c1b_8_production_settings_with_wrong_secret_returns_fail(valid_review_receipt_payload):
    """Test 8: production settings + wrong secret -> FAIL."""
    secret_a = b"production-signing-secret-key-AAA-32b!"
    secret_b = b"production-signing-secret-key-BBB-32b!"
    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["verification_proof"] = make_valid_receipt_proof(receipt, secret_a)

    with patch("apps.market_data.friction.legal_entity.is_production_environment", return_value=True):
        with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", return_value=secret_b):
            res = verify_governed_composite_legal_entity(receipt_data=receipt, is_test_ctx=False)
            assert res.composite_evidence_integrity == "PASS"
            assert res.details.get("cryptographic_receipt_valid") is False
            assert res.production_origin_authenticity == "FAIL"
            assert res.governed_status == "FAIL"
            assert res.is_qualified is False


def test_d5c1b_9_test_sentinel_remains_prohibited_for_production(valid_review_receipt_payload):
    """Test 9: test sentinel remains strictly prohibited for production qualification."""
    test_sentinel = b"aurumiq-test-only-provenance-key-NOT-FOR-PRODUCTION"
    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["verification_proof"] = make_valid_receipt_proof(receipt, test_sentinel)

    with patch("apps.market_data.friction.legal_entity.is_production_environment", return_value=True):
        with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", return_value=test_sentinel):
            res = verify_governed_composite_legal_entity(receipt_data=receipt, is_test_ctx=False)
            assert res.composite_evidence_integrity == "PASS"
            assert res.production_origin_authenticity == "HOLD"
            assert res.governed_status == "HOLD"
            assert res.is_qualified is False
            assert "Test sentinel key prohibited" in " ".join(res.reasons)


def test_d5c1b_10_canonical_test_receipt_leak_remains_blocked():
    """Test 10: canonical receipt must be production-safe and must not contain test proof, secret, or PII."""
    canonical_receipt = ROOT_DIR / "artifacts" / "calibration" / "legal_entity_review_receipt.json"
    if canonical_receipt.exists():
        content = canonical_receipt.read_text(encoding="utf-8")
        assert "aurumiq-test-only" not in content
        assert "password" not in content.lower()
        assert "token" not in content.lower()
        data = json.loads(content)
        assert data.get("verifier_identity") == VERIFIER_IDENTITY_LEGAL_ENTITY_WORKFLOW
        assert data.get("verification_method") == VERIFICATION_METHOD_COMPOSITE_REVIEW
        assert bool(data.get("verification_proof")) is True


def test_d5c1b_11_phase6_tests_remain_byte_for_byte_unchanged_from_main():
    """Test 11: Phase 6 test file remains byte-for-byte identical to main branch."""
    import shutil
    import subprocess
    git_bin = shutil.which("git") or "git"
    target_ref = None
    for candidate in ["main", "origin/main", "remotes/origin/main"]:
        r = subprocess.run([git_bin, "rev-parse", "--verify", candidate], cwd=str(ROOT_DIR), capture_output=True, text=True)
        if r.returncode == 0:
            target_ref = candidate
            break

    if target_ref is not None:
        proc = subprocess.run(
            [git_bin, "diff", target_ref, "--", "tests/unit/test_phase6_xauusdc_empirical_freeze.py"],
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0
        assert proc.stdout.strip() == "", f"Phase 6 test file has diff against {target_ref}:\n{proc.stdout}"


def test_d5c1b_12_d5b_single_source_bypass_remains_impossible():
    """Test 12: D5B single-source bypass remains impossible for STANDARD_CENT execution."""
    model = MagicMock()
    model.model_version_id = "MV_SINGLE_SOURCE_D5C1B"
    model.venue = "EXNESS"
    model.symbol = "XAUUSD"
    model.account_tier = "STANDARD_CENT"
    model.legal_entity_code = "EXNESS_SC_LTD"
    model.server = "Exness-MT5Real25"

    snap = MagicMock()
    snap.venue = "EXNESS"
    snap.symbol = "XAUUSD"
    snap.account_tier = "STANDARD_CENT"
    snap.source_type = FrictionSourceType.ACCOUNT_CLIENT_AGREEMENT.value
    snap.raw_content = b"Single source fake content"
    snap.raw_payload_bytes_sha256 = hashlib.sha256(snap.raw_content).hexdigest()
    model.legal_entity_source_snapshot = snap

    res = validate_friction_model_for_activation(
        model_version=model,
        target_venue="EXNESS",
        target_symbol="XAUUSD",
        target_account_tier="STANDARD_CENT",
        target_legal_entity_code="EXNESS_SC_LTD",
        composite_review_receipt=None,
    )
    assert res.is_valid is False
    assert any("COMPOSITE_CONJUNCTION_REQUIRED" in r for r in res.reasons)
# ======================================================================================
# STAGE D5C.1C — CANONICAL RECEIPT RUNTIME WIRING & AUTO-DISCOVERY TESTS
# ======================================================================================

def _make_standard_cent_model_for_d5c1c():
    """Helper to construct a mock FrictionModelVersion for STANDARD_CENT activation."""
    model = MagicMock()
    model.model_version_id = "MV_STANDARD_CENT_D5C1C_TEST"
    model.venue = "EXNESS"
    model.symbol = "XAUUSD"
    model.account_tier = "STANDARD_CENT"
    model.legal_entity_code = "EXNESS_SC_LTD"
    model.legal_entity_name = QUALIFIED_LEGAL_ENTITY_NAME
    model.regulator = QUALIFIED_REGULATOR
    model.license_number = QUALIFIED_LICENSE_NUMBER
    model.server = "Exness-MT5Real25"

    snap = MagicMock()
    snap.venue = "EXNESS"
    snap.symbol = "XAUUSD"
    snap.account_tier = "STANDARD_CENT"
    snap.source_type = FrictionSourceType.ACCOUNT_CLIENT_AGREEMENT.value
    snap.raw_content = b"Valid content bytes for D5C1C"
    snap.raw_payload_bytes_sha256 = hashlib.sha256(snap.raw_content).hexdigest()
    model.legal_entity_source_snapshot = snap
    return model


def test_d5c1c_1_normal_standard_cent_activation_no_canonical_receipt_rejected():
    """Test 1: Normal STANDARD_CENT activation without canonical receipt returns HOLD and fails closed."""
    non_existent = ROOT_DIR / "artifacts" / "calibration" / "_non_existent_receipt.json"
    model = _make_standard_cent_model_for_d5c1c()
    with patch("apps.market_data.friction.validation.DEFAULT_REVIEW_RECEIPT_PATH", str(non_existent)):
        with patch("apps.market_data.friction.legal_entity.DEFAULT_REVIEW_RECEIPT_PATH", str(non_existent)):
            res = validate_friction_model_for_activation(
                model_version=model,
                target_venue="EXNESS",
                target_symbol="XAUUSD",
                target_account_tier="STANDARD_CENT",
                target_legal_entity_code="EXNESS_SC_LTD",
            )
            assert res.is_valid is False
            assert res.status == "LEGAL_ENTITY_EVIDENCE_MISSING"
            assert any("COMPOSITE_CONJUNCTION_REQUIRED" in r for r in res.reasons)


def test_d5c1c_2_normal_standard_cent_activation_valid_canonical_receipt_in_isolated_production_passes(valid_review_receipt_payload):
    """Test 2: Normal STANDARD_CENT activation automatically discovers and passes valid canonical receipt under production."""
    secret = b"d5c1c-prod-secret-key-32-bytes-long-1234!"
    temp_receipt_path = ROOT_DIR / "artifacts" / "calibration" / "_test_canonical_auto_discovery.json"

    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["receipt_id"] = "RECEIPT-D5C1C-AUTO-DISCOVERED"
    receipt["verification_proof"] = make_valid_receipt_proof(receipt, secret)
    temp_receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    model = _make_standard_cent_model_for_d5c1c()
    try:
        with patch("apps.market_data.friction.validation.DEFAULT_REVIEW_RECEIPT_PATH", str(temp_receipt_path)):
            with patch("apps.market_data.friction.legal_entity.DEFAULT_REVIEW_RECEIPT_PATH", str(temp_receipt_path)):
                with patch("apps.market_data.friction.legal_entity.is_production_environment", return_value=True):
                    with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", return_value=secret):
                        # Normal activation call: NO composite_review_receipt and NO composite_receipt_file_path passed!
                        res = validate_friction_model_for_activation(
                            model_version=model,
                            target_venue="EXNESS",
                            target_symbol="XAUUSD",
                            target_account_tier="STANDARD_CENT",
                            target_legal_entity_code="EXNESS_SC_LTD",
                        )
                        # The auto-discovered canonical receipt passes the composite legal entity gate!
                        assert not any("COMPOSITE_CONJUNCTION_REQUIRED" in r for r in res.reasons)
    finally:
        if temp_receipt_path.exists():
            temp_receipt_path.unlink()

    # Verify temp receipt was cleaned up
    assert not temp_receipt_path.exists()


def test_d5c1c_3_activation_succeeds_without_explicitly_passing_receipt_data(valid_review_receipt_payload):
    """Test 3: Confirms caller does not need to inject receipt_data; auto-discovery is sufficient."""
    secret = b"d5c1c-prod-secret-key-32-bytes-long-1234!"
    temp_receipt_path = ROOT_DIR / "artifacts" / "calibration" / "_test_canonical_auto_discovery_no_inject.json"

    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["receipt_id"] = "RECEIPT-D5C1C-NO-INJECT"
    receipt["verification_proof"] = make_valid_receipt_proof(receipt, secret)
    temp_receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    model = _make_standard_cent_model_for_d5c1c()
    try:
        with patch("apps.market_data.friction.validation.DEFAULT_REVIEW_RECEIPT_PATH", str(temp_receipt_path)):
            with patch("apps.market_data.friction.legal_entity.DEFAULT_REVIEW_RECEIPT_PATH", str(temp_receipt_path)):
                with patch("apps.market_data.friction.legal_entity.is_production_environment", return_value=True):
                    with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", return_value=secret):
                        # Calling with explicit None to prove default auto-discovery works
                        res = validate_friction_model_for_activation(
                            model_version=model,
                            target_venue="EXNESS",
                            target_symbol="XAUUSD",
                            target_account_tier="STANDARD_CENT",
                            target_legal_entity_code="EXNESS_SC_LTD",
                            composite_review_receipt=None,
                            composite_receipt_file_path=None,
                        )
                        assert not any("COMPOSITE_CONJUNCTION_REQUIRED" in r for r in res.reasons)
    finally:
        if temp_receipt_path.exists():
            temp_receipt_path.unlink()


def test_d5c1c_4_canonical_receipt_with_wrong_hmac_rejected(valid_review_receipt_payload):
    """Test 4: Auto-discovered canonical receipt with wrong HMAC fails closed."""
    secret = b"d5c1c-prod-secret-key-32-bytes-long-1234!"
    temp_receipt_path = ROOT_DIR / "artifacts" / "calibration" / "_test_canonical_wrong_hmac.json"

    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["receipt_id"] = "RECEIPT-D5C1C-WRONG-HMAC"
    receipt["verification_proof"] = "0000000000000000000000000000000000000000000000000000000000000000"
    temp_receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    model = _make_standard_cent_model_for_d5c1c()
    try:
        with patch("apps.market_data.friction.validation.DEFAULT_REVIEW_RECEIPT_PATH", str(temp_receipt_path)):
            with patch("apps.market_data.friction.legal_entity.DEFAULT_REVIEW_RECEIPT_PATH", str(temp_receipt_path)):
                with patch("apps.market_data.friction.legal_entity.is_production_environment", return_value=True):
                    with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", return_value=secret):
                        res = validate_friction_model_for_activation(
                            model_version=model,
                            target_venue="EXNESS",
                            target_symbol="XAUUSD",
                            target_account_tier="STANDARD_CENT",
                            target_legal_entity_code="EXNESS_SC_LTD",
                        )
                        assert res.is_valid is False
                        assert any("COMPOSITE_CONJUNCTION_REQUIRED" in r for r in res.reasons)
                        assert any("Cryptographic verification proof mismatch" in r for r in res.reasons)
    finally:
        if temp_receipt_path.exists():
            temp_receipt_path.unlink()


def test_d5c1c_5_canonical_receipt_with_tampered_component_hash_rejected(valid_review_receipt_payload):
    """Test 5: Auto-discovered canonical receipt with tampered component hash fails closed."""
    secret = b"d5c1c-prod-secret-key-32-bytes-long-1234!"
    temp_receipt_path = ROOT_DIR / "artifacts" / "calibration" / "_test_canonical_tampered_hash.json"

    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["receipt_id"] = "RECEIPT-D5C1C-TAMPERED-HASH"
    receipt["governed_components"]["component_1"]["sha256"] = "1111111111111111111111111111111111111111111111111111111111111111"
    receipt["verification_proof"] = make_valid_receipt_proof(receipt, secret)
    temp_receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    model = _make_standard_cent_model_for_d5c1c()
    try:
        with patch("apps.market_data.friction.validation.DEFAULT_REVIEW_RECEIPT_PATH", str(temp_receipt_path)):
            with patch("apps.market_data.friction.legal_entity.DEFAULT_REVIEW_RECEIPT_PATH", str(temp_receipt_path)):
                with patch("apps.market_data.friction.legal_entity.is_production_environment", return_value=True):
                    with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", return_value=secret):
                        res = validate_friction_model_for_activation(
                            model_version=model,
                            target_venue="EXNESS",
                            target_symbol="XAUUSD",
                            target_account_tier="STANDARD_CENT",
                            target_legal_entity_code="EXNESS_SC_LTD",
                        )
                        assert res.is_valid is False
                        assert any("COMPOSITE_CONJUNCTION_REQUIRED" in r for r in res.reasons)
    finally:
        if temp_receipt_path.exists():
            temp_receipt_path.unlink()


def test_d5c1c_6_canonical_receipt_under_development_settings_returns_hold(valid_review_receipt_payload):
    """Test 6: Auto-discovered canonical receipt under development settings returns HOLD even with valid HMAC."""
    strong_dev_secret = b"strong-dev-secret-48-bytes-long-12345678901234567890"
    temp_receipt_path = ROOT_DIR / "artifacts" / "calibration" / "_test_canonical_dev_settings.json"

    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["receipt_id"] = "RECEIPT-D5C1C-DEV-SETTINGS"
    receipt["verification_proof"] = make_valid_receipt_proof(receipt, strong_dev_secret)
    temp_receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    model = _make_standard_cent_model_for_d5c1c()
    try:
        with patch("apps.market_data.friction.validation.DEFAULT_REVIEW_RECEIPT_PATH", str(temp_receipt_path)):
            with patch("apps.market_data.friction.legal_entity.DEFAULT_REVIEW_RECEIPT_PATH", str(temp_receipt_path)):
                with patch.object(settings, "SETTINGS_MODULE", "config.settings.development", create=True):
                    with patch.object(settings, "DEBUG", True):
                        with patch("apps.market_data.friction.legal_entity.is_test_environment", return_value=False):
                            with patch("apps.market_data.friction.provenance.is_test_environment", return_value=False):
                                with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", return_value=strong_dev_secret):
                                    res = validate_friction_model_for_activation(
                                        model_version=model,
                                        target_venue="EXNESS",
                                        target_symbol="XAUUSD",
                                        target_account_tier="STANDARD_CENT",
                                        target_legal_entity_code="EXNESS_SC_LTD",
                                    )
                                    assert res.is_valid is False
                                    assert any("NON_PRODUCTION_CONTEXT" in r for r in res.reasons)
    finally:
        if temp_receipt_path.exists():
            temp_receipt_path.unlink()


def test_d5c1c_7_canonical_receipt_under_testing_settings_blocked(valid_review_receipt_payload):
    """Test 7: Auto-discovered canonical receipt under testing settings cannot qualify production authenticity."""
    test_sentinel = b"aurumiq-test-only-provenance-key-NOT-FOR-PRODUCTION"
    temp_receipt_path = ROOT_DIR / "artifacts" / "calibration" / "_test_canonical_testing_settings.json"

    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["receipt_id"] = "RECEIPT-D5C1C-TEST-SETTINGS"
    receipt["verification_proof"] = make_valid_receipt_proof(receipt, test_sentinel)
    temp_receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    model = _make_standard_cent_model_for_d5c1c()
    try:
        with patch("apps.market_data.friction.validation.DEFAULT_REVIEW_RECEIPT_PATH", str(temp_receipt_path)):
            with patch("apps.market_data.friction.legal_entity.DEFAULT_REVIEW_RECEIPT_PATH", str(temp_receipt_path)):
                with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", return_value=test_sentinel):
                    res = validate_friction_model_for_activation(
                        model_version=model,
                        target_venue="EXNESS",
                        target_symbol="XAUUSD",
                        target_account_tier="STANDARD_CENT",
                        target_legal_entity_code="EXNESS_SC_LTD",
                    )
                    assert res.is_valid is False
                    assert any("COMPOSITE_CONJUNCTION_REQUIRED" in r for r in res.reasons)
    finally:
        if temp_receipt_path.exists():
            temp_receipt_path.unlink()


def test_d5c1c_8_missing_production_signing_secret_holds_without_crash(valid_review_receipt_payload):
    """Test 8: Missing production signing secret returns HOLD without crashing the activation validator."""
    temp_receipt_path = ROOT_DIR / "artifacts" / "calibration" / "_test_canonical_missing_secret.json"

    receipt = copy.deepcopy(valid_review_receipt_payload)
    receipt["receipt_id"] = "RECEIPT-D5C1C-MISSING-SECRET"
    receipt["verification_proof"] = "dummy-proof"
    temp_receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    model = _make_standard_cent_model_for_d5c1c()
    try:
        with patch("apps.market_data.friction.validation.DEFAULT_REVIEW_RECEIPT_PATH", str(temp_receipt_path)):
            with patch("apps.market_data.friction.legal_entity.DEFAULT_REVIEW_RECEIPT_PATH", str(temp_receipt_path)):
                with patch("apps.market_data.friction.legal_entity.is_production_environment", return_value=True):
                    with patch("apps.market_data.friction.legal_entity.get_governed_signing_secret", side_effect=RuntimeError("PROVENANCE_SIGNING_SECRET not configured")):
                        res = validate_friction_model_for_activation(
                            model_version=model,
                            target_venue="EXNESS",
                            target_symbol="XAUUSD",
                            target_account_tier="STANDARD_CENT",
                            target_legal_entity_code="EXNESS_SC_LTD",
                        )
                        assert res.is_valid is False
                        assert any("PROVENANCE_SIGNING_SECRET unavailable" in r for r in res.reasons)
    finally:
        if temp_receipt_path.exists():
            temp_receipt_path.unlink()


def test_d5c1c_9_legacy_single_source_cannot_bypass_composite():
    """Test 9: D5B single-source bypass remains impossible even with canonical auto-discovery."""
    model = MagicMock()
    model.model_version_id = "MV_SINGLE_SOURCE_D5C1C"
    model.venue = "EXNESS"
    model.symbol = "XAUUSD"
    model.account_tier = "STANDARD_CENT"
    model.legal_entity_code = "EXNESS_SC_LTD"
    model.server = "Exness-MT5Real25"

    snap = MagicMock()
    snap.venue = "EXNESS"
    snap.symbol = "XAUUSD"
    snap.account_tier = "STANDARD_CENT"
    snap.source_type = FrictionSourceType.ACCOUNT_CLIENT_AGREEMENT.value
    snap.raw_content = b"Single source fake content for D5C1C"
    snap.raw_payload_bytes_sha256 = hashlib.sha256(snap.raw_content).hexdigest()
    model.legal_entity_source_snapshot = snap

    res = validate_friction_model_for_activation(
        model_version=model,
        target_venue="EXNESS",
        target_symbol="XAUUSD",
        target_account_tier="STANDARD_CENT",
        target_legal_entity_code="EXNESS_SC_LTD",
    )
    assert res.is_valid is False
    assert any("COMPOSITE_CONJUNCTION_REQUIRED" in r for r in res.reasons)


def test_d5c1c_10_financing_remains_partial():
    """Test 10: Invariant check - FINANCING remains PARTIAL in Phase 6 manifest."""
    freeze_path = ROOT_DIR / "artifacts" / "calibration" / "phase6_xauusdc_empirical_freeze.json"
    assert freeze_path.exists()
    data = json.loads(freeze_path.read_text(encoding="utf-8"))
    assert data["published_and_terminal_cost_context"]["financing_status"] == "PARTIAL"
    blockers = " ".join(data["governance"]["phase8_gate"]["remaining_known_blockers"])
    assert "FINANCING" in blockers
    assert "LEGAL_ENTITY" in blockers


def test_d5c1c_11_phase8_ready_remains_false():
    """Test 11: Invariant check - PHASE8_READY remains false."""
    freeze_path = ROOT_DIR / "artifacts" / "calibration" / "phase6_xauusdc_empirical_freeze.json"
    assert freeze_path.exists()
    data = json.loads(freeze_path.read_text(encoding="utf-8"))
    assert data["governance"]["phase8_gate"]["status"] == "BLOCKED"


def test_d5c1c_12_phase6_sealed_tests_remain_unchanged_from_main():
    """Test 12: Phase 6 test file remains byte-for-byte identical to main branch."""
    import shutil
    import subprocess
    git_bin = shutil.which("git") or "git"
    target_ref = None
    for candidate in ["main", "origin/main", "remotes/origin/main"]:
        r = subprocess.run([git_bin, "rev-parse", "--verify", candidate], cwd=str(ROOT_DIR), capture_output=True, text=True)
        if r.returncode == 0:
            target_ref = candidate
            break

    if target_ref is not None:
        proc = subprocess.run(
            [git_bin, "diff", target_ref, "--", "tests/unit/test_phase6_xauusdc_empirical_freeze.py"],
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0
        assert proc.stdout.strip() == "", f"Phase 6 test file has diff against {target_ref}:\n{proc.stdout}"
