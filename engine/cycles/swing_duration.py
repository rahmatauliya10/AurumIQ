"""Swing duration and correction maturity analysis module with knowable age and effective sample guard."""
from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Sequence, Tuple
import bisect

from engine.core.types import (
    CandleData,
    SampleEvaluation,
    SampleQuality,
    StructureResult,
    SwingDurationContext,
    SwingPoint,
)
from engine.cycles.profile import CalibrationStatus, Cycle3AProfile

# Strict whitelist of authorized operational timeframes in AurumIQ
ALLOWED_TIMEFRAMES = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
    "1w": 604800,
}


def timeframe_to_seconds(timeframe: str) -> int:
    """
    Parse timeframe string into exact duration in seconds.
    Raises ValueError on unsupported or invalid timeframe formats.
    """
    tf_clean = timeframe.lower().strip()
    if tf_clean not in ALLOWED_TIMEFRAMES:
        raise ValueError(
            f"Unsupported timeframe: '{timeframe}'. "
            f"Authorized timeframes are: {sorted(list(ALLOWED_TIMEFRAMES.keys()))}."
        )
    return ALLOWED_TIMEFRAMES[tf_clean]


def calculate_swing_duration(
    latest_candle: CandleData,
    structure: StructureResult,
    timeframe: str = "15m",
    historical_durations: Optional[Sequence[int]] = None,
    effective_n: Optional[float] = None,
    sample_eval: Optional[SampleEvaluation] = None,
    profile: Optional[Cycle3AProfile] = None,
) -> SwingDurationContext:
    """
    Calculate causal swing duration maturity and age percentiles.

    Causality Invariants (P3A-07):
      - Point-in-Time filtering: Evaluates ONLY swings with confirmed detected_at <= as_of.
        Future swing confirmations at detected_at > as_of are strictly excluded.
      - market_age: Elapsed time/bars from physical swing peak/trough (timestamp).
      - known_age: Elapsed time/bars from confirmation point (detected_at).
      - Scoring and maturity decisions MUST strictly evaluate knowable age (detected_at).

    Statistical Effective Sample Guard (P3A-09, P3A-15, P3A-18):
      - Evaluates effective sample size (n_eff), discounting overlapping and clustered samples.
      - Unknown independence (sample_eval=None and effective_n=None) MUST FAIL CLOSED:
        eff_n defaults to 0.0, maturity_score = 0.0 (INSUFFICIENT).
      - Raw N must NEVER be assumed equal to effective N.
      - If eff_n < min_eff_n or sample_is_blocked: maturity_score = 0.0 (INSUFFICIENT).
      - Percentile may remain descriptive, but confidence score strictly defaults to 0.0.
      - Zero hardcoded fallback distributions allowed.
      - If profile production scoring is disabled (PENDING_DATA / CANDIDATE_NOT_FROZEN),
        strictly returns maturity_score = 0.0.
    """
    tf_sec = timeframe_to_seconds(timeframe)
    as_of = latest_candle.timestamp_close if latest_candle.is_closed else latest_candle.timestamp_open

    # Strict Point-in-Time swing filtering: only confirmed swings knowable as of as_of
    eligible_swings = [
        s for s in structure.swings
        if s.is_confirmed and s.detected_at <= as_of
    ]

    if not eligible_swings:
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

    last_swing = eligible_swings[-1]

    # 1. Market age (from formation timestamp)
    market_delta_sec = max(0.0, (as_of - last_swing.timestamp).total_seconds())
    market_hours = float(round(market_delta_sec / 3600.0, 2))
    market_bars = max(0, int(market_delta_sec // tf_sec))

    # 2. Known age (from detected_at confirmation timestamp)
    known_delta_sec = max(0.0, (as_of - last_swing.detected_at).total_seconds())
    known_hours = float(round(known_delta_sec / 3600.0, 2))
    known_bars = max(0, int(known_delta_sec // tf_sec))

    durations_seq = historical_durations
    if durations_seq is None and profile is not None and profile.is_production_scoring_enabled:
        durations_seq = profile.historical_durations

    raw_n = len(durations_seq) if durations_seq else 0

    # Descriptive percentile calculation (available descriptively)
    percentile: Optional[float] = None
    if durations_seq and raw_n >= 10:
        durations = sorted(durations_seq)
        pos = bisect.bisect_left(durations, known_bars)
        percentile = float(round(min(100.0, max(0.0, (pos / raw_n) * 100.0)), 2))

    # Profile governance check: if production scoring is disabled, return 0.0 score
    if profile is not None and not profile.is_production_scoring_enabled:
        return SwingDurationContext(
            market_age_bars=market_bars,
            market_age_hours=market_hours,
            known_age_bars=known_bars,
            known_age_hours=known_hours,
            pullback_age_percentile=percentile,
            is_mature=False,
            maturity_score=0.0,
            sample_quality=SampleQuality.INSUFFICIENT,
            effective_n=0.0,
        )

    if profile is not None:
        min_eff_n = profile.swing_min_effective_n
        max_score = profile.swing_max_score
    else:
        min_eff_n = 30.0
        max_score = 20.0

    if min_eff_n is None or max_score is None:
        return SwingDurationContext(
            market_age_bars=market_bars,
            market_age_hours=market_hours,
            known_age_bars=known_bars,
            known_age_hours=known_hours,
            pullback_age_percentile=percentile,
            is_mature=False,
            maturity_score=0.0,
            sample_quality=SampleQuality.INSUFFICIENT,
            effective_n=0.0,
        )

    # 3. Determine effective N and sample quality (P3A-18: Fail Closed on Unknown Effective-N)
    if sample_eval is not None:
        eff_n = sample_eval.effective_n
        sample_is_blocked = sample_eval.is_blocked
    elif effective_n is not None:
        eff_n = float(effective_n)
        sample_is_blocked = eff_n < min_eff_n
    else:
        # Raw N must NEVER be assumed equal to effective N.
        eff_n = 0.0
        sample_is_blocked = True

    # 5. Statistical Effective Sample Gate (P3A-18)
    if not durations_seq or raw_n < 30 or eff_n < min_eff_n or sample_is_blocked:
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

    is_mature = (75.0 <= percentile <= 95.0) if percentile is not None else False

    if percentile is not None:
        if 75.0 <= percentile <= 90.0:
            raw_score = max_score
        elif 50.0 <= percentile < 75.0:
            raw_score = max_score * 0.75
        elif 25.0 <= percentile < 50.0:
            raw_score = max_score * 0.50
        elif percentile > 90.0:
            raw_score = max_score * 0.40
        else:
            raw_score = max_score * 0.25
    else:
        raw_score = 0.0

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
