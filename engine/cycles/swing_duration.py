"""Swing duration and correction maturity analysis module."""
from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Sequence, Tuple
import bisect

from engine.core.types import CandleData, StructureResult, SwingDurationContext, SwingPoint


# Default baseline historical pullback duration distribution in 15m bars (empirical gold baseline)
DEFAULT_HISTORICAL_SWING_DURATIONS = [
    4, 5, 6, 7, 8, 9, 10, 12, 14, 16, 18, 20, 22, 25, 28, 32, 36, 40, 45, 52, 60, 75
]


def calculate_swing_duration(
    latest_candle: CandleData,
    structure: StructureResult,
    historical_durations: Optional[Sequence[int]] = None,
) -> SwingDurationContext:
    """
    Calculate causal swing duration maturity and age percentiles.

    Causality Rule:
      - Swing duration is measured from the confirmed swing timestamp up to latest_candle.timestamp_close.
      - Never look ahead or project future swing endpoints.

    Maturity Percentiles:
      - P10..P25: Young / nascent move.
      - P25..P50: Developing move.
      - P50..P75: Mature move.
      - P75..P90: Highly mature correction ready for structural resolution.
      - > P90: Extended / potential exhaustion.
    """
    if not structure.swings:
        return SwingDurationContext(
            bars_since_last_swing=0,
            hours_since_last_swing=0.0,
            active_pullback_bars=0,
            pullback_age_percentile=0.0,
            is_mature=False,
            maturity_score=0.0,
        )

    last_swing = structure.swings[-1]
    
    # Calculate elapsed time from swing peak/trough to current closed bar
    delta_seconds = max(0.0, (latest_candle.timestamp_close - last_swing.timestamp).total_seconds())
    hours_elapsed = float(round(delta_seconds / 3600.0, 2))

    # Approximate bar count from elapsed time if uniform (e.g. 15m = 900s)
    # Or exact bar count from index difference if available
    bars_elapsed = max(0, int(delta_seconds // 900))  # standard 15m bar unit

    durations = sorted(historical_durations or DEFAULT_HISTORICAL_SWING_DURATIONS)
    n = len(durations)

    if n > 0 and bars_elapsed > 0:
        pos = bisect.bisect_left(durations, bars_elapsed)
        percentile = float(round((pos / n) * 100.0, 2))
    else:
        percentile = 0.0

    percentile = min(100.0, max(0.0, percentile))
    is_mature = 75.0 <= percentile <= 95.0

    # Score calculation (Max 20.0 points in Phase 3A weight table)
    # Peak score given to mature corrections between P70 and P90
    if 75.0 <= percentile <= 90.0:
        score = 20.0
    elif 50.0 <= percentile < 75.0:
        score = 15.0
    elif 25.0 <= percentile < 50.0:
        score = 10.0
    elif percentile > 90.0:
        score = 8.0  # Extended / potential regime shift
    else:
        score = 5.0  # Young move

    return SwingDurationContext(
        bars_since_last_swing=bars_elapsed,
        hours_since_last_swing=hours_elapsed,
        active_pullback_bars=bars_elapsed,
        pullback_age_percentile=percentile,
        is_mature=is_mature,
        maturity_score=score,
    )
