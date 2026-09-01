"""
Unit tests for master XauUsdRiskPlanner evaluating LONG and SHORT setups.
"""
from datetime import datetime, timezone
from decimal import Decimal
import pytest

from engine.core.types import (
    DualSideSignalSnapshot,
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
from engine.risk.xauusd_planner import XauUsdRiskPlanner
from engine.risk.xauusd_policy import (
    SideRiskPolicy,
    XauUsdRiskProfile,
    uncalibrated_xauusd_risk_profile,
)


def make_test_dual_side_snapshot(
    candidate_state: SignalState,
    candidate_decision: UserDecision,
    eval_ts: datetime,
    instrument: str = "XAUUSD",
) -> DualSideSignalSnapshot:
    """Fixture creating immutable DualSideSignalSnapshot for tests."""
    side_dir = SideDirectionScoreResult(RiskSide.LONG, 85.0, 100.0, (), True, True)
    side_tim = SideTimingScoreResult(RiskSide.LONG, 85.0, 100.0, (), True, True)
    hg = XauUsdHardGateEvaluation(False, None, (), RuntimeFeedHealth())
    return DualSideSignalSnapshot(
        timestamp=eval_ts,
        instrument=instrument,
        timeframe="15m",
        state=SignalState.NO_TRADE,             # Published Layer B always NO_TRADE
        user_decision=UserDecision.WAIT,        # Published Layer B always WAIT
        candidate_state=candidate_state,
        candidate_user_decision=candidate_decision,
        long_direction=side_dir,
        short_direction=side_dir,
        long_timing=side_tim,
        short_timing=side_tim,
        hard_gate=hg,
        reasons_long_positive=("Strong momentum",),
        reasons_long_negative=(),
        reasons_short_positive=(),
        reasons_short_negative=(),
        hard_gate_reasons=(),
        resolution_reason="Production blocked pending Phase 6",
        candidate_resolution_reason="Candidate signal evaluated",
        publication_reason="Production blocked",
        analysis_fingerprint="test_phase4_analysis_fp_12345",
        phase4_policy_fingerprint="test_phase4_policy_fp_67890",
        code_revision="test_rev_p4",
        profile_name="XAUUSD_TEST",
        calibration_status="CANDIDATE_NOT_FROZEN",
    )


@pytest.fixture
def calibrated_test_profile():
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
    )


@pytest.mark.unit
def test_planner_requires_code_revision():
    """XauUsdRiskPlanner requires non-empty code_revision."""
    with pytest.raises(ValueError, match="code_revision is required"):
        XauUsdRiskPlanner(code_revision="")


@pytest.mark.unit
def test_planner_rejects_non_xauusd_snapshot(calibrated_test_profile):
    """Planner rejects snapshot with instrument != 'XAUUSD'."""
    planner = XauUsdRiskPlanner(code_revision="test_rev", risk_profile=calibrated_test_profile)
    t = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    snap = make_test_dual_side_snapshot(SignalState.BUY_WINDOW, UserDecision.BUY, t, instrument="XAUT")

    with pytest.raises(ValueError, match="requires XAUUSD snapshot"):
        planner.plan_long(snap, structure_15m=None, atr14=Decimal("5.0"))


@pytest.mark.unit
def test_planner_rejects_naive_timestamp(calibrated_test_profile):
    """Planner rejects snapshot with naive timestamp (lacks timezone awareness)."""
    planner = XauUsdRiskPlanner(code_revision="test_rev", risk_profile=calibrated_test_profile)
    naive_t = datetime(2026, 9, 1, 8, 0, 0)
    snap_long = make_test_dual_side_snapshot(SignalState.BUY_WINDOW, UserDecision.BUY, naive_t)
    snap_short = make_test_dual_side_snapshot(SignalState.SELL_WINDOW, UserDecision.SELL, naive_t)

    with pytest.raises(ValueError, match="must be timezone aware"):
        planner.plan_long(snap_long, structure_15m=None, atr14=Decimal("5.0"))

    with pytest.raises(ValueError, match="must be timezone aware"):
        planner.plan_short(snap_short, structure_15m=None, atr14=Decimal("5.0"))


@pytest.mark.unit
def test_plan_long_valid(calibrated_test_profile):
    """Valid LONG candidate produces valid SideRiskPlanSnapshot with candidate_effective_action=BUY, publication=WAIT."""
    planner = XauUsdRiskPlanner(code_revision="test_rev", risk_profile=calibrated_test_profile)
    t = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    snap = make_test_dual_side_snapshot(SignalState.BUY_WINDOW, UserDecision.BUY, t)

    support = StructureZone("SUPPORT", Decimal("2500.00"), Decimal("2505.00"), t, 2, True)
    res1 = StructureZone("RESISTANCE", Decimal("2530.00"), Decimal("2535.00"), t, 2, True)
    struct_15m = StructureResult(t, StructureType.HH, None, None, None, (), (support, res1))

    plan = planner.plan_long(
        phase4_snapshot=snap,
        structure_15m=struct_15m,
        atr14=Decimal("5.00"),
    )

    assert plan.risk_candidate_valid is True
    assert plan.risk_candidate_status == RiskCandidateStatus.VALID_LONG_RISK_CANDIDATE
    assert plan.candidate_effective_action == UserDecision.BUY
    assert plan.publication_effective_action == UserDecision.WAIT  # Layer B authority blocked!
    assert plan.side == RiskSide.LONG
    assert plan.entry_min == Decimal("2500.00")
    assert plan.entry_max == Decimal("2505.00")
    assert plan.tp1 == Decimal("2530.00")
    assert plan.planned_rr_tp1 == Decimal("2.0")  # (2530 - 2505) / 12.50 = 25 / 12.50 = 2.0
    assert plan.source_phase4_fingerprint == snap.analysis_fingerprint
    assert plan.signal_generated_at == t


