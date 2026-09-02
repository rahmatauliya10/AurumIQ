"""Celery asynchronous tasks for historical backtesting and validation."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

from celery import shared_task

from apps.backtests.models import BacktestRun
from apps.backtests.services import persist_backtest_run, persist_xauusd_backtest_run
from engine.backtest.repository import PointInTimeDataset
from engine.backtest.runner import BacktestRunner
from engine.backtest.types import (
    AblationType,
    BacktestCostConfig,
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
    QuoteData,
)
from engine.risk.xauusd_policy import XauUsdRiskProfile
from engine.signals.profile import Phase4SignalProfile


@shared_task(queue="backtest", bind=True, max_retries=1)
def run_backtest_task(
    self,
    start_time_iso: str,
    end_time_iso: str,
    dataset_hash: str,
    code_revision: str,
    instrument: str = "XAUTUSDT",
    cost_scenario: str = "ZERO_FRICTION",
    entry_fee_bps: Optional[str] = None,
    exit_fee_bps: Optional[str] = None,
    slippage_bps: Optional[str] = None,
    spread_usd: Optional[str] = None,
    ablation_type: str = "BASELINE",
    engine_version: str = "2.0.0",
    config_version: str = "cfg-2026-v1",
    feature_version: str = "feat-2026-v1",
    cycle_version: str = "3.0.0-3A",
    risk_version: str = "1.0.0",
    execution_model_version: str = "1.0.0",
    backtest_version: str = "1.0.0",
) -> dict:
    """
    Asynchronous Celery task for running historical point-in-time backtest for XAUT.
    """
    if not code_revision or not code_revision.strip():
        raise ValueError("Explicit code_revision is strictly required for backtest provenance.")

    start_dt = datetime.fromisoformat(start_time_iso)
    end_dt = datetime.fromisoformat(end_time_iso)

    if start_dt.tzinfo is None or start_dt.tzinfo.utcoffset(start_dt) is None:
        raise ValueError("start_time_iso must include an explicit timezone offset (naive timestamps forbidden).")
    if end_dt.tzinfo is None or end_dt.tzinfo.utcoffset(end_dt) is None:
        raise ValueError("end_time_iso must include an explicit timezone offset (naive timestamps forbidden).")

    if cost_scenario == "REALISTIC":
        cost_cfg = BacktestCostConfig.realistic(
            entry_fee_bps=Decimal(entry_fee_bps or "4.0"),
            exit_fee_bps=Decimal(exit_fee_bps or "4.0"),
            synthetic_spread_bps=Decimal(spread_usd or "5.0"),
            entry_slippage_bps=Decimal(slippage_bps or "2.0"),
            exit_slippage_bps=Decimal(slippage_bps or "2.0"),
        )
    elif cost_scenario == "IDEALIZED" or cost_scenario == "ZERO_FRICTION":
        cost_cfg = BacktestCostConfig.idealized()
        cost_scenario = "IDEALIZED"
    else:
        raise ValueError(f"Unknown cost_scenario: {cost_scenario}")

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
        code_revision=code_revision.strip(),
        ablation_type=AblationType(ablation_type),
    )

    try:
        dataset = PointInTimeDataset()
        from apps.market_data.models import MarketCandle

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
                "instrument": "XAUTUSDT",
                "dataset_identity": dataset_hash,
                "historical_start": start_dt,
                "historical_end": end_dt,
                "code_revision": code_revision,
                "status": "FAILED",
                "error_message": str(exc),
            },
        )
        raise exc


def resolve_xauusd_research_profiles(
    signal_profile_id: Optional[str] = None,
    risk_profile_id: Optional[str] = None,
    calibration_artifact_id: Optional[str] = None,
    signal_profile_dict: Optional[Dict[str, Any]] = None,
    risk_profile_dict: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Phase4SignalProfile], Optional[XauUsdRiskProfile]]:
    """
    Resolve immutable research profiles server-side from identifier or validated JSON-safe dictionary.
    """
    sig_prof: Optional[Phase4SignalProfile] = None
    risk_prof: Optional[XauUsdRiskProfile] = None

    # Server-side ID resolution requires a real persisted empirical calibration artifact.
    # When no empirical artifact exists, unknown / default IDs return None, failing closed.
    if signal_profile_dict:
        # Reconstruct from validated dictionary
        from engine.signals.profile import (
            Phase4CalibrationStatus,
            Phase4FeedPolicy,
            Phase4SignalProfile,
            SideDirectionPolicy,
            SideGatePolicy,
            SideTimingPolicy,
        )
        ld = SideDirectionPolicy(**signal_profile_dict["long_direction"]) if "long_direction" in signal_profile_dict and isinstance(signal_profile_dict["long_direction"], dict) else signal_profile_dict.get("long_direction", SideDirectionPolicy())
        sd = SideDirectionPolicy(**signal_profile_dict["short_direction"]) if "short_direction" in signal_profile_dict and isinstance(signal_profile_dict["short_direction"], dict) else signal_profile_dict.get("short_direction", SideDirectionPolicy())
        lt = SideTimingPolicy(**signal_profile_dict["long_timing"]) if "long_timing" in signal_profile_dict and isinstance(signal_profile_dict["long_timing"], dict) else signal_profile_dict.get("long_timing", SideTimingPolicy())
        st = SideTimingPolicy(**signal_profile_dict["short_timing"]) if "short_timing" in signal_profile_dict and isinstance(signal_profile_dict["short_timing"], dict) else signal_profile_dict.get("short_timing", SideTimingPolicy())
        lg = SideGatePolicy(**signal_profile_dict["long_gate"]) if "long_gate" in signal_profile_dict and isinstance(signal_profile_dict["long_gate"], dict) else signal_profile_dict.get("long_gate", SideGatePolicy())
        sg = SideGatePolicy(**signal_profile_dict["short_gate"]) if "short_gate" in signal_profile_dict and isinstance(signal_profile_dict["short_gate"], dict) else signal_profile_dict.get("short_gate", SideGatePolicy())
        fp = Phase4FeedPolicy(**signal_profile_dict["feed_policy"]) if "feed_policy" in signal_profile_dict and isinstance(signal_profile_dict["feed_policy"], dict) else signal_profile_dict.get("feed_policy", Phase4FeedPolicy())
        cal_status = Phase4CalibrationStatus(signal_profile_dict.get("calibration_status", "CANDIDATE_NOT_FROZEN"))
        sig_prof = Phase4SignalProfile(
            name=signal_profile_dict.get("name", "XAUUSD_RESEARCH"),
            long_direction=ld,
            short_direction=sd,
            long_timing=lt,
            short_timing=st,
            long_gate=lg,
            short_gate=sg,
            feed_policy=fp,
            calibration_status=cal_status,
            details=signal_profile_dict.get("details", {}),
        )

    if risk_profile_dict:
        from engine.risk.xauusd_policy import (
            SideRiskPolicy,
            XauUsdExecutionPolicy,
            XauUsdRiskProfile,
        )
        lr = SideRiskPolicy(**{k: Decimal(str(v)) if isinstance(v, (int, float, str)) else v for k, v in risk_profile_dict["long_risk_policy"].items()}) if "long_risk_policy" in risk_profile_dict and isinstance(risk_profile_dict["long_risk_policy"], dict) else risk_profile_dict.get("long_risk_policy", SideRiskPolicy())
        sr = SideRiskPolicy(**{k: Decimal(str(v)) if isinstance(v, (int, float, str)) else v for k, v in risk_profile_dict["short_risk_policy"].items()}) if "short_risk_policy" in risk_profile_dict and isinstance(risk_profile_dict["short_risk_policy"], dict) else risk_profile_dict.get("short_risk_policy", SideRiskPolicy())
        le = XauUsdExecutionPolicy(**{k: Decimal(str(v)) if k != "latency_seconds" and isinstance(v, (int, float, str)) else v for k, v in risk_profile_dict["long_execution_policy"].items()}) if "long_execution_policy" in risk_profile_dict and isinstance(risk_profile_dict["long_execution_policy"], dict) else risk_profile_dict.get("long_execution_policy", XauUsdExecutionPolicy())
        se = XauUsdExecutionPolicy(**{k: Decimal(str(v)) if k != "latency_seconds" and isinstance(v, (int, float, str)) else v for k, v in risk_profile_dict["short_execution_policy"].items()}) if "short_execution_policy" in risk_profile_dict and isinstance(risk_profile_dict["short_execution_policy"], dict) else risk_profile_dict.get("short_execution_policy", XauUsdExecutionPolicy())
        risk_prof = XauUsdRiskProfile(
            name=risk_profile_dict.get("name", "XAUUSD_RESEARCH"),
            long_risk_policy=lr,
            short_risk_policy=sr,
            long_execution_policy=le,
            short_execution_policy=se,
        )

    return sig_prof, risk_prof


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
    signal_profile_id: Optional[str] = None,
    risk_profile_id: Optional[str] = None,
    calibration_artifact_id: Optional[str] = None,
    signal_profile_dict: Optional[Dict[str, Any]] = None,
    risk_profile_dict: Optional[Dict[str, Any]] = None,
    entry_fee_bps: Optional[str] = None,
    exit_fee_bps: Optional[str] = None,
    synthetic_spread_bps: Optional[str] = None,
    entry_slippage_bps: Optional[str] = None,
    exit_slippage_bps: Optional[str] = None,
    ablation_type: str = "BASELINE",
    holding_horizon_seconds: Optional[float] = None,
    max_fill_wait_seconds: Optional[float] = None,
    execution_policy: str = "NEXT_BAR_OPEN",
    intrabar_policy: str = "LOWER_TIMEFRAME_REPLAY",
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
    Payload arguments must be native JSON-serializable primitives (strings, ints, dicts).
    """
    if not code_revision or not code_revision.strip():
        raise ValueError("Explicit code_revision is strictly required for XAUUSD backtest provenance.")

    start_dt = datetime.fromisoformat(start_time_iso)
    end_dt = datetime.fromisoformat(end_time_iso)

    if start_dt.tzinfo is None or start_dt.tzinfo.utcoffset(start_dt) is None:
        raise ValueError("start_time_iso must include an explicit timezone offset (naive timestamps forbidden).")
    if end_dt.tzinfo is None or end_dt.tzinfo.utcoffset(end_dt) is None:
        raise ValueError("end_time_iso must include an explicit timezone offset (naive timestamps forbidden).")

    # Resolve research profiles server-side
    signal_profile, risk_profile = resolve_xauusd_research_profiles(
        signal_profile_id=signal_profile_id,
        risk_profile_id=risk_profile_id,
        calibration_artifact_id=calibration_artifact_id,
        signal_profile_dict=signal_profile_dict,
        risk_profile_dict=risk_profile_dict,
    )

    # If caller has not supplied configured profiles, return CALIBRATION_REQUIRED without fake completion
    if signal_profile is None or risk_profile is None:
        return {
            "status": "CALIBRATION_REQUIRED",
            "message": "Explicit calibrated signal_profile and risk_profile are required before executing empirical backtest.",
        }

    exec_policy_enum = EntryExecutionPolicy(execution_policy)
    intrabar_policy_enum = IntrabarPolicy(intrabar_policy)

    if cost_scenario == "EMPIRICAL":
        if (
            entry_fee_bps is None
            or exit_fee_bps is None
            or synthetic_spread_bps is None
            or entry_slippage_bps is None
            or exit_slippage_bps is None
        ):
            raise ValueError(
                "cost_scenario EMPIRICAL strictly requires all 5 friction parameters: "
                "entry_fee_bps, exit_fee_bps, synthetic_spread_bps, entry_slippage_bps, exit_slippage_bps."
            )
        cost_cfg = XauUsdCostConfig.empirical(
            entry_fee_bps=Decimal(str(entry_fee_bps)),
            exit_fee_bps=Decimal(str(exit_fee_bps)),
            synthetic_spread_bps=Decimal(str(synthetic_spread_bps)),
            entry_slippage_bps=Decimal(str(entry_slippage_bps)),
            exit_slippage_bps=Decimal(str(exit_slippage_bps)),
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
        execution_policy=exec_policy_enum,
        intrabar_policy=intrabar_policy_enum,
        engine_version=engine_version,
        config_version=config_version,
        feature_version=feature_version,
        cycle_version=cycle_version,
        risk_version=risk_version,
        execution_model_version=execution_model_version,
        backtest_version=backtest_version,
        code_revision=code_revision.strip(),
        ablation_type=XauUsdAblationType(ablation_type),
        signal_profile=signal_profile,
        risk_profile=risk_profile,
    )

    try:
        dataset = PointInTimeDataset()
        from apps.market_data.models import MarketCandle

        # Load XAUUSD candles
        for tf in ("15m", "1h", "4h", "1d", "1m", "5m"):
            candles_qs = MarketCandle.objects.filter(
                timeframe=tf,
                timestamp_close__gte=start_dt,
                timestamp_close__lt=end_dt,
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

        from engine.backtest.xauusd_fingerprint import compute_xauusd_dataset_identity_from_dataset
        computed_dataset_hash = compute_xauusd_dataset_identity_from_dataset(dataset, start_dt, end_dt)
        if dataset_hash != computed_dataset_hash:
            raise ValueError(f"dataset_hash mismatch: expected '{dataset_hash}', computed '{computed_dataset_hash}'")

        if exec_policy_enum == EntryExecutionPolicy.MARKET_AFTER_SIGNAL:
            quotes = dataset.get_quotes(start_dt, end_dt)
            if not quotes:
                return {
                    "status": "EVIDENCE_NOT_CONFIGURED",
                    "message": "Quotes evidence required for MARKET_AFTER_SIGNAL execution policy is not configured or persisted.",
                }

        runner = XauUsdBacktestRunner(
            execution_policy=exec_policy_enum,
            intrabar_policy=intrabar_policy_enum,
        )
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
