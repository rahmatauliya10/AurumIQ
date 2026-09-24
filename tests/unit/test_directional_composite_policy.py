"""
Unit tests for XAUUSD Directional Sub-Profile Calibration Policy & Composite Assembler.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest

from engine.core.types import (
    Phase5CalibrationStatus,
    SignalSide,
    SignalState,
    UserDecision,
)
from engine.signals.profile import Phase4CalibrationStatus
from engine.backtest.xauusd_types import XauUsdSimulatedTrade, XauUsdTradeOutcome
from engine.backtest.xauusd_risk_candidate_generator import XauUsdJointCandidateGenerator
from engine.backtest.xauusd_composite_policy import (
    SideEvaluationMetrics,
    compute_composite_policy_fingerprint,
    construct_composite_candidate,
    evaluate_side_trades,
    load_governed_composite_calibration_policy,
    rank_side_subprofiles,
)
from scripts.run_xauusd_calibration import check_structural_reachability


def test_composite_policy_loading_and_fingerprint():
    """Verify governed policy artifact loads correctly and fingerprint is cryptographically valid."""
    policy = load_governed_composite_calibration_policy()
    assert policy.schema in (
        "aurumiq.calibration.composite_calibration_policy.v1",
        "aurumiq.calibration.composite_calibration_policy.v2",
    )
    assert policy.policy_id in (
        "XAUUSD-DIRECTIONAL-COMPOSITE-CALIBRATION-POLICY-v1",
        "XAUUSD-DIRECTIONAL-COMPOSITE-CALIBRATION-POLICY-v2",
    )
    assert policy.instrument == "XAUUSD"
    assert policy.total_folds == 5
    assert len(policy.folds) == 5
    assert policy.buy_min_effective_n == 60.0
    assert policy.sell_min_effective_n == 60.0
    assert policy.composite_combined_min_effective_n == 100.0
    assert policy.side_long_max_drawdown_r == 18.0
    assert policy.side_short_max_drawdown_r == 18.0
    assert policy.composite_combined_max_drawdown_r == 18.0

    # Verify recomputed fingerprint matches embedded policy fingerprint
    recomputed_fp = compute_composite_policy_fingerprint(policy.raw_payload)
    assert recomputed_fp == policy.policy_fingerprint


def test_side_metrics_evaluation_isolated_by_side():
    """Verify that evaluate_side_trades strictly isolates trades by side and computes governed metrics."""
    policy = load_governed_composite_calibration_policy()
    folds = policy.folds

    # Generate synthetic trades across fold 1 and fold 2
    f1_start = datetime.fromisoformat(folds[0]["val_start"].replace("Z", "+00:00"))
    f2_start = datetime.fromisoformat(folds[1]["val_start"].replace("Z", "+00:00"))

    trades = [
        # Long trade in Fold 1: +2.0 R
        XauUsdSimulatedTrade(
            trade_id="t1",
            side=SignalSide.LONG,
            candidate_state=SignalState.BUY_WINDOW,
            candidate_user_decision=UserDecision.BUY,
            source_signal_fingerprint="fp1",
            signal_timestamp=f1_start + timedelta(hours=1),
            fill_timestamp=f1_start + timedelta(hours=1, minutes=15),
            exit_timestamp=f1_start + timedelta(hours=2),
            risk_plan_fingerprint="rp1",
            planned_risk_amount=Decimal("100.00"),
            outcome=XauUsdTradeOutcome.TP1_FIRST,
            net_r=Decimal("2.00"),
        ),
        # Short trade in Fold 1: -1.0 R (must be ignored by LONG evaluation)
        XauUsdSimulatedTrade(
            trade_id="t2",
            side=SignalSide.SHORT,
            candidate_state=SignalState.SELL_WINDOW,
            candidate_user_decision=UserDecision.SELL,
            source_signal_fingerprint="fp2",
            signal_timestamp=f1_start + timedelta(hours=3),
            fill_timestamp=f1_start + timedelta(hours=3, minutes=15),
            exit_timestamp=f1_start + timedelta(hours=4),
            risk_plan_fingerprint="rp2",
            planned_risk_amount=Decimal("100.00"),
            outcome=XauUsdTradeOutcome.SL_FIRST,
            net_r=Decimal("-1.00"),
        ),
        # Long trade in Fold 2: +1.5 R
        XauUsdSimulatedTrade(
            trade_id="t3",
            side=SignalSide.LONG,
            candidate_state=SignalState.BUY_WINDOW,
            candidate_user_decision=UserDecision.BUY,
            source_signal_fingerprint="fp3",
            signal_timestamp=f2_start + timedelta(hours=1),
            fill_timestamp=f2_start + timedelta(hours=1, minutes=15),
            exit_timestamp=f2_start + timedelta(hours=2),
            risk_plan_fingerprint="rp3",
            planned_risk_amount=Decimal("100.00"),
            outcome=XauUsdTradeOutcome.TP1_FIRST,
            net_r=Decimal("1.50"),
        ),
    ]

    # Evaluate LONG: should see t1 and t3, but NOT t2
    long_res = evaluate_side_trades(
        trades=trades,
        side=SignalSide.LONG,
        folds=folds,
        policy=policy,
        candidate_id="CAND_TEST_001",
        candidate_index=1,
    )
    assert long_res.trade_count == 2
    assert long_res.side == SignalSide.LONG
    assert long_res.mean_r == 1.75
    assert long_res.side_max_drawdown_r == 0.0  # No loss in Long trades

    # Evaluate SHORT: should see t2 only, NOT t1 or t3
    short_res = evaluate_side_trades(
        trades=trades,
        side=SignalSide.SHORT,
        folds=folds,
        policy=policy,
        candidate_id="CAND_TEST_001",
        candidate_index=1,
    )
    assert short_res.trade_count == 1
    assert short_res.side == SignalSide.SHORT
    assert short_res.mean_r == -1.0
    assert short_res.side_max_drawdown_r == 1.0


def test_deterministic_side_ranking():
    """Verify ranking order: highest LCB95, then lowest MDD, then lowest index tie-break."""
    c1 = SideEvaluationMetrics(
        candidate_id="C1",
        index=1,
        side=SignalSide.LONG,
        trade_count=70,
        effective_n=65.0,
        mean_r=0.25,
        std_r=0.8,
        side_lcb_95=0.08,
        side_max_drawdown_r=12.0,
        temporal_stability=0.60,
        profit_concentration_pct=40.0,
        fold_expectancies=(0.1, 0.2, 0.3, 0.2, 0.1),
        positive_folds=5,
        total_folds=5,
        qualified=True,
        disqualification_reasons=(),
    )
    c2 = SideEvaluationMetrics(
        candidate_id="C2",
        index=2,
        side=SignalSide.LONG,
        trade_count=80,
        effective_n=75.0,
        mean_r=0.30,
        std_r=0.8,
        side_lcb_95=0.14,  # Higher LCB95 -> Should win
        side_max_drawdown_r=10.0,
        temporal_stability=0.65,
        profit_concentration_pct=35.0,
        fold_expectancies=(0.2, 0.2, 0.3, 0.2, 0.2),
        positive_folds=5,
        total_folds=5,
        qualified=True,
        disqualification_reasons=(),
    )
    c3 = SideEvaluationMetrics(
        candidate_id="C3",
        index=3,
        side=SignalSide.LONG,
        trade_count=75,
        effective_n=70.0,
        mean_r=0.28,
        std_r=0.8,
        side_lcb_95=0.14,  # Same LCB95 as C2, but lower MDD (8.0 vs 10.0) -> Should beat C2
        side_max_drawdown_r=8.0,
        temporal_stability=0.70,
        profit_concentration_pct=30.0,
        fold_expectancies=(0.2, 0.2, 0.3, 0.2, 0.2),
        positive_folds=5,
        total_folds=5,
        qualified=True,
        disqualification_reasons=(),
    )
    c4 = SideEvaluationMetrics(
        candidate_id="C4",
        index=4,
        side=SignalSide.LONG,
        trade_count=75,
        effective_n=70.0,
        mean_r=0.28,
        std_r=0.8,
        side_lcb_95=0.14,  # Same LCB95 and same MDD as C3, but higher index (4 > 3)
        side_max_drawdown_r=8.0,
        temporal_stability=0.70,
        profit_concentration_pct=30.0,
        fold_expectancies=(0.2, 0.2, 0.3, 0.2, 0.2),
        positive_folds=5,
        total_folds=5,
        qualified=True,
        disqualification_reasons=(),
    )

    ranked = rank_side_subprofiles([c1, c2, c3, c4])
    assert [m.candidate_id for m in ranked] == ["C3", "C4", "C2", "C1"]


def test_composite_construction_and_structural_reachability():
    """Verify that composite profile correctly fuses Long sub-profile and Short sub-profile."""
    policy = load_governed_composite_calibration_policy()
    gen = XauUsdJointCandidateGenerator()
    cand_long = gen.generate_joint_candidate(1)
    cand_short = gen.generate_joint_candidate(2)

    composite = construct_composite_candidate(
        best_long_candidate=cand_long,
        best_short_candidate=cand_short,
        policy=policy,
    )

    # 1. Signal Profile Verification
    sig = composite.signal_profile
    assert sig.long_direction == cand_long.signal_profile.long_direction
    assert sig.long_timing == cand_long.signal_profile.long_timing
    assert sig.long_gate == cand_long.signal_profile.long_gate
    assert sig.short_direction == cand_short.signal_profile.short_direction
    assert sig.short_timing == cand_short.signal_profile.short_timing
    assert sig.short_gate == cand_short.signal_profile.short_gate

    # 2. Risk Profile Verification
    risk = composite.risk_profile
    assert risk.long_risk_policy == cand_long.risk_profile.long_risk_policy
    assert risk.short_risk_policy == cand_short.risk_profile.short_risk_policy
    assert risk.long_execution_policy == cand_long.risk_profile.long_execution_policy
    assert risk.short_execution_policy == cand_short.risk_profile.short_execution_policy

    # 3. Governed Shared Fields
    assert sig.target_instrument == "XAUUSD"
    assert sig.is_production_authorized is False
    assert risk.is_production_authorized is False
    assert sig.feed_policy.primary_15m.value == "CRITICAL"
    assert sig.feed_policy.macro_blackout.value == "CRITICAL"

    # 4. Structural Reachability Precheck
    reachable, reason = check_structural_reachability(sig)
    assert reachable is True, f"Composite signal profile failed reachability: {reason}"
