"""
Point-in-Time (PIT) and Closed-Candle Isolation Suite for Phase 4 XAUUSD.
Covers Task 10 contracts.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest

from engine.core.types import (
    CandleData,
    FeedHealthStatus,
    RuntimeFeedHealth,
    SignalState,
    UserDecision,
)
from engine.signals.engine import XauUsdSignalEngine
from engine.signals.profile import (
    Phase4CalibrationStatus,
    Phase4SignalProfile,
    SideDirectionPolicy,
    SideGatePolicy,
    SideTimingPolicy,
)


def _make_candle(ts: datetime, close_val: float, is_closed: bool = True) -> CandleData:
    return CandleData(
        timestamp_open=ts - timedelta(minutes=15),
        timestamp_close=ts,
        open=Decimal(str(close_val - 2.0)),
        high=Decimal(str(close_val + 5.0)),
        low=Decimal(str(close_val - 5.0)),
        close=Decimal(str(close_val)),
        volume=Decimal("500.0"),
        is_closed=is_closed,
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
def test_closed_candle_pit_isolation(candidate_profile):
    """
    Verify evaluation up to T_close is invariant to future data injection.
    """
    t0 = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    c1 = _make_candle(t0 - timedelta(minutes=30), 2500.0)
    c2 = _make_candle(t0 - timedelta(minutes=15), 2505.0)
    c3 = _make_candle(t0, 2510.0)

    engine = XauUsdSignalEngine()
    rfh = RuntimeFeedHealth(
        primary_15m=FeedHealthStatus.HEALTHY,
        primary_1h=FeedHealthStatus.HEALTHY,
        macro_blackout_feed=FeedHealthStatus.HEALTHY,
    )

    res1 = engine.analyze(
        closed_candles_15m=[c1, c2, c3],
        runtime_health=rfh,
        profile=candidate_profile,
        as_of=t0,
    )

    # Identical evaluation repeated gives same analysis_fingerprint
    res2 = engine.analyze(
        closed_candles_15m=[c1, c2, c3],
        runtime_health=rfh,
        profile=candidate_profile,
        as_of=t0,
    )

    assert res1.analysis_fingerprint == res2.analysis_fingerprint
    assert res1.timestamp == t0


@pytest.mark.unit
def test_unclosed_candle_fails_closed(candidate_profile):
    """
    Verify unclosed latest candle immediately trips hard safety gate to FORCE_WAIT / WAIT.
    """
    t0 = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    c1 = _make_candle(t0 - timedelta(minutes=15), 2500.0, is_closed=True)
    c2_unclosed = _make_candle(t0, 2505.0, is_closed=False)

    engine = XauUsdSignalEngine()
    rfh = RuntimeFeedHealth(
        primary_15m=FeedHealthStatus.HEALTHY,
        macro_blackout_feed=FeedHealthStatus.HEALTHY,
    )

    snapshot = engine.analyze(
        closed_candles_15m=[c1, c2_unclosed],
        runtime_health=rfh,
        profile=candidate_profile,
        as_of=t0,
    )

    assert snapshot.state == SignalState.FORCE_WAIT
    assert snapshot.user_decision == UserDecision.WAIT
    assert snapshot.hard_gate.is_blocked is True
    assert any("unclosed" in r for r in snapshot.hard_gate.block_reasons)
