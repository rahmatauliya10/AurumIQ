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
from pathlib import Path
import re
import subprocess
import pytest

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
    compute_canonical_composite_payload,
    compute_composite_verification_proof,
    verify_governed_composite_legal_entity,
)
from apps.market_data.friction.provenance import (
    get_governed_signing_secret,
    is_test_environment,
)
from apps.market_data.models import FrictionSourceType

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
CALIBRATION_DIR = ROOT_DIR / "artifacts" / "calibration"
EVIDENCE_DIR = CALIBRATION_DIR / "legal_entity_evidence"
INDEX_PATH = CALIBRATION_DIR / "legal_entity_evidence_index.json"
ATTESTATION_PATH = CALIBRATION_DIR / "legal_entity_governed_attestation.json"
FREEZE_MANIFEST_PATH = CALIBRATION_DIR / "phase6_xauusdc_empirical_freeze.json"
CANONICAL_MANIFEST_PATH = CALIBRATION_DIR / "xauusd_standard_cent_empirical_friction_manifest.json"


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
    """Requirement 25: FINANCING remains strictly PARTIAL."""
    assert FREEZE_MANIFEST_PATH.exists()
    freeze = json.loads(FREEZE_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert freeze["published_and_terminal_cost_context"]["financing_status"] == "PARTIAL"

    assert CANONICAL_MANIFEST_PATH.exists()
    manifest = json.loads(CANONICAL_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["evidence_inventory"]["financing_policy"]["status"] == "PARTIAL"


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
