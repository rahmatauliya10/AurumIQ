"""Empirical Promotion Gate for Phase 3B Experimental Spectral Features (A24, P3B-23, P3B-26)."""
from typing import List, Optional, Sequence

from engine.core.types import (
    BaselineBenchmark,
    PromotionEvaluation,
    PromotionStatus,
    WalkForwardFoldResult,
)
from engine.cycles.experimental.profile import (
    Cycle3BResearchProfile,
    ResearchCalibrationStatus,
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
    profile: Optional[Cycle3BResearchProfile] = None,
    target_instrument: Optional[str] = None,
) -> PromotionEvaluation:
    """
    Evaluate experimental cycle features against frozen promotion hurdles (A24).

    Strict Promotion Invariants:
      1. BaselineBenchmark.is_empirical MUST be True (P3B-11).
      2. Experimental backtest must have >= min_trades (P3B-12).
      3. Profit Factor improvement must be >= min_pf_imp vs baseline (P3B-13).
      4. Expectancy must improve (exp_expectancy_r > base_expectancy_r).
      5. Max Drawdown deterioration must be <= max_dd_det worse than baseline (P3B-14).
      6. At least min_folds_passed of min_folds_total walk-forward folds must pass (P3B-15).
      7. No single-period profit concentration > max_fold_conc of total gains (P3B-23).
      8. Statistical effective sample size MUST be >= min_effective_n (P3B-26).

    Deterministic XAUUSD Precedence:
      A. Promotion policy not complete / unconfigured -> POLICY_NOT_CONFIGURED
      B. Policy complete but baseline invalid/missing/non-XAUUSD/non-PIT/not Phase 6 -> BLOCKED_BY_PHASE6
      C. Valid XAUUSD Phase 6 baseline exists but hurdles fail (including trade count) -> FAILED
      D. All research hurdles pass -> PROMOTABLE (production_weight remains 0.0)
    """
    reasons: List[str] = []

    # 1. Derive and validate target instrument
    if profile is not None:
        eff_target = profile.target_instrument.upper().replace("/", "")
        if target_instrument is not None:
            norm_target = target_instrument.upper().replace("/", "")
            if norm_target != eff_target:
                raise ValueError(
                    f"Target instrument mismatch: requested '{target_instrument}' but profile target is '{profile.target_instrument}'."
                )
    else:
        eff_target = target_instrument.upper().replace("/", "") if target_instrument is not None else "XAUT"

    base_pf = baseline.base_profit_factor if baseline else 0.0
    base_exp = baseline.base_expectancy_r if baseline else 0.0
    base_dd = baseline.base_max_drawdown if baseline else 0.0

    pf_improvement = ((exp_profit_factor - base_pf) / base_pf) * 100.0 if base_pf > 0 else 0.0
    pf_improvement = float(round(pf_improvement, 2))

    dd_deterioration = ((exp_max_drawdown - base_dd) / base_dd) * 100.0 if base_dd > 0 else 0.0
    dd_deterioration = float(round(dd_deterioration, 2))

    is_single_period_dependent = False
    max_fold_share_pct = 0.0

    # 2. XAUUSD Deterministic Precedence Branch
    if eff_target == "XAUUSD":
        # Precedence A: Full promotion policy completeness check
        if profile is None or not profile.is_promotion_policy_configured:
            reasons.append("XAUUSD promotion threshold policy is incomplete or not configured.")
            return PromotionEvaluation(
                status=PromotionStatus.POLICY_NOT_CONFIGURED,
                is_promotable=False,
                baseline_pf=base_pf,
                experimental_pf=exp_profit_factor,
                pf_improvement_pct=pf_improvement,
                trade_count=exp_trade_count,
                max_drawdown_pct=exp_max_drawdown,
                dd_deterioration_pct=dd_deterioration,
                walk_forward_folds_passed=walk_forward_folds_passed,
                walk_forward_folds_total=walk_forward_folds_total,
                is_single_period_dependent=False,
                max_fold_profit_share_pct=0.0,
                effective_n=effective_n,
                reasons=tuple(reasons),
            )

        # Precedence B: Strict Baseline Provenance Checks
        is_baseline_valid = True
        if baseline is None or not baseline.is_empirical:
            is_baseline_valid = False
            reasons.append("XAUUSD promotion evaluation blocked: baseline benchmark is non-empirical or missing. Blocked by Phase 6.")
        elif baseline.instrument is None or baseline.instrument.upper().replace("/", "") != "XAUUSD":
            is_baseline_valid = False
            reasons.append(f"XAUUSD promotion evaluation blocked: baseline instrument '{baseline.instrument}' != XAUUSD. Blocked by Phase 6.")
        elif baseline.timeframe is None or (profile.timeframe is not None and baseline.timeframe != profile.timeframe):
            is_baseline_valid = False
            reasons.append("XAUUSD promotion evaluation blocked: baseline timeframe missing or incompatible. Blocked by Phase 6.")
        elif not baseline.source or not baseline.source.strip():
            is_baseline_valid = False
            reasons.append("XAUUSD promotion evaluation blocked: baseline source/provider is missing. Blocked by Phase 6.")
        elif baseline.data_start is None or baseline.data_end is None or baseline.as_of is None:
            is_baseline_valid = False
            reasons.append("XAUUSD promotion evaluation blocked: baseline dates are incomplete. Blocked by Phase 6.")
        elif not (baseline.data_start <= baseline.data_end <= baseline.as_of):
            is_baseline_valid = False
            reasons.append("XAUUSD promotion evaluation blocked: baseline date causality violated. Blocked by Phase 6.")
        elif not baseline.pit_safe:
            is_baseline_valid = False
            reasons.append("XAUUSD promotion evaluation blocked: baseline is not pit_safe. Blocked by Phase 6.")
        elif not baseline.phase6_validated:
            is_baseline_valid = False
            reasons.append("XAUUSD promotion evaluation blocked: baseline is not phase6_validated. Blocked by Phase 6.")

        if not is_baseline_valid:
            return PromotionEvaluation(
                status=PromotionStatus.BLOCKED_BY_PHASE6,
                is_promotable=False,
                baseline_pf=base_pf,
                experimental_pf=exp_profit_factor,
                pf_improvement_pct=pf_improvement,
                trade_count=exp_trade_count,
                max_drawdown_pct=exp_max_drawdown,
                dd_deterioration_pct=dd_deterioration,
                walk_forward_folds_passed=walk_forward_folds_passed,
                walk_forward_folds_total=walk_forward_folds_total,
                is_single_period_dependent=False,
                max_fold_profit_share_pct=0.0,
                effective_n=effective_n,
                reasons=tuple(reasons),
            )

        # Precedence C & D: Evaluate policy hurdles
        min_trades = profile.promotion_min_trades
        min_pf_imp = profile.promotion_min_pf_improvement_pct
        max_dd_det = profile.promotion_max_dd_deterioration_pct
        min_folds = profile.promotion_min_folds_passed
        tot_folds = profile.promotion_min_folds_total
        max_conc = profile.promotion_max_fold_concentration_pct
        min_eff = profile.promotion_min_effective_n

        # Compute fold concentration descriptively without hardcoded 60%
        if fold_results is not None and len(fold_results) > 0:
            walk_forward_folds_total = len(fold_results)
            walk_forward_folds_passed = sum(1 for f in fold_results if f.profit_factor >= base_pf)

            pos_profits = [max(0.0, f.net_profit) for f in fold_results]
            total_pos_profit = sum(pos_profits)
            if total_pos_profit > 0:
                max_fold_share_pct = float(round((max(pos_profits) / total_pos_profit) * 100.0, 2))
                if max_fold_share_pct > max_conc:
                    is_single_period_dependent = True

        passed_all_hurdles = True

        if exp_trade_count < min_trades:
            passed_all_hurdles = False
            reasons.append(f"Insufficient trade count ({exp_trade_count} < {min_trades} required).")

        if pf_improvement < min_pf_imp:
            passed_all_hurdles = False
            reasons.append(f"Profit Factor improvement (+{pf_improvement}%) < {min_pf_imp}% minimum threshold.")

        if exp_expectancy_r <= base_exp:
            passed_all_hurdles = False
            reasons.append(f"Expectancy ({exp_expectancy_r}R) did not exceed baseline ({base_exp}R).")

        if dd_deterioration > max_dd_det:
            passed_all_hurdles = False
            reasons.append(f"Max Drawdown deterioration (+{dd_deterioration}%) > {max_dd_det}% allowable limit.")

        if walk_forward_folds_passed < min_folds or walk_forward_folds_total < tot_folds:
            passed_all_hurdles = False
            reasons.append(f"Walk-forward folds passed ({walk_forward_folds_passed}/{walk_forward_folds_total}) < {min_folds}/{tot_folds} required.")

        if is_single_period_dependent or max_fold_share_pct > max_conc:
            passed_all_hurdles = False
            reasons.append(f"Single-period profit concentration ({max_fold_share_pct}%) exceeds {max_conc}% allowable limit.")

        if effective_n < min_eff:
            passed_all_hurdles = False
            reasons.append(f"Promotion effective sample size ({round(effective_n, 1)}) < {min_eff} minimum threshold.")

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

    # 3. Legacy / Historical XAUT Evaluation Branch
    if fold_results is not None and len(fold_results) > 0:
        walk_forward_folds_total = len(fold_results)
        walk_forward_folds_passed = sum(1 for f in fold_results if f.profit_factor >= base_pf)
        pos_profits = [max(0.0, f.net_profit) for f in fold_results]
        total_pos_profit = sum(pos_profits)
        if total_pos_profit > 0:
            max_fold_share_pct = float(round((max(pos_profits) / total_pos_profit) * 100.0, 2))
            if max_fold_share_pct > 60.0:
                is_single_period_dependent = True

    if not baseline or not baseline.is_empirical:
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

    passed_all_hurdles = True
    if pf_improvement < 5.0:
        passed_all_hurdles = False
        reasons.append(f"Profit Factor improvement (+{pf_improvement}%) < 5.0% minimum threshold.")

    if exp_expectancy_r <= base_exp:
        passed_all_hurdles = False
        reasons.append(f"Expectancy ({exp_expectancy_r}R) did not exceed baseline ({base_exp}R).")

    if dd_deterioration > 10.0:
        passed_all_hurdles = False
        reasons.append(f"Max Drawdown deterioration (+{dd_deterioration}%) > 10.0% allowable limit.")

    if walk_forward_folds_passed < 4 or walk_forward_folds_total < 6:
        passed_all_hurdles = False
        reasons.append(f"Walk-forward folds passed ({walk_forward_folds_passed}/{walk_forward_folds_total}) < 4/6 required.")

    if is_single_period_dependent:
        passed_all_hurdles = False
        reasons.append(f"Single-period profit concentration ({max_fold_share_pct}%) exceeds 60.0% allowable limit.")

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
