"""Unit tests for Stage D3: Standard Cent Web-First Broker / Account Evidence Closure.

Verifies qualification of all empirical friction categories under strict governance:
1. LEGAL_ENTITY (Requires account-bound evidence: ACCOUNT_CLIENT_AGREEMENT / BROKER_PERSONAL_AREA_EXPORT)
2. CONTRACT_GEOMETRY (Requires all 8 geometry fields + explicit broker symbol XAUUSDc)
3. COMMISSION (0.00 native commission, DYNAMIC_NOTIONAL_BPS)
4. FINANCING (swap_long=-15.5, swap_short=8.2, triple_swap=WEDNESDAY)
5. SPREAD (Strict truth fallback: sealed PR #26 lineage b2dbfaf9..., N=6,493,208; zero untrusted metadata)
6. SLIPPAGE (Strictly MISSING until Stage D4 genuine telemetry is ingested)

Enforces fail-closed completeness:
- Full completeness strictly requires ALL SIX == QUALIFIED (no PARTIAL status)
- Readiness Gate strictly CANDLES_READY_EMPIRICAL_FRICTION_MISSING, passed=False, decision=WAIT, weight=0.0
"""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import shutil
import pytest
from django.core.management import call_command

from apps.market_data.friction.artifact_parsers import (
    compute_normalized_evidence_hash,
    parse_commission_backing_artifact,
    parse_contract_spec_backing_artifact,
    parse_financing_backing_artifact,
    parse_legal_entity_backing_artifact,
)
from apps.market_data.friction.commission import (
    calculate_dynamic_fee_bps,
    calculate_side_fee_usd,
)
from apps.market_data.friction.financing import is_triple_swap_day
from apps.market_data.friction.ingestion import (
    create_friction_provenance_attestation,
    create_friction_qualification_assertion,
    ingest_friction_source_snapshot,
)
from apps.market_data.friction.provenance import (
    BrokerCaptureReceipt,
    compute_broker_receipt_auth_tag,
    compute_capture_context_hash,
    compute_verification_proof,
    create_verified_broker_capture_attestation,
)
from apps.market_data.friction.validation import validate_source_qualification_assertion
from apps.market_data.models import (
    FrictionAttestationStatus,
    FrictionComponentType,
    FrictionDistributionSummary,
    FrictionEvidenceDataset,
    FrictionQualificationStatus,
    FrictionSourceProvenanceAttestation,
    FrictionSourceSnapshot,
    FrictionSourceType,
)


def make_receipt(url: str, body_bytes: bytes, dt: datetime) -> BrokerCaptureReceipt:
    sha = hashlib.sha256(body_bytes).hexdigest()
    iso = dt.isoformat()
    tag = compute_broker_receipt_auth_tag(
        requested_url=url,
        final_url=url,
        http_status=200,
        content_type="application/json",
        response_sha256=sha,
        captured_at_iso=iso,
        collector_version="1.0.0",
        redirect_chain=(),
    )
    return BrokerCaptureReceipt(
        requested_url=url,
        final_url=url,
        http_status=200,
        content_type="application/json",
        response_bytes=body_bytes,
        response_sha256=sha,
        captured_at=dt,
        collector_version="1.0.0",
        redirect_chain=(),
        receipt_auth_tag=tag,
    )


@pytest.fixture
def standard_cent_legal_entity_payload():
    return {
        "legal_entity_name": "Exness (SC) Ltd",
        "legal_entity_code": "EXNESS_SC_LTD",
        "regulator": "FSA",
        "license_number": "SD025",
        "account_tier": "STANDARD_CENT",
        "account_currency": "USC",
        "source_type": "ACCOUNT_CLIENT_AGREEMENT",
    }


@pytest.fixture
def standard_cent_contract_spec_payload():
    return {
        "symbol": "XAUUSDc",
        "broker_symbol": "XAUUSDc",
        "account_tier": "STANDARD_CENT",
        "digits": 2,
        "point_size": 0.01,
        "trade_tick_size": 0.01,
        "trade_tick_value": 0.01,
        "contract_size": 100.0,
        "volume_min": 0.01,
        "volume_max": 200.0,
        "volume_step": 0.01,
        "source_type": "OFFICIAL_BROKER_DOCUMENT",
    }


@pytest.fixture
def standard_cent_fee_schedule_payload():
    return {
        "account_tier": "STANDARD_CENT",
        "symbol": "XAUUSDc",
        "native_commission_usd_per_lot_per_side": "0.00",
        "commission_formula": "DYNAMIC_NOTIONAL_BPS",
        "source_type": "OFFICIAL_BROKER_DOCUMENT",
    }


@pytest.fixture
def standard_cent_swap_spec_payload():
    return {
        "symbol": "XAUUSDc",
        "broker_symbol": "XAUUSDc",
        "account_tier": "STANDARD_CENT",
        "swap_long_points": "-15.5",
        "swap_short_points": "8.2",
        "rollover_summer_utc_hour": 21,
        "rollover_winter_utc_hour": 22,
        "triple_swap_weekday": "WEDNESDAY",
        "swap_free_available_for_account_type": None,
        "actual_account_swap_free_status": None,
        "source_type": "OFFICIAL_BROKER_DOCUMENT",
    }


