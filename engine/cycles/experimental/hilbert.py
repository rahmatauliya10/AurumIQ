"""Causal Hilbert Transform instantaneous phase and amplitude tracking module."""
import math
from typing import Optional, Sequence
import numpy as np
from scipy.signal import hilbert

from engine.core.types import HilbertResult
from engine.cycles.experimental.profile import (
    Cycle3BResearchProfile,
    ResearchCalibrationStatus,
)


def calculate_causal_hilbert(
    series: Sequence[float | int],
    dominant_period: Optional[float] = None,
    min_lookback: int = 48,
    profile: Optional[Cycle3BResearchProfile] = None,
) -> HilbertResult:
    """
    Calculate causal instantaneous phase and amplitude via the analytic signal.

    Strict Causality & Endpoint Guard:
      - Uses strictly trailing closed historical series[0..N-1] up to timestamp T.
      - Never performs centered forward/backward smoothing across the endpoint.

    Missing Observation Safety (P3B-21):
      - If series contains None, NaN, or non-finite values, fails closed
        rather than dropping items (which would compress time spacing).

    Endpoint Reliability Policy:
      - If profile policy is incomplete, computes descriptive phase and amplitude,
        but is_endpoint_reliable is strictly False.
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

    eval_min_lookback = profile.min_lookback if profile is not None else min_lookback

    if n < max(eval_min_lookback, 16):
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

    # 2. Analytic Signal computation via Hilbert transform
    analytic_signal = hilbert(detrended)
    amplitude_envelope = np.abs(analytic_signal)
    instantaneous_phase_unwrapped = np.unwrap(np.angle(analytic_signal))

    endpoint_amp = float(amplitude_envelope[-1])
    endpoint_phase = float(np.angle(analytic_signal[-1]))  # Wrapped in [-pi, pi]
    endpoint_phase = float(round(endpoint_phase, 4))
    endpoint_amp = float(round(endpoint_amp, 4))

    # 3. Phase velocity and stability over the trailing lookback window (last 5 bars)
    w_eval = min(5, n - 1)
    if w_eval >= 2:
        d_phase = np.diff(instantaneous_phase_unwrapped[-w_eval:])
        avg_velocity = float(np.mean(d_phase))
        std_velocity = float(np.std(d_phase))
        monotonic_ratio = float(np.sum(d_phase > 0) / len(d_phase))
        cv = std_velocity / (avg_velocity + 1e-6) if avg_velocity > 0 else 1.0
        stability = max(0.0, min(1.0, 1.0 - cv * 0.5)) * monotonic_ratio
    else:
        stability = 0.0
        avg_velocity = 0.0

    stability = float(round(stability, 4))
    avg_velocity = float(round(avg_velocity, 4))

    # 4. Endpoint Reliability Resolution with strict policy completeness (Zero legacy numerical fallbacks on explicit profiles)
    if profile is None:
        is_reliable = (n >= 48) and (stability >= 0.60) and (avg_velocity > 0.05) and (endpoint_amp > 1e-6)
    else:
        if profile.is_hilbert_policy_configured:
            min_lb = profile.hilbert_min_lookback
            min_stab = profile.hilbert_min_stability
            min_vel = profile.hilbert_min_velocity
            min_amp = profile.hilbert_min_amplitude
            is_reliable = (n >= min_lb) and (stability >= min_stab) and (avg_velocity > min_vel) and (endpoint_amp > min_amp)
        else:
            is_reliable = False

    return HilbertResult(
        instantaneous_phase=endpoint_phase,
        instantaneous_amplitude=endpoint_amp,
        phase_velocity=avg_velocity,
        phase_stability=stability,
        is_endpoint_reliable=is_reliable,
    )
