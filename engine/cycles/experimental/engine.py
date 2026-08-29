"""Experimental Spectral & Cycle Research Pipeline Engine (Phase 3B)."""
from datetime import datetime, timezone
from typing import Optional, Sequence

from engine.core.exceptions import IncompleteCandleError
from engine.core.types import (
    AcfResult,
    BaselineBenchmark,
    CandleData,
    Cycle3BExperimentalSnapshot,
    CycleReliabilityResult,
    FftResult,
    HilbertResult,
    PromotionStatus,
    ReliabilityStatus,
    SampleEvaluation,
    SampleQuality,
    WaveletResult,
)
from engine.cycles.experimental.acf import calculate_causal_acf
from engine.cycles.experimental.fft import calculate_causal_fft
from engine.cycles.experimental.hilbert import calculate_causal_hilbert
from engine.cycles.experimental.reliability import evaluate_cycle_reliability
from engine.cycles.experimental.wavelet import calculate_causal_wavelet
from engine.cycles.swing_duration import timeframe_to_seconds


class ExperimentalTimeCycleEngine:
    """
    Pure Python Phase 3B Experimental Spectral & Statistical Cycle Engine.

    Strict Invariants:
      1. Pure Python: Zero Django or ORM imports in engine package.
      2. Public API Point-in-Time Isolation (P3B-19):
         - Accepts `as_of`.
         - Engine itself filters `timestamp_close <= as_of AND is_closed == True`.
      3. Spectral Time-Grid Integrity (P3B-20):
         - Validates strict ascending sequence, exact timeframe interval spacing,
           no duplicates, and zero unclosed bars in sequence.
      4. Hard Locked Production Weight (P3B-24):
         - `production_weight` is permanently 0.0 until empirical promotion.
      5. Baseline Empirical Check (P3B-11):
         - If BaselineBenchmark.is_empirical is False -> promotion_status = BASELINE_NOT_EMPIRICAL.
    """

    def __init__(self, experimental_version: str = "3.1.0-3B"):
        self.experimental_version = experimental_version

    def analyze(
        self,
        candles: Sequence[CandleData],
        as_of: Optional[datetime] = None,
        timeframe: str = "15m",
        baseline_benchmark: Optional[BaselineBenchmark] = None,
        effective_n: Optional[float] = None,
        sample_eval: Optional[SampleEvaluation] = None,
        period_history: Optional[Sequence[float]] = None,
    ) -> Cycle3BExperimentalSnapshot:
        """
        Execute multi-method causal spectral analysis (ACF, FFT, Wavelet, Hilbert)
        on closed historical candles strictly knowable on or before `as_of`.
        """
        if not candles:
            raise ValueError("Experimental cycle analysis requires at least one candle.")

        # 1. Point-in-Time Isolation (P3B-19)
        as_of_utc = None
        if as_of is not None:
            as_of_utc = as_of.astimezone(timezone.utc) if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)

        valid_candles = []
        for c in candles:
            c_close = c.timestamp_close.astimezone(timezone.utc) if c.timestamp_close.tzinfo else c.timestamp_close.replace(tzinfo=timezone.utc)
            if as_of_utc is not None and c_close > as_of_utc:
                continue  # Exclude future candle strictly
            if not c.is_closed:
                # If an unclosed candle is present at or before as_of, raise error
                raise IncompleteCandleError(
                    f"Unclosed candle found at open={c.timestamp_open} on or before as_of={as_of_utc}."
                )
            valid_candles.append(c)

        if not valid_candles:
            raise ValueError(f"No closed historical candles available on or before as_of={as_of_utc}.")

        latest_candle = valid_candles[-1]
        analysis_timestamp = latest_candle.timestamp_close

        # 2. Time-Grid Regularity & Spacing Validation (P3B-20)
        tf_seconds = timeframe_to_seconds(timeframe)
        is_grid_regular = True
        grid_error_reason = ""

        for i in range(1, len(valid_candles)):
            prev_ts = valid_candles[i - 1].timestamp_close
            curr_ts = valid_candles[i].timestamp_close
            delta_sec = (curr_ts - prev_ts).total_seconds()

            if delta_sec <= 0:
                is_grid_regular = False
                grid_error_reason = f"Duplicate or non-ascending timestamp at index {i} ({curr_ts} vs {prev_ts})."
                break
            elif delta_sec != tf_seconds:
                is_grid_regular = False
                grid_error_reason = f"Irregular time spacing ({delta_sec}s != {tf_seconds}s) between index {i-1} and {i}."
                break

        # Determine effective sample size and quality
        close_series = [float(c.close) for c in valid_candles]
        raw_n = len(close_series)
        eff_n: float = 0.0
        sample_is_blocked = True
        sample_quality = SampleQuality.INSUFFICIENT

        if sample_eval is not None:
            eff_n = sample_eval.effective_n
            sample_is_blocked = sample_eval.is_blocked
            sample_quality = sample_eval.quality
        elif effective_n is not None:
            eff_n = float(effective_n)
            sample_is_blocked = eff_n < 30.0
            if eff_n < 30.0:
                sample_quality = SampleQuality.INSUFFICIENT
            elif eff_n < 60.0:
                sample_quality = SampleQuality.LOW
            elif eff_n < 100.0:
                sample_quality = SampleQuality.MEDIUM
            else:
                sample_quality = SampleQuality.HIGH
        else:
            eff_n = 0.0
            sample_is_blocked = True
            sample_quality = SampleQuality.INSUFFICIENT

        # If time-grid is broken or contains gaps, fail closed with zero reliability (P3B-20)
        if not is_grid_regular:
            zero_acf = AcfResult(None, 0.0, False, 0.0, (), eff_n, SampleQuality.INSUFFICIENT)
            zero_fft = FftResult(None, None, 0.0, 1.0, (), False)
            zero_wavelet = WaveletResult(None, 0.0, 1.0, False, ())
            zero_hilbert = HilbertResult(0.0, 0.0, 0.0, 0.0, False)
            zero_rel = CycleReliabilityResult(
                dominant_period_bars=None,
                acf_strength=0.0,
                fft_power_ratio=0.0,
                wavelet_scale_strength=0.0,
                hilbert_phase=0.0,
                phase_stability=0.0,
                method_agreement_pct=0.0,
                effective_n=eff_n,
                sample_quality=SampleQuality.INSUFFICIENT,
                reliability_score=0.0,
                reliability_status=ReliabilityStatus.UNRELIABLE,
                reasons=(f"Spectral time-grid integrity failure: {grid_error_reason}",),
            )
            return Cycle3BExperimentalSnapshot(
                timestamp=analysis_timestamp,
                timeframe=timeframe,
                acf=zero_acf,
                fft=zero_fft,
                wavelet=zero_wavelet,
                hilbert=zero_hilbert,
                reliability=zero_rel,
                experimental_version=self.experimental_version,
                promotion_status=PromotionStatus.BASELINE_NOT_EMPIRICAL,
            )

        # 3. Causal Spectral Calculations (A05, P3B-21, P3B-25)
        acf_res = calculate_causal_acf(
            series=close_series,
            max_lag=64,
            effective_n=eff_n,
            sample_eval=sample_eval,
        )

        fft_res = calculate_causal_fft(
            series=close_series,
            min_period=4.0,
            window_type="hann",
        )

        wavelet_res = calculate_causal_wavelet(
            series=close_series,
            wavelet_name="morl",
            min_period=4.0,
            max_period=64.0,
        )

        hilbert_res = calculate_causal_hilbert(
            series=close_series,
            dominant_period=fft_res.dominant_period,
        )

        # 4. Consolidated Multi-Method Reliability (A13, P3B-22)
        reliability_res = evaluate_cycle_reliability(
            acf=acf_res,
            fft=fft_res,
            wavelet=wavelet_res,
            hilbert=hilbert_res,
            effective_n=eff_n,
            sample_quality=sample_quality,
            sample_is_blocked=sample_is_blocked,
            period_history=period_history,
        )

        # 5. Promotion Status Assessment (A24, P3B-11)
        if baseline_benchmark is None or not baseline_benchmark.is_empirical:
            promotion_status = PromotionStatus.BASELINE_NOT_EMPIRICAL
        else:
            promotion_status = PromotionStatus.NOT_EVALUATED

        return Cycle3BExperimentalSnapshot(
            timestamp=analysis_timestamp,
            timeframe=timeframe,
            acf=acf_res,
            fft=fft_res,
            wavelet=wavelet_res,
            hilbert=hilbert_res,
            reliability=reliability_res,
            experimental_version=self.experimental_version,
            promotion_status=promotion_status,
        )
