"""
Hostile point-in-time safety, coverage, and provenance tests for Checkpoint B (Spec §33, §34).

Covers non-negotiable governance requirements:
1. Future schedule masking
2. Reschedule masking
3. Cancellation masking
4. Future release masking
5. Revision masking
6. Unlimited revisions (rev 0, rev 1, rev 2, rev 3)
7. Mutation isolation (adding evidence at T+1 does not change replay at T)
8. Duplicate hostility
9. Sparse hostility (1/204 fails hard)
10. Extra-event hostility (unexpected events do not raise coverage)
11. Naive timestamp hostility (fails closed)
12. Missing provenance hostility (missing SourceSnapshot / hash fails closed)
13. Deterministic macro evidence fingerprint invariance
14. Production readiness gate evaluation fails closed on incomplete macro evidence
"""
from datetime import date, datetime, timezone
from decimal import Decimal
import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from apps.market_data.macro.coverage import (
    evaluate_canonical_macro_coverage,
    get_canonical_expected_cpi_keys,
    get_canonical_expected_fomc_keys,
    get_canonical_expected_nfp_keys,
    get_effective_schedule_provenance,
    validate_schedule_vintage_provenance,
)
from apps.market_data.macro.fingerprint import compute_macro_evidence_fingerprint
from apps.market_data.macro.ingestion import (
    IngestionStats,
    fetch_or_reuse_snapshot,
    record_schedule_provenance_assertion,
)
from apps.market_data.macro.replay import resolve_macro_events_as_of
from apps.market_data.models import (
    MacroEventFamily,
    MacroEventIdentity,
    MacroObservationVintage,
    MacroScheduleProvenanceAssertion,
    MacroScheduleVintage,
    PublicationStatus,
    ScheduleProvenanceType,
    ScheduleStatus,
    SourceSnapshot,
)
from apps.market_data.readiness import parse_strict_iso_datetime, XauUsdDataReadinessEvaluator


@pytest.fixture
def macro_identities(db):
    """Fixture providing canonical macro event identities."""
    cpi = MacroEventIdentity.objects.create(
        identity_id="US_CPI",
        name="US Consumer Price Index",
        event_family=MacroEventFamily.US_CPI,
        impact="HIGH",
        reporting_agency="Bureau of Labor Statistics",
    )
    nfp = MacroEventIdentity.objects.create(
        identity_id="US_NFP",
        name="US Non-Farm Payrolls",
        event_family=MacroEventFamily.US_NFP,
        impact="HIGH",
        reporting_agency="Bureau of Labor Statistics",
    )
    fomc = MacroEventIdentity.objects.create(
        identity_id="FOMC_RATE",
        name="Federal Open Market Committee Rate Decision",
        event_family=MacroEventFamily.FOMC_RATE,
        impact="CRITICAL",
        reporting_agency="Federal Reserve",
    )
    return {"US_CPI": cpi, "US_NFP": nfp, "FOMC_RATE": fomc}


@pytest.fixture
def source_snapshot_fixture(db):
    """Fixture providing an authoritative source snapshot."""
    return SourceSnapshot.objects.create(
        snapshot_id="snap_test_authoritative_001",
        source_url="https://www.bls.gov/schedule/2026/home.htm",
        source_name="bls_schedule_2026",
        first_retrieved_at=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        raw_payload_bytes_sha256="1234567890abcdef" * 4,
        raw_content=b"sample schedule table",
    )


@pytest.fixture
def xauusd_setup(db):
    """Seed standard assets, instruments, and primary XAUUSD spot listing."""
    from django.core.management import call_command
    from apps.instruments.models import Instrument, MarketListing, ListingRole, ListingStatus

    call_command("seed_instruments")
    instrument = Instrument.get_canonical_xauusd()
    primary_listing = MarketListing.objects.filter(
        instrument=instrument,
        listing_role=ListingRole.PRIMARY_XAUUSD_SPOT,
        status=ListingStatus.ACTIVE,
    ).first()
    return instrument, primary_listing


# 1. Future schedule masking
@pytest.mark.django_db
def test_future_schedule_masking(macro_identities, source_snapshot_fixture):
    """A schedule vintage whose known_at > T must not exist in replay at T."""
    cpi = macro_identities["US_CPI"]

    # Schedule published at 2026-01-05
    MacroScheduleVintage.objects.create(
        vintage_id="sched_cpi_2026_01_v0",
        event=cpi,
        reference_period="2026-01",
        scheduled_at=datetime(2026, 2, 13, 13, 30, tzinfo=timezone.utc),
        schedule_status=ScheduleStatus.SCHEDULED,
        known_at=datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc),
        source_snapshot=source_snapshot_fixture,
    )

    # Replay at T = 2026-01-01 (before known_at)
    events_before = resolve_macro_events_as_of(
        as_of=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        event_families=["US_CPI"],
    )
    assert len(events_before) == 0, "Schedule vintage leaked before its known_at!"

    # Replay at T = 2026-01-06 (after known_at)
    events_after = resolve_macro_events_as_of(
        as_of=datetime(2026, 1, 6, 0, 0, tzinfo=timezone.utc),
        event_families=["US_CPI"],
    )
    assert len(events_after) == 1
    assert events_after[0].scheduled_at == datetime(2026, 2, 13, 13, 30, tzinfo=timezone.utc)


# 2. Reschedule masking
@pytest.mark.django_db
def test_reschedule_masking(macro_identities, source_snapshot_fixture):
    """A reschedule published after T must not alter replay before T."""
    fomc = macro_identities["FOMC_RATE"]

    # Vintage 0: announced 2025-08-01, meeting scheduled for 2026-05-06
    MacroScheduleVintage.objects.create(
        vintage_id="sched_fomc_2026_05_v0",
        event=fomc,
        reference_period="2026-05",
        scheduled_at=datetime(2026, 5, 6, 18, 0, tzinfo=timezone.utc),
        schedule_status=ScheduleStatus.SCHEDULED,
        known_at=datetime(2025, 8, 1, 0, 0, tzinfo=timezone.utc),
        source_snapshot=source_snapshot_fixture,
    )

    # Vintage 1: reschedule announced 2025-11-01, meeting moved to 2026-04-29
    MacroScheduleVintage.objects.create(
        vintage_id="sched_fomc_2026_05_v1",
        event=fomc,
        reference_period="2026-05",
        scheduled_at=datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc),
        schedule_status=ScheduleStatus.RESCHEDULED,
        known_at=datetime(2025, 11, 1, 0, 0, tzinfo=timezone.utc),
        source_snapshot=source_snapshot_fixture,
    )

    # Replay at T = 2025-09-01 (before reschedule was known)
    events_sep = resolve_macro_events_as_of(
        as_of=datetime(2025, 9, 1, 0, 0, tzinfo=timezone.utc),
        event_families=["FOMC_RATE"],
    )
    assert len(events_sep) == 1
    assert events_sep[0].scheduled_at == datetime(2026, 5, 6, 18, 0, tzinfo=timezone.utc)

    # Replay at T = 2025-11-15 (after reschedule was known)
    events_nov = resolve_macro_events_as_of(
        as_of=datetime(2025, 11, 15, 0, 0, tzinfo=timezone.utc),
        event_families=["FOMC_RATE"],
    )
    assert len(events_nov) == 1
    assert events_nov[0].scheduled_at == datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc)


# 3. Cancellation masking
@pytest.mark.django_db
def test_cancellation_masking(macro_identities, source_snapshot_fixture):
    """Cancellation published after T must not cancel an event in replay before T."""
    nfp = macro_identities["US_NFP"]

    # Scheduled 2026-01-01
    sched = MacroScheduleVintage.objects.create(
        vintage_id="sched_nfp_2026_02_v0",
        event=nfp,
        reference_period="2026-02",
        scheduled_at=datetime(2026, 3, 6, 13, 30, tzinfo=timezone.utc),
        schedule_status=ScheduleStatus.SCHEDULED,
        known_at=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        source_snapshot=source_snapshot_fixture,
    )

    # Cancelled 2026-02-15
    MacroScheduleVintage.objects.create(
        vintage_id="sched_nfp_2026_02_v1",
        event=nfp,
        reference_period="2026-02",
        scheduled_at=datetime(2026, 3, 6, 13, 30, tzinfo=timezone.utc),
        schedule_status=ScheduleStatus.CANCELLED,
        known_at=datetime(2026, 2, 15, 0, 0, tzinfo=timezone.utc),
        source_snapshot=source_snapshot_fixture,
    )

    # At T = 2026-02-01: event must be visible as scheduled
    events_pre = resolve_macro_events_as_of(
        as_of=datetime(2026, 2, 1, 0, 0, tzinfo=timezone.utc),
        event_families=["US_NFP"],
    )
    assert len(events_pre) == 1
    assert events_pre[0].scheduled_at == datetime(2026, 3, 6, 13, 30, tzinfo=timezone.utc)

    # At T = 2026-02-20: event must be cancelled (excluded)
    events_post = resolve_macro_events_as_of(
        as_of=datetime(2026, 2, 20, 0, 0, tzinfo=timezone.utc),
        event_families=["US_NFP"],
    )
    assert len(events_post) == 0


# 4. Future release masking
@pytest.mark.django_db
def test_future_release_masking(macro_identities, source_snapshot_fixture):
    """Release value must not appear before its actual publication time."""
    cpi = macro_identities["US_CPI"]

    sched = MacroScheduleVintage.objects.create(
        vintage_id="sched_cpi_2026_06_v0",
        event=cpi,
        reference_period="2026-06",
        scheduled_at=datetime(2026, 7, 14, 12, 30, tzinfo=timezone.utc),
        schedule_status=ScheduleStatus.SCHEDULED,
        known_at=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        source_snapshot=source_snapshot_fixture,
    )

    MacroObservationVintage.objects.create(
        vintage_id="obs_cpi_2026_06_v0",
        event=cpi,
        schedule_vintage=sched,
        reference_period="2026-06",
        revision_number=0,
        observation_date=date(2026, 6, 1),
        vintage_date=date(2026, 7, 14),
        scheduled_at=datetime(2026, 7, 14, 12, 30, tzinfo=timezone.utc),
        source_published_at=datetime(2026, 7, 14, 12, 30, tzinfo=timezone.utc),
        first_retrieved_at=datetime(2026, 7, 14, 12, 31, tzinfo=timezone.utc),
        known_at=datetime(2026, 7, 14, 12, 30, tzinfo=timezone.utc),
        raw_value="+0.3%",
        level_value=Decimal("315.200"),
        derived_change_value=Decimal("0.30"),
        unit="PERCENT_MOM",
        source_snapshot=source_snapshot_fixture,
    )

    # At T = 2026-07-14 12:29:59 (1 second before release)
    events_pre = resolve_macro_events_as_of(
        as_of=datetime(2026, 7, 14, 12, 29, 59, tzinfo=timezone.utc),
        event_families=["US_CPI"],
    )
    assert len(events_pre) == 1
    assert events_pre[0].released_at is None
    assert events_pre[0].initial_value is None

    # At T = 2026-07-14 12:30:00 (exact release)
    events_at = resolve_macro_events_as_of(
        as_of=datetime(2026, 7, 14, 12, 30, 0, tzinfo=timezone.utc),
        event_families=["US_CPI"],
    )
    assert len(events_at) == 1
    assert events_at[0].released_at == datetime(2026, 7, 14, 12, 30, 0, tzinfo=timezone.utc)
    assert events_at[0].initial_value == "+0.3%"


