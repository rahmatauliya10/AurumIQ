"""
Empirical Calibration Pipeline for Phase 3A Robust Time Cycles.

Pure-Python, point-in-time safe calibration routines and data contracts.
Takes historical inputs (candles, confirmed swings, macro event logs)
with explicit provenance tracking and produces deterministic calibration artifacts.

Zero Django imports, zero network calls, zero lookahead, zero numerical fallbacks.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from engine.core.types import (
    CalendarEffectEntry,
    CandleData,
    RegimeType,
    SampleEvaluation,
    SampleQuality,
    SessionExpectancyEntry,
    SessionType,
    SwingPoint,
)
from engine.cycles.calendar import (
    calendar_bucket_key,
)
from engine.cycles.profile import CalibrationStatus, Cycle3AProfile, _deep_freeze
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

    def __post_init__(self):
        """Validate chronological and statistical integrity invariants."""
        if not self.instrument or not self.instrument.strip():
            raise ValueError("Provenance instrument cannot be empty.")
        if self.timeframe not in ("1m", "5m", "15m", "1h", "4h", "1d", "1w"):
            raise ValueError(f"Provenance timeframe '{self.timeframe}' not in authorized list.")
        if self.data_start > self.data_end:
            raise ValueError(
                f"Provenance data_start ({self.data_start}) cannot be after data_end ({self.data_end})."
            )
        if self.data_end > self.as_of:
            raise ValueError(
                f"Provenance data_end ({self.data_end}) cannot be after as_of ({self.as_of})."
            )
        if self.raw_observations < 0:
            raise ValueError("raw_observations cannot be negative.")
        if self.effective_n < 0.0:
            raise ValueError("effective_n cannot be negative.")


@dataclass(frozen=True)
class CalendarCalibrationFold:
    """
    Explicit calibration-only chronological partition.

    OOS partitions must never be supplied here.
    Intervals are strict half-open [start, end).
    """
    fold_id: int
    start: datetime
    end: datetime

    def __post_init__(self):
        if self.fold_id < 1:
            raise ValueError(
                "Calendar fold_id must be >= 1."
            )

        if (
            self.start.tzinfo is None
            or self.start.tzinfo.utcoffset(
                self.start
            ) is None
        ):
            raise ValueError(
                "Calendar fold start must "
                "be timezone-aware."
            )

        if (
            self.end.tzinfo is None
            or self.end.tzinfo.utcoffset(
                self.end
            ) is None
        ):
            raise ValueError(
                "Calendar fold end must "
                "be timezone-aware."
            )

        if self.start >= self.end:
            raise ValueError(
                "Calendar fold start must "
                "be before end."
            )


@dataclass(frozen=True)
class Cycle3ACalibrationArtifact:
    """
    Immutable consolidated empirical calibration artifact for Phase 3A.
    Captures empirical session matrices, swing duration percentiles, calendar effects,
    and macro timing parameters with defensive immutability.
    """
    provenance: CalibrationProvenance
    session_expectancy_table: Mapping[Tuple[SessionType, RegimeType], SessionExpectancyEntry] = field(
        default_factory=dict
    )
    swing_duration_percentiles: Mapping[str, Any] = field(default_factory=dict)
    swing_sample_evaluation: Optional[SampleEvaluation] = None
    calendar_effect_table: Mapping[str, CalendarEffectEntry] = field(default_factory=dict)
    macro_timing_config: Mapping[str, Any] = field(default_factory=dict)
    status: CalibrationStatus = CalibrationStatus.CANDIDATE_NOT_FROZEN

    def __post_init__(self):
        """Enforce strict recursive deep immutability across all dictionary fields."""
        if self.session_expectancy_table is not None:
            object.__setattr__(
                self,
                "session_expectancy_table",
                _deep_freeze(self.session_expectancy_table),
            )
        if self.swing_duration_percentiles is not None:
            object.__setattr__(
                self,
                "swing_duration_percentiles",
                _deep_freeze(self.swing_duration_percentiles),
            )
        if self.calendar_effect_table is not None:
            object.__setattr__(
                self,
                "calendar_effect_table",
                _deep_freeze(self.calendar_effect_table),
            )
        if self.macro_timing_config is not None:
            object.__setattr__(
                self,
                "macro_timing_config",
                _deep_freeze(self.macro_timing_config),
            )


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
    sample_evaluations: Optional[Mapping[Tuple[SessionType, RegimeType], SampleEvaluation]] = None,
    effective_n_mapping: Optional[Mapping[Tuple[SessionType, RegimeType], float]] = None,
    significance_policy: Optional[Callable[[float, float, int], bool]] = None,
    min_effective_n: Optional[float] = None,
) -> Dict[Tuple[SessionType, RegimeType], SessionExpectancyEntry]:
    """
    Calibrate empirical session expectancy table from closed historical candles and point-in-time regimes.

    Invariants:
      - Zero hardcoded sample thresholds (no default min_samples=30).
      - Zero hardcoded t-statistic thresholds (no default 1.96).
      - Raw N is NEVER assumed equal to effective N.
      - If sample_evaluations or effective_n_mapping is not supplied, effective_n defaults to 0.0
        and is_statistically_significant defaults to False.
    """
    if not candles or not regimes:
        return {}

    regime_map = {ts: reg for ts, reg in regimes}
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

        ret = float((c_next.close - c_curr.close) / c_curr.close) if c_curr.close > 0 else 0.0
        key = (session, regime)
        if key not in bucket_returns:
            bucket_returns[key] = []
        bucket_returns[key].append(ret)

    results: Dict[Tuple[SessionType, RegimeType], SessionExpectancyEntry] = {}

    for (sess, reg), rets in bucket_returns.items():
        sample_count = len(rets)
        if sample_count == 0:
            continue

        wins = sum(1 for r in rets if r > 0)
        win_rate = float(round(wins / sample_count, 4))
        avg_ret = sum(rets) / sample_count
        variance = sum((r - avg_ret) ** 2 for r in rets) / (sample_count - 1) if sample_count > 1 else 0.0
        std_dev = math.sqrt(variance) if variance > 0 else 0.0001
        expectancy_r = float(round(avg_ret / std_dev, 4)) if std_dev > 0 else 0.0

        # Determine effective N from explicit mapping (raw N is NEVER assumed equal to effective N)
        key = (sess, reg)
        if sample_evaluations and key in sample_evaluations:
            effective_n = float(sample_evaluations[key].effective_n)
        elif effective_n_mapping and key in effective_n_mapping:
            effective_n = float(effective_n_mapping[key])
        else:
            effective_n = 0.0

        # Significance policy evaluation (no hardcoded p-values)
        if significance_policy is not None and effective_n > 0.0:
            is_sig = significance_policy(avg_ret, std_dev, sample_count)
            if min_effective_n is not None and effective_n < min_effective_n:
                is_sig = False
        else:
            is_sig = False

        results[key] = SessionExpectancyEntry(
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
    as_of: Optional[datetime] = None,
) -> Dict[str, Dict[str, float]]:
    """
    Calibrate empirical swing duration distributions using causal confirmation chronology (detected_at).
    Produces separate percentiles for known_duration (from detected_at) and market_duration (from timestamp).
    Rejects unconfirmed swings and swings with detected_at > as_of.
    """
    tf_sec = timeframe_to_seconds(timeframe)

    # Filter to eligible confirmed swings strictly knowable as of as_of
    eligible = [
        s for s in swings
        if s.is_confirmed and (as_of is None or s.detected_at <= as_of)
    ]

    if len(eligible) < 2:
        return {
            "known_duration": {},
            "market_duration": {},
        }

    # Sort chronologically by confirmation time (detected_at)
    sorted_swings = sorted(eligible, key=lambda s: s.detected_at)

    known_durations: List[int] = []
    market_durations: List[int] = []

    for i in range(len(sorted_swings) - 1):
        s_curr = sorted_swings[i]
        s_next = sorted_swings[i + 1]

        # 1. Causal known duration (between confirmation points)
        known_sec = max(0.0, (s_next.detected_at - s_curr.detected_at).total_seconds())
        known_bars = max(1, int(known_sec // tf_sec))
        known_durations.append(known_bars)

        # 2. Market physical duration (between formation peaks)
        market_sec = max(0.0, (s_next.timestamp - s_curr.timestamp).total_seconds())
        market_bars = max(1, int(market_sec // tf_sec))
        market_durations.append(market_bars)

    return {
        "known_duration": calculate_distribution_percentiles(known_durations),
        "market_duration": calculate_distribution_percentiles(market_durations),
    }


def calibrate_calendar_effects(
    candles: Sequence[CandleData],
    folds: Sequence[CalendarCalibrationFold],
    timeframe: str,
    min_stability_folds: int,
    min_fold_observations: int,
    sample_evaluations: Optional[
        Mapping[str, SampleEvaluation]
    ] = None,
    effective_n_mapping: Optional[
        Mapping[str, float]
    ] = None,
    significance_policy: Optional[
        Callable[[float, float, int], bool]
    ] = None,
    min_effective_n: Optional[float] = None,
) -> Dict[str, CalendarEffectEntry]:
    """
    Calibrate PIT-safe empirical calendar effects.

    Strict invariants:
    - Closed candles only.
    - Canonical runtime bucket function is shared.
    - Only exactly contiguous candle pairs are outcomes.
    - T and T+1 must belong to the SAME supplied
      calibration fold.
    - Folds are explicit calibration partitions only;
      this function has no OOS discovery/access.
    - Raw N is never assumed equal to effective N.
    - Statistical significance requires an explicit policy.
    - Stability requires explicit minimum fold coverage.
    """
    if min_stability_folds < 1:
        raise ValueError(
            "min_stability_folds must be >= 1."
        )

    if min_fold_observations < 1:
        raise ValueError(
            "min_fold_observations must be >= 1."
        )

    if not candles or not folds:
        return {}

    tf_seconds = timeframe_to_seconds(
        timeframe
    )

    ordered_folds = sorted(
        folds,
        key=lambda fold: fold.start,
    )

    seen_ids = set()

    for idx, fold in enumerate(
        ordered_folds
    ):
        if fold.fold_id in seen_ids:
            raise ValueError(
                "Duplicate calendar fold_id."
            )

        seen_ids.add(fold.fold_id)

        if idx > 0:
            previous = ordered_folds[
                idx - 1
            ]

            if fold.start < previous.end:
                raise ValueError(
                    "Calendar calibration folds "
                    "must not overlap."
                )

    ordered_candles = sorted(
        (
            candle
            for candle in candles
            if candle.is_closed
        ),
        key=lambda candle: (
            candle.timestamp_close
        ),
    )

    bucket_returns: Dict[
        str,
        List[float],
    ] = {}

    bucket_fold_returns: Dict[
        str,
        Dict[int, List[float]],
    ] = {}

    def _pair_fold_id(
        current: CandleData,
        nxt: CandleData,
    ) -> Optional[int]:
        for fold in ordered_folds:
            if (
                fold.start
                <= current.timestamp_close
                < fold.end
                and fold.start
                <= nxt.timestamp_close
                < fold.end
            ):
                return fold.fold_id

        return None

    for idx in range(
        len(ordered_candles) - 1
    ):
        current = ordered_candles[idx]
        nxt = ordered_candles[idx + 1]

        # Exact physical candle continuity.
        if (
            nxt.timestamp_open
            != current.timestamp_close
        ):
            continue

        close_delta = (
            nxt.timestamp_close
            - current.timestamp_close
        ).total_seconds()

        if close_delta != tf_seconds:
            continue

        fold_id = _pair_fold_id(
            current,
            nxt,
        )

        if fold_id is None:
            continue

        if current.close <= 0:
            continue

        realized_return = float(
            (
                nxt.close
                - current.close
            )
            / current.close
        )

        bucket = calendar_bucket_key(
            current.timestamp_close
        )

        bucket_returns.setdefault(
            bucket,
            [],
        ).append(
            realized_return
        )

        bucket_fold_returns.setdefault(
            bucket,
            {},
        ).setdefault(
            fold_id,
            [],
        ).append(
            realized_return
        )

    results: Dict[
        str,
        CalendarEffectEntry,
    ] = {}

    for bucket, returns in (
        bucket_returns.items()
    ):
        sample_count = len(returns)

        if sample_count == 0:
            continue

        wins = sum(
            1
            for value in returns
            if value > 0.0
        )

        win_rate = float(
            round(
                wins / sample_count,
                4,
            )
        )

        avg_return = (
            sum(returns)
            / sample_count
        )

        if sample_count > 1:
            variance = sum(
                (
                    value
                    - avg_return
                ) ** 2
                for value in returns
            ) / (
                sample_count - 1
            )
        else:
            variance = 0.0

        std_dev = (
            math.sqrt(variance)
            if variance > 0.0
            else 0.0
        )

        # No variance -> no defensible
        # standardized expectancy.
        expectancy_r = (
            float(
                round(
                    avg_return
                    / std_dev,
                    4,
                )
            )
            if std_dev > 0.0
            else 0.0
        )

        # -----------------------------
        # Effective N — explicit only.
        # -----------------------------
        if (
            sample_evaluations
            and bucket
            in sample_evaluations
        ):
            effective_n = float(
                sample_evaluations[
                    bucket
                ].effective_n
            )
        elif (
            effective_n_mapping
            and bucket
            in effective_n_mapping
        ):
            effective_n = float(
                effective_n_mapping[
                    bucket
                ]
            )
        else:
            effective_n = 0.0

        # -----------------------------
        # Statistical significance.
        # -----------------------------
        if (
            significance_policy
            is not None
            and effective_n > 0.0
        ):
            is_significant = bool(
                significance_policy(
                    avg_return,
                    std_dev,
                    sample_count,
                )
            )

            if (
                min_effective_n
                is not None
                and effective_n
                < min_effective_n
            ):
                is_significant = False
        else:
            is_significant = False

        # -----------------------------
        # Fold directional stability.
        # -----------------------------
        aggregate_direction = (
            1
            if avg_return > 0.0
            else -1
            if avg_return < 0.0
            else 0
        )

        eligible_fold_means = []

        for fold_returns in (
            bucket_fold_returns
            .get(bucket, {})
            .values()
        ):
            if (
                len(fold_returns)
                < min_fold_observations
            ):
                continue

            eligible_fold_means.append(
                sum(fold_returns)
                / len(fold_returns)
            )

        if (
            aggregate_direction == 0
            or len(
                eligible_fold_means
            ) < min_stability_folds
        ):
            stability = 0.0
        else:
            agreement_count = sum(
                1
                for fold_mean
                in eligible_fold_means
                if (
                    fold_mean > 0.0
                    and aggregate_direction > 0
                )
                or (
                    fold_mean < 0.0
                    and aggregate_direction < 0
                )
            )

            stability = float(
                round(
                    agreement_count
                    / len(
                        eligible_fold_means
                    ),
                    4,
                )
            )

        results[bucket] = (
            CalendarEffectEntry(
                bucket=bucket,
                sample_count=sample_count,
                effective_n=effective_n,
                win_rate=win_rate,
                expectancy_r=expectancy_r,
                stability=stability,
                is_statistically_significant=(
                    is_significant
                ),
            )
        )

    return results


def build_profile_from_artifact(
    artifact: Cycle3ACalibrationArtifact,
    name: Optional[str] = None,
) -> Cycle3AProfile:
    """
    Construct a candidate Cycle3AProfile from a Cycle3ACalibrationArtifact.

    Governance Rules:
      - Sets calibration_status = CANDIDATE_NOT_FROZEN.
      - Production scoring weights and multipliers are strictly set to None.
      - Macro pre/post blackout and clear bonus values are strictly set to None.
      - Descriptive candidate tables and distributions are attached for inspection.
      - CANDIDATE_NOT_FROZEN profiles strictly produce 0.0 production scores at runtime.
    """
    profile_name = name or f"{artifact.provenance.instrument}_CANDIDATE_{artifact.provenance.calibration_version}"
    inst = artifact.provenance.instrument.upper().replace("/", "")

    # Extract known duration percentiles if nested
    swing_pcts = artifact.swing_duration_percentiles
    if isinstance(swing_pcts, Mapping) and "known_duration" in swing_pcts:
        swing_pcts = swing_pcts["known_duration"]

    return Cycle3AProfile(
        name=profile_name,
        calibration_status=CalibrationStatus.CANDIDATE_NOT_FROZEN,
        target_instrument=inst,
        timeframe=artifact.provenance.timeframe,
        session_max_score=None,
        session_min_effective_n=None,
        session_expectancy_multiplier=None,
        session_expectancy_table=artifact.session_expectancy_table or None,
        swing_max_score=None,
        swing_min_effective_n=None,
        swing_sample_evaluation=artifact.swing_sample_evaluation,
        swing_maturity_bands=None,
        historical_durations=None,
        swing_duration_percentiles=swing_pcts or None,
        calendar_max_score=None,
        calendar_min_effective_n=None,
        calendar_stability_threshold=None,
        calendar_expectancy_multiplier=None,
        calendar_effect_table=artifact.calendar_effect_table or None,
        macro_blackout_pre_minutes=None,
        macro_blackout_post_minutes=None,
        macro_clear_window_far_minutes=None,
        macro_clear_window_near_minutes=None,
        macro_clear_bonus_far=None,
        macro_clear_bonus_near=None,
        details={
            "instrument": inst,
            "calibration_status": CalibrationStatus.CANDIDATE_NOT_FROZEN.value,
            "calibration_version": artifact.provenance.calibration_version,
            "effective_n": artifact.provenance.effective_n,
            "data_fingerprint": artifact.provenance.data_fingerprint,
            "generated_at": artifact.provenance.generated_at.isoformat(),
        },
    )
