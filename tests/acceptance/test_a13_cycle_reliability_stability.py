"""Acceptance Test A13: Cycle Reliability Consensus & Stability Protection."""
import pytest
from engine.core.types import (
    AcfResult,
    FftResult,
    HilbertResult,
    ReliabilityStatus,
    SampleQuality,
    WaveletResult,
)
from engine.cycles.experimental.reliability import evaluate_cycle_reliability


@pytest.mark.acceptance
def test_a13_cycle_reliability_high_consensus():
    """A13: Multi-method spectral consensus yields high reliability score."""
    acf = AcfResult(
        dominant_lag=16,
        autocorrelation=0.65,
        is_significant=True,
        confidence_bound=0.19,
        acf_series=(1.0, 0.5, 0.2, 0.65),
        effective_n=100.0,
        sample_quality=SampleQuality.HIGH,
    )
    fft = FftResult(
        dominant_period=16.0,
        dominant_frequency=0.0625,
        power_ratio=0.55,
        spectral_entropy=0.35,
        psd_top_frequencies=((0.0625, 0.55),),
        is_cycle_detected=True,
    )
    wavelet = WaveletResult(
        dominant_scale_period=16.0,
        energy_ratio=0.60,
        coi_contamination_pct=0.15,
        is_clean_endpoint=True,
        scales_analyzed=(4.0, 8.0, 16.0, 32.0),
    )
    hilbert = HilbertResult(
        instantaneous_phase=1.25,
        instantaneous_amplitude=15.0,
        phase_velocity=0.39,
        phase_stability=0.90,
        is_endpoint_reliable=True,
    )

    res = evaluate_cycle_reliability(
        acf=acf,
        fft=fft,
        wavelet=wavelet,
        hilbert=hilbert,
        effective_n=100.0,
        sample_quality=SampleQuality.HIGH,
    )

    assert res.method_agreement_pct == 100.0
    assert res.dominant_period_bars == 16.0
    assert res.reliability_score >= 60.0
    assert res.reliability_status == ReliabilityStatus.HIGH


@pytest.mark.acceptance
def test_a13_cycle_reliability_zeroed_on_subthreshold_sample():
    """A13: Ineffective sample (n_eff < 30.0) strictly forces reliability to 0.0."""
    acf = AcfResult(
        dominant_lag=16,
        autocorrelation=0.65,
        is_significant=False,
        confidence_bound=0.19,
        acf_series=(),
        effective_n=18.0,
        sample_quality=SampleQuality.INSUFFICIENT,
    )
    fft = FftResult(
        dominant_period=16.0,
        dominant_frequency=0.0625,
        power_ratio=0.55,
        spectral_entropy=0.35,
        psd_top_frequencies=(),
        is_cycle_detected=True,
    )
    wavelet = WaveletResult(
        dominant_scale_period=16.0,
        energy_ratio=0.60,
        coi_contamination_pct=0.15,
        is_clean_endpoint=True,
        scales_analyzed=(),
    )
    hilbert = HilbertResult(
        instantaneous_phase=1.25,
        instantaneous_amplitude=15.0,
        phase_velocity=0.39,
        phase_stability=0.90,
        is_endpoint_reliable=True,
    )

    res = evaluate_cycle_reliability(
        acf=acf,
        fft=fft,
        wavelet=wavelet,
        hilbert=hilbert,
        effective_n=18.0,
        sample_quality=SampleQuality.INSUFFICIENT,
        sample_is_blocked=True,
    )

    assert res.reliability_score == 0.0
    assert res.reliability_status == ReliabilityStatus.UNRELIABLE
    assert any("n_eff" in r for r in res.reasons)


@pytest.mark.acceptance
def test_a13_cycle_reliability_zeroed_on_material_method_disagreement():
    """A13: Spectral methods diverging by >30% dispersion strictly collapse consensus to 0."""
    acf = AcfResult(
        dominant_lag=10,
        autocorrelation=0.60,
        is_significant=True,
        confidence_bound=0.19,
        acf_series=(),
        effective_n=100.0,
        sample_quality=SampleQuality.HIGH,
    )
    fft = FftResult(
        dominant_period=32.0,  # 320% different from ACF 10
        dominant_frequency=0.03125,
        power_ratio=0.40,
        spectral_entropy=0.50,
        psd_top_frequencies=(),
        is_cycle_detected=True,
    )
    wavelet = WaveletResult(
        dominant_scale_period=60.0,
        energy_ratio=0.45,
        coi_contamination_pct=0.10,
        is_clean_endpoint=True,
        scales_analyzed=(),
    )
    hilbert = HilbertResult(
        instantaneous_phase=0.5,
        instantaneous_amplitude=5.0,
        phase_velocity=0.1,
        phase_stability=0.5,
        is_endpoint_reliable=True,
    )

    res = evaluate_cycle_reliability(
        acf=acf,
        fft=fft,
        wavelet=wavelet,
        hilbert=hilbert,
        effective_n=100.0,
        sample_quality=SampleQuality.HIGH,
    )

    assert res.method_agreement_pct == 0.0
    assert res.dominant_period_bars is None
    assert res.reliability_score == 0.0
    assert res.reliability_status == ReliabilityStatus.UNRELIABLE
