"""Celery asynchronous tasks for executing historical backtests and walk-forward evaluations."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from celery import shared_task

from apps.backtests.models import BacktestRun
from apps.backtests.services import persist_backtest_run, persist_xauusd_backtest_run
from engine.backtest.clock import ReplayClock
from engine.backtest.metrics import BacktestMetricsCalculator
from engine.backtest.outcomes import OutcomeEngine
from engine.backtest.replay import PointInTimeReplay
from engine.backtest.repository import PointInTimeDataset
from engine.backtest.runner import BacktestRunner
from engine.backtest.types import (
    AblationType,
    BacktestCostConfig,
    BacktestRunResult,
    BacktestRunSpec,
    CostScenario,
)
from engine.backtest.xauusd_fingerprint import compute_xauusd_dataset_identity
from engine.backtest.xauusd_runner import XauUsdBacktestRunner
from engine.backtest.xauusd_types import (
    XauUsdAblationType,
    XauUsdBacktestRunSpec,
    XauUsdCostConfig,
    XauUsdCostScenario,
)
from engine.core.types import (
    CandleData,
    EntryExecutionPolicy,
    IntrabarPolicy,
    SignalState,
    UserDecision,
)


@shared_task(queue="backtest", bind=True, max_retries=1)
def run_backtest_task(
    self,
    instrument: str,
    start_time_iso: str,
    end_time_iso: str,
    dataset_hash: str,
    code_revision: str,
    cost_scenario: str = "IDEALIZED",
    ablation_type: str = "BASELINE",
    engine_version: str = "2.0.0-frozen",
    config_version: str = "cfg-2026-v1",
    feature_version: str = "feat-2026-v1",
    cycle_version: str = "3.0.0-3A",
    risk_version: str = "1.0.0-frozen",
    execution_model_version: str = "1.0.0-exec-v1",
    backtest_version: str = "1.0.0-bt-v1",
) -> dict:
    """
    Asynchronous Celery task for running a point-in-time backtest for historical XAUT baseline.
    """
    if not code_revision:
        raise ValueError("Explicit code_revision is strictly required for backtest execution provenance.")

    start_dt = datetime.fromisoformat(start_time_iso)
    end_dt = datetime.fromisoformat(end_time_iso)

    if cost_scenario == "EMPIRICAL":
        cost_cfg = BacktestCostConfig.empirical()
    else:
        cost_cfg = BacktestCostConfig.idealized()

    spec = BacktestRunSpec(
        instrument=instrument,
        start_time=start_dt,
        end_time=end_dt,
        timeframes=("15m", "1h", "4h", "1d"),
        cost_config=cost_cfg,
        cost_scenario=CostScenario(cost_scenario),
        dataset_hash=dataset_hash,
        engine_version=engine_version,
        config_version=config_version,
        feature_version=feature_version,
        cycle_version=cycle_version,
        risk_version=risk_version,
        execution_model_version=execution_model_version,
        backtest_version=backtest_version,
        code_revision=code_revision,
        ablation_type=AblationType(ablation_type),
    )

    try:
        dataset = PointInTimeDataset()
        from apps.market_data.models import MarketCandle
        from engine.core.types import CandleData

        # Load closed candles from DB
        for tf in ("15m", "1h", "4h", "1d"):
            candles_qs = MarketCandle.objects.filter(
                timeframe=tf,
                timestamp_close__gte=start_dt,
                timestamp_close__lte=end_dt,
                is_closed=True,
            ).order_by("timestamp_close")

            for c in candles_qs:
                dataset.add_candle(
                    tf,
                    CandleData(
                        timestamp_open=c.timestamp_open,
                        timestamp_close=c.timestamp_close,
                        open=c.open,
                        high=c.high,
                        low=c.low,
                        close=c.close,
                        volume=c.volume,
                        is_closed=c.is_closed,
                        source_id=c.source,
                    ),
                )

        runner = BacktestRunner()
        result = runner.run(dataset=dataset, spec=spec)
        run_obj, created = persist_backtest_run(run_result=result, dataset_identity=dataset_hash)

        return {
            "status": "COMPLETED",
            "run_fingerprint": run_obj.run_fingerprint,
            "created": created,
            "net_expectancy_r": result.metrics.net_expectancy_r,
            "trade_count": result.metrics.trade_count,
        }
    except Exception as exc:
        BacktestRun.objects.update_or_create(
            run_fingerprint=f"failed-{dataset_hash[:16]}-{code_revision[:8]}",
            defaults={
                "instrument": instrument,
                "dataset_identity": dataset_hash,
                "historical_start": start_dt,
                "historical_end": end_dt,
                "code_revision": code_revision,
                "status": "FAILED",
                "error_message": str(exc),
            },
        )
        raise exc


@shared_task(queue="backtest", bind=True, max_retries=1)
def run_xauusd_backtest_task(
    self,
    start_time_iso: str,
    end_time_iso: str,
    dataset_hash: str,
    code_revision: str,
    cost_scenario: str,  # REQUIRED: No silent default
    holding_horizon_bars_15m: int,  # REQUIRED: Explicit horizon
    max_fill_wait_bars_15m: int,  # REQUIRED: Explicit fill-search horizon
    entry_fee_bps: Optional[str] = None,
    exit_fee_bps: Optional[str] = None,
    synthetic_spread_bps: Optional[str] = None,
    entry_slippage_bps: Optional[str] = None,
    exit_slippage_bps: Optional[str] = None,
    ablation_type: str = "BASELINE",
    holding_horizon_seconds: Optional[float] = None,
    max_fill_wait_seconds: Optional[float] = None,
    engine_version: str = "4.0.0-xauusd",
    config_version: str = "cfg-xauusd-2026-v1",
    feature_version: str = "feat-xauusd-2026-v1",
    cycle_version: str = "3.0.0-3A",
    risk_version: str = "5.0.0-xauusd",
    execution_model_version: str = "5.0.0-exec-v1",
    backtest_version: str = "6.0.0-xauusd",
) -> dict:
    """
    Asynchronous Celery task for running a point-in-time backtest for canonical XAUUSD.
    """
    if not code_revision or not code_revision.strip():
        raise ValueError("Explicit code_revision is strictly required for XAUUSD backtest provenance.")

    start_dt = datetime.fromisoformat(start_time_iso)
    end_dt = datetime.fromisoformat(end_time_iso)

    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)

    if cost_scenario == "EMPIRICAL":
        cost_cfg = XauUsdCostConfig.empirical(
            entry_fee_bps=Decimal(entry_fee_bps or "0.0"),
            exit_fee_bps=Decimal(exit_fee_bps or "0.0"),
            synthetic_spread_bps=Decimal(synthetic_spread_bps or "0.0"),
            entry_slippage_bps=Decimal(entry_slippage_bps or "0.0"),
            exit_slippage_bps=Decimal(exit_slippage_bps or "0.0"),
        )
    elif cost_scenario == "IDEALIZED":
        cost_cfg = XauUsdCostConfig.idealized()
    else:
        raise ValueError(f"Unknown cost_scenario: {cost_scenario}")

    spec = XauUsdBacktestRunSpec(
        instrument="XAUUSD",
        start_time=start_dt,
        end_time=end_dt,
        timeframes=("15m", "1h", "4h", "1d"),
        cost_config=cost_cfg,
        cost_scenario=XauUsdCostScenario(cost_scenario),
        dataset_hash=dataset_hash,
        holding_horizon_bars_15m=holding_horizon_bars_15m,
        holding_horizon_seconds=holding_horizon_seconds,
        max_fill_wait_bars_15m=max_fill_wait_bars_15m,
        max_fill_wait_seconds=max_fill_wait_seconds,
        engine_version=engine_version,
        config_version=config_version,
        feature_version=feature_version,
        cycle_version=cycle_version,
        risk_version=risk_version,
        execution_model_version=execution_model_version,
        backtest_version=backtest_version,
        code_revision=code_revision.strip(),
        ablation_type=XauUsdAblationType(ablation_type),
    )

    try:
        dataset = PointInTimeDataset()
        from apps.market_data.models import MarketCandle

        # Load XAUUSD candles
        for tf in ("15m", "1h", "4h", "1d", "1m", "5m"):
            candles_qs = MarketCandle.objects.filter(
                timeframe=tf,
                timestamp_close__gte=start_dt,
                timestamp_close__lte=end_dt,
                is_closed=True,
                instrument__base_asset__code="XAU",
                instrument__quote_asset__code="USD",
            ).order_by("timestamp_close")

            for c in candles_qs:
                dataset.add_candle(
                    tf,
                    CandleData(
                        timestamp_open=c.timestamp_open,
                        timestamp_close=c.timestamp_close,
                        open=c.open,
                        high=c.high,
                        low=c.low,
                        close=c.close,
                        volume=c.volume,
                        is_closed=c.is_closed,
                        source_id=c.source,
                    ),
                )

        computed_dataset_hash = compute_xauusd_dataset_identity(
            candles_15m=dataset.get_closed_candles("15m", as_of=end_dt),
            start_time=start_dt,
            end_time=end_dt,
            candles_4h=dataset.get_closed_candles("4h", as_of=end_dt),
            candles_1d=dataset.get_closed_candles("1d", as_of=end_dt),
            candles_5m=getattr(dataset, "_candles", {}).get("5m", []),
            candles_1m=getattr(dataset, "_candles", {}).get("1m", []),
        )

        if dataset_hash and dataset_hash != computed_dataset_hash:
            raise ValueError(f"dataset_hash mismatch: expected '{dataset_hash}', computed '{computed_dataset_hash}'")

        # Update spec with verified dataset hash
        spec = XauUsdBacktestRunSpec(
            instrument="XAUUSD",
            start_time=start_dt,
            end_time=end_dt,
            timeframes=("15m", "1h", "4h", "1d"),
            cost_config=cost_cfg,
            cost_scenario=XauUsdCostScenario(cost_scenario),
            dataset_hash=computed_dataset_hash,
            holding_horizon_bars_15m=holding_horizon_bars_15m,
            holding_horizon_seconds=holding_horizon_seconds,
            max_fill_wait_bars_15m=max_fill_wait_bars_15m,
            max_fill_wait_seconds=max_fill_wait_seconds,
            engine_version=engine_version,
            config_version=config_version,
            feature_version=feature_version,
            cycle_version=cycle_version,
            risk_version=risk_version,
            execution_model_version=execution_model_version,
            backtest_version=backtest_version,
            code_revision=code_revision.strip(),
            ablation_type=XauUsdAblationType(ablation_type),
        )

        runner = XauUsdBacktestRunner()
        metrics, trades, signals, run_fp = runner.run_point_in_time(dataset=dataset, spec=spec)
        run_obj, created = persist_xauusd_backtest_run(
            spec=spec,
            metrics=metrics,
            trades=trades,
            run_fingerprint=run_fp,
            dataset_identity=computed_dataset_hash,
        )

        return {
            "status": "COMPLETED",
            "run_fingerprint": run_obj.run_fingerprint,
            "created": created,
            "net_expectancy_r": metrics.net_expectancy_r,
            "trade_count": metrics.trade_count,
            "long_trade_count": metrics.long_trade_count,
            "short_trade_count": metrics.short_trade_count,
        }
    except Exception as exc:
        BacktestRun.objects.update_or_create(
            run_fingerprint=f"failed-xauusd-{dataset_hash[:16]}-{code_revision[:8]}",
            defaults={
                "instrument": "XAUUSD",
                "dataset_identity": dataset_hash,
                "historical_start": start_dt,
                "historical_end": end_dt,
                "code_revision": code_revision,
                "status": "FAILED",
                "error_message": str(exc),
            },
        )
        raise exc
