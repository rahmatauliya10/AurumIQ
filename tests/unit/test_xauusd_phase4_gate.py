"""
Unit tests for Phase 4 XAUUSD Safety Gate & Layer A Candidate Conflict Matrix.
Covers Task 5 contracts.
"""
import pytest

from engine.core.types import (
    CandidateGateResult,
    FeedCriticality,
    FeedHealthStatus,
    RuntimeFeedHealth,
    SideDirectionScoreResult,
    SideTimingScoreResult,
    SignalSide,
    SignalState,
    UserDecision,
    XauUsdHardGateEvaluation,
)
from engine.signals.gate import (
    evaluate_hard_gates,
    evaluate_selective_gate,
    evaluate_xauusd_candidate_gate,
    evaluate_xauusd_hard_gates,
)
from engine.signals.profile import (
    Phase4CalibrationStatus,
    Phase4FeedPolicy,
    Phase4SignalProfile,
    SideDirectionPolicy,
    SideGatePolicy,
    SideTimingPolicy,
)


def _make_test_profile() -> Phase4SignalProfile:
    return Phase4SignalProfile(
        target_instrument="XAUUSD",
        calibration_status=Phase4CalibrationStatus.CANDIDATE_NOT_FROZEN,
        long_direction=SideDirectionPolicy(15.0, 10.0, 10.0, 10.0, 20.0, 15.0, 10.0, 10.0),
        short_direction=SideDirectionPolicy(15.0, 10.0, 10.0, 10.0, 20.0, 15.0, 10.0, 10.0),
        long_timing=SideTimingPolicy(25.0, 25.0, 20.0, 20.0, 10.0),
        short_timing=SideTimingPolicy(25.0, 25.0, 20.0, 20.0, 10.0),
        long_gate=SideGatePolicy(
            threshold_watch_direction=70.0,
            threshold_ready_direction=75.0,
            threshold_ready_timing=70.0,
            threshold_window_direction=80.0,
            threshold_window_timing=80.0,
        ),
        short_gate=SideGatePolicy(
            threshold_watch_direction=70.0,
            threshold_ready_direction=75.0,
            threshold_ready_timing=70.0,
            threshold_window_direction=80.0,
            threshold_window_timing=80.0,
        ),
    )


@pytest.mark.unit
def test_hard_gate_generic_feed_health_and_fail_safe_defaults():
    """Verify missing/unknown critical runtime feeds fail closed (FORCE_WAIT)."""
    prof = _make_test_profile()

    # 1. Default (all UNKNOWN) fails closed on primary_15m and macro_blackout
    res_default = evaluate_xauusd_hard_gates(None, prof)
    assert res_default.is_blocked is True
    assert res_default.override_state == SignalState.FORCE_WAIT
    assert len(res_default.block_reasons) >= 2

    # 2. Healthy 15m and macro -> pass
    healthy_rfh = RuntimeFeedHealth(
        primary_15m=FeedHealthStatus.HEALTHY,
        macro_blackout_feed=FeedHealthStatus.HEALTHY,
        is_macro_blackout=False,
    )
    res_healthy = evaluate_xauusd_hard_gates(healthy_rfh, prof)
    assert res_healthy.is_blocked is False
    assert res_healthy.override_state is None

    # 3. Macro blackout active -> blocks
    blackout_rfh = RuntimeFeedHealth(
        primary_15m=FeedHealthStatus.HEALTHY,
        macro_blackout_feed=FeedHealthStatus.HEALTHY,
        is_macro_blackout=True,
    )
    res_blackout = evaluate_xauusd_hard_gates(blackout_rfh, prof)
    assert res_blackout.is_blocked is True
    assert res_blackout.override_state == SignalState.FORCE_WAIT

    # 4. Optional feed missing (e.g. secondary provider) does NOT block
    optional_missing_rfh = RuntimeFeedHealth(
        primary_15m=FeedHealthStatus.HEALTHY,
        macro_blackout_feed=FeedHealthStatus.HEALTHY,
        is_macro_blackout=False,
        secondary_provider=FeedHealthStatus.MISSING,
    )
    res_opt = evaluate_xauusd_hard_gates(optional_missing_rfh, prof)
    assert res_opt.is_blocked is False

    # 5. Primary feed in TRANSITION -> trips FORCE_WAIT
    transition_rfh = RuntimeFeedHealth(
        primary_15m=FeedHealthStatus.TRANSITION,
        macro_blackout_feed=FeedHealthStatus.HEALTHY,
        is_macro_blackout=False,
    )
    res_trans = evaluate_xauusd_hard_gates(transition_rfh, prof)
    assert res_trans.is_blocked is True
    assert res_trans.override_state == SignalState.FORCE_WAIT

    # 6. Custom policy with primary_1h set to CRITICAL -> missing primary_1h blocks
    prof_custom_critical = Phase4SignalProfile(
        target_instrument="XAUUSD",
        calibration_status=Phase4CalibrationStatus.CANDIDATE_NOT_FROZEN,
        feed_policy=Phase4FeedPolicy(
            primary_15m=FeedCriticality.CRITICAL,
            primary_1h=FeedCriticality.CRITICAL,
            macro_blackout=FeedCriticality.CRITICAL,
        ),
    )
    rfh_missing_1h = RuntimeFeedHealth(
        primary_15m=FeedHealthStatus.HEALTHY,
        primary_1h=FeedHealthStatus.MISSING,
        macro_blackout_feed=FeedHealthStatus.HEALTHY,
        is_macro_blackout=False,
    )
    res_crit = evaluate_xauusd_hard_gates(rfh_missing_1h, prof_custom_critical)
    assert res_crit.is_blocked is True
    assert any("primary_1h" in r for r in res_crit.block_reasons)


