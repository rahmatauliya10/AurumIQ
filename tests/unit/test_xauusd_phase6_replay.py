"""Unit tests for Phase 6 XAUUSD Point-In-Time Replay Engine."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest

from engine.backtest.clock import ReplayClock
from engine.backtest.repository import PointInTimeDataset
from engine.backtest.xauusd_outcomes import XauUsdOutcomeEngine
from engine.backtest.xauusd_replay import XauUsdPointInTimeReplay
from engine.backtest.xauusd_types import (
    XauUsdCostConfig,
    XauUsdTradeOutcome,
)
from engine.core.types import (
    CandleData,
    EntryExecutionPolicy,
    IntrabarPolicy,
    SignalSide,
    SignalState,
    UserDecision,
)
from engine.risk.xauusd_planner import XauUsdRiskPlanner
from engine.risk.xauusd_policy import (
    SideRiskPolicy,
    XauUsdExecutionPolicy,
    XauUsdRiskProfile,
)
from engine.signals.engine import XauUsdSignalEngine


@pytest.fixture
def calibrated_test_risk_profile():
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


def make_candle(ts_open: datetime, o: Decimal, h: Decimal, l: Decimal, c: Decimal, is_closed: bool = True) -> CandleData:
    return CandleData(
        timestamp_open=ts_open,
        timestamp_close=ts_open + timedelta(minutes=15),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=Decimal("100"),
        is_closed=is_closed,
        source_id="TEST",
    )


def test_replay_chronological_pit_ordering(calibrated_test_risk_profile):
    """Test that replay iterates strictly in chronological forward order."""
    code_rev = "46e388a106b9bdc388e646c73570e7879142c837"
    start_t = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    dataset = PointInTimeDataset()

    for i in range(40):
        t_open = start_t + timedelta(minutes=15 * i)
        dataset.add_candle("15m", make_candle(t_open, Decimal("2600.00"), Decimal("2605.00"), Decimal("2595.00"), Decimal("2602.00")))

    sig_engine = XauUsdSignalEngine(code_revision=code_rev)
    risk_planner = XauUsdRiskPlanner(code_revision=code_rev, risk_profile=calibrated_test_risk_profile)
    outcome_engine = XauUsdOutcomeEngine(cost_config=XauUsdCostConfig.idealized(), holding_horizon_bars_15m=10)

    replay = XauUsdPointInTimeReplay(
        dataset=dataset,
        signal_engine=sig_engine,
        risk_planner=risk_planner,
        outcome_engine=outcome_engine,
        holding_horizon_bars_15m=10,
    )

    timestamps = [start_t + timedelta(hours=5) + timedelta(minutes=15 * i) for i in range(12)]
    clock = ReplayClock(timestamps=timestamps)
    signals, trades = replay.run(clock)

    assert len(signals) > 0
    for i in range(1, len(signals)):
        assert signals[i].timestamp > signals[i - 1].timestamp


def test_replay_published_decision_is_always_wait(calibrated_test_risk_profile):
    """Test that every signal snapshot generated in Phase 6 has published decision WAIT."""
    code_rev = "46e388a106b9bdc388e646c73570e7879142c837"
    start_t = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    dataset = PointInTimeDataset()

    for i in range(40):
        t_open = start_t + timedelta(minutes=15 * i)
        dataset.add_candle("15m", make_candle(t_open, Decimal("2600.00"), Decimal("2605.00"), Decimal("2595.00"), Decimal("2602.00")))

    sig_engine = XauUsdSignalEngine(code_revision=code_rev)
    risk_planner = XauUsdRiskPlanner(code_revision=code_rev, risk_profile=calibrated_test_risk_profile)
    outcome_engine = XauUsdOutcomeEngine(cost_config=XauUsdCostConfig.idealized(), holding_horizon_bars_15m=10)

    replay = XauUsdPointInTimeReplay(
        dataset=dataset,
        signal_engine=sig_engine,
        risk_planner=risk_planner,
        outcome_engine=outcome_engine,
        holding_horizon_bars_15m=10,
    )

    timestamps = [start_t + timedelta(hours=5) + timedelta(minutes=15 * i) for i in range(12)]
    clock = ReplayClock(timestamps=timestamps)
    signals, trades = replay.run(clock)

    for s in signals:
        assert s.user_decision == UserDecision.WAIT
        assert s.state in (SignalState.NO_TRADE, SignalState.FORCE_WAIT)


def test_replay_unclosed_candle_activates_safety_hold(calibrated_test_risk_profile):
    """Test that an unclosed candle <= T activates closed candle safety hold."""
    code_rev = "46e388a106b9bdc388e646c73570e7879142c837"
    start_t = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    dataset = PointInTimeDataset()

    for i in range(30):
        t_open = start_t + timedelta(minutes=15 * i)
        # Bar 25 is marked unclosed
        is_closed = (i != 25)
        dataset.add_candle("15m", make_candle(t_open, Decimal("2600.00"), Decimal("2605.00"), Decimal("2595.00"), Decimal("2602.00"), is_closed=is_closed))

    sig_engine = XauUsdSignalEngine(code_revision=code_rev)
    risk_planner = XauUsdRiskPlanner(code_revision=code_rev, risk_profile=calibrated_test_risk_profile)
    outcome_engine = XauUsdOutcomeEngine(cost_config=XauUsdCostConfig.idealized(), holding_horizon_bars_15m=10)

    replay = XauUsdPointInTimeReplay(
        dataset=dataset,
        signal_engine=sig_engine,
        risk_planner=risk_planner,
        outcome_engine=outcome_engine,
        holding_horizon_bars_15m=10,
    )

    eval_t = start_t + timedelta(minutes=15 * 26)
    clock = ReplayClock(timestamps=[eval_t])
    signals, trades = replay.run(clock)

    assert len(signals) >= 1
    sig = signals[0]
    assert sig.hard_gate.is_blocked is True or sig.state == SignalState.FORCE_WAIT
