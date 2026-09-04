"""
Unit and integration tests for Macroeconomic Event Evidence Infrastructure (Phase 8 - Checkpoint A).
Covers:
1. Append-only models immutability and uniqueness / referential constraints.
2. Zero-lookahead as-of masking.
3. Pre-event blackout safety with future unobserved releases.
4. Rescheduling without is_terminal filtering.
5. Cancellation without is_terminal filtering.
6. Unlimited revision chains (N >= 2).
7. Timezone & DST (America/New_York EST vs EDT to UTC).
8. Idempotency and source-version conflict semantics.
9. Canonical expected-event set reconciliation and coverage gating.
"""
from datetime import date, datetime, timezone
from decimal import Decimal
import pytest
from django.db import IntegrityError
from zoneinfo import ZoneInfo

from apps.market_data.macro.coverage import (
    evaluate_canonical_macro_coverage,
    get_canonical_expected_cpi_keys,
    get_canonical_expected_fomc_keys,
    get_canonical_expected_nfp_keys,
)
from apps.market_data.macro.replay import resolve_macro_events_as_of
from apps.market_data.macro.sources import (
    ConflictResolution,
    convert_eastern_to_utc,
    resolve_conflict_action,
)
from apps.market_data.models import (
    MacroEventFamily,
    MacroEventIdentity,
    MacroObservationVintage,
    MacroScheduleVintage,
    ScheduleStatus,
    SourceSnapshot,
)
from engine.core.types import EventImpact, MacroEvent
from engine.cycles.events import evaluate_macro_event_risk


@pytest.mark.django_db
def test_models_immutability_and_constraints():
    """Test append-only immutability, unique constraints, and PROTECT foreign keys."""
    # 1. MacroEventIdentity
    event_ident = MacroEventIdentity.objects.create(
        identity_id="US_CPI",
        name="US Consumer Price Index",
        event_family=MacroEventFamily.US_CPI,
        impact="HIGH",
        reporting_agency="Bureau of Labor Statistics",
    )

    # 2. SourceSnapshot immutability
    snap1 = SourceSnapshot.objects.create(
        snapshot_id="snap_cpi_001",
        source_url="https://api.stlouisfed.org/fred/series/observations",
        source_name="alfred_bls",
        first_retrieved_at=datetime(2026, 8, 12, 12, 31, tzinfo=timezone.utc),
        raw_payload_bytes_sha256="a" * 64,
    )
    with pytest.raises(ValueError, match="SourceSnapshot is immutable and append-only"):
        snap1.raw_payload_bytes_sha256 = "b" * 64
        snap1.save()

    # 3. MacroScheduleVintage immutability & uniqueness
    sched1 = MacroScheduleVintage.objects.create(
        vintage_id="sched_cpi_2026_07_v1",
        event=event_ident,
        reference_period="2026-07",
        scheduled_at=datetime(2026, 8, 12, 12, 30, tzinfo=timezone.utc),
        schedule_status=ScheduleStatus.SCHEDULED,
        known_at=datetime(2025, 12, 1, 10, 0, tzinfo=timezone.utc),
        source_snapshot=snap1,
    )
    with pytest.raises(ValueError, match="MacroScheduleVintage is immutable and append-only"):
        sched1.schedule_status = ScheduleStatus.RESCHEDULED
        sched1.save()

    from django.db import transaction

    with transaction.atomic():
        with pytest.raises(IntegrityError):
            MacroScheduleVintage.objects.create(
                vintage_id="sched_cpi_2026_07_v1_dup",
                event=event_ident,
                reference_period="2026-07",
                scheduled_at=datetime(2026, 8, 12, 12, 30, tzinfo=timezone.utc),
                known_at=datetime(2025, 12, 1, 10, 0, tzinfo=timezone.utc),  # duplicate (event, ref_period, known_at)
            )

    # 4. MacroObservationVintage immutability & uniqueness
    obs0 = MacroObservationVintage.objects.create(
        vintage_id="obs_cpi_2026_07_v0",
        event=event_ident,
        schedule_vintage=sched1,
        reference_period="2026-07",
        revision_number=0,
        observation_date=date(2026, 7, 1),
        vintage_date=date(2026, 8, 12),
        scheduled_at=datetime(2026, 8, 12, 12, 30, tzinfo=timezone.utc),
        source_published_at=datetime(2026, 8, 12, 12, 30, tzinfo=timezone.utc),
        known_at=datetime(2026, 8, 12, 12, 31, tzinfo=timezone.utc),
        raw_value="314.54",
        level_value=Decimal("314.5400"),
        derived_change_value=Decimal("0.2000"),
        source_snapshot=snap1,
    )
    with pytest.raises(ValueError, match="MacroObservationVintage is immutable and append-only"):
        obs0.raw_value = "315.00"
        obs0.save()

    with transaction.atomic():
        with pytest.raises(IntegrityError):
            MacroObservationVintage.objects.create(
                vintage_id="obs_cpi_2026_07_v0_dup",
                event=event_ident,
                reference_period="2026-07",
                revision_number=0,  # duplicate (event, ref_period, revision_number)
                known_at=datetime(2026, 8, 12, 13, 0, tzinfo=timezone.utc),
            )

    # 5. Referential integrity (PROTECT)
    from django.db.models.deletion import ProtectedError
    with pytest.raises(ProtectedError):
        event_ident.delete()


