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
from engine.cycles.experimental.profile import (
    Cycle3BResearchProfile,
    ResearchCalibrationStatus,
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
    profile: Optional[Cycle3BResearchProfile] = None,
) -> CycleReliabilityResult:
    """
    Consolidate multi-method spectral evidence (ACF, FFT, Wavelet, Hilbert) into a single reliability score.

    Acceptance Rule A13 & P3B-10 / P3B-22 / P3B-27:
      - If effective_n < min_eff or sample_is_blocked -> reliability_score = 0.0, status = UNRELIABLE.
      - If methods materially disagree (dominant periods diverge > moderate dispersion) -> reliability is strictly zeroed.
      - If cross-window period history is unstable -> reliability is strictly zeroed (P3B-22).
      - If Wavelet endpoint is not clean (Option A P3B-27), wavelet contribution is 0.0.

    Uncalibrated / Zero-Fallback Governance:
      - If profile reliability policy is incomplete, descriptive metrics
        are computed, but reliability_score is strictly 0.0 and status is UNRELIABLE.
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

    # 2. Check for Policy Completeness
    is_legacy = (profile is None or profile.status == ResearchCalibrationStatus.LEGACY_REFERENCE)
    is_complete = is_legacy or profile.is_reliability_policy_configured

    if not is_complete:
        consensus_period: Optional[float] = None
        agreement_pct = 0.0
        # Compute descriptive period if any method detected candidates
        all_candidate_periods = []
        if acf.dominant_lag is not None:
            all_candidate_periods.append(float(acf.dominant_lag))
        if fft.dominant_period is not None:
            all_candidate_periods.append(float(fft.dominant_period))
        if wavelet.dominant_scale_period is not None:
            all_candidate_periods.append(float(wavelet.dominant_scale_period))

        if all_candidate_periods:
            consensus_period = float(round(sum(all_candidate_periods) / len(all_candidate_periods), 2))
            if len(all_candidate_periods) > 1:
                p_min = min(all_candidate_periods)
                p_max = max(all_candidate_periods)
                p_mean = sum(all_candidate_periods) / len(all_candidate_periods)
                disp = (p_max - p_min) / p_mean if p_mean > 0 else 1.0
                agreement_pct = float(round(max(0.0, (1.0 - disp) * 100.0), 1))

        return CycleReliabilityResult(
            dominant_period_bars=consensus_period,
            acf_strength=acf.autocorrelation,
            fft_power_ratio=fft.power_ratio,
            wavelet_scale_strength=wavelet.energy_ratio,
            hilbert_phase=hilbert.instantaneous_phase,
            phase_stability=hilbert.phase_stability,
            method_agreement_pct=agreement_pct,
            effective_n=effective_n,
            sample_quality=SampleQuality.INSUFFICIENT,
            reliability_score=0.0,
            reliability_status=ReliabilityStatus.UNRELIABLE,
            reasons=("CALIBRATION_REQUIRED: Empirical reliability thresholds are not configured for XAUUSD.",),
        )

    # 3. Resolving Policy Constants
    if is_legacy:
        disp_high = (profile.dispersion_high_threshold if profile else None) or 0.15
        disp_mod = (profile.dispersion_moderate_threshold if profile else None) or 0.30
        single_agr = (profile.single_method_agreement_pct if profile else None) or 35.0
        mod_agr = (profile.moderate_method_agreement_pct if profile else None) or 65.0
        cross_disp = (profile.cross_window_dispersion_threshold if profile else None) or 0.35
        band_high = (profile.reliability_band_high if profile else None) or 60.0
        band_mod = (profile.reliability_band_moderate if profile else None) or 35.0
        band_low = (profile.reliability_band_low if profile else None) or 15.0
        w_acf = (profile.reliability_weight_acf if profile else None) or 30.0
        w_fft = (profile.reliability_weight_fft if profile else None) or 30.0
        w_wav = (profile.reliability_weight_wavelet if profile else None) or 20.0
        w_hil = (profile.reliability_weight_hilbert if profile else None) or 20.0
        fft_mult = (profile.fft_power_score_multiplier if profile else None) or 2.5
        q_low = (profile.quality_multiplier_low if profile else None) or 0.5
        q_med = (profile.quality_multiplier_medium if profile else None) or 0.8
        q_high = (profile.quality_multiplier_high if profile else None) or 1.0
        min_eff = (profile.min_effective_n if profile else None) or 30.0
    else:
        disp_high = profile.dispersion_high_threshold
        disp_mod = profile.dispersion_moderate_threshold
        single_agr = profile.single_method_agreement_pct
        mod_agr = profile.moderate_method_agreement_pct
        cross_disp = profile.cross_window_dispersion_threshold
        band_high = profile.reliability_band_high
        band_mod = profile.reliability_band_moderate
        band_low = profile.reliability_band_low
        w_acf = profile.reliability_weight_acf
        w_fft = profile.reliability_weight_fft
        w_wav = profile.reliability_weight_wavelet
        w_hil = profile.reliability_weight_hilbert
        fft_mult = profile.fft_power_score_multiplier
        q_low = profile.quality_multiplier_low
        q_med = profile.quality_multiplier_medium
        q_high = profile.quality_multiplier_high
        min_eff = profile.min_effective_n

    # 4. Method Agreement Evaluation
    consensus_period = None
    agreement_pct = 0.0

    if len(valid_periods) == 0:
        reasons.append("No method detected a statistically significant cycle.")
    elif len(valid_periods) == 1:
        consensus_period = float(round(valid_periods[0][1], 2))
        agreement_pct = single_agr  # Isolated single-method detection
        reasons.append(f"Cycle detected only by {valid_periods[0][0]}. Single method agreement is low.")
    else:
        periods = [p[1] for p in valid_periods]
        p_min = min(periods)
        p_max = max(periods)
        p_mean = sum(periods) / len(periods)
        dispersion = (p_max - p_min) / p_mean if p_mean > 0 else 1.0

        if dispersion <= disp_high:  # High agreement
            agreement_pct = 100.0
            total_weight = sum(p[2] for p in valid_periods)
            consensus_period = float(round(sum(p[1] * p[2] for p in valid_periods) / total_weight, 2))
            reasons.append(f"Strong spectral consensus across {len(valid_periods)} methods (~{consensus_period} bars).")
        elif dispersion <= disp_mod:  # Moderate agreement
            agreement_pct = mod_agr
            total_weight = sum(p[2] for p in valid_periods)
            consensus_period = float(round(sum(p[1] * p[2] for p in valid_periods) / total_weight, 2))
            reasons.append(f"Moderate spectral agreement with {round(dispersion * 100, 1)}% dispersion.")
        else:  # Material disagreement (> mod dispersion)
            agreement_pct = 0.0
            consensus_period = None
            reasons.append(f"Spectral methods materially disagree (dispersion {round(dispersion * 100, 1)}% > {int(disp_mod*100)}%). Zero consensus.")

    # 5. Cross-Window Temporal Stability Check (P3B-22)
    window_is_unstable = False
    if period_history is not None and len(period_history) >= 3:
        clean_hist = [float(p) for p in period_history if p is not None and p > 0]
        if len(clean_hist) >= 3:
            h_min = min(clean_hist)
            h_max = max(clean_hist)
            h_mean = sum(clean_hist) / len(clean_hist)
            h_dispersion = (h_max - h_min) / h_mean if h_mean > 0 else 1.0
            if h_dispersion > cross_disp:
                window_is_unstable = True
                reasons.append(f"Cross-window period instability detected (dispersion {round(h_dispersion * 100, 1)}% > {int(cross_disp*100)}%).")

    # 6. Fail-Closed Sample Guard & Zero-Reliability Gates (A13, P3B-10, P3B-22)
    if effective_n < min_eff or sample_is_blocked or sample_quality == SampleQuality.INSUFFICIENT or window_is_unstable or agreement_pct == 0.0:
        if effective_n < min_eff or sample_is_blocked:
            reasons.append(f"Effective sample size (n_eff={round(effective_n, 1)}) < {min_eff}. Reliability locked to 0.0.")
        return CycleReliabilityResult(
            dominant_period_bars=consensus_period if not window_is_unstable else None,
            acf_strength=acf.autocorrelation,
            fft_power_ratio=fft.power_ratio,
            wavelet_scale_strength=wavelet.energy_ratio,
            hilbert_phase=hilbert.instantaneous_phase,
            phase_stability=hilbert.phase_stability,
            method_agreement_pct=agreement_pct if not window_is_unstable else 0.0,
            effective_n=effective_n,
            sample_quality=SampleQuality.INSUFFICIENT if effective_n < min_eff else sample_quality,
            reliability_score=0.0,
            reliability_status=ReliabilityStatus.UNRELIABLE,
            reasons=tuple(reasons),
        )

    # 7. Multi-Method Composite Reliability Score
    score_acf = (acf.autocorrelation if acf.is_significant else 0.0) * w_acf
    score_fft = min(1.0, fft.power_ratio * fft_mult) * w_fft
    score_wavelet = (wavelet.energy_ratio * (1.0 - wavelet.coi_contamination_pct) * w_wav) if wavelet.is_clean_endpoint else 0.0
    score_hilbert = (hilbert.phase_stability if hilbert.is_endpoint_reliable else 0.0) * w_hil

    raw_score = score_acf + score_fft + score_wavelet + score_hilbert
    agreement_mult = agreement_pct / 100.0

    if sample_quality == SampleQuality.LOW:
        quality_mult = q_low
    elif sample_quality == SampleQuality.MEDIUM:
        quality_mult = q_med
    else:
        quality_mult = q_high

    final_score = float(round(raw_score * agreement_mult * quality_mult, 2))

    if final_score >= band_high and agreement_pct >= 80.0:
        status = ReliabilityStatus.HIGH
    elif final_score >= band_mod and agreement_pct >= 50.0:
        status = ReliabilityStatus.MODERATE
    elif final_score >= band_low:
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