@pytest.mark.django_db
def test_standard_cent_legal_entity_qualification(standard_cent_legal_entity_payload):
    """Test legal entity qualification with account-bound evidence for Standard Cent."""
    raw_bytes = json.dumps(standard_cent_legal_entity_payload, sort_keys=True).encode("utf-8")
    parsed = parse_legal_entity_backing_artifact(raw_bytes)

    assert parsed["legal_entity_name"] == "Exness (SC) Ltd"
    assert parsed["legal_entity_code"] == "EXNESS_SC_LTD"
    assert parsed["regulator"] == "FSA"
    assert parsed["license_number"] == "SD025"

    now_utc = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)
    legal_url = "https://my.exness.com/legal-documents/exness_sc_ltd_standard_cent_test.json"
    snap, _ = ingest_friction_source_snapshot(
        source_url=legal_url,
        source_name="EXNESS_LEGAL_ENTITY_SPEC",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD_CENT",
        retrieved_at=now_utc,
        known_at=now_utc,
        raw_content=raw_bytes,
        source_type=FrictionSourceType.ACCOUNT_CLIENT_AGREEMENT.value,
        source_origin=legal_url,
        collection_methodology="GOVERNED_BROKER_URL_CAPTURE",
    )

    raw_sha = hashlib.sha256(raw_bytes).hexdigest()
    proof = compute_verification_proof(
        source_snapshot_id=snap.snapshot_id,
        raw_artifact_sha256=raw_sha,
        component_role="LEGAL_ENTITY",
        verification_method="MANUAL_REVIEWED_OFFICIAL_DOCUMENT",
        source_type=FrictionSourceType.ACCOUNT_CLIENT_AGREEMENT.value,
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD_CENT",
        captured_at=now_utc,
        verifier_identity="TEST_SUITE_ISOLATED_PROVENANCE_SEAM",
    )
    att = create_friction_provenance_attestation(
        source_snapshot=snap,
        component_role="LEGAL_ENTITY",
        verification_method="MANUAL_REVIEWED_OFFICIAL_DOCUMENT",
        verifier_identity="TEST_SUITE_ISOLATED_PROVENANCE_SEAM",
        source_type=FrictionSourceType.ACCOUNT_CLIENT_AGREEMENT.value,
        source_origin=legal_url,
        collection_methodology="CLIENT_PORTAL_DOWNLOAD",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD_CENT",
        raw_artifact_sha256=raw_sha,
        captured_at=now_utc,
        reviewed_at=now_utc,
        attestation_status=FrictionAttestationStatus.VERIFIED.value,
        verification_proof=proof,
    )
    assert att.attestation_status == FrictionAttestationStatus.VERIFIED.value

    assertion = create_friction_qualification_assertion(
        source_snapshot=snap,
        provenance_attestation=att,
        component_role="LEGAL_ENTITY",
        qualification_status=FrictionQualificationStatus.QUALIFIED.value,
        parser_name="parse_legal_entity_backing_artifact",
        normalized_evidence_hash=parsed["normalized_evidence_hash"],
    )
    assert assertion.qualification_status == FrictionQualificationStatus.QUALIFIED.value

    is_valid, reasons, _ = validate_source_qualification_assertion(
        snapshot=snap,
        assertion=assertion,
        expected_component_role="LEGAL_ENTITY",
        expected_parser="parse_legal_entity_backing_artifact",
        expected_symbol="XAUUSD",
        expected_account_tier="STANDARD_CENT",
        expected_venue="EXNESS",
        expected_broker_symbol="XAUUSDc",
    )
    assert is_valid is True
    assert reasons == []


@pytest.mark.django_db
def test_standard_cent_legal_entity_generic_document_fails_account_binding(standard_cent_legal_entity_payload):
    """Hostile test: generic OFFICIAL_BROKER_DOCUMENT alone cannot qualify STANDARD_CENT legal entity."""
    payload = dict(standard_cent_legal_entity_payload)
    payload["source_type"] = FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value
    raw_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
    parsed = parse_legal_entity_backing_artifact(raw_bytes)
    now_utc = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)
    legal_url = "https://my.exness.com/legal-documents/generic_exness_sc_ltd.json"
    snap, _ = ingest_friction_source_snapshot(
        source_url=legal_url,
        source_name="EXNESS_LEGAL_ENTITY_SPEC",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD_CENT",
        retrieved_at=now_utc,
        known_at=now_utc,
        raw_content=raw_bytes,
        source_type=FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value,
        source_origin=legal_url,
        collection_methodology="GOVERNED_BROKER_URL_CAPTURE",
    )
    receipt = make_receipt(legal_url, raw_bytes, now_utc)
    att = create_verified_broker_capture_attestation(
        source_snapshot=snap,
        component_role="LEGAL_ENTITY",
        capture_receipt=receipt,
        expected_symbol="XAUUSD",
        expected_venue="EXNESS",
        expected_account_tier="STANDARD_CENT",
        expected_broker_symbol="XAUUSDc",
    )
    assertion = create_friction_qualification_assertion(
        source_snapshot=snap,
        provenance_attestation=att,
        component_role="LEGAL_ENTITY",
        qualification_status=FrictionQualificationStatus.QUALIFIED.value,
        parser_name="parse_legal_entity_backing_artifact",
        normalized_evidence_hash=parsed["normalized_evidence_hash"],
    )
    is_valid, reasons, _ = validate_source_qualification_assertion(
        snapshot=snap,
        assertion=assertion,
        expected_component_role="LEGAL_ENTITY",
        expected_parser="parse_legal_entity_backing_artifact",
        expected_symbol="XAUUSD",
        expected_account_tier="STANDARD_CENT",
        expected_venue="EXNESS",
        expected_broker_symbol="XAUUSDc",
    )
    assert is_valid is False
    assert any("ACCOUNT_BINDING_REQUIRED" in r for r in reasons)


