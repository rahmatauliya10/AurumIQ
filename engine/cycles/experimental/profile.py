"""
Phase 3B Experimental Spectral & Cycle Research Profile Architecture.

Provides explicit, immutable profile configurations and research governance
for spectral cycle analysis (ACF, FFT, Wavelet, Hilbert). Ensures strict
isolation between historical frozen XAUT reference numbers and target XAUUSD
research configuration, preventing uncalibrated research from silently inheriting
legacy empirical thresholds.

Production Weight Guarantee:
  All Phase 3B profiles operate with production_weight = 0.0 permanently.
"""
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Optional


class ResearchCalibrationStatus(str, Enum):
    """Lifecycle status for Phase 3B experimental spectral research governance."""
    LEGACY_REFERENCE = "LEGACY_REFERENCE"          # Historical frozen XAUT research reference
    PENDING_DATA = "PENDING_DATA"                  # Target instrument uncalibrated research
    CANDIDATE_NOT_FROZEN = "CANDIDATE_NOT_FROZEN"  # Empirical candidate generated (research only)
    REVALIDATED_RESEARCH = "REVALIDATED_RESEARCH"  # Research policy configured (research only)
    # Note: PRODUCTION_ACTIVE is strictly prohibited in Phase 3B.


def _deep_freeze(val: Any) -> Any:
    """Recursively convert nested dicts to MappingProxyType and collections to tuples."""
    if isinstance(val, (dict, MappingProxyType, Mapping)):
        return MappingProxyType({k: _deep_freeze(v) for k, v in val.items()})
    elif isinstance(val, (list, tuple, set)):
        return tuple(_deep_freeze(x) for x in val)
    return val


