"""
Official Acceptance Contract Tests: XAU-P4-01, XAU-P4-02, XAU-P4-03, XAU-P4-04.
Covers Task 9 contracts.
"""
from datetime import datetime, timezone
from decimal import Decimal
import pytest

from engine.core.types import (
    CandleData,
    ComponentScore,
    FeedHealthStatus,
    RuntimeFeedHealth,
    SideDirectionScoreResult,
    SideTimingScoreResult,
    SignalSide,
    SignalState,
    UserDecision,
    XauUsdHardGateEvaluation,
)
from engine.signals.engine import XauUsdSignalEngine
from engine.signals.gate import evaluate_xauusd_candidate_gate, evaluate_xauusd_hard_gates
from engine.signals.profile import (
    Phase4CalibrationStatus,
    Phase4SignalProfile,
    SideDirectionPolicy,
    SideGatePolicy,
    SideTimingPolicy,
)


@pytest.fixture
def candidate_profile():
    return Phase4SignalProfile(
        target_instrument="XAUUSD",
        calibration_status=Phase4CalibrationStatus.CANDIDATE_NOT_FROZEN,
        long_direction=SideDirectionPolicy(15.0, 10.0, 10.0, 10.0, 20.0, 15.0, 10.0, 10.0),
        short_direction=SideDirectionPolicy(15.0, 10.0, 10.0, 10.0, 20.0, 15.0, 10.0, 10.0),
        long_timing=SideTimingPolicy(25.0, 25.0, 20.0, 20.0, 10.0),
        short_timing=SideTimingPolicy(25.0, 25.0, 20.0, 20.0, 10.0),
        long_gate=SideGatePolicy(70.0, 75.0, 70.0, 80.0, 80.0),
        short_gate=SideGatePolicy(70.0, 75.0, 70.0, 80.0, 80.0),
    )


@pytest.mark.unit
def test_contract_xau_p4_01_buy_window_to_buy(candidate_profile):
    """
    Contract XAU-P4-01: BUY_WINDOW -> BUY
    Layer A candidate mechanics yield BUY_WINDOW / BUY.
    Layer B publication authority guard yields NO_TRADE / WAIT while recording candidate state.
    """
    long_dir = SideDirectionScoreResult(SignalSide.LONG, 85.0, 100.0, (ComponentScore("D1", 85.0, 100.0, "Long dir valid"),), True, True)
    short_dir = SideDirectionScoreResult(SignalSide.SHORT, 25.0, 100.0, (ComponentScore("D2", 25.0, 100.0, "Short dir low"),), True, False)
    long_tim = SideTimingScoreResult(SignalSide.LONG, 82.0, 100.0, (ComponentScore("T1", 82.0, 100.0, "Long timing trigger"),), True, True)
    short_tim = SideTimingScoreResult(SignalSide.SHORT, 15.0, 100.0, (ComponentScore("T2", 15.0, 100.0, "Short timing low"),), True, False)

    rfh = RuntimeFeedHealth(
        primary_15m=FeedHealthStatus.HEALTHY,
        primary_1h=FeedHealthStatus.HEALTHY,
        macro_blackout_feed=FeedHealthStatus.HEALTHY,
        is_macro_blackout=False,
    )
    hard_gate = evaluate_xauusd_hard_gates(rfh, candidate_profile)
    assert hard_gate.is_blocked is False

    # Layer A pure deterministic candidate evaluation
    cand_res = evaluate_xauusd_candidate_gate(
        long_direction=long_dir,
        short_direction=short_dir,
        long_timing=long_tim,
        short_timing=short_tim,
        hard_gate=hard_gate,
        profile=candidate_profile,
    )
    assert cand_res.candidate_state == SignalState.BUY_WINDOW
    assert cand_res.candidate_user_decision == UserDecision.BUY
    assert cand_res.resolution_reason == "LONG_QUALIFIED"


@pytest.mark.unit
def test_contract_xau_p4_02_sell_window_to_sell(candidate_profile):
    """
    Contract XAU-P4-02: SELL_WINDOW -> SELL
    Layer A candidate mechanics yield SELL_WINDOW / SELL.
    Layer B publication authority guard yields NO_TRADE / WAIT while recording candidate state.
    """
    long_dir = SideDirectionScoreResult(SignalSide.LONG, 20.0, 100.0, (ComponentScore("D1", 20.0, 100.0, "Long dir low"),), True, False)
    short_dir = SideDirectionScoreResult(SignalSide.SHORT, 86.0, 100.0, (ComponentScore("D2", 86.0, 100.0, "Short dir valid"),), True, True)
    long_tim = SideTimingScoreResult(SignalSide.LONG, 15.0, 100.0, (ComponentScore("T1", 15.0, 100.0, "Long timing low"),), True, False)
    short_tim = SideTimingScoreResult(SignalSide.SHORT, 84.0, 100.0, (ComponentScore("T2", 84.0, 100.0, "Short timing trigger"),), True, True)

    rfh = RuntimeFeedHealth(
        primary_15m=FeedHealthStatus.HEALTHY,
        primary_1h=FeedHealthStatus.HEALTHY,
        macro_blackout_feed=FeedHealthStatus.HEALTHY,
        is_macro_blackout=False,
    )
    hard_gate = evaluate_xauusd_hard_gates(rfh, candidate_profile)
    assert hard_gate.is_blocked is False

    # Layer A pure deterministic candidate evaluation
    cand_res = evaluate_xauusd_candidate_gate(
        long_direction=long_dir,
        short_direction=short_dir,
        long_timing=long_tim,
        short_timing=short_tim,
        hard_gate=hard_gate,
        profile=candidate_profile,
    )
    assert cand_res.candidate_state == SignalState.SELL_WINDOW
    assert cand_res.candidate_user_decision == UserDecision.SELL
    assert cand_res.resolution_reason == "SHORT_QUALIFIED"


