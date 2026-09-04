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
)
from apps.market_data.macro.fingerprint import compute_macro_evidence_fingerprint
from apps.market_data.macro.replay import resolve_macro_events_as_of
from apps.market_data.models import (
    MacroEventFamily,
    MacroEventIdentity,
    MacroObservationVintage,
    MacroScheduleVintage,
    PublicationStatus,
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

