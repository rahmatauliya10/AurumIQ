"""Trading session cycle detection with strict zoneinfo DST awareness and empirical statistical expectancy (A02)."""
from datetime import datetime, time, timezone
from typing import Dict, Optional, Mapping, Tuple
from zoneinfo import ZoneInfo

from engine.core.types import (
    RegimeType,
    SampleQuality,
    SessionContext,
    SessionExpectancyEntry,
    SessionType,
)
from engine.cycles.profile import CalibrationStatus, Cycle3AProfile


# Standard financial center timezones
TZ_TOKYO = ZoneInfo("Asia/Tokyo")
TZ_LONDON = ZoneInfo("Europe/London")
TZ_NEW_YORK = ZoneInfo("America/New_York")


def classify_session(
    timestamp: datetime,
    regime: Optional[RegimeType] = None,
    expectancy_table: Optional[Mapping[Tuple[SessionType, RegimeType], SessionExpectancyEntry]] = None,
    profile: Optional[Cycle3AProfile] = None,
) -> SessionContext:
    """
    Classify the market trading session for a point-in-time timestamp using local timezones
    and evaluate empirical expectancy conditioned on (Session, Regime) with Sample Guard.

    Acceptance Rule A02 (DST Session Integrity):
      Never hard-code UTC offsets. Explicit timezone conversions via zoneinfo
      handle daylight saving time (DST) shifts in London and New York accurately.

    Statistical Expectancy & Significance Rule (P3A-06, P3A-14):
      - Zero hardcoding of expectancy scores.
      - If no historical table or bucket effective_n < min_eff_n or NOT is_statistically_significant:
        expectancy_score = 0.0 (INSUFFICIENT).
      - Positive score is only granted when empirical evidence meets minimum sample threshold
        AND is verified statistically significant AND profile production scoring is enabled.
      - If profile is uncalibrated or candidate (PENDING_DATA / CANDIDATE_NOT_FROZEN),
        strictly returns expectancy_score = 0.0.
    """
    # Ensure timezone awareness (assume UTC if naive)
    if timestamp.tzinfo is None:
        dt_utc = timestamp.replace(tzinfo=timezone.utc)
    else:
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
    # 2. London Morning (08:00 - 13:00 London local)
    elif in_range(t_london, time(8, 0), time(13, 0)):
        session = SessionType.LONDON
        progress = calc_progress(t_london, 8.0, 13.0)
        is_high_liq = True
    # 3. London Pre-open (07:00 - 08:00 London local)
    elif in_range(t_london, time(7, 0), time(8, 0)):
        session = SessionType.LONDON_PREOPEN
        progress = calc_progress(t_london, 7.0, 8.0)
        is_high_liq = False
    # 4. New York Afternoon (11:30 - 17:00 NY local)
    elif in_range(t_ny, time(11, 30), time(17, 0)):
        session = SessionType.NEW_YORK
        progress = calc_progress(t_ny, 11.5, 17.0)
        is_high_liq = True
    # 5. US Late / Evening (17:00 - 20:00 NY local)
    elif in_range(t_ny, time(17, 0), time(20, 0)):
        session = SessionType.US_LATE
        progress = calc_progress(t_ny, 17.0, 20.0)
        is_high_liq = False
    # 6. Asia Session (09:00 - 17:00 Tokyo local or default overnight)
    else:
        session = SessionType.ASIA
        if in_range(t_tokyo, time(9, 0), time(17, 0)):
            progress = calc_progress(t_tokyo, 9.0, 17.0)
        else:
            progress = 50.0  # Overnight inter-session baseline
        is_high_liq = False

    # --- Empirical Statistical Expectancy & Significance Calculation ---
    expectancy_score = 0.0
    sample_quality = SampleQuality.INSUFFICIENT
    effective_n = 0.0

    # Profile governance check: if production scoring is not enabled, strictly return 0.0
    if profile is not None and not profile.is_production_scoring_enabled:
        return SessionContext(
            session=session,
            progress_pct=progress,
            is_high_liquidity=is_high_liq,
            local_times=local_times,
            expectancy_score=0.0,
            sample_quality=SampleQuality.INSUFFICIENT,
            effective_n=0.0,
        )

    # Determine effective expectancy table
    table = expectancy_table
    if table is None and profile is not None and profile.is_production_scoring_enabled:
        table = profile.session_expectancy_table

    if profile is not None:
        min_eff_n = profile.session_min_effective_n
        max_score = profile.session_max_score
        exp_multiplier = profile.session_expectancy_multiplier
    else:
        # Legacy default parameters (for historical unprofiled calls)
        min_eff_n = 30.0
        max_score = 15.0
        exp_multiplier = 30.0

    # If any required threshold is None, empirical scoring cannot execute
    if min_eff_n is None or max_score is None or exp_multiplier is None:
        return SessionContext(
            session=session,
            progress_pct=progress,
            is_high_liquidity=is_high_liq,
            local_times=local_times,
            expectancy_score=0.0,
            sample_quality=SampleQuality.INSUFFICIENT,
            effective_n=0.0,
        )

    if table and regime:
        key = (session, regime)
        entry = table.get(key)
        if entry:
            effective_n = entry.effective_n
            # P3A-14: Significance Gate - must be statistically significant AND effective_n >= min_eff_n
            if effective_n < min_eff_n or not entry.is_statistically_significant:
                sample_quality = SampleQuality.INSUFFICIENT
                weight_mult = 0.0
            elif effective_n < 60.0:
                sample_quality = SampleQuality.LOW
                weight_mult = 0.5
            elif effective_n < 100.0:
                sample_quality = SampleQuality.MEDIUM
                weight_mult = 0.8
            else:
                sample_quality = SampleQuality.HIGH
                weight_mult = 1.0

            # Scale positive expectancy up to max score
            if entry.expectancy_r > 0 and entry.is_statistically_significant and weight_mult > 0:
                raw_exp_score = min(max_score, entry.expectancy_r * exp_multiplier)
                expectancy_score = float(round(raw_exp_score * weight_mult, 2))
            else:
                expectancy_score = 0.0

    return SessionContext(
        session=session,
        progress_pct=progress,
        is_high_liquidity=is_high_liq,
        local_times=local_times,
        expectancy_score=expectancy_score,
        sample_quality=sample_quality,
        effective_n=effective_n,
    )