@pytest.mark.django_db
def test_standard_cent_contract_geometry_qualification(standard_cent_contract_spec_payload):
    """Test contract geometry qualification parsing and validation for Standard Cent (XAUUSDc)."""
    raw_bytes = json.dumps(standard_cent_contract_spec_payload, sort_keys=True).encode("utf-8")
    parsed = parse_contract_spec_backing_artifact(
        raw_bytes,
        expected_symbol="XAUUSD",
        expected_broker_symbol="XAUUSDc",
        expected_account_tier="STANDARD_CENT",
    )

    assert parsed["digits"] == 2
    assert parsed["point_size"] == Decimal("0.01")
    assert parsed["trade_tick_size"] == Decimal("0.01")
    assert parsed["trade_tick_value"] == Decimal("0.01")
    assert parsed["contract_size"] == Decimal("100.0")
    assert parsed["volume_min"] == Decimal("0.01")
    assert parsed["volume_max"] == Decimal("200.0")
    assert parsed["volume_step"] == Decimal("0.01")

    now_utc = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)
    contract_url = "https://my.exness.com/contract-specifications/xauusdc_standard_cent_test.json"
    snap, _ = ingest_friction_source_snapshot(
        source_url=contract_url,
        source_name="EXNESS_CONTRACT_SPEC",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD_CENT",
        retrieved_at=now_utc,
        known_at=now_utc,
        raw_content=raw_bytes,
        source_type=FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value,
        source_origin=contract_url,
        collection_methodology="GOVERNED_BROKER_URL_CAPTURE",
    )

    receipt = make_receipt(contract_url, raw_bytes, now_utc)
    att = create_verified_broker_capture_attestation(
        source_snapshot=snap,
        component_role="CONTRACT_SPEC",
        capture_receipt=receipt,
        expected_symbol="XAUUSD",
        expected_venue="EXNESS",
        expected_account_tier="STANDARD_CENT",
        expected_broker_symbol="XAUUSDc",
    )

    assertion = create_friction_qualification_assertion(
        source_snapshot=snap,
        provenance_attestation=att,
        component_role="CONTRACT_SPEC",
        qualification_status=FrictionQualificationStatus.QUALIFIED.value,
        parser_name="parse_contract_spec_backing_artifact",
        normalized_evidence_hash=parsed["normalized_evidence_hash"],
    )

    is_valid, reasons, _ = validate_source_qualification_assertion(
        snapshot=snap,
        assertion=assertion,
        expected_component_role="CONTRACT_SPEC",
        expected_parser="parse_contract_spec_backing_artifact",
        expected_symbol="XAUUSD",
        expected_account_tier="STANDARD_CENT",
        expected_venue="EXNESS",
        expected_broker_symbol="XAUUSDc",
    )
    assert is_valid is True
    assert reasons == []


@pytest.mark.django_db
def test_standard_cent_contract_geometry_missing_required_fields_fails_closed(standard_cent_contract_spec_payload):
    """Hostile test: contract spec missing any of the 8 required geometry keys fails-closed."""
    required_keys = [
        "digits", "point_size", "trade_tick_size", "trade_tick_value",
        "contract_size", "volume_min", "volume_max", "volume_step",
    ]
    for key in required_keys:
        bad_payload = dict(standard_cent_contract_spec_payload)
        del bad_payload[key]
        raw_bytes = json.dumps(bad_payload, sort_keys=True).encode("utf-8")
        with pytest.raises(ValueError):
            parse_contract_spec_backing_artifact(
                raw_bytes,
                expected_symbol="XAUUSD",
                expected_broker_symbol="XAUUSDc",
                expected_account_tier="STANDARD_CENT",
            )

    # Also missing explicit broker symbol for STANDARD_CENT fails closed
    raw_bytes = json.dumps(standard_cent_contract_spec_payload, sort_keys=True).encode("utf-8")
    with pytest.raises(ValueError, match="broker_symbol"):
        parse_contract_spec_backing_artifact(
            raw_bytes,
            expected_symbol="XAUUSD",
            expected_broker_symbol=None,
            expected_account_tier="STANDARD_CENT",
        )


@pytest.mark.django_db
def test_standard_cent_commission_policy_qualification(standard_cent_fee_schedule_payload):
    """Test commission policy qualification and calculation for Standard Cent."""
    raw_bytes = json.dumps(standard_cent_fee_schedule_payload, sort_keys=True).encode("utf-8")
    parsed = parse_commission_backing_artifact(
        raw_bytes,
        expected_symbol="XAUUSD",
        expected_account_tier="STANDARD_CENT",
        expected_broker_symbol="XAUUSDc",
    )

    assert parsed["native_commission_usd_per_lot_per_side"] == Decimal("0.00")
    assert parsed["commission_formula"] == "DYNAMIC_NOTIONAL_BPS"

    fee_usd = calculate_side_fee_usd(
        volume_lots=Decimal("1.0"),
        commission_usd_per_lot_per_side=parsed["native_commission_usd_per_lot_per_side"],
    )
    assert fee_usd == Decimal("0.00")

    fee_bps = calculate_dynamic_fee_bps(
        commission_usd_per_lot_per_side=parsed["native_commission_usd_per_lot_per_side"],
        contract_size=Decimal("100.0"),
        execution_price=Decimal("2000.00"),
    )
    assert fee_bps == Decimal("0.0000")

    now_utc = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)
    fee_url = "https://my.exness.com/fee-schedules/standard_cent_commission_test.json"
    snap, _ = ingest_friction_source_snapshot(
        source_url=fee_url,
        source_name="EXNESS_FEE_SCHEDULE",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD_CENT",
        retrieved_at=now_utc,
        known_at=now_utc,
        raw_content=raw_bytes,
        source_type=FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value,
        source_origin=fee_url,
        collection_methodology="GOVERNED_BROKER_URL_CAPTURE",
    )

    receipt = make_receipt(fee_url, raw_bytes, now_utc)
    att = create_verified_broker_capture_attestation(
        source_snapshot=snap,
        component_role="COMMISSION",
        capture_receipt=receipt,
        expected_symbol="XAUUSD",
        expected_venue="EXNESS",
        expected_account_tier="STANDARD_CENT",
        expected_broker_symbol="XAUUSDc",
    )

    assertion = create_friction_qualification_assertion(
        source_snapshot=snap,
        provenance_attestation=att,
        component_role="COMMISSION",
        qualification_status=FrictionQualificationStatus.QUALIFIED.value,
        parser_name="parse_commission_backing_artifact",
        normalized_evidence_hash=parsed["normalized_evidence_hash"],
    )

    is_valid, reasons, _ = validate_source_qualification_assertion(
        snapshot=snap,
        assertion=assertion,
        expected_component_role="COMMISSION",
        expected_parser="parse_commission_backing_artifact",
        expected_symbol="XAUUSD",
        expected_account_tier="STANDARD_CENT",
        expected_venue="EXNESS",
        expected_broker_symbol="XAUUSDc",
    )
    assert is_valid is True
    assert reasons == []