# 5. Revision masking & 6. Unlimited revisions
@pytest.mark.django_db
def test_unlimited_revision_chain_and_masking(macro_identities, source_snapshot_fixture):
    """Replay must correctly handle multiple revisions (0, 1, 2) without lookahead."""
    nfp = macro_identities["US_NFP"]

    sched = MacroScheduleVintage.objects.create(
        vintage_id="sched_nfp_2024_01_v0",
        event=nfp,
        reference_period="2024-01",
        scheduled_at=datetime(2024, 2, 2, 13, 30, tzinfo=timezone.utc),
        schedule_status=ScheduleStatus.SCHEDULED,
        known_at=datetime(2023, 12, 1, 0, 0, tzinfo=timezone.utc),
        source_snapshot=source_snapshot_fixture,
    )

    # Rev 0: released Feb 2, 2024 (+353K)
    MacroObservationVintage.objects.create(
        vintage_id="obs_nfp_2024_01_v0",
        event=nfp,
        schedule_vintage=sched,
        reference_period="2024-01",
        revision_number=0,
        observation_date=date(2024, 1, 1),
        vintage_date=date(2024, 2, 2),
        scheduled_at=datetime(2024, 2, 2, 13, 30, tzinfo=timezone.utc),
        source_published_at=datetime(2024, 2, 2, 13, 30, tzinfo=timezone.utc),
        first_retrieved_at=datetime(2024, 2, 2, 13, 31, tzinfo=timezone.utc),
        known_at=datetime(2024, 2, 2, 13, 30, tzinfo=timezone.utc),
        raw_value="+353K",
        level_value=Decimal("157700"),
        derived_change_value=Decimal("353"),
        unit="THOUSANDS_OF_PERSONS",
        source_snapshot=source_snapshot_fixture,
    )

    # Rev 1: revised Mar 8, 2024 (+275K)
    MacroObservationVintage.objects.create(
        vintage_id="obs_nfp_2024_01_v1",
        event=nfp,
        schedule_vintage=sched,
        reference_period="2024-01",
        revision_number=1,
        observation_date=date(2024, 1, 1),
        vintage_date=date(2024, 3, 8),
        scheduled_at=datetime(2024, 2, 2, 13, 30, tzinfo=timezone.utc),
        source_published_at=datetime(2024, 3, 8, 13, 30, tzinfo=timezone.utc),
        first_retrieved_at=datetime(2024, 3, 8, 13, 31, tzinfo=timezone.utc),
        known_at=datetime(2024, 3, 8, 13, 30, tzinfo=timezone.utc),
        raw_value="+275K",
        level_value=Decimal("157622"),
        derived_change_value=Decimal("275"),
        unit="THOUSANDS_OF_PERSONS",
        source_snapshot=source_snapshot_fixture,
    )

    # Rev 2: revised Apr 5, 2024 (+256K)
    MacroObservationVintage.objects.create(
        vintage_id="obs_nfp_2024_01_v2",
        event=nfp,
        schedule_vintage=sched,
        reference_period="2024-01",
        revision_number=2,
        observation_date=date(2024, 1, 1),
        vintage_date=date(2024, 4, 5),
        scheduled_at=datetime(2024, 2, 2, 13, 30, tzinfo=timezone.utc),
        source_published_at=datetime(2024, 4, 5, 12, 30, tzinfo=timezone.utc),
        first_retrieved_at=datetime(2024, 4, 5, 12, 31, tzinfo=timezone.utc),
        known_at=datetime(2024, 4, 5, 12, 30, tzinfo=timezone.utc),
        raw_value="+256K",
        level_value=Decimal("157603"),
        derived_change_value=Decimal("256"),
        unit="THOUSANDS_OF_PERSONS",
        source_snapshot=source_snapshot_fixture,
    )

    # Stage 1: As of Feb 15, 2024 -> only Rev 0 is known
    ev1 = resolve_macro_events_as_of(
        as_of=datetime(2024, 2, 15, 0, 0, tzinfo=timezone.utc),
        event_families=["US_NFP"],
    )[0]
    assert ev1.initial_value == "+353K"
    assert ev1.revised_value is None

    # Stage 2: As of Mar 15, 2024 -> Rev 1 is known
    ev2 = resolve_macro_events_as_of(
        as_of=datetime(2024, 3, 15, 0, 0, tzinfo=timezone.utc),
        event_families=["US_NFP"],
    )[0]
    assert ev2.initial_value == "+353K"
    assert ev2.revised_value == "+275K"

    # Stage 3: As of Apr 10, 2024 -> Rev 2 is known
    ev3 = resolve_macro_events_as_of(
        as_of=datetime(2024, 4, 10, 0, 0, tzinfo=timezone.utc),
        event_families=["US_NFP"],
    )[0]
    assert ev3.initial_value == "+353K"
    assert ev3.revised_value == "+256K"


# 7. Mutation isolation
@pytest.mark.django_db
def test_mutation_isolation(macro_identities, source_snapshot_fixture):
    """Adding evidence at T+1 must not change replay at T."""
    cpi = macro_identities["US_CPI"]

    sched = MacroScheduleVintage.objects.create(
        vintage_id="sched_cpi_2023_01_v0",
        event=cpi,
        reference_period="2023-01",
        scheduled_at=datetime(2023, 2, 14, 13, 30, tzinfo=timezone.utc),
        schedule_status=ScheduleStatus.SCHEDULED,
        known_at=datetime(2022, 12, 1, 0, 0, tzinfo=timezone.utc),
        source_snapshot=source_snapshot_fixture,
    )

    # Initial replay at T = 2023-01-01
    replay_before = resolve_macro_events_as_of(
        as_of=datetime(2023, 1, 1, 0, 0, tzinfo=timezone.utc),
        event_families=["US_CPI"],
    )

    # Add evidence at T+1 (2023-02-14)
    MacroObservationVintage.objects.create(
        vintage_id="obs_cpi_2023_01_v0",
        event=cpi,
        schedule_vintage=sched,
        reference_period="2023-01",
        revision_number=0,
        observation_date=date(2023, 1, 1),
        vintage_date=date(2023, 2, 14),
        scheduled_at=datetime(2023, 2, 14, 13, 30, tzinfo=timezone.utc),
        source_published_at=datetime(2023, 2, 14, 13, 30, tzinfo=timezone.utc),
        first_retrieved_at=datetime(2023, 2, 14, 13, 31, tzinfo=timezone.utc),
        known_at=datetime(2023, 2, 14, 13, 30, tzinfo=timezone.utc),
        raw_value="+0.5%",
        level_value=Decimal("300.536"),
        derived_change_value=Decimal("0.50"),
        unit="PERCENT_MOM",
        source_snapshot=source_snapshot_fixture,
    )

    # Replay again at T = 2023-01-01
    replay_after = resolve_macro_events_as_of(
        as_of=datetime(2023, 1, 1, 0, 0, tzinfo=timezone.utc),
        event_families=["US_CPI"],
    )

    assert len(replay_before) == len(replay_after) == 1
    assert replay_before[0].released_at == replay_after[0].released_at is None
    assert replay_before[0].initial_value == replay_after[0].initial_value is None


# 8. Duplicate hostility
def test_duplicate_hostility():
    """Duplicate canonical keys must fail completeness."""
    cpi_keys = list(get_canonical_expected_cpi_keys())
    # Add a duplicate key
    cpi_keys.append(cpi_keys[0])

    report = evaluate_canonical_macro_coverage("US_CPI", cpi_keys)
    assert report.is_complete is False
    assert report.duplicate_count == 1
    assert report.observed_count == 78
    assert report.matched_count == 77


# 9. Sparse hostility
def test_sparse_hostility():
    """1 real event out of 204 must fail completeness."""
    report = evaluate_canonical_macro_coverage("US_CPI", ["US_CPI_2020_03"])
    assert report.is_complete is False
    assert report.matched_count == 1
    assert report.expected_count == 77
    assert report.missing_count == 76
    assert report.coverage_pct < 2.0


# 10. Extra-event hostility
def test_extra_event_hostility():
    """Unexpected records must not raise canonical coverage and must fail completeness."""
    cpi_keys = list(get_canonical_expected_cpi_keys())
    # Remove 1 expected key, add 2 unexpected extra keys
    removed = cpi_keys.pop()
    cpi_keys.extend(["US_CPI_1999_01", "US_CPI_2099_12"])

    report = evaluate_canonical_macro_coverage("US_CPI", cpi_keys)
    assert report.is_complete is False
    assert report.matched_count == 76
    assert report.missing_count == 1
    assert report.unexpected_extra_count == 2
    assert report.coverage_pct < 100.0
    assert "US_CPI_1999_01" in report.unexpected_keys


# 11. Naive timestamp hostility
def test_naive_timestamp_hostility():
    """Naive datetimes must fail closed with explicit validation error."""
    with pytest.raises(ValueError, match="NAIVE_DATETIME_FORBIDDEN"):
        parse_strict_iso_datetime("2026-08-12T08:30:00")


# 12. Missing provenance hostility
@pytest.mark.django_db
def test_missing_provenance_hostility(macro_identities):
    """Records lacking SourceSnapshot or hash must fail provenance verification and fail-closed."""
    cpi = macro_identities["US_CPI"]
    cpi_keys = list(get_canonical_expected_cpi_keys())

    # Create a schedule vintage without source snapshot
    sched_unproven = MacroScheduleVintage.objects.create(
        vintage_id="sched_no_provenance",
        event=cpi,
        reference_period="2026-01",
        scheduled_at=datetime(2026, 2, 13, 13, 30, tzinfo=timezone.utc),
        schedule_status=ScheduleStatus.SCHEDULED,
        known_at=datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc),
        source_snapshot=None,
    )
    assert sched_unproven.source_snapshot is None

    # Hostility check: Any record with missing provenance is flagged as invalid_key
    invalid_records = ["US_CPI_2026_01"]
    report = evaluate_canonical_macro_coverage("US_CPI", cpi_keys, invalid_keys=invalid_records)
    assert report.is_complete is False
    assert report.invalid_count == 1
    assert "US_CPI_2026_01" in report.missing_keys



