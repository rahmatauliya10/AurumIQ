"""
Unit tests for Phase 4 XAUUSD Core Enums, Dataclasses, and Value Objects.
Covers Task 1 additive contracts.
"""
from datetime import datetime, timezone
import pytest

from engine.core.types import (
    CandidateGateResult,
    ComponentScore,
    DualSideDirectionResult,
    DualSideSignalSnapshot,
    DualSideTimingResult,
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


@pytest.mark.unit
def test_user_decision_enum_additive_sell():
    """Verify UserDecision contains SELL alongside historical values."""
    assert UserDecision.BUY.value == "BUY"
    assert UserDecision.WAIT.value == "WAIT"
    assert UserDecision.AVOID.value == "AVOID"
    assert UserDecision.SELL.value == "SELL"


@pytest.mark.unit
def test_signal_state_enum_additive_dual_side():
    """Verify SignalState contains all historical and additive dual-side states."""
    # Historical
    assert SignalState.NO_TRADE.value == "NO_TRADE"
    assert SignalState.AVOID.value == "AVOID"
    assert SignalState.WATCH.value == "WATCH"
    assert SignalState.READY.value == "READY"
    assert SignalState.BUY_WINDOW.value == "BUY_WINDOW"
    assert SignalState.FORCE_WAIT.value == "FORCE_WAIT"
    # Additive XAUUSD dual-side
    assert SignalState.WATCH_LONG.value == "WATCH_LONG"
    assert SignalState.READY_LONG.value == "READY_LONG"
    assert SignalState.WATCH_SHORT.value == "WATCH_SHORT"
    assert SignalState.READY_SHORT.value == "READY_SHORT"
    assert SignalState.SELL_WINDOW.value == "SELL_WINDOW"
    assert SignalState.CONFLICT.value == "CONFLICT"


@pytest.mark.unit
def test_feed_enums():
    """Verify FeedCriticality and FeedHealthStatus enums."""
    assert FeedCriticality.CRITICAL.value == "CRITICAL"
    assert FeedCriticality.OPTIONAL.value == "OPTIONAL"
    assert FeedCriticality.INFORMATIONAL.value == "INFORMATIONAL"

    assert FeedHealthStatus.HEALTHY.value == "HEALTHY"
    assert FeedHealthStatus.UNHEALTHY.value == "UNHEALTHY"
    assert FeedHealthStatus.STALE.value == "STALE"
    assert FeedHealthStatus.TRANSITION.value == "TRANSITION"
    assert FeedHealthStatus.MISSING.value == "MISSING"
    assert FeedHealthStatus.UNKNOWN.value == "UNKNOWN"


@pytest.mark.unit
def test_runtime_feed_health_defaults_to_fail_safe():
    """Verify RuntimeFeedHealth defaults all feeds to UNKNOWN (fail-safe)."""
    rfh = RuntimeFeedHealth()
    assert rfh.primary_15m == FeedHealthStatus.UNKNOWN
    assert rfh.primary_1h == FeedHealthStatus.UNKNOWN
    assert rfh.primary_4h == FeedHealthStatus.UNKNOWN
    assert rfh.primary_1d == FeedHealthStatus.UNKNOWN
    assert rfh.secondary_provider == FeedHealthStatus.UNKNOWN
    assert rfh.secondary_provider_disagreement is False
    assert rfh.macro_blackout_feed == FeedHealthStatus.UNKNOWN
    assert rfh.is_macro_blackout is False
    assert rfh.volume == FeedHealthStatus.UNKNOWN
    assert rfh.phase3a == FeedHealthStatus.UNKNOWN
    assert rfh.phase3b == FeedHealthStatus.UNKNOWN
    assert rfh.is_unclosed_candle is False


@pytest.mark.unit
def test_dual_side_direction_and_timing_results():
    """Verify SideDirectionScoreResult, DualSideDirectionResult, SideTimingScoreResult, DualSideTimingResult."""
    comp = ComponentScore(name="Regime", score=15.0, max_score=15.0, reason="Bullish")
    long_dir = SideDirectionScoreResult(
        side=SignalSide.LONG,
        total_score=85.0,
        max_score=100.0,
        components=(comp,),
        is_valid=True,
        is_direction_ready=True,
        config_version="cfg-2026-v1",
    )
    short_dir = SideDirectionScoreResult(
        side=SignalSide.SHORT,
        total_score=15.0,
        max_score=100.0,
        components=(),
        is_valid=True,
        is_direction_ready=False,
        config_version="cfg-2026-v1",
    )
    dual_dir = DualSideDirectionResult(
        long_direction=long_dir,
        short_direction=short_dir,
        is_calibrated=True,
    )
    assert dual_dir.long_direction.total_score == 85.0
    assert dual_dir.short_direction.total_score == 15.0

    long_tim = SideTimingScoreResult(
        side=SignalSide.LONG,
        total_score=80.0,
        max_score=100.0,
        components=(),
        is_valid=True,
        is_timing_ready=True,
        config_version="cfg-2026-v1",
    )
    short_tim = SideTimingScoreResult(
        side=SignalSide.SHORT,
        total_score=0.0,
        max_score=100.0,
        components=(),
        is_valid=True,
        is_timing_ready=False,
        config_version="cfg-2026-v1",
    )
    dual_tim = DualSideTimingResult(
        long_timing=long_tim,
        short_timing=short_tim,
        is_calibrated=True,
    )
    assert dual_tim.long_timing.is_timing_ready is True
    assert dual_tim.short_timing.is_timing_ready is False


@pytest.mark.unit
def test_dual_side_signal_snapshot():
    """Verify DualSideSignalSnapshot stores candidate and published states for audit."""
    rfh = RuntimeFeedHealth(primary_15m=FeedHealthStatus.HEALTHY)
    hard_gate = XauUsdHardGateEvaluation(
        is_blocked=False,
        override_state=None,
        block_reasons=(),
        runtime_health=rfh,
    )
    long_dir = SideDirectionScoreResult(SignalSide.LONG, 85.0, 100.0, (), True, True, "v1")
    short_dir = SideDirectionScoreResult(SignalSide.SHORT, 10.0, 100.0, (), True, False, "v1")
    long_tim = SideTimingScoreResult(SignalSide.LONG, 80.0, 100.0, (), True, True, "v1")
    short_tim = SideTimingScoreResult(SignalSide.SHORT, 0.0, 100.0, (), True, False, "v1")

    now = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    snap = DualSideSignalSnapshot(
        timestamp=now,
        instrument="XAUUSD",
        timeframe="15m",
        state=SignalState.NO_TRADE,
        user_decision=UserDecision.WAIT,
        candidate_state=SignalState.BUY_WINDOW,
        candidate_user_decision=UserDecision.BUY,
        long_direction=long_dir,
        short_direction=short_dir,
        long_timing=long_tim,
        short_timing=short_tim,
        hard_gate=hard_gate,
        reasons_long_positive=("Strong trend",),
        reasons_long_negative=(),
        reasons_short_positive=(),
        reasons_short_negative=("No short setup",),
        hard_gate_reasons=(),
        resolution_reason="UNAUTHORIZED_PRODUCTION_PROFILE",
        analysis_fingerprint="abc123hash",
        phase4_policy_fingerprint="policy456hash",
        code_revision="19015f9a8cc536bb2f33b54d2c071139f26590d1",
        profile_name="XAUUSD_UNCALIBRATED",
        calibration_status="PENDING_PHASE6",
    )
    assert snap.candidate_state == SignalState.BUY_WINDOW
    assert snap.candidate_user_decision == UserDecision.BUY
    assert snap.state == SignalState.NO_TRADE
    assert snap.user_decision == UserDecision.WAIT
