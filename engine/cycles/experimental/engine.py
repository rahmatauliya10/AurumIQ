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
from engine.cycles.experimental.profile import (
    Cycle3BResearchProfile,
    ResearchCalibrationStatus,
)
from engine.cycles.experimental.reliability import evaluate_cycle_reliability
from engine.cycles.experimental.wavelet import calculate_causal_wavelet
from engine.cycles.swing_duration import timeframe_to_seconds


def normalize_target_instrument(inst: str) -> str:
    """Canonical repository instrument target normalizer."""
    if not inst:
        return ""
    s = inst.upper().replace("/", "").replace("_", "").strip()
    if s in ("XAUUSD", "GOLD"):
        return "XAUUSD"
    if s in ("XAUT", "XAUTUSD", "XAUTF"):
        return "XAUT"
    return s


class ExperimentalTimeCycleEngine:
    """
    Pure Python Phase 3B Experimental Spectral & Statistical Cycle Engine.

    Strict Invariants:
      1. Pure Python: Zero Django or ORM imports in engine package.
      2. Public API Point-in-Time Isolation (P3B-19):
         - Accepts `as_of`.
         - Engine itself filters `timestamp_close <= as_of AND is_closed == True`.
         - Unclosed candles strictly after as_of are completely ignored.
         - Unclosed candles at or before as_of raise IncompleteCandleError.
      3. Spectral Time-Grid Integrity (P3B-20):
         - Validates strict ascending sequence, exact timeframe interval spacing,
           no duplicates, and zero unclosed bars in sequence.
      4. Hard Locked Production Weight (P3B-24):
         - `production_weight` is permanently 0.0 under all lifecycle profiles.
      5. Strict Symmetric Target Instrument Segregation:
         - Rejects ALL instrument / profile / engine target mismatches symmetrically.
      6. Zero Experiment Metric Fabrication:
         - analyze() does not fabricate backtest performance metrics.
      7. Runtime Timeframe Rigidity:
         - Profile timeframe must match execution timeframe.
    """

    def __init__(
        self,
        profile: Optional[Cycle3BResearchProfile] = None,
        experimental_version: str = "3.1.0-3B",
    ):
        self.profile = profile if profile is not None else Cycle3BResearchProfile.legacy_xaut_research_profile()
        self.experimental_version = experimental_version

    @classmethod
    def for_legacy_xaut(cls) -> "ExperimentalTimeCycleEngine":
        """Factory method for verified historical XAUT research reference profile."""
        return cls(profile=Cycle3BResearchProfile.legacy_xaut_research_profile())

    @classmethod
    def for_xauusd(
        cls,
        profile: Optional[Cycle3BResearchProfile] = None,
        timeframe: Optional[str] = None,
    ) -> "ExperimentalTimeCycleEngine":
        """
        Factory method for XAUUSD target research profile.
        Strictly validates target instrument matches XAUUSD and enforces PENDING_DATA by default.
        """
        if profile is not None:
            target = normalize_target_instrument(profile.target_instrument)
            if target != "XAUUSD":
                raise ValueError(
                    f"Invalid profile for XAUUSD engine: target instrument is '{profile.target_instrument}', "
                    f"expected 'XAUUSD'."
                )
            if timeframe is not None and profile.timeframe is not None and profile.timeframe != timeframe:
                raise ValueError(
                    f"Profile timeframe '{profile.timeframe}' does not match requested timeframe '{timeframe}'."
                )
            return cls(profile=profile)
        return cls(profile=Cycle3BResearchProfile.uncalibrated_xauusd_research_profile(timeframe=timeframe))

    def analyze(
        self,
        candles: Sequence[CandleData],
        as_of: Optional[datetime] = None,
        timeframe: str = "15m",
        baseline_benchmark: Optional[BaselineBenchmark] = None,
        effective_n: Optional[float] = None,
        sample_eval: Optional[SampleEvaluation] = None,
        period_history: Optional[Sequence[float]] = None,
        instrument: Optional[str] = None,
        profile: Optional[Cycle3BResearchProfile] = None,
    ) -> Cycle3BExperimentalSnapshot:
        """
        Execute multi-method causal spectral analysis (ACF, FFT, Wavelet, Hilbert)
        on closed historical candles strictly knowable on or before `as_of`.
        """
        if not candles:
            raise ValueError("Experimental cycle analysis requires at least one candle.")

        # Symmetric Instrument & Profile Target Validation
        engine_target = normalize_target_instrument(self.profile.target_instrument)

        if profile is not None:
            eff_profile = profile
            eff_target = normalize_target_instrument(eff_profile.target_instrument)
            if eff_target != engine_target:
                raise ValueError(
                    f"Per-call profile target instrument '{eff_profile.target_instrument}' does not match engine profile target '{self.profile.target_instrument}'."
                )
        else:
            eff_profile = self.profile
            eff_target = engine_target

        if instrument is not None:
            req_target = normalize_target_instrument(instrument)
            if req_target != eff_target:
                raise ValueError(
                    f"Explicit requested instrument '{instrument}' does not match effective profile target '{eff_profile.target_instrument}'."
                )

        # Runtime Timeframe Rigidity for Target Instrument
        if eff_profile.timeframe is not None and eff_profile.timeframe != timeframe:
            raise ValueError(
                f"Profile timeframe '{eff_profile.timeframe}' does not match analysis timeframe '{timeframe}'."
            )

        # 1. Point-in-Time & Closed Candle Isolation (P3B-19 & Closed-Candle Split)
        as_of_utc = None
        if as_of is not None:
            as_of_utc = as_of.astimezone(timezone.utc) if as_of.tzinfo is not None else as_of.replace(tzinfo=timezone.utc)

        valid_candles = []
        for c in candles:
            c_close = c.timestamp_close.astimezone(timezone.utc) if c.timestamp_close.tzinfo is not None else c.timestamp_close.replace(tzinfo=timezone.utc)
            if as_of_utc is not None and c_close > as_of_utc:
                # Candle is strictly in the future relative to as_of -> completely ignore
                continue
            if not c.is_closed:
                # Unclosed candle on or before as_of is forbidden
                raise IncompleteCandleError(
                    f"Unclosed candle found at open={c.timestamp_open} on or before as_of={as_of_utc}."
                )
            valid_candles.append(c)

        if not valid_candles:
            raise ValueError(f"No closed historical candles available on or before as_of={as_of_utc}.")

        latest_candle = valid_candles[-1]
        analysis_timestamp = latest_candle.timestamp_close.astimezone(timezone.utc) if latest_candle.timestamp_close.tzinfo is not None else latest_candle.timestamp_close.replace(tzinfo=timezone.utc)

        # 2. Time-Grid Regularity & Spacing Validation (P3B-20)
        tf_seconds = timeframe_to_seconds(timeframe)
        is_grid_regular = True
        grid_error_reason = ""

        for i in range(1, len(valid_candles)):
            prev_ts = valid_candles[i - 1].timestamp_close
            curr_ts = valid_candles[i].timestamp_close
            prev_utc = prev_ts.astimezone(timezone.utc) if prev_ts.tzinfo is not None else prev_ts.replace(tzinfo=timezone.utc)
            curr_utc = curr_ts.astimezone(timezone.utc) if curr_ts.tzinfo is not None else curr_ts.replace(tzinfo=timezone.utc)
            delta_sec = (curr_utc - prev_utc).total_seconds()

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
        eff_n: float = 0.0
        sample_is_blocked = True
        sample_quality = SampleQuality.INSUFFICIENT

        is_legacy = (eff_profile.status == ResearchCalibrationStatus.LEGACY_REFERENCE or eff_profile.target_instrument != "XAUUSD")

        if sample_eval is not None:
            eff_n = sample_eval.effective_n
            sample_is_blocked = sample_eval.is_blocked
            sample_quality = sample_eval.quality
        elif effective_n is not None:
            eff_n = float(effective_n)
            if is_legacy:
                min_eff = eff_profile.min_effective_n or 30.0
                sample_is_blocked = eff_n < min_eff
                if eff_n < min_eff:
                    sample_quality = SampleQuality.INSUFFICIENT
                elif eff_n < 60.0:
                    sample_quality = SampleQuality.LOW
                elif eff_n < 100.0:
                    sample_quality = SampleQuality.MEDIUM
                else:
                    sample_quality = SampleQuality.HIGH
            else:
                if eff_profile.is_reliability_policy_configured:
                    min_eff = eff_profile.min_effective_n
                    sample_is_blocked = eff_n < min_eff
                    sample_quality = SampleQuality.MEDIUM if eff_n >= min_eff else SampleQuality.INSUFFICIENT
                else:
                    sample_is_blocked = True
                    sample_quality = SampleQuality.INSUFFICIENT
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
                profile_name=eff_profile.name,
                calibration_status=eff_profile.status.value,
                instrument=eff_profile.target_instrument,
            )

        # 3. Causal Spectral Calculations (A05, P3B-21, P3B-25)
        acf_res = calculate_causal_acf(
            series=close_series,
            max_lag=eff_profile.max_lag,
            effective_n=eff_n,
            sample_eval=sample_eval,
            profile=eff_profile,
        )

        fft_res = calculate_causal_fft(
            series=close_series,
            min_period=eff_profile.min_period,
            max_period=eff_profile.max_period,
            window_type=eff_profile.window_type,
            profile=eff_profile,
        )

        wavelet_res = calculate_causal_wavelet(
            series=close_series,
            wavelet_name=eff_profile.wavelet_name,
            min_period=eff_profile.min_period,
            max_period=eff_profile.max_period,
            num_scales=eff_profile.num_scales,
            profile=eff_profile,
        )

        hilbert_res = calculate_causal_hilbert(
            series=close_series,
            dominant_period=fft_res.dominant_period,
            profile=eff_profile,
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
            profile=eff_profile,
        )

        # 5. Promotion Status Assessment without fabricating experimental performance metrics
        if eff_target == "XAUUSD":
            if not eff_profile.is_promotion_policy_configured:
                promotion_status = PromotionStatus.POLICY_NOT_CONFIGURED
            elif baseline_benchmark is None or not baseline_benchmark.is_empirical:
                promotion_status = PromotionStatus.BLOCKED_BY_PHASE6
            else:
                # Check baseline provenance completeness
                inst_norm = normalize_target_instrument(baseline_benchmark.instrument) if baseline_benchmark.instrument else ""
                tf_match = (eff_profile.timeframe is not None and baseline_benchmark.timeframe == eff_profile.timeframe)
                source_ok = bool(baseline_benchmark.source and baseline_benchmark.source.strip())
                dates_ok = bool(
                    baseline_benchmark.data_start
                    and baseline_benchmark.data_end
                    and baseline_benchmark.as_of
                    and (baseline_benchmark.data_start <= baseline_benchmark.data_end <= baseline_benchmark.as_of)
                )
                pit_ok = baseline_benchmark.pit_safe
                phase6_ok = baseline_benchmark.phase6_validated

                if inst_norm == "XAUUSD" and tf_match and source_ok and dates_ok and pit_ok and phase6_ok:
                    promotion_status = PromotionStatus.NOT_EVALUATED
                else:
                    promotion_status = PromotionStatus.BLOCKED_BY_PHASE6
        else:
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
            profile_name=eff_profile.name,
            calibration_status=eff_profile.status.value,
            instrument=eff_profile.target_instrument,
        )
