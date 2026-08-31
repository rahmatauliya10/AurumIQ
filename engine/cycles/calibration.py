"""
Empirical Calibration Pipeline for Phase 3A Robust Time Cycles.

Pure-Python, point-in-time safe calibration routines and data contracts.
Takes historical inputs (candles, confirmed swings, macro event logs)
with explicit provenance tracking and produces deterministic calibration artifacts.

Zero Django imports, zero network calls, zero lookahead.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
import bisect

from engine.core.types import (
    CalendarEffectEntry,
    CandleData,
    RegimeType,
    SampleQuality,
    SessionExpectancyEntry,
    SessionType,
    SwingPoint,
)
from engine.cycles.profile import Cycle3AProfile
from engine.cycles.session import classify_session
from engine.cycles.swing_duration import timeframe_to_seconds


@dataclass(frozen=True)
class CalibrationProvenance:
    """Audit provenance tracking for empirical cycle calibration artifacts."""
    instrument: str
    provider: str
    timeframe: str
    data_start: datetime
    data_end: datetime
    as_of: datetime
    raw_observations: int
    effective_n: float
    calibration_version: str
    code_revision: str
    data_fingerprint: str
    generated_at: datetime


@dataclass(frozen=True)
class Cycle3ACalibrationArtifact:
    """
    Immutable consolidated empirical calibration artifact for Phase 3A.
    Captures empirical session matrices, swing duration percentiles, calendar effects,
    and macro timing parameters.
    """
    provenance: CalibrationProvenance
    session_expectancy_table: Dict[Tuple[SessionType, RegimeType], SessionExpectancyEntry] = field(default_factory=dict)
    swing_duration_percentiles: Dict[str, float] = field(default_factory=dict)
    calendar_effect_table: Dict[str, CalendarEffectEntry] = field(default_factory=dict)
    macro_timing_config: Dict[str, Any] = field(default_factory=dict)
    status: str = "CANDIDATE_NOT_FROZEN"


def calculate_distribution_percentiles(durations: Sequence[int]) -> Dict[str, float]:
    """
    Compute descriptive percentiles (P10, P25, P50, P75, P90, P95) from a sequence of confirmed durations.
    Returns empty dict if durations is empty.
    """
    if not durations:
        return {}

    sorted_d = sorted(durations)
    n = len(sorted_d)

    def _pct(p: float) -> float:
        if n == 1:
            return float(sorted_d[0])
        rank = (p / 100.0) * (n - 1)
        k = int(math.floor(rank))
        d = rank - k
        if k + 1 < n:
            val = sorted_d[k] + d * (sorted_d[k + 1] - sorted_d[k])
        else:
            val = float(sorted_d[k])
        return float(round(val, 2))

    return {
        "P10": _pct(10.0),
        "P25": _pct(25.0),
        "P50": _pct(50.0),
        "P75": _pct(75.0),
        "P90": _pct(90.0),
        "P95": _pct(95.0),
    }


def calibrate_session_expectancy(
    candles: Sequence[CandleData],
    regimes: Sequence[Tuple[datetime, RegimeType]],
    min_samples: int = 30,
) -> Dict[Tuple[SessionType, RegimeType], SessionExpectancyEntry]:
    """
    Calibrate empirical session expectancy table from closed historical candles and point-in-time regime classifications.
    Ensures zero lookahead: only closed candles are evaluated.
    """
    if not candles or not regimes:
        return {}

    # Map timestamps to regimes
    regime_map = {ts: reg for ts, reg in regimes}

    # Group returns by (SessionType, RegimeType)
    bucket_returns: Dict[Tuple[SessionType, RegimeType], List[float]] = {}

    for i in range(len(candles) - 1):
        c_curr = candles[i]
        c_next = candles[i + 1]
        if not c_curr.is_closed or not c_next.is_closed:
            continue

        as_of = c_curr.timestamp_close
        regime = regime_map.get(as_of)
        if regime is None:
            continue

        session_ctx = classify_session(as_of)
        session = session_ctx.session

        # Percentage return of next bar
        ret = float((c_next.close - c_curr.close) / c_curr.close) if c_curr.close > 0 else 0.0

        key = (session, regime)
        if key not in bucket_returns:
            bucket_returns[key] = []
        bucket_returns[key].append(ret)

    results: Dict[Tuple[SessionType, RegimeType], SessionExpectancyEntry] = {}

    for (sess, reg), rets in bucket_returns.items():
        sample_count = len(rets)
        if sample_count < min_samples:
            continue

        wins = sum(1 for r in rets if r > 0)
        win_rate = float(round(wins / sample_count, 4))
        avg_ret = sum(rets) / sample_count
        variance = sum((r - avg_ret) ** 2 for r in rets) / (sample_count - 1) if sample_count > 1 else 0.0
        std_dev = math.sqrt(variance) if variance > 0 else 0.0001
        expectancy_r = float(round(avg_ret / std_dev, 4)) if std_dev > 0 else 0.0

        # Standard t-statistic significance check (two-tailed p < 0.05 approx t >= 1.96)
        t_stat = (avg_ret / (std_dev / math.sqrt(sample_count))) if std_dev > 0 and sample_count > 0 else 0.0
        is_sig = abs(t_stat) >= 1.96 and sample_count >= min_samples

        # In pure engine, effective_n is evaluated from sample count (can be adjusted with discount)
        effective_n = float(sample_count)

        results[(sess, reg)] = SessionExpectancyEntry(
            session=sess,
            regime=reg,
            sample_count=sample_count,
            effective_n=effective_n,
            win_rate=win_rate,
            expectancy_r=expectancy_r,
            is_statistically_significant=is_sig,
        )

    return results


def calibrate_swing_durations(
    swings: Sequence[SwingPoint],
    timeframe: str = "15m",
) -> Dict[str, float]:
    """
    Calibrate empirical swing duration distribution from causal confirmed swings.
    Uses knowable age (bars between consecutive confirmed swings).
    """
    if len(swings) < 2:
        return {}

    tf_sec = timeframe_to_seconds(timeframe)
    durations: List[int] = []

    for i in range(len(swings) - 1):
        s_curr = swings[i]
        s_next = swings[i + 1]
        delta_sec = max(0.0, (s_next.timestamp - s_curr.timestamp).total_seconds())
        bars = max(1, int(delta_sec // tf_sec))
        durations.append(bars)

    return calculate_distribution_percentiles(durations)


def build_profile_from_artifact(
    artifact: Cycle3ACalibrationArtifact,
    name: Optional[str] = None,
) -> Cycle3AProfile:
    """
    Construct a calibrated Cycle3AProfile from a certified Cycle3ACalibrationArtifact.
    Enforces that the resulting profile captures all calibrated parameters explicitly.
    """
    profile_name = name or f"{artifact.provenance.instrument}_CALIBRATED_{artifact.provenance.calibration_version}"
    return Cycle3AProfile(
        name=profile_name,
        is_calibrated=True,
        session_max_score=15.0,
        session_min_effective_n=30.0,
        session_expectancy_multiplier=30.0,
        session_expectancy_table=artifact.session_expectancy_table or None,
        swing_max_score=20.0,
        swing_min_effective_n=30.0,
        swing_maturity_bands={
            "P75_90": 20.0,
            "P50_75": 15.0,
            "P25_50": 10.0,
            "P90_plus": 8.0,
            "default": 5.0,
        },
        historical_durations=None,
        calendar_max_score=5.0,
        calendar_min_effective_n=30.0,
        calendar_stability_threshold=0.60,
        calendar_expectancy_multiplier=10.0,
        calendar_effect_table=artifact.calendar_effect_table or None,
        macro_blackout_minutes=artifact.macro_timing_config.get("blackout_minutes", 30),
        macro_clear_window_far_minutes=artifact.macro_timing_config.get("clear_window_far_minutes", 120),
        macro_clear_window_near_minutes=artifact.macro_timing_config.get("clear_window_near_minutes", 60),
        macro_clear_bonus_far=artifact.macro_timing_config.get("clear_bonus_far", 5.0),
        macro_clear_bonus_near=artifact.macro_timing_config.get("clear_bonus_near", 2.0),
        details={
            "instrument": artifact.provenance.instrument,
            "calibration_status": artifact.status,
            "calibration_version": artifact.provenance.calibration_version,
            "effective_n": artifact.provenance.effective_n,
            "generated_at": artifact.provenance.generated_at.isoformat(),
        },
    )
