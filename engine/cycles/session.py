"""Trading session cycle detection with strict zoneinfo DST awareness (A02)."""
from datetime import datetime, time
from typing import Dict, Optional
from zoneinfo import ZoneInfo

from engine.core.types import SessionContext, SessionType


# Standard financial center timezones
TZ_TOKYO = ZoneInfo("Asia/Tokyo")
TZ_LONDON = ZoneInfo("Europe/London")
TZ_NEW_YORK = ZoneInfo("America/New_York")


def classify_session(timestamp: datetime) -> SessionContext:
    """
    Classify the market trading session for a point-in-time timestamp using local timezones.

    Acceptance Rule A02:
      Never hard-code UTC offsets. Explicit timezone conversions via zoneinfo
      handle daylight saving time (DST) shifts in London and New York accurately.

    Session Windows (Local Times):
      - LONDON_PREOPEN:    07:00 - 08:00 London local
      - LONDON:            08:00 - 13:00 London local
      - LONDON_NY_OVERLAP: 13:00 - 16:30 London local / 08:00 - 11:30 NY local
      - NEW_YORK:          11:30 - 17:00 NY local
      - US_LATE:           17:00 - 20:00 NY local
      - ASIA:              09:00 - 17:00 Tokyo local (default/off-hours global anchor)
    """
    # Ensure timezone awareness (assume UTC if naive)
    if timestamp.tzinfo is None:
        from datetime import timezone
        dt_utc = timestamp.replace(tzinfo=timezone.utc)
    else:
        from datetime import timezone
        dt_utc = timestamp.astimezone(timezone.utc)

    # Convert to local timezone representations
    dt_london = dt_utc.astimezone(TZ_LONDON)
    dt_ny = dt_utc.astimezone(TZ_NEW_YORK)
    dt_tokyo = dt_utc.astimezone(TZ_TOKYO)

    t_london = dt_london.time()
    t_ny = dt_ny.time()
    t_tokyo = dt_tokyo.time()

    local_times = {
        "UTC": dt_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "London": dt_london.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "New_York": dt_ny.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "Tokyo": dt_tokyo.strftime("%Y-%m-%d %H:%M:%S %Z"),
    }

    # Helper for time range check [start, end)
    def in_range(t: time, start: time, end: time) -> bool:
        return start <= t < end

    # Helper for progress calculation
    def calc_progress(t: time, start_h: float, end_h: float) -> float:
        curr_h = t.hour + t.minute / 60.0 + t.second / 3600.0
        total_duration = end_h - start_h
        if total_duration <= 0:
            return 0.0
        pct = ((curr_h - start_h) / total_duration) * 100.0
        return float(round(max(0.0, min(100.0, pct)), 2))

    # Priority-based local window classification:
    # 1. London/NY Overlap (13:00 - 16:30 London local / 08:00 - 11:30 NY local)
    if in_range(t_london, time(13, 0), time(16, 30)) and in_range(t_ny, time(8, 0), time(11, 30)):
        session = SessionType.LONDON_NY_OVERLAP
        progress = calc_progress(t_london, 13.0, 16.5)
        is_high_liq = True
        expectancy = 15.0  # Peak global liquidity
    # 2. London Morning (08:00 - 13:00 London local)
    elif in_range(t_london, time(8, 0), time(13, 0)):
        session = SessionType.LONDON
        progress = calc_progress(t_london, 8.0, 13.0)
        is_high_liq = True
        expectancy = 12.0
    # 3. London Pre-open (07:00 - 08:00 London local)
    elif in_range(t_london, time(7, 0), time(8, 0)):
        session = SessionType.LONDON_PREOPEN
        progress = calc_progress(t_london, 7.0, 8.0)
        is_high_liq = False
        expectancy = 6.0
    # 4. New York Afternoon (11:30 - 17:00 NY local)
    elif in_range(t_ny, time(11, 30), time(17, 0)):
        session = SessionType.NEW_YORK
        progress = calc_progress(t_ny, 11.5, 17.0)
        is_high_liq = True
        expectancy = 10.0
    # 5. US Late / Evening (17:00 - 20:00 NY local)
    elif in_range(t_ny, time(17, 0), time(20, 0)):
        session = SessionType.US_LATE
        progress = calc_progress(t_ny, 17.0, 20.0)
        is_high_liq = False
        expectancy = 3.0
    # 6. Asia Session (09:00 - 17:00 Tokyo local or default overnight)
    else:
        session = SessionType.ASIA
        if in_range(t_tokyo, time(9, 0), time(17, 0)):
            progress = calc_progress(t_tokyo, 9.0, 17.0)
        else:
            progress = 50.0  # Overnight inter-session baseline
        is_high_liq = False
        expectancy = 7.0

    return SessionContext(
        session=session,
        progress_pct=progress,
        is_high_liquidity=is_high_liq,
        local_times=local_times,
        expectancy_score=expectancy,
    )
