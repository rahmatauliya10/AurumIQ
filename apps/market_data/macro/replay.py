"""Point-in-time historical replay resolver for macroeconomic events (P3A-11, A06, A26)."""
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple
from django.db.models import Max

from apps.market_data.models import (
    MacroEventIdentity,
    MacroObservationVintage,
    MacroScheduleVintage,
    ScheduleStatus,
)
from engine.core.types import EventImpact, MacroEvent


def resolve_macro_events_as_of(
    as_of: datetime,
    event_families: Optional[Sequence[str]] = None,
) -> List[MacroEvent]:
    """
    Resolve historical macroeconomic events known and valid at a point-in-time timestamp `as_of`.

    Guarantees:
    1. Zero Lookahead (Point-in-Time Safety):
       - Only schedule vintages known at `known_at <= as_of` are evaluated.
       - NEVER filters by `is_terminal=True`. The latest active schedule vintage at `as_of`
         is chosen via `ORDER BY known_at DESC, vintage_id DESC`.
       - If the latest schedule known at `as_of` is CANCELLED, the event is excluded from replay.
       - Future releases (`source_published_at > as_of` or `known_at > as_of`) have `released_at=None`
         and `initial_value=None`, allowing pre-event blackout gates to evaluate cleanly without
         unobserved release figures.
       - Future revisions (`revision.source_published_at > as_of` or `revision.known_at > as_of`)
         are strictly masked, returning initial or earlier point-in-time revision values.
    2. Deterministic Tie-Breaking:
       - Tie-breaks between multiple vintages known at the exact same microsecond are resolved
         deterministically by highest `vintage_id` or `created_at`.
    """
    if as_of.tzinfo is None:
        as_of_utc = as_of.replace(tzinfo=timezone.utc)
    else:
        as_of_utc = as_of.astimezone(timezone.utc)

    # 1. Fetch eligible schedule vintages known at or before as_of
    schedule_qs = (
        MacroScheduleVintage.objects.select_related("event")
        .filter(known_at__lte=as_of_utc)
        .order_by("event_id", "reference_period", "-known_at", "-vintage_id")
    )
    if event_families:
        schedule_qs = schedule_qs.filter(event__event_family__in=event_families)

    # Pick the greatest known_at vintage per (event_id, reference_period)
    latest_schedules: Dict[Tuple[str, str], MacroScheduleVintage] = {}
    for sched in schedule_qs:
        key = (sched.event_id, sched.reference_period)
        if key not in latest_schedules:
            latest_schedules[key] = sched

    resolved_events: List[MacroEvent] = []

    # 2. For each active schedule, resolve corresponding point-in-time observation
    for (event_id, ref_period), sched in sorted(latest_schedules.items()):
        event_ident = sched.event
        impact_enum = getattr(EventImpact, event_ident.impact, EventImpact.HIGH)
        composite_event_id = f"{event_id}_{ref_period}"

        # Query observations known at or before as_of and published at or before as_of
        obs_qs = (
            MacroObservationVintage.objects.filter(
                event=event_ident,
                reference_period=ref_period,
                known_at__lte=as_of_utc,
                source_published_at__lte=as_of_utc,
            )
            .order_by("revision_number")
        )

        observations = list(obs_qs)

        # Filter out observations that are OFFICIALLY_NOT_PUBLISHED from numeric releases
        valid_numeric_obs = [
            o for o in observations
            if getattr(o, "publication_status", "PUBLISHED") != "OFFICIALLY_NOT_PUBLISHED"
            and (o.level_value is not None or o.derived_change_value is not None or (o.raw_value and o.raw_value != "OFFICIALLY_NOT_PUBLISHED"))
        ]

        # If schedule is CANCELLED:
        if sched.schedule_status == ScheduleStatus.CANCELLED:
            # If no valid numeric observation has been published as of as_of_utc, exclude from replay
            if not valid_numeric_obs:
                continue
            # If a late/bundled observation has become known and published as of as_of_utc, emit it as an observed release
            init_obs = valid_numeric_obs[0]
            init_val = init_obs.raw_value or (str(init_obs.derived_change_value) if init_obs.derived_change_value is not None else str(init_obs.level_value))
            rev_at = None
            rev_val = None
            if len(valid_numeric_obs) > 1:
                latest_rev = valid_numeric_obs[-1]
                rev_at = latest_rev.source_published_at or latest_rev.known_at
                rev_val = latest_rev.raw_value or (str(latest_rev.derived_change_value) if latest_rev.derived_change_value is not None else str(latest_rev.level_value))

            resolved_events.append(
                MacroEvent(
                    event_id=composite_event_id,
                    name=f"{event_ident.name} ({ref_period})",
                    scheduled_at=init_obs.scheduled_at or sched.scheduled_at,
                    released_at=init_obs.source_published_at or init_obs.known_at,
                    initial_value=init_val,
                    revised_at=rev_at,
                    revised_value=rev_val,
                    impact=impact_enum,
                )
            )
            continue

        # Case A: Scheduled event where official release is in the future relative to as_of
        if not valid_numeric_obs:
            resolved_events.append(
                MacroEvent(
                    event_id=composite_event_id,
                    name=f"{event_ident.name} ({ref_period})",
                    scheduled_at=sched.scheduled_at,
                    released_at=None,
                    initial_value=None,
                    revised_at=None,
                    revised_value=None,
                    impact=impact_enum,
                )
            )
            continue

        # Case B: Initial release is known and observed at as_of
        init_obs = valid_numeric_obs[0]
        init_val = init_obs.raw_value
        if not init_val and init_obs.derived_change_value is not None:
            init_val = str(init_obs.derived_change_value)
        elif not init_val and init_obs.level_value is not None:
            init_val = str(init_obs.level_value)

        rev_at: Optional[datetime] = None
        rev_val: Optional[str] = None

        # Case C: Check if subsequent revisions exist and were known/published as of as_of
        if len(valid_numeric_obs) > 1:
            latest_rev = valid_numeric_obs[-1]
            rev_at = latest_rev.source_published_at or latest_rev.known_at
            rev_val = latest_rev.raw_value
            if not rev_val and latest_rev.derived_change_value is not None:
                rev_val = str(latest_rev.derived_change_value)
            elif not rev_val and latest_rev.level_value is not None:
                rev_val = str(latest_rev.level_value)

        resolved_events.append(
            MacroEvent(
                event_id=composite_event_id,
                name=f"{event_ident.name} ({ref_period})",
                scheduled_at=sched.scheduled_at,
                released_at=init_obs.source_published_at or init_obs.known_at,
                initial_value=init_val,
                revised_at=rev_at,
                revised_value=rev_val,
                impact=impact_enum,
            )
        )

    # Sort deterministically by scheduled_at, then event_id
    resolved_events.sort(key=lambda e: (e.scheduled_at, e.event_id))
    return resolved_events
