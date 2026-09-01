"""Unit tests for Phase 6 XAUUSD Walk-Forward Engine."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest

from engine.backtest.folds import ChronologicalFoldGenerator
from engine.backtest.purge import PurgeEngine
from engine.backtest.repository import PointInTimeDataset
from engine.backtest.xauusd_fingerprint import compute_xauusd_dataset_identity
from engine.backtest.xauusd_types import (
    XauUsdBacktestRunSpec,
    XauUsdCostConfig,
    XauUsdCostScenario,
    XauUsdSimulatedTrade,
    XauUsdTradeOutcome,
    XauUsdWalkForwardConfig,
)
from engine.backtest.xauusd_walkforward import XauUsdWalkForwardEngine
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
    """Test generating non-overlapping chronological folds."""
    start_t = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    end_t = start_t + timedelta(hours=100)

    wf_config = XauUsdWalkForwardConfig(
        total_folds=3,
        train_ratio=0.6,
        val_ratio=0.2,
        embargo_seconds=3600.0,
        purge_overlapping=True,
    )

    folds = ChronologicalFoldGenerator.generate_folds(
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
    )

    res = PurgeEngine.filter_partition([t1, t2], partition_start=p_start, partition_end=p_end, purge_overlapping=True)
    assert len(res.eligible_trades) == 1
    assert res.eligible_trades[0].trade_id == "t1"
    assert len(res.purged_trades) == 1
    assert res.purged_trades[0].trade_id == "t2"
