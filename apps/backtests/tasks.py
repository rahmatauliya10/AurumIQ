"""Celery background tasks for asynchronous backtest execution."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from celery import shared_task

from apps.backtests.models import BacktestRun
from apps.backtests.services import persist_backtest_run
from engine.backtest.repository import PointInTimeDataset
from engine.backtest.runner import BacktestRunner
from engine.backtest.types import (
    AblationType,
    BacktestCostConfig,
    BacktestRunSpec,
    CostScenario,
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
    engine_version: str = "4.0.0",
    config_version: str = "cfg-2026-v1",
    feature_version: str = "feat-2026-v1",
    cycle_version: str = "3.0.0-3A",
    risk_version: str = "5.0.0",
    execution_model_version: str = "5.0.0-exec-v1",
    backtest_version: str = "6.0.0",
) -> dict:
    """
    Asynchronous Celery task for running a point-in-time backtest.
    Requires explicitly pinned code_revision and version identifiers.
    """
    if not code_revision:
        raise ValueError("Explicit code_revision is strictly required for backtest provenance.")

    start_dt = datetime.fromisoformat(start_time_iso)
    end_dt = datetime.fromisoformat(end_time_iso)

    cost_cfg = (
        BacktestCostConfig.realistic()
        if cost_scenario == "REALISTIC"
        else BacktestCostConfig.idealized()
    )

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

        parts = instrument.split("/")
        base_filter = {}
        if len(parts) == 2:
            base_filter["instrument__base_asset__code"] = parts[0]
            base_filter["instrument__quote_asset__code"] = parts[1]

        # 1. Load multi-timeframe execution candles for target instrument
        for tf in ("15m", "4h", "1d", "1m", "5m"):
            candles_qs = MarketCandle.objects.filter(
                timeframe=tf,
                timestamp_close__gte=start_dt,
                timestamp_close__lte=end_dt,
                is_closed=True,
                **base_filter,
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
                        quote_rate=c.quote_rate,
                        close_usd=c.close_usd,
                        source_id=c.source,
                    ),
                )

        # 2. Load XAU reference candles if available
        xau_qs = MarketCandle.objects.filter(
            instrument__base_asset__code="XAU",
            instrument__quote_asset__code="USD",
            timestamp_close__gte=start_dt,
            timestamp_close__lte=end_dt,
            is_closed=True,
        ).order_by("timestamp_close")

        for c in xau_qs:
            dataset.add_xau_candle(
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
                )
            )

        # 3. Load USDT normalization series
        for c in MarketCandle.objects.filter(
            timeframe="15m",
            timestamp_close__gte=start_dt,
            timestamp_close__lte=end_dt,
            is_closed=True,
            **base_filter,
        ).exclude(quote_rate__isnull=True).order_by("timestamp_close"):
            dataset.add_usdt_rate(c.timestamp_close, c.quote_rate)

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