@pytest.mark.unit
def test_candidate_conflict_matrix_all_combinations():
    """Verify Layer A candidate gate evaluates full 16-row symmetric conflict matrix."""
    prof = _make_test_profile()
    unblocked_gate = XauUsdHardGateEvaluation(
        is_blocked=False,
        override_state=None,
        block_reasons=(),
        runtime_health=RuntimeFeedHealth(primary_15m=FeedHealthStatus.HEALTHY, macro_blackout_feed=FeedHealthStatus.HEALTHY),
    )

    def _eval(l_dir_score, l_tim_score, s_dir_score, s_tim_score) -> CandidateGateResult:
        l_dir = SideDirectionScoreResult(SignalSide.LONG, l_dir_score, 100.0, (), True, l_dir_score >= 70.0)
        s_dir = SideDirectionScoreResult(SignalSide.SHORT, s_dir_score, 100.0, (), True, s_dir_score >= 70.0)
        l_tim = SideTimingScoreResult(SignalSide.LONG, l_tim_score, 100.0, (), True, l_tim_score >= 70.0)
        s_tim = SideTimingScoreResult(SignalSide.SHORT, s_tim_score, 100.0, (), True, s_tim_score >= 70.0)
        return evaluate_xauusd_candidate_gate(l_dir, s_dir, l_tim, s_tim, unblocked_gate, prof)

    # 1. (BUY_WINDOW, NO_TRADE) -> BUY_WINDOW / BUY [XAU-P4-01]
    res1 = _eval(85.0, 85.0, 20.0, 10.0)
    assert res1.candidate_state == SignalState.BUY_WINDOW
    assert res1.candidate_user_decision == UserDecision.BUY

    # 2. (NO_TRADE, SELL_WINDOW) -> SELL_WINDOW / SELL [XAU-P4-02]
    res2 = _eval(20.0, 10.0, 85.0, 85.0)
    assert res2.candidate_state == SignalState.SELL_WINDOW
    assert res2.candidate_user_decision == UserDecision.SELL

    # 3. (BUY_WINDOW, SELL_WINDOW) -> CONFLICT / WAIT [XAU-P4-03]
    res3 = _eval(85.0, 85.0, 85.0, 85.0)
    assert res3.candidate_state == SignalState.CONFLICT
    assert res3.candidate_user_decision == UserDecision.WAIT

    # 4. (BUY_WINDOW, READY_SHORT) -> CONFLICT / WAIT
    res4 = _eval(85.0, 85.0, 76.0, 72.0)
    assert res4.candidate_state == SignalState.CONFLICT
    assert res4.candidate_user_decision == UserDecision.WAIT

    # 5. (BUY_WINDOW, WATCH_SHORT) -> BUY_WINDOW / BUY
    res5 = _eval(85.0, 85.0, 72.0, 50.0)
    assert res5.candidate_state == SignalState.BUY_WINDOW
    assert res5.candidate_user_decision == UserDecision.BUY

    # 6. (READY_LONG, SELL_WINDOW) -> CONFLICT / WAIT
    res6 = _eval(76.0, 72.0, 85.0, 85.0)
    assert res6.candidate_state == SignalState.CONFLICT
    assert res6.candidate_user_decision == UserDecision.WAIT

    # 7. (READY_LONG, READY_SHORT) -> CONFLICT / WAIT
    res7 = _eval(76.0, 72.0, 76.0, 72.0)
    assert res7.candidate_state == SignalState.CONFLICT
    assert res7.candidate_user_decision == UserDecision.WAIT

    # 8. (READY_LONG, WATCH_SHORT) -> READY_LONG / WAIT
    res8 = _eval(76.0, 72.0, 72.0, 50.0)
    assert res8.candidate_state == SignalState.READY_LONG
    assert res8.candidate_user_decision == UserDecision.WAIT

    # 9. (READY_LONG, NO_TRADE) -> READY_LONG / WAIT
    res9 = _eval(76.0, 72.0, 30.0, 20.0)
    assert res9.candidate_state == SignalState.READY_LONG
    assert res9.candidate_user_decision == UserDecision.WAIT

    # 10. (WATCH_LONG, SELL_WINDOW) -> SELL_WINDOW / SELL
    res10 = _eval(72.0, 50.0, 85.0, 85.0)
    assert res10.candidate_state == SignalState.SELL_WINDOW
    assert res10.candidate_user_decision == UserDecision.SELL

    # 11. (WATCH_LONG, READY_SHORT) -> READY_SHORT / WAIT
    res11 = _eval(72.0, 50.0, 76.0, 72.0)
    assert res11.candidate_state == SignalState.READY_SHORT
    assert res11.candidate_user_decision == UserDecision.WAIT

    # 12. (WATCH_LONG, WATCH_SHORT) -> CONFLICT / WAIT
    res12 = _eval(72.0, 50.0, 72.0, 50.0)
    assert res12.candidate_state == SignalState.CONFLICT
    assert res12.candidate_user_decision == UserDecision.WAIT

    # 13. (WATCH_LONG, NO_TRADE) -> WATCH_LONG / WAIT
    res13 = _eval(72.0, 50.0, 20.0, 10.0)
    assert res13.candidate_state == SignalState.WATCH_LONG
    assert res13.candidate_user_decision == UserDecision.WAIT

    # 14. (NO_TRADE, READY_SHORT) -> READY_SHORT / WAIT
    res14 = _eval(20.0, 10.0, 76.0, 72.0)
    assert res14.candidate_state == SignalState.READY_SHORT
    assert res14.candidate_user_decision == UserDecision.WAIT

    # 15. (NO_TRADE, WATCH_SHORT) -> WATCH_SHORT / WAIT
    res15 = _eval(20.0, 10.0, 72.0, 50.0)
    assert res15.candidate_state == SignalState.WATCH_SHORT
    assert res15.candidate_user_decision == UserDecision.WAIT

    # 16. (NO_TRADE, NO_TRADE) -> NO_TRADE / WAIT
    res16 = _eval(20.0, 10.0, 20.0, 10.0)
    assert res16.candidate_state == SignalState.NO_TRADE
    assert res16.candidate_user_decision == UserDecision.WAIT


@pytest.mark.unit
def test_historical_xaut_gate_preserved():
    """Verify historical evaluate_hard_gates and evaluate_selective_gate remain 100% frozen."""
    res_hard = evaluate_hard_gates(
        is_feed_stale=True,
        is_provider_transition=False,
    )
    assert res_hard.is_blocked is True
    assert res_hard.override_state == SignalState.FORCE_WAIT

    from engine.core.types import DirectionScoreResult, TimingScoreResult
    dir_res = DirectionScoreResult(50.0, 100.0, (), False)
    tim_res = TimingScoreResult(50.0, 100.0, (), False)

    state, decision = evaluate_selective_gate(
        direction=dir_res,
        timing=tim_res,
        regime=None,
        structure=None,
        hard_gate=res_hard,
    )
    assert state == SignalState.FORCE_WAIT
    assert decision == UserDecision.WAIT