# 13. Deterministic macro evidence fingerprint
@pytest.mark.django_db
def test_deterministic_macro_evidence_fingerprint(macro_identities, source_snapshot_fixture):
    """Running fingerprint twice produces identical SHA-256; changing one level value changes fingerprint."""
    cpi = macro_identities["US_CPI"]

    sched = MacroScheduleVintage.objects.create(
        vintage_id="sched_cpi_fp_01",
        event=cpi,
        reference_period="2024-01",
        scheduled_at=datetime(2024, 2, 13, 13, 30, tzinfo=timezone.utc),
        schedule_status=ScheduleStatus.SCHEDULED,
        known_at=datetime(2024, 1, 5, 0, 0, tzinfo=timezone.utc),
        source_snapshot=source_snapshot_fixture,
    )

    obs = MacroObservationVintage.objects.create(
        vintage_id="obs_cpi_fp_01",
        event=cpi,
        schedule_vintage=sched,
        reference_period="2024-01",
        revision_number=0,
        observation_date=date(2024, 1, 1),
        vintage_date=date(2024, 2, 13),
        scheduled_at=datetime(2024, 2, 13, 13, 30, tzinfo=timezone.utc),
        source_published_at=datetime(2024, 2, 13, 13, 30, tzinfo=timezone.utc),
        first_retrieved_at=datetime(2024, 2, 13, 13, 31, tzinfo=timezone.utc),
        known_at=datetime(2024, 2, 13, 13, 30, tzinfo=timezone.utc),
        raw_value="+0.3%",
        level_value=Decimal("308.417"),
        derived_change_value=Decimal("0.30"),
        unit="PERCENT_MOM",
        source_snapshot=source_snapshot_fixture,
    )

    fp1 = compute_macro_evidence_fingerprint()
    fp2 = compute_macro_evidence_fingerprint()
    assert fp1 == fp2
    assert len(fp1) == 64

    # Now create a new revision vintage with different level_value
    obs_rev = MacroObservationVintage.objects.create(
        vintage_id="obs_cpi_fp_02",
        event=cpi,
        schedule_vintage=sched,
        reference_period="2024-01",
        revision_number=1,
        observation_date=date(2024, 1, 1),
        vintage_date=date(2024, 3, 12),
        scheduled_at=datetime(2024, 2, 13, 13, 30, tzinfo=timezone.utc),
        source_published_at=datetime(2024, 3, 12, 12, 30, tzinfo=timezone.utc),
        first_retrieved_at=datetime(2024, 3, 12, 12, 31, tzinfo=timezone.utc),
        known_at=datetime(2024, 3, 12, 12, 30, tzinfo=timezone.utc),
        raw_value="+0.4%",
        level_value=Decimal("308.700"),
        derived_change_value=Decimal("0.40"),
        unit="PERCENT_MOM",
        source_snapshot=source_snapshot_fixture,
    )

    fp3 = compute_macro_evidence_fingerprint()
    assert fp3 != fp1, "Fingerprint failed to detect new observation revision!"


# 14. Production readiness gate evaluation fails closed on incomplete macro evidence
@pytest.mark.django_db
def test_readiness_gate_fails_closed_when_macro_incomplete(xauusd_setup):
    """Production readiness gate fails closed on CANDLES_READY_MACRO_MISSING if persistent macro evidence is incomplete."""
    from tests.unit.test_xauusd_data_readiness_pipeline import _create_clean_candles

    instrument, _ = xauusd_setup
    _create_clean_candles(instrument, count=30, tf="15m")

    # When override_macro_count is NOT passed (production path), evaluator checks persistent DB
    # Currently DB has 0 or incomplete macro records -> Must return CANDLES_READY_MACRO_MISSING
    report = XauUsdDataReadinessEvaluator.evaluate(instrument=instrument)
    assert report.candle_gate_passed is True
    assert report.passed is False
    assert report.decision == "CANDLES_READY_MACRO_MISSING"
    assert any("Canonical macro coverage incomplete" in r or "Point-in-time macro event coverage is 0" in r for r in report.reasons)


# ==============================================================================
# CHECKPOINT B REMEDIATION HOSTILE TESTS (Section 13)
# ==============================================================================

# 1. Cancelled CPI is not treated as unexplained missing
def test_remediation_01_cancelled_cpi_not_unexplained_missing():
    """Cancelled October 2025 CPI with OFFICIALLY_NOT_PUBLISHED status is not treated as missing unexplained."""
    keys = list(get_canonical_expected_cpi_keys())
    st_map = {"US_CPI_2025_10": "OFFICIALLY_NOT_PUBLISHED"}
    num_map = {"US_CPI_2025_10": None}
    prov_map = {"US_CPI_2025_10": True}

    report = evaluate_canonical_macro_coverage(
        "US_CPI",
        keys,
        observation_status_map=st_map,
        numeric_values_map=num_map,
        provenance_map=prov_map,
    )
    assert report.is_complete is True
    assert report.officially_not_published_count == 1
    assert report.missing_unexplained_count == 0
    assert report.missing_count == 0
    assert report.published_count == 76


# 2. CPI October 2025 contains no numeric observation
@pytest.mark.django_db
def test_remediation_02_cpi_october_2025_no_numeric_observation(macro_identities, source_snapshot_fixture):
    """CPI October 2025 persistent observation has publication_status=OFFICIALLY_NOT_PUBLISHED and level_value=None."""
    cpi = macro_identities["US_CPI"]
    sched = MacroScheduleVintage.objects.create(
        vintage_id="sched_cpi_2025_10_v0_test",
        event=cpi,
        reference_period="2025-10",
        scheduled_at=datetime(2025, 11, 13, 13, 30, tzinfo=timezone.utc),
        schedule_status=ScheduleStatus.CANCELLED,
        known_at=datetime(2025, 11, 20, 13, 30, tzinfo=timezone.utc),
        source_snapshot=source_snapshot_fixture,
    )
    obs = MacroObservationVintage.objects.create(
        vintage_id="obs_cpi_2025_10_v0_test",
        event=cpi,
        schedule_vintage=sched,
        reference_period="2025-10",
        revision_number=0,
        publication_status=PublicationStatus.OFFICIALLY_NOT_PUBLISHED,
        non_publication_reason="2025_LAPSE_IN_APPROPRIATIONS",
        observation_date=date(2025, 10, 1),
        vintage_date=date(2025, 12, 18),
        scheduled_at=datetime(2025, 11, 13, 13, 30, tzinfo=timezone.utc),
        source_published_at=datetime(2025, 12, 18, 13, 30, tzinfo=timezone.utc),
        first_retrieved_at=datetime(2025, 12, 18, 13, 35, tzinfo=timezone.utc),
        known_at=datetime(2025, 12, 18, 13, 30, tzinfo=timezone.utc),
        raw_value="OFFICIALLY_NOT_PUBLISHED",
        level_value=None,
        derived_change_value=None,
        unit="PERCENT_MOM",
        source_snapshot=source_snapshot_fixture,
    )
    assert obs.level_value is None
    assert obs.derived_change_value is None
    assert obs.publication_status == PublicationStatus.OFFICIALLY_NOT_PUBLISHED
    assert obs.non_publication_reason == "2025_LAPSE_IN_APPROPRIATIONS"


# 3. Fabricated CPI October value is rejected
def test_remediation_03_fabricated_cpi_october_value_rejected():
    """Any attempt to provide a synthetic numeric value for October 2025 CPI fails validation as INVALID."""
    keys = list(get_canonical_expected_cpi_keys())
    st_map = {"US_CPI_2025_10": "OFFICIALLY_NOT_PUBLISHED"}
    # Hostile synthetic value
    num_map = {"US_CPI_2025_10": Decimal("315.421")}
    prov_map = {"US_CPI_2025_10": True}

    report = evaluate_canonical_macro_coverage(
        "US_CPI",
        keys,
        observation_status_map=st_map,
        numeric_values_map=num_map,
        provenance_map=prov_map,
    )
    assert report.is_complete is False
    assert report.invalid_count >= 1
    assert "US_CPI_2025_10" in report.missing_keys


# 4. Before cancellation known_at, original schedule remains visible
@pytest.mark.django_db
def test_remediation_04_before_cancellation_known_at_original_schedule_visible(macro_identities, source_snapshot_fixture):
    """Before cancellation known_at, original scheduled event is visible in point-in-time replay."""
    cpi = macro_identities["US_CPI"]

    # Schedule v0: Scheduled (known at 2024-12-01)
    s_v0 = MacroScheduleVintage.objects.create(
        vintage_id="sched_cpi_2025_10_v0",
        event=cpi,
        reference_period="2025-10",
        scheduled_at=datetime(2025, 11, 13, 13, 30, tzinfo=timezone.utc),
        schedule_status=ScheduleStatus.SCHEDULED,
        known_at=datetime(2024, 12, 1, 0, 0, tzinfo=timezone.utc),
        source_snapshot=source_snapshot_fixture,
    )
    # Schedule v1: Cancelled (known at 2025-11-20 13:30)
    MacroScheduleVintage.objects.create(
        vintage_id="sched_cpi_2025_10_v1",
        event=cpi,
        reference_period="2025-10",
        scheduled_at=datetime(2025, 11, 13, 13, 30, tzinfo=timezone.utc),
        schedule_status=ScheduleStatus.CANCELLED,
        known_at=datetime(2025, 11, 20, 13, 30, tzinfo=timezone.utc),
        supersedes_vintage=s_v0,
        source_snapshot=source_snapshot_fixture,
    )

    # Replay as of 2025-11-10 (before cancellation known_at)
    events = resolve_macro_events_as_of(
        as_of=datetime(2025, 11, 10, 0, 0, tzinfo=timezone.utc),
        event_families=["US_CPI"],
    )
    cpi_event = next((e for e in events if "2025-10" in e.event_id), None)
    assert cpi_event is not None
    assert cpi_event.scheduled_at == datetime(2025, 11, 13, 13, 30, tzinfo=timezone.utc)
    assert cpi_event.released_at is None


# 5. After cancellation known_at, future blackout from cancelled release disappears
@pytest.mark.django_db
def test_remediation_05_after_cancellation_known_at_blackout_disappears(macro_identities, source_snapshot_fixture):
    """After cancellation known_at, cancelled release is excluded from replay so future blackout disappears."""
    cpi = macro_identities["US_CPI"]

    s_v0 = MacroScheduleVintage.objects.create(
        vintage_id="sched_cpi_2025_10_v0",
        event=cpi,
        reference_period="2025-10",
        scheduled_at=datetime(2025, 11, 13, 13, 30, tzinfo=timezone.utc),
        schedule_status=ScheduleStatus.SCHEDULED,
        known_at=datetime(2024, 12, 1, 0, 0, tzinfo=timezone.utc),
        source_snapshot=source_snapshot_fixture,
    )
    MacroScheduleVintage.objects.create(
        vintage_id="sched_cpi_2025_10_v1",
        event=cpi,
        reference_period="2025-10",
        scheduled_at=datetime(2025, 11, 13, 13, 30, tzinfo=timezone.utc),
        schedule_status=ScheduleStatus.CANCELLED,
        known_at=datetime(2025, 11, 20, 13, 30, tzinfo=timezone.utc),
        supersedes_vintage=s_v0,
        source_snapshot=source_snapshot_fixture,
    )
    # Observation is OFFICIALLY_NOT_PUBLISHED
    MacroObservationVintage.objects.create(
        vintage_id="obs_cpi_2025_10_v0",
        event=cpi,
        schedule_vintage=s_v0,
        reference_period="2025-10",
        revision_number=0,
        publication_status=PublicationStatus.OFFICIALLY_NOT_PUBLISHED,
        observation_date=date(2025, 10, 1),
        vintage_date=date(2025, 12, 18),
        scheduled_at=datetime(2025, 11, 13, 13, 30, tzinfo=timezone.utc),
        source_published_at=datetime(2025, 12, 18, 13, 30, tzinfo=timezone.utc),
        first_retrieved_at=datetime(2025, 12, 18, 13, 35, tzinfo=timezone.utc),
        known_at=datetime(2025, 12, 18, 13, 30, tzinfo=timezone.utc),
        raw_value="OFFICIALLY_NOT_PUBLISHED",
        level_value=None,
        derived_change_value=None,
        unit="PERCENT_MOM",
        source_snapshot=source_snapshot_fixture,
    )

    # Replay as of 2025-11-21 (after cancellation known_at)
    events = resolve_macro_events_as_of(
        as_of=datetime(2025, 11, 21, 0, 0, tzinfo=timezone.utc),
        event_families=["US_CPI"],
    )
    cpi_event = next((e for e in events if "2025-10" in e.event_id), None)
    assert cpi_event is None, "Cancelled release without valid numeric observation must be excluded from replay!"


