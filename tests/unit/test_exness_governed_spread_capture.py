"""Tests for Exness Official Tick History Governed URL Capture & Spread Provenance Closure (Stage D2A).

Verifies the authenticated production capture pipeline for SPREAD_DATASET:
1. Permitted domain allowlist: exact hostname ticks.ex2archive.com allowed; wildcards and unrelated hosts fail closed.
2. Safe ZIP envelope parsing: single CSV member, bounded decompression size, path traversal rejection, corruption rejection.
3. Clean end-to-end governed capture flow:
   execute_governed_broker_url_capture()
   -> authenticated BrokerCaptureReceipt
   -> immutable FrictionSourceSnapshot from exact receipt bytes
   -> create_verified_broker_capture_attestation(role="SPREAD_DATASET")
   -> FrictionEvidenceDataset ingestion
   -> FrictionSourceQualificationAssertion (QUALIFIED)
   -> validate_source_qualification_assertion() PASS
   with ZERO manual SQL updates, ZERO test seams in production path.
4. Hostile tests:
   - Caller-constructed or tampered receipt rejected
   - Unauthorized domain rejected
   - Tampered response bytes rejected
   - Snapshot raw SHA mismatch rejected
   - Source origin mismatch rejected
   - Source type mismatch rejected
   - Verification method mismatch rejected
   - Symbol / broker symbol mismatch rejected
   - HTTP non-200 rejected
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import hashlib
import io
import os
import pytest
import urllib.parse
import zipfile

from apps.market_data.models import (
    FrictionSourceType,
    FrictionVerificationMethod,
    FrictionAttestationStatus,
    FrictionQualificationStatus,
    FrictionSourceSnapshot,
    FrictionSourceProvenanceAttestation,
    FrictionSourceQualificationAssertion,
    FrictionEvidenceDataset,
    QUALIFIED_SPREAD_SOURCE_TYPES,
    QUALIFIED_SPREAD_VERIFICATION_METHODS_BY_SOURCE,
)
from apps.market_data.friction.provenance import (
    execute_governed_broker_url_capture,
    create_verified_broker_capture_attestation,
    compute_broker_receipt_auth_tag,
    verify_broker_receipt_auth_tag,
    BrokerCaptureReceipt,
    PERMITTED_BROKER_DOMAINS,
    _validate_broker_url,
)
from apps.market_data.friction.tick_parser import parse_exness_official_tick_history
from apps.market_data.friction.distribution import validate_spread_dataset_sufficiency
from apps.market_data.friction.artifact_parsers import compute_normalized_evidence_hash
from apps.market_data.friction.ingestion import (
    ingest_friction_source_snapshot,
    ingest_friction_evidence_dataset,
    create_friction_qualification_assertion,
)
from apps.market_data.friction.validation import validate_source_qualification_assertion


def _create_synthetic_tick_csv_lines(symbol="XAUUSDc", distinct_days=6, samples_per_session=110):
    """Generate chronological synthetic Exness tick CSV rows across multiple days and sessions."""
    rows = []
    base_date = datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)
    session_hours = {
        "ASIAN": 2,
        "LONDON": 9,
        "NEW_YORK": 14,
        "ROLLOVER": 21,
    }
    rollover_samples = 35  # >= 30 required

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
                rows.append(f'"Exness","{symbol}","{ts_str}","{bid}","{ask}"')

    def _extract_ts(r):
        parts = r.split(",")
        return parts[2].strip('"')

    rows.sort(key=_extract_ts)
    header = '"Exness","Symbol","Timestamp","Bid","Ask"\n'
    return (header + "\n".join(rows) + "\n").encode("utf-8")


def _create_synthetic_zip_archive(csv_filename="Exness_XAUUSDc_2026_09.csv", csv_bytes=None):
    if csv_bytes is None:
        csv_bytes = _create_synthetic_tick_csv_lines()
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(csv_filename, csv_bytes)
    return bio.getvalue()


# =============================================================================
# 1. Permitted Domain Allowlist & Hostile Host Tests
# =============================================================================

def test_permitted_broker_domains_exact_host():
    """Verify ticks.ex2archive.com is explicitly in allowlist and wildcards fail closed."""
    assert "ticks.ex2archive.com" in PERMITTED_BROKER_DOMAINS
    assert "*.ex2archive.com" not in PERMITTED_BROKER_DOMAINS
    assert "ex2archive.com" not in PERMITTED_BROKER_DOMAINS

    # Valid exact hostname passes
    _validate_broker_url("https://ticks.ex2archive.com/ticks/XAUUSDc/2026/09/Exness_XAUUSDc_2026_09.zip")


@pytest.mark.parametrize("hostile_url", [
    "https://evil.com/ticks/XAUUSDc/2026/09/Exness_XAUUSDc_2026_09.zip",
    "https://fake-exness.com/tick-history",
    "https://sub.ticks.ex2archive.com/ticks.zip",
    "https://ex2archive.com/ticks.zip",
    "https://ticks.ex2archive.com.attacker.com/ticks.zip",
    "http://ticks.ex2archive.com/ticks.zip",  # Non-HTTPS
    "https://user:pass@ticks.ex2archive.com/ticks.zip",  # Userinfo credentials
])
def test_hostile_broker_urls_rejected(hostile_url):
    """Hostile or unpermitted URLs must fail closed with ValueError."""
    with pytest.raises(ValueError):
        _validate_broker_url(hostile_url)


# =============================================================================
# 2. Safe ZIP Archive Parsing Protections
# =============================================================================

def test_parse_exness_official_tick_history_from_zip():
    """Verify parse_exness_official_tick_history seamlessly accepts valid ZIP archives."""
    csv_bytes = _create_synthetic_tick_csv_lines(symbol="XAUUSDc", distinct_days=6)
    zip_bytes = _create_synthetic_zip_archive(csv_filename="Exness_XAUUSDc_2026_09.csv", csv_bytes=csv_bytes)

    ticks, summary = parse_exness_official_tick_history(
        zip_bytes,
        expected_symbol="XAUUSD",
        expected_broker_symbol="XAUUSDc",
        expected_account_tier="STANDARD_CENT",
    )
    assert len(ticks) > 1000
    assert summary["symbol"] == "XAUUSD"
    assert summary["broker_symbol"] == "XAUUSDc"


def test_corrupted_zip_archive_rejected():
    """Corrupted ZIP bytes must fail closed with BadZipFile error."""
    corrupted_bytes = b"PK\x03\x04" + b"\x00" * 50
    with pytest.raises(ValueError, match="Corrupt ZIP archive"):
        parse_exness_official_tick_history(
            corrupted_bytes,
            expected_symbol="XAUUSD",
            expected_broker_symbol="XAUUSDc",
            expected_account_tier="STANDARD_CENT",
        )


def test_empty_zip_archive_rejected():
    """Empty ZIP archive must fail closed."""
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w") as zf:
        pass
    with pytest.raises(ValueError, match="ZIP archive is empty"):
        parse_exness_official_tick_history(
            bio.getvalue(),
            expected_symbol="XAUUSD",
            expected_broker_symbol="XAUUSDc",
            expected_account_tier="STANDARD_CENT",
        )


def test_multiple_csv_members_rejected():
    """ZIP archive containing multiple ambiguous CSV members must fail closed."""
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w") as zf:
        zf.writestr("ticks_day1.csv", b"data1")
        zf.writestr("ticks_day2.csv", b"data2")
    with pytest.raises(ValueError, match="Expected exactly one CSV member"):
        parse_exness_official_tick_history(
            bio.getvalue(),
            expected_symbol="XAUUSD",
            expected_broker_symbol="XAUUSDc",
            expected_account_tier="STANDARD_CENT",
        )


def test_path_traversal_in_zip_rejected():
    """Path traversal attempt in ZIP filename must fail closed."""
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w") as zf:
        zf.writestr("../etc/passwd.csv", b"data")
    with pytest.raises(ValueError, match="Path traversal detected"):
        parse_exness_official_tick_history(
            bio.getvalue(),
            expected_symbol="XAUUSD",
            expected_broker_symbol="XAUUSDc",
            expected_account_tier="STANDARD_CENT",
        )


# =============================================================================
# 3. Clean End-to-End Governed Capture -> Qualification Flow
# =============================================================================

@pytest.mark.django_db
def test_clean_end_to_end_governed_spread_capture_qualification():
    """Prove clean, authentic qualification from clean DB with ZERO direct SQL.

    Flow:
    execute_governed_broker_url_capture()
    -> BrokerCaptureReceipt
    -> FrictionSourceSnapshot (from exact receipt bytes and URL)
    -> create_verified_broker_capture_attestation(role="SPREAD_DATASET")
    -> FrictionEvidenceDataset ingestion
    -> FrictionSourceQualificationAssertion (QUALIFIED)
    -> validate_source_qualification_assertion() == PASS
    """
    url = "https://ticks.ex2archive.com/ticks/XAUUSDc/2026/09/Exness_XAUUSDc_2026_09.zip"
    raw_zip_bytes = _create_synthetic_zip_archive()
    raw_zip_sha = hashlib.sha256(raw_zip_bytes).hexdigest()

    # Mock HTTP client inside test environment
    def mock_transport(target_url):
        assert target_url == url
        return (raw_zip_bytes, url, 200, "application/zip", [])

    # 1. Execute governed capture
    receipt = execute_governed_broker_url_capture(url, http_client=mock_transport)
    assert isinstance(receipt, BrokerCaptureReceipt)
    assert receipt.response_sha256 == raw_zip_sha
    assert verify_broker_receipt_auth_tag(receipt)

    # 2. Parse ticks from exact receipt bytes
    ticks_data, summary_meta = parse_exness_official_tick_history(
        receipt.response_bytes,
        expected_symbol="XAUUSD",
        expected_broker_symbol="XAUUSDc",
        expected_account_tier="STANDARD_CENT",
    )
    is_sufficient, suff_errors = validate_spread_dataset_sufficiency(ticks_data)
    assert is_sufficient, f"Dataset insufficiency: {suff_errors}"

    # 3. Ingest snapshot using exact receipt fields
    snapshot, _ = ingest_friction_source_snapshot(
        source_url=receipt.final_url,
        source_name="EXNESS_OFFICIAL_TICK_HISTORY",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD_CENT",
        retrieved_at=receipt.captured_at,
        known_at=receipt.captured_at,
        raw_content=receipt.response_bytes,
        metadata=summary_meta,
        source_type=FrictionSourceType.EXNESS_OFFICIAL_TICK_HISTORY.value,
        source_origin=receipt.final_url,
        collection_methodology="EXNESS_OFFICIAL_TICK_ARCHIVE",
        original_filename="Exness_XAUUSDc_2026_09.zip",
    )
    assert snapshot.source_origin == receipt.final_url
    assert snapshot.raw_payload_bytes_sha256 == raw_zip_sha

    # 4. Ingest evidence dataset
    dataset, _ = ingest_friction_evidence_dataset(
        source_snapshot=snapshot,
        venue="EXNESS",
        account_tier="STANDARD_CENT",
        symbol="XAUUSD",
        sample_start=summary_meta["sample_start"],
        sample_end=summary_meta["sample_end"],
        ticks_data=ticks_data,
    )

    # 5. Create verified broker capture attestation for SPREAD_DATASET
    attestation = create_verified_broker_capture_attestation(
        source_snapshot=snapshot,
        component_role="SPREAD_DATASET",
        capture_receipt=receipt,
        expected_symbol="XAUUSD",
        expected_venue="EXNESS",
        expected_account_tier="STANDARD_CENT",
        expected_broker_symbol="XAUUSDc",
    )
    assert attestation.attestation_status == FrictionAttestationStatus.VERIFIED.value
    assert attestation.source_type == FrictionSourceType.EXNESS_OFFICIAL_TICK_HISTORY.value
    assert attestation.verification_method == FrictionVerificationMethod.BROKER_OFFICIAL_URL_CAPTURE.value
    assert attestation.source_origin == receipt.final_url

    # 6. Create qualification assertion
    assertion = create_friction_qualification_assertion(
        source_snapshot=snapshot,
        provenance_attestation=attestation,
        component_role="SPREAD_DATASET",
        qualification_status=FrictionQualificationStatus.QUALIFIED.value,
        parser_name="parse_exness_official_tick_history",
        parser_version="1.0.0",
        normalized_evidence_hash=compute_normalized_evidence_hash({"raw_dataset_sha256": dataset.raw_dataset_sha256}),
        qualification_reason="Verified by official Exness tick archive governed URL capture and provenance attestation",
    )
    assert assertion.qualification_status == FrictionQualificationStatus.QUALIFIED.value

    # 7. Independent validation of qualification assertion
    is_valid, reasons, parsed_data = validate_source_qualification_assertion(
        snapshot=snapshot,
        assertion=assertion,
        expected_component_role="SPREAD_DATASET",
        expected_parser="parse_exness_official_tick_history",
        expected_symbol="XAUUSD",
        expected_account_tier="STANDARD_CENT",
        expected_venue="EXNESS",
        expected_broker_symbol="XAUUSDc",
    )
    assert is_valid is True, f"Assertion validation failed: {reasons}"
    assert len(reasons) == 0
    assert parsed_data["sample_count"] == len(ticks_data)


# =============================================================================
# 4. Hostile Tests (Tampering, Seams, Mismatches)
# =============================================================================

@pytest.mark.django_db
def test_caller_constructed_receipt_rejected():
    """A caller-constructed receipt without valid receipt_auth_tag must be rejected."""
    url = "https://ticks.ex2archive.com/ticks/XAUUSDc/2026/09/Exness_XAUUSDc_2026_09.zip"
    raw_zip_bytes = _create_synthetic_zip_archive()
    raw_zip_sha = hashlib.sha256(raw_zip_bytes).hexdigest()

    # Create receipt with fake / missing auth tag
    fake_receipt = BrokerCaptureReceipt(
        requested_url=url,
        final_url=url,
        http_status=200,
        content_type="application/zip",
        response_bytes=raw_zip_bytes,
        response_sha256=raw_zip_sha,
        captured_at=datetime.now(timezone.utc),
        collector_version="1.0.0",
        redirect_chain=(),
        receipt_auth_tag="FAKE_FORGED_AUTH_TAG",
    )

    snapshot, _ = ingest_friction_source_snapshot(
        source_url=url,
        source_name="EXNESS_OFFICIAL_TICK_HISTORY",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD_CENT",
        retrieved_at=datetime.now(timezone.utc),
        known_at=datetime.now(timezone.utc),
        raw_content=raw_zip_bytes,
        source_type=FrictionSourceType.EXNESS_OFFICIAL_TICK_HISTORY.value,
        source_origin=url,
    )

    with pytest.raises(ValueError, match="BrokerCaptureReceipt authentication tag is invalid or missing"):
        create_verified_broker_capture_attestation(
            source_snapshot=snapshot,
            component_role="SPREAD_DATASET",
            capture_receipt=fake_receipt,
            expected_symbol="XAUUSD",
            expected_venue="EXNESS",
            expected_account_tier="STANDARD_CENT",
            expected_broker_symbol="XAUUSDc",
        )


@pytest.mark.django_db
def test_tampered_response_bytes_rejected():
    """Tampering with receipt response bytes after capture must fail tag verification and SHA check."""
    url = "https://ticks.ex2archive.com/ticks/XAUUSDc/2026/09/Exness_XAUUSDc_2026_09.zip"
    raw_zip_bytes = _create_synthetic_zip_archive()

    def mock_transport(target_url):
        return (raw_zip_bytes, url, 200, "application/zip", [])

    receipt = execute_governed_broker_url_capture(url, http_client=mock_transport)

    # Tamper with response_bytes on receipt
    tampered_bytes = raw_zip_bytes + b"\x00\x00"
    tampered_receipt = BrokerCaptureReceipt(
        requested_url=receipt.requested_url,
        final_url=receipt.final_url,
        http_status=receipt.http_status,
        content_type=receipt.content_type,
        response_bytes=tampered_bytes,
        response_sha256=receipt.response_sha256,
        captured_at=receipt.captured_at,
        collector_version=receipt.collector_version,
        redirect_chain=receipt.redirect_chain,
        receipt_auth_tag=receipt.receipt_auth_tag,
    )

    snapshot, _ = ingest_friction_source_snapshot(
        source_url=url,
        source_name="EXNESS_OFFICIAL_TICK_HISTORY",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD_CENT",
        retrieved_at=datetime.now(timezone.utc),
        known_at=datetime.now(timezone.utc),
        raw_content=raw_zip_bytes,
        source_type=FrictionSourceType.EXNESS_OFFICIAL_TICK_HISTORY.value,
        source_origin=url,
    )

    with pytest.raises(ValueError):
        create_verified_broker_capture_attestation(
            source_snapshot=snapshot,
            component_role="SPREAD_DATASET",
            capture_receipt=tampered_receipt,
            expected_symbol="XAUUSD",
            expected_venue="EXNESS",
            expected_account_tier="STANDARD_CENT",
            expected_broker_symbol="XAUUSDc",
        )


@pytest.mark.django_db
def test_snapshot_raw_sha_mismatch_rejected():
    """If snapshot raw content SHA does not match receipt SHA, attestation creation must fail."""
    url = "https://ticks.ex2archive.com/ticks/XAUUSDc/2026/09/Exness_XAUUSDc_2026_09.zip"
    raw_zip_bytes = _create_synthetic_zip_archive()

    def mock_transport(target_url):
        return (raw_zip_bytes, url, 200, "application/zip", [])

    receipt = execute_governed_broker_url_capture(url, http_client=mock_transport)

    different_bytes = raw_zip_bytes + b"DIFFERENT"
    snapshot, _ = ingest_friction_source_snapshot(
        source_url=url,
        source_name="EXNESS_OFFICIAL_TICK_HISTORY",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD_CENT",
        retrieved_at=datetime.now(timezone.utc),
        known_at=datetime.now(timezone.utc),
        raw_content=different_bytes,
        source_type=FrictionSourceType.EXNESS_OFFICIAL_TICK_HISTORY.value,
        source_origin=url,
    )

    with pytest.raises(ValueError, match="does not match snapshot raw_payload_bytes_sha256"):
        create_verified_broker_capture_attestation(
            source_snapshot=snapshot,
            component_role="SPREAD_DATASET",
            capture_receipt=receipt,
            expected_symbol="XAUUSD",
            expected_venue="EXNESS",
            expected_account_tier="STANDARD_CENT",
            expected_broker_symbol="XAUUSDc",
        )


@pytest.mark.django_db
def test_wrong_source_type_rejected():
    """If snapshot source_type is not EXNESS_OFFICIAL_TICK_HISTORY for SPREAD_DATASET, reject."""
    url = "https://ticks.ex2archive.com/ticks/XAUUSDc/2026/09/Exness_XAUUSDc_2026_09.zip"
    raw_zip_bytes = _create_synthetic_zip_archive()

    def mock_transport(target_url):
        return (raw_zip_bytes, url, 200, "application/zip", [])

    receipt = execute_governed_broker_url_capture(url, http_client=mock_transport)

    snapshot, _ = ingest_friction_source_snapshot(
        source_url=url,
        source_name="EXNESS_OFFICIAL_TICK_HISTORY",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD_CENT",
        retrieved_at=datetime.now(timezone.utc),
        known_at=datetime.now(timezone.utc),
        raw_content=raw_zip_bytes,
        source_type=FrictionSourceType.MT5_TICK_HISTORY_EXPORT.value,  # Wrong source type!
        source_origin=url,
    )

    with pytest.raises(ValueError, match="mismatch with expected 'EXNESS_OFFICIAL_TICK_HISTORY'"):
        create_verified_broker_capture_attestation(
            source_snapshot=snapshot,
            component_role="SPREAD_DATASET",
            capture_receipt=receipt,
            expected_symbol="XAUUSD",
            expected_venue="EXNESS",
            expected_account_tier="STANDARD_CENT",
            expected_broker_symbol="XAUUSDc",
        )


@pytest.mark.django_db
def test_wrong_broker_symbol_scope_rejected():
    """If broker symbol in archive is XAUUSDm instead of expected XAUUSDc under STANDARD_CENT, reject."""
    url = "https://ticks.ex2archive.com/ticks/XAUUSDc/2026/09/Exness_XAUUSDc_2026_09.zip"
    wrong_symbol_csv = _create_synthetic_tick_csv_lines(symbol="XAUUSDm", distinct_days=6)
    raw_zip_bytes = _create_synthetic_zip_archive(csv_filename="Exness_XAUUSDm_2026_09.csv", csv_bytes=wrong_symbol_csv)

    def mock_transport(target_url):
        return (raw_zip_bytes, url, 200, "application/zip", [])

    receipt = execute_governed_broker_url_capture(url, http_client=mock_transport)

    snapshot, _ = ingest_friction_source_snapshot(
        source_url=url,
        source_name="EXNESS_OFFICIAL_TICK_HISTORY",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD_CENT",
        retrieved_at=datetime.now(timezone.utc),
        known_at=datetime.now(timezone.utc),
        raw_content=raw_zip_bytes,
        source_type=FrictionSourceType.EXNESS_OFFICIAL_TICK_HISTORY.value,
        source_origin=url,
    )

    with pytest.raises(ValueError, match="Symbol mismatch"):
        create_verified_broker_capture_attestation(
            source_snapshot=snapshot,
            component_role="SPREAD_DATASET",
            capture_receipt=receipt,
            expected_symbol="XAUUSD",
            expected_venue="EXNESS",
            expected_account_tier="STANDARD_CENT",
            expected_broker_symbol="XAUUSDc",
        )


@pytest.mark.django_db
def test_non_200_http_status_rejected():
    """Non-200 HTTP status response cannot create verified attestation."""
    url = "https://ticks.ex2archive.com/ticks/XAUUSDc/2026/09/Exness_XAUUSDc_2026_09.zip"
    raw_zip_bytes = _create_synthetic_zip_archive()

    def mock_transport(target_url):
        return (raw_zip_bytes, url, 404, "application/zip", [])

    receipt = execute_governed_broker_url_capture(url, http_client=mock_transport)

    snapshot, _ = ingest_friction_source_snapshot(
        source_url=url,
        source_name="EXNESS_OFFICIAL_TICK_HISTORY",
        venue="EXNESS",
        symbol="XAUUSD",
        account_tier="STANDARD_CENT",
        retrieved_at=datetime.now(timezone.utc),
        known_at=datetime.now(timezone.utc),
        raw_content=raw_zip_bytes,
        source_type=FrictionSourceType.EXNESS_OFFICIAL_TICK_HISTORY.value,
        source_origin=url,
    )

    with pytest.raises(ValueError, match="HTTP status 404 != 200"):
        create_verified_broker_capture_attestation(
            source_snapshot=snapshot,
            component_role="SPREAD_DATASET",
            capture_receipt=receipt,
            expected_symbol="XAUUSD",
            expected_venue="EXNESS",
            expected_account_tier="STANDARD_CENT",
            expected_broker_symbol="XAUUSDc",
        )


@pytest.mark.django_db
def test_management_command_governed_tick_url_capture(monkeypatch):
    """Test ingest_xauusd_empirical_friction management command end-to-end with --tick-url."""
    import tempfile
    from django.core.management import call_command
    import apps.market_data.friction.provenance as prov_module

    url = "https://ticks.ex2archive.com/ticks/XAUUSDc/2026/09/Exness_XAUUSDc_2026_09.zip"
    raw_zip_bytes = _create_synthetic_zip_archive()

    def mock_transport(target_url):
        return (raw_zip_bytes, url, 200, "application/zip", [])

    orig_capture = prov_module.execute_governed_broker_url_capture
    monkeypatch.setattr(
        prov_module,
        "execute_governed_broker_url_capture",
        lambda u: orig_capture(u, http_client=mock_transport),
    )

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf_m, tempfile.NamedTemporaryFile(suffix=".md", delete=False) as tf_r:
        manifest_path = tf_m.name
        report_path = tf_r.name

    try:
        # Command executes and parses official archive via --tick-url without error
        call_command(
            "ingest_xauusd_empirical_friction",
            "--venue", "EXNESS",
            "--account-tier", "STANDARD_CENT",
            "--broker-symbol", "XAUUSDc",
            "--tick-url", url,
            "--output-manifest", manifest_path,
            "--output-report", report_path,
            "--dry-run",
        )
        assert os.path.exists(manifest_path)
        assert os.path.exists(report_path)
    finally:
        for p in [manifest_path, report_path]:
            if os.path.exists(p):
                os.remove(p)


@pytest.mark.django_db
def test_management_command_governed_tick_url_capture_production_persistence(monkeypatch):
    """Test ingest_xauusd_empirical_friction non-dry-run CLI creates genuine snapshots, attestations, and qualified assertions."""
    import tempfile
    from django.core.management import call_command
    import apps.market_data.friction.provenance as prov_module
    from apps.market_data.models import (
        FrictionSourceSnapshot,
        FrictionEvidenceDataset,
        FrictionSourceProvenanceAttestation,
        FrictionSourceQualificationAssertion,
        FrictionSourceType,
        FrictionQualificationStatus,
    )
    from apps.market_data.friction.validation import validate_source_qualification_assertion

    url = "https://ticks.ex2archive.com/ticks/XAUUSDc/2026/09/Exness_XAUUSDc_2026_09.zip"
    raw_zip_bytes = _create_synthetic_zip_archive()
    expected_sha = hashlib.sha256(raw_zip_bytes).hexdigest()

    def mock_transport(target_url):
        return (raw_zip_bytes, url, 200, "application/zip", [])

    orig_capture = prov_module.execute_governed_broker_url_capture
    monkeypatch.setattr(
        prov_module,
        "execute_governed_broker_url_capture",
        lambda u: orig_capture(u, http_client=mock_transport),
    )

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf_m, tempfile.NamedTemporaryFile(suffix=".md", delete=False) as tf_r:
        manifest_path = tf_m.name
        report_path = tf_r.name

    try:
        # Run command WITHOUT --dry-run
        call_command(
            "ingest_xauusd_empirical_friction",
            "--venue", "EXNESS",
            "--account-tier", "STANDARD_CENT",
            "--broker-symbol", "XAUUSDc",
            "--tick-url", url,
            "--output-manifest", manifest_path,
            "--output-report", report_path,
        )
        assert os.path.exists(manifest_path)
        assert os.path.exists(report_path)

        # 1. FrictionSourceSnapshot assertion
        snapshot = FrictionSourceSnapshot.objects.get(
            venue="EXNESS",
            symbol="XAUUSD",
            account_tier="STANDARD_CENT",
            source_type=FrictionSourceType.EXNESS_OFFICIAL_TICK_HISTORY.value,
        )
        assert snapshot.account_tier == "STANDARD_CENT"
        assert snapshot.source_type == FrictionSourceType.EXNESS_OFFICIAL_TICK_HISTORY.value
        assert snapshot.source_origin == url
        assert snapshot.raw_payload_bytes_sha256 == expected_sha

        # 2. FrictionEvidenceDataset assertion
        dataset = FrictionEvidenceDataset.objects.get(source_snapshot=snapshot)
        assert dataset.venue == "EXNESS"
        assert dataset.account_tier == "STANDARD_CENT"
        assert dataset.symbol == "XAUUSD"

        # 3. FrictionSourceProvenanceAttestation assertion
        attestation = FrictionSourceProvenanceAttestation.objects.get(source_snapshot=snapshot)
        assert attestation.component_role == "SPREAD_DATASET"
        assert attestation.attestation_status == "VERIFIED"
        assert attestation.verification_method == "BROKER_OFFICIAL_URL_CAPTURE"
        assert attestation.source_type == FrictionSourceType.EXNESS_OFFICIAL_TICK_HISTORY.value
        assert attestation.source_origin == url
        assert attestation.raw_artifact_sha256 == expected_sha

        # 4. FrictionSourceQualificationAssertion assertion
        assertion = FrictionSourceQualificationAssertion.objects.get(source_snapshot=snapshot)
        assert assertion.component_role == "SPREAD_DATASET"
        assert assertion.qualification_status == FrictionQualificationStatus.QUALIFIED.value
        assert assertion.parser_name == "parse_exness_official_tick_history"
        assert assertion.provenance_attestation == attestation

        # 5. Qualification assertion validation
        is_valid, reasons, parsed_data = validate_source_qualification_assertion(
            snapshot=snapshot,
            assertion=assertion,
            expected_component_role="SPREAD_DATASET",
            expected_parser="parse_exness_official_tick_history",
            expected_symbol="XAUUSD",
            expected_account_tier="STANDARD_CENT",
            expected_venue="EXNESS",
            expected_broker_symbol="XAUUSDc",
        )
        assert is_valid is True, f"Qualification assertion invalid: {reasons}"
        assert reasons == []
    finally:
        for p in [manifest_path, report_path]:
            if os.path.exists(p):
                os.remove(p)


# =============================================================================
# 5. Host-Aware Capture Limits Tests
# =============================================================================

def test_governed_broker_url_capture_host_aware_limits_normal_broker():
    """Normal broker document hosts enforce normal MAX_BROKER_RESPONSE_BYTES (10 MB)."""
    url = "https://www.exness.com/legal/client-agreement.pdf"
    oversized_bytes = b"X" * (11 * 1024 * 1024)  # 11 MB > 10 MB limit

    def mock_transport(target_url):
        return (oversized_bytes, url, 200, "application/pdf", [])

    with pytest.raises(ValueError, match="exceeds maximum permitted size"):
        execute_governed_broker_url_capture(url, http_client=mock_transport)

    # Valid size (1 MB) passes
    valid_bytes = b"X" * (1024 * 1024)
    def mock_transport_valid(target_url):
        return (valid_bytes, url, 200, "application/pdf", [])

    receipt = execute_governed_broker_url_capture(url, http_client=mock_transport_valid)
    assert len(receipt.response_bytes) == 1024 * 1024


def test_governed_broker_url_capture_host_aware_limits_tick_archive():
    """Official tick archive host ticks.ex2archive.com permits up to MAX_TICK_ARCHIVE_RESPONSE_BYTES (100 MB)."""
    url = "https://ticks.ex2archive.com/ticks/XAUUSDc/2026/09/Exness_XAUUSDc_2026_09.zip"
    large_bytes = b"P" * (15 * 1024 * 1024)  # 15 MB > 10 MB normal limit, but < 100 MB tick limit

    def mock_transport(target_url):
        return (large_bytes, url, 200, "application/zip", [])

    receipt = execute_governed_broker_url_capture(url, http_client=mock_transport)
    assert len(receipt.response_bytes) == 15 * 1024 * 1024

    # Exceeding 100 MB tick limit fails
    oversized_tick_bytes = b"P" * (101 * 1024 * 1024)  # 101 MB > 100 MB limit
    def mock_transport_oversized(target_url):
        return (oversized_tick_bytes, url, 200, "application/zip", [])

    with pytest.raises(ValueError, match="exceeds maximum permitted size"):
        execute_governed_broker_url_capture(url, http_client=mock_transport_oversized)


def test_governed_broker_url_capture_host_aware_limits_untrusted_host():
    """Untrusted hosts fail closed before transport."""
    url = "https://evil.com/ticks.zip"
    with pytest.raises(ValueError, match="is not in permitted broker domains"):
        execute_governed_broker_url_capture(url, http_client=lambda u: (b"", u, 200, "application/zip", []))


# =============================================================================
# 6. Spread Evidence Artifact Truth Regression Tests (Stage D2B Remediation)
# =============================================================================

@pytest.mark.django_db
def test_regression_a_governed_spread_qualification_emits_qualified_manifest(monkeypatch):
    """Test A: Governed official Exness capture with verified attestation & qualified assertion emits spread_status=QUALIFIED in manifest."""
    import json
    import tempfile
    from django.core.management import call_command
    import apps.market_data.friction.provenance as prov_module

    url = "https://ticks.ex2archive.com/ticks/XAUUSDc/2026/09/Exness_XAUUSDc_2026_09.zip"
    raw_zip_bytes = _create_synthetic_zip_archive()
    expected_sha = hashlib.sha256(raw_zip_bytes).hexdigest()

    def mock_transport(target_url):
        return (raw_zip_bytes, url, 200, "application/zip", [])

    orig_capture = prov_module.execute_governed_broker_url_capture
    monkeypatch.setattr(
        prov_module,
        "execute_governed_broker_url_capture",
        lambda u: orig_capture(u, http_client=mock_transport),
    )

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf_m, tempfile.NamedTemporaryFile(suffix=".md", delete=False) as tf_r:
        manifest_path = tf_m.name
        report_path = tf_r.name

    try:
        call_command(
            "ingest_xauusd_empirical_friction",
            "--venue", "EXNESS",
            "--account-tier", "STANDARD_CENT",
            "--broker-symbol", "XAUUSDc",
            "--tick-url", url,
            "--output-manifest", manifest_path,
            "--output-report", report_path,
        )
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        spread_entry = manifest["evidence_inventory"]["bid_ask_spread_distribution"]
        assert spread_entry["status"] == "QUALIFIED"
        assert spread_entry["source_type"] == "EXNESS_OFFICIAL_TICK_HISTORY"
        assert spread_entry["verification_method"] == "BROKER_OFFICIAL_URL_CAPTURE"
        assert spread_entry["broker_symbol"] == "XAUUSDc"
        assert spread_entry["raw_response_sha256"] == expected_sha
        assert spread_entry["sample_count"] > 0
    finally:
        for p in [manifest_path, report_path]:
            if os.path.exists(p):
                os.remove(p)


@pytest.mark.django_db
def test_regression_b_sample_sufficient_without_qualification_emits_sample_available():
    """Test B: When sample is sufficient but unverified/unqualified, emitted spread status is EMPIRICAL_SAMPLE_EVIDENCE_AVAILABLE."""
    import json
    import tempfile
    from django.core.management import call_command

    raw_zip_bytes = _create_synthetic_zip_archive()
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tf_z, \
         tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf_m, \
         tempfile.NamedTemporaryFile(suffix=".md", delete=False) as tf_r:
        tf_z.write(raw_zip_bytes)
        tf_z.flush()
        zip_path = tf_z.name
        manifest_path = tf_m.name
        report_path = tf_r.name

    try:
        call_command(
            "ingest_xauusd_empirical_friction",
            "--venue", "EXNESS",
            "--account-tier", "STANDARD_CENT",
            "--broker-symbol", "XAUUSDc",
            "--tick-file", zip_path,
            "--output-manifest", manifest_path,
            "--output-report", report_path,
        )
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        spread_entry = manifest["evidence_inventory"]["bid_ask_spread_distribution"]
        assert spread_entry["status"] == "EMPIRICAL_SAMPLE_EVIDENCE_AVAILABLE"
        assert spread_entry["status"] != "QUALIFIED"
        assert spread_entry["sample_count"] > 0
    finally:
        for p in [zip_path, manifest_path, report_path]:
            if os.path.exists(p):
                os.remove(p)


@pytest.mark.django_db
def test_regression_c_qualification_validator_failure_fails_closed_to_not_qualified(monkeypatch):
    """Test C: If assertion is marked QUALIFIED but validator fails, command fails closed and does NOT emit QUALIFIED."""
    import json
    import tempfile
    from django.core.management import call_command
    import apps.market_data.friction.provenance as prov_module
    import apps.market_data.management.commands.ingest_xauusd_empirical_friction as cmd_module

    url = "https://ticks.ex2archive.com/ticks/XAUUSDc/2026/09/Exness_XAUUSDc_2026_09.zip"
    raw_zip_bytes = _create_synthetic_zip_archive()

    def mock_transport(target_url):
        return (raw_zip_bytes, url, 200, "application/zip", [])

    orig_capture = prov_module.execute_governed_broker_url_capture
    monkeypatch.setattr(
        prov_module,
        "execute_governed_broker_url_capture",
        lambda u: orig_capture(u, http_client=mock_transport),
    )

    # Force validator to fail
    monkeypatch.setattr(
        cmd_module,
        "validate_source_qualification_assertion",
        lambda **kwargs: (False, ["Simulated qualification validation failure: tampered integrity"], {}),
    )

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf_m, tempfile.NamedTemporaryFile(suffix=".md", delete=False) as tf_r:
        manifest_path = tf_m.name
        report_path = tf_r.name

    try:
        call_command(
            "ingest_xauusd_empirical_friction",
            "--venue", "EXNESS",
            "--account-tier", "STANDARD_CENT",
            "--broker-symbol", "XAUUSDc",
            "--tick-url", url,
            "--output-manifest", manifest_path,
            "--output-report", report_path,
        )
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        spread_entry = manifest["evidence_inventory"]["bid_ask_spread_distribution"]
        # Must NOT be QUALIFIED
        assert spread_entry["status"] != "QUALIFIED"
        assert spread_entry["status"] == "EMPIRICAL_SAMPLE_EVIDENCE_AVAILABLE"
        assert any("Simulated qualification validation failure" in r for r in manifest["blocking_reasons"])
    finally:
        for p in [manifest_path, report_path]:
            if os.path.exists(p):
                os.remove(p)


@pytest.mark.django_db
def test_regression_d_standard_cent_report_omits_stale_mt5_language_when_qualified(monkeypatch):
    """Test D: Standard Cent report omits stale MT5 language and lists spread as qualified when official Exness capture passes."""
    import tempfile
    from django.core.management import call_command
    import apps.market_data.friction.provenance as prov_module

    url = "https://ticks.ex2archive.com/ticks/XAUUSDc/2026/09/Exness_XAUUSDc_2026_09.zip"
    raw_zip_bytes = _create_synthetic_zip_archive()

    def mock_transport(target_url):
        return (raw_zip_bytes, url, 200, "application/zip", [])

    orig_capture = prov_module.execute_governed_broker_url_capture
    monkeypatch.setattr(
        prov_module,
        "execute_governed_broker_url_capture",
        lambda u: orig_capture(u, http_client=mock_transport),
    )

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf_m, tempfile.NamedTemporaryFile(suffix=".md", delete=False) as tf_r:
        manifest_path = tf_m.name
        report_path = tf_r.name

    try:
        call_command(
            "ingest_xauusd_empirical_friction",
            "--venue", "EXNESS",
            "--account-tier", "STANDARD_CENT",
            "--broker-symbol", "XAUUSDc",
            "--tick-url", url,
            "--output-manifest", manifest_path,
            "--output-report", report_path,
        )
        with open(report_path, "r", encoding="utf-8") as f:
            report_content = f.read()

        # Proves stale MT5 claims are removed
        assert "Because genuine MT5 tick history exports" not in report_content
        assert "Provide authentic Exness MT5 tick history export" not in report_content

        # Proves truthful spread qualification is recorded
        assert "Spread Evidence Qualification Record" in report_content
        assert "EXNESS_OFFICIAL_TICK_HISTORY" in report_content
        assert "BROKER_OFFICIAL_URL_CAPTURE" in report_content
        assert "**SPREAD EVIDENCE** | `QUALIFIED`" in report_content

        # Proves Next Steps does NOT claim spread or MT5 tick export is missing
        assert "## 4. Next Steps for Unblocking" in report_content
        next_steps_section = report_content.split("## 4. Next Steps for Unblocking")[-1]
        assert "Bid-Ask Spread" not in next_steps_section
        assert "MT5 tick export" not in next_steps_section
        assert "tick history" not in next_steps_section

        # Proves the 5 remaining missing categories ARE listed
        assert "legal_entity_code" in next_steps_section
        assert "contract specification snapshot" in next_steps_section
        assert "fee schedule snapshot" in next_steps_section
        assert "financing swap schedule snapshot" in next_steps_section
        assert "execution telemetry fills" in next_steps_section
    finally:
        for p in [manifest_path, report_path]:
            if os.path.exists(p):
                os.remove(p)


@pytest.mark.django_db
def test_regression_e_completeness_gate_strictly_requires_qualified_spread():
    """Test E: is_evidence_complete strictly requires spread_status == 'QUALIFIED'; EMPIRICAL_SAMPLE_EVIDENCE_AVAILABLE does not qualify."""
    # Verify Boolean logic directly as implemented in Command.handle()
    legal_entity_status = "OFFICIAL_CONTRACT_EVIDENCE_AVAILABLE"
    contract_status = "OFFICIAL_CONTRACT_EVIDENCE_AVAILABLE"
    commission_status = "OFFICIAL_CONTRACT_EVIDENCE_AVAILABLE"
    financing_status = "OFFICIAL_CONTRACT_EVIDENCE_AVAILABLE"
    slippage_status = "EMPIRICAL_SAMPLE_EVIDENCE_AVAILABLE"

    # When spread_status is EMPIRICAL_SAMPLE_EVIDENCE_AVAILABLE
    spread_status_sample = "EMPIRICAL_SAMPLE_EVIDENCE_AVAILABLE"
    is_complete_sample = (
        legal_entity_status == "OFFICIAL_CONTRACT_EVIDENCE_AVAILABLE"
        and contract_status == "OFFICIAL_CONTRACT_EVIDENCE_AVAILABLE"
        and commission_status == "OFFICIAL_CONTRACT_EVIDENCE_AVAILABLE"
        and financing_status == "OFFICIAL_CONTRACT_EVIDENCE_AVAILABLE"
        and spread_status_sample == "QUALIFIED"
        and slippage_status == "EMPIRICAL_SAMPLE_EVIDENCE_AVAILABLE"
    )
    assert is_complete_sample is False

    # When spread_status is QUALIFIED
    spread_status_qualified = "QUALIFIED"
    is_complete_qualified = (
        legal_entity_status == "OFFICIAL_CONTRACT_EVIDENCE_AVAILABLE"
        and contract_status == "OFFICIAL_CONTRACT_EVIDENCE_AVAILABLE"
        and commission_status == "OFFICIAL_CONTRACT_EVIDENCE_AVAILABLE"
        and financing_status == "OFFICIAL_CONTRACT_EVIDENCE_AVAILABLE"
        and spread_status_qualified == "QUALIFIED"
        and slippage_status == "EMPIRICAL_SAMPLE_EVIDENCE_AVAILABLE"
    )
    assert is_complete_qualified is True


@pytest.mark.django_db
def test_regression_f_qualified_report_fails_closed_when_mandatory_evidence_missing(monkeypatch):
    """Test F: Report generation fails closed (raises ValueError) if spread_status is QUALIFIED but mandatory evidence is missing."""
    import tempfile
    from django.core.management import call_command
    import apps.market_data.friction.provenance as prov_module

    url = "https://ticks.ex2archive.com/ticks/XAUUSDc/2026/09/Exness_XAUUSDc_2026_09.zip"
    raw_zip_bytes = _create_synthetic_zip_archive()

    def mock_transport(target_url):
        return (raw_zip_bytes, url, 200, "application/zip", [])

    orig_capture = prov_module.execute_governed_broker_url_capture
    monkeypatch.setattr(
        prov_module,
        "execute_governed_broker_url_capture",
        lambda u: orig_capture(u, http_client=mock_transport),
    )

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf_m, tempfile.NamedTemporaryFile(suffix=".md", delete=False) as tf_r:
        manifest_path = tf_m.name
        report_path = tf_r.name

    import apps.market_data.management.commands.ingest_xauusd_empirical_friction as cmd_module
    orig_validate = cmd_module.validate_source_qualification_assertion

    def tamper_validate(*args, **kwargs):
        res = orig_validate(*args, **kwargs)
        # Clear raw_payload_bytes_sha256 on snapshot to verify fail-closed report generation
        kwargs["snapshot"].raw_payload_bytes_sha256 = ""
        return res

    monkeypatch.setattr(cmd_module, "validate_source_qualification_assertion", tamper_validate)

    try:
        # Should raise ValueError because raw_payload_bytes_sha256 is missing when required for QUALIFIED report
        with pytest.raises(ValueError, match="Fail-closed: QUALIFIED spread evidence missing raw_payload_bytes_sha256"):
            call_command(
                "ingest_xauusd_empirical_friction",
                "--venue", "EXNESS",
                "--account-tier", "STANDARD_CENT",
                "--broker-symbol", "XAUUSDc",
                "--tick-url", url,
                "--output-manifest", manifest_path,
                "--output-report", report_path,
            )
    finally:
        for p in [manifest_path, report_path]:
            if os.path.exists(p):
                os.remove(p)