@pytest.mark.django_db
def test_standard_cent_financing_policy_qualification(standard_cent_swap_spec_payload):
    """Test financing policy qualification and triple swap calculation for Standard Cent."""
    raw_bytes = json.dumps(standard_cent_swap_spec_payload, sort_keys=True).encode("utf-8")
    parsed = parse_financing_backing_artifact(
        raw_bytes,
        expected_symbol="XAUUSD",
        expected_broker_symbol="XAUUSDc",
        expected_account_tier="STANDARD_CENT",
    )

    assert parsed["swap_long_points"] == Decimal("-15.5")
    assert parsed["swap_short_points"] == Decimal("8.2")
    assert parsed["triple_swap_weekday"] == "WEDNESDAY"
    assert parsed["actual_account_swap_free_status"] is None

    wed_dt = datetime(2026, 9, 2, 22, 0, tzinfo=timezone.utc)
    thu_dt = datetime(2026, 9, 3, 22, 0, tzinfo=timezone.utc)
    assert is_triple_swap_day(wed_dt) is True
    assert is_triple_swap_day(thu_dt) is False

    now_utc = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)
    swap_url = "https://my.exness.com/financing/xauusdc_swap_spec_test.json"
    snap, _ = ingest_friction_source_snapshot(
        source_url=swap_url,
        source_name="EXNESS_SWAP_SPEC",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD_CENT",
        retrieved_at=now_utc,
        known_at=now_utc,
        raw_content=raw_bytes,
        source_type=FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value,
        source_origin=swap_url,
        collection_methodology="GOVERNED_BROKER_URL_CAPTURE",
    )

    receipt = make_receipt(swap_url, raw_bytes, now_utc)
    att = create_verified_broker_capture_attestation(
        source_snapshot=snap,
        component_role="FINANCING",
        capture_receipt=receipt,
        expected_symbol="XAUUSD",
        expected_venue="EXNESS",
        expected_account_tier="STANDARD_CENT",
        expected_broker_symbol="XAUUSDc",
    )

    assertion = create_friction_qualification_assertion(
        source_snapshot=snap,
        provenance_attestation=att,
        component_role="FINANCING",
        qualification_status=FrictionQualificationStatus.QUALIFIED.value,
        parser_name="parse_financing_backing_artifact",
        normalized_evidence_hash=parsed["normalized_evidence_hash"],
    )

    is_valid, reasons, _ = validate_source_qualification_assertion(
        snapshot=snap,
        assertion=assertion,
        expected_component_role="FINANCING",
        expected_parser="parse_financing_backing_artifact",
        expected_symbol="XAUUSD",
        expected_account_tier="STANDARD_CENT",
        expected_venue="EXNESS",
        expected_broker_symbol="XAUUSDc",
    )
    assert is_valid is True
    assert reasons == []


@pytest.mark.django_db
def test_standard_cent_hostile_scope_mismatches(
    standard_cent_legal_entity_payload,
    standard_cent_contract_spec_payload,
):
    """Hostile test: Verifies that scope mismatches (account_tier, missing broker_symbol) fail validation."""
    raw_contract = json.dumps(standard_cent_contract_spec_payload, sort_keys=True).encode("utf-8")
    with pytest.raises(ValueError, match="account tier mismatch"):
        parse_contract_spec_backing_artifact(
            raw_contract,
            expected_symbol="XAUUSD",
            expected_broker_symbol="XAUUSDc",
            expected_account_tier="PRO",
        )

    raw_legal = json.dumps(standard_cent_legal_entity_payload, sort_keys=True).encode("utf-8")
    now_utc = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)
    legal_url = "https://my.exness.com/legal-documents/exness_sc_ltd_standard_cent_hostile.json"
    snap, _ = ingest_friction_source_snapshot(
        source_url=legal_url,
        source_name="EXNESS_LEGAL_ENTITY_SPEC",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD_CENT",
        retrieved_at=now_utc,
        known_at=now_utc,
        raw_content=raw_legal,
        source_type=FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value,
        source_origin=legal_url,
        collection_methodology="GOVERNED_BROKER_URL_CAPTURE",
    )
    receipt = make_receipt(legal_url, raw_legal, now_utc)
    att = create_verified_broker_capture_attestation(
        source_snapshot=snap,
        component_role="LEGAL_ENTITY",
        capture_receipt=receipt,
        expected_symbol="XAUUSD",
        expected_venue="EXNESS",
        expected_account_tier="STANDARD_CENT",
        expected_broker_symbol="XAUUSDc",
    )
    parsed = parse_legal_entity_backing_artifact(raw_legal)
    assertion = create_friction_qualification_assertion(
        source_snapshot=snap,
        provenance_attestation=att,
        component_role="LEGAL_ENTITY",
        qualification_status=FrictionQualificationStatus.QUALIFIED.value,
        parser_name="parse_legal_entity_backing_artifact",
        normalized_evidence_hash=parsed["normalized_evidence_hash"],
    )
    is_valid, reasons, _ = validate_source_qualification_assertion(
        snapshot=snap,
        assertion=assertion,
        expected_component_role="LEGAL_ENTITY",
        expected_parser="parse_legal_entity_backing_artifact",
        expected_symbol="XAUUSD",
        expected_account_tier="STANDARD_CENT",
        expected_venue="EXNESS",
        expected_broker_symbol=None,
    )
    assert is_valid is False
    assert any("BROKER_SYMBOL_SCOPE_MISSING" in r for r in reasons)


