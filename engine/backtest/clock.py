"""Point-in-time replay clock ensuring strictly chronological timeline progression."""
from datetime import datetime, timezone
from typing import Iterator, List, Sequence


class ReplayClock:
    """
    Chronological timeline stepper.

    Strict Invariants:
      1. Monotonic strictly increasing progression.
      2. No future timestamp generation or jumping backward.
      3. Point-in-time reference for historical decision evaluation.
    """

    def __init__(self, timestamps: Sequence[datetime]):
        if not timestamps:
            raise ValueError("ReplayClock requires at least one timestamp.")

        # Ensure all timestamps are UTC-aware and sorted strictly
        utc_ts: List[datetime] = []
        for ts in timestamps:
            t = ts.astimezone(timezone.utc) if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
            utc_ts.append(t)

        utc_ts = sorted(list(set(utc_ts)))
        self._timestamps: Sequence[datetime] = utc_ts
        self._current_index: int = 0

    @property
    def total_steps(self) -> int:
        return len(self._timestamps)

    @property
    def current_time(self) -> datetime:
        return self._timestamps[self._current_index]

    def __iter__(self) -> Iterator[datetime]:
        for i, ts in enumerate(self._timestamps):
            self._current_index = i
            yield ts
