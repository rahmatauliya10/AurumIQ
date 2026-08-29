"""Empirical Promotion Gate for Phase 3B Experimental Spectral Features (A24, P3B-23, P3B-26)."""
from typing import List, Optional, Sequence

from engine.core.types import (
    BaselineBenchmark,
    PromotionEvaluation,
    PromotionStatus,
    WalkForwardFoldResult,
)


def evaluate_promotion_eligibility(
    baseline: BaselineBenchmark,
    exp_profit_factor: float,
    exp_expectancy_r: float,
    exp_max_drawdown: float,
    exp_trade_count: int,
    walk_forward_folds_passed: int = 0,
    walk_forward_folds_total: int = 6,
    fold_results: Optional[Sequence[WalkForwardFoldResult]] = None,
    effective_n: float = 0.0,
) -> PromotionEvaluation:
    """
    Evaluate experimental cycle features against frozen promotion hurdles (A24).

    Strict Promotion Invariants:
      1. BaselineBenchmark.is_empirical MUST be True (P3B-11).
      2. Experimental backtest must have >= 100 trades (P3B-12).
      3. Profit Factor improvement must be >= +5.0% vs baseline (P3B-13).
      4. Expectancy must improve (exp_expectancy_r > base_expectancy_r).
      5. Max Drawdown deterioration must be <= 10.0% worse than baseline (P3B-14).
      6. At least 4 of 6 walk-forward folds must pass/improve (P3B-15).
      7. No single-period profit concentration > 60% of total gains (P3B-23).
      8. Statistical effective sample size MUST be >= 30.0 (P3B-26).
    """
    reasons: List[str] = []

    base_pf = baseline.base_profit_factor
    base_exp = baseline.base_expectancy_r
    base_dd = baseline.base_max_drawdown

    pf_improvement = ((exp_profit_factor - base_pf) / base_pf) * 100.0 if base_pf > 0 else 0.0
    pf_improvement = float(round(pf_improvement, 2))

    dd_deterioration = ((exp_max_drawdown - base_dd) / base_dd) * 100.0 if base_dd > 0 else 0.0
    dd_deterioration = float(round(dd_deterioration, 2))

    # Evaluate fold metrics if provided (P3B-23)
    is_single_period_dependent = False
    max_fold_share_pct = 0.0

    if fold_results is not None and len(fold_results) > 0:
        walk_forward_folds_total = len(fold_results)
        walk_forward_folds_passed = sum(1 for f in fold_results if f.profit_factor >= base_pf)
        
        pos_profits = [max(0.0, f.net_profit) for f in fold_results]
        total_pos_profit = sum(pos_profits)
        if total_pos_profit > 0:
            max_fold_share_pct = float(round((max(pos_profits) / total_pos_profit) * 100.0, 2))
            if max_fold_share_pct > 60.0:
                is_single_period_dependent = True

    # 1. Check Empirical Baseline Requirement (P3B-11)
    if not baseline.is_empirical:
        reasons.append("Baseline benchmark is non-empirical (unverified recorder only). Cannot evaluate promotion.")
        return PromotionEvaluation(
            status=PromotionStatus.BASELINE_NOT_EMPIRICAL,
            is_promotable=False,
            baseline_pf=base_pf,
            experimental_pf=exp_profit_factor,
            pf_improvement_pct=pf_improvement,
            trade_count=exp_trade_count,
            max_drawdown_pct=exp_max_drawdown,
            dd_deterioration_pct=dd_deterioration,
            walk_forward_folds_passed=walk_forward_folds_passed,
            walk_forward_folds_total=walk_forward_folds_total,
            is_single_period_dependent=is_single_period_dependent,
            max_fold_profit_share_pct=max_fold_share_pct,
            effective_n=effective_n,
            reasons=tuple(reasons),
        )

    # 2. Minimum Trades Guard (P3B-12)
    if exp_trade_count < 100:
        reasons.append(f"Insufficient trade count ({exp_trade_count} < 100 required).")
        return PromotionEvaluation(
            status=PromotionStatus.INSUFFICIENT_TRADES,
            is_promotable=False,
            baseline_pf=base_pf,
            experimental_pf=exp_profit_factor,
            pf_improvement_pct=pf_improvement,
            trade_count=exp_trade_count,
            max_drawdown_pct=exp_max_drawdown,
            dd_deterioration_pct=dd_deterioration,
            walk_forward_folds_passed=walk_forward_folds_passed,
            walk_forward_folds_total=walk_forward_folds_total,
            is_single_period_dependent=is_single_period_dependent,
            max_fold_profit_share_pct=max_fold_share_pct,
            effective_n=effective_n,
            reasons=tuple(reasons),
        )

    # 3. Check Performance Hurdles
    passed_all_hurdles = True

    # Hurdle A: PF improvement >= +5.0% (P3B-13)
    if pf_improvement < 5.0:
        passed_all_hurdles = False
        reasons.append(f"Profit Factor improvement (+{pf_improvement}%) < 5.0% minimum threshold.")

    # Hurdle B: Expectancy improvement
    if exp_expectancy_r <= base_exp:
        passed_all_hurdles = False
        reasons.append(f"Expectancy ({exp_expectancy_r}R) did not exceed baseline ({base_exp}R).")

    # Hurdle C: Drawdown deterioration <= 10.0% (P3B-14)
    if dd_deterioration > 10.0:
        passed_all_hurdles = False
        reasons.append(f"Max Drawdown deterioration (+{dd_deterioration}%) > 10.0% allowable limit.")

    # Hurdle D: Walk-Forward folds >= 4 of 6 (P3B-15)
    if walk_forward_folds_passed < 4 or walk_forward_folds_total < 6:
        passed_all_hurdles = False
        reasons.append(f"Walk-forward folds passed ({walk_forward_folds_passed}/{walk_forward_folds_total}) < 4/6 required.")

    # Hurdle E: Single-period concentration guard (P3B-23)
    if is_single_period_dependent:
        passed_all_hurdles = False
        reasons.append(f"Single-period profit concentration ({max_fold_share_pct}%) exceeds 60.0% allowable limit.")

    # Hurdle F: Promotion Effective Sample Guard (P3B-26)
    if effective_n < 30.0:
        passed_all_hurdles = False
        reasons.append(f"Promotion effective sample size ({round(effective_n, 1)}) < 30.0 minimum threshold.")

    if passed_all_hurdles:
        reasons.append("All promotion hurdles satisfied with statistical significance.")
        status = PromotionStatus.PROMOTABLE
        is_promotable = True
    else:
        status = PromotionStatus.FAILED
        is_promotable = False

    return PromotionEvaluation(
        status=status,
        is_promotable=is_promotable,
        baseline_pf=base_pf,
        experimental_pf=exp_profit_factor,
        pf_improvement_pct=pf_improvement,
        trade_count=exp_trade_count,
        max_drawdown_pct=exp_max_drawdown,
        dd_deterioration_pct=dd_deterioration,
        walk_forward_folds_passed=walk_forward_folds_passed,
        walk_forward_folds_total=walk_forward_folds_total,
        is_single_period_dependent=is_single_period_dependent,
        max_fold_profit_share_pct=max_fold_share_pct,
        effective_n=effective_n,
        reasons=tuple(reasons),
    )
