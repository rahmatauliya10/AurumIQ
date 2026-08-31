"""
Unit tests for Phase 4 XAUUSD Fingerprinting and Dual-Side Explainer.
Covers Task 6 contracts.
"""
from datetime import datetime, timezone
import pytest

from engine.core.types import (
    CandidateGateResult,
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
from engine.signals.explainer import (
    compute_canonical_fingerprint,
    compute_xauusd_fingerprint,
    explain_dual_side_signal,
    explain_signal,
)


@pytest.mark.unit
def test_compute_xauusd_fingerprint_deterministic():
    """Verify compute_xauusd_fingerprint is deterministic and binds policy and 1H candle hash."""
    now = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    long_dir = SideDirectionScoreResult(SignalSide.LONG, 85.0, 100.0, (ComponentScore("Regime", 15.0, 15.0, "Bull"),), True, True)
    short_dir = SideDirectionScoreResult(SignalSide.SHORT, 20.0, 100.0, (), True, False)
    long_tim = SideTimingScoreResult(SignalSide.LONG, 80.0, 100.0, (ComponentScore("Zone", 25.0, 25.0, "Zone"),), True, True)
    short_tim = SideTimingScoreResult(SignalSide.SHORT, 10.0, 100.0, (), True, False)
    rfh = RuntimeFeedHealth(primary_15m=FeedHealthStatus.HEALTHY, primary_1h=FeedHealthStatus.HEALTHY, macro_blackout_feed=FeedHealthStatus.HEALTHY)

    fp1 = compute_xauusd_fingerprint(
        timestamp=now,
        instrument="XAUUSD",
        timeframe="15m",
        phase4_policy_fingerprint="policy_sha256_abc",
        closed_candle_15m_hash="c15m_hash",
        closed_candle_1h_hash="c1h_hash",
        closed_candle_4h_hash="c4h_hash",
        closed_candle_1d_hash="c1d_hash",
        long_direction=long_dir,
        short_direction=short_dir,
        long_timing=long_tim,
        short_timing=short_tim,
        runtime_health=rfh,
        published_state=SignalState.NO_TRADE,
        published_user_decision=UserDecision.WAIT,
        candidate_state=SignalState.BUY_WINDOW,
        candidate_user_decision=UserDecision.BUY,
        candidate_resolution_reason="LONG_QUALIFIED",
        publication_reason="UNAUTHORIZED_PRODUCTION_PROFILE",
        code_revision="19015f9a8cc536bb2f33b54d2c071139f26590d1",
    )
    fp2 = compute_xauusd_fingerprint(
        timestamp=now,
        instrument="XAUUSD",
        timeframe="15m",
        phase4_policy_fingerprint="policy_sha256_abc",
        closed_candle_15m_hash="c15m_hash",
        closed_candle_1h_hash="c1h_hash",
        closed_candle_4h_hash="c4h_hash",
        closed_candle_1d_hash="c1d_hash",
        long_direction=long_dir,
        short_direction=short_dir,
        long_timing=long_tim,
        short_timing=short_tim,
        runtime_health=rfh,
        published_state=SignalState.NO_TRADE,
        published_user_decision=UserDecision.WAIT,
        candidate_state=SignalState.BUY_WINDOW,
        candidate_user_decision=UserDecision.BUY,
        candidate_resolution_reason="LONG_QUALIFIED",
        publication_reason="UNAUTHORIZED_PRODUCTION_PROFILE",
        code_revision="19015f9a8cc536bb2f33b54d2c071139f26590d1",
    )
    assert fp1 == fp2
    assert len(fp1) == 64

    # Changing 1H candle hash produces different fingerprint
    fp3 = compute_xauusd_fingerprint(
        timestamp=now,
        instrument="XAUUSD",
        timeframe="15m",
        phase4_policy_fingerprint="policy_sha256_abc",
        closed_candle_15m_hash="c15m_hash",
        closed_candle_1h_hash="c1h_DIFFERENT_hash",
        closed_candle_4h_hash="c4h_hash",
        closed_candle_1d_hash="c1d_hash",
        long_direction=long_dir,
        short_direction=short_dir,
        long_timing=long_tim,
        short_timing=short_tim,
        runtime_health=rfh,
        published_state=SignalState.NO_TRADE,
        published_user_decision=UserDecision.WAIT,
        candidate_state=SignalState.BUY_WINDOW,
        candidate_user_decision=UserDecision.BUY,
        candidate_resolution_reason="LONG_QUALIFIED",
        publication_reason="UNAUTHORIZED_PRODUCTION_PROFILE",
        code_revision="19015f9a8cc536bb2f33b54d2c071139f26590d1",
    )
    assert fp1 != fp3

    # Mutating candidate_resolution_reason produces different fingerprint
    fp4 = compute_xauusd_fingerprint(
        timestamp=now,
        instrument="XAUUSD",
        timeframe="15m",
        phase4_policy_fingerprint="policy_sha256_abc",
        closed_candle_15m_hash="c15m_hash",
        closed_candle_1h_hash="c1h_hash",
        closed_candle_4h_hash="c4h_hash",
        closed_candle_1d_hash="c1d_hash",
        long_direction=long_dir,
        short_direction=short_dir,
        long_timing=long_tim,
        short_timing=short_tim,
        runtime_health=rfh,
        published_state=SignalState.NO_TRADE,
        published_user_decision=UserDecision.WAIT,
        candidate_state=SignalState.BUY_WINDOW,
        candidate_user_decision=UserDecision.BUY,
        candidate_resolution_reason="MUTATED_CANDIDATE_REASON",
        publication_reason="UNAUTHORIZED_PRODUCTION_PROFILE",
        code_revision="19015f9a8cc536bb2f33b54d2c071139f26590d1",
    )
    assert fp1 != fp4


@pytest.mark.unit
def test_explain_dual_side_signal():
    """Verify explain_dual_side_signal generates structured reasons for Long and Short."""
    long_dir = SideDirectionScoreResult(SignalSide.LONG, 85.0, 100.0, (ComponentScore("Regime", 15.0, 15.0, "Bull trend confirmed"),), True, True)
    short_dir = SideDirectionScoreResult(SignalSide.SHORT, 20.0, 100.0, (ComponentScore("Regime", 0.0, 15.0, "Adverse regime"),), True, False)
    long_tim = SideTimingScoreResult(SignalSide.LONG, 80.0, 100.0, (ComponentScore("Zone", 25.0, 25.0, "Near EMA20 support"),), True, True)
    short_tim = SideTimingScoreResult(SignalSide.SHORT, 10.0, 100.0, (ComponentScore("Zone", 5.0, 25.0, "Stretched from resistance"),), True, False)
    rfh = RuntimeFeedHealth(primary_15m=FeedHealthStatus.HEALTHY, macro_blackout_feed=FeedHealthStatus.HEALTHY)
    hard_gate = XauUsdHardGateEvaluation(False, None, (), rfh)
    cand_gate = CandidateGateResult(SignalState.BUY_WINDOW, UserDecision.BUY, "LONG_QUALIFIED", True)

    l_pos, l_neg, s_pos, s_neg, hg_reasons, cand_res_reason, pub_reason = explain_dual_side_signal(
        long_direction=long_dir,
        short_direction=short_dir,
        long_timing=long_tim,
        short_timing=short_tim,
        hard_gate=hard_gate,
        candidate_result=cand_gate,
        is_production_authorized=False,
    )

    assert len(l_pos) > 0
    assert "Bull trend confirmed" in l_pos[0]
    assert len(s_neg) > 0
    assert cand_res_reason == "LONG_QUALIFIED"
    assert pub_reason == "BLOCKED_PENDING_PHASE6_CALIBRATION (Candidate: BUY_WINDOW / BUY)"


@pytest.mark.unit
def test_historical_xaut_explainer_preserved():
    """Verify historical compute_canonical_fingerprint and explain_signal remain preserved."""
    from engine.core.types import DirectionScoreResult, TimingScoreResult
    dir_res = DirectionScoreResult(80.0, 100.0, (), True)
    tim_res = TimingScoreResult(80.0, 100.0, (), True)

    fp = compute_canonical_fingerprint(
        instrument="XAUT",
        timeframe="15m",
        as_of=datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc),
        closed_candles=[],
        direction=dir_res,
        timing=tim_res,
        state=SignalState.BUY_WINDOW,
        user_decision=UserDecision.BUY,
        code_revision="19015f9a8cc536bb2f33b54d2c071139f26590d1",
    )
    assert len(fp) == 64

