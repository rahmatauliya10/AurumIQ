"""Acceptance Test A16: Statistical Effective Sample Guard."""
import pytest
from engine.guards.sample_guard import EffectiveSampleEstimator
from engine.core.types import SampleQuality


@pytest.mark.acceptance
def test_a16_sample_guard_evaluation():
    """
    A16: Verify that sample count or effective sample size below 30
    strictly assigns INSUFFICIENT_DATA and zero positive weight (weight_multiplier = 0.0).
    """
    estimator = EffectiveSampleEstimator()

    # Case 1: Raw count n < 30
    eval_raw_small = estimator.evaluate_sample(n_raw=20)
    assert eval_raw_small.quality == SampleQuality.INSUFFICIENT
    assert eval_raw_small.weight_multiplier == 0.0
    assert eval_raw_small.is_blocked is True
    assert "A16 INSUFFICIENT DATA" in eval_raw_small.message

    # Case 2: Raw count n = 50, but severe regime concentration (HHI) discounts n_eff to < 30
    # 98% of samples in single regime -> HHI discount ~ 0.50 -> n_eff = 50 * 0.50 = 25 < 30
    single_regime_dist = {"BULL_TREND": 49, "BEAR_TREND": 1}
    eval_hhi_discounted = estimator.evaluate_sample(
        n_raw=50,
        regime_distribution=single_regime_dist,
        autocorrelation_factor=0.0,
    )
    assert eval_hhi_discounted.quality == SampleQuality.INSUFFICIENT
    assert eval_hhi_discounted.effective_n < 30.0
    assert eval_hhi_discounted.weight_multiplier == 0.0
    assert eval_hhi_discounted.is_blocked is True

    # Case 3: Balanced distribution with 30 <= n_eff < 60 -> LOW quality (weight = 0.5)
    balanced_dist = {"BULL_TREND": 25, "BEAR_TREND": 25}
    eval_low = estimator.evaluate_sample(
        n_raw=50,
        regime_distribution=balanced_dist,
        autocorrelation_factor=0.1,
    )
    assert eval_low.quality == SampleQuality.LOW
    assert eval_low.weight_multiplier == 0.5
    assert eval_low.is_blocked is False

    # Case 4: Balanced distribution with 60 <= n_eff < 100 -> MEDIUM quality (weight = 0.8)
    balanced_dist_80 = {"BULL_TREND": 40, "BEAR_TREND": 40}
    eval_med = estimator.evaluate_sample(
        n_raw=80,
        regime_distribution=balanced_dist_80,
        autocorrelation_factor=0.0,
    )
    assert eval_med.quality == SampleQuality.MEDIUM
    assert eval_med.weight_multiplier == 0.8
    assert eval_med.is_blocked is False

    # Case 5: n_eff >= 100 -> HIGH quality (weight = 1.0)
    balanced_dist_120 = {"BULL_TREND": 60, "BEAR_TREND": 60}
    eval_high = estimator.evaluate_sample(
        n_raw=120,
        regime_distribution=balanced_dist_120,
        autocorrelation_factor=0.0,
    )
    assert eval_high.quality == SampleQuality.HIGH
    assert eval_high.weight_multiplier == 1.0
    assert eval_high.is_blocked is False