@pytest.mark.django_db
def test_as_of_masking():
    """Verify zero-lookahead as-of masking for unreleased values and future revisions."""
    event_ident = MacroEventIdentity.objects.create(
        identity_id="US_CPI",
        name="US CPI YoY",
        event_family=MacroEventFamily.US_CPI,
        impact="HIGH",
    )
    sched = MacroScheduleVintage.objects.create(
        vintage_id="sched_cpi_2026_07",
        event=event_ident,
        reference_period="2026-07",
        scheduled_at=datetime(2026, 8, 12, 12, 30, tzinfo=timezone.utc),
        known_at=datetime(2025, 12, 1, 0, 0, tzinfo=timezone.utc),
    )
    # Observation initial release: published 2026-08-12 12:30 UTC
    obs_init = MacroObservationVintage.objects.create(
        vintage_id="obs_cpi_init",
        event=event_ident,
        schedule_vintage=sched,
        reference_period="2026-07",
        revision_number=0,
        source_published_at=datetime(2026, 8, 12, 12, 30, tzinfo=timezone.utc),
        known_at=datetime(2026, 8, 12, 12, 30, 5, tzinfo=timezone.utc),
        raw_value="2.9%",
    )
    # Observation revision: published 2026-09-11 12:30 UTC
    MacroObservationVintage.objects.create(
        vintage_id="obs_cpi_rev1",
        event=event_ident,
        schedule_vintage=sched,
        reference_period="2026-07",
        revision_number=1,
        revises_vintage=obs_init,
        source_published_at=datetime(2026, 9, 11, 12, 30, tzinfo=timezone.utc),
        known_at=datetime(2026, 9, 11, 12, 30, 5, tzinfo=timezone.utc),
        raw_value="3.0%",
    )

    # 1. Before release: 2026-08-12 12:00 UTC
    evs_pre = resolve_macro_events_as_of(datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc))
    assert len(evs_pre) == 1
    assert evs_pre[0].released_at is None
    assert evs_pre[0].initial_value is None
    assert evs_pre[0].revised_at is None
    assert evs_pre[0].revised_value is None

    # 2. At release: 2026-08-12 12:35 UTC (after initial release, before revision)
    evs_release = resolve_macro_events_as_of(datetime(2026, 8, 12, 12, 35, tzinfo=timezone.utc))
    assert len(evs_release) == 1
    assert evs_release[0].released_at == datetime(2026, 8, 12, 12, 30, tzinfo=timezone.utc)
    assert evs_release[0].initial_value == "2.9%"
    assert evs_release[0].revised_at is None
    assert evs_release[0].revised_value is None

    # 3. After revision: 2026-09-12 00:00 UTC
    evs_rev = resolve_macro_events_as_of(datetime(2026, 9, 12, 0, 0, tzinfo=timezone.utc))
    assert len(evs_rev) == 1
    assert evs_rev[0].initial_value == "2.9%"
    assert evs_rev[0].revised_value == "3.0%"
    assert evs_rev[0].revised_at == datetime(2026, 9, 11, 12, 30, tzinfo=timezone.utc)


@pytest.mark.django_db
def test_pre_event_blackout():
    """Verify that an upcoming scheduled event triggers pre-event blackout with released_at=None."""
    event_ident = MacroEventIdentity.objects.create(
        identity_id="US_NFP",
        name="US Non-Farm Payrolls",
        event_family=MacroEventFamily.US_NFP,
        impact="HIGH",
    )
    MacroScheduleVintage.objects.create(
        vintage_id="sched_nfp_upcoming",
        event=event_ident,
        reference_period="2026-08",
        scheduled_at=datetime(2026, 9, 4, 12, 30, tzinfo=timezone.utc),
        known_at=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
    )

    # Evaluate at 12:15 UTC (15 minutes prior to scheduled release)
    as_of = datetime(2026, 9, 4, 12, 15, tzinfo=timezone.utc)
    events = resolve_macro_events_as_of(as_of)
    assert len(events) == 1
    event = events[0]
    assert event.released_at is None
    assert event.initial_value is None

    # Pass directly to engine risk evaluator
    ctx = evaluate_macro_event_risk(as_of, events, blackout_minutes=30)
    assert ctx.is_in_blackout is True
    assert ctx.active_event_name == "US Non-Farm Payrolls (2026-08)"
    assert ctx.minutes_to_next_event == 15
    assert ctx.point_in_time_value is None  # Never leaked ahead of time


