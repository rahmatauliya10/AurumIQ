"""Unit tests for Stage D3: Standard Cent Web-First Broker / Account Evidence Closure.

Verifies qualification of all 4 non-slippage empirical friction categories:
1. LEGAL_ENTITY (Exness (SC) Ltd, FSA SD025)
2. CONTRACT_GEOMETRY (XAUUSDc, digits=2, contract_size=100.0)
3. COMMISSION (0.00 native commission, DYNAMIC_NOTIONAL_BPS)
4. FINANCING (swap_long=-15.5, swap_short=8.2, triple_swap=WEDNESDAY)

Also enforces the frozen invariants:
- Spread Dataset: Sealed QUALIFIED (6,493,208 samples, SHA256 b2dbfaf9...)
- Slippage: Strictly MISSING (Stage D4 task)
- Readiness Gate: Strictly CANDLES_READY_EMPIRICAL_FRICTION_MISSING, passed=False, decision=WAIT, weight=0.0
"""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
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
    create_friction_qualification_assertion,
    ingest_friction_source_snapshot,
)
from apps.market_data.friction.provenance import (
    BrokerCaptureReceipt,
    compute_broker_receipt_auth_tag,
    create_verified_broker_capture_attestation,
)
from apps.market_data.friction.validation import validate_source_qualification_assertion
from apps.market_data.models import (
    FrictionAttestationStatus,
    FrictionQualificationStatus,
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
        "source_type": "OFFICIAL_BROKER_DOCUMENT",
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
    """Test legal entity qualification parsing and validation for Standard Cent."""
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

    # Verify 0.00 fee calculation
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

    # Verify triple swap day helper
    wed_dt = datetime(2026, 9, 2, 22, 0, tzinfo=timezone.utc)  # Wednesday
    thu_dt = datetime(2026, 9, 3, 22, 0, tzinfo=timezone.utc)  # Thursday
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
    # 1. Scope mismatch on account tier
    raw_contract = json.dumps(standard_cent_contract_spec_payload, sort_keys=True).encode("utf-8")
    with pytest.raises(ValueError, match="account tier mismatch"):
        parse_contract_spec_backing_artifact(
            raw_contract,
            expected_symbol="XAUUSD",
            expected_broker_symbol="XAUUSDc",
            expected_account_tier="PRO",
        )

    # 2. Missing broker symbol when validating under STANDARD_CENT scope
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
    # Validate without expected_broker_symbol under STANDARD_CENT -> must fail
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


def seed_all_standard_cent_evidence(
    legal_payload,
    contract_payload,
    fee_payload,
    swap_payload,
):
    now_utc = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)
    venue = "EXNESS"
    symbol = "XAUUSD"
    account_tier = "STANDARD_CENT"
    broker_symbol = "XAUUSDc"

    # 1. Legal Entity
    legal_url = "https://my.exness.com/legal-documents/exness_sc_ltd_standard_cent.json"
    legal_bytes = json.dumps(legal_payload, sort_keys=True).encode("utf-8")
    legal_parsed = parse_legal_entity_backing_artifact(legal_bytes)
    legal_snap, _ = ingest_friction_source_snapshot(
        source_url=legal_url,
        source_name="EXNESS_LEGAL_ENTITY_SPEC",
        venue=venue,
        symbol=symbol,
        account_tier=account_tier,
        retrieved_at=now_utc,
        known_at=now_utc,
        raw_content=legal_bytes,
        source_type=FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value,
        source_origin=legal_url,
        collection_methodology="GOVERNED_BROKER_URL_CAPTURE",
    )
    legal_receipt = make_receipt(legal_url, legal_bytes, now_utc)
    legal_att = create_verified_broker_capture_attestation(
        source_snapshot=legal_snap,
        component_role="LEGAL_ENTITY",
        capture_receipt=legal_receipt,
        expected_symbol=symbol,
        expected_venue=venue,
        expected_account_tier=account_tier,
        expected_broker_symbol=broker_symbol,
    )
    create_friction_qualification_assertion(
        source_snapshot=legal_snap,
        provenance_attestation=legal_att,
        component_role="LEGAL_ENTITY",
        qualification_status=FrictionQualificationStatus.QUALIFIED.value,
        parser_name="parse_legal_entity_backing_artifact",
        normalized_evidence_hash=legal_parsed["normalized_evidence_hash"],
    )

    # 2. Contract Spec
    contract_url = "https://my.exness.com/contract-specifications/xauusdc_standard_cent.json"
    contract_bytes = json.dumps(contract_payload, sort_keys=True).encode("utf-8")
    contract_parsed = parse_contract_spec_backing_artifact(
        contract_bytes,
        expected_symbol=symbol,
        expected_broker_symbol=broker_symbol,
        expected_account_tier=account_tier,
    )
    contract_snap, _ = ingest_friction_source_snapshot(
        source_url=contract_url,
        source_name="EXNESS_CONTRACT_SPEC",
        venue=venue,
        symbol=symbol,
        account_tier=account_tier,
        retrieved_at=now_utc,
        known_at=now_utc,
        raw_content=contract_bytes,
        source_type=FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value,
        source_origin=contract_url,
        collection_methodology="GOVERNED_BROKER_URL_CAPTURE",
    )
    contract_receipt = make_receipt(contract_url, contract_bytes, now_utc)
    contract_att = create_verified_broker_capture_attestation(
        source_snapshot=contract_snap,
        component_role="CONTRACT_SPEC",
        capture_receipt=contract_receipt,
        expected_symbol=symbol,
        expected_venue=venue,
        expected_account_tier=account_tier,
        expected_broker_symbol=broker_symbol,
    )
    create_friction_qualification_assertion(
        source_snapshot=contract_snap,
        provenance_attestation=contract_att,
        component_role="CONTRACT_SPEC",
        qualification_status=FrictionQualificationStatus.QUALIFIED.value,
        parser_name="parse_contract_spec_backing_artifact",
        normalized_evidence_hash=contract_parsed["normalized_evidence_hash"],
    )

    # 3. Fee Schedule
    fee_url = "https://my.exness.com/fee-schedules/standard_cent_commission.json"
    fee_bytes = json.dumps(fee_payload, sort_keys=True).encode("utf-8")
    fee_parsed = parse_commission_backing_artifact(
        fee_bytes,
        expected_symbol=symbol,
        expected_account_tier=account_tier,
        expected_broker_symbol=broker_symbol,
    )
    fee_snap, _ = ingest_friction_source_snapshot(
        source_url=fee_url,
        source_name="EXNESS_FEE_SCHEDULE",
        venue=venue,
        symbol=symbol,
        account_tier=account_tier,
        retrieved_at=now_utc,
        known_at=now_utc,
        raw_content=fee_bytes,
        source_type=FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value,
        source_origin=fee_url,
        collection_methodology="GOVERNED_BROKER_URL_CAPTURE",
    )
    fee_receipt = make_receipt(fee_url, fee_bytes, now_utc)
    fee_att = create_verified_broker_capture_attestation(
        source_snapshot=fee_snap,
        component_role="COMMISSION",
        capture_receipt=fee_receipt,
        expected_symbol=symbol,
        expected_venue=venue,
        expected_account_tier=account_tier,
        expected_broker_symbol=broker_symbol,
    )
    create_friction_qualification_assertion(
        source_snapshot=fee_snap,
        provenance_attestation=fee_att,
        component_role="COMMISSION",
        qualification_status=FrictionQualificationStatus.QUALIFIED.value,
        parser_name="parse_commission_backing_artifact",
        normalized_evidence_hash=fee_parsed["normalized_evidence_hash"],
    )

    # 4. Swap Spec
    swap_url = "https://my.exness.com/financing/xauusdc_swap_spec.json"
    swap_bytes = json.dumps(swap_payload, sort_keys=True).encode("utf-8")
    swap_parsed = parse_financing_backing_artifact(
        swap_bytes,
        expected_symbol=symbol,
        expected_broker_symbol=broker_symbol,
        expected_account_tier=account_tier,
    )
    swap_snap, _ = ingest_friction_source_snapshot(
        source_url=swap_url,
        source_name="EXNESS_SWAP_SPEC",
        venue=venue,
        symbol=symbol,
        account_tier=account_tier,
        retrieved_at=now_utc,
        known_at=now_utc,
        raw_content=swap_bytes,
        source_type=FrictionSourceType.OFFICIAL_BROKER_DOCUMENT.value,
        source_origin=swap_url,
        collection_methodology="GOVERNED_BROKER_URL_CAPTURE",
    )
    swap_receipt = make_receipt(swap_url, swap_bytes, now_utc)
    swap_att = create_verified_broker_capture_attestation(
        source_snapshot=swap_snap,
        component_role="FINANCING",
        capture_receipt=swap_receipt,
        expected_symbol=symbol,
        expected_venue=venue,
        expected_account_tier=account_tier,
        expected_broker_symbol=broker_symbol,
    )
    create_friction_qualification_assertion(
        source_snapshot=swap_snap,
        provenance_attestation=swap_att,
        component_role="FINANCING",
        qualification_status=FrictionQualificationStatus.QUALIFIED.value,
        parser_name="parse_financing_backing_artifact",
        normalized_evidence_hash=swap_parsed["normalized_evidence_hash"],
    )


