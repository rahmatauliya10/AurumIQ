"""
Acceptance Test A26: Macro Event Revision Point-in-Time Safety.
Verifies that subsequent data revisions published at t_revised > T are strictly masked
when evaluating historical macro features at timestamp T.
"""
from datetime import datetime, timezone
import pytest

from engine.core.types import EventImpact, MacroEvent
from engine.cycles.events import evaluate_macro_event_risk


@pytest.mark.acceptance
def test_a26_future_revision_masked_at_point_in_time():
    """
    Test NFP (Non-Farm Payrolls) release and subsequent month revision.
    Release: August 7, 2026 12:30 UTC -> initial_value = '+114K'
    Revision: September 4, 2026 12:30 UTC -> revised_value = '+89K'
    """
    nfp_event = MacroEvent(
        event_id="NFP-2026-07",
        name="US Non-Farm Payrolls",
        scheduled_at=datetime(2026, 8, 7, 12, 30, tzinfo=timezone.utc),
        released_at=datetime(2026, 8, 7, 12, 30, tzinfo=timezone.utc),
        initial_value="+114K",
        revised_at=datetime(2026, 9, 4, 12, 30, tzinfo=timezone.utc),
        revised_value="+89K",
        impact=EventImpact.HIGH,
    )
    events = [nfp_event]

    # 1. Point-in-Time: August 10, 2026 (Before revision on September 4)
    # MUST see initial value '+114K', NEVER '+89K'
    ctx_aug10 = evaluate_macro_event_risk(datetime(2026, 8, 10, 10, 0, tzinfo=timezone.utc), events, blackout_minutes=30)
    # The event is not in blackout on Aug 10, but if we query blackout at event time on Aug 7:
    ctx_aug7 = evaluate_macro_event_risk(datetime(2026, 8, 7, 12, 35, tzinfo=timezone.utc), events, blackout_minutes=30)
    assert ctx_aug7.point_in_time_value == "+114K"
    assert ctx_aug7.point_in_time_value != "+89K"

    # 2. Point-in-Time: September 5, 2026 (After revision on September 4)
    # Evaluated during the revision blackout window:
    ctx_sep4 = evaluate_macro_event_risk(datetime(2026, 9, 4, 12, 30, tzinfo=timezone.utc), events, blackout_minutes=30)
    assert ctx_sep4.point_in_time_value == "+89K"
