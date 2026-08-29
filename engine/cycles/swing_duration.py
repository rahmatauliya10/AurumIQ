"""Swing duration and correction maturity analysis module with knowable age and effective sample guard."""
from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Sequence, Tuple
import bisect
import re

from engine.core.types import (
    CandleData,
    SampleEvaluation,
    SampleQuality,
    StructureResult,
    SwingDurationContext,
    SwingPoint,
)


def timeframe_to_seconds(timeframe: str) -> int:
    """
    Parse timeframe string into exact duration in seconds.
    Raises ValueError on unsupported or invalid timeframe formats.
    """
    tf_clean = timeframe.lower().strip()
    match = re.match(r"^(\d+)([mhdwn])$", tf_clean)
    if not match:
        raise ValueError(f"Unsupported timeframe: '{timeframe}'. Expected format like '1m', '5m', '15m', '1h', '4h', '1d', '1w'.")

    val = int(match.group(1))
    unit = match.group(2)
    if unit == "m":
        return val * 60
    elif unit == "h":
        return val * 3600
    elif unit == "d":
        return val * 86400
    elif unit == "w":
        return val * 604800
    else:
        raise ValueError(f"Unsupported timeframe unit: '{unit}' in '{timeframe}'")


def calculate_swing_duration(
    latest_candle: CandleData,
    structure: StructureResult,
    timeframe: str = "15m",
    historical_durations: Optional[Sequence[int]] = None,
    effective_n: Optional[float] = None,
    sample_eval: Optional[SampleEvaluation] = None,
) -> SwingDurationContext:
    """
    Calculate causal swing duration maturity and age percentiles.

    Causality Invariants (P3A-07):
      - market_age: Elapsed time/bars from physical swing peak/trough (timestamp).
      - known_age: Elapsed time/bars from confirmation point (detected_at).
      - Scoring and maturity decisions MUST strictly evaluate knowable age (detected_at).

    Statistical Effective Sample Guard (P3A-09, P3A-15):
      - Evaluates effective sample size (n_eff), discounting overlapping and clustered samples.
      - If effective_n < 30.0 or sample_eval.is_blocked: maturity_score = 0.0 (INSUFFICIENT).
      - Percentile may remain descriptive, but confidence score strictly defaults to 0.0.
      - Zero hardcoded fallback distributions allowed.
    """
    tf_sec = max(1, timeframe_to_seconds(timeframe))

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
            effective_n=0.0,
        )

    last_swing = structure.swings[-1]
    as_of = latest_candle.timestamp_close if latest_candle.is_closed else latest_candle.timestamp_open

    # 1. Market age (from formation timestamp)
    market_delta_sec = max(0.0, (as_of - last_swing.timestamp).total_seconds())
    market_hours = float(round(market_delta_sec / 3600.0, 2))
    market_bars = max(0, int(market_delta_sec // tf_sec))

    # 2. Known age (from detected_at confirmation timestamp)
    known_delta_sec = max(0.0, (as_of - last_swing.detected_at).total_seconds())
    known_hours = float(round(known_delta_sec / 3600.0, 2))
    known_bars = max(0, int(known_delta_sec // tf_sec))

    # 3. Determine effective N and sample quality
    raw_n = len(historical_durations) if historical_durations else 0
    eff_n: float = 0.0
    if sample_eval is not None:
        eff_n = sample_eval.effective_n
    elif effective_n is not None:
        eff_n = float(effective_n)
    else:
        eff_n = float(raw_n)

    # 4. Statistical Effective Sample Guard (P3A-15)
    if not historical_durations or raw_n < 30 or eff_n < 30.0:
        # Descriptive percentile can be calculated if raw_n >= 10, but maturity score is BLOCKED (0.0)
        percentile = None
        if historical_durations and raw_n >= 10:
            durations = sorted(historical_durations)
            pos = bisect.bisect_left(durations, known_bars)
            percentile = float(round(min(100.0, max(0.0, (pos / raw_n) * 100.0)), 2))

        return SwingDurationContext(
            market_age_bars=market_bars,
            market_age_hours=market_hours,
            known_age_bars=known_bars,
            known_age_hours=known_hours,
            pullback_age_percentile=percentile,
            is_mature=False,
            maturity_score=0.0,
            sample_quality=SampleQuality.INSUFFICIENT,
            effective_n=eff_n,
        )

    # Calculate empirical percentile using knowable age
    durations = sorted(historical_durations)
    pos = bisect.bisect_left(durations, known_bars)
    percentile = float(round((pos / raw_n) * 100.0, 2))
    percentile = min(100.0, max(0.0, percentile))

    # Determine sample quality and weight multiplier based on effective N
    if eff_n < 60.0:
        sample_quality = SampleQuality.LOW
        weight_mult = 0.5
    elif eff_n < 100.0:
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
        effective_n=eff_n,
    )
