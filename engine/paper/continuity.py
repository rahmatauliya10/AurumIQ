"""14-Day operational continuity gate and interruption tracking for Phase 8."""
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from engine.paper.types import InterruptionCategory, Phase8Status


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


def get_expected_15m_closes(start_time: datetime, end_time: datetime) -> List[datetime]:
    """
    Generate all 15-minute candle close timestamps occurring in (start_time, end_time].
    Closed 15m intervals align at :00, :15, :30, :45 of each hour.
    """
    start_utc = start_time.astimezone(timezone.utc) if start_time.tzinfo else start_time.replace(tzinfo=timezone.utc)
    end_utc = end_time.astimezone(timezone.utc) if end_time.tzinfo else end_time.replace(tzinfo=timezone.utc)

    if start_utc >= end_utc:
        return []

    # Align to the first 15m close strictly greater than start_utc
    t = start_utc.replace(second=0, microsecond=0)
    minute = (t.minute // 15) * 15
    t = t.replace(minute=minute)
    if t <= start_utc:
        t += timedelta(minutes=15)

    closes: List[datetime] = []
    while t <= end_utc:
        closes.append(t)
        t += timedelta(minutes=15)

    return closes


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
      6. Hardened Gate (Section 7): COMPLETE strictly requires:
         A. observation age >= 14 calendar days
         B. unresolved_failures == 0
         C. eligible_cycles_missing == 0
         D. all eligible cycles expected up to evaluation time are accounted for
         E. latest successful eligible observation is not stale beyond permitted grace policy
            when market is open.
    """

    def __init__(
        self,
        window_start: Optional[datetime] = None,
        unresolved_failures: int = 0,
        current_status: Phase8Status = Phase8Status.NOT_STARTED,
        eligible_cycles_expected: int = 0,
        eligible_cycles_observed: int = 0,
        eligible_cycles_missing: int = 0,
        last_successful_evaluation: Optional[datetime] = None,
        max_stale_seconds: float = 3600.0,
    ):
        self.window_start = window_start.astimezone(timezone.utc) if window_start else None
        self.unresolved_failures = unresolved_failures
        self.current_status = current_status
        self.eligible_cycles_expected = eligible_cycles_expected
        self.eligible_cycles_observed = eligible_cycles_observed
        self.eligible_cycles_missing = eligible_cycles_missing
        self.last_successful_evaluation = (
            last_successful_evaluation.astimezone(timezone.utc) if last_successful_evaluation else None
        )
        self.max_stale_seconds = max_stale_seconds

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
        eligible_cycles_expected: Optional[int] = None,
        eligible_cycles_observed: Optional[int] = None,
        eligible_cycles_missing: Optional[int] = None,
        last_successful_evaluation: Optional[datetime] = None,
    ) -> Phase8Status:
        """
        Evaluate whether the 14-day continuity gate is NOT_STARTED, OBSERVING, INTERRUPTED, or COMPLETE.
        Hardened to enforce all Section 7 invariants: age >= 14d, zero unresolved failures,
        zero missing cycles, coverage >= expected, and non-stale during open market hours.
        """
        if self.window_start is None:
            return Phase8Status.NOT_STARTED

        exp = eligible_cycles_expected if eligible_cycles_expected is not None else self.eligible_cycles_expected
        obs = eligible_cycles_observed if eligible_cycles_observed is not None else self.eligible_cycles_observed
        miss = eligible_cycles_missing if eligible_cycles_missing is not None else self.eligible_cycles_missing
        last_eval = (
            last_successful_evaluation.astimezone(timezone.utc)
            if last_successful_evaluation
            else self.last_successful_evaluation
        )

        # Operational failure or missing intervals immediately interrupt the gate
        if has_critical_unresolved_failure or self.unresolved_failures > 0 or miss > 0:
            return Phase8Status.INTERRUPTED

        now_utc = current_time.astimezone(timezone.utc) if current_time.tzinfo else current_time.replace(tzinfo=timezone.utc)
        age_seconds = (now_utc - self.window_start).total_seconds()
        required_seconds = OBSERVATION_WINDOW_CALENDAR_DAYS * 86400

        # Condition A: Age requirement
        if age_seconds < required_seconds:
            return Phase8Status.OBSERVING

        # Condition B: Unresolved failures == 0 (verified above)
        # Condition C: Eligible cycles missing == 0 (verified above)

        # Condition D: Coverage check (all expected cycles must be observed)
        if exp > 0 and obs < exp:
            return Phase8Status.INTERRUPTED

        # Condition E: Staleness check during open market
        if not is_expected_market_closure(now_utc):
            if exp > 0 and last_eval is None:
                return Phase8Status.INTERRUPTED
            if last_eval is not None:
                eval_age = (now_utc - last_eval).total_seconds()
                if eval_age > self.max_stale_seconds:
                    return Phase8Status.INTERRUPTED

        return Phase8Status.COMPLETE

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
