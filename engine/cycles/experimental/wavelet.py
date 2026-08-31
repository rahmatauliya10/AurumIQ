"""Causal Wavelet Multi-Scale Spectral Analysis module using PyWavelets."""
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
    max_period: float = 64.0,
    num_scales: int = 32,
    min_lookback: int = 32,
    profile: Optional[Cycle3BResearchProfile] = None,
) -> WaveletResult:
    """
    Calculate causal Continuous Wavelet Transform (CWT) over trailing observations up to T.

    Strict Causality:
      - Uses only observations up to timestamp T (series[0..N-1]).
      - Zero centered lookahead or future-padded values.

    Missing Observation Safety (P3B-21):
      - If series contains None, NaN, or non-finite values, fails closed.

    Cone of Influence (COI) & Edge Handling (Option A — P3B-27):
      - Trailing endpoint T (index N-1) is ALWAYS right-boundary edge-sensitive.
      - Trusted wavelet evidence requires evaluating the latest coefficient outside the right COI:
        trusted_lag_bars = ceil(sqrt(2) * scale).
      - If lookback N is insufficient to provide uncompromised interior support or if
        profile policy is incomplete/uncalibrated, is_clean_endpoint is strictly False.
    """
    if not series or any(x is None or math.isnan(float(x)) or math.isinf(float(x)) for x in series):
        return WaveletResult(
            dominant_scale_period=None,
            energy_ratio=0.0,
            coi_contamination_pct=1.0,
            is_clean_endpoint=False,
            scales_analyzed=(),
            trusted_lag_bars=0,
        )

    clean_series = [float(x) for x in series]
    n = len(clean_series)

    eval_min_lookback = profile.min_lookback if profile is not None else min_lookback
    eval_wavelet_name = profile.wavelet_name if (profile is not None and wavelet_name == "morl") else wavelet_name
    eval_min_period = profile.min_period if (profile is not None and min_period == 4.0) else min_period
    eval_max_period = profile.max_period if (profile is not None and max_period == 64.0) else max_period
    eval_num_scales = profile.num_scales if (profile is not None and num_scales == 32) else num_scales

    if n < eval_min_lookback:
        return WaveletResult(
            dominant_scale_period=None,
            energy_ratio=0.0,
            coi_contamination_pct=1.0,
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

    # 1. Linear detrending and zero-mean centering
    x = np.arange(n, dtype=np.float64)
    coeffs_trend = np.polyfit(x, arr, deg=1)
    detrended = arr - (coeffs_trend[0] * x + coeffs_trend[1])
    detrended = detrended - np.mean(detrended)

    # 2. Scale generation using log-spaced scales (geomspace)
    try:
        central_freq = pywt.central_frequency(eval_wavelet_name)
    except Exception:
        eval_wavelet_name = "morl"
        central_freq = pywt.central_frequency("morl")

    min_scale = max(1.0, eval_min_period * central_freq)
    max_scale = max(min_scale + 2.0, min(float(n // 2), eval_max_period * central_freq))
    scales = np.geomspace(min_scale, max_scale, eval_num_scales)

    # 3. Continuous Wavelet Transform
    coefs, freqs = pywt.cwt(detrended, scales, eval_wavelet_name, sampling_period=1.0)
    power = np.abs(coefs) ** 2

    # 4. Total and Scale Energy
    total_energy = float(np.sum(power))
    if total_energy < 1e-12:
        return WaveletResult(
            dominant_scale_period=None,
            energy_ratio=0.0,
            coi_contamination_pct=1.0,
            is_clean_endpoint=False,
            scales_analyzed=tuple([float(round(s, 2)) for s in scales]),
            trusted_lag_bars=0,
        )

    scale_energy = np.sum(power, axis=1)
    best_scale_idx = int(np.argmax(scale_energy))
    dom_scale = float(scales[best_scale_idx])
    dom_freq = float(freqs[best_scale_idx])
    dom_period = float(round(1.0 / dom_freq, 2)) if dom_freq > 0 else None
    dom_energy_ratio = float(round(scale_energy[best_scale_idx] / total_energy, 4))

    # 5. Option A Cone of Influence (COI) Safety (P3B-27)
    coi_distance = math.sqrt(2.0) * dom_scale
    trusted_lag = int(math.ceil(coi_distance))
    trusted_idx = (n - 1) - trusted_lag

    contaminated_scales_count = sum(1 for s in scales if (n // 2) < (math.sqrt(2.0) * s))
    coi_contamination_pct = float(round(contaminated_scales_count / len(scales), 4))

    # Clean endpoint resolution with strict policy completeness
    if profile is None:
        is_clean = (trusted_idx >= 0) and (n >= coi_distance * 3.0) and (coi_contamination_pct <= 0.40)
    elif profile.status == ResearchCalibrationStatus.LEGACY_REFERENCE:
        max_coi = profile.wavelet_max_coi_contamination or 0.40
        support_ratio = profile.wavelet_min_interior_support_ratio or 3.0
        is_clean = (trusted_idx >= 0) and (n >= coi_distance * support_ratio) and (coi_contamination_pct <= max_coi)
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
