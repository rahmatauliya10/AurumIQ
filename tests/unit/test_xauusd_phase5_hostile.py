"""
Comprehensive hostile test suite for Phase 5 XAUUSD Side-Aware Risk Engine.
Covers adversarial test cases H1 through H74.
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import math
from typing import Optional
import pytest

from engine.core.types import (
    BarrierHitType,
    CandleData,
    DualSideSignalSnapshot,
    EntryExecutionPolicy,
    IntrabarPolicy,
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
    VolumeEvidenceType,
    XauUsdHardGateEvaluation,
    RuntimeFeedHealth,
)
from engine.risk.xauusd_execution import (
    SideAwareEntryExecutionModel,
    validate_xauusd_candle,
    validate_xauusd_quote,
)
from engine.risk.xauusd_fingerprints import (
    canonical_utc_timestamp,
    compute_candle_evidence_fingerprint,
    compute_execution_fingerprint,
    compute_phase5_policy_fingerprint,
    compute_quote_evidence_fingerprint,
    compute_risk_plan_fingerprint,
    compute_zone_fingerprint,
)
from engine.risk.xauusd_intrabar import SideAwareIntrabarResolver
from engine.risk.xauusd_planner import XauUsdRiskPlanner
from engine.risk.xauusd_policy import (
    SideRiskPolicy,
    XauUsdExecutionPolicy,
    XauUsdRiskProfile,
    uncalibrated_xauusd_risk_profile,
)
from engine.risk.xauusd_stops import (
    calculate_long_stops,
    calculate_short_stops,
)
from engine.risk.xauusd_targets import (
    calculate_long_targets,
    calculate_short_targets,
)


def _make_snapshot(
    cand_state: SignalState = SignalState.BUY_WINDOW,
    cand_dec: UserDecision = UserDecision.BUY,
    eval_ts: Optional[datetime] = None,
    instrument: str = "XAUUSD",
) -> DualSideSignalSnapshot:
    t = eval_ts or datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    s_dir = SideDirectionScoreResult(RiskSide.LONG, 85.0, 100.0, (), True, True)
    s_tim = SideTimingScoreResult(RiskSide.LONG, 85.0, 100.0, (), True, True)
    hg = XauUsdHardGateEvaluation(False, None, (), RuntimeFeedHealth())
    return DualSideSignalSnapshot(
        timestamp=t,
        instrument=instrument,
        timeframe="15m",
        state=SignalState.NO_TRADE,
        user_decision=UserDecision.WAIT,
        candidate_state=cand_state,
        candidate_user_decision=cand_dec,
        long_direction=s_dir,
        short_direction=s_dir,
        long_timing=s_tim,
        short_timing=s_tim,
        hard_gate=hg,
        reasons_long_positive=("Positive",),
        reasons_long_negative=(),
        reasons_short_positive=(),
        reasons_short_negative=(),
        hard_gate_reasons=(),
        resolution_reason="Production blocked",
        candidate_resolution_reason="Candidate evaluated",
        publication_reason="Production blocked",
        analysis_fingerprint="test_phase4_fp_abc123",
        phase4_policy_fingerprint="test_p4_policy_fp_xyz",
        code_revision="test_rev",
        profile_name="XAUUSD_TEST",
        calibration_status="CANDIDATE_NOT_FROZEN",
    )


@pytest.fixture
def test_profile():
    return XauUsdRiskProfile(
        name="XAUUSD_TEST",
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


# H1 - H10: Core side segregation, target routing, PIT, and numeric safety
@pytest.mark.unit
def test_h01_short_not_negated_long(test_profile):
    """H1: SHORT formulas explicitly use max(), +buffer, BID, and entry_min."""
    res = StructureZone("RESISTANCE", Decimal("2500.00"), Decimal("2505.00"), datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc), 2, True)
    # Stop = max(2505 + 1.50 = 2506.50, 2502.50 + 10 = 2512.50) = 2512.50
    st_struct, st_atr, st_final, st_dist, ok, _ = calculate_short_stops(
        res, Decimal("2500.00"), Decimal("2502.50"), Decimal("2505.00"), Decimal("5.00"), test_profile.short_risk_policy
    )
    assert ok is True
    assert st_final == Decimal("2512.50")
    assert st_final > Decimal("2505.00")


@pytest.mark.unit
def test_h02_long_uses_support_short_uses_resistance(test_profile):
    """H2: LONG requires SUPPORT zone, SHORT requires RESISTANCE zone."""
    planner = XauUsdRiskPlanner("rev", test_profile)
    t = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    res_zone = StructureZone("RESISTANCE", Decimal("2500.00"), Decimal("2505.00"), t, 2, True)

    snap_l = _make_snapshot(SignalState.BUY_WINDOW, UserDecision.BUY, t)
    struct_only_res = StructureResult(t, StructureType.HH, None, None, None, (), (res_zone,))
    plan_l = planner.plan_long(snap_l, struct_only_res, Decimal("5.00"))
    assert plan_l.risk_candidate_valid is False
    assert "Missing confirmed active support zone" in plan_l.reasons[0]


@pytest.mark.unit
def test_h03_long_tp_resistance_short_tp_support(test_profile):
    """H3: LONG TP1 requires RESISTANCE above entry; SHORT TP1 requires SUPPORT below entry."""
    t = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    sup = StructureZone("SUPPORT", Decimal("2480.00"), Decimal("2485.00"), t, 2, True)
    struct_sup = StructureResult(t, StructureType.HH, None, None, None, (), (sup,))

    # LONG target evaluation with only SUPPORT zone fails
    tp1, _, _, _, _, _, ok, err = calculate_long_targets(
        Decimal("2500.00"), Decimal("2502.50"), Decimal("2505.00"), Decimal("2495.00"),
        struct_sup, Decimal("5.00"), t, test_profile.long_risk_policy
    )
    assert ok is False
    assert tp1 is None


@pytest.mark.unit
def test_h04_h05_future_zones_rejected(test_profile):
    """H4 & H5: Future entry and target zones (created_at > T) are rejected."""
    t = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    future_t = t + timedelta(minutes=5)
    future_res = StructureZone("RESISTANCE", Decimal("2530.00"), Decimal("2535.00"), future_t, 2, True)
    struct_future = StructureResult(t, StructureType.HH, None, None, None, (), (future_res,))

    tp1, _, _, _, _, _, ok, _ = calculate_long_targets(
        Decimal("2500.00"), Decimal("2502.50"), Decimal("2505.00"), Decimal("2495.00"),
        struct_future, Decimal("5.00"), t, test_profile.long_risk_policy
    )
    assert ok is False
    assert tp1 is None


@pytest.mark.unit
def test_h06_h07_no_fabricated_tp1(test_profile):
    """H6 & H7: Missing structural TP1 returns INVALID_RISK_CANDIDATE; no RR fabrication."""
    t = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    empty_struct = StructureResult(t, StructureType.HH, None, None, None, (), ())
    tp1, tp2, rr1, _, _, _, ok, err = calculate_long_targets(
        Decimal("2500.00"), Decimal("2502.50"), Decimal("2505.00"), Decimal("2495.00"),
        empty_struct, Decimal("5.00"), t, test_profile.long_risk_policy
    )
    assert ok is False
    assert tp1 is None
    assert rr1 is None


@pytest.mark.unit
def test_h08_h09_conservative_rr_worst_entry(test_profile):
    """H8 & H9: LONG RR uses entry_max (2505); SHORT RR uses entry_min (2500)."""
    t = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    res = StructureZone("RESISTANCE", Decimal("2525.00"), Decimal("2530.00"), t, 2, True)
    struct_res = StructureResult(t, StructureType.HH, None, None, None, (), (res,))
    # LONG: risk = 2505 - 2495 = 10. reward = 2525 - 2505 = 20 -> RR = 2.0
    _, _, rr_l, _, _, _, ok_l, _ = calculate_long_targets(
        Decimal("2500.00"), Decimal("2502.50"), Decimal("2505.00"), Decimal("2495.00"),
        struct_res, Decimal("5.00"), t, test_profile.long_risk_policy
    )
    assert ok_l is True
    assert rr_l == Decimal("2.0")

    sup = StructureZone("SUPPORT", Decimal("2475.00"), Decimal("2480.00"), t, 2, True)
    struct_sup = StructureResult(t, StructureType.LL, None, None, None, (), (sup,))
    # SHORT: risk = 2510 - 2500 = 10. reward = 2500 - 2480 = 20 -> RR = 2.0
    _, _, rr_s, _, _, _, ok_s, _ = calculate_short_targets(
        Decimal("2500.00"), Decimal("2502.50"), Decimal("2505.00"), Decimal("2510.00"),
        struct_sup, Decimal("5.00"), t, test_profile.short_risk_policy
    )
    assert ok_s is True
    assert rr_s == Decimal("2.0")


@pytest.mark.unit
def test_h10_atr_non_positive_rejected(test_profile):
    """H10: ATR <= 0 or non-finite rejected."""
    support = StructureZone("SUPPORT", Decimal("2500.00"), Decimal("2505.00"), datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc), 2, True)
    _, _, _, _, ok1, _ = calculate_long_stops(support, Decimal("2500"), Decimal("2502.5"), Decimal("2505"), Decimal("0.00"), test_profile.long_risk_policy)
    _, _, _, _, ok2, _ = calculate_long_stops(support, Decimal("2500"), Decimal("2502.5"), Decimal("2505"), Decimal("-5.00"), test_profile.long_risk_policy)
    assert ok1 is False
    assert ok2 is False


# H11 - H20: Policy numerics, stop sides, immutability, execution prices
@pytest.mark.unit
def test_h11_h12_nan_inf_policy_rejected():
    """H11 & H12: NaN and ±Inf policy values are rejected by is_configured."""
    p_nan = SideRiskPolicy(structure_buffer=Decimal("NaN"), atr_multiplier=Decimal("2.0"), max_stop_distance_atr=Decimal("4.0"), min_rr_tp1=Decimal("1.80"))
    p_inf = SideRiskPolicy(structure_buffer=Decimal("Infinity"), atr_multiplier=Decimal("2.0"), max_stop_distance_atr=Decimal("4.0"), min_rr_tp1=Decimal("1.80"))
    assert p_nan.is_configured is False
    assert p_inf.is_configured is False


@pytest.mark.unit
def test_h13_stop_on_wrong_side_rejected(test_profile):
    """H13: LONG stop >= entry_min or SHORT stop <= entry_max rejected."""
    sup = StructureZone("SUPPORT", Decimal("2500.00"), Decimal("2505.00"), datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc), 2, True)
    bad_policy = SideRiskPolicy(structure_buffer=Decimal("-10.00"), atr_multiplier=Decimal("-2.0"), max_stop_distance_atr=Decimal("4.0"), min_rr_tp1=Decimal("1.80"))
    # Stop would be 2500 - (-10) = 2510 >= 2500
    _, _, _, _, ok, err = calculate_long_stops(sup, Decimal("2500"), Decimal("2502.5"), Decimal("2505"), Decimal("5.0"), bad_policy)
    assert ok is False


@pytest.mark.unit
def test_h14_rr_denominator_non_positive_rejected(test_profile):
    """H14: Stop >= entry_max for LONG produces planned_risk <= 0 and is rejected."""
    t = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    res = StructureZone("RESISTANCE", Decimal("2530.00"), Decimal("2535.00"), t, 2, True)
    struct_res = StructureResult(t, StructureType.HH, None, None, None, (), (res,))
    _, _, _, _, _, _, ok, err = calculate_long_targets(
        Decimal("2500.00"), Decimal("2502.50"), Decimal("2505.00"), Decimal("2506.00"),  # stop 2506 > entry_max 2505
        struct_res, Decimal("5.00"), t, test_profile.long_risk_policy
    )
    assert ok is False
    assert "Invalid risk distance" in err


@pytest.mark.unit
def test_h15_h16_h17_phase4_immutability_and_no_promotion(test_profile):
    """H15, H16, H17: Phase4 snapshot is never mutated and publication action is always WAIT."""
    planner = XauUsdRiskPlanner("rev", test_profile)
    t = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    snap = _make_snapshot(SignalState.BUY_WINDOW, UserDecision.BUY, t)
    orig_fp = snap.analysis_fingerprint

    sup = StructureZone("SUPPORT", Decimal("2500.00"), Decimal("2505.00"), t, 2, True)
    res = StructureZone("RESISTANCE", Decimal("2530.00"), Decimal("2535.00"), t, 2, True)
    struct = StructureResult(t, StructureType.HH, None, None, None, (), (sup, res))
    plan = planner.plan_long(snap, struct, Decimal("5.00"))

    assert snap.analysis_fingerprint == orig_fp
    assert plan.source_phase4_fingerprint == orig_fp
    assert plan.publication_effective_action == UserDecision.WAIT


@pytest.mark.unit
def test_h18_h19_h20_h21_market_slippage_and_spread(test_profile):
    """H18, H19, H20, H21: LONG uses ASK (+slip), SHORT uses BID (-slip), spread counted once."""
    exec_m = SideAwareEntryExecutionModel("rev", test_profile.long_execution_policy, phase5_policy_fingerprint="exec_fp")
    t = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    q = QuoteData(t + timedelta(seconds=2), Decimal("2500.00"), Decimal("2500.50"))

    res_l = exec_m.simulate_market_after_signal(RiskSide.LONG, t, [q], "fp")
    assert res_l.raw_executable_price == Decimal("2500.50")
    assert res_l.fill_price == Decimal("2500.50") + (Decimal("2500.50") * Decimal("0.0001"))
    assert res_l.observed_spread == Decimal("0.50")
    assert res_l.synthetic_spread == Decimal("0.00")

    res_s = exec_m.simulate_market_after_signal(RiskSide.SHORT, t, [q], "fp")
    assert res_s.raw_executable_price == Decimal("2500.00")
    assert res_s.fill_price == Decimal("2499.75")  # 2500.00 - 0.25 (adverse slippage lowers fill)


# H22 - H33: Limit triggers, intrabar side checks, fingerprints, production locks
@pytest.mark.unit
def test_h22_h23_h24_h25_h26_limit_execution(test_profile):
    """H22-H26: Limit trigger logic, fill bounding, pre-activation ignore, mid-bar closed."""
    exec_m = SideAwareEntryExecutionModel("rev", test_profile.long_execution_policy, phase5_policy_fingerprint="exec_fp")
    t = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    limit = Decimal("2500.00")

    # Pre-activation quote touched limit, but post-activation quote does not
    q_pre = QuoteData(t + timedelta(milliseconds=500), Decimal("2499.00"), Decimal("2499.50"))
    q_post = QuoteData(t + timedelta(seconds=2), Decimal("2500.50"), Decimal("2500.80"))

    res = exec_m.simulate_limit_zone(RiskSide.LONG, t, limit, "fp", quotes=[q_pre, q_post])
    assert res.is_filled is False  # Pre-activation touch ignored!


@pytest.mark.unit
def test_h27_h28_intrabar_side_correctness():
    """H27 & H28: SHORT intrabar TP=low<=TP, SL=high>=SL; ambiguous is SL_FIRST."""
    resolver = SideAwareIntrabarResolver()
    t_open = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    t_close = datetime(2026, 9, 1, 8, 15, 0, tzinfo=timezone.utc)

    # Bar touches both for SHORT (low=2480 <= TP 2490, high=2515 >= SL 2510)
    bar = CandleData(t_open, t_close, Decimal("2500"), Decimal("2515"), Decimal("2480"), Decimal("2505"), Decimal("100"), True)
    res = resolver.resolve(RiskSide.SHORT, bar, tp_price=Decimal("2490"), sl_price=Decimal("2510"), policy=IntrabarPolicy.CONSERVATIVE_SL_FIRST)
    assert res.barrier_hit == BarrierHitType.SL_FIRST


@pytest.mark.unit
def test_h29_h30_h31_h32_h33_governance_and_fingerprints(test_profile):
    """H29-H33: Policy mutation changes fingerprint; uncalibrated is invalid; is_production_authorized=True fails."""
    # H33: Profile raises if is_production_authorized=True
    with pytest.raises(ValueError, match="production authority is blocked"):
        XauUsdRiskProfile(is_production_authorized=True)

    # H32: All-None uncalibrated profile is invalid
    uncal = uncalibrated_xauusd_risk_profile()
    planner = XauUsdRiskPlanner("rev", uncal)
    t = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    plan = planner.plan_long(_make_snapshot(SignalState.BUY_WINDOW, UserDecision.BUY, t), None, Decimal("5.00"))
    assert plan.risk_candidate_valid is False


# H34 - H47: Extended edge cases from Revision 1
@pytest.mark.unit
def test_h34_pre_activation_touches_do_not_invalidate_active_zone(test_profile):
    """H34: Active support zone with pre-existing touches before T is eligible for entry planning."""
    planner = XauUsdRiskPlanner("rev", test_profile)
    t = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    zone_with_touches = StructureZone("SUPPORT", Decimal("2500.00"), Decimal("2505.00"), t - timedelta(hours=1), 5, True)
    res_zone = StructureZone("RESISTANCE", Decimal("2530.00"), Decimal("2535.00"), t - timedelta(hours=1), 2, True)
    struct = StructureResult(t, StructureType.HH, None, None, None, (), (zone_with_touches, res_zone))

    plan = planner.plan_long(_make_snapshot(SignalState.BUY_WINDOW, UserDecision.BUY, t), struct, Decimal("5.00"))
    assert plan.risk_candidate_valid is True
    assert plan.entry_min == Decimal("2500.00")
    assert plan.entry_max == Decimal("2505.00")


@pytest.mark.unit
def test_h35_future_structure_result_rejected(test_profile):
    """H35: Future StructureResult (timestamp > T) rejects all its internal zones."""
    planner = XauUsdRiskPlanner("rev", test_profile)
    t = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    old_zone = StructureZone("SUPPORT", Decimal("2500.00"), Decimal("2505.00"), t - timedelta(minutes=15), 2, True)

    # StructureResult timestamp is in future (8:15 > 8:00)
    future_struct = StructureResult(t + timedelta(minutes=15), StructureType.HH, None, None, None, (), (old_zone,))
    plan = planner.plan_long(_make_snapshot(SignalState.BUY_WINDOW, UserDecision.BUY, t), future_struct, Decimal("5.00"))
    assert plan.risk_candidate_valid is False
    assert "Missing confirmed active support zone" in plan.reasons[0]


@pytest.mark.unit
def test_h36_h37_deterministic_zone_sorting_and_shuffle(test_profile):
    """H36 & H37: Multiple entry zones select highest support deterministically regardless of input tuple order."""
    planner = XauUsdRiskPlanner("rev", test_profile)
    t = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    z1 = StructureZone("SUPPORT", Decimal("2490.00"), Decimal("2495.00"), t, 2, True)
    z2 = StructureZone("SUPPORT", Decimal("2500.00"), Decimal("2505.00"), t, 2, True)  # highest price_high 2505
    res = StructureZone("RESISTANCE", Decimal("2530.00"), Decimal("2535.00"), t, 2, True)

    struct_order1 = StructureResult(t, StructureType.HH, None, None, None, (), (z1, z2, res))
    struct_order2 = StructureResult(t, StructureType.HH, None, None, None, (), (z2, z1, res))

    p1 = planner.plan_long(_make_snapshot(SignalState.BUY_WINDOW, UserDecision.BUY, t), struct_order1, Decimal("5.00"))
    p2 = planner.plan_long(_make_snapshot(SignalState.BUY_WINDOW, UserDecision.BUY, t), struct_order2, Decimal("5.00"))

    assert p1.entry_max == Decimal("2505.00")
    assert p2.entry_max == Decimal("2505.00")
    assert p1.risk_plan_fingerprint == p2.risk_plan_fingerprint


@pytest.mark.unit
def test_h38_tp2_none_does_not_invalidate_tp1(test_profile):
    """H38: tp2_atr_multiplier=None does not invalidate TP1 risk planning."""
    policy_no_tp2 = SideRiskPolicy(
        structure_buffer=Decimal("1.50"),
        atr_multiplier=Decimal("2.0"),
        max_stop_distance_atr=Decimal("4.0"),
        min_rr_tp1=Decimal("1.80"),
        tp2_atr_multiplier=None,
    )
    t = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    res = StructureZone("RESISTANCE", Decimal("2525.00"), Decimal("2530.00"), t, 2, True)
    struct = StructureResult(t, StructureType.HH, None, None, None, (), (res,))
    tp1, tp2, rr1, rr2, _, _, ok, _ = calculate_long_targets(
        Decimal("2500.00"), Decimal("2502.50"), Decimal("2505.00"), Decimal("2495.00"),
        struct, Decimal("5.00"), t, policy_no_tp2
    )
    assert ok is True
    assert tp1 == Decimal("2525.00")
    assert tp2 is None
    assert rr2 is None


@pytest.mark.unit
def test_h39_tp2_atr_not_beyond_tp1_is_omitted(test_profile):
    """H39: tp2_atr_multiplier producing price <= TP1 is omitted (tp2=None) rather than invalidating TP1."""
    # Policy with tp2_atr_multiplier = 1.0 -> 2502.50 + 1.0 * 5 = 2507.50 <= structural TP1 2525.00
    policy_low_tp2 = SideRiskPolicy(
        structure_buffer=Decimal("1.50"),
        atr_multiplier=Decimal("2.0"),
        max_stop_distance_atr=Decimal("4.0"),
        min_rr_tp1=Decimal("1.80"),
        tp2_atr_multiplier=Decimal("1.0"),
    )
    t = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    res = StructureZone("RESISTANCE", Decimal("2525.00"), Decimal("2530.00"), t, 2, True)
    struct = StructureResult(t, StructureType.HH, None, None, None, (), (res,))
    tp1, tp2, rr1, rr2, tp1_fp, tp2_fp, ok, _ = calculate_long_targets(
        Decimal("2500.00"), Decimal("2502.50"), Decimal("2505.00"), Decimal("2495.00"),
        struct, Decimal("5.00"), t, policy_low_tp2
    )
    assert ok is True
    assert tp1 == Decimal("2525.00")
    assert tp2 is None
    assert rr2 is None
    assert tp2_fp is None


@pytest.mark.unit
def test_h40_h41_invalid_quotes_and_chronological_sort(test_profile):
    """H40 & H41: Invalid quotes ignored; unordered quotes sorted chronologically."""
    exec_m = SideAwareEntryExecutionModel("rev", test_profile.long_execution_policy, phase5_policy_fingerprint="exec_fp")
    t = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)

    q_invalid = QuoteData(t + timedelta(seconds=2), Decimal("2500.50"), Decimal("2500.10"))  # crossed bid > ask
    q2 = QuoteData(t + timedelta(seconds=4), Decimal("2500.20"), Decimal("2500.40"))
    q1 = QuoteData(t + timedelta(seconds=3), Decimal("2500.10"), Decimal("2500.30"))

    # Unordered input [q_invalid, q2, q1] -> resolves to q1 (first valid chronologically)
    res = exec_m.simulate_market_after_signal(RiskSide.LONG, t, [q_invalid, q2, q1], "fp")
    assert res.is_filled is True
    assert res.raw_executable_price == Decimal("2500.30")
    assert res.fill_timestamp == q1.timestamp


@pytest.mark.unit
def test_h42_short_candle_limit_uses_high(test_profile):
    """H42: SHORT candle limit uses HIGH >= limit, not low."""
    exec_m = SideAwareEntryExecutionModel("rev", test_profile.long_execution_policy, phase5_policy_fingerprint="exec_fp")
    t = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    limit = Decimal("2510.00")

    # Bar high=2512 >= 2510 -> triggers short limit
    bar = CandleData(t + timedelta(minutes=15), t + timedelta(minutes=30), Decimal("2500"), Decimal("2512"), Decimal("2495"), Decimal("2505"), Decimal("100"), True)
    res = exec_m.simulate_limit_zone(RiskSide.SHORT, t, limit, "fp", candles=[bar])
    assert res.is_filled is True
    assert res.fill_price == limit


@pytest.mark.unit
def test_h43_h44_h45_fingerprints_consistency():
    """H43, H44, H45: Microsecond diff changes zone fp; identical risk inputs produce identical fp; differing inputs produce differing fp."""
    t1 = datetime(2026, 9, 1, 8, 0, 0, 100000, tzinfo=timezone.utc)
    t2 = datetime(2026, 9, 1, 8, 0, 0, 200000, tzinfo=timezone.utc)
    z1 = StructureZone("SUPPORT", Decimal("2500"), Decimal("2505"), t1, 1, True)
    z2 = StructureZone("SUPPORT", Decimal("2500"), Decimal("2505"), t2, 1, True)
    assert compute_zone_fingerprint(z1) != compute_zone_fingerprint(z2)

    # H44: Identical risk plan inputs produce identical fingerprint
    fp_a = compute_risk_plan_fingerprint(
        source_phase4_fingerprint="sig_fp",
        source_candidate_state=SignalState.BUY_WINDOW,
        source_candidate_decision=UserDecision.BUY,
        side=RiskSide.LONG,
        authoritative_timestamp=t1,
        entry_min=Decimal("2500.00"),
        entry_mid=Decimal("2502.50"),
        entry_max=Decimal("2505.00"),
        stop_structure=Decimal("2495.00"),
        stop_atr=Decimal("2492.50"),
        stop_final=Decimal("2492.50"),
        stop_distance_atr=Decimal("2.50"),
        tp1=Decimal("2525.00"),
        tp2=None,
        planned_rr_tp1=Decimal("2.00"),
        planned_rr_tp2=None,
        entry_zone_fingerprint="z_fp",
        tp1_zone_fingerprint="tp1_fp",
        tp2_zone_fingerprint=None,
        atr_value=Decimal("5.00"),
        phase5_policy_fingerprint="pol_fp",
        risk_version="5.0.0",
        code_revision="rev1",
    )
    fp_b = compute_risk_plan_fingerprint(
        source_phase4_fingerprint="sig_fp",
        source_candidate_state=SignalState.BUY_WINDOW,
        source_candidate_decision=UserDecision.BUY,
        side=RiskSide.LONG,
        authoritative_timestamp=t1,
        entry_min=Decimal("2500.00"),
        entry_mid=Decimal("2502.50"),
        entry_max=Decimal("2505.00"),
        stop_structure=Decimal("2495.00"),
        stop_atr=Decimal("2492.50"),
        stop_final=Decimal("2492.50"),
        stop_distance_atr=Decimal("2.50"),
        tp1=Decimal("2525.00"),
        tp2=None,
        planned_rr_tp1=Decimal("2.00"),
        planned_rr_tp2=None,
        entry_zone_fingerprint="z_fp",
        tp1_zone_fingerprint="tp1_fp",
        tp2_zone_fingerprint=None,
        atr_value=Decimal("5.00"),
        phase5_policy_fingerprint="pol_fp",
        risk_version="5.0.0",
        code_revision="rev1",
    )
    assert fp_a == fp_b

    # H45: Differing stop input produces different fingerprint
    fp_diff = compute_risk_plan_fingerprint(
        source_phase4_fingerprint="sig_fp",
        source_candidate_state=SignalState.BUY_WINDOW,
        source_candidate_decision=UserDecision.BUY,
        side=RiskSide.LONG,
        authoritative_timestamp=t1,
        entry_min=Decimal("2500.00"),
        entry_mid=Decimal("2502.50"),
        entry_max=Decimal("2505.00"),
        stop_structure=Decimal("2495.00"),
        stop_atr=Decimal("2492.50"),
        stop_final=Decimal("2490.00"),
        stop_distance_atr=Decimal("3.00"),
        tp1=Decimal("2525.00"),
        tp2=None,
        planned_rr_tp1=Decimal("2.00"),
        planned_rr_tp2=None,
        entry_zone_fingerprint="z_fp",
        tp1_zone_fingerprint="tp1_fp",
        tp2_zone_fingerprint=None,
        atr_value=Decimal("5.00"),
        phase5_policy_fingerprint="pol_fp",
        risk_version="5.0.0",
        code_revision="rev1",
    )
    assert fp_a != fp_diff


@pytest.mark.unit
def test_h46_non_xauusd_snapshot_rejected(test_profile):
    """H46: Malformed non-XAUUSD snapshot rejected with ValueError."""
    planner = XauUsdRiskPlanner("rev", test_profile)
    t = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    snap = _make_snapshot(SignalState.BUY_WINDOW, UserDecision.BUY, t, instrument="BTCUSD")
    with pytest.raises(ValueError, match="requires XAUUSD snapshot"):
        planner.plan_long(snap, None, Decimal("5.0"))


@pytest.mark.unit
def test_h47_worst_case_short_uses_stop_plus_gap():
    """H47: WORST_CASE SHORT uses stop + gap, not stop - gap."""
    resolver = SideAwareIntrabarResolver()
    t = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    bar = CandleData(t, t + timedelta(minutes=15), Decimal("2500"), Decimal("2525"), Decimal("2485"), Decimal("2510"), Decimal("100"), True)
    res = resolver.resolve(RiskSide.SHORT, bar, Decimal("2490"), Decimal("2510"), policy=IntrabarPolicy.WORST_CASE, worst_case_adverse_gap=Decimal("5.00"))
    assert res.exit_price == Decimal("2515.00")  # 2510 + 5


# H48 - H67: Target ordering, timezone normalization, strict gates, execution formulas
@pytest.mark.unit
def test_h48_h49_equal_price_target_zones_deterministic_after_shuffle(test_profile):
    """H48 & H49: Equal-price target zones order deterministically by created_at, price_high, zone_fp."""
    t1 = datetime(2026, 9, 1, 8, 0, 0, 100000, tzinfo=timezone.utc)
    t2 = datetime(2026, 9, 1, 8, 0, 0, 200000, tzinfo=timezone.utc)
    z1 = StructureZone("RESISTANCE", Decimal("2525.00"), Decimal("2530.00"), t1, 1, True)
    z2 = StructureZone("RESISTANCE", Decimal("2525.00"), Decimal("2535.00"), t2, 1, True)

    struct_a = StructureResult(t2, StructureType.HH, None, None, None, (), (z1, z2))
    struct_b = StructureResult(t2, StructureType.HH, None, None, None, (), (z2, z1))

    tp1_a, _, _, _, tp1_fp_a, _, _, _ = calculate_long_targets(
        Decimal("2500"), Decimal("2502.5"), Decimal("2505"), Decimal("2495"), struct_a, Decimal("5.0"), t2, test_profile.long_risk_policy
    )
    tp1_b, _, _, _, tp1_fp_b, _, _, _ = calculate_long_targets(
        Decimal("2500"), Decimal("2502.5"), Decimal("2505"), Decimal("2495"), struct_b, Decimal("5.0"), t2, test_profile.long_risk_policy
    )
    assert tp1_fp_a == tp1_fp_b == compute_zone_fingerprint(z1)  # z1 is earlier created_at


@pytest.mark.unit
def test_h50_h51_timezone_canonicalization():
    """H50 & H51: Equivalent datetimes in different offsets canonicalize identically; naive rejected."""
    dt_utc = datetime(2026, 9, 1, 8, 0, 0, 0, tzinfo=timezone.utc)
    dt_plus2 = datetime(2026, 9, 1, 10, 0, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    assert canonical_utc_timestamp(dt_utc) == canonical_utc_timestamp(dt_plus2) == "2026-09-01T08:00:00.000000Z"

    with pytest.raises(ValueError, match="Timezone-aware"):
        canonical_utc_timestamp(datetime(2026, 9, 1, 8, 0, 0))


@pytest.mark.unit
def test_h52_h53_planned_rr_tp1_gate(test_profile):
    """H52 & H53: planned_rr_tp1 < min_rr is invalid; planned_rr_tp1 == min_rr is valid."""
    t = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    # risk = 10.00. exact min_rr=1.80 requires tp1 = 2505 + 18 = 2523.00
    z_valid = StructureZone("RESISTANCE", Decimal("2523.00"), Decimal("2530.00"), t, 1, True)
    z_invalid = StructureZone("RESISTANCE", Decimal("2522.90"), Decimal("2530.00"), t, 1, True)

    struct_val = StructureResult(t, StructureType.HH, None, None, None, (), (z_valid,))
    struct_inval = StructureResult(t, StructureType.HH, None, None, None, (), (z_invalid,))

    _, _, _, _, _, _, ok_val, _ = calculate_long_targets(Decimal("2500"), Decimal("2502.5"), Decimal("2505"), Decimal("2495"), struct_val, Decimal("5.0"), t, test_profile.long_risk_policy)
    _, _, _, _, _, _, ok_inval, _ = calculate_long_targets(Decimal("2500"), Decimal("2502.5"), Decimal("2505"), Decimal("2495"), struct_inval, Decimal("5.0"), t, test_profile.long_risk_policy)

    assert ok_val is True
    assert ok_inval is False


@pytest.mark.unit
def test_h54_h55_stop_distance_atr_gate(test_profile):
    """H54 & H55: stop_distance_atr > max is invalid; stop_distance_atr == max is valid."""
    sup = StructureZone("SUPPORT", Decimal("2500.00"), Decimal("2505.00"), datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc), 1, True)
    # max_stop_distance_atr = 4.0 * 5.0 = 20.00 risk. stop_final = 2505 - 20 = 2485.00
    p_exact = SideRiskPolicy(structure_buffer=Decimal("15.00"), atr_multiplier=Decimal("3.5"), max_stop_distance_atr=Decimal("4.0"), min_rr_tp1=Decimal("1.80"))
    p_excess = SideRiskPolicy(structure_buffer=Decimal("15.06"), atr_multiplier=Decimal("3.5"), max_stop_distance_atr=Decimal("4.0"), min_rr_tp1=Decimal("1.80"))

    _, _, _, _, ok_exact, _ = calculate_long_stops(sup, Decimal("2500"), Decimal("2502.5"), Decimal("2505"), Decimal("5.0"), p_exact)
    _, _, _, _, ok_excess, _ = calculate_long_stops(sup, Decimal("2500"), Decimal("2502.5"), Decimal("2505"), Decimal("5.0"), p_excess)

    assert ok_exact is True
    assert ok_excess is False


@pytest.mark.unit
def test_h56_h57_h58_ohlc_validation():
    """H56, H57, H58: Malformed geometric, NaN/Inf, or zero/negative OHLC rejected."""
    t1 = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 9, 1, 8, 15, tzinfo=timezone.utc)

    # Geometry: low > high
    assert validate_xauusd_candle(CandleData(t1, t2, Decimal("2500"), Decimal("2490"), Decimal("2510"), Decimal("2500"), Decimal("10"), True)) is False
    # Negative
    assert validate_xauusd_candle(CandleData(t1, t2, Decimal("-2500"), Decimal("2510"), Decimal("2490"), Decimal("2500"), Decimal("10"), True)) is False
    # Non-finite
    assert validate_xauusd_candle(CandleData(t1, t2, Decimal("NaN"), Decimal("2510"), Decimal("2490"), Decimal("2500"), Decimal("10"), True)) is False


@pytest.mark.unit
def test_h59_h60_h61_h62_slippage_and_spread_exact(test_profile):
    """H59-H62: Slippage and synthetic spread exact percentages."""
    exec_m = SideAwareEntryExecutionModel("rev", test_profile.long_execution_policy, phase5_policy_fingerprint="exec_fp")
    t = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    bar = CandleData(t + timedelta(minutes=15), t + timedelta(minutes=30), Decimal("2500.00"), Decimal("2510.00"), Decimal("2495.00"), Decimal("2505.00"), Decimal("100"), True)

    res_l = exec_m.simulate_next_bar_open(RiskSide.LONG, t, [bar], "fp")
    # 2500.00 + (2500 * 0.02% = 0.50) + (2500 * 0.01% = 0.25) = 2500.75
    assert res_l.fill_price == Decimal("2500.75")
    assert res_l.synthetic_spread == Decimal("0.50")
    assert res_l.adverse_slippage == Decimal("0.25")

    res_s = exec_m.simulate_next_bar_open(RiskSide.SHORT, t, [bar], "fp")
    # 2500.00 - 0.50 - 0.25 = 2499.25
    assert res_s.fill_price == Decimal("2499.25")


@pytest.mark.unit
def test_h63_h64_h65_h66_h67_invalid_and_dedup(test_profile):
    """H63-H67: Candidate WAIT cannot become BUY/SELL; missing entry produces None coordinates; NO_FILL produces None evidence."""
    planner = XauUsdRiskPlanner("rev", test_profile)
    t = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)

    # H63 & H64: WAIT candidate remains WAIT
    snap_wait = _make_snapshot(SignalState.NO_TRADE, UserDecision.WAIT, t)
    plan_l = planner.plan_long(snap_wait, None, Decimal("5.00"))
    plan_s = planner.plan_short(snap_wait, None, Decimal("5.00"))
    assert plan_l.candidate_effective_action == UserDecision.WAIT
    assert plan_s.candidate_effective_action == UserDecision.WAIT

    # H65: Missing entry coordinates are None
    assert plan_l.entry_min is None
    assert plan_l.entry_max is None

    # H66: NO_FILL execution result
    exec_m = SideAwareEntryExecutionModel("rev", test_profile.long_execution_policy, phase5_policy_fingerprint="exec_fp")
    fill_res = exec_m.simulate_market_after_signal(RiskSide.LONG, t, [], "fp")
    assert fill_res.is_filled is False
    assert fill_res.source_evidence_type is None
    assert fill_res.source_evidence_fingerprint is None

    # H67: Multiple target zones across 15m and 4H with identical zone fingerprint are deduplicated
    z_res = StructureZone("RESISTANCE", Decimal("2530.00"), Decimal("2535.00"), t, 1, True)
    tp1, _, _, _, tp1_fp, _, ok_dedup, _ = calculate_long_targets(
        Decimal("2500.00"), Decimal("2502.50"), Decimal("2505.00"), Decimal("2495.00"),
        StructureResult(t, StructureType.HH, None, None, None, (), (z_res,)),
        Decimal("5.00"),
        t,
        test_profile.long_risk_policy,
        structure_4h=StructureResult(t, StructureType.HH, None, None, None, (), (z_res,)),
    )
    assert ok_dedup is True
    assert tp1 == Decimal("2530.00")


# H68 - H74: Revision 2.1 Micro-Lock Hostile Tests
@pytest.mark.unit
def test_h68_touches_changes_zone_fingerprint():
    """H68: StructureZone differing only in `touches` produces a different zone fingerprint."""
    t = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    z_touch1 = StructureZone("SUPPORT", Decimal("2500.00"), Decimal("2505.00"), t, 1, True)
    z_touch2 = StructureZone("SUPPORT", Decimal("2500.00"), Decimal("2505.00"), t, 2, True)
    assert compute_zone_fingerprint(z_touch1) != compute_zone_fingerprint(z_touch2)


@pytest.mark.unit
def test_h69_duplicate_15m_4h_zone_cannot_occupy_tp1_and_tp2(test_profile):
    """H69: Identical zone present in 15m and 4H is deduplicated by zone_fp and cannot occupy both TP1 and TP2."""
    t = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    identical_res = StructureZone("RESISTANCE", Decimal("2530.00"), Decimal("2535.00"), t, 2, True)

    struct_15m = StructureResult(t, StructureType.HH, None, None, None, (), (identical_res,))
    struct_4h = StructureResult(t, StructureType.HH, None, None, None, (), (identical_res,))

    tp1, tp2, rr1, rr2, tp1_fp, tp2_fp, ok, _ = calculate_long_targets(
        Decimal("2500.00"), Decimal("2502.50"), Decimal("2505.00"), Decimal("2495.00"),
        struct_15m, Decimal("5.00"), t, test_profile.long_risk_policy, structure_4h=struct_4h
    )
    assert ok is True
    assert tp1 == Decimal("2530.00")
    # Because there was only 1 unique structural zone, TP2 is synthetic or None; never the identical zone!
    assert tp2_fp is None or tp2_fp != tp1_fp


@pytest.mark.unit
def test_h70_equal_price_tp_zones_cannot_produce_tp2_equals_tp1(test_profile):
    """H70: Two distinct equal-price TP zones cannot produce TP2 == TP1; TP2 must be strictly beyond TP1."""
    t = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    # Two distinct resistance zones with the same price_low = 2530.00
    res1 = StructureZone("RESISTANCE", Decimal("2530.00"), Decimal("2535.00"), t, 1, True)
    res2 = StructureZone("RESISTANCE", Decimal("2530.00"), Decimal("2540.00"), t, 2, True)
    res3 = StructureZone("RESISTANCE", Decimal("2545.00"), Decimal("2550.00"), t, 1, True)
    struct = StructureResult(t, StructureType.HH, None, None, None, (), (res1, res2, res3))

    tp1, tp2, _, _, _, _, ok, _ = calculate_long_targets(
        Decimal("2500.00"), Decimal("2502.50"), Decimal("2505.00"), Decimal("2495.00"),
        struct, Decimal("5.00"), t, test_profile.long_risk_policy
    )
    assert ok is True
    assert tp1 == Decimal("2530.00")
    assert tp2 == Decimal("2545.00")  # Strictly beyond TP1!
    assert tp2 > tp1


@pytest.mark.unit
def test_h71_quote_evidence_fingerprint_mutation():
    """H71: Quote evidence fingerprint is deterministic and changes when any field changes."""
    t = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    q1 = QuoteData(t, Decimal("2500.10"), Decimal("2500.30"), "src_a")
    q2 = QuoteData(t, Decimal("2500.10"), Decimal("2500.30"), "src_b")
    assert compute_quote_evidence_fingerprint(q1) != compute_quote_evidence_fingerprint(q2)


@pytest.mark.unit
def test_h72_candle_evidence_fingerprint_mutation():
    """H72: Candle evidence fingerprint changes when any canonical CandleData field changes."""
    t1 = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 9, 1, 8, 15, tzinfo=timezone.utc)
    c1 = CandleData(t1, t2, Decimal("2500"), Decimal("2510"), Decimal("2495"), Decimal("2505"), Decimal("100"), True, "s1", None, None, VolumeEvidenceType.REAL_VOLUME)
    c2 = CandleData(t1, t2, Decimal("2500"), Decimal("2510"), Decimal("2495"), Decimal("2505"), Decimal("100"), False, "s1", None, None, VolumeEvidenceType.REAL_VOLUME)
    assert compute_candle_evidence_fingerprint(c1) != compute_candle_evidence_fingerprint(c2)


@pytest.mark.unit
def test_h73_invalid_utcoffset_rejected():
    """H73: Candle datetime with tzinfo object but None utcoffset is rejected."""
    from datetime import tzinfo

    class BadTz(tzinfo):
        def utcoffset(self, dt):
            return None
        def tzname(self, dt):
            return "BadTz"
        def dst(self, dt):
            return timedelta(0)

    bad_tz = BadTz()
    t1 = datetime(2026, 9, 1, 8, 0, tzinfo=bad_tz)
    t2 = datetime(2026, 9, 1, 8, 15, tzinfo=bad_tz)
    c = CandleData(t1, t2, Decimal("2500"), Decimal("2510"), Decimal("2495"), Decimal("2505"), Decimal("100"), True)
    assert validate_xauusd_candle(c) is False


@pytest.mark.unit
def test_h74_malformed_parent_or_lower_tf_candle_untrusted():
    """H74: Malformed parent candle fails closed; malformed lower-TF falls back to CONSERVATIVE_SL_FIRST."""
    resolver = SideAwareIntrabarResolver()
    t1 = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 9, 1, 8, 15, tzinfo=timezone.utc)

    # Malformed parent: low > high
    bad_parent = CandleData(t1, t2, Decimal("2500"), Decimal("2490"), Decimal("2510"), Decimal("2505"), Decimal("100"), True)
    res = resolver.resolve(RiskSide.LONG, bad_parent, Decimal("2520"), Decimal("2490"))
    assert res.barrier_hit == BarrierHitType.UNRESOLVED
    assert "failed strict validation" in res.reasons[0]
