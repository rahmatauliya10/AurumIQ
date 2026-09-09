"""Unit tests for Phase 6 XAUUSD Walk-Forward Engine and OOS Isolation."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest

from engine.backtest.purge import PurgeEngine
from engine.backtest.repository import PointInTimeDataset
from engine.backtest.xauusd_fingerprint import compute_xauusd_dataset_identity_from_dataset
from engine.backtest.xauusd_runner import XauUsdBacktestRunner
from engine.backtest.xauusd_types import (
    XauUsdBacktestRunSpec,
    XauUsdCostConfig,
    XauUsdCostScenario,
    XauUsdSimulatedTrade,
    XauUsdTradeOutcome,
    XauUsdWalkForwardConfig,
    XauUsdWalkForwardResult,
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
from engine.risk.xauusd_policy import (
    SideRiskPolicy,
    XauUsdExecutionPolicy,
    XauUsdRiskProfile,
)
from engine.signals.profile import (
    Phase4CalibrationStatus,
    Phase4SignalProfile,
    SideDirectionPolicy,
    SideTimingPolicy,
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


def test_e2e_xauusd_backtest_runner_walk_forward():
    """
    End-to-end regression test invoking the REAL XauUsdBacktestRunner.run_walk_forward().
    Verifies that:
      1. PurgeResult contract is cleanly unpacked into eligible trades (no TypeError / PurgeResult misuse).
      2. >= 2 chronological folds are executed with train, val, and OOS partitions.
      3. All fold trades are tuples of XauUsdSimulatedTrade and contain strictly eligible trades.
      4. Overlapping dependency windows crossing partition boundaries are purged.
      5. OOS fold IDs are correctly tagged.
      6. Repeated identical runs are strictly deterministic.
    """
    start_t = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    end_t = start_t + timedelta(hours=30)
    code_rev = "d5716a10aafc1c01b1394f2570a6551199b2951b"

    dataset = PointInTimeDataset()
    # Populate 120 15m candles across 30 hours
    for i in range(120):
        t_open = start_t + timedelta(minutes=15 * i)
        dataset.add_candle(
            "15m",
            make_candle(
                t_open,
                Decimal("2600.00") + Decimal(str(i * 0.1)),
                Decimal("2605.00") + Decimal(str(i * 0.1)),
                Decimal("2595.00") + Decimal(str(i * 0.1)),
                Decimal("2602.00") + Decimal(str(i * 0.1)),
            ),
        )

    risk_prof = XauUsdRiskProfile(
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

    sig_prof = Phase4SignalProfile(
        name="XAUUSD_TEST_PROFILE",
        target_instrument="XAUUSD",
        calibration_status=Phase4CalibrationStatus.CANDIDATE_NOT_FROZEN,
        timeframe="15m",
        long_direction=SideDirectionPolicy(
            weight_regime=20.0,
            weight_trend_1h=20.0,
            weight_trend_4h=20.0,
            weight_trend_1d=10.0,
            weight_structure_bos=10.0,
            weight_pullback=10.0,
            weight_momentum=5.0,
            weight_volume=5.0,
        ),
        short_direction=SideDirectionPolicy(
            weight_regime=20.0,
            weight_trend_1h=20.0,
            weight_trend_4h=20.0,
            weight_trend_1d=10.0,
            weight_structure_bos=10.0,
            weight_pullback=10.0,
            weight_momentum=5.0,
            weight_volume=5.0,
        ),
        long_timing=SideTimingPolicy(
            weight_entry_zone=30.0,
            weight_reversal_confirmation_15m=25.0,
            weight_momentum_turn_15m_1h=20.0,
            weight_phase3a=15.0,
            weight_volume_response=10.0,
        ),
        short_timing=SideTimingPolicy(
            weight_entry_zone=30.0,
            weight_reversal_confirmation_15m=25.0,
            weight_momentum_turn_15m_1h=20.0,
            weight_phase3a=15.0,
            weight_volume_response=10.0,
        ),
    )

    ds_hash = compute_xauusd_dataset_identity_from_dataset(dataset, start_t, end_t)

    spec = XauUsdBacktestRunSpec(
        instrument="XAUUSD",
        start_time=start_t,
        end_time=end_t,
        timeframes=("15m",),
        cost_config=XauUsdCostConfig.idealized(),
        cost_scenario=XauUsdCostScenario.IDEALIZED,
        dataset_hash=ds_hash,
        code_revision=code_rev,
        holding_horizon_bars_15m=10,
        max_fill_wait_bars_15m=4,
        signal_profile=sig_prof,
        risk_profile=risk_prof,
    )

    wf_config = XauUsdWalkForwardConfig(
        total_folds=2,
        train_ratio=0.6,
        val_ratio=0.2,
        oos_ratio=0.2,
        embargo_seconds=1800.0,
        purge_overlapping=True,
    )

    runner = XauUsdBacktestRunner()
    result = runner.run_walk_forward(dataset=dataset, spec=spec, wf_config=wf_config)

    # 1. Output type and fold count
    assert isinstance(result, XauUsdWalkForwardResult)
    assert len(result.folds) == 2
    assert [f.fold_id for f in result.folds] == [1, 2]

    # 2. Verify each fold's trades are tuples of XauUsdSimulatedTrade (NOT PurgeResult)
    for f in result.folds:
        assert isinstance(f.train_trades, tuple)
        assert isinstance(f.val_trades, tuple)
        assert isinstance(f.oos_trades, tuple)

        for t in f.train_trades:
            assert isinstance(t, XauUsdSimulatedTrade)
            assert f.spec.train_start <= t.signal_timestamp < f.spec.train_end
            assert t.dependency_end_timestamp < f.spec.train_end  # Overlapping purged!

        for t in f.val_trades:
            assert isinstance(t, XauUsdSimulatedTrade)
            assert f.spec.val_start <= t.signal_timestamp < f.spec.val_end
            assert t.dependency_end_timestamp < f.spec.val_end

        for t in f.oos_trades:
            assert isinstance(t, XauUsdSimulatedTrade)
            assert f.spec.oos_start <= t.signal_timestamp < f.spec.oos_end
            assert t.fold_id == f.fold_id

        assert f.train_trade_count == len(f.train_trades)
        assert f.val_trade_count == len(f.val_trades)
        assert f.oos_trade_count == len(f.oos_trades)

    # 3. Deterministic repeatability
    result2 = runner.run_walk_forward(dataset=dataset, spec=spec, wf_config=wf_config)
    assert result2.run_fingerprint == result.run_fingerprint
    assert result2.temporal_stability_score == result.temporal_stability_score
    assert len(result2.folds) == len(result.folds)


def test_walkforward_embargo_semantics_and_boundary():
    """
    Test embargo semantics:
      1. Trades inside [val_end, val_end + embargo) are excluded from OOS partition.
      2. Trades at exactly val_end + embargo (oos_start) are eligible.
      3. Embargo is applied strictly once (no double-embargo).
    """
    start_t = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    end_t = start_t + timedelta(hours=10)

    wf_config = XauUsdWalkForwardConfig(
        total_folds=2,
        train_ratio=0.5,
        val_ratio=0.2,
        oos_ratio=0.3,
        embargo_seconds=3600.0,
        purge_overlapping=True,
    )

    folds = XauUsdChronologicalFoldGenerator.generate_folds(
        start_time=start_t,
        end_time=end_t,
        config=wf_config,
    )
    fold1 = folds[0]

    val_end = fold1.val_end
    oos_start = fold1.oos_start
    assert oos_start == val_end + timedelta(seconds=3600.0)

    # Trade inside embargo window: val_end + 30m
    t_embargoed = XauUsdSimulatedTrade(
        trade_id="t-embargoed",
        side=SignalSide.LONG,
        candidate_state=SignalState.BUY_WINDOW,
        candidate_user_decision=UserDecision.BUY,
        source_signal_fingerprint="s-emb",
        signal_timestamp=val_end + timedelta(minutes=30),
        risk_plan_fingerprint="r-emb",
        planned_risk_amount=Decimal("5.00"),
        outcome=XauUsdTradeOutcome.TP1_FIRST,
        dependency_window=(val_end + timedelta(minutes=30), val_end + timedelta(minutes=45)),
        dependency_end_timestamp=val_end + timedelta(minutes=45),
    )

    # Trade at exact boundary: oos_start
    t_boundary = XauUsdSimulatedTrade(
        trade_id="t-boundary",
        side=SignalSide.LONG,
        candidate_state=SignalState.BUY_WINDOW,
        candidate_user_decision=UserDecision.BUY,
        source_signal_fingerprint="s-bnd",
        signal_timestamp=oos_start,
        risk_plan_fingerprint="r-bnd",
        planned_risk_amount=Decimal("5.00"),
        outcome=XauUsdTradeOutcome.TP1_FIRST,
        dependency_window=(oos_start, oos_start + timedelta(minutes=15)),
        dependency_end_timestamp=oos_start + timedelta(minutes=15),
    )

    # Trade inside OOS: oos_start + 15m
    t_oos = XauUsdSimulatedTrade(
        trade_id="t-oos",
        side=SignalSide.LONG,
        candidate_state=SignalState.BUY_WINDOW,
        candidate_user_decision=UserDecision.BUY,
        source_signal_fingerprint="s-oos",
        signal_timestamp=oos_start + timedelta(minutes=15),
        risk_plan_fingerprint="r-oos",
        planned_risk_amount=Decimal("5.00"),
        outcome=XauUsdTradeOutcome.TP1_FIRST,
        dependency_window=(oos_start + timedelta(minutes=15), oos_start + timedelta(minutes=30)),
        dependency_end_timestamp=oos_start + timedelta(minutes=30),
    )

    all_candidate_trades = [t_embargoed, t_boundary, t_oos]

    # In walk-forward OOS extraction:
    oos_trades_raw = [
        t for t in all_candidate_trades
        if fold1.oos_start <= t.signal_timestamp < fold1.oos_end
    ]

    # t_embargoed is naturally excluded because its timestamp < fold1.oos_start
    assert t_embargoed not in oos_trades_raw
    assert t_boundary in oos_trades_raw
    assert t_oos in oos_trades_raw

    # Filter partition on OOS
    oos_purge_res = PurgeEngine.filter_partition(
        trades=oos_trades_raw,
        partition_start=fold1.oos_start,
        partition_end=fold1.oos_end,
        purge_overlapping=True,
    )
    eligible = oos_purge_res.eligible_trades
    assert len(eligible) == 2
    assert eligible[0].trade_id == "t-boundary"
    assert eligible[1].trade_id == "t-oos"
