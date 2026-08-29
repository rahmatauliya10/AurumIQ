"""Cycle Reliability Engine combining multi-method spectral consensus (A13, P3B-22, P3B-27)."""
import math
from typing import List, Optional, Sequence, Tuple
import numpy as np

from engine.core.types import (
    AcfResult,
    CycleReliabilityResult,
    FftResult,
    HilbertResult,
    ReliabilityStatus,
    SampleQuality,
    WaveletResult,
)


def evaluate_cycle_reliability(
    acf: AcfResult,
    fft: FftResult,
    wavelet: WaveletResult,
    hilbert: HilbertResult,
    effective_n: float,
    sample_quality: SampleQuality,
    sample_is_blocked: bool = False,
    period_history: Optional[Sequence[float]] = None,
) -> CycleReliabilityResult:
    """
    Consolidate multi-method spectral evidence (ACF, FFT, Wavelet, Hilbert) into a single reliability score.

    Acceptance Rule A13 & P3B-10 / P3B-22 / P3B-27:
      - If effective_n < 30.0 or sample_is_blocked -> reliability_score = 0.0, status = UNRELIABLE.
      - If methods materially disagree (dominant periods diverge > 30%) -> reliability is strictly zeroed.
      - If cross-window period history is unstable (dispersion > 35%) -> reliability is strictly zeroed (P3B-22).
      - If Wavelet endpoint is not clean (Option A P3B-27), wavelet contribution is 0.0.
    """
    reasons: List[str] = []

    # 1. Collect candidate dominant periods
    valid_periods: List[Tuple[str, float, float]] = []  # (method_name, period, weight/power)

    if acf.dominant_lag is not None and acf.is_significant:
        valid_periods.append(("ACF", float(acf.dominant_lag), max(0.1, acf.autocorrelation)))

    if fft.dominant_period is not None and fft.is_cycle_detected:
        valid_periods.append(("FFT", float(fft.dominant_period), max(0.1, fft.power_ratio)))

    # Wavelet contributes only if clean interior support is proven (P3B-27)
    if wavelet.dominant_scale_period is not None and wavelet.is_clean_endpoint and wavelet.coi_contamination_pct <= 0.40:
        valid_periods.append(("Wavelet", float(wavelet.dominant_scale_period), max(0.1, wavelet.energy_ratio)))

    # 2. Method Agreement Evaluation
    consensus_period: Optional[float] = None
    agreement_pct = 0.0

    if len(valid_periods) == 0:
        reasons.append("No method detected a statistically significant cycle.")
    elif len(valid_periods) == 1:
        consensus_period = float(round(valid_periods[0][1], 2))
        agreement_pct = 35.0  # Isolated single-method detection
        reasons.append(f"Cycle detected only by {valid_periods[0][0]}. Single method agreement is low.")
    else:
        periods = [p[1] for p in valid_periods]
        p_min = min(periods)
        p_max = max(periods)
        p_mean = sum(periods) / len(periods)
        dispersion = (p_max - p_min) / p_mean if p_mean > 0 else 1.0

        if dispersion <= 0.15:  # Within 15% dispersion -> High agreement
            agreement_pct = 100.0
            total_weight = sum(p[2] for p in valid_periods)
            consensus_period = float(round(sum(p[1] * p[2] for p in valid_periods) / total_weight, 2))
            reasons.append(f"Strong spectral consensus across {len(valid_periods)} methods (~{consensus_period} bars).")
        elif dispersion <= 0.30:  # Moderate agreement
            agreement_pct = 65.0
            total_weight = sum(p[2] for p in valid_periods)
            consensus_period = float(round(sum(p[1] * p[2] for p in valid_periods) / total_weight, 2))
            reasons.append(f"Moderate spectral agreement with {round(dispersion * 100, 1)}% dispersion.")
        else:  # Material disagreement (> 30% dispersion)
            agreement_pct = 0.0
            consensus_period = None
            reasons.append(f"Spectral methods materially disagree (dispersion {round(dispersion * 100, 1)}% > 30%). Zero consensus.")

    # 3. Cross-Window Temporal Stability Check (P3B-22)
    window_is_unstable = False
    if period_history is not None and len(period_history) >= 3:
        clean_hist = [float(p) for p in period_history if p is not None and p > 0]
        if len(clean_hist) >= 3:
            h_min = min(clean_hist)
            h_max = max(clean_hist)
            h_mean = sum(clean_hist) / len(clean_hist)
            h_dispersion = (h_max - h_min) / h_mean if h_mean > 0 else 1.0
            if h_dispersion > 0.35:
                window_is_unstable = True
                reasons.append(f"Cross-window period instability detected (dispersion {round(h_dispersion * 100, 1)}% > 35%).")

    # 4. Fail-Closed Sample Guard & Zero-Reliability Gates (P3B-10, P3B-22)
    if effective_n < 30.0 or sample_is_blocked or sample_quality == SampleQuality.INSUFFICIENT or window_is_unstable or agreement_pct == 0.0:
        if effective_n < 30.0 or sample_is_blocked:
            reasons.append(f"Effective sample size (n_eff={round(effective_n, 1)}) < 30.0. Reliability locked to 0.0.")
        return CycleReliabilityResult(
            dominant_period_bars=consensus_period if not window_is_unstable else None,
            acf_strength=acf.autocorrelation,
            fft_power_ratio=fft.power_ratio,
            wavelet_scale_strength=wavelet.energy_ratio,
            hilbert_phase=hilbert.instantaneous_phase,
            phase_stability=hilbert.phase_stability,
            method_agreement_pct=agreement_pct if not window_is_unstable else 0.0,
            effective_n=effective_n,
            sample_quality=SampleQuality.INSUFFICIENT if effective_n < 30.0 else sample_quality,
            reliability_score=0.0,
            reliability_status=ReliabilityStatus.UNRELIABLE,
            reasons=tuple(reasons),
        )

    # 5. Multi-Method Composite Reliability Score
    score_acf = (acf.autocorrelation if acf.is_significant else 0.0) * 30.0
    score_fft = min(1.0, fft.power_ratio * 2.5) * 30.0
    # Wavelet contributes strictly if clean interior support is verified (P3B-27)
    score_wavelet = (wavelet.energy_ratio * (1.0 - wavelet.coi_contamination_pct) * 20.0) if wavelet.is_clean_endpoint else 0.0
    score_hilbert = (hilbert.phase_stability if hilbert.is_endpoint_reliable else 0.0) * 20.0

    raw_score = score_acf + score_fft + score_wavelet + score_hilbert

    agreement_mult = agreement_pct / 100.0

    if sample_quality == SampleQuality.LOW:
        quality_mult = 0.5
    elif sample_quality == SampleQuality.MEDIUM:
        quality_mult = 0.8
    else:
        quality_mult = 1.0

    final_score = float(round(raw_score * agreement_mult * quality_mult, 2))

    if final_score >= 60.0 and agreement_pct >= 80.0:
        status = ReliabilityStatus.HIGH
    elif final_score >= 35.0 and agreement_pct >= 50.0:
        status = ReliabilityStatus.MODERATE
    elif final_score >= 15.0:
        status = ReliabilityStatus.LOW
    else:
        status = ReliabilityStatus.UNRELIABLE

    return CycleReliabilityResult(
        dominant_period_bars=consensus_period,
        acf_strength=acf.autocorrelation,
        fft_power_ratio=fft.power_ratio,
        wavelet_scale_strength=wavelet.energy_ratio,
        hilbert_phase=hilbert.instantaneous_phase,
        phase_stability=hilbert.phase_stability,
        method_agreement_pct=agreement_pct,
        effective_n=effective_n,
        sample_quality=sample_quality,
        reliability_score=final_score,
        reliability_status=status,
        reasons=tuple(reasons),
    )