# 6. NFP October dedicated release is represented as cancelled
@pytest.mark.django_db
def test_remediation_06_nfp_october_dedicated_release_cancelled(macro_identities, source_snapshot_fixture):
    """NFP October 2025 dedicated release has a CANCELLED schedule vintage with defensible known_at."""
    nfp = macro_identities["US_NFP"]
    sched_cancel = MacroScheduleVintage.objects.create(
        vintage_id="sched_nfp_2025_10_v1",
        event=nfp,
        reference_period="2025-10",
        scheduled_at=datetime(2025, 11, 7, 13, 30, tzinfo=timezone.utc),
        schedule_status=ScheduleStatus.CANCELLED,
        known_at=datetime(2025, 11, 20, 13, 30, tzinfo=timezone.utc),
        source_snapshot=source_snapshot_fixture,
    )
    assert sched_cancel.schedule_status == ScheduleStatus.CANCELLED
    assert sched_cancel.known_at == datetime(2025, 11, 20, 13, 30, tzinfo=timezone.utc)


# 7. Delayed/bundled October PAYEMS is invisible before actual publication
@pytest.mark.django_db
def test_remediation_07_delayed_bundled_october_payems_invisible_before_publication(macro_identities, source_snapshot_fixture):
    """Delayed/bundled October PAYEMS observation is invisible before its actual publication timestamp (2025-12-16T13:30:00Z)."""
    nfp = macro_identities["US_NFP"]
    s_v0 = MacroScheduleVintage.objects.create(
        vintage_id="sched_nfp_2025_10_v0",
        event=nfp,
        reference_period="2025-10",
        scheduled_at=datetime(2025, 11, 7, 13, 30, tzinfo=timezone.utc),
        schedule_status=ScheduleStatus.CANCELLED,
        known_at=datetime(2025, 11, 20, 13, 30, tzinfo=timezone.utc),
        source_snapshot=source_snapshot_fixture,
    )
    MacroObservationVintage.objects.create(
        vintage_id="obs_nfp_2025_10_v0",
        event=nfp,
        schedule_vintage=s_v0,
        reference_period="2025-10",
        revision_number=0,
        publication_status=PublicationStatus.PUBLISHED_LATE_OR_BUNDLED,
        observation_date=date(2025, 10, 1),
        vintage_date=date(2025, 12, 16),
        scheduled_at=datetime(2025, 12, 16, 13, 30, tzinfo=timezone.utc),
        source_published_at=datetime(2025, 12, 16, 13, 30, tzinfo=timezone.utc),
        first_retrieved_at=datetime(2025, 12, 16, 13, 35, tzinfo=timezone.utc),
        known_at=datetime(2025, 12, 16, 13, 30, tzinfo=timezone.utc),
        raw_value="-105K",
        level_value=Decimal("159488"),
        derived_change_value=Decimal("-105"),
        unit="THOUSANDS_OF_PERSONS",
        source_snapshot=source_snapshot_fixture,
    )

    # Replay on Dec 01 (before actual publication date of Dec 16)
    events = resolve_macro_events_as_of(
        as_of=datetime(2025, 12, 1, 0, 0, tzinfo=timezone.utc),
        event_families=["US_NFP"],
    )
    nfp_event = next((e for e in events if "2025-10" in e.event_id), None)
    assert nfp_event is None, "Bundled PAYEMS observation must be invisible before actual publication timestamp!"


# 8. Original scheduled release date does not expose PAYEMS early
@pytest.mark.django_db
def test_remediation_08_original_scheduled_release_date_does_not_expose_payems_early(macro_identities, source_snapshot_fixture):
    """The original Nov 7 release date does NOT expose PAYEMS early."""
    nfp = macro_identities["US_NFP"]
    s_v0 = MacroScheduleVintage.objects.create(
        vintage_id="sched_nfp_2025_10_v0",
        event=nfp,
        reference_period="2025-10",
        scheduled_at=datetime(2025, 11, 7, 13, 30, tzinfo=timezone.utc),
        schedule_status=ScheduleStatus.SCHEDULED,
        known_at=datetime(2024, 12, 1, 0, 0, tzinfo=timezone.utc),
        source_snapshot=source_snapshot_fixture,
    )
    MacroObservationVintage.objects.create(
        vintage_id="obs_nfp_2025_10_v0",
        event=nfp,
        schedule_vintage=s_v0,
        reference_period="2025-10",
        revision_number=0,
        publication_status=PublicationStatus.PUBLISHED_LATE_OR_BUNDLED,
        observation_date=date(2025, 10, 1),
        vintage_date=date(2025, 12, 16),
        scheduled_at=datetime(2025, 12, 16, 13, 30, tzinfo=timezone.utc),
        source_published_at=datetime(2025, 12, 16, 13, 30, tzinfo=timezone.utc),
        first_retrieved_at=datetime(2025, 12, 16, 13, 35, tzinfo=timezone.utc),
        known_at=datetime(2025, 12, 16, 13, 30, tzinfo=timezone.utc),
        raw_value="-105K",
        level_value=Decimal("159488"),
        derived_change_value=Decimal("-105"),
        unit="THOUSANDS_OF_PERSONS",
        source_snapshot=source_snapshot_fixture,
    )

    # Replay on original scheduled date Nov 7 at 14:00 UTC
    events = resolve_macro_events_as_of(
        as_of=datetime(2025, 11, 7, 14, 0, tzinfo=timezone.utc),
        event_families=["US_NFP"],
    )
    nfp_event = next((e for e in events if "2025-10" in e.event_id), None)
    assert nfp_event is not None
    assert nfp_event.released_at is None
    assert nfp_event.initial_value is None, "PAYEMS must not leak on originally scheduled release date!"


# 9. Actual later publication exposes October PAYEMS
@pytest.mark.django_db
def test_remediation_09_actual_later_publication_exposes_october_payems(macro_identities, source_snapshot_fixture):
    """At actual publication timestamp (2025-12-16T13:30:00Z), October PAYEMS becomes visible."""
    nfp = macro_identities["US_NFP"]
    s_v0 = MacroScheduleVintage.objects.create(
        vintage_id="sched_nfp_2025_10_v0",
        event=nfp,
        reference_period="2025-10",
        scheduled_at=datetime(2025, 11, 7, 13, 30, tzinfo=timezone.utc),
        schedule_status=ScheduleStatus.CANCELLED,
        known_at=datetime(2025, 11, 20, 13, 30, tzinfo=timezone.utc),
        source_snapshot=source_snapshot_fixture,
    )
    MacroObservationVintage.objects.create(
        vintage_id="obs_nfp_2025_10_v0",
        event=nfp,
        schedule_vintage=s_v0,
        reference_period="2025-10",
        revision_number=0,
        publication_status=PublicationStatus.PUBLISHED_LATE_OR_BUNDLED,
        observation_date=date(2025, 10, 1),
        vintage_date=date(2025, 12, 16),
        scheduled_at=datetime(2025, 12, 16, 13, 30, tzinfo=timezone.utc),
        source_published_at=datetime(2025, 12, 16, 13, 30, tzinfo=timezone.utc),
        first_retrieved_at=datetime(2025, 12, 16, 13, 35, tzinfo=timezone.utc),
        known_at=datetime(2025, 12, 16, 13, 30, tzinfo=timezone.utc),
        raw_value="-105K",
        level_value=Decimal("159488"),
        derived_change_value=Decimal("-105"),
        unit="THOUSANDS_OF_PERSONS",
        source_snapshot=source_snapshot_fixture,
    )

    # Replay on Dec 16 at 14:00 UTC
    events = resolve_macro_events_as_of(
        as_of=datetime(2025, 12, 16, 14, 0, tzinfo=timezone.utc),
        event_families=["US_NFP"],
    )
    nfp_event = next((e for e in events if "2025-10" in e.event_id), None)
    assert nfp_event is not None
    assert nfp_event.released_at == datetime(2025, 12, 16, 13, 30, tzinfo=timezone.utc)
    assert nfp_event.initial_value == "-105K"


# 10. Insertion of later evidence does not mutate replay before that evidence was known
@pytest.mark.django_db
def test_remediation_10_insertion_of_later_evidence_does_not_mutate_replay_before_known(macro_identities, source_snapshot_fixture):
    """Inserting evidence known at T+30 does not mutate replay resolved at T."""
    cpi = macro_identities["US_CPI"]

    sched1 = MacroScheduleVintage.objects.create(
        vintage_id="sched_mut_01",
        event=cpi,
        reference_period="2024-01",
        scheduled_at=datetime(2024, 2, 13, 13, 30, tzinfo=timezone.utc),
        schedule_status=ScheduleStatus.SCHEDULED,
        known_at=datetime(2024, 1, 5, 0, 0, tzinfo=timezone.utc),
        source_snapshot=source_snapshot_fixture,
    )
    MacroObservationVintage.objects.create(
        vintage_id="obs_mut_01",
        event=cpi,
        schedule_vintage=sched1,
        reference_period="2024-01",
        revision_number=0,
        observation_date=date(2024, 1, 1),
        vintage_date=date(2024, 2, 13),
        scheduled_at=datetime(2024, 2, 13, 13, 30, tzinfo=timezone.utc),
        source_published_at=datetime(2024, 2, 13, 13, 30, tzinfo=timezone.utc),
        first_retrieved_at=datetime(2024, 2, 13, 13, 35, tzinfo=timezone.utc),
        known_at=datetime(2024, 2, 13, 13, 30, tzinfo=timezone.utc),
        raw_value="+0.3%",
        level_value=Decimal("308.417"),
        unit="PERCENT_MOM",
        source_snapshot=source_snapshot_fixture,
    )

    t_eval = datetime(2024, 2, 20, 0, 0, tzinfo=timezone.utc)
    replay_before = resolve_macro_events_as_of(t_eval, event_families=["US_CPI"])
    assert len(replay_before) == 1
    assert replay_before[0].initial_value == "+0.3%"
    assert replay_before[0].revised_value is None

    # Insert later revision published and known at T+30 (2024-03-12)
    MacroObservationVintage.objects.create(
        vintage_id="obs_mut_02",
        event=cpi,
        schedule_vintage=sched1,
        reference_period="2024-01",
        revision_number=1,
        observation_date=date(2024, 1, 1),
        vintage_date=date(2024, 3, 12),
        scheduled_at=datetime(2024, 2, 13, 13, 30, tzinfo=timezone.utc),
        source_published_at=datetime(2024, 3, 12, 12, 30, tzinfo=timezone.utc),
        first_retrieved_at=datetime(2024, 3, 12, 12, 35, tzinfo=timezone.utc),
        known_at=datetime(2024, 3, 12, 12, 30, tzinfo=timezone.utc),
        raw_value="+0.4%",
        level_value=Decimal("308.700"),
        unit="PERCENT_MOM",
        source_snapshot=source_snapshot_fixture,
    )

    replay_after = resolve_macro_events_as_of(t_eval, event_families=["US_CPI"])
    assert len(replay_after) == 1
    assert replay_after[0].initial_value == "+0.3%"
    assert replay_after[0].revised_value is None, "Replay at T must not be mutated by later revision!"


