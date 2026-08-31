"""Causal Autocorrelation Function (ACF) cycle analysis module."""
import math
from typing import Optional, Sequence, Tuple
import numpy as np

from engine.core.types import AcfResult, SampleEvaluation, SampleQuality
from engine.cycles.experimental.profile import (
    Cycle3BResearchProfile,
    ResearchCalibrationStatus,
)


def calculate_causal_acf(
    series: Sequence[float | int],
    max_lag: int = 64,
    min_lookback: int = 32,
    effective_n: Optional[float] = None,
    sample_eval: Optional[SampleEvaluation] = None,
    profile: Optional[Cycle3BResearchProfile] = None,
) -> AcfResult:
    """
    Calculate causal autocorrelation of a trailing lookback series.

    Strict Causality:
      - Uses only observations up to timestamp T (series[0..N-1]).
      - Evaluates lags k in [1, max_lag] strictly from closed historical bars.

    Missing Observation Safety (P3B-21):
      - If series contains None, NaN, or non-finite values, it fails closed
        rather than dropping items (which would compress time spacing).

    Profile & Effective-N-Aware Significance (P3B-25):
      - If profile is uncalibrated / acf policy incomplete:
        computes descriptive ACF series and candidate dominant lag,
        but is_significant is strictly False and confidence_bound is 0.0.
      - If significance bound is configured: uses n_sig = min(raw_n, eff_n).
    """
    # 1. Check for missing/corrupted values (P3B-21)
    if not series or any(x is None or math.isnan(float(x)) or math.isinf(float(x)) for x in series):
        eff_n = sample_eval.effective_n if sample_eval else (float(effective_n) if effective_n is not None else 0.0)
        return AcfResult(
            dominant_lag=None,
            autocorrelation=0.0,
            is_significant=False,
            confidence_bound=0.0,
            acf_series=(),
            effective_n=eff_n,
            sample_quality=SampleQuality.INSUFFICIENT,
        )

    clean_series = [float(x) for x in series]
    n = len(clean_series)

    # 2. Determine effective sample size and quality without historical leakage
    if profile is None:
        # Legacy historical function-call
        sig_bound = 1.96
        min_eff = 30.0
        is_policy_complete = True
        if sample_eval is not None:
            eff_n = sample_eval.effective_n
            sample_is_blocked = sample_eval.is_blocked
            sample_quality = sample_eval.quality
        elif effective_n is not None:
            eff_n = float(effective_n)
            sample_is_blocked = eff_n < 30.0
            if eff_n < 30.0:
                sample_quality = SampleQuality.INSUFFICIENT
            elif eff_n < 60.0:
                sample_quality = SampleQuality.LOW
            elif eff_n < 100.0:
                sample_quality = SampleQuality.MEDIUM
            else:
                sample_quality = SampleQuality.HIGH
        else:
            eff_n = 0.0
            sample_is_blocked = True
            sample_quality = SampleQuality.INSUFFICIENT
    else:
        # Explicit Profile supplied
        if profile.is_acf_policy_configured:
            sig_bound = profile.acf_bartlett_z_multiplier
            min_eff = profile.acf_min_effective_n
            is_policy_complete = True
            if sample_eval is not None:
                eff_n = sample_eval.effective_n
                sample_is_blocked = sample_eval.is_blocked
                sample_quality = sample_eval.quality
            elif effective_n is not None:
                eff_n = float(effective_n)
                sample_is_blocked = eff_n < min_eff
                sample_quality = SampleQuality.MEDIUM if eff_n >= min_eff else SampleQuality.INSUFFICIENT
            else:
                eff_n = 0.0
                sample_is_blocked = True
                sample_quality = SampleQuality.INSUFFICIENT
        else:
            sig_bound = 0.0
            min_eff = 0.0
            is_policy_complete = False
            eff_n = sample_eval.effective_n if sample_eval else (float(effective_n) if effective_n is not None else 0.0)
            sample_is_blocked = True
            sample_quality = sample_eval.quality if sample_eval else SampleQuality.INSUFFICIENT

    eval_min_lookback = profile.min_lookback if profile is not None else min_lookback
    eval_max_lag = profile.max_lag if profile is not None else max_lag

    if n < eval_min_lookback:
        return AcfResult(
            dominant_lag=None,
            autocorrelation=0.0,
            is_significant=False,
            confidence_bound=0.0,
            acf_series=(),
            effective_n=eff_n,
            sample_quality=SampleQuality.INSUFFICIENT,
        )

    # 3. Check for constant/flat series
    arr = np.array(clean_series, dtype=np.float64)
    variance = float(np.var(arr))
    if variance < 1e-12:
        return AcfResult(
            dominant_lag=None,
            autocorrelation=0.0,
            is_significant=False,
            confidence_bound=0.0,
            acf_series=tuple([1.0] + [0.0] * min(eval_max_lag, n - 1)),
            effective_n=eff_n,
            sample_quality=SampleQuality.INSUFFICIENT,
        )

    # 4. Detrend series using linear regression to remove macro trend drift
    x = np.arange(n, dtype=np.float64)
    coeffs = np.polyfit(x, arr, deg=1)
    detrended = arr - (coeffs[0] * x + coeffs[1])
    detrended_mean = np.mean(detrended)
    detrended_norm = detrended - detrended_mean
    denom = np.sum(detrended_norm ** 2)
    if denom < 1e-12:
        denom = 1e-12

    # 5. Compute causal ACF for lags k in [0, max_eval_lag]
    max_eval_lag = min(eval_max_lag, n // 2)
    acf_list = [1.0]  # Lag 0 is always 1.0

    for k in range(1, max_eval_lag + 1):
        num = np.sum(detrended_norm[k:] * detrended_norm[:-k])
        r_k = float(num / denom)
        acf_list.append(float(round(r_k, 4)))

    acf_tuple = tuple(acf_list)

    # 6. Significance bound resolution
    if not is_policy_complete:
        conf_bound = 0.0
    else:
        if eff_n >= min_eff and not sample_is_blocked:
            n_sig = min(float(n), float(eff_n))
            conf_bound = float(round(sig_bound / math.sqrt(n_sig), 4))
        else:
            conf_bound = float(round(sig_bound / math.sqrt(n), 4))

    # 7. Peak detection for dominant cyclical lag (k >= 3)
    dominant_lag: Optional[int] = None
    dominant_corr = 0.0

    for k in range(3, len(acf_list) - 1):
        if acf_list[k] > acf_list[k - 1] and acf_list[k] >= acf_list[k + 1]:
            if is_policy_complete and acf_list[k] <= conf_bound:
                continue
            if acf_list[k] > dominant_corr:
                dominant_corr = acf_list[k]
                dominant_lag = k

    if dominant_lag is None and len(acf_list) > 3:
        max_idx = int(np.argmax(acf_list[3:])) + 3
        if not is_policy_complete or acf_list[max_idx] > conf_bound:
            dominant_lag = max_idx
            dominant_corr = acf_list[max_idx]

    # Significance determination
    if not is_policy_complete or eff_n < min_eff or sample_is_blocked:
        is_significant = False
    else:
        is_significant = dominant_lag is not None and dominant_corr > conf_bound

    return AcfResult(
        dominant_lag=dominant_lag,
        autocorrelation=float(round(dominant_corr, 4)),
        is_significant=is_significant,
        confidence_bound=conf_bound,
        acf_series=acf_tuple,
        effective_n=eff_n,
        sample_quality=sample_quality,
    )
