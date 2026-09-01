"""
Acceptance Test Contracts for Phase 4 XAUUSD Dual-Side Signal Engine.
Official Approved Contract IDs:
  - XAU-P4-01: candidate BUY_WINDOW -> BUY
  - XAU-P4-02: candidate SELL_WINDOW -> SELL
  - XAU-P4-03: long/short conflict -> CONFLICT -> WAIT
  - XAU-P4-04: macro blackout -> FORCE_WAIT -> WAIT
Production publication remains WAIT-only pending Phase 6 empirical calibration.
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


@pytest.mark.acceptance
def test_xau_p4_01_buy_window_to_buy(candidate_profile):
    """
    Official Acceptance Contract: XAU-P4-01
    Candidate BUY_WINDOW -> BUY on bullish qualified setup.
    Production publication remains WAIT-only.
    """
    long_dir = SideDirectionScoreResult(SignalSide.LONG, 85.0, 100.0, (ComponentScore("Regime", 85.0, 100.0, "Long dir valid"),), True, True)
    short_dir = SideDirectionScoreResult(SignalSide.SHORT, 25.0, 100.0, (ComponentScore("Regime", 25.0, 100.0, "Short dir low"),), True, False)
    long_tim = SideTimingScoreResult(SignalSide.LONG, 82.0, 100.0, (ComponentScore("Zone", 82.0, 100.0, "Long timing trigger"),), True, True)
    short_tim = SideTimingScoreResult(SignalSide.SHORT, 15.0, 100.0, (ComponentScore("Zone", 15.0, 100.0, "Short timing low"),), True, False)

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


@pytest.mark.acceptance
def test_xau_p4_02_sell_window_to_sell(candidate_profile):
    """
    Official Acceptance Contract: XAU-P4-02
    Candidate SELL_WINDOW -> SELL on bearish qualified setup.
    Production publication remains WAIT-only.
    """
    long_dir = SideDirectionScoreResult(SignalSide.LONG, 20.0, 100.0, (ComponentScore("Regime", 20.0, 100.0, "Long dir low"),), True, False)
    short_dir = SideDirectionScoreResult(SignalSide.SHORT, 86.0, 100.0, (ComponentScore("Regime", 86.0, 100.0, "Short dir valid"),), True, True)
    long_tim = SideTimingScoreResult(SignalSide.LONG, 15.0, 100.0, (ComponentScore("Zone", 15.0, 100.0, "Long timing low"),), True, False)
    short_tim = SideTimingScoreResult(SignalSide.SHORT, 84.0, 100.0, (ComponentScore("Zone", 84.0, 100.0, "Short timing trigger"),), True, True)

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


@pytest.mark.acceptance
def test_xau_p4_03_conflict_to_wait(candidate_profile):
    """
    Official Acceptance Contract: XAU-P4-03
    When both Long and Short are simultaneously qualified, Layer A resolves CONFLICT -> WAIT.
    """
    long_dir = SideDirectionScoreResult(SignalSide.LONG, 85.0, 100.0, (ComponentScore("Regime", 85.0, 100.0, "Long dir valid"),), True, True)
    short_dir = SideDirectionScoreResult(SignalSide.SHORT, 85.0, 100.0, (ComponentScore("Regime", 85.0, 100.0, "Short dir valid"),), True, True)
    long_tim = SideTimingScoreResult(SignalSide.LONG, 82.0, 100.0, (ComponentScore("Zone", 82.0, 100.0, "Long timing trigger"),), True, True)
    short_tim = SideTimingScoreResult(SignalSide.SHORT, 82.0, 100.0, (ComponentScore("Zone", 82.0, 100.0, "Short timing trigger"),), True, True)

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


@pytest.mark.acceptance
def test_xau_p4_04_macro_blackout_to_force_wait(candidate_profile):
    """
    Official Acceptance Contract: XAU-P4-04
    Macro blackout trips hard safety gate -> FORCE_WAIT -> WAIT.
    """
    long_dir = SideDirectionScoreResult(SignalSide.LONG, 95.0, 100.0, (ComponentScore("Regime", 95.0, 100.0, "Long dir valid"),), True, True)
    short_dir = SideDirectionScoreResult(SignalSide.SHORT, 10.0, 100.0, (), True, False)
    long_tim = SideTimingScoreResult(SignalSide.LONG, 95.0, 100.0, (ComponentScore("Zone", 95.0, 100.0, "Long timing trigger"),), True, True)
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

    # Master Engine Pipeline Publication Authority check
    now = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    candle = CandleData(
        timestamp_open=now,
        timestamp_close=now,
        open=Decimal("2500.0"),
        high=Decimal("2505.0"),
        low=Decimal("2495.0"),
        close=Decimal("2502.0"),
        volume=Decimal("100.0"),
        is_closed=True,
    )
    engine = XauUsdSignalEngine(code_revision="test-rev-p4")
    snapshot = engine.analyze(
        closed_candles_15m=[candle],
        runtime_health=rfh,
        profile=candidate_profile,
    )
    assert snapshot.state == SignalState.FORCE_WAIT
    assert snapshot.user_decision == UserDecision.WAIT
    assert snapshot.hard_gate.is_blocked is True
