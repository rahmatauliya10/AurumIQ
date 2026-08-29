"""Causal Wavelet Multi-Scale Spectral Analysis module using PyWavelets."""
import math
from typing import Optional, Sequence, Tuple
import numpy as np
import pywt

from engine.core.types import WaveletResult


def calculate_causal_wavelet(
    series: Sequence[float | int],
    wavelet_name: str = "morl",
    min_period: float = 4.0,
    max_period: float = 64.0,
    num_scales: int = 32,
    min_lookback: int = 32,
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
      - If lookback N is insufficient to provide uncompromised interior support,
        is_clean_endpoint is strictly False and wavelet reliability contribution is 0.
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

    if n < min_lookback:
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
        central_freq = pywt.central_frequency(wavelet_name)
    except Exception:
        wavelet_name = "morl"
        central_freq = pywt.central_frequency("morl")

    min_scale = max(1.0, min_period * central_freq)
    max_scale = max(min_scale + 2.0, min(float(n // 2), max_period * central_freq))
    scales = np.geomspace(min_scale, max_scale, num_scales)

    # 3. Continuous Wavelet Transform
    coefs, freqs = pywt.cwt(detrended, scales, wavelet_name, sampling_period=1.0)
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

    # Clean endpoint requires that lookback is sufficiently large to hold trusted interior points
    is_clean = (trusted_idx >= 0) and (n >= coi_distance * 3.0) and (coi_contamination_pct <= 0.40)

    return WaveletResult(
        dominant_scale_period=dom_period,
        energy_ratio=dom_energy_ratio,
        coi_contamination_pct=coi_contamination_pct,
        is_clean_endpoint=is_clean,
        scales_analyzed=tuple([float(round(s, 2)) for s in scales]),
        trusted_lag_bars=trusted_lag,
    )
