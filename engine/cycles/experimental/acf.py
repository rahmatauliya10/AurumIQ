"""Causal Autocorrelation Function (ACF) cycle analysis module."""
import math
from typing import Optional, Sequence, Tuple
import numpy as np

from engine.core.types import AcfResult, SampleEvaluation, SampleQuality


def calculate_causal_acf(
    series: Sequence[float | int],
    max_lag: int = 64,
    min_lookback: int = 32,
    effective_n: Optional[float] = None,
    sample_eval: Optional[SampleEvaluation] = None,
) -> AcfResult:
    """
    Calculate causal autocorrelation of a trailing lookback series.

    Strict Causality:
      - Uses only observations up to timestamp T (series[0..N-1]).
      - Evaluates lags k in [1, max_lag] strictly from closed historical bars.

    Missing Observation Safety (P3B-21):
      - If series contains None, NaN, or non-finite values, it fails closed
        rather than dropping items (which would compress time spacing).

    Effective-N-Aware Significance (P3B-25):
      - Bartlett bound uses n_sig = min(raw_n, eff_n) to prevent artificial
        confidence inflation.
      - If effective_n < 30 or sample_is_blocked -> is_significant is strictly False.
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

    # Determine effective sample size
    eff_n: float = 0.0
    sample_is_blocked = True
    if sample_eval is not None:
        eff_n = sample_eval.effective_n
        sample_is_blocked = sample_eval.is_blocked
    elif effective_n is not None:
        eff_n = float(effective_n)
        sample_is_blocked = eff_n < 30.0
    else:
        eff_n = 0.0
        sample_is_blocked = True

    if n < min_lookback:
        return AcfResult(
            dominant_lag=None,
            autocorrelation=0.0,
            is_significant=False,
            confidence_bound=0.0,
            acf_series=(),
            effective_n=eff_n,
            sample_quality=SampleQuality.INSUFFICIENT,
        )

    # 2. Check for constant/flat series
    arr = np.array(clean_series, dtype=np.float64)
    variance = float(np.var(arr))
    if variance < 1e-12:
        return AcfResult(
            dominant_lag=None,
            autocorrelation=0.0,
            is_significant=False,
            confidence_bound=0.0,
            acf_series=tuple([1.0] + [0.0] * min(max_lag, n - 1)),
            effective_n=eff_n,
            sample_quality=SampleQuality.INSUFFICIENT,
        )

    # 3. Detrend series using linear regression to remove macro trend drift
    x = np.arange(n, dtype=np.float64)
    coeffs = np.polyfit(x, arr, deg=1)
    detrended = arr - (coeffs[0] * x + coeffs[1])
    detrended_mean = np.mean(detrended)
    detrended_norm = detrended - detrended_mean
    denom = np.sum(detrended_norm ** 2)
    if denom < 1e-12:
        denom = 1e-12

    # 4. Compute causal ACF for lags k in [0, max_eval_lag]
    max_eval_lag = min(max_lag, n // 2)
    acf_list = [1.0]  # Lag 0 is always 1.0

    for k in range(1, max_eval_lag + 1):
        num = np.sum(detrended_norm[k:] * detrended_norm[:-k])
        r_k = float(num / denom)
        acf_list.append(float(round(r_k, 4)))

    acf_tuple = tuple(acf_list)

    # Effective-N-Aware Bartlett Bound (P3B-25 with min(raw_n, eff_n) defensive guard)
    if eff_n >= 30.0 and not sample_is_blocked:
        n_sig = min(float(n), float(eff_n))
        conf_bound = float(round(1.96 / math.sqrt(n_sig), 4))
    else:
        conf_bound = float(round(1.96 / math.sqrt(n), 4))

    # 5. Peak detection for dominant cyclical lag (k >= 3)
    dominant_lag: Optional[int] = None
    dominant_corr = 0.0

    for k in range(3, len(acf_list) - 1):
        if acf_list[k] > acf_list[k - 1] and acf_list[k] >= acf_list[k + 1] and acf_list[k] > conf_bound:
            if acf_list[k] > dominant_corr:
                dominant_corr = acf_list[k]
                dominant_lag = k

    if dominant_lag is None and len(acf_list) > 3:
        max_idx = int(np.argmax(acf_list[3:])) + 3
        if acf_list[max_idx] > conf_bound:
            dominant_lag = max_idx
            dominant_corr = acf_list[max_idx]

    # Sample Quality & Significance determination
    if eff_n < 30.0 or sample_is_blocked:
        sample_quality = SampleQuality.INSUFFICIENT
        is_significant = False
    elif eff_n < 60.0:
        sample_quality = SampleQuality.LOW
        is_significant = dominant_lag is not None and dominant_corr > conf_bound
    elif eff_n < 100.0:
        sample_quality = SampleQuality.MEDIUM
        is_significant = dominant_lag is not None and dominant_corr > conf_bound
    else:
        sample_quality = SampleQuality.HIGH
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
