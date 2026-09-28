"""
Governed empirical measurement policy for A16.

This module derives the existing EffectiveSampleEstimator
inputs from observed evidence.

No Django.
No network.
No profile authority.
No threshold optimization.
"""

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
from typing import (
    Dict,
    Optional,
    Sequence,
    Tuple,
)

from engine.core.config import (
    EngineConfigData,
)
from engine.core.types import (
    SampleEvaluation,
)
from engine.guards.sample_guard import (
    EffectiveSampleEstimator,
)


A16_EMPIRICAL_POLICY_SCHEMA = (
    "aurumiq.a16.empirical_policy.v1"
)

OVERLAP_METHOD = (
    "STRICT_TEMPORAL_WINDOW_OVERLAP_RATIO_V1"
)

AUTOCORRELATION_METHOD = (
    "ABS_PEARSON_LAG1_CHRONOLOGICAL_OUTCOMES_V1"
)

REGIME_POLICY = (
    "UNKNOWN_SINGLE_CATEGORY_UNTIL_"
    "XAUUSD_REGIME_CALIBRATED"
)


@dataclass(frozen=True)
class ObservationWindow:
    start: datetime
    end: datetime
    value: float
    regime: str = "UNKNOWN"

    def __post_init__(self):
        for name, value in (
            ("start", self.start),
            ("end", self.end),
        ):
            if (
                value.tzinfo is None
                or value.tzinfo.utcoffset(value)
                is None
            ):
                raise ValueError(
                    f"{name} must be timezone-aware."
                )

        if self.start >= self.end:
            raise ValueError(
                "Observation start must precede end."
            )

        if not math.isfinite(self.value):
            raise ValueError(
                "Observation value must be finite."
            )

        if not self.regime:
            raise ValueError(
                "Observation regime must be explicit."
            )


@dataclass(frozen=True)
class OverlapMeasurement:
    n_raw: int
    overlapping_count: int
    overlap_ratio: float


@dataclass(frozen=True)
class EmpiricalA16Result:
    is_certified: bool
    reason: str

    overlap_method: str
    autocorrelation_method: str
    regime_policy: str

    overlapping_count: int
    overlap_ratio: float

    lag1_autocorrelation: Optional[float]
    autocorrelation_factor: Optional[float]

    regime_distribution: Dict[str, int]

    policy_fingerprint: str
    evaluation: Optional[SampleEvaluation]


def _ordered(
    observations: Sequence[ObservationWindow],
) -> Tuple[ObservationWindow, ...]:
    if not observations:
        return ()

    return tuple(
        sorted(
            observations,
            key=lambda item: (
                item.start,
                item.end,
            ),
        )
    )


def compute_overlap_ratio(
    observations: Sequence[ObservationWindow],
) -> OverlapMeasurement:
    ordered = _ordered(observations)

    n_raw = len(ordered)

    if n_raw == 0:
        return OverlapMeasurement(
            n_raw=0,
            overlapping_count=0,
            overlap_ratio=0.0,
        )

    overlapping = 0
    max_end = None

    for observation in ordered:
        if (
            max_end is not None
            and observation.start < max_end
        ):
            overlapping += 1

        if (
            max_end is None
            or observation.end > max_end
        ):
            max_end = observation.end

    ratio = float(
        round(
            overlapping / n_raw,
            8,
        )
    )

    return OverlapMeasurement(
        n_raw=n_raw,
        overlapping_count=overlapping,
        overlap_ratio=ratio,
    )


def compute_lag1_autocorrelation(
    values: Sequence[float],
) -> Optional[float]:
    values = tuple(
        float(value)
        for value in values
    )

    if len(values) < 3:
        return None

    if any(
        not math.isfinite(value)
        for value in values
    ):
        raise ValueError(
            "Non-finite value in "
            "autocorrelation series."
        )

    x = values[:-1]
    y = values[1:]

    mean_x = sum(x) / len(x)
    mean_y = sum(y) / len(y)

    centered_x = [
        value - mean_x
        for value in x
    ]

    centered_y = [
        value - mean_y
        for value in y
    ]

    variance_x = sum(
        value * value
        for value in centered_x
    )

    variance_y = sum(
        value * value
        for value in centered_y
    )

    if (
        variance_x <= 0.0
        or variance_y <= 0.0
    ):
        return None

    covariance = sum(
        left * right
        for left, right in zip(
            centered_x,
            centered_y,
        )
    )

    rho = covariance / math.sqrt(
        variance_x * variance_y
    )

    rho = max(
        -1.0,
        min(1.0, rho),
    )

    return float(
        round(rho, 8)
    )


