"""
Acceptance Test A06: Macro Event Blackout Gate.
Verifies that any high-impact economic release within the configured blackout window
(e.g. +- 30 minutes) flags is_in_blackout = True and forces signal wait.
"""
from datetime import datetime, timezone
import pytest

from engine.core.types import EventImpact, MacroEvent
from engine.cycles.events import evaluate_macro_event_risk


@pytest.mark.acceptance
def test_a06_high_impact_event_blackout_window():
    """Test that +- 30 minutes around high-impact event (CPI/FOMC) triggers blackout."""
    cpi_event = MacroEvent(
        event_id="CPI-2026-08",
        name="US CPI YoY",
        scheduled_at=datetime(2026, 8, 12, 12, 30, tzinfo=timezone.utc),
        released_at=datetime(2026, 8, 12, 12, 30, tzinfo=timezone.utc),
        initial_value="2.9%",
        impact=EventImpact.HIGH,
    )
    events = [cpi_event]

    # 1. 45 minutes prior (11:45 UTC) -> Outside blackout (minutes_to_next = 45)
    ctx_45m_before = evaluate_macro_event_risk(datetime(2026, 8, 12, 11, 45, tzinfo=timezone.utc), events, blackout_minutes=30)
    assert ctx_45m_before.is_in_blackout is False
    assert ctx_45m_before.minutes_to_next_event == 45

    # 2. 15 minutes prior (12:15 UTC) -> Inside blackout
    ctx_15m_before = evaluate_macro_event_risk(datetime(2026, 8, 12, 12, 15, tzinfo=timezone.utc), events, blackout_minutes=30)
    assert ctx_15m_before.is_in_blackout is True
    assert ctx_15m_before.active_event_name == "US CPI YoY"
    assert ctx_15m_before.minutes_to_next_event == 15

    # 3. Exact event time (12:30 UTC) -> Inside blackout
    ctx_at_event = evaluate_macro_event_risk(datetime(2026, 8, 12, 12, 30, tzinfo=timezone.utc), events, blackout_minutes=30)
    assert ctx_at_event.is_in_blackout is True
    assert ctx_at_event.minutes_to_next_event == 0
    assert ctx_at_event.point_in_time_value == "2.9%"

    # 4. 20 minutes after (12:50 UTC) -> Inside blackout
    ctx_20m_after = evaluate_macro_event_risk(datetime(2026, 8, 12, 12, 50, tzinfo=timezone.utc), events, blackout_minutes=30)
    assert ctx_20m_after.is_in_blackout is True
    assert ctx_20m_after.minutes_since_last_event == 20

    # 5. 35 minutes after (13:05 UTC) -> Outside blackout
    ctx_35m_after = evaluate_macro_event_risk(datetime(2026, 8, 12, 13, 5, tzinfo=timezone.utc), events, blackout_minutes=30)
    assert ctx_35m_after.is_in_blackout is False
    assert ctx_35m_after.minutes_since_last_event == 35


@pytest.mark.acceptance
def test_a06_low_impact_event_does_not_trigger_blackout():
    """Test that low-impact economic releases do not trigger the blackout gate."""
    low_impact = MacroEvent(
        event_id="API-CRUDE-2026",
        name="API Weekly Crude Stock",
        scheduled_at=datetime(2026, 8, 12, 12, 30, tzinfo=timezone.utc),
        released_at=datetime(2026, 8, 12, 12, 30, tzinfo=timezone.utc),
        initial_value="-1.2M",
        impact=EventImpact.LOW,
    )
    ctx = evaluate_macro_event_risk(datetime(2026, 8, 12, 12, 30, tzinfo=timezone.utc), [low_impact], blackout_minutes=30)
    assert ctx.is_in_blackout is False