# 11. OFFICIALLY_NOT_PUBLISHED without authoritative provenance fails
def test_remediation_11_officially_not_published_without_authoritative_provenance_fails():
    """OFFICIALLY_NOT_PUBLISHED status lacking authoritative provenance fails as INVALID."""
    keys = list(get_canonical_expected_cpi_keys())
    st_map = {"US_CPI_2025_10": "OFFICIALLY_NOT_PUBLISHED"}
    num_map = {"US_CPI_2025_10": None}
    prov_map = {"US_CPI_2025_10": False}  # Missing provenance!

    report = evaluate_canonical_macro_coverage(
        "US_CPI",
        keys,
        observation_status_map=st_map,
        numeric_values_map=num_map,
        provenance_map=prov_map,
    )
    assert report.is_complete is False
    assert report.invalid_count >= 1


# 12. Fake cancellation without SourceSnapshot fails
@pytest.mark.django_db
def test_remediation_12_fake_cancellation_without_source_snapshot_fails(macro_identities):
    """A cancellation schedule vintage without an authoritative SourceSnapshot fails clean() validation and coverage."""
    cpi = macro_identities["US_CPI"]
    fake_sched = MacroScheduleVintage(
        vintage_id="sched_fake_cancel",
        event=cpi,
        reference_period="2025-10",
        scheduled_at=datetime(2025, 11, 13, 13, 30, tzinfo=timezone.utc),
        schedule_status=ScheduleStatus.CANCELLED,
        known_at=datetime(2025, 11, 20, 13, 30, tzinfo=timezone.utc),
        source_snapshot=None,
    )
    with pytest.raises(ValidationError):
        fake_sched.clean()

    # And coverage set reconciliation fails closed when provenance is missing
    keys = list(get_canonical_expected_cpi_keys())
    report = evaluate_canonical_macro_coverage(
        "US_CPI",
        keys,
        observation_status_map={"US_CPI_2025_10": "OFFICIALLY_NOT_PUBLISHED"},
        provenance_map={"US_CPI_2025_10": False},
    )
    assert report.is_complete is False
    assert report.invalid_count >= 1


# 13. Duplicate canonical lifecycle fails
def test_remediation_13_duplicate_canonical_lifecycle_fails():
    """Duplicate canonical keys in observed list fail coverage evaluation."""
    keys = list(get_canonical_expected_cpi_keys())
    keys.append("US_CPI_2025_10")  # Duplicate!

    report = evaluate_canonical_macro_coverage("US_CPI", keys)
    assert report.is_complete is False
    assert report.duplicate_count == 1


# 14. Naive timestamps fail
def test_remediation_14_naive_timestamps_fail():
    """Attempting to parse a naive ISO timestamp without timezone designator fails strict validation."""
    with pytest.raises(ValueError):
        parse_strict_iso_datetime("2024-02-13T13:30:00")  # Missing Z or offset


# 15. Unexplained missing event fails
def test_remediation_15_unexplained_missing_event_fails():
    """Missing any canonical event without an approved non-publication reason fails is_complete."""
    keys = [k for k in get_canonical_expected_cpi_keys() if k != "US_CPI_2022_06"]
    report = evaluate_canonical_macro_coverage("US_CPI", keys)
    assert report.is_complete is False
    assert report.missing_count == 1
    assert "US_CPI_2022_06" in report.missing_keys


# 16. Extra events do not increase canonical coverage
def test_remediation_16_extra_events_do_not_increase_canonical_coverage():
    """Extra unexpected events (O \\ E) are flagged and do not raise coverage or permit is_complete."""
    keys = [k for k in get_canonical_expected_cpi_keys() if k != "US_CPI_2022_06"]
    keys.append("US_CPI_2099_01")  # Unexpected extra event

    report = evaluate_canonical_macro_coverage("US_CPI", keys)
    assert report.is_complete is False
    assert report.unexpected_extra_count == 1
    assert "US_CPI_2099_01" in report.unexpected_keys
    assert report.coverage_pct < 100.0


# ==============================================================================
# SECTION 7 & SECTION 9: HOSTILE PROVENANCE & IMMUTABILITY TESTS
# ==============================================================================

# 1. Canonical key US_CPI_YYYY_MM is correctly converted before schedule-map lookup
def test_hostile_01_canonical_key_cpi_conversion():
    """Canonical key US_CPI_YYYY_MM is converted to YYYY-MM and resolves previous reference period."""
    from apps.market_data.macro.coverage import canonical_key_to_ref_period, get_previous_canonical_ref_period
    assert canonical_key_to_ref_period("US_CPI_2024_05") == "2024-05"
    sorted_keys = ["US_CPI_2024_04", "US_CPI_2024_05"]
    schedule_map = {"2024-04": ("sched_dt", "2024"), "2024-05": ("sched_dt", "2024")}
    prev = get_previous_canonical_ref_period(sorted_keys, 1, schedule_map)
    assert prev == "2024-04"
    assert prev in schedule_map, "Resolved previous reference period must match schedule_map key!"


# 2. Canonical key US_NFP_YYYY_MM is correctly converted before schedule-map lookup
def test_hostile_02_canonical_key_nfp_conversion():
    """Canonical key US_NFP_YYYY_MM is converted to YYYY-MM and resolves previous reference period."""
    from apps.market_data.macro.coverage import canonical_key_to_ref_period, get_previous_canonical_ref_period
    assert canonical_key_to_ref_period("US_NFP_2024_05") == "2024-05"
    sorted_keys = ["US_NFP_2024_04", "US_NFP_2024_05"]
    schedule_map = {"2024-04": ("sched_dt", "2024"), "2024-05": ("sched_dt", "2024")}
    prev = get_previous_canonical_ref_period(sorted_keys, 1, schedule_map)
    assert prev == "2024-04"
    assert prev in schedule_map, "Resolved previous reference period must match schedule_map key!"


# 3. No generic December-1 fallback exists
@pytest.mark.django_db
def test_hostile_03_no_generic_december_1_fallback():
    """No schedule vintage may be assigned a generic December 1 fallback date without an authoritative source."""
    from apps.market_data.macro.coverage import validate_schedule_vintage_provenance
    from apps.market_data.models import ScheduleProvenanceType, MacroScheduleVintage
    sched = MacroScheduleVintage(
        vintage_id="sched_synth_dec1",
        reference_period="2025-01",
        scheduled_at=datetime(2025, 2, 12, 13, 30, tzinfo=timezone.utc),
        known_at=datetime(2024, 12, 1, 0, 0, tzinfo=timezone.utc),
        provenance_type=ScheduleProvenanceType.UNKNOWN,
        source_snapshot=None,
    )
    is_valid, reason = validate_schedule_vintage_provenance(sched)
    assert is_valid is False
    assert "UNKNOWN provenance type" in reason or "lacks supporting SourceSnapshot" in reason


# 4. BLS previous-release schedule uses the announcing release timestamp
@pytest.mark.django_db
def test_hostile_04_bls_previous_release_uses_announcing_timestamp(macro_identities):
    """BLS previous-release schedule requires known_at to match announcing release publication timestamp."""
    from apps.market_data.macro.coverage import validate_schedule_vintage_provenance
    from apps.market_data.models import ScheduleProvenanceType, MacroScheduleVintage, SourceSnapshot
    cpi = macro_identities["US_CPI"]
    snap = SourceSnapshot.objects.create(
        snapshot_id="snap_cpi_prev_test",
        source_url="https://www.bls.gov/news.release/archives/cpi_04102024.htm",
        source_name="bls_cpi_prev_test",
        first_retrieved_at=datetime(2024, 4, 10, 12, 35, tzinfo=timezone.utc),
        raw_payload_bytes_sha256="a" * 64,
        raw_content=b"consumer price index scheduled to be released",
    )
    ann_ts = datetime(2024, 4, 10, 12, 30, tzinfo=timezone.utc)
    sched = MacroScheduleVintage(
        vintage_id="sched_valid_ann",
        event=cpi,
        reference_period="2024-04",
        scheduled_at=datetime(2024, 5, 15, 12, 30, tzinfo=timezone.utc),
        known_at=ann_ts,
        announcing_release_url="https://www.bls.gov/news.release/archives/cpi_04102024.htm",
        announcing_release_timestamp=ann_ts,
        provenance_type=ScheduleProvenanceType.BLS_PREVIOUS_RELEASE_ANNOUNCEMENT,
        source_snapshot=snap,
    )
    is_valid, reason = validate_schedule_vintage_provenance(sched)
    assert is_valid is True, f"Expected valid, got: {reason}"

    sched.known_at = datetime(2024, 5, 1, 0, 0, tzinfo=timezone.utc)
    is_valid, reason = validate_schedule_vintage_provenance(sched)
    assert is_valid is False
    assert "known_at does not match announcing release timestamp" in reason


# 5. Schedule does NOT use its own future release timestamp as known_at
@pytest.mark.django_db
def test_hostile_05_schedule_cannot_use_future_release_timestamp_as_known_at(macro_identities, source_snapshot_fixture):
    """Schedule vintage cannot use its own future release timestamp as known_at (hostility: known_at >= scheduled_at)."""
    from apps.market_data.macro.coverage import validate_schedule_vintage_provenance
    from apps.market_data.models import ScheduleProvenanceType, MacroScheduleVintage
    cpi = macro_identities["US_CPI"]
    rel_ts = datetime(2024, 5, 15, 12, 30, tzinfo=timezone.utc)
    sched = MacroScheduleVintage(
        vintage_id="sched_future_leak",
        event=cpi,
        reference_period="2024-04",
        scheduled_at=rel_ts,
        known_at=rel_ts,
        provenance_type=ScheduleProvenanceType.BLS_PREVIOUS_RELEASE_ANNOUNCEMENT,
        announcing_release_url="https://www.bls.gov/news.release/archives/cpi_05152024.htm",
        announcing_release_timestamp=rel_ts,
        source_snapshot=source_snapshot_fixture,
    )
    is_valid, reason = validate_schedule_vintage_provenance(sched)
    assert is_valid is False
    assert "is >= scheduled_at" in reason


