"""
Hostile Point-in-Time (PIT) and Closed-Candle Isolation Suite for Phase 4 XAUUSD.
Covers complete Matrix (A through E).
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


def _make_candle(ts: datetime, close_val: float, is_closed: bool = True, tf_mins: int = 15) -> CandleData:
    return CandleData(
        timestamp_open=ts - timedelta(minutes=tf_mins),
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


@pytest.fixture
def base_rfh():
    return RuntimeFeedHealth(
        primary_15m=FeedHealthStatus.HEALTHY,
        primary_1h=FeedHealthStatus.HEALTHY,
        primary_4h=FeedHealthStatus.HEALTHY,
        primary_1d=FeedHealthStatus.HEALTHY,
        macro_blackout_feed=FeedHealthStatus.HEALTHY,
    )


@pytest.mark.unit
def test_matrix_a_future_closed_candles_ignored(candidate_profile, base_rfh):
    """Matrix A: Future closed candles > T are strictly ignored before closure validation."""
    t0 = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    c1 = _make_candle(t0 - timedelta(minutes=30), 2500.0)
    c2 = _make_candle(t0 - timedelta(minutes=15), 2505.0)
    c3 = _make_candle(t0, 2510.0)
    c_future = _make_candle(t0 + timedelta(minutes=15), 2550.0, is_closed=True)

    engine = XauUsdSignalEngine(code_revision="test-rev-p4")

    res_clean = engine.analyze(
        closed_candles_15m=[c1, c2, c3],
        runtime_health=base_rfh,
        profile=candidate_profile,
        as_of=t0,
    )

    res_with_future = engine.analyze(
        closed_candles_15m=[c1, c2, c3, c_future],
        runtime_health=base_rfh,
        profile=candidate_profile,
        as_of=t0,
    )

    assert res_clean.analysis_fingerprint == res_with_future.analysis_fingerprint
    assert res_clean.timestamp == t0
    assert res_with_future.timestamp == t0


@pytest.mark.unit
def test_matrix_b_future_candle_mutation_invariance(candidate_profile, base_rfh):
    """Matrix B: Mutating prices/volume of future candles > T does not alter T fingerprint."""
    t0 = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    c1 = _make_candle(t0 - timedelta(minutes=30), 2500.0)
    c2 = _make_candle(t0 - timedelta(minutes=15), 2505.0)
    c3 = _make_candle(t0, 2510.0)

    c_future_v1 = _make_candle(t0 + timedelta(minutes=15), 2550.0)
    c_future_v2 = _make_candle(t0 + timedelta(minutes=15), 2100.0)

    engine = XauUsdSignalEngine(code_revision="test-rev-p4")

    res1 = engine.analyze(
        closed_candles_15m=[c1, c2, c3, c_future_v1],
        runtime_health=base_rfh,
        profile=candidate_profile,
        as_of=t0,
    )

    res2 = engine.analyze(
        closed_candles_15m=[c1, c2, c3, c_future_v2],
        runtime_health=base_rfh,
        profile=candidate_profile,
        as_of=t0,
    )

    assert res1.analysis_fingerprint == res2.analysis_fingerprint


@pytest.mark.unit
def test_matrix_c_future_unclosed_candle_ignored_no_force_wait(candidate_profile, base_rfh):
    """Matrix C: Future unclosed candle > T is ignored and MUST NOT trigger FORCE_WAIT."""
    t0 = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    c1 = _make_candle(t0 - timedelta(minutes=15), 2500.0, is_closed=True)
    c2 = _make_candle(t0, 2505.0, is_closed=True)
    c_future_unclosed = _make_candle(t0 + timedelta(minutes=15), 2510.0, is_closed=False)

    engine = XauUsdSignalEngine(code_revision="test-rev-p4")

    snapshot = engine.analyze(
        closed_candles_15m=[c1, c2, c_future_unclosed],
        runtime_health=base_rfh,
        profile=candidate_profile,
        as_of=t0,
    )

    assert snapshot.hard_gate.is_blocked is False
    assert snapshot.hard_gate.runtime_health.is_unclosed_candle is False


@pytest.mark.unit
def test_matrix_d_unclosed_candle_le_t_causes_force_wait(candidate_profile, base_rfh):
    """Matrix D: Unclosed decision candle <= T causes immediate FORCE_WAIT."""
    t0 = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    c1 = _make_candle(t0 - timedelta(minutes=15), 2500.0, is_closed=True)
    c2_unclosed = _make_candle(t0, 2505.0, is_closed=False)

    engine = XauUsdSignalEngine(code_revision="test-rev-p4")

    snapshot = engine.analyze(
        closed_candles_15m=[c1, c2_unclosed],
        runtime_health=base_rfh,
        profile=candidate_profile,
        as_of=t0,
    )

    assert snapshot.state == SignalState.FORCE_WAIT
    assert snapshot.user_decision == UserDecision.WAIT
    assert snapshot.hard_gate.is_blocked is True
    assert snapshot.hard_gate.runtime_health.is_unclosed_candle is True


@pytest.mark.unit
@pytest.mark.parametrize("tf_name, tf_mins, delta", [
    ("1h", 60, timedelta(hours=1)),
    ("4h", 240, timedelta(hours=4)),
    ("1d", 1440, timedelta(days=1)),
])
def test_matrix_e_future_higher_timeframes_mutation_invariance(candidate_profile, base_rfh, tf_name, tf_mins, delta):
    """Matrix E: Future 1H, 4H, 1D candles > T mutated do not alter T fingerprint."""
    t0 = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    c15 = [_make_candle(t0, 2500.0, is_closed=True, tf_mins=15)]
    c1h = [_make_candle(t0, 2500.0, is_closed=True, tf_mins=60)]
    c4h = [_make_candle(t0, 2500.0, is_closed=True, tf_mins=240)]
    c1d = [_make_candle(t0, 2500.0, is_closed=True, tf_mins=1440)]

    c_future_a = _make_candle(t0 + delta, 2600.0, is_closed=True, tf_mins=tf_mins)
    c_future_b = _make_candle(t0 + delta, 2100.0, is_closed=True, tf_mins=tf_mins)

    kwargs_a = {"closed_candles_15m": c15, "closed_candles_1h": list(c1h), "closed_candles_4h": list(c4h), "closed_candles_1d": list(c1d)}
    kwargs_b = {"closed_candles_15m": c15, "closed_candles_1h": list(c1h), "closed_candles_4h": list(c4h), "closed_candles_1d": list(c1d)}

    kwargs_a[f"closed_candles_{tf_name}"].append(c_future_a)
    kwargs_b[f"closed_candles_{tf_name}"].append(c_future_b)

    engine = XauUsdSignalEngine(code_revision="test-rev-p4")

    res_a = engine.analyze(
        runtime_health=base_rfh,
        profile=candidate_profile,
        as_of=t0,
        **kwargs_a,
    )
    res_b = engine.analyze(
        runtime_health=base_rfh,
        profile=candidate_profile,
        as_of=t0,
        **kwargs_b,
    )

    assert res_a.analysis_fingerprint == res_b.analysis_fingerprint


@pytest.mark.unit
@pytest.mark.parametrize("tf_name, tf_mins, delta", [
    ("1h", 60, timedelta(hours=1)),
    ("4h", 240, timedelta(hours=4)),
    ("1d", 1440, timedelta(days=1)),
])
def test_matrix_f_future_higher_tf_unclosed_ignored_no_force_wait(candidate_profile, base_rfh, tf_name, tf_mins, delta):
    """Matrix F: Future unclosed higher-TF candle > T is ignored and MUST NOT trigger FORCE_WAIT."""
    t0 = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    c15 = [_make_candle(t0, 2500.0, is_closed=True, tf_mins=15)]
    c1h = [_make_candle(t0, 2500.0, is_closed=True, tf_mins=60)]
    c4h = [_make_candle(t0, 2500.0, is_closed=True, tf_mins=240)]
    c1d = [_make_candle(t0, 2500.0, is_closed=True, tf_mins=1440)]

    c_future_unclosed = _make_candle(t0 + delta, 2550.0, is_closed=False, tf_mins=tf_mins)
    kwargs = {"closed_candles_15m": c15, "closed_candles_1h": list(c1h), "closed_candles_4h": list(c4h), "closed_candles_1d": list(c1d)}
    kwargs[f"closed_candles_{tf_name}"].append(c_future_unclosed)

    engine = XauUsdSignalEngine(code_revision="test-rev-p4")
    snapshot = engine.analyze(
        runtime_health=base_rfh,
        profile=candidate_profile,
        as_of=t0,
        **kwargs,
    )

    assert snapshot.hard_gate.is_blocked is False
    assert snapshot.hard_gate.runtime_health.is_unclosed_candle is False


@pytest.mark.unit
@pytest.mark.parametrize("tf_name, tf_mins", [
    ("1h", 60),
    ("4h", 240),
    ("1d", 1440),
])
def test_matrix_g_unclosed_higher_tf_candle_le_t_causes_force_wait(candidate_profile, base_rfh, tf_name, tf_mins):
    """Matrix G: Unclosed higher-TF decision candle <= T causes immediate FORCE_WAIT."""
    t0 = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    c15 = [_make_candle(t0, 2500.0, is_closed=True, tf_mins=15)]
    c1h = [_make_candle(t0, 2500.0, is_closed=True, tf_mins=60)]
    c4h = [_make_candle(t0, 2500.0, is_closed=True, tf_mins=240)]
    c1d = [_make_candle(t0, 2500.0, is_closed=True, tf_mins=1440)]

    # Replace the <= T candle with an unclosed one
    kwargs = {"closed_candles_15m": c15, "closed_candles_1h": list(c1h), "closed_candles_4h": list(c4h), "closed_candles_1d": list(c1d)}
    kwargs[f"closed_candles_{tf_name}"] = [_make_candle(t0, 2500.0, is_closed=False, tf_mins=tf_mins)]

    engine = XauUsdSignalEngine(code_revision="test-rev-p4")
    snapshot = engine.analyze(
        runtime_health=base_rfh,
        profile=candidate_profile,
        as_of=t0,
        **kwargs,
    )

    assert snapshot.state == SignalState.FORCE_WAIT
    assert snapshot.user_decision == UserDecision.WAIT
    assert snapshot.hard_gate.is_blocked is True
    assert snapshot.hard_gate.runtime_health.is_unclosed_candle is True

