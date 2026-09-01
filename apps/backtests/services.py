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
from engine.backtest.xauusd_types import (
    XauUsdBacktestMetrics,
    XauUsdBacktestRunSpec,
    XauUsdSimulatedTrade,
    XauUsdWalkForwardResult,
)


def _metrics_to_dict(m) -> dict:
    if m is None:
        return {}
    return {
        "signal_count": getattr(m, "signal_count", 0),
        "execution_eligible_count": getattr(m, "execution_eligible_count", 0),
        "trade_count": getattr(m, "trade_count", 0),
        "fill_rate": getattr(m, "fill_rate", 0.0),
        "win_count": getattr(m, "win_count", 0),
        "loss_count": getattr(m, "loss_count", 0),
        "win_rate": getattr(m, "win_rate", 0.0),
        "net_expectancy_r": getattr(m, "net_expectancy_r", 0.0),
        "gross_expectancy_r": getattr(m, "gross_expectancy_r", 0.0),
        "profit_factor": getattr(m, "profit_factor", 0.0),
        "max_trade_sequence_drawdown_r": getattr(m, "max_trade_sequence_drawdown_r", getattr(m, "max_drawdown_r", 0.0)),
        "drawdown_duration_trades": getattr(m, "drawdown_duration_trades", 0),
        "maximum_consecutive_losses": getattr(m, "maximum_consecutive_losses", 0),
        "gross_return_pct": float(getattr(m, "gross_return_pct", 0.0)),
        "net_return_pct": float(getattr(m, "net_return_pct", 0.0)),
        "cost_drag_r": getattr(m, "cost_drag_r", 0.0),
        "cost_drag_pct": getattr(m, "cost_drag_pct", 0.0),
        "tp1_first_count": getattr(m, "tp1_first_count", 0),
        "sl_first_count": getattr(m, "sl_first_count", 0),
        "conservative_sl_first_count": getattr(m, "conservative_sl_first_count", 0),
        "unresolved_count": getattr(m, "unresolved_count", 0),
        "timeout_count": getattr(m, "timeout_count", 0),
    }


def _cost_config_to_dict(c) -> dict:
    if c is None:
        return {}
    return {
        "entry_fee_bps": str(getattr(c, "entry_fee_bps", "0.0")),
        "exit_fee_bps": str(getattr(c, "exit_fee_bps", "0.0")),
        "synthetic_spread_bps": str(getattr(c, "synthetic_spread_bps", "0.0")),
        "entry_slippage_bps": str(getattr(c, "entry_slippage_bps", "0.0")),
        "exit_slippage_bps": str(getattr(c, "exit_slippage_bps", "0.0")),
    }


def _walkforward_config_to_dict(cfg) -> dict:
    if cfg is None:
        return {}
    return {
        "total_folds": getattr(cfg, "total_folds", 0),
        "train_ratio": getattr(cfg, "train_ratio", 0.0),
        "val_ratio": getattr(cfg, "val_ratio", 0.0),
        "oos_ratio": getattr(cfg, "oos_ratio", 0.0),
        "embargo_seconds": getattr(cfg, "embargo_seconds", 0.0),
        "purge_overlapping": getattr(cfg, "purge_overlapping", True),
        "rolling_window": getattr(cfg, "rolling_window", False),
    }


@transaction.atomic
def persist_backtest_run(
    run_result: BacktestRunResult,
    dataset_identity: Optional[str] = None,
) -> Tuple[BacktestRun, bool]:
    """
    Persist a historical BacktestRunResult idempotently.
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
        ablation_id=spec.ablation_type.value if hasattr(spec.ablation_type, "value") else str(spec.ablation_type),
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
                side=getattr(t, "side", "LONG").value if hasattr(getattr(t, "side", "LONG"), "value") else str(getattr(t, "side", "LONG")),
                source_signal_fingerprint=t.source_signal_fingerprint,
                signal_timestamp=t.signal_timestamp,
                dependency_end_timestamp=t.dependency_end_timestamp,
                fill_timestamp=t.fill_timestamp,
                fill_price=t.fill_price,
                exit_timestamp=t.exit_timestamp,
                exit_price=t.exit_price,
                outcome=t.outcome.value if hasattr(t.outcome, "value") else str(t.outcome),
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
                ambiguity_policy=t.ambiguity_policy.value if hasattr(t.ambiguity_policy, "value") else str(t.ambiguity_policy),
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
    Persist a historical WalkForwardResult idempotently.
    """
    fp = getattr(wf_result, "walkforward_fingerprint", None) or getattr(wf_result, "run_fingerprint", "")
    existing = BacktestRun.objects.filter(run_fingerprint=fp).first()
    if existing is not None:
        return existing, False

    stab = getattr(wf_result, "stability_report", None)
    stab_dict = {}
    if stab is not None:
        stab_dict = {
            "total_folds": stab.total_folds,
            "positive_expectancy_folds": stab.positive_expectancy_folds,
            "median_oos_expectancy_r": stab.median_oos_expectancy_r,
            "worst_oos_expectancy_r": stab.worst_oos_expectancy_r,
            "best_oos_expectancy_r": stab.best_oos_expectancy_r,
            "is_stable_positive": stab.is_stable_positive,
            "oos_expectancies_r": list(stab.oos_expectancies_r),
            "oos_profit_factors": list(stab.oos_profit_factors),
            "oos_drawdowns_r": list(stab.oos_drawdowns_r),
        }

    agg_m = getattr(stab, "aggregate_oos_metrics", None) or getattr(wf_result, "oos_aggregated_metrics", None)

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
        walkforward_config=_walkforward_config_to_dict(getattr(wf_result, "config", None) or getattr(wf_result, "wf_config", None)),
        ablation_id=spec.ablation_type.value if hasattr(spec.ablation_type, "value") else str(spec.ablation_type),
        aggregate_metrics=_metrics_to_dict(agg_m),
        temporal_stability=stab_dict,
        status="COMPLETED",
    )

    return run_obj, True


