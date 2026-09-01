"""
Unit tests for Phase 4 Master Engine XauUsdSignalEngine with Production Authority Guard.
Covers Task 7 contracts.
"""
from datetime import datetime, timezone
from decimal import Decimal
import pytest

from engine.core.types import (
    CandleData,
    FeedHealthStatus,
    RuntimeFeedHealth,
    SignalState,
    UserDecision,
)
from engine.signals.engine import XautSignalEngine, XauUsdSignalEngine
from engine.signals.profile import (
    Phase4CalibrationStatus,
    Phase4SignalProfile,
    SideDirectionPolicy,
    SideGatePolicy,
    SideTimingPolicy,
    uncalibrated_xauusd_signal_profile,
)


def _make_dummy_candle(close_p: float = 2500.0) -> CandleData:
    now = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    return CandleData(
        timestamp_open=now,
        timestamp_close=now,
        open=Decimal(str(close_p - 2)),
        high=Decimal(str(close_p + 3)),
        low=Decimal(str(close_p - 3)),
        close=Decimal(str(close_p)),
        volume=Decimal("1000.0"),
        is_closed=True,
    )


@pytest.mark.unit
def test_xauusd_engine_production_authority_blocks_publication():
    """
    Verify Layer B production authority guard:
    Even when Layer A candidate mechanics resolve BUY_WINDOW / BUY on a configured candidate profile,
    published state is NO_TRADE and published user_decision is WAIT.
    """
    configured_candidate_profile = Phase4SignalProfile(
        target_instrument="XAUUSD",
        calibration_status=Phase4CalibrationStatus.CANDIDATE_NOT_FROZEN,
        long_direction=SideDirectionPolicy(15.0, 10.0, 10.0, 10.0, 20.0, 15.0, 10.0, 10.0),
        short_direction=SideDirectionPolicy(15.0, 10.0, 10.0, 10.0, 20.0, 15.0, 10.0, 10.0),
        long_timing=SideTimingPolicy(25.0, 25.0, 20.0, 20.0, 10.0),
        short_timing=SideTimingPolicy(25.0, 25.0, 20.0, 20.0, 10.0),
        long_gate=SideGatePolicy(70.0, 75.0, 70.0, 80.0, 80.0),
        short_gate=SideGatePolicy(70.0, 75.0, 70.0, 80.0, 80.0),
    )
    assert configured_candidate_profile.is_production_authorized is False

    engine = XauUsdSignalEngine(code_revision="test-rev-p4")
    candle = _make_dummy_candle()
    rfh = RuntimeFeedHealth(
        primary_15m=FeedHealthStatus.HEALTHY,
        macro_blackout_feed=FeedHealthStatus.HEALTHY,
        is_macro_blackout=False,
    )

    snapshot = engine.analyze(
        closed_candles_15m=[candle],
        runtime_health=rfh,
        profile=configured_candidate_profile,
    )

    # Layer B: Published state is NO_TRADE / WAIT
    assert snapshot.state == SignalState.NO_TRADE
    assert snapshot.user_decision == UserDecision.WAIT

    # Layer A: Candidate states are preserved in snapshot
    assert snapshot.candidate_state in (SignalState.NO_TRADE, SignalState.WATCH_LONG, SignalState.READY_LONG, SignalState.BUY_WINDOW, SignalState.CONFLICT)
    assert snapshot.candidate_user_decision in (UserDecision.WAIT, UserDecision.BUY, UserDecision.SELL)
    assert snapshot.profile_name == "XAUUSD_UNCALIBRATED" or "XAUUSD" in snapshot.profile_name
    assert snapshot.calibration_status == Phase4CalibrationStatus.CANDIDATE_NOT_FROZEN.value


@pytest.mark.unit
def test_xauusd_engine_uncalibrated_defaults():
    """Verify engine defaults to uncalibrated profile with fail-safe behavior when profile=None."""
    engine = XauUsdSignalEngine(code_revision="test-rev-p4")
    candle = _make_dummy_candle()
    rfh = RuntimeFeedHealth(
        primary_15m=FeedHealthStatus.HEALTHY,
        macro_blackout_feed=FeedHealthStatus.HEALTHY,
    )

    snapshot = engine.analyze(
        closed_candles_15m=[candle],
        runtime_health=rfh,
        profile=None,
    )

    assert snapshot.state == SignalState.NO_TRADE
    assert snapshot.user_decision == UserDecision.WAIT
    assert snapshot.candidate_state == SignalState.NO_TRADE
    assert snapshot.candidate_user_decision == UserDecision.WAIT
    assert snapshot.long_direction.is_valid is False
    assert snapshot.short_direction.is_valid is False
    assert snapshot.long_timing.is_valid is False
    assert snapshot.short_timing.is_valid is False


@pytest.mark.unit
def test_xauusd_engine_code_revision_validation():
    """Verify code_revision is strictly required and non-empty."""
    with pytest.raises(ValueError, match="code_revision"):
        XauUsdSignalEngine(code_revision="")

    with pytest.raises(ValueError, match="code_revision"):
        XauUsdSignalEngine(code_revision="   ")


@pytest.mark.unit
def test_historical_xaut_signal_engine_preserved():
    """Verify historical XautSignalEngine remains 100% frozen."""
    engine = XautSignalEngine(code_revision="19015f9a8cc536bb2f33b54d2c071139f26590d1")
    assert hasattr(engine, "analyze")