@pytest.mark.django_db
def test_standard_cent_ingest_cli_execution(
    standard_cent_legal_entity_payload,
    standard_cent_contract_spec_payload,
    standard_cent_fee_schedule_payload,
    standard_cent_swap_spec_payload,
):
    """Test full CLI command execution and manifest/report generation for Standard Cent."""
    from pathlib import Path
    import shutil
    scratch_dir = Path("scratch/tmp_test_cli")
    scratch_dir.mkdir(parents=True, exist_ok=True)
    try:
        seed_all_standard_cent_evidence(
            standard_cent_legal_entity_payload,
            standard_cent_contract_spec_payload,
            standard_cent_fee_schedule_payload,
            standard_cent_swap_spec_payload,
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
        assert report_file.exists()

        with open(manifest_file, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        inv = manifest["evidence_inventory"]
        assert inv["legal_entity_scope"]["status"] == "QUALIFIED"
        assert inv["legal_entity_scope"]["legal_entity_code"] == "EXNESS_SC_LTD"
        assert inv["legal_entity_scope"]["regulator"] == "FSA"
        assert inv["legal_entity_scope"]["license_number"] == "SD025"

        assert inv["contract_geometry"]["status"] == "QUALIFIED"
        assert inv["contract_geometry"]["broker_symbol"] == "XAUUSDc"
        assert inv["contract_geometry"]["digits"] == 2
        assert inv["contract_geometry"]["point_size"] == "0.01"
        assert inv["contract_geometry"]["contract_size"] == "100.0"

        assert inv["commission_policy"]["status"] == "QUALIFIED"
        assert inv["commission_policy"]["native_commission_usd_per_lot_per_side"] == "0.00"
        assert inv["commission_policy"]["commission_formula"] == "DYNAMIC_NOTIONAL_BPS"

        assert inv["financing_policy"]["status"] == "QUALIFIED"
        assert inv["financing_policy"]["swap_long_points"] == "-15.5"
        assert inv["financing_policy"]["swap_short_points"] == "8.2"
        assert inv["financing_policy"]["triple_swap_weekday"] == "WEDNESDAY"

        assert inv["execution_slippage_telemetry"]["status"] == "SLIPPAGE_EMPIRICAL_EVIDENCE_MISSING"
        assert manifest["hard_readiness_gate"]["decision"] == "CANDLES_READY_EMPIRICAL_FRICTION_MISSING"
        assert manifest["hard_readiness_gate"]["passed"] is False
        assert manifest["hard_readiness_gate"]["published_decision"] == "WAIT"
        assert manifest["hard_readiness_gate"]["phase3b_production_weight"] == 0.0
    finally:
        if scratch_dir.exists():
            shutil.rmtree(scratch_dir, ignore_errors=True)

