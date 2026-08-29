"""Macroeconomic event risk gate and point-in-time revision-safe provider (A06, A26)."""
from datetime import datetime, timezone
from typing import List, Optional, Sequence

from engine.core.types import EventImpact, MacroEvent, MacroEventContext


def evaluate_macro_event_risk(
    as_of: datetime,
    events: Sequence[MacroEvent],
    blackout_minutes: int = 30,
) -> MacroEventContext:
    """
    Evaluate macroeconomic event risk at a specific point-in-time timestamp.

    Acceptance Rule A06 (Macro Event Blackout Gate):
      If a HIGH impact event is within [-blackout_minutes, +blackout_minutes] of `as_of`,
      `is_in_blackout` is set to True (prohibiting BUY_WINDOW and forcing WAIT).

    Acceptance Rule A26 (Point-in-Time Revision Safety):
      - Releases with released_at > as_of are strictly unobserved.
      - If an event was revised at revised_at > as_of, the revision is strictly masked,
        and `point_in_time_value` returns `initial_value`.
      - Only if revised_at <= as_of is the revised value returned.
    """
    if as_of.tzinfo is None:
        as_of_utc = as_of.replace(tzinfo=timezone.utc)
    else:
        as_of_utc = as_of.astimezone(timezone.utc)

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

        # Check proximity to future events
        if diff_minutes >= 0:
            if min_to_next is None or diff_minutes < min_to_next:
                min_to_next = diff_minutes
        # Check proximity from past events
        else:
            elapsed_minutes = abs(diff_minutes)
            if min_since_last is None or elapsed_minutes < min_since_last:
                min_since_last = elapsed_minutes

        # Check blackout window [-blackout_minutes, +blackout_minutes] around scheduled time
        if abs(diff_minutes) <= blackout_minutes:
            is_in_blackout = True
            active_event_name = event.name

        # Also check blackout window around revision time if applicable
        if event.revised_at is not None:
            rev_sched_utc = event.revised_at.astimezone(timezone.utc) if event.revised_at.tzinfo else event.revised_at.replace(tzinfo=timezone.utc)
            rev_diff_minutes = int((rev_sched_utc - as_of_utc).total_seconds() // 60)
            if abs(rev_diff_minutes) <= blackout_minutes:
                is_in_blackout = True
                active_event_name = f"{event.name} (Revision)"

        # Point-in-Time value resolution (A26)
        rel_utc = event.released_at.astimezone(timezone.utc) if event.released_at.tzinfo else event.released_at.replace(tzinfo=timezone.utc)
        if as_of_utc >= rel_utc:
            # Determine effective value as of as_of_utc
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
    )