@pytest.mark.django_db
def test_rescheduling_without_is_terminal():
    """Verify that rescheduled events supersede earlier vintages based on known_at without is_terminal."""
    event_ident = MacroEventIdentity.objects.create(
        identity_id="FOMC_RATE",
        name="FOMC Rate Decision",
        event_family=MacroEventFamily.FOMC_RATE,
        impact="CRITICAL",
    )
    # Vintage 1: Announced 2025-10-01, meeting originally scheduled for 2026-05-06
    v1 = MacroScheduleVintage.objects.create(
        vintage_id="fomc_v1",
        event=event_ident,
        reference_period="2026-05",
        scheduled_at=datetime(2026, 5, 6, 18, 0, tzinfo=timezone.utc),
        schedule_status=ScheduleStatus.SCHEDULED,
        known_at=datetime(2025, 10, 1, 0, 0, tzinfo=timezone.utc),
    )
    # Vintage 2: Corrected schedule announced 2026-01-15, rescheduled to 2026-04-29
    MacroScheduleVintage.objects.create(
        vintage_id="fomc_v2",
        event=event_ident,
        reference_period="2026-05",
        scheduled_at=datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc),
        schedule_status=ScheduleStatus.RESCHEDULED,
        known_at=datetime(2026, 1, 15, 0, 0, tzinfo=timezone.utc),
        supersedes_vintage=v1,
    )

    # Before correction: 2026-01-10
    evs_early = resolve_macro_events_as_of(datetime(2026, 1, 10, 0, 0, tzinfo=timezone.utc))
    assert len(evs_early) == 1
    assert evs_early[0].scheduled_at == datetime(2026, 5, 6, 18, 0, tzinfo=timezone.utc)

    # After correction: 2026-01-20
    evs_late = resolve_macro_events_as_of(datetime(2026, 1, 20, 0, 0, tzinfo=timezone.utc))
    assert len(evs_late) == 1
    assert evs_late[0].scheduled_at == datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc)


@pytest.mark.django_db
def test_cancellation_without_is_terminal():
    """Verify that cancelled events are excluded from replay based on latest known schedule."""
    event_ident = MacroEventIdentity.objects.create(
        identity_id="US_CPI",
        name="US CPI YoY",
        event_family=MacroEventFamily.US_CPI,
        impact="HIGH",
    )
    # Vintage 1: Scheduled on 2026-03-15
    v1 = MacroScheduleVintage.objects.create(
        vintage_id="cpi_sched_v1",
        event=event_ident,
        reference_period="2026-02",
        scheduled_at=datetime(2026, 3, 15, 12, 30, tzinfo=timezone.utc),
        schedule_status=ScheduleStatus.SCHEDULED,
        known_at=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
    )
    # Vintage 2: Emergency cancellation on 2026-03-01
    MacroScheduleVintage.objects.create(
        vintage_id="cpi_sched_v2",
        event=event_ident,
        reference_period="2026-02",
        scheduled_at=datetime(2026, 3, 15, 12, 30, tzinfo=timezone.utc),
        schedule_status=ScheduleStatus.CANCELLED,
        known_at=datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc),
        supersedes_vintage=v1,
    )

    # 1. As of 2026-02-15: Event is active and scheduled
    evs_active = resolve_macro_events_as_of(datetime(2026, 2, 15, 0, 0, tzinfo=timezone.utc))
    assert len(evs_active) == 1

    # 2. As of 2026-03-05: Event is CANCELLED and excluded
    evs_cancelled = resolve_macro_events_as_of(datetime(2026, 3, 5, 0, 0, tzinfo=timezone.utc))
    assert len(evs_cancelled) == 0

    # Risk evaluation during former scheduled window: No blackout
    ctx = evaluate_macro_event_risk(
        datetime(2026, 3, 15, 12, 30, tzinfo=timezone.utc),
        evs_cancelled,
        blackout_minutes=30,
    )
    assert ctx.is_in_blackout is False


