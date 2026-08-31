"""Macroeconomic event risk gate and point-in-time revision-safe provider (A06, A26)."""
from datetime import datetime, timezone
from typing import List, Optional, Sequence

from engine.core.types import EventImpact, MacroEvent, MacroEventContext
from engine.cycles.profile import Cycle3AProfile


def evaluate_macro_event_risk(
    as_of: datetime,
    events: Optional[Sequence[MacroEvent]] = None,
    blackout_minutes: Optional[int] = 30,
    profile: Optional[Cycle3AProfile] = None,
) -> MacroEventContext:
    """
    Evaluate macroeconomic event risk at a specific point-in-time timestamp.

    Acceptance Rule A06 (Macro Event Blackout Gate):
      If a HIGH impact event is within [-blackout_minutes, +blackout_minutes] of scheduled time,
      `is_in_blackout` is set to True (prohibiting BUY_WINDOW and forcing WAIT).

    Acceptance Rule A26 & P3A-11 (Point-in-Time Revision Safety):
      - Releases with released_at > as_of are strictly unobserved.
      - A future revision at revised_at > as_of is UNKNOWN at as_of. It cannot create
        a pre-revision blackout window before revised_at occurs.
      - If revised_at > as_of, the revision is strictly masked, returning initial_value.
      - Only if revised_at <= as_of is the revised value returned.

    Fail-Safe Rule (P3A-12):
      - If events list is empty or None, is_feed_healthy is False. Zero clear-market bonus allowed.
    """
    if as_of.tzinfo is None:
        as_of_utc = as_of.replace(tzinfo=timezone.utc)
    else:
        as_of_utc = as_of.astimezone(timezone.utc)

    if not events:
        return MacroEventContext(
            is_in_blackout=False,
            minutes_to_next_event=None,
            minutes_since_last_event=None,
            active_event_name=None,
            point_in_time_value=None,
            is_feed_healthy=False,
        )

    # Determine effective blackout window
    effective_blackout = blackout_minutes
    if effective_blackout is None and profile is not None:
        effective_blackout = profile.macro_blackout_minutes
    if effective_blackout is None:
        effective_blackout = 30

    # Filter events to only high-impact ones for blackout gating
    high_impact_events = [e for e in events if e.impact == EventImpact.HIGH]

    is_in_blackout = False
    active_event_name: Optional[str] = None
    min_to_next: Optional[int] = None
    min_since_last: Optional[int] = None
    latest_pit_value: Optional[str] = None
    latest_past_event_time: Optional[datetime] = None

    for event in high_impact_events:
        sched_utc = event.scheduled_at.astimezone(timezone.utc) if event.scheduled_at.tzinfo else event.scheduled_at.replace(tzinfo=timezone.utc)
        diff_minutes = int((sched_utc - as_of_utc).total_seconds() // 60)

        # Check proximity to future scheduled events
        if diff_minutes >= 0:
            if min_to_next is None or diff_minutes < min_to_next:
                min_to_next = diff_minutes
        # Check proximity from past scheduled events
        else:
            elapsed_minutes = abs(diff_minutes)
            if min_since_last is None or elapsed_minutes < min_since_last:
                min_since_last = elapsed_minutes

        # Check scheduled blackout window [-blackout_minutes, +blackout_minutes] around scheduled time
        if abs(diff_minutes) <= effective_blackout:
            is_in_blackout = True
            active_event_name = event.name

        # Post-revision publication blackout window (only if revision has ACTUALLY been published as of as_of_utc)
        if event.revised_at is not None:
            rev_utc = event.revised_at.astimezone(timezone.utc) if event.revised_at.tzinfo else event.revised_at.replace(tzinfo=timezone.utc)
            if as_of_utc >= rev_utc:
                # Revision is known. Apply post-revision blackout window [0, +blackout_minutes]
                rev_elapsed_min = int((as_of_utc - rev_utc).total_seconds() // 60)
                if 0 <= rev_elapsed_min <= effective_blackout:
                    is_in_blackout = True
                    active_event_name = f"{event.name} (Revision)"

        # Point-in-Time value resolution (A26)
        rel_utc = event.released_at.astimezone(timezone.utc) if event.released_at.tzinfo else event.released_at.replace(tzinfo=timezone.utc)
        if as_of_utc >= rel_utc:
            # Value has been released
            effective_time = rel_utc
            val = event.initial_value
            if event.revised_at is not None:
                rev_utc = event.revised_at.astimezone(timezone.utc) if event.revised_at.tzinfo else event.revised_at.replace(tzinfo=timezone.utc)
                if as_of_utc >= rev_utc:
                    val = event.revised_value or event.initial_value
                    effective_time = rev_utc

            if latest_past_event_time is None or effective_time >= latest_past_event_time:
                latest_past_event_time = effective_time
                latest_pit_value = val

    return MacroEventContext(
        is_in_blackout=is_in_blackout,
        minutes_to_next_event=min_to_next,
        minutes_since_last_event=min_since_last,
        active_event_name=active_event_name,
        point_in_time_value=latest_pit_value,
        is_feed_healthy=True,
    )
