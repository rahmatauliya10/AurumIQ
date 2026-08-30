"""Persistence and retrieval services for backtest runs and trade ledgers."""
from decimal import Decimal
from typing import Optional, Sequence, Tuple
from django.db import transaction

from apps.backtests.models import BacktestRun, BacktestTrade
from engine.backtest.types import (
    BacktestRunResult,
    BacktestRunSpec,
    SimulatedTrade,
    WalkForwardResult,
)


def _metrics_to_dict(m) -> dict:
    if m is None:
        return {}
    return {
        "signal_count": m.signal_count,
        "execution_eligible_count": m.execution_eligible_count,
        "trade_count": m.trade_count,
        "fill_rate": m.fill_rate,
        "win_count": m.win_count,
        "loss_count": m.loss_count,
        "win_rate": m.win_rate,
        "net_expectancy_r": m.net_expectancy_r,
        "gross_expectancy_r": m.gross_expectancy_r,
        "profit_factor": m.profit_factor,
        "max_trade_sequence_drawdown_r": m.max_trade_sequence_drawdown_r,
        "drawdown_duration_trades": m.drawdown_duration_trades,
        "maximum_consecutive_losses": m.maximum_consecutive_losses,
        "gross_return_pct": float(m.gross_return_pct),
        "net_return_pct": float(m.net_return_pct),
        "cost_drag_r": m.cost_drag_r,
        "cost_drag_pct": m.cost_drag_pct,
        "tp1_first_count": m.tp1_first_count,
        "sl_first_count": m.sl_first_count,
        "conservative_sl_first_count": m.conservative_sl_first_count,
        "unresolved_count": m.unresolved_count,
    }


def _cost_config_to_dict(c) -> dict:
    if c is None:
        return {}
    return {
        "entry_fee_bps": str(c.entry_fee_bps),
        "exit_fee_bps": str(c.exit_fee_bps),
        "synthetic_spread_bps": str(c.synthetic_spread_bps),
        "entry_slippage_bps": str(c.entry_slippage_bps),
        "exit_slippage_bps": str(c.exit_slippage_bps),
    }


def _walkforward_config_to_dict(cfg) -> dict:
    if cfg is None:
        return {}
    return {
        "total_folds": cfg.total_folds,
        "train_ratio": cfg.train_ratio,
        "val_ratio": cfg.val_ratio,
        "oos_ratio": cfg.oos_ratio,
        "embargo_seconds": cfg.embargo_seconds,
        "purge_overlapping_dependencies": cfg.purge_overlapping_dependencies,
        "rolling_window": cfg.rolling_window,
    }


@transaction.atomic
def persist_backtest_run(
    run_result: BacktestRunResult,
    dataset_identity: Optional[str] = None,
) -> Tuple[BacktestRun, bool]:
    """
    Persist a BacktestRunResult idempotently.

    Returns:
        (BacktestRun, created: bool)
    """
    spec = run_result.run_spec
    fp = run_result.run_fingerprint

    existing = BacktestRun.objects.filter(run_fingerprint=fp).first()
    if existing is not None:
        return existing, False

    run_obj = BacktestRun.objects.create(
        run_fingerprint=fp,
        instrument=spec.instrument,
        dataset_identity=dataset_identity or spec.dataset_hash,
        historical_start=spec.start_time,
        historical_end=spec.end_time,
        engine_version=spec.engine_version,
        config_version=spec.config_version,
        feature_version=spec.feature_version,
        cycle_version=spec.cycle_version,
        risk_version=spec.risk_version,
        execution_model_version=spec.execution_model_version,
        backtest_version=spec.backtest_version,
        code_revision=spec.code_revision,
        cost_config=_cost_config_to_dict(spec.cost_config),
        walkforward_config={},
        ablation_id=spec.ablation_type.value,
        aggregate_metrics=_metrics_to_dict(run_result.metrics),
        temporal_stability={},
        status="COMPLETED",
    )

    trade_objs = []
    for t in run_result.trades:
        trade_objs.append(
            BacktestTrade(
                backtest_run=run_obj,
                trade_id=t.trade_id,
                source_signal_fingerprint=t.source_signal_fingerprint,
                signal_timestamp=t.signal_timestamp,
                dependency_end_timestamp=t.dependency_end_timestamp,
                fill_timestamp=t.fill_timestamp,
                fill_price=t.fill_price,
                exit_timestamp=t.exit_timestamp,
                exit_price=t.exit_price,
                outcome=t.outcome.value,
                planned_risk_amount=t.planned_risk_amount,
                gross_r=t.gross_r,
                net_r=t.net_r,
                gross_return_pct=t.gross_return_pct,
                net_return_pct=t.net_return_pct,
                mfe_r=t.mfe_r,
                mae_r=t.mae_r,
                entry_fee=t.entry_fee,
                exit_fee=t.exit_fee,
                entry_spread=t.entry_spread,
                exit_spread=t.exit_spread,
                entry_slippage=t.entry_slippage,
                exit_slippage=t.exit_slippage,
                fold_id=t.fold_id,
                ambiguity_policy=t.ambiguity_policy.value,
            )
        )

    if trade_objs:
        BacktestTrade.objects.bulk_create(trade_objs, batch_size=500)

    return run_obj, True


