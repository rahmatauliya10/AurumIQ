"""
Governed Phase 3A Chronological Fold Stability Contract for XAUUSD.

Provides:
- Equal-duration, outcome-independent chronological fold generation
- Half-open [start, end) boundary semantics
- Strict full-window observation containment
- A16 Empirical Effective-N measurement per fold
- Governed positive-mean statistical significance per fold
- Population fold-expectancy stability scoring: 1 - std / (abs(mean) + 1)
- 4/5 positive fold rule and stability >= 0.50 rule enforcement

Zero Django imports, zero lookahead, zero network calls, zero production authority.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import math
from typing import Optional, Sequence, Tuple

from engine.guards.empirical_a16 import (
    ObservationWindow,
    compute_empirical_a16_policy_fingerprint,
    evaluate_empirical_a16,
)
from engine.guards.statistical_significance import (
    compute_phase3a_significance_policy_fingerprint,
    evaluate_positive_mean_significance,
)


PHASE3A_FOLD_STABILITY_SCHEMA = (
    "aurumiq.phase3a.fold_stability.v1"
)

TOTAL_FOLDS = 5
MIN_POSITIVE_FOLDS = 4
MIN_TEMPORAL_STABILITY_SCORE = 0.50

FOLD_BOUNDARY_METHOD = (
    "EQUAL_DURATION_HALF_OPEN_UTC_V1"
)

OBSERVATION_CONTAINMENT = (
    "STRICT_FULL_WINDOW_CONTAINMENT"
)

FOLD_EXPECTANCY_METRIC = (
    "STANDARDIZED_MEAN_RETURN_OVER_SAMPLE_STD_V1"
)

STABILITY_FORMULA = (
    "1-(STD/ABS_MEAN_PLUS_ONE)"
)


@dataclass(frozen=True)
class ChronologicalFold:
    fold_id: int
    start: datetime
    end: datetime


@dataclass(frozen=True)
class FoldEvidence:
    fold_id: int
    raw_n: int
    effective_n: float
    mean_return: float
    std_dev: float
    expectancy_r: float
    positive: bool
    significant_positive: bool
    lcb_95: Optional[float]
    a16_certified: bool


@dataclass(frozen=True)
class TemporalStabilityResult:
    fold_count: int
    covered_fold_count: int
    positive_fold_count: int

    fold_expectancies: Tuple[float, ...]

    mean_fold_expectancy: float
    std_fold_expectancy: float
    stability_score: Optional[float]

    positive_fold_rule_passed: bool
    stability_rule_passed: bool
    temporal_stability_passed: bool

    folds: Tuple[FoldEvidence, ...]

    policy_fingerprint: str


def build_equal_duration_folds(
    start: datetime,
    end: datetime,
    total_folds: int = 5,
) -> Tuple[ChronologicalFold, ...]:
    """
    Generate contiguous, non-overlapping, half-open [start, end) folds of equal duration.

    Boundary generation is strictly outcome-independent: no alignment with returns,
    swings, sessions, or volatility.
    """
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be timezone-aware datetimes")

    if start >= end:
        raise ValueError("start must be strictly earlier than end")

    if total_folds <= 0:
        raise ValueError("total_folds must be positive")

    total_duration = end - start
    fold_duration = total_duration / total_folds

    folds = []
    for i in range(total_folds):
        f_start = start + fold_duration * i
        f_end = (
            start + fold_duration * (i + 1)
            if i < total_folds - 1
            else end
        )
        folds.append(
            ChronologicalFold(
                fold_id=i + 1,
                start=f_start,
                end=f_end,
            )
        )

    return tuple(folds)


def observations_for_fold(
    observations: Sequence[ObservationWindow],
    fold: ChronologicalFold,
) -> Tuple[ObservationWindow, ...]:
    """
    Filter observations that are strictly contained within [fold.start, fold.end].

    Observations crossing a fold boundary are excluded from both folds.
    """
    return tuple(
        obs
        for obs in observations
        if (
            obs.start >= fold.start
            and obs.end <= fold.end
        )
    )


def compute_phase3a_fold_stability_policy_fingerprint() -> str:
    """
    Deterministic SHA-256 fingerprint binding all Phase 3A fold stability policy rules.
    """
    payload = {
        "schema": PHASE3A_FOLD_STABILITY_SCHEMA,
        "total_folds": TOTAL_FOLDS,
        "fold_boundary_method": FOLD_BOUNDARY_METHOD,
        "observation_containment": OBSERVATION_CONTAINMENT,
        "min_positive_folds": MIN_POSITIVE_FOLDS,
        "min_positive_folds_total": TOTAL_FOLDS,
        "min_temporal_stability_score": MIN_TEMPORAL_STABILITY_SCORE,
        "fold_expectancy_metric": FOLD_EXPECTANCY_METRIC,
        "stability_formula": STABILITY_FORMULA,
        "a16_policy_fingerprint": compute_empirical_a16_policy_fingerprint(),
        "significance_policy_fingerprint": compute_phase3a_significance_policy_fingerprint(),
        "production_authority": False,
    }

    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )

    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def evaluate_temporal_stability(
    observations: Sequence[ObservationWindow],
    folds: Sequence[ChronologicalFold],
) -> TemporalStabilityResult:
    """
    Evaluate 5-fold chronological stability for an observation series.

    - Canonical ordering of observations by (start, end)
    - Full-window containment per fold
    - Empirical A16 Effective-N and LCB95 significance per fold
    - Exact population standard deviation stability scoring across fold expectancies
    """
    policy_fp = compute_phase3a_fold_stability_policy_fingerprint()

    # Canonical chronological ordering guarantees identical output regardless of input order
    ordered_observations = sorted(
        observations,
        key=lambda obs: (obs.start, obs.end),
    )

    fold_evidences = []
    for fold in folds:
        fold_obs = observations_for_fold(ordered_observations, fold)
        raw_n = len(fold_obs)

        if raw_n == 0:
            fold_evidences.append(
                FoldEvidence(
                    fold_id=fold.fold_id,
                    raw_n=0,
                    effective_n=0.0,
                    mean_return=0.0,
                    std_dev=0.0,
                    expectancy_r=0.0,
                    positive=False,
                    significant_positive=False,
                    lcb_95=None,
                    a16_certified=False,
                )
            )
            continue

        a16 = evaluate_empirical_a16(fold_obs)
        if a16.is_certified and a16.evaluation is not None:
            effective_n = float(a16.evaluation.effective_n)
            a16_certified = True
        else:
            effective_n = 0.0
            a16_certified = False

        values = [obs.value for obs in fold_obs]
        mean_return = sum(values) / raw_n
        variance = (
            sum((x - mean_return) ** 2 for x in values) / (raw_n - 1)
            if raw_n > 1
            else 0.0
        )
        std_dev = math.sqrt(variance) if variance > 0.0 else 0.0
        expectancy_r = (
            mean_return / std_dev
            if std_dev > 0.0
            else 0.0
        )

        if a16_certified and effective_n > 0.0 and std_dev > 0.0:
            sig = evaluate_positive_mean_significance(
                mean_return=mean_return,
                std_dev=std_dev,
                effective_n=effective_n,
            )
            significant_positive = bool(sig.is_significant)
            lcb_95 = sig.lcb_95
        else:
            significant_positive = False
            lcb_95 = None

        positive = mean_return > 0.0

        fold_evidences.append(
            FoldEvidence(
                fold_id=fold.fold_id,
                raw_n=raw_n,
                effective_n=effective_n,
                mean_return=mean_return,
                std_dev=std_dev,
                expectancy_r=expectancy_r,
                positive=positive,
                significant_positive=significant_positive,
                lcb_95=lcb_95,
                a16_certified=a16_certified,
            )
        )

    fold_evidences_tuple = tuple(fold_evidences)
    fold_count = len(folds)
    covered_fold_count = sum(1 for ev in fold_evidences_tuple if ev.raw_n > 0)
    positive_fold_count = sum(1 for ev in fold_evidences_tuple if ev.positive)
    fold_expectancies = tuple(ev.expectancy_r for ev in fold_evidences_tuple)

    if covered_fold_count < fold_count:
        # Missing fold fails closed
        mean_fold_expectancy = 0.0
        std_fold_expectancy = 0.0
        stability_score = None
        positive_fold_rule_passed = False
        stability_rule_passed = False
        temporal_stability_passed = False
    else:
        mean_fold_expectancy = sum(fold_expectancies) / fold_count
        variance = sum(
            (x - mean_fold_expectancy) ** 2
            for x in fold_expectancies
        ) / fold_count
        std_fold_expectancy = math.sqrt(variance)

        stability_score = float(
            1.0
            - (
                std_fold_expectancy
                / (
                    abs(mean_fold_expectancy)
                    + 1.0
                )
            )
        )

        positive_fold_rule_passed = bool(
            positive_fold_count >= MIN_POSITIVE_FOLDS
            and covered_fold_count == TOTAL_FOLDS
        )

        stability_rule_passed = bool(
            stability_score >= MIN_TEMPORAL_STABILITY_SCORE
        )

        temporal_stability_passed = bool(
            positive_fold_rule_passed
            and stability_rule_passed
        )

    return TemporalStabilityResult(
        fold_count=fold_count,
        covered_fold_count=covered_fold_count,
        positive_fold_count=positive_fold_count,
        fold_expectancies=fold_expectancies,
        mean_fold_expectancy=mean_fold_expectancy,
        std_fold_expectancy=std_fold_expectancy,
        stability_score=stability_score,
        positive_fold_rule_passed=positive_fold_rule_passed,
        stability_rule_passed=stability_rule_passed,
        temporal_stability_passed=temporal_stability_passed,
        folds=fold_evidences_tuple,
        policy_fingerprint=policy_fp,
    )