@pytest.mark.django_db
def test_unlimited_revision_chain():
    """Verify that arbitrary revision chains (revision_number >= 0) resolve correctly point-in-time."""
    event_ident = MacroEventIdentity.objects.create(
        identity_id="US_NFP",
        name="US Non-Farm Payrolls",
        event_family=MacroEventFamily.US_NFP,
        impact="HIGH",
    )
    sched = MacroScheduleVintage.objects.create(
        vintage_id="sched_nfp_2024_01",
        event=event_ident,
        reference_period="2024-01",
        scheduled_at=datetime(2024, 2, 2, 13, 30, tzinfo=timezone.utc),
        known_at=datetime(2023, 12, 1, 0, 0, tzinfo=timezone.utc),
    )

    # Rev 0: Initial release (Feb 2, 2024)
    v0 = MacroObservationVintage.objects.create(
        vintage_id="nfp_2024_01_v0",
        event=event_ident,
        schedule_vintage=sched,
        reference_period="2024-01",
        revision_number=0,
        source_published_at=datetime(2024, 2, 2, 13, 30, tzinfo=timezone.utc),
        known_at=datetime(2024, 2, 2, 13, 30, tzinfo=timezone.utc),
        raw_value="+353K",
    )
    # Rev 1: First revision (Mar 8, 2024)
    v1 = MacroObservationVintage.objects.create(
        vintage_id="nfp_2024_01_v1",
        event=event_ident,
        schedule_vintage=sched,
        reference_period="2024-01",
        revision_number=1,
        revises_vintage=v0,
        source_published_at=datetime(2024, 3, 8, 13, 30, tzinfo=timezone.utc),
        known_at=datetime(2024, 3, 8, 13, 30, tzinfo=timezone.utc),
        raw_value="+275K",
    )
    # Rev 2: Second revision (Apr 5, 2024)
    v2 = MacroObservationVintage.objects.create(
        vintage_id="nfp_2024_01_v2",
        event=event_ident,
        schedule_vintage=sched,
        reference_period="2024-01",
        revision_number=2,
        revises_vintage=v1,
        source_published_at=datetime(2024, 4, 5, 12, 30, tzinfo=timezone.utc),
        known_at=datetime(2024, 4, 5, 12, 30, tzinfo=timezone.utc),
        raw_value="+256K",
    )
    # Rev 3: Annual benchmark revision (Feb 7, 2025)
    MacroObservationVintage.objects.create(
        vintage_id="nfp_2024_01_v3",
        event=event_ident,
        schedule_vintage=sched,
        reference_period="2024-01",
        revision_number=3,
        revises_vintage=v2,
        source_published_at=datetime(2025, 2, 7, 13, 30, tzinfo=timezone.utc),
        known_at=datetime(2025, 2, 7, 13, 30, tzinfo=timezone.utc),
        raw_value="+210K",
    )

    # Point-in-time test across 4 chronological windows:
    # 1. Feb 15, 2024 -> Sees Rev 0 only
    r_feb = resolve_macro_events_as_of(datetime(2024, 2, 15, 0, 0, tzinfo=timezone.utc))[0]
    assert r_feb.initial_value == "+353K"
    assert r_feb.revised_value is None

    # 2. Mar 15, 2024 -> Sees Rev 1
    r_mar = resolve_macro_events_as_of(datetime(2024, 3, 15, 0, 0, tzinfo=timezone.utc))[0]
    assert r_mar.initial_value == "+353K"
    assert r_mar.revised_value == "+275K"

    # 3. Apr 15, 2024 -> Sees Rev 2
    r_apr = resolve_macro_events_as_of(datetime(2024, 4, 15, 0, 0, tzinfo=timezone.utc))[0]
    assert r_apr.initial_value == "+353K"
    assert r_apr.revised_value == "+256K"

    # 4. Feb 15, 2025 -> Sees Rev 3
    r_2025 = resolve_macro_events_as_of(datetime(2025, 2, 15, 0, 0, tzinfo=timezone.utc))[0]
    assert r_2025.initial_value == "+353K"
    assert r_2025.revised_value == "+210K"


def test_timezone_and_dst():
    """Verify timezone and DST conversions from Eastern Time (EST/EDT) to UTC."""
    # 1. EST (Standard Time, UTC-5): January
    utc_jan = convert_eastern_to_utc(2024, 1, 10, 8, 30)
    assert utc_jan == datetime(2024, 1, 10, 13, 30, tzinfo=timezone.utc)

    # 2. EDT (Daylight Saving Time, UTC-4): June
    utc_jun = convert_eastern_to_utc(2024, 6, 12, 8, 30)
    assert utc_jun == datetime(2024, 6, 12, 12, 30, tzinfo=timezone.utc)

    # 3. FOMC 2:00 PM EST (Winter): 19:00 UTC
    utc_fomc_est = convert_eastern_to_utc(2024, 1, 31, 14, 0)
    assert utc_fomc_est == datetime(2024, 1, 31, 19, 0, tzinfo=timezone.utc)

    # 4. FOMC 2:00 PM EDT (Summer): 18:00 UTC
    utc_fomc_edt = convert_eastern_to_utc(2024, 5, 1, 14, 0)
    assert utc_fomc_edt == datetime(2024, 5, 1, 18, 0, tzinfo=timezone.utc)