@transaction.atomic
def persist_walkforward_run(
    wf_result: WalkForwardResult,
    spec: BacktestRunSpec,
    dataset_identity: Optional[str] = None,
) -> Tuple[BacktestRun, bool]:
    """
    Persist a WalkForwardResult idempotently.
    """
    fp = wf_result.walkforward_fingerprint
    existing = BacktestRun.objects.filter(run_fingerprint=fp).first()
    if existing is not None:
        return existing, False

    stab = wf_result.stability_report
    stab_dict = {
        "total_folds": stab.total_folds,
        "positive_expectancy_folds": stab.positive_expectancy_folds,
        "oos_expectancies_r": stab.oos_expectancies_r,
        "oos_profit_factors": stab.oos_profit_factors,
        "oos_drawdowns_r": stab.oos_drawdowns_r,
        "median_oos_expectancy_r": stab.median_oos_expectancy_r,
        "worst_oos_expectancy_r": stab.worst_oos_expectancy_r,
        "best_oos_expectancy_r": stab.best_oos_expectancy_r,
        "is_stable_positive": stab.is_stable_positive,
    }

    run_obj = BacktestRun.objects.create(
        run_fingerprint=fp,
        instrument=spec.instrument,
        dataset_identity=dataset_identity or spec.dataset_hash,
        historical_start=spec.start_time,
        historical_end=spec.end_time,
        engine_version=spec.engine_version,
        config_version=spec.config_version,
        feature_version=spec.feature_version,
        cycle_version=spec.cycle_version,
        risk_version=spec.risk_version,
        execution_model_version=spec.execution_model_version,
        backtest_version=spec.backtest_version,
        code_revision=spec.code_revision,
        cost_config=_cost_config_to_dict(spec.cost_config),
        walkforward_config=_walkforward_config_to_dict(wf_result.config),
        ablation_id=spec.ablation_type.value,
        aggregate_metrics=_metrics_to_dict(stab.aggregate_oos_metrics),
        temporal_stability=stab_dict,
        status="COMPLETED",
    )

    trade_objs = []
    for f in wf_result.folds:
        for t in f.oos_trades:
            trade_objs.append(
                BacktestTrade(
                    backtest_run=run_obj,
                    trade_id=f"f{f.fold_id}-{t.trade_id}",
                    source_signal_fingerprint=t.source_signal_fingerprint,
                    signal_timestamp=t.signal_timestamp,
                    dependency_end_timestamp=t.dependency_end_timestamp,
                    fill_timestamp=t.fill_timestamp,
                    fill_price=t.fill_price,
                    exit_timestamp=t.exit_timestamp,
                    exit_price=t.exit_price,
                    outcome=t.outcome.value,
                    planned_risk_amount=t.planned_risk_amount,
                    gross_r=t.gross_r,
                    net_r=t.net_r,
                    gross_return_pct=t.gross_return_pct,
                    net_return_pct=t.net_return_pct,
                    mfe_r=t.mfe_r,
                    mae_r=t.mae_r,
                    entry_fee=t.entry_fee,
                    exit_fee=t.exit_fee,
                    entry_spread=t.entry_spread,
                    exit_spread=t.exit_spread,
                    entry_slippage=t.entry_slippage,
                    exit_slippage=t.exit_slippage,
                    fold_id=f.fold_id,
                    ambiguity_policy=t.ambiguity_policy.value,
                )
            )

    if trade_objs:
        BacktestTrade.objects.bulk_create(trade_objs, batch_size=500)

    return run_obj, True
