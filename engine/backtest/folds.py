"""Chronological walk-forward fold generator strictly preserving temporal ordering."""
from datetime import datetime, timedelta, timezone
from typing import List, Sequence

from engine.backtest.types import FoldSpec, WalkForwardConfig


def _to_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class ChronologicalFoldGenerator:
    """
    Generates deterministic chronological train/validation/OOS folds.

    Strict Invariants (P6-19, P6-23A):
      1. Zero random shuffle: Folds follow chronological arrow of time strictly.
      2. Half-open intervals: [start, end).
      3. No temporal overlap or role reversal: train_end <= val_start <= val_end <= oos_start.
      4. Configurable expanding or rolling window.
      5. Embargo duration explicitly tracked per fold.
    """

    @staticmethod
    def generate_folds(
        start_time: datetime,
        end_time: datetime,
        config: WalkForwardConfig,
    ) -> List[FoldSpec]:
        """Generate chronological fold specifications over [start_time, end_time)."""
        start_utc = _to_utc(start_time)
        end_utc = _to_utc(end_time)

        total_duration = (end_utc - start_utc).total_seconds()
        if total_duration <= 0:
            raise ValueError(f"end_time ({end_utc}) must be strictly after start_time ({start_utc}).")

        num_folds = max(1, config.total_folds)
        folds: List[FoldSpec] = []

        if num_folds == 1:
            # Single fold partition
            train_dur = total_duration * config.train_ratio
            val_dur = total_duration * config.val_ratio
            train_end = start_utc + timedelta(seconds=train_dur)
            val_end = train_end + timedelta(seconds=val_dur)

            spec = FoldSpec(
                fold_id=1,
                train_start=start_utc,
                train_end=train_end,
                val_start=train_end if config.val_ratio > 0 else None,
                val_end=val_end if config.val_ratio > 0 else None,
                oos_start=val_end if config.val_ratio > 0 else train_end,
                oos_end=end_utc,
                embargo_duration_seconds=config.embargo_seconds,
            )
            folds.append(spec)
            return folds

        # Multi-fold chronological slicing
        # Segment the timeline into equal chronological step slices
        # For K folds, allocate sequential step windows
        step_seconds = total_duration / (num_folds + 2)  # Base step unit

        for fold_idx in range(1, num_folds + 1):
            if config.rolling_window:
                train_start = start_utc + timedelta(seconds=(fold_idx - 1) * step_seconds)
            else:
                train_start = start_utc  # Expanding window

            train_end = start_utc + timedelta(seconds=(fold_idx + 1) * step_seconds)

            if config.val_ratio > 0:
                val_start = train_end
                val_end = val_start + timedelta(seconds=0.5 * step_seconds)
                oos_start = val_end
            else:
                val_start = None
                val_end = None
                oos_start = train_end

            oos_end = min(end_utc, oos_start + timedelta(seconds=step_seconds))

            spec = FoldSpec(
                fold_id=fold_idx,
                train_start=train_start,
                train_end=train_end,
                val_start=val_start,
                val_end=val_end,
                oos_start=oos_start,
                oos_end=oos_end,
                embargo_duration_seconds=config.embargo_seconds,
            )
            folds.append(spec)

        return folds
