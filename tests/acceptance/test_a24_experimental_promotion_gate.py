"""Acceptance Test A24: Experimental Spectral Promotion Gate Safety (including P3B-23)."""
from datetime import datetime, timezone
import pytest

from engine.core.types import (
    BaselineBenchmark,
    PromotionStatus,
    WalkForwardFoldResult,
)
from engine.cycles.experimental.promotion import evaluate_promotion_eligibility


@pytest.mark.acceptance
def test_a24_promotion_blocked_on_non_empirical_baseline():
    """A24: If baseline benchmark is non-empirical, promotion is strictly BASELINE_NOT_EMPIRICAL."""
    fake_baseline = BaselineBenchmark(
        base_profit_factor=1.85,
        base_expectancy_r=0.42,
        base_max_drawdown=8.5,
        base_trade_count=120,
        recorded_at=datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc),
        is_empirical=False,
    )

    eval_res = evaluate_promotion_eligibility(
        baseline=fake_baseline,
        exp_profit_factor=2.10,
        exp_expectancy_r=0.55,
        exp_max_drawdown=7.5,
        exp_trade_count=200,
        walk_forward_folds_passed=6,
        walk_forward_folds_total=6,
        effective_n=150.0,
    )

    assert eval_res.status == PromotionStatus.BASELINE_NOT_EMPIRICAL
    assert eval_res.is_promotable is False
    assert any("non-empirical" in r for r in eval_res.reasons)


@pytest.mark.acceptance
def test_a24_promotion_promotable_on_verified_empirical_hurdles():
    """A24: Passing all frozen empirical hurdles with balanced fold distribution returns PROMOTABLE."""
    empirical_baseline = BaselineBenchmark(
        base_profit_factor=1.80,
        base_expectancy_r=0.40,
        base_max_drawdown=10.0,
        base_trade_count=150,
        recorded_at=datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc),
        is_empirical=True,
    )

    # 6 folds with balanced profit distribution (max fold share = 25%)
    folds = [
        WalkForwardFoldResult(fold_id=1, profit_factor=1.90, expectancy_r=0.42, max_drawdown=9.0, trade_count=30, net_profit=2500.0),
        WalkForwardFoldResult(fold_id=2, profit_factor=1.85, expectancy_r=0.41, max_drawdown=9.5, trade_count=30, net_profit=2000.0),
        WalkForwardFoldResult(fold_id=3, profit_factor=2.00, expectancy_r=0.48, max_drawdown=8.5, trade_count=30, net_profit=2500.0),
        WalkForwardFoldResult(fold_id=4, profit_factor=1.95, expectancy_r=0.45, max_drawdown=10.0, trade_count=30, net_profit=2000.0),
        WalkForwardFoldResult(fold_id=5, profit_factor=1.82, expectancy_r=0.40, max_drawdown=10.5, trade_count=30, net_profit=1000.0),
        WalkForwardFoldResult(fold_id=6, profit_factor=1.75, expectancy_r=0.35, max_drawdown=11.0, trade_count=30, net_profit=0.0),
    ]

    eval_res = evaluate_promotion_eligibility(
        baseline=empirical_baseline,
        exp_profit_factor=1.95,
        exp_expectancy_r=0.45,
        exp_max_drawdown=10.5,
        exp_trade_count=180,
        fold_results=folds,
        effective_n=120.0,
    )

    assert eval_res.status == PromotionStatus.PROMOTABLE
    assert eval_res.is_promotable is True
    assert eval_res.pf_improvement_pct == 8.33
    assert eval_res.is_single_period_dependent is False
    assert eval_res.walk_forward_folds_passed == 5


@pytest.mark.acceptance
def test_a24_promotion_fails_on_single_period_dependence():
    """A24 & P3B-23: If >60% of total profit comes from a single fold, promotion is rejected."""
    empirical_baseline = BaselineBenchmark(
        base_profit_factor=1.80,
        base_expectancy_r=0.40,
        base_max_drawdown=10.0,
        base_trade_count=150,
        recorded_at=datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc),
        is_empirical=True,
    )

    # Fold 1 accounts for $8,000 out of $10,000 (80% concentration!)
    folds_concentrated = [
        WalkForwardFoldResult(fold_id=1, profit_factor=2.80, expectancy_r=0.90, max_drawdown=5.0, trade_count=30, net_profit=8000.0),
        WalkForwardFoldResult(fold_id=2, profit_factor=1.81, expectancy_r=0.41, max_drawdown=9.5, trade_count=30, net_profit=500.0),
        WalkForwardFoldResult(fold_id=3, profit_factor=1.82, expectancy_r=0.42, max_drawdown=8.5, trade_count=30, net_profit=500.0),
        WalkForwardFoldResult(fold_id=4, profit_factor=1.81, expectancy_r=0.41, max_drawdown=10.0, trade_count=30, net_profit=500.0),
        WalkForwardFoldResult(fold_id=5, profit_factor=1.81, expectancy_r=0.41, max_drawdown=10.0, trade_count=30, net_profit=500.0),
        WalkForwardFoldResult(fold_id=6, profit_factor=1.70, expectancy_r=0.30, max_drawdown=11.0, trade_count=30, net_profit=0.0),
    ]

    eval_res = evaluate_promotion_eligibility(
        baseline=empirical_baseline,
        exp_profit_factor=2.05,
        exp_expectancy_r=0.50,
        exp_max_drawdown=10.0,
        exp_trade_count=180,
        fold_results=folds_concentrated,
        effective_n=120.0,
    )

    assert eval_res.status == PromotionStatus.FAILED
    assert eval_res.is_promotable is False
    assert eval_res.is_single_period_dependent is True
    assert any("Single-period profit concentration" in r for r in eval_res.reasons)