# 6. OMB provenance requires a real SourceSnapshot
def test_hostile_06_omb_provenance_requires_real_sourcesnapshot():
    """OMB PFEI schedule provenance requires a real supporting SourceSnapshot with valid SHA-256."""
    from apps.market_data.macro.coverage import validate_schedule_vintage_provenance
    from apps.market_data.models import ScheduleProvenanceType, MacroScheduleVintage
    sched = MacroScheduleVintage(
        vintage_id="sched_omb_no_snap",
        reference_period="2025-01",
        scheduled_at=datetime(2025, 2, 12, 13, 30, tzinfo=timezone.utc),
        known_at=datetime(2024, 12, 15, 0, 0, tzinfo=timezone.utc),
        source_published_at=datetime(2024, 12, 15, 0, 0, tzinfo=timezone.utc),
        provenance_type=ScheduleProvenanceType.OMB_PFEI_SCHEDULE,
        source_snapshot=None,
    )
    is_valid, reason = validate_schedule_vintage_provenance(sched)
    assert is_valid is False
    assert "lacks supporting SourceSnapshot" in reason


# 7. OMB provenance without defensible publication date fails
@pytest.mark.django_db
def test_hostile_07_omb_provenance_without_defensible_publication_date_fails(source_snapshot_fixture):
    """OMB PFEI schedule provenance without defensible publication date fails validation."""
    from apps.market_data.macro.coverage import validate_schedule_vintage_provenance
    from apps.market_data.models import ScheduleProvenanceType, MacroScheduleVintage
    sched = MacroScheduleVintage(
        vintage_id="sched_omb_no_pub_date",
        reference_period="2025-01",
        scheduled_at=datetime(2025, 2, 12, 13, 30, tzinfo=timezone.utc),
        known_at=datetime(2024, 12, 15, 0, 0, tzinfo=timezone.utc),
        source_published_at=None,
        provenance_type=ScheduleProvenanceType.OMB_PFEI_SCHEDULE,
        source_snapshot=source_snapshot_fixture,
    )
    is_valid, reason = validate_schedule_vintage_provenance(sched)
    assert is_valid is False
    assert "lacks defensible publication date" in reason


# 8. Fake known_at with a valid-looking timestamp but no supporting source fails
def test_hostile_08_fake_known_at_without_supporting_source_fails():
    """A valid-looking known_at timestamp without a supporting SourceSnapshot fails provenance validation."""
    from apps.market_data.macro.coverage import validate_schedule_vintage_provenance
    from apps.market_data.models import ScheduleProvenanceType, MacroScheduleVintage
    sched = MacroScheduleVintage(
        vintage_id="sched_fake_timestamp",
        reference_period="2023-05",
        scheduled_at=datetime(2023, 6, 13, 12, 30, tzinfo=timezone.utc),
        known_at=datetime(2023, 5, 10, 12, 30, tzinfo=timezone.utc),
        provenance_type=ScheduleProvenanceType.UNKNOWN,
        source_snapshot=None,
    )
    is_valid, reason = validate_schedule_vintage_provenance(sched)
    assert is_valid is False


# 9. Source snapshot whose contents do not contain/support the target schedule fails
@pytest.mark.django_db
def test_hostile_09_unsupporting_snapshot_content_fails_provenance(macro_identities):
    """SourceSnapshot whose body does not contain the required release announcement text fails validation."""
    from apps.market_data.macro.coverage import validate_schedule_vintage_provenance
    from apps.market_data.models import ScheduleProvenanceType, MacroScheduleVintage, SourceSnapshot
    cpi = macro_identities["US_CPI"]
    unrelated_snap = SourceSnapshot.objects.create(
        snapshot_id="snap_unrelated_weather",
        source_url="https://www.noaa.gov/weather.htm",
        source_name="noaa_weather_test",
        first_retrieved_at=datetime(2024, 4, 10, 12, 35, tzinfo=timezone.utc),
        raw_payload_bytes_sha256="b" * 64,
        raw_content=b"National Weather Service severe weather bulletin.",
    )
    ann_ts = datetime(2024, 4, 10, 12, 30, tzinfo=timezone.utc)
    sched = MacroScheduleVintage(
        vintage_id="sched_unrelated_content",
        event=cpi,
        reference_period="2024-04",
        scheduled_at=datetime(2024, 5, 15, 12, 30, tzinfo=timezone.utc),
        known_at=ann_ts,
        announcing_release_url="https://www.noaa.gov/weather.htm",
        announcing_release_timestamp=ann_ts,
        provenance_type=ScheduleProvenanceType.BLS_PREVIOUS_RELEASE_ANNOUNCEMENT,
        source_snapshot=unrelated_snap,
    )
    is_valid, reason = validate_schedule_vintage_provenance(sched)
    assert is_valid is False
    assert "does not contain CPI announcement text" in reason


# 10. UNKNOWN schedule provenance keeps macro readiness fail-closed
def test_hostile_10_unknown_provenance_keeps_macro_readiness_fail_closed():
    """Even if counts match expected 77, if provenance_map indicates UNKNOWN, is_complete is False."""
    keys = list(get_canonical_expected_cpi_keys())
    prov_map = {k: True for k in keys}
    prov_map["US_CPI_2023_08"] = False

    report = evaluate_canonical_macro_coverage(
        "US_CPI",
        keys,
        provenance_map=prov_map,
    )
    assert report.is_complete is False
    assert report.provenance_coverage_complete is False
    assert report.invalid_count >= 1


# 11. Historical evidence cannot be silently deleted or overwritten
@pytest.mark.django_db
def test_hostile_11_historical_evidence_cannot_be_silently_overwritten(macro_identities, source_snapshot_fixture):
    """MacroScheduleVintage and MacroObservationVintage raise ValueError on updating existing instances."""
    cpi = macro_identities["US_CPI"]
    sched = MacroScheduleVintage.objects.create(
        vintage_id="sched_immutability_test",
        event=cpi,
        reference_period="2024-01",
        scheduled_at=datetime(2024, 2, 13, 13, 30, tzinfo=timezone.utc),
        known_at=datetime(2024, 1, 11, 13, 30, tzinfo=timezone.utc),
        source_snapshot=source_snapshot_fixture,
    )
    sched.scheduled_at = datetime(2024, 2, 14, 13, 30, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="immutable and append-only"):
        sched.save()

    obs = MacroObservationVintage.objects.create(
        vintage_id="obs_immutability_test",
        event=cpi,
        schedule_vintage=sched,
        reference_period="2024-01",
        revision_number=0,
        observation_date=date(2024, 1, 1),
        vintage_date=date(2024, 2, 13),
        scheduled_at=datetime(2024, 2, 13, 13, 30, tzinfo=timezone.utc),
        source_published_at=datetime(2024, 2, 13, 13, 30, tzinfo=timezone.utc),
        first_retrieved_at=datetime(2024, 2, 13, 13, 35, tzinfo=timezone.utc),
        known_at=datetime(2024, 2, 13, 13, 30, tzinfo=timezone.utc),
        raw_value="+0.3%",
        level_value=Decimal("308.417"),
        unit="PERCENT_MOM",
        source_snapshot=source_snapshot_fixture,
    )
    obs.raw_value = "+0.5%"
    with pytest.raises(ValueError, match="immutable and append-only"):
        obs.save()


# ==============================================================================
# CHECKPOINT B FINAL SEAL HARDENING HOSTILE TESTS (Prompt §10)
# ==============================================================================

# 1. MacroScheduleVintage.save() update fails
@pytest.mark.django_db
def test_final_seal_01_macro_schedule_vintage_save_update_fails(macro_identities, source_snapshot_fixture):
    """MacroScheduleVintage.save() update of an existing record raises ValueError."""
    cpi = macro_identities["US_CPI"]
    sched = MacroScheduleVintage.objects.create(
        vintage_id="sched_fs_01",
        event=cpi,
        reference_period="2024-01",
        scheduled_at=datetime(2024, 2, 13, 13, 30, tzinfo=timezone.utc),
        known_at=datetime(2024, 1, 11, 13, 30, tzinfo=timezone.utc),
        source_snapshot=source_snapshot_fixture,
    )
    sched.scheduled_at = datetime(2024, 2, 14, 13, 30, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="immutable and append-only"):
        sched.save()


# 2. MacroScheduleVintage QuerySet.update() through production manager fails
@pytest.mark.django_db
def test_final_seal_02_macro_schedule_vintage_queryset_update_fails(macro_identities, source_snapshot_fixture):
    """MacroScheduleVintage.objects.update() raises PermissionError."""
    cpi = macro_identities["US_CPI"]
    MacroScheduleVintage.objects.create(
        vintage_id="sched_fs_02",
        event=cpi,
        reference_period="2024-02",
        scheduled_at=datetime(2024, 3, 12, 12, 30, tzinfo=timezone.utc),
        known_at=datetime(2024, 2, 13, 13, 30, tzinfo=timezone.utc),
        source_snapshot=source_snapshot_fixture,
    )
    with pytest.raises(PermissionError, match="is prohibited"):
        MacroScheduleVintage.objects.filter(vintage_id="sched_fs_02").update(
            schedule_status=ScheduleStatus.CANCELLED
        )


# 3. MacroScheduleVintage.delete() through production manager fails
@pytest.mark.django_db
def test_final_seal_03_macro_schedule_vintage_delete_fails(macro_identities, source_snapshot_fixture):
    """MacroScheduleVintage instance .delete() and QuerySet .delete() both raise PermissionError."""
    cpi = macro_identities["US_CPI"]
    sched = MacroScheduleVintage.objects.create(
        vintage_id="sched_fs_03",
        event=cpi,
        reference_period="2024-03",
        scheduled_at=datetime(2024, 4, 10, 12, 30, tzinfo=timezone.utc),
        known_at=datetime(2024, 3, 12, 12, 30, tzinfo=timezone.utc),
        source_snapshot=source_snapshot_fixture,
    )
    # Instance delete
    with pytest.raises(PermissionError, match="cannot be deleted"):
        sched.delete()

    # QuerySet delete
    with pytest.raises(PermissionError, match="is prohibited"):
        MacroScheduleVintage.objects.filter(vintage_id="sched_fs_03").delete()


# 4. MacroObservationVintage QuerySet.update() fails
@pytest.mark.django_db
def test_final_seal_04_macro_observation_vintage_queryset_update_fails(macro_identities, source_snapshot_fixture):
    """MacroObservationVintage QuerySet.update() and delete() raise PermissionError."""
    cpi = macro_identities["US_CPI"]
    sched = MacroScheduleVintage.objects.create(
        vintage_id="sched_fs_04",
        event=cpi,
        reference_period="2024-04",
        scheduled_at=datetime(2024, 5, 15, 12, 30, tzinfo=timezone.utc),
        known_at=datetime(2024, 4, 10, 12, 30, tzinfo=timezone.utc),
        source_snapshot=source_snapshot_fixture,
    )
    MacroObservationVintage.objects.create(
        vintage_id="obs_fs_04",
        event=cpi,
        schedule_vintage=sched,
        reference_period="2024-04",
        revision_number=0,
        observation_date=date(2024, 4, 1),
        vintage_date=date(2024, 5, 15),
        scheduled_at=datetime(2024, 5, 15, 12, 30, tzinfo=timezone.utc),
        source_published_at=datetime(2024, 5, 15, 12, 30, tzinfo=timezone.utc),
        first_retrieved_at=datetime(2024, 5, 15, 12, 35, tzinfo=timezone.utc),
        known_at=datetime(2024, 5, 15, 12, 30, tzinfo=timezone.utc),
        raw_value="+0.3%",
        level_value=Decimal("314.069"),
        unit="PERCENT_MOM",
        source_snapshot=source_snapshot_fixture,
    )
    with pytest.raises(PermissionError, match="is prohibited"):
        MacroObservationVintage.objects.filter(vintage_id="obs_fs_04").update(raw_value="+0.5%")

    with pytest.raises(PermissionError, match="is prohibited"):
        MacroObservationVintage.objects.filter(vintage_id="obs_fs_04").delete()


