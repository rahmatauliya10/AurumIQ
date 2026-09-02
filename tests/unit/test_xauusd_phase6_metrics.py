"""Unit tests for Phase 6 XAUUSD Backtest Metrics Calculator."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest

from engine.backtest.xauusd_metrics import XauUsdMetricsCalculator
from engine.backtest.xauusd_types import (
    XauUsdSimulatedTrade,
    XauUsdTradeOutcome,
)
from engine.core.types import (
    DualSideSignalSnapshot,
    RegimeType,
    RiskSide,
    SessionType,
    SideDirectionScoreResult,
    SideTimingScoreResult,
    SignalSide,
    SignalState,
    UserDecision,
    XauUsdHardGateEvaluation,
    RuntimeFeedHealth,
)


def make_signal(timestamp: datetime, candidate_state: SignalState, side: SignalSide) -> DualSideSignalSnapshot:
    dir_res = SideDirectionScoreResult(RiskSide.LONG if side == SignalSide.LONG else RiskSide.SHORT, 80.0, 100.0, (), True, True)
    tim_res = SideTimingScoreResult(RiskSide.LONG if side == SignalSide.LONG else RiskSide.SHORT, 80.0, 100.0, (), True, True)
    return DualSideSignalSnapshot(
        timestamp=timestamp,
        instrument="XAUUSD",
        timeframe="15m",
        state=SignalState.NO_TRADE,
        user_decision=UserDecision.WAIT,
        candidate_state=candidate_state,
        candidate_user_decision=UserDecision.BUY if candidate_state == SignalState.BUY_WINDOW else (UserDecision.SELL if candidate_state == SignalState.SELL_WINDOW else UserDecision.WAIT),
        long_direction=dir_res,
        short_direction=dir_res,
        long_timing=tim_res,
        short_timing=tim_res,
        hard_gate=XauUsdHardGateEvaluation(False, None, (), RuntimeFeedHealth()),
        reasons_long_positive=(),
        reasons_long_negative=(),
        reasons_short_positive=(),
        reasons_short_negative=(),
        hard_gate_reasons=(),
        resolution_reason="",
        candidate_resolution_reason="",
        publication_reason="",
        analysis_fingerprint=f"sig-{timestamp.isoformat()}",
        phase4_policy_fingerprint="p4",
        code_revision="rev",
        profile_name="XAUUSD",
        calibration_status="CANDIDATE",
    )


def test_metrics_empty_inputs():
    """Test metrics calculation with empty signals and trades."""
    metrics = XauUsdMetricsCalculator.calculate([], [])
    assert metrics.candidate_count == 0
    assert metrics.trade_count == 0
    assert metrics.fill_rate == 0.0
    assert metrics.win_rate == 0.0
    assert metrics.net_expectancy_r == 0.0
    assert metrics.profit_factor == 0.0


def test_metrics_expectancy_and_payoff():
    """Test win rate, payoff ratio, and expectancy calculation."""
    t0 = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    sig1 = make_signal(t0, SignalState.BUY_WINDOW, SignalSide.LONG)
    sig2 = make_signal(t0 + timedelta(hours=1), SignalState.SELL_WINDOW, SignalSide.SHORT)

    # Trade 1: Win +2.0R
    trade1 = XauUsdSimulatedTrade(
        trade_id="t1",
        side=SignalSide.LONG,
        candidate_state=SignalState.BUY_WINDOW,
        candidate_user_decision=UserDecision.BUY,
        source_signal_fingerprint=sig1.analysis_fingerprint,
        signal_timestamp=t0,
        risk_plan_fingerprint="r1",
        planned_risk_amount=Decimal("5.00"),
        outcome=XauUsdTradeOutcome.TP1_FIRST,
        fill_timestamp=t0 + timedelta(minutes=1),
        fill_price=Decimal("2600.00"),
        exit_timestamp=t0 + timedelta(minutes=30),
        exit_price=Decimal("2610.00"),
        gross_pnl_per_unit=Decimal("10.00"),
        net_pnl_per_unit=Decimal("10.00"),
        gross_r=Decimal("2.0000"),
        net_r=Decimal("2.0000"),
    )

    # Trade 2: Loss -1.0R
    trade2 = XauUsdSimulatedTrade(
        trade_id="t2",
        side=SignalSide.SHORT,
        candidate_state=SignalState.SELL_WINDOW,
        candidate_user_decision=UserDecision.SELL,
        source_signal_fingerprint=sig2.analysis_fingerprint,
        signal_timestamp=t0 + timedelta(hours=1),
        risk_plan_fingerprint="r2",
        planned_risk_amount=Decimal("5.00"),
        outcome=XauUsdTradeOutcome.SL_FIRST,
        fill_timestamp=t0 + timedelta(hours=1, minutes=1),
        fill_price=Decimal("2610.00"),
        exit_timestamp=t0 + timedelta(hours=1, minutes=30),
        exit_price=Decimal("2615.00"),
        gross_pnl_per_unit=Decimal("-5.00"),
        net_pnl_per_unit=Decimal("-5.00"),
        gross_r=Decimal("-1.0000"),
        net_r=Decimal("-1.0000"),
    )

    metrics = XauUsdMetricsCalculator.calculate([sig1, sig2], [trade1, trade2])
    assert metrics.candidate_count == 2
    assert metrics.trade_count == 2
    assert metrics.win_count == 1
    assert metrics.loss_count == 1
    assert metrics.win_rate == 0.5
    assert metrics.avg_win_r == 2.0
    assert metrics.avg_loss_r == 1.0
    assert metrics.payoff_ratio == 2.0
    # Expectancy = (0.5 * 2.0) - (0.5 * 1.0) = 0.5
    assert metrics.net_expectancy_r == 0.5
    assert metrics.profit_factor == 2.0


def test_metrics_max_drawdown_calculation():
    """Test max drawdown in R."""
    t0 = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    trades = []
    # Sequence: +2R, -1R, -1R, -1R, +3R
    r_vals = [Decimal("2.0"), Decimal("-1.0"), Decimal("-1.0"), Decimal("-1.0"), Decimal("3.0")]
    for idx, r in enumerate(r_vals):
        trades.append(
            XauUsdSimulatedTrade(
                trade_id=f"t{idx}",
                side=SignalSide.LONG,
                candidate_state=SignalState.BUY_WINDOW,
                candidate_user_decision=UserDecision.BUY,
                source_signal_fingerprint=f"s{idx}",
                signal_timestamp=t0 + timedelta(hours=idx),
                risk_plan_fingerprint=f"r{idx}",
                planned_risk_amount=Decimal("5.00"),
                outcome=XauUsdTradeOutcome.TP1_FIRST if r > 0 else XauUsdTradeOutcome.SL_FIRST,
                fill_timestamp=t0 + timedelta(hours=idx, minutes=1),
                fill_price=Decimal("2600.00"),
                exit_timestamp=t0 + timedelta(hours=idx, minutes=30),
                exit_price=Decimal("2600.00") + r * Decimal("5.00"),
                gross_pnl_per_unit=r * Decimal("5.00"),
                net_pnl_per_unit=r * Decimal("5.00"),
                gross_r=r,
                net_r=r,
            )
        )

    metrics = XauUsdMetricsCalculator.calculate([], trades)
    # Peak is 2.0 -> drops to -1.0 (cumulative) -> drawdown is 3.0 R
    assert metrics.max_drawdown_r == 3.0
    assert metrics.maximum_consecutive_losses == 3