@dataclass(frozen=True)
class Cycle3BResearchProfile:
    """
    Immutable specification of mathematical parameters, empirical detection thresholds,
    and research reliability / promotion policies for Phase 3B Spectral Research.
    
    Zero Default Legacy Empirical Numbers:
      All empirical detection, reliability, and promotion policies strictly default to None.
      Historical reference numbers are defined exclusively in legacy_xaut_research_profile().
    """
    name: str = "LEGACY_XAUT_RESEARCH"
    status: ResearchCalibrationStatus = ResearchCalibrationStatus.LEGACY_REFERENCE
    target_instrument: str = "XAUT"
    timeframe: Optional[str] = None

    # --- 1. Algorithm Configuration (RESEARCH_CONFIG_NOT_XAUUSD_OPTIMIZED) ---
    max_lag: int = 64
    min_lookback: int = 32
    min_period: float = 4.0
    max_period: float = 64.0
    window_type: str = "hann"
    wavelet_name: str = "morl"
    num_scales: int = 32

    # --- 2. Empirical Detection Policies (Strictly None by default) ---
    acf_bartlett_z_multiplier: Optional[float] = None
    acf_min_effective_n: Optional[float] = None

    fft_min_power_ratio: Optional[float] = None
    fft_power_score_multiplier: Optional[float] = None

    wavelet_max_coi_contamination: Optional[float] = None
    wavelet_min_interior_support_ratio: Optional[float] = None

    hilbert_min_stability: Optional[float] = None
    hilbert_min_lookback: Optional[int] = None
    hilbert_min_velocity: Optional[float] = None
    hilbert_min_amplitude: Optional[float] = None

    # --- 3. Reliability & Consensus Policy (Strictly None by default) ---
    dispersion_high_threshold: Optional[float] = None
    dispersion_moderate_threshold: Optional[float] = None
    single_method_agreement_pct: Optional[float] = None
    moderate_method_agreement_pct: Optional[float] = None
    cross_window_dispersion_threshold: Optional[float] = None

    reliability_band_high: Optional[float] = None
    reliability_band_moderate: Optional[float] = None
    reliability_band_low: Optional[float] = None

    reliability_weight_acf: Optional[float] = None
    reliability_weight_fft: Optional[float] = None
    reliability_weight_wavelet: Optional[float] = None
    reliability_weight_hilbert: Optional[float] = None

    quality_multiplier_low: Optional[float] = None
    quality_multiplier_medium: Optional[float] = None
    quality_multiplier_high: Optional[float] = None
    min_effective_n: Optional[float] = None

    reliability_high_min_agreement_pct: Optional[float] = None
    reliability_moderate_min_agreement_pct: Optional[float] = None

    # --- 4. Promotion Gate Policy (Strictly None by default) ---
    promotion_min_trades: Optional[int] = None
    promotion_min_pf_improvement_pct: Optional[float] = None
    promotion_max_dd_deterioration_pct: Optional[float] = None
    promotion_min_folds_passed: Optional[int] = None
    promotion_min_folds_total: Optional[int] = None
    promotion_max_fold_concentration_pct: Optional[float] = None
    promotion_min_effective_n: Optional[float] = None

    # --- 5. Metadata / Provenance (Strictly immutable mapping) ---
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Enforce strict recursive deep immutability across all attributes and target invariants."""
        norm_target = self.target_instrument.upper().replace("/", "").replace("_", "").strip()

        if self.status == ResearchCalibrationStatus.LEGACY_REFERENCE:
            if norm_target not in ("XAUT", "XAUTUSD", "XAUTF"):
                raise ValueError(
                    f"LEGACY_REFERENCE status requires target instrument 'XAUT', got '{self.target_instrument}'."
                )
            if not (
                self.is_detection_policy_configured
                and self.is_reliability_policy_configured
                and self.is_promotion_policy_configured
            ):
                raise ValueError(
                    "LEGACY_REFERENCE status requires complete frozen detection, reliability, and promotion policies."
                )

        if norm_target in ("XAUUSD", "GOLD"):
            # If research candidate / revalidated policy or fully configured policy, timeframe is required
            if self.status == ResearchCalibrationStatus.REVALIDATED_RESEARCH or (
                self.is_detection_policy_configured
                and self.is_reliability_policy_configured
                and self.is_promotion_policy_configured
            ):
                if not self.timeframe or not self.timeframe.strip():
                    raise ValueError("Configured XAUUSD research policy requires an explicit non-empty timeframe.")

        object.__setattr__(self, "details", _deep_freeze(dict(self.details)) if self.details else MappingProxyType({}))

    # --- Policy Completeness Properties ---

    @property
    def is_acf_policy_configured(self) -> bool:
        """True when all ACF empirical detection policy thresholds are configured."""
        return (
            self.acf_bartlett_z_multiplier is not None
            and self.acf_min_effective_n is not None
        )

    @property
    def is_fft_policy_configured(self) -> bool:
        """True when all FFT empirical detection policy thresholds are configured."""
        return self.fft_min_power_ratio is not None

    @property
    def is_wavelet_policy_configured(self) -> bool:
        """True when all Wavelet empirical detection policy thresholds are configured."""
        return (
            self.wavelet_max_coi_contamination is not None
            and self.wavelet_min_interior_support_ratio is not None
        )

    @property
    def is_hilbert_policy_configured(self) -> bool:
        """True when all Hilbert empirical detection policy thresholds are configured."""
        return (
            self.hilbert_min_stability is not None
            and self.hilbert_min_lookback is not None
            and self.hilbert_min_velocity is not None
            and self.hilbert_min_amplitude is not None
        )

    @property
    def is_detection_policy_configured(self) -> bool:
        """True when all four spectral detection policies (ACF, FFT, Wavelet, Hilbert) are complete."""
        return (
            self.is_acf_policy_configured
            and self.is_fft_policy_configured
            and self.is_wavelet_policy_configured
            and self.is_hilbert_policy_configured
        )

    @property
    def is_reliability_policy_configured(self) -> bool:
        """True when ALL required empirical consensus & reliability policy thresholds are configured."""
        return (
            self.dispersion_high_threshold is not None
            and self.dispersion_moderate_threshold is not None
            and self.single_method_agreement_pct is not None
            and self.moderate_method_agreement_pct is not None
            and self.cross_window_dispersion_threshold is not None
            and self.reliability_band_high is not None
            and self.reliability_band_moderate is not None
            and self.reliability_band_low is not None
            and self.reliability_weight_acf is not None
            and self.reliability_weight_fft is not None
            and self.reliability_weight_wavelet is not None
            and self.reliability_weight_hilbert is not None
            and self.fft_power_score_multiplier is not None
            and self.quality_multiplier_low is not None
            and self.quality_multiplier_medium is not None
            and self.quality_multiplier_high is not None
            and self.min_effective_n is not None
            and self.reliability_high_min_agreement_pct is not None
            and self.reliability_moderate_min_agreement_pct is not None
        )

    @property
    def is_promotion_policy_configured(self) -> bool:
        """True when ALL empirical promotion gate hurdle thresholds are configured."""
        return (
            self.promotion_min_trades is not None
            and self.promotion_min_pf_improvement_pct is not None
            and self.promotion_max_dd_deterioration_pct is not None
            and self.promotion_min_folds_passed is not None
            and self.promotion_min_folds_total is not None
            and self.promotion_max_fold_concentration_pct is not None
            and self.promotion_min_effective_n is not None
        )

    @property
    def is_research_policy_configured(self) -> bool:
        """True when full detection, reliability, and promotion policies are complete."""
        return (
            self.status in (ResearchCalibrationStatus.LEGACY_REFERENCE, ResearchCalibrationStatus.REVALIDATED_RESEARCH)
            and self.is_detection_policy_configured
            and self.is_reliability_policy_configured
            and self.is_promotion_policy_configured
        )

    @classmethod
    def legacy_xaut_research_profile(cls) -> "Cycle3BResearchProfile":
        """
        Historical XAUT frozen research reference profile.
        Preserves historical Phase 3B behavior and tests byte-for-byte with explicit constants.
        """
        return cls(
            name="LEGACY_XAUT_RESEARCH",
            status=ResearchCalibrationStatus.LEGACY_REFERENCE,
            target_instrument="XAUT",
            timeframe=None,
            # Algorithm Config
            max_lag=64,
            min_lookback=32,
            min_period=4.0,
            max_period=64.0,
            window_type="hann",
            wavelet_name="morl",
            num_scales=32,
            # Detection Policies
            acf_bartlett_z_multiplier=1.96,
            acf_min_effective_n=30.0,
            fft_min_power_ratio=0.15,
            fft_power_score_multiplier=2.5,
            wavelet_max_coi_contamination=0.40,
            wavelet_min_interior_support_ratio=3.0,
            hilbert_min_stability=0.60,
            hilbert_min_lookback=48,
            hilbert_min_velocity=0.05,
            hilbert_min_amplitude=1e-6,
            # Reliability Policies
            dispersion_high_threshold=0.15,
            dispersion_moderate_threshold=0.30,
            single_method_agreement_pct=35.0,
            moderate_method_agreement_pct=65.0,
            cross_window_dispersion_threshold=0.35,
            reliability_band_high=60.0,
            reliability_band_moderate=35.0,
            reliability_band_low=15.0,
            reliability_weight_acf=30.0,
            reliability_weight_fft=30.0,
            reliability_weight_wavelet=20.0,
            reliability_weight_hilbert=20.0,
            quality_multiplier_low=0.5,
            quality_multiplier_medium=0.8,
            quality_multiplier_high=1.0,
            min_effective_n=30.0,
            reliability_high_min_agreement_pct=80.0,
            reliability_moderate_min_agreement_pct=50.0,
            # Promotion Policies
            promotion_min_trades=100,
            promotion_min_pf_improvement_pct=5.0,
            promotion_max_dd_deterioration_pct=10.0,
            promotion_min_folds_passed=4,
            promotion_min_folds_total=6,
            promotion_max_fold_concentration_pct=60.0,
            promotion_min_effective_n=30.0,
            details={
                "instrument": "XAUT",
                "calibration_status": ResearchCalibrationStatus.LEGACY_REFERENCE.value,
                "description": "Historical frozen XAUT research reference profile.",
            },
        )

    @classmethod
    def uncalibrated_xauusd_research_profile(cls, timeframe: Optional[str] = None) -> "Cycle3BResearchProfile":
        """
        Explicitly uncalibrated research profile for XAUUSD with NO empirical detection/reliability thresholds.
        Guarantees zero hidden fallback to historical XAUT reference values.
        """
        return cls(
            name="XAUUSD_UNCALIBRATED_RESEARCH",
            status=ResearchCalibrationStatus.PENDING_DATA,
            target_instrument="XAUUSD",
            timeframe=timeframe,
            # Algorithm Config (Labeled RESEARCH_CONFIG_NOT_XAUUSD_OPTIMIZED)
            max_lag=64,
            min_lookback=32,
            min_period=4.0,
            max_period=64.0,
            window_type="hann",
            wavelet_name="morl",
            num_scales=32,
            # Empirical Detection Policies (Strictly None)
            acf_bartlett_z_multiplier=None,
            acf_min_effective_n=None,
            fft_min_power_ratio=None,
            fft_power_score_multiplier=None,
            wavelet_max_coi_contamination=None,
            wavelet_min_interior_support_ratio=None,
            hilbert_min_stability=None,
            hilbert_min_lookback=None,
            hilbert_min_velocity=None,
            hilbert_min_amplitude=None,
            # Reliability Policies (Strictly None)
            dispersion_high_threshold=None,
            dispersion_moderate_threshold=None,
            single_method_agreement_pct=None,
            moderate_method_agreement_pct=None,
            cross_window_dispersion_threshold=None,
            reliability_band_high=None,
            reliability_band_moderate=None,
            reliability_band_low=None,
            reliability_weight_acf=None,
            reliability_weight_fft=None,
            reliability_weight_wavelet=None,
            reliability_weight_hilbert=None,
            quality_multiplier_low=None,
            quality_multiplier_medium=None,
            quality_multiplier_high=None,
            min_effective_n=None,
            reliability_high_min_agreement_pct=None,
            reliability_moderate_min_agreement_pct=None,
            # Promotion Policies (Strictly None)
            promotion_min_trades=None,
            promotion_min_pf_improvement_pct=None,
            promotion_max_dd_deterioration_pct=None,
            promotion_min_folds_passed=None,
            promotion_min_folds_total=None,
            promotion_max_fold_concentration_pct=None,
            promotion_min_effective_n=None,
            details={
                "instrument": "XAUUSD",
                "calibration_status": ResearchCalibrationStatus.PENDING_DATA.value,
                "reason": "XAUUSD empirical spectral parameters not configured.",
            },
        )
