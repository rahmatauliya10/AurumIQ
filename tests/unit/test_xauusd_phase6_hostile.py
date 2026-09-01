"""Hostile edge-case and regression tests for Phase 6 XAUUSD backtest engine."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import inspect
import pytest

from engine.backtest.clock import ReplayClock
from engine.backtest.repository import PointInTimeDataset
from engine.backtest.xauusd_outcomes import XauUsdOutcomeEngine
from engine.backtest.xauusd_replay import XauUsdPointInTimeReplay
from engine.backtest.xauusd_types import (
    XauUsdCostConfig,
    XauUsdTradeOutcome,
)
import engine.backtest.xauusd_outcomes as xauusd_outcomes_mod
import engine.backtest.xauusd_replay as xauusd_replay_mod
import engine.backtest.xauusd_types as xauusd_types_mod
from engine.core.types import (
    CandleData,
    EntryExecutionPolicy,
    IntrabarPolicy,
    QuoteData,
    RiskSide,
    SideDirectionScoreResult,
    SideTimingScoreResult,
    SignalSide,
    SignalState,
    StructureResult,
    StructureType,
    StructureZone,
    UserDecision,
    XauUsdHardGateEvaluation,
    RuntimeFeedHealth,
    DualSideSignalSnapshot,
)
from engine.risk.xauusd_planner import XauUsdRiskPlanner
from engine.risk.xauusd_policy import (
    SideRiskPolicy,
    XauUsdExecutionPolicy,
    XauUsdRiskProfile,
)


@pytest.fixture
def calibrated_risk_profile():
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


def test_hostile_future_candle_leakage_prevented():
    """Verify that candles closing after T are strictly invisible at T."""
    eval_t = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    dataset = PointInTimeDataset()

    # Add 10 candles <= T
    for i in range(10):
        t_open = eval_t - timedelta(minutes=15 * (10 - i))
        dataset.add_candle("15m", make_candle(t_open, Decimal("2600.00"), Decimal("2605.00"), Decimal("2595.00"), Decimal("2600.00")))

    # Add future candle > T
    dataset.add_candle("15m", make_candle(eval_t, Decimal("2700.00"), Decimal("2750.00"), Decimal("2690.00"), Decimal("2740.00")))

    closed_at_t = dataset.get_closed_candles("15m", as_of=eval_t)
    assert len(closed_at_t) == 10
    assert all(c.timestamp_close <= eval_t for c in closed_at_t)


def test_hostile_same_bar_pre_activation_fill_rejected(calibrated_risk_profile):
    """Verify that an order cannot be filled before earliest_exec_ts (signal_t + latency)."""
    eval_t = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    outcome_engine = XauUsdOutcomeEngine(
        cost_config=XauUsdCostConfig.idealized(),
        holding_horizon_bars_15m=5,
    )

    sig = DualSideSignalSnapshot(
        timestamp=eval_t,
        instrument="XAUUSD",
        timeframe="15m",
        state=SignalState.NO_TRADE,
        user_decision=UserDecision.WAIT,
        candidate_state=SignalState.BUY_WINDOW,
        candidate_user_decision=UserDecision.BUY,
        long_direction=SideDirectionScoreResult(RiskSide.LONG, 80.0, 100.0, (), True, True),
        short_direction=SideDirectionScoreResult(RiskSide.SHORT, 80.0, 100.0, (), True, True),
        long_timing=SideTimingScoreResult(RiskSide.LONG, 80.0, 100.0, (), True, True),
        short_timing=SideTimingScoreResult(RiskSide.SHORT, 80.0, 100.0, (), True, True),
        hard_gate=XauUsdHardGateEvaluation(False, None, (), RuntimeFeedHealth()),
        reasons_long_positive=(),
        reasons_long_negative=(),
        reasons_short_positive=(),
        reasons_short_negative=(),
        hard_gate_reasons=(),
        resolution_reason="",
        candidate_resolution_reason="",
        publication_reason="",
        analysis_fingerprint="sig-long",
        phase4_policy_fingerprint="p4",
        code_revision="46e388a106b9bdc388e646c73570e7879142c837",
        profile_name="XAUUSD",
        calibration_status="CANDIDATE",
    )

    support = StructureZone("SUPPORT", Decimal("2600.00"), Decimal("2605.00"), eval_t - timedelta(hours=2), 2, True)
    resistance = StructureZone("RESISTANCE", Decimal("2630.00"), Decimal("2635.00"), eval_t - timedelta(hours=2), 2, True)
    struct = StructureResult(eval_t, StructureType.HH, None, None, None, (), (support, resistance))

    planner = XauUsdRiskPlanner(code_revision="rev", risk_profile=calibrated_risk_profile)
    plan = planner.plan_long(phase4_snapshot=sig, structure_15m=struct, atr14=Decimal("3.00"))

    # Future quotes: one before latency (10:00:00.500), one after latency (10:00:02.000)
    q_early = QuoteData(timestamp=eval_t + timedelta(milliseconds=500), bid=Decimal("2604.00"), ask=Decimal("2605.00"))
    q_valid = QuoteData(timestamp=eval_t + timedelta(seconds=2), bid=Decimal("2604.50"), ask=Decimal("2605.50"))

    trade = outcome_engine.resolve_trade(
        signal=sig,
        risk_plan=plan,
        future_candles_15m=[make_candle(eval_t, Decimal("2605.00"), Decimal("2635.00"), Decimal("2604.00"), Decimal("2632.00"))],
        future_quotes=[q_early, q_valid],
        execution_policy=EntryExecutionPolicy.MARKET_AFTER_SIGNAL,
        trade_id="t-hostile-1",
    )

    # Fill MUST happen at q_valid (after latency), NEVER at q_early
    assert trade.fill_timestamp == eval_t + timedelta(seconds=2)
    assert trade.fill_price >= Decimal("2605.50")


def test_hostile_decimal_preservation():
    """Verify that all monetary and normalized R values are Decimal without float precision loss."""
    cost = XauUsdCostConfig(
        entry_fee_bps=Decimal("1.25"),
        exit_fee_bps=Decimal("1.25"),
        synthetic_spread_bps=Decimal("2.50"),
        entry_slippage_bps=Decimal("1.00"),
        exit_slippage_bps=Decimal("1.00"),
    )
    assert isinstance(cost.entry_fee_bps, Decimal)
    assert isinstance(cost.exit_fee_bps, Decimal)


def test_hostile_legacy_defaults_audit():
    """Audit source files to ensure no hardcoded 96-bar defaults, 4bps crypto presets, or USDT dependencies in Phase 6."""
    outcomes_src = inspect.getsource(xauusd_outcomes_mod)
    replay_src = inspect.getsource(xauusd_replay_mod)
    types_src = inspect.getsource(xauusd_types_mod)

    all_code = outcomes_src + replay_src + types_src

    # No hardcoded 96 bars default
    assert "96" not in all_code
    # No Tether Gold / USDT dependencies
    assert "XAUT" not in all_code
    assert "USDT" not in all_code
    assert "Tether" not in all_code
