"""Tests for Django persistence of backtest runs, trade ledgers, and Celery tasks (Phase 6C)."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest

from apps.backtests.models import BacktestRun, BacktestTrade
from apps.backtests.services import persist_backtest_run, persist_walkforward_run
from apps.backtests.tasks import run_backtest_task
from engine.backtest.metrics import BacktestMetricsCalculator
from engine.backtest.types import (
    AblationType,
    BacktestCostConfig,
    BacktestRunResult,
    BacktestRunSpec,
    CostScenario,
    FoldDataResult,
    FoldSpec,
    SimulatedTrade,
    TemporalStabilityReport,
    TradeOutcome,
    WalkForwardConfig,
    WalkForwardResult,
)
from engine.core.types import SignalSnapshot, SignalState, UserDecision


@pytest.mark.django_db
def test_persist_backtest_run_creates_records_and_trades():
    """Verify persist_backtest_run creates BacktestRun and child BacktestTrade records."""
    t_start = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    t_end = datetime(2026, 3, 1, 14, 0, tzinfo=timezone.utc)

    spec = BacktestRunSpec(
        instrument="XAUT/USDT",
        start_time=t_start,
        end_time=t_end,
        timeframes=("15m",),
        cost_config=BacktestCostConfig.idealized(),
        cost_scenario=CostScenario.IDEALIZED,
        dataset_hash="dataset-db-test-1",
        code_revision="6ac79ab7597e58fee7e9b9e3d02bc50d06c9feee",
    )

    trade = SimulatedTrade(
        trade_id="t-db-1",
        source_signal_fingerprint="sig-db-1",
        signal_timestamp=t_start,
        risk_plan_fingerprint="r-db-1",
        planned_risk_amount=Decimal("10.00"),
        outcome=TradeOutcome.TP1_FIRST,
        fill_timestamp=t_start + timedelta(minutes=15),
        fill_price=Decimal("2000.00"),
        exit_timestamp=t_start + timedelta(minutes=30),
        exit_price=Decimal("2020.00"),
        gross_r=Decimal("2.0000"),
        net_r=Decimal("2.0000"),
        gross_pnl_per_unit=Decimal("20.00"),
        net_pnl_per_unit=Decimal("20.00"),
        dependency_window=(t_start, t_start + timedelta(minutes=30)),
    )

    metrics = BacktestMetricsCalculator.calculate([], [trade])
    run_res = BacktestRunResult(
        run_spec=spec,
        run_fingerprint="fp-db-run-001",
        metrics=metrics,
        trades=(trade,),
        signals=(),
    )

    run_obj, created = persist_backtest_run(run_res, dataset_identity="dataset-db-test-1")

    assert created is True
    assert run_obj.run_fingerprint == "fp-db-run-001"
    assert run_obj.trades.count() == 1

    t_db = run_obj.trades.first()
    assert t_db.trade_id == "t-db-1"
    assert t_db.outcome == "TP1_FIRST"
    assert t_db.net_r == Decimal("2.0000")


@pytest.mark.django_db
def test_persist_backtest_run_is_idempotent():
    """Duplicate run results with identical fingerprint return existing object without creating new records."""
    t_start = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    spec = BacktestRunSpec(
        instrument="XAUT/USDT",
        start_time=t_start,
        end_time=t_start + timedelta(hours=2),
        timeframes=("15m",),
        cost_config=BacktestCostConfig.idealized(),
        cost_scenario=CostScenario.IDEALIZED,
        dataset_hash="dataset-idem-1",
        code_revision="6ac79ab7597e58fee7e9b9e3d02bc50d06c9feee",
    )

    metrics = BacktestMetricsCalculator.calculate([], [])
    run_res = BacktestRunResult(
        run_spec=spec,
        run_fingerprint="fp-idempotent-001",
        metrics=metrics,
        trades=(),
        signals=(),
    )

    obj1, created1 = persist_backtest_run(run_res)
    assert created1 is True

    obj2, created2 = persist_backtest_run(run_res)
    assert created2 is False
    assert obj1.id == obj2.id
    assert BacktestRun.objects.filter(run_fingerprint="fp-idempotent-001").count() == 1


@pytest.mark.django_db
def test_persist_walkforward_run():
    """Verify persist_walkforward_run persists fold data and cross-fold stability summary."""
    t_start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    t_end = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)

    spec = BacktestRunSpec(
        instrument="XAUT/USDT",
        start_time=t_start,
        end_time=t_end,
        timeframes=("15m",),
        cost_config=BacktestCostConfig.idealized(),
        cost_scenario=CostScenario.IDEALIZED,
        dataset_hash="dataset-wf-1",
        code_revision="6ac79ab7597e58fee7e9b9e3d02bc50d06c9feee",
    )

    m = BacktestMetricsCalculator.calculate([], [])
    fold = FoldDataResult(
        fold_id=1,
        spec=FoldSpec(
            fold_id=1,
            train_start=t_start,
            train_end=t_start + timedelta(days=60),
            oos_start=t_start + timedelta(days=60),
            oos_end=t_end,
        ),
        train_metrics=m,
        oos_metrics=m,
        train_trades=(),
        oos_trades=(),
    )
    stab = TemporalStabilityReport(
        total_folds=1,
        positive_expectancy_folds=0,
        oos_expectancies_r=(0.0,),
        oos_profit_factors=(1.0,),
        oos_drawdowns_r=(0.0,),
        median_oos_expectancy_r=0.0,
        worst_oos_expectancy_r=0.0,
        best_oos_expectancy_r=0.0,
        aggregate_oos_metrics=m,
        is_stable_positive=False,
    )
    wf_res = WalkForwardResult(
        config=WalkForwardConfig(),
        folds=(fold,),
        stability_report=stab,
        walkforward_fingerprint="fp-wf-persisted-001",
    )

    run_obj, created = persist_walkforward_run(wf_res, spec)
    assert created is True
    assert run_obj.run_fingerprint == "fp-wf-persisted-001"
    assert "total_folds" in run_obj.temporal_stability


@pytest.mark.django_db
def test_celery_run_backtest_task_execution():
    """Verify run_backtest_task executes synchronously in testing and returns status."""
    from apps.instruments.models import Asset, Instrument, InstrumentType
    from apps.market_data.models import MarketCandle

    xaut, _ = Asset.objects.get_or_create(code="XAUT", defaults={"name": "Tether Gold"})
    usdt, _ = Asset.objects.get_or_create(code="USDT", defaults={"name": "Tether USD"})
    inst, _ = Instrument.objects.get_or_create(base_asset=xaut, quote_asset=usdt, defaults={"instrument_type": InstrumentType.SPOT})

    t_base = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    for i in range(16):
        MarketCandle.objects.create(
            instrument=inst,
            source="binance",
            timeframe="15m",
            timestamp_open=t_base + timedelta(minutes=15 * i),
            timestamp_close=t_base + timedelta(minutes=15 * (i + 1)),
            open=Decimal("2000.00"),
            high=Decimal("2010.00"),
            low=Decimal("1995.00"),
            close=Decimal("2005.00"),
            volume=Decimal("100.00"),
            is_closed=True,
        )

    res = run_backtest_task(
        instrument="XAUT/USDT",
        start_time_iso="2026-03-01T10:00:00+00:00",
        end_time_iso="2026-03-01T14:00:00+00:00",
        dataset_hash="task-dataset-hash",
        code_revision="6ac79ab7597e58fee7e9b9e3d02bc50d06c9feee",
        cost_scenario="IDEALIZED",
        ablation_type="BASELINE",
    )

    assert res["status"] == "COMPLETED"
    assert "run_fingerprint" in res
    assert BacktestRun.objects.filter(run_fingerprint=res["run_fingerprint"]).exists()
