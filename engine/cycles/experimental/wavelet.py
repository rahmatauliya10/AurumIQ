"""Causal Continuous Wavelet Transform (CWT) multi-scale cycle analysis module."""
import math
from typing import Optional, Sequence, Tuple
import numpy as np
import pywt

from engine.core.types import WaveletResult
from engine.cycles.experimental.profile import (
    Cycle3BResearchProfile,
    ResearchCalibrationStatus,
)


def calculate_causal_wavelet(
    series: Sequence[float | int],
    wavelet_name: str = "morl",
    min_period: float = 4.0,
    max_period: Optional[float] = None,
    num_scales: int = 32,
    min_lookback: int = 32,
    profile: Optional[Cycle3BResearchProfile] = None,
) -> WaveletResult:
    """
    Calculate causal Continuous Wavelet Transform on a trailing window of observations up to T.

    Strict Causality & Safe Interior Support:
      - Uses only observations up to timestamp T (series[0..N-1]).
      - Zero centered lookahead windows or future padding.

    Missing Observation Safety (P3B-21):
      - If series contains None, NaN, or non-finite values, fails closed
        rather than dropping items (which would compress time spacing).

    Option A Cone of Influence (COI) Endpoint Safety (P3B-27):
      - In pure causal online operation, scales whose support extends beyond
        the available causal history boundary suffer from COI edge distortion.
      - If the dominant scale is contaminated by the COI boundary, or if the
        profile wavelet policy is incomplete, is_clean_endpoint is strictly False.
    """
    if not series or any(x is None or math.isnan(float(x)) or math.isinf(float(x)) for x in series):
        return WaveletResult(
            dominant_scale_period=None,
            energy_ratio=0.0,
            coi_contamination_pct=0.0,
            is_clean_endpoint=False,
            scales_analyzed=(),
            trusted_lag_bars=0,
        )

    clean_series = [float(x) for x in series]
    n = len(clean_series)

    eval_min_lookback = profile.min_lookback if profile is not None else min_lookback
    eval_wavelet_name = profile.wavelet_name if (profile is not None and wavelet_name == "morl") else wavelet_name
    eval_min_period = profile.min_period if (profile is not None and min_period == 4.0) else min_period
    eval_max_period = profile.max_period if (profile is not None and max_period is None) else max_period
    eval_num_scales = profile.num_scales if (profile is not None and num_scales == 32) else num_scales

    if n < eval_min_lookback:
        return WaveletResult(
            dominant_scale_period=None,
            energy_ratio=0.0,
            coi_contamination_pct=0.0,
            is_clean_endpoint=False,
            scales_analyzed=(),
            trusted_lag_bars=0,
        )

    arr = np.array(clean_series, dtype=np.float64)
    variance = float(np.var(arr))
    if variance < 1e-12:
        return WaveletResult(
            dominant_scale_period=None,
            energy_ratio=0.0,
            coi_contamination_pct=0.0,
            is_clean_endpoint=False,
            scales_analyzed=(),
            trusted_lag_bars=0,
        )

    # 1. Linear detrending
    x = np.arange(n, dtype=np.float64)
    coeffs = np.polyfit(x, arr, deg=1)
    detrended = arr - (coeffs[0] * x + coeffs[1])
    detrended = detrended - np.mean(detrended)

    # 2. Scale generation
    max_p = eval_max_period if eval_max_period is not None else float(n // 2)
    s_min = max(2.0, eval_min_period)
    s_max = max(s_min + 1.0, max_p)
    scales = np.geomspace(s_min, s_max, num=eval_num_scales)

    # 3. Continuous Wavelet Transform
    coefs, freqs = pywt.cwt(detrended, scales, eval_wavelet_name, sampling_period=1.0)
    power = np.abs(coefs) ** 2

    # 4. Energy distribution and dominant scale
    scale_energy = np.sum(power, axis=1)
    total_energy = float(np.sum(scale_energy))

    if total_energy < 1e-12:
        return WaveletResult(
            dominant_scale_period=None,
            energy_ratio=0.0,
            coi_contamination_pct=0.0,
            is_clean_endpoint=False,
            scales_analyzed=tuple([float(round(s, 2)) for s in scales]),
            trusted_lag_bars=0,
        )

    dom_idx = int(np.argmax(scale_energy))
    dom_scale = float(scales[dom_idx])
    dom_freq = float(freqs[dom_idx])
    dom_period = float(round(1.0 / dom_freq, 2)) if dom_freq > 0 else float(round(dom_scale, 2))
    dom_energy_ratio = float(round(scale_energy[dom_idx] / total_energy, 4))

    # 5. Option A Cone of Influence (COI) Safety (P3B-27)
    coi_distance = math.sqrt(2.0) * dom_scale
    trusted_lag = int(math.ceil(coi_distance))
    trusted_idx = (n - 1) - trusted_lag

    contaminated_scales_count = sum(1 for s in scales if (n // 2) < (math.sqrt(2.0) * s))
    coi_contamination_pct = float(round(contaminated_scales_count / len(scales), 4))

    # Clean endpoint resolution with strict policy completeness (Zero legacy numerical fallbacks on explicit profiles)
    if profile is None:
        is_clean = (trusted_idx >= 0) and (n >= coi_distance * 3.0) and (coi_contamination_pct <= 0.40)
    else:
        if profile.is_wavelet_policy_configured:
            max_coi = profile.wavelet_max_coi_contamination
            support_ratio = profile.wavelet_min_interior_support_ratio
            is_clean = (trusted_idx >= 0) and (n >= coi_distance * support_ratio) and (coi_contamination_pct <= max_coi)
        else:
            is_clean = False

    return WaveletResult(
        dominant_scale_period=dom_period,
        energy_ratio=dom_energy_ratio,
        coi_contamination_pct=coi_contamination_pct,
        is_clean_endpoint=is_clean,
        scales_analyzed=tuple([float(round(s, 2)) for s in scales]),
        trusted_lag_bars=trusted_lag,
    )
