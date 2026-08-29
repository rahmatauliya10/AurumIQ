"""Causal Hilbert Transform Instantaneous Phase and Amplitude module."""
import math
from typing import Optional, Sequence
import numpy as np
from scipy.signal import hilbert

from engine.core.types import HilbertResult


def calculate_causal_hilbert(
    series: Sequence[float | int],
    dominant_period: Optional[float] = None,
    min_lookback: int = 32,
) -> HilbertResult:
    """
    Calculate causal Hilbert Transform instantaneous phase and amplitude on trailing window.

    Strict Causality:
      - Uses only observations up to timestamp T (series[0..N-1]).
      - Zero centered future smoothing.

    Missing Observation Safety (P3B-21):
      - If series contains None, NaN, or non-finite values, fails closed.
    """
    if not series or any(x is None or math.isnan(float(x)) or math.isinf(float(x)) for x in series):
        return HilbertResult(
            instantaneous_phase=0.0,
            instantaneous_amplitude=0.0,
            phase_velocity=0.0,
            phase_stability=0.0,
            is_endpoint_reliable=False,
        )

    clean_series = [float(x) for x in series]
    n = len(clean_series)

    if n < min_lookback:
        return HilbertResult(
            instantaneous_phase=0.0,
            instantaneous_amplitude=0.0,
            phase_velocity=0.0,
            phase_stability=0.0,
            is_endpoint_reliable=False,
        )

    arr = np.array(clean_series, dtype=np.float64)
    variance = float(np.var(arr))
    if variance < 1e-12:
        return HilbertResult(
            instantaneous_phase=0.0,
            instantaneous_amplitude=0.0,
            phase_velocity=0.0,
            phase_stability=0.0,
            is_endpoint_reliable=False,
        )

    # 1. Linear detrending
    x = np.arange(n, dtype=np.float64)
    coeffs = np.polyfit(x, arr, deg=1)
    detrended = arr - (coeffs[0] * x + coeffs[1])
    detrended = detrended - np.mean(detrended)

    # 2. Analytic Signal computation via SciPy Hilbert transform
    analytic_signal = hilbert(detrended)
    amplitudes = np.abs(analytic_signal)
    phases = np.angle(analytic_signal)  # in [-pi, pi]

    endpoint_amp = float(round(amplitudes[-1], 4))
    endpoint_phase = float(round(phases[-1], 4))

    # 3. Phase Velocity and Stability Analysis over trailing 5 bars
    unwrapped_phases = np.unwrap(phases)
    eval_window = min(5, n - 1)
    phase_diffs = np.diff(unwrapped_phases[-eval_window - 1:])

    avg_velocity = float(np.mean(phase_diffs)) if len(phase_diffs) > 0 else 0.0
    positive_steps = sum(1 for d in phase_diffs if d > 0)
    monotonic_ratio = positive_steps / float(len(phase_diffs)) if len(phase_diffs) > 0 else 0.0

    if len(phase_diffs) > 1 and avg_velocity > 0:
        var_velocity = float(np.var(phase_diffs))
        cv = math.sqrt(var_velocity) / (avg_velocity + 1e-6)
        stability = max(0.0, min(1.0, 1.0 - cv * 0.5)) * monotonic_ratio
    else:
        stability = 0.0

    stability = float(round(stability, 4))
    avg_velocity = float(round(avg_velocity, 4))

    is_reliable = (n >= 48) and (stability >= 0.60) and (avg_velocity > 0.05) and (endpoint_amp > 1e-6)

    return HilbertResult(
        instantaneous_phase=endpoint_phase,
        instantaneous_amplitude=endpoint_amp,
        phase_velocity=avg_velocity,
        phase_stability=stability,
        is_endpoint_reliable=is_reliable,
    )
