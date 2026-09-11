"""14-Day operational continuity gate and interruption tracking for Phase 8."""
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from engine.paper.types import InterruptionCategory, Phase8OperationalMetrics, Phase8Status


OBSERVATION_WINDOW_CALENDAR_DAYS: int = 14


def is_expected_market_closure(dt: datetime) -> bool:
    """
    Determine if a UTC timestamp falls within scheduled market closure for XAUUSD spot.

    Standard Exness XAUUSD trading hours:
      - Closes Friday 21:00 UTC (Summer) / 22:00 UTC (Winter).
      - Reopens Sunday 21:00 UTC (Summer) / 22:00 UTC (Winter).
      - Daily maintenance break: ~21:00 - 22:00 UTC (approx. 1 hour).
    
    A weekend window between Friday 21:00 UTC and Sunday 21:00 UTC is strictly EXPECTED_MARKET_CLOSURE.
    """
    if dt.tzinfo is None:
        raise ValueError("Timestamp must be timezone-aware UTC.")

    utc_dt = dt.astimezone(timezone.utc)
    weekday = utc_dt.weekday()  # Monday=0, Sunday=6

    # Saturday is entirely closed
    if weekday == 5:
        return True

    # Friday after 21:00 UTC is closed
    if weekday == 4 and utc_dt.hour >= 21:
        return True

    # Sunday before 21:00 UTC is closed
    if weekday == 6 and utc_dt.hour < 21:
        return True

    return False


class Phase8ContinuityTracker:
    """
    Tracks the 14-day operational continuity gate and verifies system health.

    Strict Invariants:
      1. Window starts on the first valid live paper observation cycle (NOT backdated).
      2. 14 calendar days required before status can be evaluated for COMPLETE.
      3. EXPECTED_MARKET_CLOSURE does not increment failure counts or interrupt the phase.
      4. Operational failures (collector, database, stale data outside closure, engine errors)
         increment failure counts and record interruptions.
      5. COMPLETE status is strictly forbidden if window_age < 14 calendar days or unresolved
         critical failures exist.
    """

    def __init__(
        self,
        window_start: Optional[datetime] = None,
        unresolved_failures: int = 0,
        current_status: Phase8Status = Phase8Status.NOT_STARTED,
    ):
        self.window_start = window_start.astimezone(timezone.utc) if window_start else None
        self.unresolved_failures = unresolved_failures
        self.current_status = current_status

    def calculate_day_progress(self, current_time: datetime) -> Tuple[int, int, float]:
        """
        Calculate (current_day_number, completed_calendar_days, age_seconds).

        current_day_number is 1-indexed (Day 1..14).
        """
        if not self.window_start:
            return 0, 0, 0.0

        now_utc = current_time.astimezone(timezone.utc) if current_time.tzinfo else current_time.replace(tzinfo=timezone.utc)
        if now_utc < self.window_start:
            return 1, 0, 0.0

        age_seconds = (now_utc - self.window_start).total_seconds()
        completed_days = int(age_seconds // 86400)
        current_day = min(completed_days + 1, OBSERVATION_WINDOW_CALENDAR_DAYS)

        return current_day, completed_days, age_seconds

    def evaluate_gate_status(
        self,
        current_time: datetime,
        has_critical_unresolved_failure: bool = False,
    ) -> Phase8Status:
        """
        Evaluate whether the 14-day continuity gate is NOT_STARTED, OBSERVING, INTERRUPTED, or COMPLETE.
        """
        if self.window_start is None:
            return Phase8Status.NOT_STARTED

        if has_critical_unresolved_failure or self.unresolved_failures > 0:
            return Phase8Status.INTERRUPTED

        now_utc = current_time.astimezone(timezone.utc) if current_time.tzinfo else current_time.replace(tzinfo=timezone.utc)
        age_seconds = (now_utc - self.window_start).total_seconds()
        required_seconds = OBSERVATION_WINDOW_CALENDAR_DAYS * 86400

        if age_seconds >= required_seconds and self.unresolved_failures == 0:
            return Phase8Status.COMPLETE

        return Phase8Status.OBSERVING

    def record_interruption(
        self,
        category: InterruptionCategory,
        timestamp: datetime,
    ) -> bool:
        """
        Record an interruption event.

        Returns True if this is an operational failure, False if expected market closure.
        """
        if category == InterruptionCategory.EXPECTED_MARKET_CLOSURE or is_expected_market_closure(timestamp):
            # Expected market closure: logged as normal operational cycle event, not failure
            return False

        self.unresolved_failures += 1
        self.current_status = Phase8Status.INTERRUPTED
        return True
