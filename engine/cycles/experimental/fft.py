"""Causal Discrete Fourier Transform (FFT) spectral cycle analysis module."""
import math
from typing import Optional, Sequence, Tuple
import numpy as np

from engine.core.types import FftResult
from engine.cycles.experimental.profile import Cycle3BResearchProfile


def calculate_causal_fft(
    series: Sequence[float | int],
    min_period: float = 4.0,
    max_period: Optional[float] = None,
    window_type: str = "hann",
    min_lookback: int = 32,
    profile: Optional[Cycle3BResearchProfile] = None,
) -> FftResult:
    """
    Calculate causal Discrete Fourier Transform on a trailing window of observations up to T.

    Strict Causality:
      - Uses only observations up to timestamp T (series[0..N-1]).
      - Zero centered lookahead windows or future padding.

    Missing Observation Safety (P3B-21):
      - If series contains None, NaN, or non-finite values, fails closed
        rather than dropping items (which would compress time spacing).

    Profile & Detection Governance:
      - If profile is uncalibrated (fft_min_power_ratio is None):
        computes descriptive dominant period, frequency, and PSD entropy,
        but is_cycle_detected is strictly False.
    """
    if not series or any(x is None or math.isnan(float(x)) or math.isinf(float(x)) for x in series):
        return FftResult(
            dominant_period=None,
            dominant_frequency=None,
            power_ratio=0.0,
            spectral_entropy=1.0,
            psd_top_frequencies=(),
            is_cycle_detected=False,
        )

    clean_series = [float(x) for x in series]
    n = len(clean_series)

    eval_min_lookback = profile.min_lookback if profile is not None else min_lookback
    eval_min_period = profile.min_period if (profile is not None and min_period == 4.0) else min_period
    eval_max_period = profile.max_period if (profile is not None and max_period is None) else max_period
    eval_window_type = profile.window_type if (profile is not None and window_type == "hann") else window_type

    if n < eval_min_lookback:
        return FftResult(
            dominant_period=None,
            dominant_frequency=None,
            power_ratio=0.0,
            spectral_entropy=1.0,
            psd_top_frequencies=(),
            is_cycle_detected=False,
        )

    arr = np.array(clean_series, dtype=np.float64)
    variance = float(np.var(arr))
    if variance < 1e-12:
        return FftResult(
            dominant_period=None,
            dominant_frequency=None,
            power_ratio=0.0,
            spectral_entropy=0.0,
            psd_top_frequencies=(),
            is_cycle_detected=False,
        )

    # 1. Linear detrending
    x = np.arange(n, dtype=np.float64)
    coeffs = np.polyfit(x, arr, deg=1)
    detrended = arr - (coeffs[0] * x + coeffs[1])
    detrended = detrended - np.mean(detrended)

    # 2. Windowing function
    if eval_window_type.lower() == "hann":
        window = np.hanning(n)
    elif eval_window_type.lower() == "hamming":
        window = np.hamming(n)
    else:
        window = np.ones(n, dtype=np.float64)

    windowed = detrended * window

    # 3. Real FFT computation
    rfft_vals = np.fft.rfft(windowed)
    freqs = np.fft.rfftfreq(n, d=1.0)
    psd = np.abs(rfft_vals) ** 2

    # 4. Filter to research-safe frequency band
    # DC component (index 0) is zeroed out
    psd[0] = 0.0

    max_p = eval_max_period if eval_max_period is not None else float(n // 2)
    min_f = 1.0 / max(max_p, eval_min_period + 1.0)
    max_f = 1.0 / max(eval_min_period, 2.0)

    # Valid mask for search
    valid_mask = (freqs >= min_f) & (freqs <= max_f)
    total_power = float(np.sum(psd[1:]))

    if total_power < 1e-12 or not np.any(valid_mask):
        return FftResult(
            dominant_period=None,
            dominant_frequency=None,
            power_ratio=0.0,
            spectral_entropy=1.0,
            psd_top_frequencies=(),
            is_cycle_detected=False,
        )

    # 5. Identify dominant frequency and power
    valid_indices = np.where(valid_mask)[0]
    best_idx = valid_indices[np.argmax(psd[valid_indices])]
    dom_freq = float(freqs[best_idx])
    dom_power = float(psd[best_idx])
    dom_period = float(round(1.0 / dom_freq, 2)) if dom_freq > 0 else None
    power_ratio = float(round(dom_power / total_power, 4)) if total_power > 0 else 0.0

    # 6. Spectral Entropy Calculation
    pos_psd = psd[1:]
    sum_pos = np.sum(pos_psd)
    if sum_pos > 1e-12:
        probs = pos_psd / sum_pos
        probs = probs[probs > 1e-12]
        entropy = -float(np.sum(probs * np.log2(probs)))
        max_entropy = math.log2(len(pos_psd)) if len(pos_psd) > 1 else 1.0
        normalized_entropy = float(round(max(0.0, min(1.0, entropy / max_entropy)), 4)) if max_entropy > 0 else 1.0
    else:
        normalized_entropy = 1.0

    # 7. Top dominant frequencies
    sorted_indices = np.argsort(psd[1:])[::-1] + 1  # 1-indexed to skip DC
    top_entries = []
    for idx in sorted_indices[:3]:
        f = float(freqs[idx])
        p = float(psd[idx])
        if p > 1e-12 and f > 0:
            top_entries.append((float(round(f, 4)), float(round(p / total_power, 4))))

    # 8. Detection Gate Resolution
    if profile is not None and profile.fft_min_power_ratio is None:
        is_detected = False
    else:
        threshold = profile.fft_min_power_ratio if (profile is not None and profile.fft_min_power_ratio is not None) else 0.15
        is_detected = (power_ratio >= threshold) and (dom_period is not None)

    return FftResult(
        dominant_period=dom_period,
        dominant_frequency=float(round(dom_freq, 4)) if dom_freq else None,
        power_ratio=power_ratio,
        spectral_entropy=normalized_entropy,
        psd_top_frequencies=tuple(top_entries),
        is_cycle_detected=is_detected,
    )