@pytest.mark.unit
def test_plan_short_valid(calibrated_test_profile):
    """Valid SHORT candidate produces valid SideRiskPlanSnapshot with candidate_effective_action=SELL, publication=WAIT."""
    planner = XauUsdRiskPlanner(code_revision="test_rev", risk_profile=calibrated_test_profile)
    t = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    snap = make_test_dual_side_snapshot(SignalState.SELL_WINDOW, UserDecision.SELL, t)

    resistance = StructureZone("RESISTANCE", Decimal("2500.00"), Decimal("2505.00"), t, 2, True)
    sup1 = StructureZone("SUPPORT", Decimal("2470.00"), Decimal("2475.00"), t, 2, True)
    struct_15m = StructureResult(t, StructureType.LL, None, None, None, (), (resistance, sup1))

    plan = planner.plan_short(
        phase4_snapshot=snap,
        structure_15m=struct_15m,
        atr14=Decimal("5.00"),
    )

    assert plan.risk_candidate_valid is True
    assert plan.risk_candidate_status == RiskCandidateStatus.VALID_SHORT_RISK_CANDIDATE
    assert plan.candidate_effective_action == UserDecision.SELL
    assert plan.publication_effective_action == UserDecision.WAIT  # Layer B authority blocked!
    assert plan.side == RiskSide.SHORT
    assert plan.entry_min == Decimal("2500.00")
    assert plan.entry_max == Decimal("2505.00")
    assert plan.tp1 == Decimal("2475.00")
    assert plan.planned_rr_tp1 == Decimal("2.0")  # (2500 - 2475) / 12.50 = 25 / 12.50 = 2.0


@pytest.mark.unit
def test_uncalibrated_profile_demotes_to_wait():
    """Uncalibrated profile produces invalid plan with candidate_effective_action=WAIT."""
    planner = XauUsdRiskPlanner(code_revision="test_rev", risk_profile=uncalibrated_xauusd_risk_profile())
    t = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    snap = make_test_dual_side_snapshot(SignalState.BUY_WINDOW, UserDecision.BUY, t)

    support = StructureZone("SUPPORT", Decimal("2500.00"), Decimal("2505.00"), t, 2, True)
    struct_15m = StructureResult(t, StructureType.HH, None, None, None, (), (support,))
    plan = planner.plan_long(snap, structure_15m=struct_15m, atr14=Decimal("5.00"))

    assert plan.risk_candidate_valid is False
    assert plan.candidate_effective_action == UserDecision.WAIT
    assert plan.publication_effective_action == UserDecision.WAIT
    assert plan.entry_min is None
    assert "not configured" in plan.reasons[0]


@pytest.mark.unit
def test_naive_structure_result_or_zone_created_at_rejected(calibrated_test_profile):
    """Naive StructureResult timestamp or naive zone created_at cannot contribute entry evidence."""
    planner = XauUsdRiskPlanner(code_revision="test_rev", risk_profile=calibrated_test_profile)
    t = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    t_naive = datetime(2026, 9, 1, 8, 0, 0)
    snap = make_test_dual_side_snapshot(SignalState.BUY_WINDOW, UserDecision.BUY, t)
    snap_short = make_test_dual_side_snapshot(SignalState.SELL_WINDOW, UserDecision.SELL, t)

    # 1. Naive StructureResult timestamp for LONG
    support_aware = StructureZone("SUPPORT", Decimal("2500.00"), Decimal("2505.00"), t, 2, True)
    struct_naive_ts = StructureResult(t_naive, StructureType.HH, None, None, None, (), (support_aware,))
    plan1 = planner.plan_long(snap, structure_15m=struct_naive_ts, atr14=Decimal("5.00"))
    assert plan1.risk_candidate_valid is False
    assert "Missing confirmed active support zone" in plan1.reasons[0]

    # 2. Naive zone created_at for LONG
    support_naive = StructureZone("SUPPORT", Decimal("2500.00"), Decimal("2505.00"), t_naive, 2, True)
    struct_naive_zone = StructureResult(t, StructureType.HH, None, None, None, (), (support_naive,))
    plan2 = planner.plan_long(snap, structure_15m=struct_naive_zone, atr14=Decimal("5.00"))
    assert plan2.risk_candidate_valid is False
    assert "Missing confirmed active support zone" in plan2.reasons[0]

    # 3. Naive zone created_at for SHORT
    res_naive = StructureZone("RESISTANCE", Decimal("2500.00"), Decimal("2505.00"), t_naive, 2, True)
    struct_short_naive_zone = StructureResult(t, StructureType.LL, None, None, None, (), (res_naive,))
    plan3 = planner.plan_short(snap_short, structure_15m=struct_short_naive_zone, atr14=Decimal("5.00"))
    assert plan3.risk_candidate_valid is False
    assert "Missing confirmed active resistance zone" in plan3.reasons[0]

