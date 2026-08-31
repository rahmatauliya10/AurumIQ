"""Causal Hilbert Transform Instantaneous Phase and Amplitude module."""
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
    min_lookback: int = 32,
    profile: Optional[Cycle3BResearchProfile] = None,
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

    eval_min_lookback = profile.min_lookback if profile is not None else min_lookback

    if n < eval_min_lookback:
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

    # 4. Endpoint Reliability Resolution with strict policy completeness
    if profile is None:
        is_reliable = (n >= 48) and (stability >= 0.60) and (avg_velocity > 0.05) and (endpoint_amp > 1e-6)
    elif profile.status == ResearchCalibrationStatus.LEGACY_REFERENCE:
        min_lb = profile.hilbert_min_lookback or 48
        min_stab = profile.hilbert_min_stability or 0.60
        min_vel = profile.hilbert_min_velocity or 0.05
        min_amp = profile.hilbert_min_amplitude or 1e-6
        is_reliable = (n >= min_lb) and (stability >= min_stab) and (avg_velocity > min_vel) and (endpoint_amp > min_amp)
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
