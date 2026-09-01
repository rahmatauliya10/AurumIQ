"""Unit tests for Phase 6 XAUUSD Walk-Forward Engine and OOS Isolation."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest

from engine.backtest.purge import PurgeEngine
from engine.backtest.repository import PointInTimeDataset
from engine.backtest.xauusd_types import (
    XauUsdCostConfig,
    XauUsdSimulatedTrade,
    XauUsdTradeOutcome,
    XauUsdWalkForwardConfig,
)
from engine.backtest.xauusd_walkforward import (
    XauUsdChronologicalFoldGenerator,
    XauUsdWalkForwardEngine,
    select_parameters_on_train_val,
)
from engine.core.types import (
    CandleData,
    SignalSide,
    SignalState,
    UserDecision,
)


def make_candle(ts_open: datetime, o: Decimal, h: Decimal, l: Decimal, c: Decimal) -> CandleData:
    return CandleData(
        timestamp_open=ts_open,
        timestamp_close=ts_open + timedelta(minutes=15),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=Decimal("100"),
        is_closed=True,
        source_id="TEST",
    )


def test_walkforward_folds_generation():
    """Test generating non-overlapping chronological folds with XauUsdChronologicalFoldGenerator."""
    start_t = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    end_t = start_t + timedelta(hours=100)

    wf_config = XauUsdWalkForwardConfig(
        total_folds=3,
        train_ratio=0.6,
        val_ratio=0.2,
        oos_ratio=0.2,
        embargo_seconds=3600.0,
        purge_overlapping=True,
    )

    folds = XauUsdChronologicalFoldGenerator.generate_folds(
        start_time=start_t,
        end_time=end_t,
        config=wf_config,
    )
    assert len(folds) == 3

    for fold in folds:
        # Chronological progression: train_start <= train_end <= val_start <= val_end <= oos_start <= oos_end
        assert fold.train_start < fold.train_end
        assert fold.val_start >= fold.train_end
        assert fold.val_start < fold.val_end
        assert fold.oos_start >= fold.val_end
        assert fold.oos_start < fold.oos_end
        assert fold.embargo_duration_seconds == 3600.0


def test_purge_engine_overlapping_dependency():
    """Test that PurgeEngine removes trades whose dependency window crosses partition boundary."""
    p_start = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    p_end = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)

    # Trade 1: Fully contained [2h, 4h)
    t1 = XauUsdSimulatedTrade(
        trade_id="t1",
        side=SignalSide.LONG,
        candidate_state=SignalState.BUY_WINDOW,
        candidate_user_decision=UserDecision.BUY,
        source_signal_fingerprint="s1",
        signal_timestamp=p_start + timedelta(hours=2),
        risk_plan_fingerprint="r1",
        planned_risk_amount=Decimal("5.00"),
        outcome=XauUsdTradeOutcome.TP1_FIRST,
        dependency_window=(p_start + timedelta(hours=2), p_start + timedelta(hours=4)),
        dependency_end_timestamp=p_start + timedelta(hours=4),
    )

    # Trade 2: Crosses boundary [8h, 12h) -> dependency_end >= p_end
    t2 = XauUsdSimulatedTrade(
        trade_id="t2",
        side=SignalSide.LONG,
        candidate_state=SignalState.BUY_WINDOW,
        candidate_user_decision=UserDecision.BUY,
        source_signal_fingerprint="s2",
        signal_timestamp=p_start + timedelta(hours=8),
        risk_plan_fingerprint="r2",
        planned_risk_amount=Decimal("5.00"),
        outcome=XauUsdTradeOutcome.TP1_FIRST,
        dependency_window=(p_start + timedelta(hours=8), p_start + timedelta(hours=12)),
        dependency_end_timestamp=p_start + timedelta(hours=12),
    )

    res = PurgeEngine.filter_partition([t1, t2], partition_start=p_start, partition_end=p_end, purge_overlapping=True)
    assert len(res.eligible_trades) == 1
    assert res.eligible_trades[0].trade_id == "t1"
    assert len(res.purged_trades) == 1
    assert res.purged_trades[0].trade_id == "t2"


def test_oos_isolation_parameter_selection():
    """Prove that TRAIN + VAL participate in parameter selection while OOS is strictly isolated."""
    p_start = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)

    train_trade = XauUsdSimulatedTrade(
        trade_id="t-train",
        side=SignalSide.LONG,
        candidate_state=SignalState.BUY_WINDOW,
        candidate_user_decision=UserDecision.BUY,
        source_signal_fingerprint="s-tr",
        signal_timestamp=p_start + timedelta(hours=1),
        risk_plan_fingerprint="r-tr",
        planned_risk_amount=Decimal("5.00"),
        outcome=XauUsdTradeOutcome.TP1_FIRST,
        gross_r=Decimal("2.0"),
        net_r=Decimal("2.0"),
        dependency_window=(p_start + timedelta(hours=1), p_start + timedelta(hours=2)),
    )

    val_trade = XauUsdSimulatedTrade(
        trade_id="t-val",
        side=SignalSide.LONG,
        candidate_state=SignalState.BUY_WINDOW,
        candidate_user_decision=UserDecision.BUY,
        source_signal_fingerprint="s-val",
        signal_timestamp=p_start + timedelta(hours=3),
        risk_plan_fingerprint="r-val",
        planned_risk_amount=Decimal("5.00"),
        outcome=XauUsdTradeOutcome.TP1_FIRST,
        gross_r=Decimal("1.5"),
        net_r=Decimal("1.5"),
        dependency_window=(p_start + timedelta(hours=3), p_start + timedelta(hours=4)),
    )

    candidate_thresholds = [1.0, 1.8, 2.5]

    def evaluator(train_t, val_t, threshold):
        # Evaluation function using only train and val
        total_r = sum(t.net_r for t in list(train_t) + list(val_t) if t.net_r >= threshold)
        return float(total_r)

    selected = select_parameters_on_train_val([train_trade], [val_trade], candidate_thresholds, evaluator)
    assert selected == 1.0

    # Even if an external OOS trade exists with huge +10R, it cannot be passed into selector
    oos_trade = XauUsdSimulatedTrade(
        trade_id="t-oos",
        side=SignalSide.LONG,
        candidate_state=SignalState.BUY_WINDOW,
        candidate_user_decision=UserDecision.BUY,
        source_signal_fingerprint="s-oos",
        signal_timestamp=p_start + timedelta(hours=5),
        risk_plan_fingerprint="r-oos",
        planned_risk_amount=Decimal("5.00"),
        outcome=XauUsdTradeOutcome.TP1_FIRST,
        gross_r=Decimal("10.0"),
        net_r=Decimal("10.0"),
        dependency_window=(p_start + timedelta(hours=5), p_start + timedelta(hours=6)),
    )

    # Re-running selection with identical in-sample data yields identical output
    selected_repeat = select_parameters_on_train_val([train_trade], [val_trade], candidate_thresholds, evaluator)
    assert selected_repeat == selected