@pytest.mark.django_db
def test_standard_cent_hostile_db_fallback_unverified_assertion_rejected(standard_cent_fee_schedule_payload):
    """Hostile test: DB fallback when URL is absent rejects unverified/rejected assertions."""
    raw_bytes = json.dumps(standard_cent_fee_schedule_payload, sort_keys=True).encode("utf-8")
    now_utc = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)
    snap, _ = ingest_friction_source_snapshot(
        source_url="https://my.exness.com/fee-schedules/unverified.json",
        source_name="EXNESS_FEE_SCHEDULE",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD_CENT",
        retrieved_at=now_utc,
        known_at=now_utc,
        raw_content=raw_bytes,
        source_type=FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value,
        source_origin="https://my.exness.com/fee-schedules/unverified.json",
        collection_methodology="GOVERNED_BROKER_URL_CAPTURE",
    )
    create_friction_qualification_assertion(
        source_snapshot=snap,
        provenance_attestation=None,
        component_role="COMMISSION",
        qualification_status=FrictionQualificationStatus.UNVERIFIED.value,
        parser_name="parse_commission_backing_artifact",
        normalized_evidence_hash="0" * 64,
    )
    # Filter strictly for QUALIFIED assertions as required by Directive §6
    candidate = FrictionSourceSnapshot.objects.filter(
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD_CENT",
        qualification_assertions__component_role="COMMISSION",
        qualification_assertions__qualification_status=FrictionQualificationStatus.QUALIFIED.value,
    ).first()
    assert candidate is None


def test_standard_cent_strict_full_completeness_gate():
    """Directive 10: Full completeness strictly requires ALL SIX categories == QUALIFIED."""
    statuses = {
        "legal": FrictionQualificationStatus.QUALIFIED.value,
        "contract": FrictionQualificationStatus.QUALIFIED.value,
        "commission": FrictionQualificationStatus.QUALIFIED.value,
        "financing": FrictionQualificationStatus.QUALIFIED.value,
        "spread": FrictionQualificationStatus.QUALIFIED.value,
        "slippage": "SLIPPAGE_EMPIRICAL_EVIDENCE_MISSING",
    }
    is_complete = (
        statuses["legal"] == FrictionQualificationStatus.QUALIFIED.value
        and statuses["contract"] == FrictionQualificationStatus.QUALIFIED.value
        and statuses["commission"] == FrictionQualificationStatus.QUALIFIED.value
        and statuses["financing"] == FrictionQualificationStatus.QUALIFIED.value
        and statuses["spread"] == FrictionQualificationStatus.QUALIFIED.value
        and statuses["slippage"] == FrictionQualificationStatus.QUALIFIED.value
    )
    assert is_complete is False

    statuses["slippage"] = FrictionQualificationStatus.QUALIFIED.value
    is_complete = (
        statuses["legal"] == FrictionQualificationStatus.QUALIFIED.value
        and statuses["contract"] == FrictionQualificationStatus.QUALIFIED.value
        and statuses["commission"] == FrictionQualificationStatus.QUALIFIED.value
        and statuses["financing"] == FrictionQualificationStatus.QUALIFIED.value
        and statuses["spread"] == FrictionQualificationStatus.QUALIFIED.value
        and statuses["slippage"] == FrictionQualificationStatus.QUALIFIED.value
    )
    assert is_complete is True


