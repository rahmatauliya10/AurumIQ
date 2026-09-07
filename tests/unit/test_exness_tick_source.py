"""Tests for Exness Official Tick History source qualification and parser.

Validates Stage D1 requirements:
- EXNESS_OFFICIAL_TICK_HISTORY registered in FrictionSourceType and QUALIFIED_SPREAD_SOURCE_TYPES
- SPREAD evidence qualification only; never qualifies slippage, legal entity, contract, commission, financing
- MT5_TICK_HISTORY_EXPORT remains accepted
- Exact schema parser: "Exness","Symbol","Timestamp","Bid","Ask"
- Explicit XAUUSDc binding under STANDARD_CENT tier; rejection of mismatched symbols (XAUUSD, XAUUSDm)
- Fail-closed if expected_broker_symbol is missing under STANDARD_CENT
- Rejection of invalid venues, crossed/non-positive quotes, naive/future/non-chronological timestamps
- 1-day dataset fails the >= 5 distinct trading dates gate
- 5-date dataset satisfies spread dataset sufficiency
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import hashlib
import json
import os
import pytest
from django.core.management import call_command

from apps.instruments.models import (
    Asset,
    AssetType,
    Instrument,
    InstrumentType,
    InstrumentRole,
    MarketListing,
    ListingRole,
    ListingStatus,
)
from apps.market_data.models import (
    FrictionSourceType,
    QUALIFIED_SPREAD_SOURCE_TYPES,
    QUALIFIED_LEGAL_ENTITY_SOURCE_TYPES,
    QUALIFIED_CONTRACT_SOURCE_TYPES,
    QUALIFIED_COMMISSION_SOURCE_TYPES,
    QUALIFIED_FINANCING_SOURCE_TYPES,
    QUALIFIED_SLIPPAGE_SOURCE_TYPES,
    FrictionSourceSnapshot,
    FrictionSourceQualificationAssertion,
    FrictionQualificationStatus,
    FrictionEvidenceDataset,
    FrictionSourceProvenanceAttestation,
    FrictionAttestationStatus,
    FrictionVerificationMethod,
)
from apps.market_data.friction.tick_parser import (
    parse_exness_official_tick_history,
    parse_mt5_tick_export,
)
from apps.market_data.friction.distribution import (
    validate_spread_dataset_sufficiency,
    compute_distribution_statistics,
)
from apps.market_data.friction.validation import (
    validate_source_qualification_assertion,
    TRUSTED_PARSERS_BY_ROLE,
)
from apps.market_data.friction.provenance import compute_verification_proof


def _make_exness_csv_bytes(rows):
    header = '"Exness","Symbol","Timestamp","Bid","Ask"\n'
    body = "\n".join(rows) + "\n"
    return (header + body).encode("utf-8")


def _generate_synthetic_exness_ticks(distinct_days=5, samples_per_session=110):
    """Generate chronological synthetic Exness tick CSV rows across multiple days and sessions."""
    rows = []
    base_date = datetime(2026, 8, 25, 0, 0, 0, tzinfo=timezone.utc)
    current_time = base_date

    # Sessions: ASIAN (0-7), LONDON (8-12), NEW_YORK (13-20), ROLLOVER (21-23)
    session_hours = {
        "ASIAN": 2,
        "LONDON": 9,
        "NEW_YORK": 14,
        "ROLLOVER": 21,
    }
    rollover_samples = 35  # threshold is >= 30

    for day_offset in range(distinct_days):
        day_date = base_date + timedelta(days=day_offset)
        for session_name, hour in session_hours.items():
            count = rollover_samples if session_name == "ROLLOVER" else samples_per_session
            for s in range(count):
                ts = datetime(
                    day_date.year,
                    day_date.month,
                    day_date.day,
                    hour,
                    (s // 60) % 60,
                    s % 60,
                    (s * 1000) % 1000000,
                    tzinfo=timezone.utc,
                )
                ts_str = ts.strftime("%Y-%m-%d %H:%M:%S.%fZ")
                bid = Decimal("2500.123") + Decimal(str(s * 0.001))
                ask = bid + Decimal("0.250")
                rows.append(f'"Exness","XAUUSDc","{ts_str}","{bid}","{ask}"')

    # Sort rows by timestamp to ensure strictly chronological
    def _extract_ts(r):
        parts = r.split(",")
        return parts[2].strip('"')

    rows.sort(key=_extract_ts)
    return rows


# =============================================================================
# 1. FrictionSourceType Registration and Qualification Isolation
# =============================================================================

def test_exness_source_type_registered_and_isolated():
    """Verify EXNESS_OFFICIAL_TICK_HISTORY is registered and qualified ONLY for spread."""
    assert hasattr(FrictionSourceType, "EXNESS_OFFICIAL_TICK_HISTORY")
    assert FrictionSourceType.EXNESS_OFFICIAL_TICK_HISTORY.value == "EXNESS_OFFICIAL_TICK_HISTORY"

    # Must be present in spread sources
    assert FrictionSourceType.EXNESS_OFFICIAL_TICK_HISTORY.value in QUALIFIED_SPREAD_SOURCE_TYPES
    # MT5 source must be preserved
    assert FrictionSourceType.MT5_TICK_HISTORY_EXPORT.value in QUALIFIED_SPREAD_SOURCE_TYPES

    # Must NEVER qualify other friction roles
    assert FrictionSourceType.EXNESS_OFFICIAL_TICK_HISTORY.value not in QUALIFIED_LEGAL_ENTITY_SOURCE_TYPES
    assert FrictionSourceType.EXNESS_OFFICIAL_TICK_HISTORY.value not in QUALIFIED_CONTRACT_SOURCE_TYPES
    assert FrictionSourceType.EXNESS_OFFICIAL_TICK_HISTORY.value not in QUALIFIED_COMMISSION_SOURCE_TYPES
    assert FrictionSourceType.EXNESS_OFFICIAL_TICK_HISTORY.value not in QUALIFIED_FINANCING_SOURCE_TYPES
    assert FrictionSourceType.EXNESS_OFFICIAL_TICK_HISTORY.value not in QUALIFIED_SLIPPAGE_SOURCE_TYPES


def test_trusted_parsers_allowlist():
    """Verify parse_exness_official_tick_history is trusted for SPREAD_DATASET only."""
    assert "parse_exness_official_tick_history" in TRUSTED_PARSERS_BY_ROLE["SPREAD_DATASET"]
    assert "parse_mt5_tick_export" in TRUSTED_PARSERS_BY_ROLE["SPREAD_DATASET"]
    assert "parse_exness_official_tick_history" not in TRUSTED_PARSERS_BY_ROLE.get("SLIPPAGE_DATASET", set())
    assert "parse_exness_official_tick_history" not in TRUSTED_PARSERS_BY_ROLE.get("LEGAL_ENTITY", set())
    assert "parse_exness_official_tick_history" not in TRUSTED_PARSERS_BY_ROLE.get("CONTRACT_SPEC", set())
    assert "parse_exness_official_tick_history" not in TRUSTED_PARSERS_BY_ROLE.get("COMMISSION", set())
    assert "parse_exness_official_tick_history" not in TRUSTED_PARSERS_BY_ROLE.get("FINANCING", set())


# =============================================================================
# 2. Schema Parser Success and Normalization
# =============================================================================

def test_parse_exness_official_tick_history_valid():
    """Verify parsing valid official Exness CSV tick data."""
    rows = [
        '"Exness","XAUUSDc","2026-09-01 00:00:01.123Z","2498.100","2498.350"',
        '"Exness","XAUUSDc","2026-09-01 00:00:02.500Z","2498.150","2498.400"',
    ]
    raw_bytes = _make_exness_csv_bytes(rows)

    ticks, summary = parse_exness_official_tick_history(
        raw_bytes,
        expected_symbol="XAUUSD",
        expected_broker_symbol="XAUUSDc",
        expected_account_tier="STANDARD_CENT",
    )

    assert len(ticks) == 2
    assert summary["sample_count"] == 2
    assert summary["symbol"] == "XAUUSD"
    assert summary["broker_symbol"] == "XAUUSDc"
    assert summary["venue"] == "EXNESS"
    assert summary["distinct_trading_days"] == 1

    t0 = ticks[0]
    assert t0["bid"] == Decimal("2498.100")
    assert t0["ask"] == Decimal("2498.350")
    assert t0["spread_price"] == Decimal("0.250")
    assert t0["mid"] == Decimal("2498.225")
    assert t0["spread_bps"] > Decimal("0")
    assert t0["session"] == "ASIAN"
    assert t0["timestamp"].tzinfo == timezone.utc


# =============================================================================
# 3. Scope Binding & Suffix Enforcement (XAUUSDc vs XAUUSD vs XAUUSDm)
# =============================================================================

def test_parse_exness_symbol_scope_binding():
    """Under STANDARD_CENT with expected_broker_symbol='XAUUSDc', accept XAUUSDc, reject others."""
    # 1. Matching broker symbol XAUUSDc succeeds
    rows = ['"Exness","XAUUSDc","2026-09-01 00:00:01.000Z","2498.100","2498.350"']
    raw_bytes = _make_exness_csv_bytes(rows)
    ticks, _ = parse_exness_official_tick_history(
        raw_bytes,
        expected_symbol="XAUUSD",
        expected_broker_symbol="XAUUSDc",
        expected_account_tier="STANDARD_CENT",
    )
    assert len(ticks) == 1

    # 2. Raw standard XAUUSD rejected under explicit XAUUSDc scope
    rows_std = ['"Exness","XAUUSD","2026-09-01 00:00:01.000Z","2498.100","2498.350"']
    with pytest.raises(ValueError, match="Symbol mismatch"):
        parse_exness_official_tick_history(
            _make_exness_csv_bytes(rows_std),
            expected_symbol="XAUUSD",
            expected_broker_symbol="XAUUSDc",
            expected_account_tier="STANDARD_CENT",
        )

    # 3. Standard mini XAUUSDm rejected under explicit XAUUSDc scope
    rows_mini = ['"Exness","XAUUSDm","2026-09-01 00:00:01.000Z","2498.100","2498.350"']
    with pytest.raises(ValueError, match="Symbol mismatch"):
        parse_exness_official_tick_history(
            _make_exness_csv_bytes(rows_mini),
            expected_symbol="XAUUSD",
            expected_broker_symbol="XAUUSDc",
            expected_account_tier="STANDARD_CENT",
        )

    # 4. Missing expected_broker_symbol under STANDARD_CENT fails closed
    with pytest.raises(ValueError, match="STANDARD_CENT execution scope requires explicit expected_broker_symbol"):
        parse_exness_official_tick_history(
            raw_bytes,
            expected_symbol="XAUUSD",
            expected_broker_symbol=None,
            expected_account_tier="STANDARD_CENT",
        )


# =============================================================================
# 4. Hostile Schema Rejections
# =============================================================================

def test_parse_exness_wrong_venue():
    """Rejects rows if venue is not 'exness'."""
    rows = ['"Binance","XAUUSDc","2026-09-01 00:00:01.000Z","2498.100","2498.350"']
    with pytest.raises(ValueError, match="Wrong venue identity"):
        parse_exness_official_tick_history(
            _make_exness_csv_bytes(rows),
            expected_symbol="XAUUSD",
            expected_broker_symbol="XAUUSDc",
            expected_account_tier="STANDARD_CENT",
        )


def test_parse_exness_missing_or_corrupt_header():
    """Rejects tick export with wrong header schema."""
    corrupt_header = b'"Exness","Symbol","Time","Bid","Ask"\n"Exness","XAUUSDc","2026-09-01 00:00:01Z","2498","2499"\n'
    with pytest.raises(ValueError, match="Unsupported Exness tick export schema"):
        parse_exness_official_tick_history(corrupt_header)


def test_parse_exness_crossed_quotes():
    """Rejects ask <= bid."""
    # Ask < Bid
    rows_inverted = ['"Exness","XAUUSDc","2026-09-01 00:00:01.000Z","2498.500","2498.100"']
    with pytest.raises(ValueError, match="Crossed or inverted quote"):
        parse_exness_official_tick_history(
            _make_exness_csv_bytes(rows_inverted),
            expected_symbol="XAUUSD",
            expected_broker_symbol="XAUUSDc",
            expected_account_tier="STANDARD_CENT",
        )

    # Ask == Bid
    rows_equal = ['"Exness","XAUUSDc","2026-09-01 00:00:01.000Z","2498.500","2498.500"']
    with pytest.raises(ValueError, match="Crossed or inverted quote"):
        parse_exness_official_tick_history(
            _make_exness_csv_bytes(rows_equal),
            expected_symbol="XAUUSD",
            expected_broker_symbol="XAUUSDc",
            expected_account_tier="STANDARD_CENT",
        )


def test_parse_exness_non_positive_quotes():
    """Rejects quotes <= 0."""
    rows = ['"Exness","XAUUSDc","2026-09-01 00:00:01.000Z","0.000","2498.500"']
    with pytest.raises(ValueError, match="Non-positive quote values"):
        parse_exness_official_tick_history(
            _make_exness_csv_bytes(rows),
            expected_symbol="XAUUSD",
            expected_broker_symbol="XAUUSDc",
            expected_account_tier="STANDARD_CENT",
        )


def test_parse_exness_naive_timestamp():
    """Rejects naive timestamps lacking explicit UTC indicator."""
    rows = ['"Exness","XAUUSDc","2026-09-01 00:00:01.000","2498.100","2498.350"']
    with pytest.raises(ValueError, match="Naive timestamp"):
        parse_exness_official_tick_history(
            _make_exness_csv_bytes(rows),
            expected_symbol="XAUUSD",
            expected_broker_symbol="XAUUSDc",
            expected_account_tier="STANDARD_CENT",
        )


def test_parse_exness_future_timestamp():
    """Rejects timestamps in the future."""
    rows = ['"Exness","XAUUSDc","2099-01-01 00:00:01.000Z","2498.100","2498.350"']
    with pytest.raises(ValueError, match="Future timestamp"):
        parse_exness_official_tick_history(
            _make_exness_csv_bytes(rows),
            expected_symbol="XAUUSD",
            expected_broker_symbol="XAUUSDc",
            expected_account_tier="STANDARD_CENT",
        )


def test_parse_exness_non_chronological():
    """Rejects timestamps that move backward."""
    rows = [
        '"Exness","XAUUSDc","2026-09-01 00:00:05.000Z","2498.100","2498.350"',
        '"Exness","XAUUSDc","2026-09-01 00:00:01.000Z","2498.100","2498.350"',
    ]
    with pytest.raises(ValueError, match="Non-chronological timestamp sequence"):
        parse_exness_official_tick_history(
            _make_exness_csv_bytes(rows),
            expected_symbol="XAUUSD",
            expected_broker_symbol="XAUUSDc",
            expected_account_tier="STANDARD_CENT",
        )


# =============================================================================
# 5. Temporal Gate Truth: 1-Day Fails vs 5-Day Passes
# =============================================================================

def test_one_day_dataset_fails_trading_date_gate():
    """A 1-day dataset (even with >= 1000 samples) must FAIL spread sufficiency."""
    # Generate 1100 samples all on 2026-09-01
    rows = []
    base_ts = datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)
    for i in range(1100):
        ts = base_ts + timedelta(seconds=i * 2)
        ts_str = ts.strftime("%Y-%m-%d %H:%M:%S.%fZ")
        rows.append(f'"Exness","XAUUSDc","{ts_str}","2500.000","2500.250"')

    raw_bytes = _make_exness_csv_bytes(rows)
    ticks, summary = parse_exness_official_tick_history(
        raw_bytes,
        expected_symbol="XAUUSD",
        expected_broker_symbol="XAUUSDc",
        expected_account_tier="STANDARD_CENT",
    )
    assert len(ticks) == 1100
    assert summary["distinct_trading_days"] == 1

    # Evaluate sufficiency gate
    is_sufficient, reasons = validate_spread_dataset_sufficiency(ticks)
    assert not is_sufficient
    assert any("Insufficient temporal span" in r for r in reasons)


def test_five_day_dataset_satisfies_spread_gate():
    """A multi-day dataset spanning 5 distinct trading dates with required session distribution PASSES."""
    rows = _generate_synthetic_exness_ticks(distinct_days=5, samples_per_session=110)
    raw_bytes = _make_exness_csv_bytes(rows)

    ticks, summary = parse_exness_official_tick_history(
        raw_bytes,
        expected_symbol="XAUUSD",
        expected_broker_symbol="XAUUSDc",
        expected_account_tier="STANDARD_CENT",
    )
    assert summary["distinct_trading_days"] == 5
    assert len(ticks) >= 1000

    is_sufficient, reasons = validate_spread_dataset_sufficiency(ticks)
    assert is_sufficient, f"Sufficiency failed: {reasons}"
    assert len(reasons) == 0


# =============================================================================
# 6. Qualification Assertion and Provenance Integration
# =============================================================================

@pytest.mark.django_db
def test_exness_source_qualification_assertion_success():
    """Verify full qualification assertion flow for EXNESS_OFFICIAL_TICK_HISTORY."""
    rows = _generate_synthetic_exness_ticks(distinct_days=5, samples_per_session=110)
    raw_bytes = _make_exness_csv_bytes(rows)
    raw_sha = hashlib.sha256(raw_bytes).hexdigest()

    # Create snapshot using ingestion helper
    from apps.market_data.friction.ingestion import ingest_friction_source_snapshot
    from apps.market_data.friction.provenance import compute_capture_context_hash
    source_url = "https://www.exness.com/tick-history/XAUUSDc_2026_09_01.csv"
    snapshot, _ = ingest_friction_source_snapshot(
        source_name="EXNESS_OFFICIAL_TICK_HISTORY",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD_CENT",
        source_type=FrictionSourceType.EXNESS_OFFICIAL_TICK_HISTORY.value,
        source_url=source_url,
        raw_content=raw_bytes,
        retrieved_at=datetime.now(timezone.utc),
        known_at=datetime.now(timezone.utc),
    )

    # Compute normalized rows for assertion hash
    from apps.market_data.friction.artifact_parsers import compute_normalized_evidence_hash
    ticks, _ = parse_exness_official_tick_history(
        raw_bytes,
        expected_symbol="XAUUSD",
        expected_broker_symbol="XAUUSDc",
        expected_account_tier="STANDARD_CENT",
    )
    norm_rows = [
        f"{t['timestamp'].astimezone(timezone.utc).isoformat()}|{t['bid']}|{t['ask']}|{t.get('spread_bps', '')}"
        for t in ticks
    ]
    raw_ds_sha = hashlib.sha256("\n".join(norm_rows).encode("utf-8")).hexdigest()
    norm_hash = compute_normalized_evidence_hash({"raw_dataset_sha256": raw_ds_sha})

    # Create provenance attestation
    cap_dt = snapshot.retrieved_at or datetime.now(timezone.utc)
    meta = {
        "requested_url": source_url,
        "final_url": source_url,
        "hostname": "www.exness.com",
        "http_status": 200,
        "raw_response_sha256": raw_sha,
        "collector_version": "1.0.0",
        "captured_at": cap_dt.isoformat(),
    }
    ctx_hash = compute_capture_context_hash(meta, FrictionVerificationMethod.BROKER_OFFICIAL_URL_CAPTURE.value)
    proof = compute_verification_proof(
        source_snapshot_id=snapshot.snapshot_id,
        raw_artifact_sha256=raw_sha,
        component_role="SPREAD_DATASET",
        verification_method=FrictionVerificationMethod.BROKER_OFFICIAL_URL_CAPTURE.value,
        source_type=snapshot.source_type,
        venue=snapshot.venue,
        symbol=snapshot.symbol,
        account_tier=snapshot.account_tier,
        captured_at=cap_dt,
        verifier_identity="TEST_SUITE_ISOLATED_PROVENANCE_SEAM",
        capture_context_hash=ctx_hash,
    )
    from apps.market_data.friction.ingestion import create_friction_provenance_attestation
    attestation = create_friction_provenance_attestation(
        source_snapshot=snapshot,
        component_role="SPREAD_DATASET",
        verification_method=FrictionVerificationMethod.BROKER_OFFICIAL_URL_CAPTURE.value,
        verifier_identity="TEST_SUITE_ISOLATED_PROVENANCE_SEAM",
        captured_at=cap_dt,
        reviewed_at=datetime.now(timezone.utc),
        attestation_status=FrictionAttestationStatus.VERIFIED.value,
        verification_proof=proof,
        provenance_metadata=meta,
    )

    # Create assertion
    assertion = FrictionSourceQualificationAssertion.objects.create(
        source_snapshot=snapshot,
        component_role="SPREAD_DATASET",
        qualification_status=FrictionQualificationStatus.QUALIFIED.value,
        parser_name="parse_exness_official_tick_history",
        parser_version="1.0.0",
        raw_artifact_sha256=raw_sha,
        normalized_evidence_hash=norm_hash,
        provenance_attestation=attestation,
    )

    # Validate assertion
    is_valid, errors, parsed_data = validate_source_qualification_assertion(
        snapshot=snapshot,
        assertion=assertion,
        expected_component_role="SPREAD_DATASET",
        expected_parser="parse_exness_official_tick_history",
        expected_symbol="XAUUSD",
        expected_account_tier="STANDARD_CENT",
        expected_venue="EXNESS",
        expected_broker_symbol="XAUUSDc",
    )
    assert is_valid, f"Assertion validation failed: {errors}"
    assert parsed_data["sample_count"] == len(ticks)


@pytest.mark.django_db
def test_exness_source_cannot_qualify_slippage_assertion():
    """Verify that attempting to qualify EXNESS_OFFICIAL_TICK_HISTORY for slippage fails."""
    from apps.market_data.friction.ingestion import ingest_friction_source_snapshot
    rows = ['"Exness","XAUUSDc","2026-09-01 00:00:01.000Z","2498.100","2498.350"']
    raw_bytes = _make_exness_csv_bytes(rows)
    raw_sha = hashlib.sha256(raw_bytes).hexdigest()

    snapshot, _ = ingest_friction_source_snapshot(
        source_name="EXNESS_OFFICIAL_TICK_HISTORY",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD_CENT",
        source_type=FrictionSourceType.EXNESS_OFFICIAL_TICK_HISTORY.value,
        source_url="https://ticks.ex2archive.com/2026/09/Exness_XAUUSDc_2026_09_01.zip",
        raw_content=raw_bytes,
        retrieved_at=datetime.now(timezone.utc),
        known_at=datetime.now(timezone.utc),
    )

    # Attempt to assert SLIPPAGE_DATASET using parse_exness_official_tick_history
    assertion = FrictionSourceQualificationAssertion.objects.create(
        source_snapshot=snapshot,
        component_role="SLIPPAGE_DATASET",
        qualification_status=FrictionQualificationStatus.QUALIFIED.value,
        parser_name="parse_exness_official_tick_history",
        parser_version="1.0.0",
        raw_artifact_sha256=raw_sha,
        normalized_evidence_hash="dummy_hash",
    )

    is_valid, errors, _ = validate_source_qualification_assertion(
        snapshot=snapshot,
        assertion=assertion,
        expected_component_role="SLIPPAGE_DATASET",
        expected_symbol="XAUUSD",
        expected_account_tier="STANDARD_CENT",
        expected_venue="EXNESS",
        expected_broker_symbol="XAUUSDc",
    )
    assert not is_valid
    assert any("not in trusted allowlist" in err for err in errors)


# =============================================================================
# 7. Hostile Provenance Method & Source Type Binding Tests
# =============================================================================

@pytest.mark.django_db
def test_exness_with_mt5_direct_export_rejected():
    """EXNESS_OFFICIAL_TICK_HISTORY authenticated via MT5_DIRECT_EXPORT must be REJECTED."""
    rows = _generate_synthetic_exness_ticks(distinct_days=5, samples_per_session=110)
    raw_bytes = _make_exness_csv_bytes(rows)
    raw_sha = hashlib.sha256(raw_bytes).hexdigest()

    from apps.market_data.friction.ingestion import ingest_friction_source_snapshot, create_friction_provenance_attestation
    snapshot, _ = ingest_friction_source_snapshot(
        source_name="EXNESS_OFFICIAL_TICK_HISTORY",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD_CENT",
        source_type=FrictionSourceType.EXNESS_OFFICIAL_TICK_HISTORY.value,
        source_url="https://ticks.ex2archive.com/2026/09/Exness_XAUUSDc_2026_09_01.zip",
        raw_content=raw_bytes,
        retrieved_at=datetime.now(timezone.utc),
        known_at=datetime.now(timezone.utc),
    )

    cap_dt = snapshot.retrieved_at
    proof = compute_verification_proof(
        source_snapshot_id=snapshot.snapshot_id,
        raw_artifact_sha256=raw_sha,
        component_role="SPREAD_DATASET",
        verification_method=FrictionVerificationMethod.MT5_DIRECT_EXPORT.value,
        source_type=snapshot.source_type,
        venue=snapshot.venue,
        symbol=snapshot.symbol,
        account_tier=snapshot.account_tier,
        captured_at=cap_dt,
        verifier_identity="TEST_SUITE_ISOLATED_PROVENANCE_SEAM",
    )
    attestation = create_friction_provenance_attestation(
        source_snapshot=snapshot,
        component_role="SPREAD_DATASET",
        verification_method=FrictionVerificationMethod.MT5_DIRECT_EXPORT.value,
        verifier_identity="TEST_SUITE_ISOLATED_PROVENANCE_SEAM",
        captured_at=cap_dt,
        reviewed_at=datetime.now(timezone.utc),
        attestation_status=FrictionAttestationStatus.VERIFIED.value,
        verification_proof=proof,
    )

    assertion = FrictionSourceQualificationAssertion.objects.create(
        source_snapshot=snapshot,
        component_role="SPREAD_DATASET",
        qualification_status=FrictionQualificationStatus.QUALIFIED.value,
        parser_name="parse_exness_official_tick_history",
        parser_version="1.0.0",
        raw_artifact_sha256=raw_sha,
        normalized_evidence_hash="dummy_hash",
        provenance_attestation=attestation,
    )

    is_valid, errors, _ = validate_source_qualification_assertion(
        snapshot=snapshot,
        assertion=assertion,
        expected_component_role="SPREAD_DATASET",
        expected_parser="parse_exness_official_tick_history",
        expected_symbol="XAUUSD",
        expected_account_tier="STANDARD_CENT",
        expected_venue="EXNESS",
        expected_broker_symbol="XAUUSDc",
    )
    assert not is_valid
    assert any("PROVENANCE_METHOD_SOURCE_MISMATCH" in err for err in errors)


@pytest.mark.django_db
def test_mt5_source_with_mt5_direct_export_accepted():
    """MT5_TICK_HISTORY_EXPORT + MT5_DIRECT_EXPORT is ACCEPTED."""
    mt5_lines = [
        "<DATE>\t<TIME>\t<BID>\t<ASK>\t<SYMBOL>",
        "2026.09.01\t00:00:01.123+00:00\t2500.000\t2500.250\tXAUUSDc",
    ]
    raw_bytes = "\n".join(mt5_lines).encode("utf-8")
    raw_sha = hashlib.sha256(raw_bytes).hexdigest()

    from apps.market_data.friction.ingestion import ingest_friction_source_snapshot, create_friction_provenance_attestation
    from apps.market_data.friction.artifact_parsers import compute_normalized_evidence_hash
    from apps.market_data.friction.provenance import compute_capture_context_hash
    snapshot, _ = ingest_friction_source_snapshot(
        source_name="EXNESS_MT5_TICKS",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD_CENT",
        source_type=FrictionSourceType.MT5_TICK_HISTORY_EXPORT.value,
        source_url="file:///mock/ticks.csv",
        raw_content=raw_bytes,
        retrieved_at=datetime.now(timezone.utc),
        known_at=datetime.now(timezone.utc),
    )

    ticks, _ = parse_mt5_tick_export(
        raw_bytes,
        expected_symbol="XAUUSD",
        expected_broker_symbol="XAUUSDc",
        expected_account_tier="STANDARD_CENT",
    )
    norm_rows = [
        f"{t['timestamp'].astimezone(timezone.utc).isoformat()}|{t['bid']}|{t['ask']}|{t.get('spread_bps', '')}"
        for t in ticks
    ]
    raw_ds_sha = hashlib.sha256("\n".join(norm_rows).encode("utf-8")).hexdigest()
    norm_hash = compute_normalized_evidence_hash({"raw_dataset_sha256": raw_ds_sha})

    cap_dt = snapshot.retrieved_at
    meta = {
        "server": "Exness-Real25",
        "export_type": "TICKS",
        "collector_version": "1.0.0",
        "raw_sha256": raw_sha,
    }
    ctx_hash = compute_capture_context_hash(meta, FrictionVerificationMethod.MT5_DIRECT_EXPORT.value)
    proof = compute_verification_proof(
        source_snapshot_id=snapshot.snapshot_id,
        raw_artifact_sha256=raw_sha,
        component_role="SPREAD_DATASET",
        verification_method=FrictionVerificationMethod.MT5_DIRECT_EXPORT.value,
        source_type=snapshot.source_type,
        venue=snapshot.venue,
        symbol=snapshot.symbol,
        account_tier=snapshot.account_tier,
        captured_at=cap_dt,
        verifier_identity="TEST_SUITE_ISOLATED_PROVENANCE_SEAM",
        capture_context_hash=ctx_hash,
    )
    attestation = create_friction_provenance_attestation(
        source_snapshot=snapshot,
        component_role="SPREAD_DATASET",
        verification_method=FrictionVerificationMethod.MT5_DIRECT_EXPORT.value,
        verifier_identity="TEST_SUITE_ISOLATED_PROVENANCE_SEAM",
        captured_at=cap_dt,
        reviewed_at=datetime.now(timezone.utc),
        attestation_status=FrictionAttestationStatus.VERIFIED.value,
        verification_proof=proof,
        provenance_metadata=meta,
    )

    assertion = FrictionSourceQualificationAssertion.objects.create(
        source_snapshot=snapshot,
        component_role="SPREAD_DATASET",
        qualification_status=FrictionQualificationStatus.QUALIFIED.value,
        parser_name="parse_mt5_tick_export",
        parser_version="1.0.0",
        raw_artifact_sha256=raw_sha,
        normalized_evidence_hash=norm_hash,
        provenance_attestation=attestation,
    )

    is_valid, errors, parsed_data = validate_source_qualification_assertion(
        snapshot=snapshot,
        assertion=assertion,
        expected_component_role="SPREAD_DATASET",
        expected_parser="parse_mt5_tick_export",
        expected_symbol="XAUUSD",
        expected_account_tier="STANDARD_CENT",
        expected_venue="EXNESS",
        expected_broker_symbol="XAUUSDc",
    )
    assert is_valid, f"MT5 direct export should be valid: {errors}"
    assert parsed_data["sample_count"] == 1


@pytest.mark.django_db
def test_mt5_source_with_broker_url_capture_rejected():
    """MT5_TICK_HISTORY_EXPORT authenticated via BROKER_OFFICIAL_URL_CAPTURE must be REJECTED."""
    mt5_lines = [
        "<DATE>\t<TIME>\t<BID>\t<ASK>\t<SYMBOL>",
        "2026.09.01\t00:00:01.123+00:00\t2500.000\t2500.250\tXAUUSDc",
    ]
    raw_bytes = "\n".join(mt5_lines).encode("utf-8")
    raw_sha = hashlib.sha256(raw_bytes).hexdigest()

    from apps.market_data.friction.ingestion import ingest_friction_source_snapshot, create_friction_provenance_attestation
    from apps.market_data.friction.artifact_parsers import compute_normalized_evidence_hash
    from apps.market_data.friction.provenance import compute_capture_context_hash
    snapshot, _ = ingest_friction_source_snapshot(
        source_name="EXNESS_MT5_TICKS",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD_CENT",
        source_type=FrictionSourceType.MT5_TICK_HISTORY_EXPORT.value,
        source_url="https://www.exness.com/tick-history/data.csv",
        raw_content=raw_bytes,
        retrieved_at=datetime.now(timezone.utc),
        known_at=datetime.now(timezone.utc),
    )

    ticks, _ = parse_mt5_tick_export(
        raw_bytes,
        expected_symbol="XAUUSD",
        expected_broker_symbol="XAUUSDc",
        expected_account_tier="STANDARD_CENT",
    )
    norm_rows = [
        f"{t['timestamp'].astimezone(timezone.utc).isoformat()}|{t['bid']}|{t['ask']}|{t.get('spread_bps', '')}"
        for t in ticks
    ]
    raw_ds_sha = hashlib.sha256("\n".join(norm_rows).encode("utf-8")).hexdigest()
    norm_hash = compute_normalized_evidence_hash({"raw_dataset_sha256": raw_ds_sha})

    cap_dt = snapshot.retrieved_at
    meta = {
        "requested_url": "https://www.exness.com/tick-history/data.csv",
        "final_url": "https://www.exness.com/tick-history/data.csv",
        "hostname": "www.exness.com",
        "http_status": 200,
        "raw_response_sha256": raw_sha,
        "collector_version": "1.0.0",
        "captured_at": cap_dt.isoformat(),
    }
    ctx_hash = compute_capture_context_hash(meta, FrictionVerificationMethod.BROKER_OFFICIAL_URL_CAPTURE.value)
    proof = compute_verification_proof(
        source_snapshot_id=snapshot.snapshot_id,
        raw_artifact_sha256=raw_sha,
        component_role="SPREAD_DATASET",
        verification_method=FrictionVerificationMethod.BROKER_OFFICIAL_URL_CAPTURE.value,
        source_type=snapshot.source_type,
        venue=snapshot.venue,
        symbol=snapshot.symbol,
        account_tier=snapshot.account_tier,
        captured_at=cap_dt,
        verifier_identity="TEST_SUITE_ISOLATED_PROVENANCE_SEAM",
        capture_context_hash=ctx_hash,
    )
    attestation = create_friction_provenance_attestation(
        source_snapshot=snapshot,
        component_role="SPREAD_DATASET",
        verification_method=FrictionVerificationMethod.BROKER_OFFICIAL_URL_CAPTURE.value,
        verifier_identity="TEST_SUITE_ISOLATED_PROVENANCE_SEAM",
        captured_at=cap_dt,
        reviewed_at=datetime.now(timezone.utc),
        attestation_status=FrictionAttestationStatus.VERIFIED.value,
        verification_proof=proof,
        provenance_metadata=meta,
    )

    assertion = FrictionSourceQualificationAssertion.objects.create(
        source_snapshot=snapshot,
        component_role="SPREAD_DATASET",
        qualification_status=FrictionQualificationStatus.QUALIFIED.value,
        parser_name="parse_mt5_tick_export",
        parser_version="1.0.0",
        raw_artifact_sha256=raw_sha,
        normalized_evidence_hash=norm_hash,
        provenance_attestation=attestation,
    )

    is_valid, errors, _ = validate_source_qualification_assertion(
        snapshot=snapshot,
        assertion=assertion,
        expected_component_role="SPREAD_DATASET",
        expected_parser="parse_mt5_tick_export",
        expected_symbol="XAUUSD",
        expected_account_tier="STANDARD_CENT",
        expected_venue="EXNESS",
        expected_broker_symbol="XAUUSDc",
    )
    assert not is_valid
    assert any("PROVENANCE_METHOD_SOURCE_MISMATCH" in err for err in errors)


@pytest.mark.django_db
def test_attestation_source_type_mismatch_rejected():
    """Attestation source_type mismatching snapshot source_type must be REJECTED."""
    rows = _generate_synthetic_exness_ticks(distinct_days=5, samples_per_session=110)
    raw_bytes = _make_exness_csv_bytes(rows)
    raw_sha = hashlib.sha256(raw_bytes).hexdigest()

    from apps.market_data.friction.ingestion import ingest_friction_source_snapshot, create_friction_provenance_attestation
    snapshot, _ = ingest_friction_source_snapshot(
        source_name="EXNESS_OFFICIAL_TICK_HISTORY",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD_CENT",
        source_type=FrictionSourceType.EXNESS_OFFICIAL_TICK_HISTORY.value,
        source_url="https://ticks.ex2archive.com/2026/09/Exness_XAUUSDc_2026_09_01.zip",
        raw_content=raw_bytes,
        retrieved_at=datetime.now(timezone.utc),
        known_at=datetime.now(timezone.utc),
    )

    cap_dt = snapshot.retrieved_at
    proof = compute_verification_proof(
        source_snapshot_id=snapshot.snapshot_id,
        raw_artifact_sha256=raw_sha,
        component_role="SPREAD_DATASET",
        verification_method=FrictionVerificationMethod.BROKER_OFFICIAL_URL_CAPTURE.value,
        source_type=FrictionSourceType.MT5_TICK_HISTORY_EXPORT.value,
        venue=snapshot.venue,
        symbol=snapshot.symbol,
        account_tier=snapshot.account_tier,
        captured_at=cap_dt,
        verifier_identity="TEST_SUITE_ISOLATED_PROVENANCE_SEAM",
    )
    attestation = create_friction_provenance_attestation(
        source_snapshot=snapshot,
        component_role="SPREAD_DATASET",
        verification_method=FrictionVerificationMethod.BROKER_OFFICIAL_URL_CAPTURE.value,
        source_type=FrictionSourceType.MT5_TICK_HISTORY_EXPORT.value,
        verifier_identity="TEST_SUITE_ISOLATED_PROVENANCE_SEAM",
        captured_at=cap_dt,
        reviewed_at=datetime.now(timezone.utc),
        attestation_status=FrictionAttestationStatus.VERIFIED.value,
        verification_proof=proof,
    )

    assertion = FrictionSourceQualificationAssertion.objects.create(
        source_snapshot=snapshot,
        component_role="SPREAD_DATASET",
        qualification_status=FrictionQualificationStatus.QUALIFIED.value,
        parser_name="parse_exness_official_tick_history",
        parser_version="1.0.0",
        raw_artifact_sha256=raw_sha,
        normalized_evidence_hash="dummy_hash",
        provenance_attestation=attestation,
    )

    is_valid, errors, _ = validate_source_qualification_assertion(
        snapshot=snapshot,
        assertion=assertion,
        expected_component_role="SPREAD_DATASET",
        expected_parser="parse_exness_official_tick_history",
        expected_symbol="XAUUSD",
        expected_account_tier="STANDARD_CENT",
        expected_venue="EXNESS",
        expected_broker_symbol="XAUUSDc",
    )
    assert not is_valid
    assert any("ATTESTATION_SOURCE_TYPE_MISMATCH" in err for err in errors)
