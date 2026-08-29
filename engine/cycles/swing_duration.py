"""Swing duration and correction maturity analysis module with knowable age and sample guard."""
from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Sequence, Tuple
import bisect

from engine.core.types import (
    CandleData,
    SampleQuality,
    StructureResult,
    SwingDurationContext,
    SwingPoint,
)


def timeframe_to_seconds(timeframe: str) -> int:
    """Parse timeframe string into exact duration in seconds."""
    tf_clean = timeframe.lower().strip()
    if tf_clean.endswith("m"):
        return int(tf_clean[:-1]) * 60
    elif tf_clean.endswith("h"):
        return int(tf_clean[:-1]) * 3600
    elif tf_clean.endswith("d"):
        return int(tf_clean[:-1]) * 86400
    elif tf_clean.endswith("w"):
        return int(tf_clean[:-1]) * 604800
    return 900


def calculate_swing_duration(
    latest_candle: CandleData,
    structure: StructureResult,
    timeframe: str = "15m",
    historical_durations: Optional[Sequence[int]] = None,
) -> SwingDurationContext:
    """
    Calculate causal swing duration maturity and age percentiles.

    Causality Invariants:
      - market_age: Elapsed time/bars from physical swing peak/trough (timestamp).
      - known_age: Elapsed time/bars from confirmation point (detected_at).
      - Scoring and maturity decisions MUST strictly evaluate knowable age (detected_at).

    Statistical Sample Guard:
      - If historical_durations is None or N < 30 -> maturity_score = 0.0, is_mature = False.
      - Zero hardcoded fallback distributions allowed.
    """
    if not structure.swings:
        return SwingDurationContext(
            market_age_bars=0,
            market_age_hours=0.0,
            known_age_bars=0,
            known_age_hours=0.0,
            pullback_age_percentile=None,
            is_mature=False,
            maturity_score=0.0,
            sample_quality=SampleQuality.INSUFFICIENT,
        )

    last_swing = structure.swings[-1]
    tf_sec = max(1, timeframe_to_seconds(timeframe))
    as_of = latest_candle.timestamp_close if latest_candle.is_closed else latest_candle.timestamp_open

    # 1. Market age (from formation timestamp)
    market_delta_sec = max(0.0, (as_of - last_swing.timestamp).total_seconds())
    market_hours = float(round(market_delta_sec / 3600.0, 2))
    market_bars = max(0, int(market_delta_sec // tf_sec))

    # 2. Known age (from detected_at confirmation timestamp)
    known_delta_sec = max(0.0, (as_of - last_swing.detected_at).total_seconds())
    known_hours = float(round(known_delta_sec / 3600.0, 2))
    known_bars = max(0, int(known_delta_sec // tf_sec))

    # 3. Statistical Sample Guard evaluation
    if not historical_durations or len(historical_durations) < 30:
        return SwingDurationContext(
            market_age_bars=market_bars,
            market_age_hours=market_hours,
            known_age_bars=known_bars,
            known_age_hours=known_hours,
            pullback_age_percentile=None,
            is_mature=False,
            maturity_score=0.0,
            sample_quality=SampleQuality.INSUFFICIENT,
        )

    # Calculate empirical percentile using knowable age
    durations = sorted(historical_durations)
    n = len(durations)
    pos = bisect.bisect_left(durations, known_bars)
    percentile = float(round((pos / n) * 100.0, 2))
    percentile = min(100.0, max(0.0, percentile))

    # Determine sample quality based on N
    if n < 60:
        sample_quality = SampleQuality.LOW
        weight_mult = 0.5
    elif n < 100:
        sample_quality = SampleQuality.MEDIUM
        weight_mult = 0.8
    else:
        sample_quality = SampleQuality.HIGH
        weight_mult = 1.0

    is_mature = (75.0 <= percentile <= 95.0)

    # Compute maturity readiness score (Max 20.0 points in Phase 3A)
    if 75.0 <= percentile <= 90.0:
        raw_score = 20.0
    elif 50.0 <= percentile < 75.0:
        raw_score = 15.0
    elif 25.0 <= percentile < 50.0:
        raw_score = 10.0
    elif percentile > 90.0:
        raw_score = 8.0
    else:
        raw_score = 5.0

    maturity_score = float(round(raw_score * weight_mult, 2))

    return SwingDurationContext(
        market_age_bars=market_bars,
        market_age_hours=market_hours,
        known_age_bars=known_bars,
        known_age_hours=known_hours,
        pullback_age_percentile=percentile,
        is_mature=is_mature,
        maturity_score=maturity_score,
        sample_quality=sample_quality,
    )