@pytest.mark.django_db
def test_standard_cent_cli_governed_url_capture_seam(
    monkeypatch,
    standard_cent_legal_entity_payload,
    standard_cent_contract_spec_payload,
    standard_cent_fee_schedule_payload,
    standard_cent_swap_spec_payload,
):
    """Test CLI URL capture via governed HTTP capture seam.
    
    Verifies that execute_governed_broker_url_capture is invoked for each provided URL,
    and generic legal entity fails account binding.
    """
    scratch_dir = Path("scratch/tmp_test_cli_seam")
    scratch_dir.mkdir(parents=True, exist_ok=True)
    try:
        captured_urls = []
        now_utc = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)

        generic_legal_payload = dict(standard_cent_legal_entity_payload)
        generic_legal_payload["source_type"] = "OFFICIAL_BROKER_DOCUMENT"

        payload_map = {
            "https://my.exness.com/legal-documents/exness_sc_ltd_standard_cent.json": json.dumps(generic_legal_payload, sort_keys=True).encode("utf-8"),
            "https://my.exness.com/contract-specifications/xauusdc_standard_cent.json": json.dumps(standard_cent_contract_spec_payload, sort_keys=True).encode("utf-8"),
            "https://my.exness.com/fee-schedules/standard_cent_commission.json": json.dumps(standard_cent_fee_schedule_payload, sort_keys=True).encode("utf-8"),
            "https://my.exness.com/financing/xauusdc_swap_spec.json": json.dumps(standard_cent_swap_spec_payload, sort_keys=True).encode("utf-8"),
        }

        def mock_capture(url, **kwargs):
            captured_urls.append(url)
            body = payload_map.get(url, b"{}")
            return make_receipt(url, body, now_utc)

        monkeypatch.setattr(
            "apps.market_data.management.commands.ingest_xauusd_empirical_friction.execute_governed_broker_url_capture",
            mock_capture,
        )

        manifest_file = scratch_dir / "manifest.json"
        report_file = scratch_dir / "report.md"

        call_command(
            "ingest_xauusd_empirical_friction",
            venue="EXNESS",
            account_tier="STANDARD_CENT",
            broker_symbol="XAUUSDc",
            account_currency="USC",
            legal_entity_url="https://my.exness.com/legal-documents/exness_sc_ltd_standard_cent.json",
            contract_spec_url="https://my.exness.com/contract-specifications/xauusdc_standard_cent.json",
            fee_schedule_url="https://my.exness.com/fee-schedules/standard_cent_commission.json",
            swap_spec_url="https://my.exness.com/financing/xauusdc_swap_spec.json",
            output_manifest=str(manifest_file),
            output_report=str(report_file),
        )

        assert manifest_file.exists()
        with open(manifest_file, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        assert len(captured_urls) == 4

        inv = manifest["evidence_inventory"]
        assert inv["legal_entity_scope"]["status"] == "LEGAL_ENTITY_EVIDENCE_MISSING"
        assert inv["contract_geometry"]["digits"] == 2
        assert inv["commission_policy"]["commission_formula"] == "DYNAMIC_NOTIONAL_BPS"
        assert inv["financing_policy"]["swap_long_points"] == "-15.5"

        assert manifest["hard_readiness_gate"]["passed"] is False
        assert manifest["hard_readiness_gate"]["decision"] == "CANDLES_READY_EMPIRICAL_FRICTION_MISSING"
    finally:
        if scratch_dir.exists():
            shutil.rmtree(scratch_dir, ignore_errors=True)


@pytest.mark.django_db
def test_standard_cent_cli_truthful_execution_with_sealed_spread(monkeypatch):
    """Test CLI execution reflecting truthful unseeded state with sealed PR #26 spread snapshot."""
    scratch_dir = Path("scratch/tmp_test_cli_truth")
    scratch_dir.mkdir(parents=True, exist_ok=True)
    try:
        now_utc = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)
        fake_payload = (
            b'"Exness","Symbol","Timestamp","Bid","Ask"\n'
            b'"Exness","XAUUSDc","2026-08-15 10:00:00.000000Z","2500.00","2500.25"\n'
            b'"Exness","XAUUSDc","2026-08-15 10:00:01.000000Z","2500.05","2500.30"\n'
        )
        sealed_sha = "b2dbfaf9297075944c1163c3c1ff53db3abfa5f78d5edcf9c6d1b47b1784c749"

        orig_sha = hashlib.sha256
        def mock_sha(data=b""):
            if data == fake_payload:
                class H:
                    def hexdigest(self):
                        return sealed_sha
                return H()
            return orig_sha(data)
        monkeypatch.setattr(hashlib, "sha256", mock_sha)

        snap, _ = ingest_friction_source_snapshot(
            source_url="https://ticks.ex2archive.com/ticks/XAUUSDc/2026/08/Exness_XAUUSDc_2026_08.zip",
            source_name="EXNESS_OFFICIAL_TICK_HISTORY",
            venue="EXNESS",
            symbol="XAUUSD",
            account_tier="STANDARD_CENT",
            retrieved_at=now_utc,
            known_at=now_utc,
            raw_content=fake_payload,
            source_type=FrictionSourceType.EXNESS_OFFICIAL_TICK_HISTORY.value,
            source_origin="https://ticks.ex2archive.com/ticks/XAUUSDc/2026/08/Exness_XAUUSDc_2026_08.zip",
            collection_methodology="EXNESS_OFFICIAL_TICK_ARCHIVE",
        )

        ds_id = hashlib.sha256(f"{snap.snapshot_id}:EXNESS:XAUUSD:STANDARD_CENT:6493208".encode()).hexdigest()
        ds = FrictionEvidenceDataset.objects.create(
            dataset_id=ds_id,
            source_snapshot=snap,
            venue="EXNESS",
            symbol="XAUUSD",
            account_tier="STANDARD_CENT",
            sample_count=6493208,
            distinct_trading_days=26,
            session_counts={"ASIAN": 2302634, "LONDON": 1209508, "NEW_YORK": 2747465, "ROLLOVER": 233601},
            source_units="POINTS",
            raw_dataset_sha256="3f8c4972d0b10b8b6c2572e4e9e925aadc07ee95a58133ea2fcce275bfa51d74",
            collection_methodology="EXNESS_OFFICIAL_TICK_ARCHIVE",
            sample_start=now_utc,
            sample_end=now_utc,
        )
        summary_id = hashlib.sha256(f"{ds.dataset_id}:SPREAD:NORMAL:ALL".encode()).hexdigest()
        FrictionDistributionSummary.objects.create(
            summary_id=summary_id,
            evidence_dataset=ds,
            component_type=FrictionComponentType.SPREAD,
            condition="NORMAL",
            session="ALL",
            unit="BPS",
            sample_count=6493208,
            stat_mean=Decimal("0.59"),
            stat_std=Decimal("0.05"),
            stat_min=Decimal("0.544"),
            stat_p50=Decimal("0.5861"),
            stat_p75=Decimal("0.5934"),
            stat_p90=Decimal("0.5997"),
            stat_p95=Decimal("0.6078"),
            stat_p99=Decimal("0.7374"),
            stat_max=Decimal("1.6211"),
            population_semantics="ALL_OBSERVED",
        )
        meta = {
            "requested_url": "https://ticks.ex2archive.com/ticks/XAUUSDc/2026/08/Exness_XAUUSDc_2026_08.zip",
            "final_url": "https://ticks.ex2archive.com/ticks/XAUUSDc/2026/08/Exness_XAUUSDc_2026_08.zip",
            "redirect_chain": [],
            "hostname": "ticks.ex2archive.com",
            "captured_at": now_utc.isoformat(),
            "http_status": 200,
            "content_type": "application/zip",
            "raw_response_sha256": sealed_sha,
            "collector_version": "1.0.0",
            "derived_symbol": "XAUUSD",
            "derived_venue": "EXNESS",
            "derived_account_tier": "STANDARD_CENT",
        }
        ctx_hash = compute_capture_context_hash(meta, "BROKER_OFFICIAL_URL_CAPTURE")
        proof = compute_verification_proof(
            source_snapshot_id=snap.snapshot_id,
            raw_artifact_sha256=sealed_sha,
            component_role="SPREAD_DATASET",
            verification_method="BROKER_OFFICIAL_URL_CAPTURE",
            source_type=FrictionSourceType.EXNESS_OFFICIAL_TICK_HISTORY.value,
            venue="EXNESS",
            symbol="XAUUSD",
            account_tier="STANDARD_CENT",
            captured_at=now_utc,
            verifier_identity="AURUMIQ_OFFICIAL_BROKER_URL_CAPTURE_WORKFLOW",
            capture_context_hash=ctx_hash,
        )
        att = create_friction_provenance_attestation(
            source_snapshot=snap,
            component_role="SPREAD_DATASET",
            verification_method="BROKER_OFFICIAL_URL_CAPTURE",
            verifier_identity="AURUMIQ_OFFICIAL_BROKER_URL_CAPTURE_WORKFLOW",
            source_type=FrictionSourceType.EXNESS_OFFICIAL_TICK_HISTORY.value,
            source_origin=snap.source_origin,
            collection_methodology=snap.collection_methodology,
            venue="EXNESS",
            symbol="XAUUSD",
            account_tier="STANDARD_CENT",
            raw_artifact_sha256=sealed_sha,
            captured_at=now_utc,
            reviewed_at=now_utc,
            attestation_status=FrictionAttestationStatus.VERIFIED.value,
            provenance_metadata=meta,
            verification_proof=proof,
        )
        assertion = create_friction_qualification_assertion(
            source_snapshot=snap,
            provenance_attestation=att,
            component_role="SPREAD_DATASET",
            qualification_status=FrictionQualificationStatus.QUALIFIED.value,
            parser_name="parse_exness_official_tick_history",
            normalized_evidence_hash="3f8c4972d0b10b8b6c2572e4e9e925aadc07ee95a58133ea2fcce275bfa51d74",
        )
        is_val, val_reasons, _ = validate_source_qualification_assertion(
            snapshot=snap,
            assertion=assertion,
            expected_component_role="SPREAD_DATASET",
            expected_parser="parse_exness_official_tick_history",
            expected_symbol="XAUUSD",
            expected_account_tier="STANDARD_CENT",
            expected_venue="EXNESS",
            expected_broker_symbol="XAUUSDc",
        )
        assert is_val is True, f"Assertion invalid: {val_reasons}"

        manifest_file = scratch_dir / "manifest.json"
        report_file = scratch_dir / "report.md"

        call_command(
            "ingest_xauusd_empirical_friction",
            venue="EXNESS",
            account_tier="STANDARD_CENT",
            broker_symbol="XAUUSDc",
            output_manifest=str(manifest_file),
            output_report=str(report_file),
        )

        assert manifest_file.exists()
        with open(manifest_file, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        inv = manifest["evidence_inventory"]
        assert inv["legal_entity_scope"]["status"] == "LEGAL_ENTITY_EVIDENCE_MISSING"
        assert inv["contract_geometry"]["status"] == "CONTRACT_SPEC_EVIDENCE_MISSING"
        assert inv["commission_policy"]["status"] == "COMMISSION_EVIDENCE_MISSING"
        assert inv["financing_policy"]["status"] == "FINANCING_EVIDENCE_MISSING"
        assert inv["bid_ask_spread_distribution"]["status"] == "QUALIFIED"
        assert inv["bid_ask_spread_distribution"]["sample_count"] == 6493208
        assert inv["bid_ask_spread_distribution"]["raw_response_sha256"] == sealed_sha
        assert inv["execution_slippage_telemetry"]["status"] == "SLIPPAGE_EMPIRICAL_EVIDENCE_MISSING"

        assert manifest["hard_readiness_gate"]["decision"] == "CANDLES_READY_EMPIRICAL_FRICTION_MISSING"
        assert manifest["hard_readiness_gate"]["passed"] is False
        assert manifest["hard_readiness_gate"]["published_decision"] == "WAIT"
        assert manifest["hard_readiness_gate"]["phase3b_production_weight"] == 0.0
    finally:
        if scratch_dir.exists():
            shutil.rmtree(scratch_dir, ignore_errors=True)


def test_governed_broker_url_capture_transparent_user_agent(monkeypatch):
    """Verify execute_governed_broker_url_capture uses transparent collector User-Agent."""
    captured_headers = {}

    class DummyResponse:
        url = "https://my.exness.com/test"
        status = 200
        headers = {"Content-Type": "application/json"}

        def read(self, n):
            return b'{"status": "ok"}'

    class DummyOpener:
        def open(self, req, timeout=30):
            captured_headers.update(dict(req.headers))
            return DummyResponse()

    monkeypatch.setattr(
        "apps.market_data.friction.provenance.urllib.request.build_opener",
        lambda handler: DummyOpener(),
    )
    from apps.market_data.friction.provenance import execute_governed_broker_url_capture

    receipt = execute_governed_broker_url_capture("https://my.exness.com/test")
    ua = captured_headers.get("User-agent") or captured_headers.get("User-Agent")
    assert ua == "AurumIQ-GovernedBrokerCapture/1.0"
    assert "Mozilla" not in ua
    assert "Chrome" not in ua


@pytest.mark.django_db
def test_spread_recomputation_from_raw_bytes_without_summary(monkeypatch):
    """Verify spread statistics are recomputed directly from raw bytes when no summary exists."""
    scratch_dir = Path("scratch/tmp_test_spread_recompute")
    scratch_dir.mkdir(parents=True, exist_ok=True)
    try:
        now_utc = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)
        fake_payload = (
            b'"Exness","Symbol","Timestamp","Bid","Ask"\n'
            b'"Exness","XAUUSDc","2026-08-15 10:00:00.000000Z","2500.00","2500.25"\n'
            b'"Exness","XAUUSDc","2026-08-15 10:00:01.000000Z","2500.05","2500.30"\n'
        )
        sealed_sha = "b2dbfaf9297075944c1163c3c1ff53db3abfa5f78d5edcf9c6d1b47b1784c749"
        orig_sha = hashlib.sha256

        def mock_sha(data=b""):
            if data == fake_payload:
                class H:
                    def hexdigest(self):
                        return sealed_sha
                return H()
            return orig_sha(data)

        monkeypatch.setattr(hashlib, "sha256", mock_sha)

        snap, _ = ingest_friction_source_snapshot(
            source_url="https://ticks.ex2archive.com/ticks/XAUUSDc/2026/08/Exness_XAUUSDc_2026_08.zip",
            source_name="EXNESS_OFFICIAL_TICK_HISTORY",
            venue="EXNESS",
            symbol="XAUUSD",
            account_tier="STANDARD_CENT",
            retrieved_at=now_utc,
            known_at=now_utc,
            raw_content=fake_payload,
            source_type=FrictionSourceType.EXNESS_OFFICIAL_TICK_HISTORY.value,
            source_origin="https://ticks.ex2archive.com/ticks/XAUUSDc/2026/08/Exness_XAUUSDc_2026_08.zip",
            collection_methodology="EXNESS_OFFICIAL_TICK_ARCHIVE",
        )
        ds_id = hashlib.sha256(f"{snap.snapshot_id}:EXNESS:XAUUSD:STANDARD_CENT:6493208".encode()).hexdigest()
        FrictionEvidenceDataset.objects.create(
            dataset_id=ds_id,
            source_snapshot=snap,
            venue="EXNESS",
            symbol="XAUUSD",
            account_tier="STANDARD_CENT",
            sample_count=6493208,
            distinct_trading_days=26,
            session_counts={"ASIAN": 2302634, "LONDON": 1209508, "NEW_YORK": 2747465, "ROLLOVER": 233601},
            source_units="POINTS",
            raw_dataset_sha256="3f8c4972d0b10b8b6c2572e4e9e925aadc07ee95a58133ea2fcce275bfa51d74",
            collection_methodology="EXNESS_OFFICIAL_TICK_ARCHIVE",
            sample_start=now_utc,
            sample_end=now_utc,
        )
        meta = {
            "requested_url": "https://ticks.ex2archive.com/ticks/XAUUSDc/2026/08/Exness_XAUUSDc_2026_08.zip",
            "final_url": "https://ticks.ex2archive.com/ticks/XAUUSDc/2026/08/Exness_XAUUSDc_2026_08.zip",
            "redirect_chain": [],
            "hostname": "ticks.ex2archive.com",
            "captured_at": now_utc.isoformat(),
            "http_status": 200,
            "content_type": "application/zip",
            "raw_response_sha256": sealed_sha,
            "collector_version": "1.0.0",
            "derived_symbol": "XAUUSD",
            "derived_venue": "EXNESS",
            "derived_account_tier": "STANDARD_CENT",
        }
        ctx_hash = compute_capture_context_hash(meta, "BROKER_OFFICIAL_URL_CAPTURE")
        proof = compute_verification_proof(
            source_snapshot_id=snap.snapshot_id,
            raw_artifact_sha256=sealed_sha,
            component_role="SPREAD_DATASET",
            verification_method="BROKER_OFFICIAL_URL_CAPTURE",
            source_type=FrictionSourceType.EXNESS_OFFICIAL_TICK_HISTORY.value,
            venue="EXNESS",
            symbol="XAUUSD",
            account_tier="STANDARD_CENT",
            captured_at=now_utc,
            verifier_identity="AURUMIQ_OFFICIAL_BROKER_URL_CAPTURE_WORKFLOW",
            capture_context_hash=ctx_hash,
        )
        att = create_friction_provenance_attestation(
            source_snapshot=snap,
            component_role="SPREAD_DATASET",
            verification_method="BROKER_OFFICIAL_URL_CAPTURE",
            verifier_identity="AURUMIQ_OFFICIAL_BROKER_URL_CAPTURE_WORKFLOW",
            source_type=FrictionSourceType.EXNESS_OFFICIAL_TICK_HISTORY.value,
            source_origin=snap.source_origin,
            collection_methodology=snap.collection_methodology,
            venue="EXNESS",
            symbol="XAUUSD",
            account_tier="STANDARD_CENT",
            raw_artifact_sha256=sealed_sha,
            captured_at=now_utc,
            reviewed_at=now_utc,
            attestation_status=FrictionAttestationStatus.VERIFIED.value,
            provenance_metadata=meta,
            verification_proof=proof,
        )
        assertion = create_friction_qualification_assertion(
            source_snapshot=snap,
            provenance_attestation=att,
            component_role="SPREAD_DATASET",
            qualification_status=FrictionQualificationStatus.QUALIFIED.value,
            parser_name="parse_exness_official_tick_history",
            normalized_evidence_hash="3f8c4972d0b10b8b6c2572e4e9e925aadc07ee95a58133ea2fcce275bfa51d74",
        )
        manifest_file = scratch_dir / "manifest.json"
        report_file = scratch_dir / "report.md"

        call_command(
            "ingest_xauusd_empirical_friction",
            venue="EXNESS",
            account_tier="STANDARD_CENT",
            broker_symbol="XAUUSDc",
            output_manifest=str(manifest_file),
            output_report=str(report_file),
        )

        assert manifest_file.exists()
        with open(manifest_file, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        inv = manifest["evidence_inventory"]
        assert inv["bid_ask_spread_distribution"]["status"] == "QUALIFIED"
        assert inv["bid_ask_spread_distribution"]["raw_response_sha256"] == sealed_sha
        assert FrictionDistributionSummary.objects.filter(component_type=FrictionComponentType.SPREAD).count() == 0
    finally:
        if scratch_dir.exists():
            shutil.rmtree(scratch_dir, ignore_errors=True)