@transaction.atomic
def persist_xauusd_backtest_run(
    spec: XauUsdBacktestRunSpec,
    metrics: XauUsdBacktestMetrics,
    trades: Sequence[XauUsdSimulatedTrade],
    run_fingerprint: str,
    dataset_identity: Optional[str] = None,
) -> Tuple[BacktestRun, bool]:
    """
    Persist an XAUUSD backtest execution idempotently.
    """
    existing = BacktestRun.objects.filter(run_fingerprint=run_fingerprint).first()
    if existing is not None:
        return existing, False

    run_obj = BacktestRun.objects.create(
        run_fingerprint=run_fingerprint,
        instrument="XAUUSD",
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
        ablation_id=spec.ablation_type.value if hasattr(spec.ablation_type, "value") else str(spec.ablation_type),
        aggregate_metrics=_metrics_to_dict(metrics),
        temporal_stability={},
        status="COMPLETED",
    )

    trade_objs = []
    for t in trades:
        trade_objs.append(
            BacktestTrade(
                backtest_run=run_obj,
                trade_id=t.trade_id,
                side=t.side.value if hasattr(t.side, "value") else str(t.side),
                candidate_state=t.candidate_state.value if hasattr(t.candidate_state, "value") else str(t.candidate_state),
                candidate_decision=t.candidate_user_decision.value if hasattr(t.candidate_user_decision, "value") else str(t.candidate_user_decision),
                source_signal_fingerprint=t.source_signal_fingerprint,
                risk_plan_fingerprint=t.risk_plan_fingerprint,
                execution_evidence_fingerprint=t.execution_evidence_fingerprint or "",
                signal_timestamp=t.signal_timestamp,
                dependency_end_timestamp=t.dependency_end_timestamp,
                fill_timestamp=t.fill_timestamp,
                fill_price=t.fill_price,
                exit_timestamp=t.exit_timestamp,
                exit_price=t.exit_price,
                outcome=t.outcome.value if hasattr(t.outcome, "value") else str(t.outcome),
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
                ambiguity_policy=t.ambiguity_policy.value if hasattr(t.ambiguity_policy, "value") else str(t.ambiguity_policy),
            )
        )

    if trade_objs:
        BacktestTrade.objects.bulk_create(trade_objs, batch_size=500)

    return run_obj, True


@transaction.atomic
def persist_xauusd_walkforward_run(
    wf_result: XauUsdWalkForwardResult,
    spec: XauUsdBacktestRunSpec,
    dataset_identity: Optional[str] = None,
) -> Tuple[BacktestRun, bool]:
    """
    Persist an XAUUSD WalkForwardResult idempotently.
    """
    fp = wf_result.run_fingerprint
    existing = BacktestRun.objects.filter(run_fingerprint=fp).first()
    if existing is not None:
        return existing, False

    stab_dict = {
        "total_folds": len(wf_result.folds),
        "temporal_stability_score": wf_result.temporal_stability_score,
        "fold_expectancies_r": list(wf_result.fold_expectancies_r),
    }

    run_obj = BacktestRun.objects.create(
        run_fingerprint=fp,
        instrument="XAUUSD",
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
        walkforward_config=_walkforward_config_to_dict(wf_result.wf_config),
        ablation_id=spec.ablation_type.value if hasattr(spec.ablation_type, "value") else str(spec.ablation_type),
        aggregate_metrics=_metrics_to_dict(wf_result.oos_aggregated_metrics),
        temporal_stability=stab_dict,
        status="COMPLETED",
    )

    return run_obj, True