def test_idempotency_and_source_version_conflicts():
    """Verify deterministic conflict resolution rules."""
    h1 = "a" * 64
    h2 = "b" * 64

    # 1. Initial observation -> APPEND_REVISION
    assert resolve_conflict_action(None, h1) == ConflictResolution.APPEND_REVISION

    # 2. Identical key + identical hash -> IDEMPOTENT_SKIP
    assert resolve_conflict_action(h1, h1) == ConflictResolution.IDEMPOTENT_SKIP

    # 3. Identical key + modified hash without later publication -> QUARANTINE
    assert resolve_conflict_action(h1, h2, is_later_official_publication=False) == ConflictResolution.QUARANTINE

    # 4. Later official vintage -> APPEND_REVISION
    assert resolve_conflict_action(h1, h2, is_later_official_publication=True) == ConflictResolution.APPEND_REVISION


def test_canonical_coverage_reconciliation():
    """Verify set reconciliation coverage gating for US_CPI, US_NFP, and FOMC_RATE."""
    # 1. CPI: 77 keys
    cpi_expected = get_canonical_expected_cpi_keys()
    assert len(cpi_expected) == 77

    rep_cpi_full = evaluate_canonical_macro_coverage("US_CPI", list(cpi_expected))
    assert rep_cpi_full.expected_count == 77
    assert rep_cpi_full.matched_count == 77
    assert rep_cpi_full.missing_count == 0
    assert rep_cpi_full.coverage_pct == 100.0
    assert rep_cpi_full.is_complete is True

    # 2. NFP: 76 keys
    nfp_expected = get_canonical_expected_nfp_keys()
    assert len(nfp_expected) == 76

    rep_nfp_full = evaluate_canonical_macro_coverage("US_NFP", list(nfp_expected))
    assert rep_nfp_full.expected_count == 76
    assert rep_nfp_full.matched_count == 76
    assert rep_nfp_full.coverage_pct == 100.0
    assert rep_nfp_full.is_complete is True

    # 3. FOMC: 51 keys (April 29, 2026 confirmed, May 6 excluded)
    fomc_expected = get_canonical_expected_fomc_keys()
    assert len(fomc_expected) == 51
    assert "FOMC_RATE_2026_04_29" in fomc_expected
    assert "FOMC_RATE_2026_05_06" not in fomc_expected

    rep_fomc_full = evaluate_canonical_macro_coverage("FOMC_RATE", list(fomc_expected))
    assert rep_fomc_full.expected_count == 51
    assert rep_fomc_full.is_complete is True

    # 4. Missing key fails completeness
    cpi_missing_one = list(cpi_expected)[:-1]
    rep_missing = evaluate_canonical_macro_coverage("US_CPI", cpi_missing_one)
    assert rep_missing.missing_count == 1
    assert rep_missing.coverage_pct < 100.0
    assert rep_missing.is_complete is False

    # 5. Unexpected extra key does NOT raise coverage and fails completeness
    cpi_with_extra = list(cpi_expected) + ["US_CPI_2020_02", "US_CPI_2030_01"]
    rep_extra = evaluate_canonical_macro_coverage("US_CPI", cpi_with_extra)
    assert rep_extra.matched_count == 77
    assert rep_extra.coverage_pct == 100.0
    assert rep_extra.unexpected_extra_count == 2
    assert rep_extra.is_complete is False  # Cannot be complete with foreign unvetted keys

    # 6. Duplicates tracked and fail completeness
    cpi_with_dup = list(cpi_expected) + [list(cpi_expected)[0]]
    rep_dup = evaluate_canonical_macro_coverage("US_CPI", cpi_with_dup)
    assert rep_dup.duplicate_count == 1
    assert rep_dup.is_complete is False

    # 7. Invalid records tracked and fail completeness
    rep_invalid = evaluate_canonical_macro_coverage(
        "US_CPI",
        list(cpi_expected),
        invalid_keys=[list(cpi_expected)[0]],
    )
    assert rep_invalid.invalid_count == 1
    assert rep_invalid.matched_count == 76
    assert rep_invalid.is_complete is False
