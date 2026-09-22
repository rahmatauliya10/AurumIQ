"""
Governed positive-effect statistical significance policy
for XAUUSD Phase 3A empirical calibration.

Decision rule:
    one-sided 95% lower confidence bound > 0

Sampling uncertainty is based on A16 Effective-N,
NEVER raw sample count.

No production authority is granted here.
"""

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Optional


PHASE3A_SIGNIFICANCE_SCHEMA = (
    "aurumiq.phase3a."
    "significance_policy.v1"
)

CONFIDENCE_LEVEL = 0.95
ALPHA = 0.05

ALTERNATIVE = "POSITIVE_MEAN"

DECISION_RULE = (
    "ONE_SIDED_LCB95_GT_ZERO"
)

SAMPLE_SIZE_BASIS = (
    "A16_EFFECTIVE_N"
)

T_CRITICAL_METHOD = (
    "PHASE6_GOVERNED_PIECEWISE_T_APPROX_V1"
)


@dataclass(frozen=True)
class SignificanceEvaluation:
    mean_return: float
    std_dev: float
    effective_n: float

    t_critical: Optional[float]
    standard_error: Optional[float]
    lcb_95: Optional[float]

    is_significant: bool
    reason: str
    policy_fingerprint: str


def _t_critical_95(
    effective_n: float,
) -> Optional[float]:
    if effective_n <= 1.0:
        return None

    df = max(
        1.0,
        effective_n - 1.0,
    )

    # Preserve the existing governed
    # Phase-6 approximation.
    if df >= 100.0:
        value = (
            1.645
            + (
                1.645
                / (4.0 * df)
            )
        )
    elif df >= 60.0:
        value = 1.671
    elif df >= 30.0:
        value = 1.697
    else:
        value = (
            1.70
            + (30.0 - df) * 0.02
        )

    return float(value)


def compute_phase3a_significance_policy_fingerprint(
) -> str:
    payload = {
        "schema": PHASE3A_SIGNIFICANCE_SCHEMA,
        "confidence_level": CONFIDENCE_LEVEL,
        "alpha": ALPHA,
        "alternative": ALTERNATIVE,
        "decision_rule": DECISION_RULE,
        "sample_size_basis": SAMPLE_SIZE_BASIS,
        "t_critical_method": T_CRITICAL_METHOD,

        "zero_variance_policy": (
            "FAIL_CLOSED"
        ),

        "invalid_effective_n_policy": (
            "FAIL_CLOSED"
        ),

        "threshold_optimization_permitted": False,
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


def evaluate_positive_mean_significance(
    mean_return: float,
    std_dev: float,
    effective_n: float,
) -> SignificanceEvaluation:
    policy_fp = (
        compute_phase3a_significance_policy_fingerprint()
    )

    values = (
        float(mean_return),
        float(std_dev),
        float(effective_n),
    )

    if any(
        not math.isfinite(value)
        for value in values
    ):
        return SignificanceEvaluation(
            mean_return=float(mean_return),
            std_dev=float(std_dev),
            effective_n=float(effective_n),
            t_critical=None,
            standard_error=None,
            lcb_95=None,
            is_significant=False,
            reason="NON_FINITE_INPUT",
            policy_fingerprint=policy_fp,
        )

    if effective_n <= 1.0:
        return SignificanceEvaluation(
            mean_return=float(mean_return),
            std_dev=float(std_dev),
            effective_n=float(effective_n),
            t_critical=None,
            standard_error=None,
            lcb_95=None,
            is_significant=False,
            reason=(
                "EFFECTIVE_N_"
                "INSUFFICIENT_FOR_INFERENCE"
            ),
            policy_fingerprint=policy_fp,
        )

    if std_dev <= 0.0:
        return SignificanceEvaluation(
            mean_return=float(mean_return),
            std_dev=float(std_dev),
            effective_n=float(effective_n),
            t_critical=None,
            standard_error=None,
            lcb_95=None,
            is_significant=False,
            reason=(
                "STANDARD_DEVIATION_"
                "NOT_IDENTIFIABLE"
            ),
            policy_fingerprint=policy_fp,
        )

    t_critical = _t_critical_95(
        float(effective_n)
    )

    if t_critical is None:
        raise AssertionError(
            "Validated effective_n must "
            "produce t-critical."
        )

    standard_error = (
        float(std_dev)
        / math.sqrt(
            float(effective_n)
        )
    )

    lcb = (
        float(mean_return)
        - t_critical
        * standard_error
    )

    significant = (
        lcb > 0.0
    )

    lcb_rounded = float(
        round(lcb, 8)
    )

    return SignificanceEvaluation(
        mean_return=float(mean_return),
        std_dev=float(std_dev),
        effective_n=float(effective_n),
        t_critical=float(t_critical),
        standard_error=float(
            standard_error
        ),
        lcb_95=lcb_rounded,
        is_significant=significant,
        reason=(
            "POSITIVE_EFFECT_SIGNIFICANT"
            if significant
            else "POSITIVE_EFFECT_NOT_SIGNIFICANT"
        ),
        policy_fingerprint=policy_fp,
    )
