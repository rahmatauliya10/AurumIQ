"""Statistical Effective Sample Guard (A16)."""
from typing import Dict, Optional, Tuple
from engine.core.types import SampleEvaluation, SampleQuality
from engine.core.config import EngineConfigData


class EffectiveSampleEstimator:
    """
    Evaluates sample size reliability and effective sample size ($n_{eff}$).
    
    Pipeline:
      raw setups
      -> discount/remove overlapping setups (overlap_ratio)
      -> temporal clustering discount (autocorrelation_factor)
      -> regime concentration discount (normalized HHI)
      -> effective_n
    
    Acceptance Criteria A16:
      - If n < 30 or n_eff < 30: INSUFFICIENT -> weight_multiplier = 0.0, is_blocked = True
      - If 30 <= n_eff < 60: LOW -> weight_multiplier = 0.5, is_blocked = False
      - If 60 <= n_eff < 100: MEDIUM -> weight_multiplier = 0.8, is_blocked = False
      - If n_eff >= 100: HIGH -> weight_multiplier = 1.0, is_blocked = False
    """

    def __init__(self, config: Optional[EngineConfigData] = None):
        self.config = config or EngineConfigData()

    def calculate_hhi(self, regime_distribution: Optional[Dict[str, int]]) -> Tuple[float, float, float]:
        """
        Calculate Herfindahl-Hirschman Index (HHI) concentration discount.
        Returns: (raw_hhi, normalized_hhi, regime_discount)
        """
        if not regime_distribution:
            return 0.0, 0.0, 0.0

        total_obs = sum(regime_distribution.values())
        if total_obs == 0:
            return 0.0, 0.0, 0.0

        num_categories = len(regime_distribution)
        if num_categories <= 1:
            raw_hhi = 1.0
            norm_hhi = 1.0
            discount = float(self.config.max_hhi_discount)
            return raw_hhi, norm_hhi, discount

        shares = [count / total_obs for count in regime_distribution.values()]
        raw_hhi = sum(s ** 2 for s in shares)  # Range: [1/K, 1.0]

        min_hhi = 1.0 / float(num_categories)
        normalized_hhi = (raw_hhi - min_hhi) / (1.0 - min_hhi) if (1.0 - min_hhi) > 0 else 0.0
        normalized_hhi = max(0.0, min(1.0, normalized_hhi))

        discount = normalized_hhi * float(self.config.max_hhi_discount)
        discount = float(round(min(discount, float(self.config.max_hhi_discount)), 4))
        return float(round(raw_hhi, 4)), float(round(normalized_hhi, 4)), discount

    def calculate_clustering_discount(self, autocorrelation_factor: float = 0.0) -> float:
        """
        Calculate temporal clustering / autocorrelation discount.
        High autocorrelation (e.g. 0.8) indicates non-independent samples.
        """
        clamped_factor = max(0.0, min(1.0, autocorrelation_factor))
        discount = clamped_factor * float(self.config.max_clustering_discount)
        return float(round(discount, 4))

    def evaluate_sample(
        self,
        n_raw: int,
        regime_distribution: Optional[Dict[str, int]] = None,
        autocorrelation_factor: float = 0.0,
        overlap_ratio: float = 0.0,
    ) -> SampleEvaluation:
        """
        Evaluate statistical effective sample size and assign reliability tier.
        """
        # 1. Independent after overlap
        clamped_overlap = max(0.0, min(1.0, overlap_ratio))
        independent_after_overlap = max(0, round(float(n_raw) * (1.0 - clamped_overlap)))

        # 2. Temporal clustering
        clustering_discount = self.calculate_clustering_discount(autocorrelation_factor)
        temporal_clusters = max(0, round(float(independent_after_overlap) * (1.0 - clustering_discount)))

        # 3. Regime concentration
        raw_hhi, hhi_norm, regime_discount = self.calculate_hhi(regime_distribution)

        # 4. Effective N
        effective_n = float(round(float(temporal_clusters) * (1.0 - regime_discount), 2))

        # Check raw sample minimum
        if n_raw < self.config.min_sample_threshold:
            return SampleEvaluation(
                n_raw=n_raw,
                independent_after_overlap=independent_after_overlap,
                temporal_clusters=temporal_clusters,
                hhi_norm=hhi_norm,
                regime_discount=regime_discount,
                clustering_discount=clustering_discount,
                effective_n=float(n_raw),
                quality=SampleQuality.INSUFFICIENT,
                weight_multiplier=0.0,
                is_blocked=True,
                message=f"A16 INSUFFICIENT DATA: Raw count {n_raw} < threshold {self.config.min_sample_threshold}. Zero positive weight assigned.",
            )

        # Check effective sample minimum
        if effective_n < float(self.config.min_sample_threshold):
            return SampleEvaluation(
                n_raw=n_raw,
                independent_after_overlap=independent_after_overlap,
                temporal_clusters=temporal_clusters,
                hhi_norm=hhi_norm,
                regime_discount=regime_discount,
                clustering_discount=clustering_discount,
                effective_n=effective_n,
                quality=SampleQuality.INSUFFICIENT,
                weight_multiplier=0.0,
                is_blocked=True,
                message=f"A16 INSUFFICIENT DATA: Effective sample count {effective_n:.1f} < threshold {self.config.min_sample_threshold} after discounts. Zero weight assigned.",
            )
        elif effective_n < float(self.config.low_quality_threshold):
            return SampleEvaluation(
                n_raw=n_raw,
                independent_after_overlap=independent_after_overlap,
                temporal_clusters=temporal_clusters,
                hhi_norm=hhi_norm,
                regime_discount=regime_discount,
                clustering_discount=clustering_discount,
                effective_n=effective_n,
                quality=SampleQuality.LOW,
                weight_multiplier=0.5,
                is_blocked=False,
                message=f"LOW SAMPLE QUALITY: Effective sample {effective_n:.1f} in [30, 60). Weight multiplier = 0.5.",
            )
        elif effective_n < float(self.config.medium_quality_threshold):
            return SampleEvaluation(
                n_raw=n_raw,
                independent_after_overlap=independent_after_overlap,
                temporal_clusters=temporal_clusters,
                hhi_norm=hhi_norm,
                regime_discount=regime_discount,
                clustering_discount=clustering_discount,
                effective_n=effective_n,
                quality=SampleQuality.MEDIUM,
                weight_multiplier=0.8,
                is_blocked=False,
                message=f"MEDIUM SAMPLE QUALITY: Effective sample {effective_n:.1f} in [60, 100). Weight multiplier = 0.8.",
            )
        else:
            return SampleEvaluation(
                n_raw=n_raw,
                independent_after_overlap=independent_after_overlap,
                temporal_clusters=temporal_clusters,
                hhi_norm=hhi_norm,
                regime_discount=regime_discount,
                clustering_discount=clustering_discount,
                effective_n=effective_n,
                quality=SampleQuality.HIGH,
                weight_multiplier=1.0,
                is_blocked=False,
                message=f"HIGH SAMPLE QUALITY: Effective sample {effective_n:.1f} >= 100. Full weight multiplier = 1.0.",
            )
