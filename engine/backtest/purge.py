"""Exact dependency-window purging and post-boundary embargo engine."""
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Sequence, Tuple

from engine.backtest.types import PurgeResult, SimulatedTrade


def _to_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class PurgeEngine:
    """
    Applies exact dependency-window purging and embargo exclusion.

    Strict Invariants (P6-20, P6-21, A34):
      1. Every sample has dependency_window = [signal_timestamp, dependency_end_timestamp].
      2. If dependency_end_timestamp >= partition_end, sample crosses boundary -> PURGE.
      3. Samples with signal_timestamp in [embargo_start, embargo_end) -> EMBARGO.
      4. Never purges using arbitrary row counts; uses exact timestamp dependency windows.
    """

    @staticmethod
    def filter_partition(
        trades: Sequence[SimulatedTrade],
        partition_start: datetime,
        partition_end: datetime,
        embargo_duration_seconds: float = 0.0,
        purge_overlapping: bool = True,
        is_post_boundary_segment: bool = False,
    ) -> PurgeResult:
        """
        Filter trades for a chronological partition [partition_start, partition_end).
        """
        p_start_utc = _to_utc(partition_start)
        p_end_utc = _to_utc(partition_end)
        embargo_end_utc = p_start_utc + timedelta(seconds=embargo_duration_seconds) if (is_post_boundary_segment and embargo_duration_seconds > 0) else p_start_utc

        eligible: List[SimulatedTrade] = []
        purged: List[SimulatedTrade] = []
        embargoed: List[SimulatedTrade] = []
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
            dep_end = _to_utc(t.dependency_end_timestamp)

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
