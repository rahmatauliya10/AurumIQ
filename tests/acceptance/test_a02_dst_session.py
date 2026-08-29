"""
Acceptance Test A02: DST Trading Session Label Integrity.
Verifies that session labeling remains strictly correct across London & New York
Daylight Saving Time (DST) clock shifts without static UTC lookahead errors.
"""
from datetime import datetime, timezone
import pytest

from engine.core.types import SessionType
from engine.cycles.session import classify_session


@pytest.mark.acceptance
def test_a02_dst_session_labeling_london_winter_vs_summer():
    """
    Test London Morning session across winter (GMT, UTC+0) and summer (BST, UTC+1).
    London Morning is 08:00 - 13:00 London local time.
    """
    # 1. Winter (January 15, 2026 - GMT, UTC+0)
    # 09:30 UTC == 09:30 London local -> LONDON
    dt_winter = datetime(2026, 1, 15, 9, 30, tzinfo=timezone.utc)
    ctx_winter = classify_session(dt_winter)
    assert ctx_winter.session == SessionType.LONDON
    assert ctx_winter.is_high_liquidity is True
    assert "09:30:00 GMT" in ctx_winter.local_times["London"]

    # 2. Summer (July 15, 2026 - BST, UTC+1)
    # 08:30 UTC == 09:30 London local -> LONDON
    dt_summer = datetime(2026, 7, 15, 8, 30, tzinfo=timezone.utc)
    ctx_summer = classify_session(dt_summer)
    assert ctx_summer.session == SessionType.LONDON
    assert ctx_summer.is_high_liquidity is True
    assert "09:30:00 BST" in ctx_summer.local_times["London"]

    # 07:30 UTC in Summer is 08:30 London local -> LONDON
    # 07:30 UTC in Winter is 07:30 London local -> LONDON_PREOPEN
    dt_winter_pre = datetime(2026, 1, 15, 7, 30, tzinfo=timezone.utc)
    assert classify_session(dt_winter_pre).session == SessionType.LONDON_PREOPEN

    dt_summer_pre = datetime(2026, 7, 15, 6, 30, tzinfo=timezone.utc)
    assert classify_session(dt_summer_pre).session == SessionType.LONDON_PREOPEN


@pytest.mark.acceptance
def test_a02_dst_session_overlap_us_dst_transition_gap():
    """
    Test London / NY overlap during the US/UK DST desynchronization gap in March 2026.
    US enters EDT (UTC-4) on March 8, 2026, while UK enters BST (UTC+1) on March 29, 2026.
    Between March 8 and March 29, US is UTC-4 and UK is UTC+0 (5 hour difference).
    """
    # March 18, 2026: US is EDT (UTC-4), UK is GMT (UTC+0)
    # London local 13:30 (UTC 13:30) is NY local 09:30 (EDT) -> LONDON_NY_OVERLAP
    dt_gap = datetime(2026, 3, 18, 13, 30, tzinfo=timezone.utc)
    ctx_gap = classify_session(dt_gap)
    assert ctx_gap.session == SessionType.LONDON_NY_OVERLAP
    assert ctx_gap.is_high_liquidity is True
    assert "13:30:00 GMT" in ctx_gap.local_times["London"]
    assert "09:30:00 EDT" in ctx_gap.local_times["New_York"]

    # Normal summer overlap (June 10, 2026: UK BST UTC+1, US EDT UTC-4)
    # London local 14:00 (UTC 13:00) is NY local 09:00 (EDT) -> LONDON_NY_OVERLAP
    dt_summer_overlap = datetime(2026, 6, 10, 13, 0, tzinfo=timezone.utc)
    ctx_summer_overlap = classify_session(dt_summer_overlap)
    assert ctx_summer_overlap.session == SessionType.LONDON_NY_OVERLAP
    assert "14:00:00 BST" in ctx_summer_overlap.local_times["London"]
    assert "09:00:00 EDT" in ctx_summer_overlap.local_times["New_York"]


@pytest.mark.acceptance
def test_a02_asia_and_us_late_session_boundaries():
    """Test Asia session and US Late session boundaries."""
    # Tokyo active hours (02:00 UTC == 11:00 JST) -> ASIA
    dt_asia = datetime(2026, 4, 10, 2, 0, tzinfo=timezone.utc)
    ctx_asia = classify_session(dt_asia)
    assert ctx_asia.session == SessionType.ASIA
    assert ctx_asia.is_high_liquidity is False

    # US Late hours (22:30 UTC in winter == 17:30 EST) -> US_LATE
    dt_us_late = datetime(2026, 1, 15, 22, 30, tzinfo=timezone.utc)
    ctx_us_late = classify_session(dt_us_late)
    assert ctx_us_late.session == SessionType.US_LATE
    assert ctx_us_late.is_high_liquidity is False
