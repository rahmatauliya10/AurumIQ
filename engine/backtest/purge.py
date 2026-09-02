"""Dependency-window purging and post-boundary embargo evaluation engine."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional, Sequence, Tuple

from engine.backtest.types import PurgeResult, SimulatedTrade


def _to_utc(dt: datetime) -> datetime:
    """Normalize datetime as UTC."""
    if dt is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class PurgeEngine:
    """
    Evaluates partition boundaries to purge overlapping trades and enforce post-boundary embargo.

    Strict Invariants:
      1. Purge Overlapping: Any trade whose dependency window extends >= partition_end is purged.
      2. Embargo Exclusion: Any trade in a post-boundary segment within [partition_start, partition_start + embargo) is excluded.
      3. Provenance Integrity: Filter results report exact counts of eligible, purged, and embargoed trades.
    """

    @classmethod
    def filter_partition(
        cls,
        trades: Sequence[Any],
        partition_start: datetime,
        partition_end: datetime,
        embargo_duration_seconds: float = 0.0,
        purge_overlapping: bool = True,
        is_post_boundary_segment: bool = False,
    ) -> PurgeResult:
        """
        Filter trades for a specific partition window with purging and embargo applied.
        """
        p_start_utc = _to_utc(partition_start)
        p_end_utc = _to_utc(partition_end)
        from datetime import timedelta
        embargo_end_utc = p_start_utc + timedelta(seconds=embargo_duration_seconds)

        eligible = []
        purged = []
        embargoed = []
        total_input = 0

        for t in trades:
            sig_ts = _to_utc(t.signal_timestamp)

            # Check if signal belongs to this partition [partition_start, partition_end)
            if sig_ts < p_start_utc or sig_ts >= p_end_utc:
                continue

            total_input += 1

            # 1. Embargo Check (P6-21)
            # If signal falls in the post-boundary embargo window [p_start, embargo_end)
            if is_post_boundary_segment and embargo_duration_seconds > 0:
                if sig_ts < embargo_end_utc:
                    embargoed.append(t)
                    continue

            # 2. Exact Dependency-Window Purge Check (P6-20, P6-C1)
            dep_end_raw = getattr(t, "dependency_end_timestamp", None)
            if dep_end_raw is None:
                dep_win = getattr(t, "dependency_window", None)
                if dep_win and len(dep_win) >= 2 and dep_win[1] is not None:
                    dep_end_raw = dep_win[1]
                else:
                    dep_end_raw = getattr(t, "exit_timestamp", None) or t.signal_timestamp

            dep_end = _to_utc(dep_end_raw)

            if purge_overlapping and dep_end >= p_end_utc:
                # The outcome window crosses the partition boundary into the next segment
                purged.append(t)
                continue

            eligible.append(t)

        return PurgeResult(
            eligible_trades=tuple(eligible),
            purged_trades=tuple(purged),
            embargoed_trades=tuple(embargoed),
            total_input_count=total_input,
        )