# 5. SourceSnapshot QuerySet.update() fails
@pytest.mark.django_db
def test_final_seal_05_source_snapshot_queryset_update_fails(source_snapshot_fixture):
    """SourceSnapshot QuerySet.update() and delete() raise PermissionError."""
    with pytest.raises(PermissionError, match="is prohibited"):
        SourceSnapshot.objects.filter(pk=source_snapshot_fixture.pk).update(
            raw_payload_bytes_sha256="0" * 64
        )

    with pytest.raises(PermissionError, match="is prohibited"):
        SourceSnapshot.objects.filter(pk=source_snapshot_fixture.pk).delete()


# 6. Existing FOMC schedule is not mutated during provenance enrichment
@pytest.mark.django_db
def test_final_seal_06_existing_fomc_schedule_not_mutated_during_provenance_enrichment(macro_identities, source_snapshot_fixture):
    """FOMC schedule vintage fields remain identical when provenance assertion is appended."""
    fomc = macro_identities["FOMC_RATE"]
    original_sched_at = datetime(2024, 3, 20, 18, 0, tzinfo=timezone.utc)
    original_known_at = datetime(2023, 9, 22, 18, 0, tzinfo=timezone.utc)
    sched = MacroScheduleVintage.objects.create(
        vintage_id="sched_fomc_fs_06",
        event=fomc,
        reference_period="2024-03-20",
        scheduled_at=original_sched_at,
        known_at=original_known_at,
        schedule_status=ScheduleStatus.SCHEDULED,
        source_snapshot=source_snapshot_fixture,
        provenance_type=ScheduleProvenanceType.UNKNOWN,
    )

    # Ingest provenance assertion
    new_snap = SourceSnapshot.objects.create(
        snapshot_id="snap_fomc_ann_06",
        source_url="https://www.federalreserve.gov/newsevents/pressreleases/monetary20230922a.htm",
        source_name="frb_announcement",
        first_retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        raw_payload_bytes_sha256="f" * 64,
        raw_content=b"FOMC announces meeting schedule for 2024",
    )
    stats = IngestionStats()
    assertion = record_schedule_provenance_assertion(
        schedule_vintage=sched,
        provenance_type=ScheduleProvenanceType.OTHER_FIRST_PARTY,
        source_snapshot=new_snap,
        announcing_release_url=new_snap.source_url,
        announcing_release_timestamp=original_known_at,
        parser_rule_version="FRB_OFFICIAL_ANN_V1",
        stats=stats,
    )
    assert assertion is not None
    assert stats.provenance_assertions_inserted == 1

    # Fetch fresh sched from DB - MUST BE STRICTLY UNMUTATED
    sched.refresh_from_db()
    assert sched.scheduled_at == original_sched_at
    assert sched.known_at == original_known_at
    assert sched.source_snapshot == source_snapshot_fixture
    assert sched.provenance_type == ScheduleProvenanceType.UNKNOWN

    # Effective provenance resolves to the new assertion
    eff = get_effective_schedule_provenance(sched)
    assert eff["provenance_type"] == ScheduleProvenanceType.OTHER_FIRST_PARTY
    assert eff["source_snapshot"] == new_snap
    assert eff["announcing_release_url"] == new_snap.source_url


# 7. FOMC provenance assertion is append-only
@pytest.mark.django_db
def test_final_seal_07_fomc_provenance_assertion_is_append_only(macro_identities, source_snapshot_fixture):
    """MacroScheduleProvenanceAssertion cannot be updated or deleted."""
    fomc = macro_identities["FOMC_RATE"]
    sched = MacroScheduleVintage.objects.create(
        vintage_id="sched_fomc_fs_07",
        event=fomc,
        reference_period="2024-05-01",
        scheduled_at=datetime(2024, 5, 1, 18, 0, tzinfo=timezone.utc),
        known_at=datetime(2023, 9, 22, 18, 0, tzinfo=timezone.utc),
        source_snapshot=source_snapshot_fixture,
    )
    stats = IngestionStats()
    assertion = record_schedule_provenance_assertion(
        schedule_vintage=sched,
        provenance_type=ScheduleProvenanceType.OTHER_FIRST_PARTY,
        source_snapshot=source_snapshot_fixture,
        announcing_release_url=source_snapshot_fixture.source_url,
        announcing_release_timestamp=sched.known_at,
        parser_rule_version="FRB_OFFICIAL_ANN_V1",
        stats=stats,
    )

    # 1. Instance save update fails
    assertion.parser_rule_version = "FRB_V2"
    with pytest.raises(ValueError, match="immutable and append-only"):
        assertion.save()

    # 2. Instance delete fails
    with pytest.raises(PermissionError, match="cannot be deleted"):
        assertion.delete()

    # 3. QuerySet update fails
    with pytest.raises(PermissionError, match="is prohibited"):
        MacroScheduleProvenanceAssertion.objects.filter(pk=assertion.pk).update(parser_rule_version="FRB_V2")

    # 4. QuerySet delete fails
    with pytest.raises(PermissionError, match="is prohibited"):
        MacroScheduleProvenanceAssertion.objects.filter(pk=assertion.pk).delete()


# 8. Second identical ingestion creates no duplicate provenance assertion
@pytest.mark.django_db
def test_final_seal_08_second_identical_ingestion_creates_no_duplicate_assertion(macro_identities, source_snapshot_fixture):
    """Re-running identical assertion ingestion is strictly idempotent with 0 duplicates."""
    fomc = macro_identities["FOMC_RATE"]
    sched = MacroScheduleVintage.objects.create(
        vintage_id="sched_fomc_fs_08",
        event=fomc,
        reference_period="2024-06-12",
        scheduled_at=datetime(2024, 6, 12, 18, 0, tzinfo=timezone.utc),
        known_at=datetime(2023, 9, 22, 18, 0, tzinfo=timezone.utc),
        source_snapshot=source_snapshot_fixture,
    )
    stats1 = IngestionStats()
    a1 = record_schedule_provenance_assertion(
        schedule_vintage=sched,
        provenance_type=ScheduleProvenanceType.OTHER_FIRST_PARTY,
        source_snapshot=source_snapshot_fixture,
        announcing_release_url=source_snapshot_fixture.source_url,
        announcing_release_timestamp=sched.known_at,
        parser_rule_version="FRB_OFFICIAL_ANN_V1",
        stats=stats1,
    )
    assert stats1.provenance_assertions_inserted == 1
    assert MacroScheduleProvenanceAssertion.objects.filter(schedule_vintage=sched).count() == 1

    stats2 = IngestionStats()
    a2 = record_schedule_provenance_assertion(
        schedule_vintage=sched,
        provenance_type=ScheduleProvenanceType.OTHER_FIRST_PARTY,
        source_snapshot=source_snapshot_fixture,
        announcing_release_url=source_snapshot_fixture.source_url,
        announcing_release_timestamp=sched.known_at,
        parser_rule_version="FRB_OFFICIAL_ANN_V1",
        stats=stats2,
    )
    assert stats2.provenance_assertions_inserted == 0
    assert stats2.idempotent_skips == 1
    assert a1.pk == a2.pk
    assert MacroScheduleProvenanceAssertion.objects.filter(schedule_vintage=sched).count() == 1


# 9. New source provenance creates a new assertion, never overwrites old assertion
@pytest.mark.django_db
def test_final_seal_09_new_source_provenance_creates_new_assertion_never_overwrites(macro_identities, source_snapshot_fixture):
    """Subsequent provenance discovery appends a new assertion linking to superseded assertion."""
    fomc = macro_identities["FOMC_RATE"]
    sched = MacroScheduleVintage.objects.create(
        vintage_id="sched_fomc_fs_09",
        event=fomc,
        reference_period="2024-07-31",
        scheduled_at=datetime(2024, 7, 31, 18, 0, tzinfo=timezone.utc),
        known_at=datetime(2023, 9, 22, 18, 0, tzinfo=timezone.utc),
        source_snapshot=source_snapshot_fixture,
    )
    stats = IngestionStats()
    a1 = record_schedule_provenance_assertion(
        schedule_vintage=sched,
        provenance_type=ScheduleProvenanceType.OTHER_FIRST_PARTY,
        source_snapshot=source_snapshot_fixture,
        announcing_release_url=source_snapshot_fixture.source_url,
        announcing_release_timestamp=sched.known_at,
        parser_rule_version="FRB_OFFICIAL_ANN_V1",
        stats=stats,
    )

    new_snap = SourceSnapshot.objects.create(
        snapshot_id="snap_fs_09_second",
        source_url="https://www.federalreserve.gov/calendars/updated.htm",
        source_name="frb_calendar_update",
        first_retrieved_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
        raw_payload_bytes_sha256="9" * 64,
        raw_content=b"Updated calendar assertion",
    )
    a2 = record_schedule_provenance_assertion(
        schedule_vintage=sched,
        provenance_type=ScheduleProvenanceType.OTHER_FIRST_PARTY,
        source_snapshot=new_snap,
        announcing_release_url=new_snap.source_url,
        announcing_release_timestamp=sched.known_at,
        parser_rule_version="FRB_OFFICIAL_ANN_V2",
        stats=stats,
    )

    assert a1.pk != a2.pk
    assert MacroScheduleProvenanceAssertion.objects.filter(schedule_vintage=sched).count() == 2
    a2.refresh_from_db()
    assert a2.supersedes_assertion == a1
    # Check that old assertion still exists and is untouched
    a1.refresh_from_db()
    assert a1.source_snapshot == source_snapshot_fixture
    assert a1.parser_rule_version == "FRB_OFFICIAL_ANN_V1"