def compute_empirical_a16_policy_fingerprint(
    config: Optional[
        EngineConfigData
    ] = None,
) -> str:
    cfg = config or EngineConfigData()

    payload = {
        "schema": A16_EMPIRICAL_POLICY_SCHEMA,
        "overlap_method": OVERLAP_METHOD,
        "autocorrelation_method": (
            AUTOCORRELATION_METHOD
        ),
        "regime_policy": REGIME_POLICY,

        "sample_guard": {
            "min_sample_threshold": (
                cfg.min_sample_threshold
            ),
            "low_quality_threshold": (
                cfg.low_quality_threshold
            ),
            "medium_quality_threshold": (
                cfg.medium_quality_threshold
            ),
            "max_hhi_discount": (
                cfg.max_hhi_discount
            ),
            "max_clustering_discount": (
                cfg.max_clustering_discount
            ),
        },

        "undefined_autocorrelation_policy": (
            "FAIL_CLOSED_NO_CERTIFICATION"
        ),

        "negative_autocorrelation_policy": (
            "ABSOLUTE_VALUE_NO_INDEPENDENCE_BONUS"
        ),

        "touching_interval_policy": (
            "BOUNDARY_TOUCH_IS_NOT_OVERLAP"
        ),
    }

    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )

    return hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def evaluate_empirical_a16(
    observations: Sequence[ObservationWindow],
    config: Optional[
        EngineConfigData
    ] = None,
) -> EmpiricalA16Result:
    ordered = _ordered(observations)

    overlap = compute_overlap_ratio(
        ordered
    )

    regimes: Dict[str, int] = {}

    for observation in ordered:
        regimes[
            observation.regime
        ] = (
            regimes.get(
                observation.regime,
                0,
            )
            + 1
        )

    policy_fp = (
        compute_empirical_a16_policy_fingerprint(
            config
        )
    )

    rho = (
        compute_lag1_autocorrelation(
            [
                observation.value
                for observation in ordered
            ]
        )
    )

    if rho is None:
        return EmpiricalA16Result(
            is_certified=False,
            reason=(
                "AUTOCORRELATION_NOT_IDENTIFIABLE"
            ),
            overlap_method=OVERLAP_METHOD,
            autocorrelation_method=(
                AUTOCORRELATION_METHOD
            ),
            regime_policy=REGIME_POLICY,
            overlapping_count=(
                overlap.overlapping_count
            ),
            overlap_ratio=(
                overlap.overlap_ratio
            ),
            lag1_autocorrelation=None,
            autocorrelation_factor=None,
            regime_distribution=regimes,
            policy_fingerprint=policy_fp,
            evaluation=None,
        )

    # Existing A16 factor is unsigned [0..1].
    # Any serial dependence is discounted.
    # Negative autocorrelation is never allowed
    # to increase Effective-N above raw N.
    autocorrelation_factor = abs(rho)

    estimator = EffectiveSampleEstimator(
        config=config
    )

    evaluation = estimator.evaluate_sample(
        n_raw=len(ordered),
        regime_distribution=regimes,
        autocorrelation_factor=(
            autocorrelation_factor
        ),
        overlap_ratio=(
            overlap.overlap_ratio
        ),
    )

    return EmpiricalA16Result(
        is_certified=True,
        reason="EMPIRICAL_A16_CERTIFIED",
        overlap_method=OVERLAP_METHOD,
        autocorrelation_method=(
            AUTOCORRELATION_METHOD
        ),
        regime_policy=REGIME_POLICY,
        overlapping_count=(
            overlap.overlapping_count
        ),
        overlap_ratio=(
            overlap.overlap_ratio
        ),
        lag1_autocorrelation=rho,
        autocorrelation_factor=(
            autocorrelation_factor
        ),
        regime_distribution=regimes,
        policy_fingerprint=policy_fp,
        evaluation=evaluation,
    )
