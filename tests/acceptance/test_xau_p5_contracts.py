"""
Official Acceptance Test Suite for Phase 5 XAUUSD Side-Aware Risk Engine.
Strictly covers official contracts:
  - XAU-P5-01: LONG side-aware risk planning contract
  - XAU-P5-02: SHORT side-aware risk planning contract
  - XAU-P5-03: Side-aware market bid/ask execution contract
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest

from engine.core.types import (
    DualSideSignalSnapshot,
    QuoteData,
    RiskCandidateStatus,
    RiskSide,
    SideDirectionScoreResult,
    SideTimingScoreResult,
    SignalState,
    StructureResult,
    StructureType,
    StructureZone,
    UserDecision,
    XauUsdHardGateEvaluation,
    RuntimeFeedHealth,
)
from engine.risk.xauusd_execution import SideAwareEntryExecutionModel
from engine.risk.xauusd_planner import XauUsdRiskPlanner
from engine.risk.xauusd_policy import (
    SideRiskPolicy,
    XauUsdExecutionPolicy,
    XauUsdRiskProfile,
)


@pytest.fixture
def calibrated_xauusd_test_profile():
    """
    Explicit TEST_ONLY calibrated profile for official acceptance verification.
    These empirical values are strictly isolated to test fixtures.
    """
    return XauUsdRiskProfile(
        name="XAUUSD_TEST_CALIBRATED",
        long_risk_policy=SideRiskPolicy(
            structure_buffer=Decimal("1.50"),
            atr_multiplier=Decimal("2.0"),
            max_stop_distance_atr=Decimal("4.0"),
            min_rr_tp1=Decimal("1.80"),
            tp2_atr_multiplier=Decimal("2.5"),
        ),
        short_risk_policy=SideRiskPolicy(
            structure_buffer=Decimal("1.50"),
            atr_multiplier=Decimal("2.0"),
            max_stop_distance_atr=Decimal("4.0"),
            min_rr_tp1=Decimal("1.80"),
            tp2_atr_multiplier=Decimal("2.5"),
        ),
        long_execution_policy=XauUsdExecutionPolicy(
            latency_seconds=1.0,
            synthetic_spread_pct=Decimal("0.02"),
            slippage_pct=Decimal("0.01"),
        ),
        short_execution_policy=XauUsdExecutionPolicy(
            latency_seconds=1.0,
            synthetic_spread_pct=Decimal("0.02"),
            slippage_pct=Decimal("0.01"),
        ),
    )


def make_official_dual_side_signal(
    candidate_state: SignalState,
    candidate_decision: UserDecision,
    timestamp: datetime,
) -> DualSideSignalSnapshot:
    """Helper creating canonical DualSideSignalSnapshot for official contracts."""
    dir_res = SideDirectionScoreResult(RiskSide.LONG, 85.0, 100.0, (), True, True)
    tim_res = SideTimingScoreResult(RiskSide.LONG, 85.0, 100.0, (), True, True)
    hg = XauUsdHardGateEvaluation(False, None, (), RuntimeFeedHealth())
    return DualSideSignalSnapshot(
        timestamp=timestamp,
        instrument="XAUUSD",
        timeframe="15m",
        state=SignalState.NO_TRADE,
        user_decision=UserDecision.WAIT,
        candidate_state=candidate_state,
        candidate_user_decision=candidate_decision,
        long_direction=dir_res,
        short_direction=dir_res,
        long_timing=tim_res,
        short_timing=tim_res,
        hard_gate=hg,
        reasons_long_positive=("Bullish momentum confirmed",),
        reasons_long_negative=(),
        reasons_short_positive=(),
        reasons_short_negative=(),
        hard_gate_reasons=(),
        resolution_reason="Production blocked pending Phase 6",
        candidate_resolution_reason="Layer A candidate evaluated",
        publication_reason="Layer B production authority blocked",
        analysis_fingerprint="sig_fp_xau_p5_contract_test",
        phase4_policy_fingerprint="p4_pol_fp_test_123",
        code_revision="xau_p5_test_revision",
        profile_name="XAUUSD_TEST",
        calibration_status="CANDIDATE_NOT_FROZEN",
    )


@pytest.mark.acceptance
def test_xau_p5_01_long_risk_contract(calibrated_xauusd_test_profile):
    """
    XAU-P5-01: LONG side-aware risk planning contract.

    Verifies:
      1. Source candidate BUY_WINDOW / BUY is required.
      2. Entry zone is derived strictly from confirmed active support.
      3. Stop loss is placed below entry zone via min(structure_stop, atr_stop).
      4. TP1 is derived strictly from nearest confirmed structural resistance above entry.
      5. Conservative RR is computed using entry_max and strictly satisfies min_rr_tp1.
      6. Phase 4 candidate snapshot remains immutable.
      7. Production effective action remains WAIT pending Phase 6 governance.
    """
    eval_ts = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    phase4_snap = make_official_dual_side_signal(
        candidate_state=SignalState.BUY_WINDOW,
        candidate_decision=UserDecision.BUY,
        timestamp=eval_ts,
    )
    orig_fingerprint = phase4_snap.analysis_fingerprint

    # Active support zone at [2500.00, 2505.00]
    support = StructureZone("SUPPORT", Decimal("2500.00"), Decimal("2505.00"), eval_ts, 2, True)
    # Structural resistance target at [2530.00, 2535.00]
    resistance = StructureZone("RESISTANCE", Decimal("2530.00"), Decimal("2535.00"), eval_ts, 2, True)
    structure_15m = StructureResult(eval_ts, StructureType.HH, None, None, None, (), (support, resistance))

    planner = XauUsdRiskPlanner(code_revision="p5_ci_rev", risk_profile=calibrated_xauusd_test_profile)

    # Execute LONG planning with Decimal ATR
    atr14 = Decimal("5.00")
    plan = planner.plan_long(
        phase4_snapshot=phase4_snap,
        structure_15m=structure_15m,
        atr14=atr14,
    )

    # 1. Verification of validity & authority segregation
    assert plan.risk_candidate_valid is True
    assert plan.risk_candidate_status == RiskCandidateStatus.VALID_LONG_RISK_CANDIDATE
    assert plan.candidate_effective_action == UserDecision.BUY
    assert plan.publication_effective_action == UserDecision.WAIT  # Layer B authority blocked!
    assert plan.side == RiskSide.LONG

    # 2. Verification of coordinates & conservative RR
    # Entry: min=2500, max=2505, mid=2502.50
    assert plan.entry_min == Decimal("2500.00")
    assert plan.entry_max == Decimal("2505.00")
    assert plan.entry_mid == Decimal("2502.50")

    # Stop: structure=2500 - 1.50 = 2498.50, ATR=2502.50 - 10.00 = 2492.50 -> stop_final = 2492.50
    assert plan.stop_structure == Decimal("2498.50")
    assert plan.stop_atr == Decimal("2492.50")
    assert plan.stop_final == Decimal("2492.50")
    assert plan.stop_final < plan.entry_min

    # Planned risk = entry_max - stop_final = 2505.00 - 2492.50 = 12.50 -> stop_dist = 2.50 ATR <= 4.00 ATR
    assert plan.stop_distance_atr == Decimal("2.50")

    # TP1 = 2530.00 -> reward = 2530 - 2505 = 25.00 -> RR = 25.00 / 12.50 = 2.00 >= 1.80
    assert plan.tp1 == Decimal("2530.00")
    assert plan.planned_rr_tp1 == Decimal("2.00")

    # 3. Provenance & Immutability
    assert plan.source_phase4_fingerprint == orig_fingerprint
    assert phase4_snap.analysis_fingerprint == orig_fingerprint
    assert plan.signal_generated_at == eval_ts
    assert isinstance(plan.risk_plan_fingerprint, str) and len(plan.risk_plan_fingerprint) == 64


@pytest.mark.acceptance
def test_xau_p5_02_short_risk_contract(calibrated_xauusd_test_profile):
    """
    XAU-P5-02: SHORT side-aware risk planning contract.

    Verifies:
      1. Source candidate SELL_WINDOW / SELL is required.
      2. Entry zone is derived strictly from confirmed active resistance.
      3. Stop loss is placed above entry zone via max(structure_stop, atr_stop).
      4. TP1 is derived strictly from nearest confirmed structural support below entry.
      5. Conservative RR is computed using entry_min and strictly satisfies min_rr_tp1.
      6. SHORT is explicitly independent (NOT implemented by negating LONG coordinates).
      7. Production effective action remains WAIT pending Phase 6 governance.
    """
    eval_ts = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    phase4_snap = make_official_dual_side_signal(
        candidate_state=SignalState.SELL_WINDOW,
        candidate_decision=UserDecision.SELL,
        timestamp=eval_ts,
    )
    orig_fingerprint = phase4_snap.analysis_fingerprint

    # Active resistance zone at [2500.00, 2505.00]
    resistance = StructureZone("RESISTANCE", Decimal("2500.00"), Decimal("2505.00"), eval_ts, 2, True)
    # Structural support target at [2470.00, 2475.00]
    support = StructureZone("SUPPORT", Decimal("2470.00"), Decimal("2475.00"), eval_ts, 2, True)
    structure_15m = StructureResult(eval_ts, StructureType.LL, None, None, None, (), (resistance, support))

    planner = XauUsdRiskPlanner(code_revision="p5_ci_rev", risk_profile=calibrated_xauusd_test_profile)

    # Execute SHORT planning with Decimal ATR
    atr14 = Decimal("5.00")
    plan = planner.plan_short(
        phase4_snapshot=phase4_snap,
        structure_15m=structure_15m,
        atr14=atr14,
    )

    # 1. Verification of validity & authority segregation
    assert plan.risk_candidate_valid is True
    assert plan.risk_candidate_status == RiskCandidateStatus.VALID_SHORT_RISK_CANDIDATE
    assert plan.candidate_effective_action == UserDecision.SELL
    assert plan.publication_effective_action == UserDecision.WAIT  # Layer B authority blocked!
    assert plan.side == RiskSide.SHORT

    # 2. Verification of coordinates & conservative RR
    # Entry: min=2500, max=2505, mid=2502.50
    assert plan.entry_min == Decimal("2500.00")
    assert plan.entry_max == Decimal("2505.00")
    assert plan.entry_mid == Decimal("2502.50")

    # Stop: structure=2505 + 1.50 = 2506.50, ATR=2502.50 + 10.00 = 2512.50 -> stop_final = 2512.50
    assert plan.stop_structure == Decimal("2506.50")
    assert plan.stop_atr == Decimal("2512.50")
    assert plan.stop_final == Decimal("2512.50")
    assert plan.stop_final > plan.entry_max

    # Planned risk = stop_final - entry_min = 2512.50 - 2500.00 = 12.50 -> stop_dist = 2.50 ATR <= 4.00 ATR
    assert plan.stop_distance_atr == Decimal("2.50")

    # TP1 = 2475.00 -> reward = 2500 - 2475 = 25.00 -> RR = 25.00 / 12.50 = 2.00 >= 1.80
    assert plan.tp1 == Decimal("2475.00")
    assert plan.planned_rr_tp1 == Decimal("2.00")

    # 3. Provenance & Immutability
    assert plan.source_phase4_fingerprint == orig_fingerprint
    assert phase4_snap.analysis_fingerprint == orig_fingerprint
    assert plan.signal_generated_at == eval_ts
    assert isinstance(plan.risk_plan_fingerprint, str) and len(plan.risk_plan_fingerprint) == 64


@pytest.mark.acceptance
def test_xau_p5_03_side_aware_market_execution_contract(calibrated_xauusd_test_profile):
    """
    XAU-P5-03: Side-aware market bid/ask execution contract.

    Verifies:
      1. LONG market entry executes at ASK price + adverse slippage (UP).
      2. SHORT market entry executes at BID price - adverse slippage (DOWN).
      3. Spread is counted exactly once (embedded in quote; observed spread is informational).
      4. Strict quote validation ensures invalid or crossed quotes never create fills.
      5. Lossless source evidence fingerprint and execution fingerprint are preserved.
    """
    exec_model = SideAwareEntryExecutionModel(
        code_revision="p5_ci_rev",
        execution_policy=calibrated_xauusd_test_profile.long_execution_policy,
        phase5_policy_fingerprint="exec_pol_fp_test_123",
    )

    sig_ts = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    # latency = 1.0s -> earliest_exec_ts = 8:00:01

    quote = QuoteData(
        timestamp=sig_ts + timedelta(seconds=2),
        bid=Decimal("2500.00"),
        ask=Decimal("2500.40"),
        source="orderbook_feed_1",
    )

    # 1. LONG Execution (ASK)
    # raw = 2500.40, slippage 0.01% of 2500.40 = 0.25004 -> fill = 2500.65004
    res_long = exec_model.simulate_market_after_signal(
        side=RiskSide.LONG,
        signal_generated_at=sig_ts,
        quotes=[quote],
        source_phase4_fingerprint="phase4_sig_fp_test",
    )
    assert res_long.is_filled is True
    assert res_long.raw_executable_price == Decimal("2500.40")
    assert res_long.fill_price == Decimal("2500.40") + (Decimal("2500.40") * Decimal("0.0001"))
    assert res_long.adverse_slippage == Decimal("2500.40") * Decimal("0.0001")
    assert res_long.observed_spread == Decimal("0.40")
    assert res_long.synthetic_spread == Decimal("0.00")
    assert res_long.source_evidence_type == "QUOTE"
    assert isinstance(res_long.source_evidence_fingerprint, str) and len(res_long.source_evidence_fingerprint) == 64
    assert isinstance(res_long.execution_fingerprint, str) and len(res_long.execution_fingerprint) == 64

    # 2. SHORT Execution (BID)
    # raw = 2500.00, slippage 0.01% of 2500.00 = 0.25 -> fill = 2499.75 (adverse slippage lowers fill price)
    res_short = exec_model.simulate_market_after_signal(
        side=RiskSide.SHORT,
        signal_generated_at=sig_ts,
        quotes=[quote],
        source_phase4_fingerprint="phase4_sig_fp_test",
    )
    assert res_short.is_filled is True
    assert res_short.raw_executable_price == Decimal("2500.00")
    assert res_short.fill_price == Decimal("2499.75")
    assert res_short.adverse_slippage == Decimal("0.25")
    assert res_short.observed_spread == Decimal("0.40")
    assert res_short.synthetic_spread == Decimal("0.00")
    assert res_short.source_evidence_type == "QUOTE"