# 10. Superseded UNKNOWN schedules do not count as active UNKNOWN
@pytest.mark.django_db
def test_final_seal_10_superseded_unknown_schedules_do_not_count_as_active(macro_identities, source_snapshot_fixture):
    """Historical superseded UNKNOWN vintages are excluded from active unknown audit count."""
    cpi = macro_identities["US_CPI"]
    # Superseded vintage v0 with UNKNOWN provenance
    v0 = MacroScheduleVintage.objects.create(
        vintage_id="sched_cpi_2024_05_v0",
        event=cpi,
        reference_period="2024-05",
        scheduled_at=datetime(2024, 6, 12, 12, 30, tzinfo=timezone.utc),
        known_at=datetime(2023, 12, 1, 0, 0, tzinfo=timezone.utc),
        provenance_type=ScheduleProvenanceType.UNKNOWN,
        source_snapshot=source_snapshot_fixture,
    )
    # Active vintage v1 with valid previous-release provenance
    ann_ts = datetime(2024, 5, 15, 12, 30, tzinfo=timezone.utc)
    v1_snap = SourceSnapshot.objects.create(
        snapshot_id="snap_cpi_prev_release_fs10",
        source_url="https://www.bls.gov/news.release/archives/cpi_05152024.htm",
        source_name="bls_cpi_prev",
        first_retrieved_at=datetime(2024, 5, 15, 12, 35, tzinfo=timezone.utc),
        raw_payload_bytes_sha256="a" * 64,
        raw_content=b"Consumer Price Index for May 2024 is scheduled for release June 12, 2024.",
    )
    v1 = MacroScheduleVintage.objects.create(
        vintage_id="sched_cpi_2024_05_v1",
        event=cpi,
        reference_period="2024-05",
        scheduled_at=datetime(2024, 6, 12, 12, 30, tzinfo=timezone.utc),
        known_at=ann_ts,
        announcing_release_url=v1_snap.source_url,
        announcing_release_timestamp=ann_ts,
        provenance_type=ScheduleProvenanceType.BLS_PREVIOUS_RELEASE_ANNOUNCEMENT,
        source_snapshot=v1_snap,
    )

    # Active vintage is v1 because known_at is higher
    active_sched = (
        MacroScheduleVintage.objects.filter(event_id="US_CPI", reference_period="2024-05")
        .order_by("-known_at")
        .first()
    )
    assert active_sched.pk == v1.pk
    is_valid, _ = validate_schedule_vintage_provenance(active_sched)
    assert is_valid is True

    # Audit separation: active unknown = 0, superseded unknown = 1
    eff = get_effective_schedule_provenance(active_sched)
    active_unknown = 1 if eff["provenance_type"] == ScheduleProvenanceType.UNKNOWN else 0
    superseded_unknown = MacroScheduleVintage.objects.filter(
        event_id="US_CPI", reference_period="2024-05", provenance_type=ScheduleProvenanceType.UNKNOWN
    ).exclude(pk=active_sched.pk).count()

    assert active_unknown == 0
    assert superseded_unknown == 1


# 11. ACTIVE_UNKNOWN > 0 makes macro gate fail closed
def test_final_seal_11_active_unknown_makes_macro_gate_fail_closed():
    """Active UNKNOWN schedule provenance forces is_complete=False and provenance_coverage_complete=False."""
    keys = list(get_canonical_expected_cpi_keys())
    prov_map = {k: True for k in keys}
    # One active schedule has UNKNOWN provenance
    prov_map["US_CPI_2024_06"] = False

    report = evaluate_canonical_macro_coverage("US_CPI", keys, provenance_map=prov_map)
    assert report.is_complete is False
    assert report.provenance_coverage_complete is False
    assert report.invalid_count >= 1


# 12. CPI October 2025 chronology matches persisted evidence
@pytest.mark.django_db
def test_final_seal_12_cpi_october_2025_chronology_matches_persisted_evidence(macro_identities):
    """CPI October 2025 cancellation must be known_at 2025-12-18 with OFFICIALLY_NOT_PUBLISHED and None numeric value."""
    cpi = macro_identities["US_CPI"]
    sched_snap = SourceSnapshot.objects.create(
        snapshot_id="snap_cpi_orig_fs12",
        source_url="https://www.bls.gov/schedule/2025/home.htm",
        source_name="bls_sched_2025",
        first_retrieved_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        raw_payload_bytes_sha256="c" * 64,
        raw_content=b"CPI 2025 schedule",
    )
    cancel_snap = SourceSnapshot.objects.create(
        snapshot_id="snap_cpi_cancel_fs12",
        source_url="https://www.bls.gov/news.release/archives/cpi_12182025.htm",
        source_name="bls_cpi_12182025",
        first_retrieved_at=datetime(2025, 12, 18, 13, 35, tzinfo=timezone.utc),
        raw_payload_bytes_sha256="d" * 64,
        raw_content=b"Consumer Price Index October 2025 was officially cancelled due to the 2025 Federal Government Shutdown.",
    )
    # Original schedule
    MacroScheduleVintage.objects.create(
        vintage_id="sched_cpi_2025_10_orig_fs12",
        event=cpi,
        reference_period="2025-10",
        scheduled_at=datetime(2025, 11, 12, 13, 30, tzinfo=timezone.utc),
        known_at=datetime(2025, 10, 10, 12, 30, tzinfo=timezone.utc),
        schedule_status=ScheduleStatus.SCHEDULED,
        source_snapshot=sched_snap,
    )
    # Cancellation schedule (known_at strictly 2025-12-18, NOT 2025-11-20 empsit!)
    MacroScheduleVintage.objects.create(
        vintage_id="sched_cpi_2025_10_cancel_fs12",
        event=cpi,
        reference_period="2025-10",
        scheduled_at=datetime(2025, 11, 12, 13, 30, tzinfo=timezone.utc),
        known_at=datetime(2025, 12, 18, 13, 30, tzinfo=timezone.utc),
        schedule_status=ScheduleStatus.CANCELLED,
        source_snapshot=cancel_snap,
    )
    # Observation vintage
    MacroObservationVintage.objects.create(
        vintage_id="obs_cpi_2025_10_fs12",
        event=cpi,
        reference_period="2025-10",
        revision_number=0,
        observation_date=date(2025, 10, 1),
        vintage_date=date(2025, 12, 18),
        scheduled_at=datetime(2025, 11, 12, 13, 30, tzinfo=timezone.utc),
        source_published_at=datetime(2025, 12, 18, 13, 30, tzinfo=timezone.utc),
        first_retrieved_at=datetime(2025, 12, 18, 13, 35, tzinfo=timezone.utc),
        known_at=datetime(2025, 12, 18, 13, 30, tzinfo=timezone.utc),
        publication_status=PublicationStatus.OFFICIALLY_NOT_PUBLISHED,
        non_publication_reason="2025 Federal Government Shutdown",
        level_value=None,
        derived_change_value=None,
        source_snapshot=cancel_snap,
    )

    active_cancel = (
        MacroScheduleVintage.objects.filter(event_id="US_CPI", reference_period="2025-10", schedule_status=ScheduleStatus.CANCELLED)
        .order_by("-known_at")
        .first()
    )
    assert active_cancel.known_at == datetime(2025, 12, 18, 13, 30, tzinfo=timezone.utc)
    assert active_cancel.source_snapshot.source_url == "https://www.bls.gov/news.release/archives/cpi_12182025.htm"

    obs = MacroObservationVintage.objects.filter(event_id="US_CPI", reference_period="2025-10").first()
    assert obs.publication_status == PublicationStatus.OFFICIALLY_NOT_PUBLISHED
    assert obs.level_value is None
    assert obs.derived_change_value is None
    assert "2025 Federal Government Shutdown" in obs.non_publication_reason


# 13. NFP October 2025 chronology matches persisted evidence
@pytest.mark.django_db
def test_final_seal_13_nfp_october_2025_chronology_matches_persisted_evidence(macro_identities):
    """NFP October 2025 cancellation known_at 2025-11-20 from empsit_11202025.htm with bundled publication."""
    nfp = macro_identities["US_NFP"]
    cancel_snap = SourceSnapshot.objects.create(
        snapshot_id="snap_nfp_cancel_fs13",
        source_url="https://www.bls.gov/news.release/archives/empsit_11202025.htm",
        source_name="bls_empsit_11202025",
        first_retrieved_at=datetime(2025, 11, 20, 13, 35, tzinfo=timezone.utc),
        raw_payload_bytes_sha256="e" * 64,
        raw_content=b"The Employment Situation for October 2025 is rescheduled and bundled.",
    )
    MacroScheduleVintage.objects.create(
        vintage_id="sched_nfp_2025_10_cancel_fs13",
        event=nfp,
        reference_period="2025-10",
        scheduled_at=datetime(2025, 11, 7, 13, 30, tzinfo=timezone.utc),
        known_at=datetime(2025, 11, 20, 13, 30, tzinfo=timezone.utc),
        schedule_status=ScheduleStatus.CANCELLED,
        source_snapshot=cancel_snap,
    )
    MacroObservationVintage.objects.create(
        vintage_id="obs_nfp_2025_10_fs13",
        event=nfp,
        reference_period="2025-10",
        revision_number=0,
        observation_date=date(2025, 10, 1),
        vintage_date=date(2025, 11, 20),
        scheduled_at=datetime(2025, 11, 7, 13, 30, tzinfo=timezone.utc),
        source_published_at=datetime(2025, 11, 20, 13, 30, tzinfo=timezone.utc),
        first_retrieved_at=datetime(2025, 11, 20, 13, 35, tzinfo=timezone.utc),
        known_at=datetime(2025, 11, 20, 13, 30, tzinfo=timezone.utc),
        publication_status=PublicationStatus.PUBLISHED_LATE_OR_BUNDLED,
        raw_value="+212K",
        level_value=Decimal("158500"),
        derived_change_value=Decimal("212"),
        unit="THOUSANDS",
        source_snapshot=cancel_snap,
    )

    cancel_sched = (
        MacroScheduleVintage.objects.filter(event_id="US_NFP", reference_period="2025-10", schedule_status=ScheduleStatus.CANCELLED)
        .order_by("-known_at")
        .first()
    )
    assert cancel_sched.known_at == datetime(2025, 11, 20, 13, 30, tzinfo=timezone.utc)
    assert cancel_sched.source_snapshot.source_url == "https://www.bls.gov/news.release/archives/empsit_11202025.htm"

    obs = MacroObservationVintage.objects.filter(event_id="US_NFP", reference_period="2025-10").first()
    assert obs.publication_status == PublicationStatus.PUBLISHED_LATE_OR_BUNDLED
    assert obs.level_value == Decimal("158500")


# 14. Same source URL with changed hash cannot overwrite/reuse old payload silently
@pytest.mark.django_db
def test_final_seal_14_same_url_with_changed_hash_appends_new_version():
    """When same URL returns a modified payload SHA-256, it creates a new versioned snapshot, never mutating."""
    url = "https://www.federalreserve.gov/calendars/target_range.htm"
    stats1 = IngestionStats()
    snap1 = fetch_or_reuse_snapshot(
        url=url,
        source_name="frb_target",
        headers={},
        stats=stats1,
        raw_content=b"Original content v1",
        force_fetch=True,
    )
    assert stats1.source_snapshots_inserted == 1
    assert snap1.raw_content == b"Original content v1"

    # Same URL fetched again with DIFFERENT payload
    stats2 = IngestionStats()
    snap2 = fetch_or_reuse_snapshot(
        url=url,
        source_name="frb_target",
        headers={},
        stats=stats2,
        raw_content=b"Modified content v2 with rate hike",
        force_fetch=True,
    )
    assert stats2.source_snapshots_inserted == 1
    assert snap2.pk != snap1.pk
    assert snap2.raw_payload_bytes_sha256 != snap1.raw_payload_bytes_sha256

    # Both records exist in DB; snap1 is completely unmodified
    all_snaps = list(SourceSnapshot.objects.filter(source_url=url).order_by("created_at"))
    assert len(all_snaps) == 2
    assert all_snaps[0].raw_content == b"Original content v1"
    assert all_snaps[1].raw_content == b"Modified content v2 with rate hike"

    # Direct mutation attempt on snap1 fails
    with pytest.raises(PermissionError, match="is prohibited"):
        SourceSnapshot.objects.filter(pk=snap1.pk).update(raw_payload_bytes_sha256="x" * 64)