@pytest.mark.unit
def test_contract_xau_p4_03_conflict_to_wait(candidate_profile):
    """
    Contract XAU-P4-03: CONFLICT -> WAIT
    When both Long and Short are simultaneously qualified, Layer A resolves CONFLICT -> WAIT.
    """
    long_dir = SideDirectionScoreResult(SignalSide.LONG, 85.0, 100.0, (ComponentScore("D1", 85.0, 100.0, "Long dir valid"),), True, True)
    short_dir = SideDirectionScoreResult(SignalSide.SHORT, 85.0, 100.0, (ComponentScore("D2", 85.0, 100.0, "Short dir valid"),), True, True)
    long_tim = SideTimingScoreResult(SignalSide.LONG, 82.0, 100.0, (ComponentScore("T1", 82.0, 100.0, "Long timing trigger"),), True, True)
    short_tim = SideTimingScoreResult(SignalSide.SHORT, 82.0, 100.0, (ComponentScore("T2", 82.0, 100.0, "Short timing trigger"),), True, True)

    rfh = RuntimeFeedHealth(
        primary_15m=FeedHealthStatus.HEALTHY,
        macro_blackout_feed=FeedHealthStatus.HEALTHY,
    )
    hard_gate = evaluate_xauusd_hard_gates(rfh, candidate_profile)

    cand_res = evaluate_xauusd_candidate_gate(
        long_direction=long_dir,
        short_direction=short_dir,
        long_timing=long_tim,
        short_timing=short_tim,
        hard_gate=hard_gate,
        profile=candidate_profile,
    )
    assert cand_res.candidate_state == SignalState.CONFLICT
    assert cand_res.candidate_user_decision == UserDecision.WAIT
    assert cand_res.resolution_reason == "SAME_TIER_WINDOW_CONFLICT"


@pytest.mark.unit
def test_contract_xau_p4_04_system_safety_hold_to_wait(candidate_profile):
    """
    Contract XAU-P4-04: SYSTEM_SAFETY_HOLD -> WAIT
    When hard safety gate trips (e.g. macro blackout or unclosed candle),
    engine and gate resolve FORCE_WAIT -> WAIT.
    """
    long_dir = SideDirectionScoreResult(SignalSide.LONG, 95.0, 100.0, (ComponentScore("D1", 95.0, 100.0, "Long dir valid"),), True, True)
    short_dir = SideDirectionScoreResult(SignalSide.SHORT, 10.0, 100.0, (), True, False)
    long_tim = SideTimingScoreResult(SignalSide.LONG, 95.0, 100.0, (ComponentScore("T1", 95.0, 100.0, "Long timing trigger"),), True, True)
    short_tim = SideTimingScoreResult(SignalSide.SHORT, 10.0, 100.0, (), True, False)

    # Macro blackout tripped
    rfh = RuntimeFeedHealth(
        primary_15m=FeedHealthStatus.HEALTHY,
        macro_blackout_feed=FeedHealthStatus.HEALTHY,
        is_macro_blackout=True,
    )
    hard_gate = evaluate_xauusd_hard_gates(rfh, candidate_profile)
    assert hard_gate.is_blocked is True
    assert any("blackout" in r for r in hard_gate.block_reasons)

    # Layer A candidate gate
    cand_res = evaluate_xauusd_candidate_gate(
        long_direction=long_dir,
        short_direction=short_dir,
        long_timing=long_tim,
        short_timing=short_tim,
        hard_gate=hard_gate,
        profile=candidate_profile,
    )
    assert cand_res.candidate_state == SignalState.FORCE_WAIT
    assert cand_res.candidate_user_decision == UserDecision.WAIT

    # Layer B engine analyze with unclosed candle
    now = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    unclosed_candle = CandleData(
        timestamp_open=now,
        timestamp_close=now,
        open=Decimal("2500.0"),
        high=Decimal("2505.0"),
        low=Decimal("2495.0"),
        close=Decimal("2502.0"),
        volume=Decimal("100.0"),
        is_closed=False,  # Unclosed candle
    )
    engine = XauUsdSignalEngine()
    snapshot = engine.analyze(
        closed_candles_15m=[unclosed_candle],
        runtime_health=rfh,
        profile=candidate_profile,
    )
    assert snapshot.state == SignalState.FORCE_WAIT
    assert snapshot.user_decision == UserDecision.WAIT
    assert snapshot.hard_gate.is_blocked is True
