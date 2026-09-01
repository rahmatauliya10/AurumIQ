"""Walk-forward execution, strict OOS parameter isolation, and temporal stability reporting for XAUUSD."""
import statistics
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from engine.backtest.clock import ReplayClock
from engine.backtest.purge import PurgeEngine
from engine.backtest.repository import PointInTimeDataset
from engine.backtest.xauusd_fingerprint import compute_xauusd_walkforward_fingerprint
from engine.backtest.xauusd_metrics import XauUsdMetricsCalculator
from engine.backtest.xauusd_outcomes import XauUsdOutcomeEngine
from engine.backtest.xauusd_replay import XauUsdPointInTimeReplay
from engine.backtest.xauusd_types import (
    XauUsdBacktestMetrics,
    XauUsdBacktestRunSpec,
    XauUsdFoldResult,
    XauUsdFoldSpec,
    XauUsdSimulatedTrade,
    XauUsdWalkForwardConfig,
    XauUsdWalkForwardResult,
)
from engine.core.types import (
    DualSideSignalSnapshot,
    EntryExecutionPolicy,
    IntrabarPolicy,
)
from engine.risk.xauusd_planner import XauUsdRiskPlanner
from engine.signals.engine import XauUsdSignalEngine


def _require_utc(dt: datetime, param_name: str = "timestamp") -> datetime:
    """Validate that datetime is explicitly timezone aware and convert to UTC."""
    if dt is None or dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ValueError(f"{param_name} must be timezone-aware with non-None utcoffset (naive timestamps forbidden).")
    return dt.astimezone(timezone.utc)


class XauUsdChronologicalFoldGenerator:
    """
    Chronological fold generator strictly adhering to XAUUSD walk-forward ratios and boundaries.
    All intervals are half-open: [start, end). Zero random shuffling.

    Deterministic Partitioning Algorithm:
      Given total duration D = end_time - start_time and ratios (r_train, r_val, r_oos) summing to 1.0:
      1. Base allocation:
         W_train = D * r_train
         W_val = D * r_val
         Delta_oos = (D * r_oos) / total_folds
      2. For each fold k in [1, total_folds] (0-indexed i = k - 1):
         shift_i = i * Delta_oos
         - If rolling_window=False (Expanding Window):
             train_start = start_time
             train_end = start_time + W_train + shift_i
         - If rolling_window=True (Rolling Window):
             train_start = start_time + shift_i
             train_end = train_start + W_train
         - If r_val > 0:
             val_start = train_end
             val_end = val_start + W_val
             oos_start = val_end + embargo
         - If r_val == 0:
             val_start = None, val_end = None
             oos_start = train_end + embargo
         oos_end = min(oos_start + Delta_oos, end_time)
    """

    @classmethod
    def generate_folds(
        cls,
        start_time: datetime,
        end_time: datetime,
        config: XauUsdWalkForwardConfig,
    ) -> List[XauUsdFoldSpec]:
        start_utc = _require_utc(start_time, "start_time")
        end_utc = _require_utc(end_time, "end_time")

        if start_utc >= end_utc:
            raise ValueError(f"start_time ({start_utc}) must be before end_time ({end_utc})")

        total_duration = end_utc - start_utc
        k = config.total_folds
        folds: List[XauUsdFoldSpec] = []

        w_train = total_duration * config.train_ratio
        w_val = total_duration * config.val_ratio
        delta_oos = (total_duration * config.oos_ratio) / k

        for fold_idx in range(1, k + 1):
            i = fold_idx - 1
            shift_i = delta_oos * i

            if config.rolling_window:
                f_train_start = start_utc + shift_i
                f_train_end = f_train_start + w_train
            else:
                f_train_start = start_utc
                f_train_end = start_utc + w_train + shift_i

            if config.val_ratio > 0.0:
                f_val_start = f_train_end
                f_val_end = f_val_start + w_val
                f_oos_start = f_val_end + timedelta(seconds=config.embargo_seconds)
            else:
                f_val_start = None
                f_val_end = None
                f_oos_start = f_train_end + timedelta(seconds=config.embargo_seconds)

            f_oos_end = min(f_oos_start + delta_oos, end_utc)

            folds.append(
                XauUsdFoldSpec(
                    fold_id=fold_idx,
                    train_start=f_train_start,
                    train_end=f_train_end,
                    val_start=f_val_start,
                    val_end=f_val_end,
                    oos_start=f_oos_start,
                    oos_end=f_oos_end,
                    embargo_duration_seconds=config.embargo_seconds,
                )
            )

        return folds


def select_parameters_on_train_val(
    train_trades: Sequence[XauUsdSimulatedTrade],
    val_trades: Sequence[XauUsdSimulatedTrade],
    candidate_configurations: Sequence[Any],
    evaluator_fn: Callable[[Sequence[XauUsdSimulatedTrade], Sequence[XauUsdSimulatedTrade], Any], float],
) -> Any:
    """
    Select optimal candidate parameter set strictly using in-sample (TRAIN + VAL) data.
    OOS data is strictly excluded and cannot be passed to this selection function.
    """
    if not candidate_configurations:
        raise ValueError("candidate_configurations must be non-empty.")

    best_score = float("-inf")
    best_config = candidate_configurations[0]

    for cfg in candidate_configurations:
        score = evaluator_fn(train_trades, val_trades, cfg)
        if score > best_score:
            best_score = score
            best_config = cfg

    return best_config


class XauUsdWalkForwardEngine:
    """
    Executes chronological multi-fold walk-forward validation for XAUUSD.

    Strict Invariants:
      1. Chronological Non-Overlapping Folds: Folds progress strictly forward in time.
      2. Half-open intervals: [start, end).
      3. OOS Isolation: OOS partition data never leaks into train/validation evaluation.
      4. Purge & Embargo: Dependency window purges and post-boundary embargos are strictly applied.
      5. Side-Specific Policy Parity: LONG and SHORT execute using their respective Phase 5 policies.
    """

    def __init__(
        self,
        signal_engine: Optional[XauUsdSignalEngine] = None,
        risk_planner: Optional[XauUsdRiskPlanner] = None,
        outcome_engine: Optional[XauUsdOutcomeEngine] = None,
        execution_policy: EntryExecutionPolicy = EntryExecutionPolicy.NEXT_BAR_OPEN,
        intrabar_policy: IntrabarPolicy = IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
    ):
        self.signal_engine = signal_engine
        self.risk_planner = risk_planner
        self.outcome_engine = outcome_engine
        self.execution_policy = execution_policy
        self.intrabar_policy = intrabar_policy

    def run(
        self,
        dataset: PointInTimeDataset,
        spec: XauUsdBacktestRunSpec,
        wf_config: XauUsdWalkForwardConfig,
    ) -> XauUsdWalkForwardResult:
        """
        Execute chronological walk-forward validation on XAUUSD dataset.
        """
        _require_utc(spec.start_time, "spec.start_time")
        _require_utc(spec.end_time, "spec.end_time")

        fold_specs = XauUsdChronologicalFoldGenerator.generate_folds(
            start_time=spec.start_time,
            end_time=spec.end_time,
            config=wf_config,
        )

        sig_engine = self.signal_engine or XauUsdSignalEngine(
            code_revision=spec.code_revision,
            engine_version=spec.engine_version,
            feature_version=spec.feature_version,
            cycle_version=spec.cycle_version,
        )

        risk_plan = self.risk_planner or XauUsdRiskPlanner(
            code_revision=spec.code_revision,
            risk_profile=spec.risk_profile,
            risk_version=spec.risk_version,
        )

        outcome_eng = self.outcome_engine or XauUsdOutcomeEngine(
            cost_config=spec.cost_config,
            holding_horizon_bars_15m=spec.holding_horizon_bars_15m,
            holding_horizon_seconds=spec.holding_horizon_seconds,
            max_fill_wait_bars_15m=spec.max_fill_wait_bars_15m,
            max_fill_wait_seconds=spec.max_fill_wait_seconds,
            code_revision=spec.code_revision,
            long_execution_policy=risk_plan.risk_profile.long_execution_policy,
            short_execution_policy=risk_plan.risk_profile.short_execution_policy,
            phase5_policy_fingerprint=risk_plan.policy_fingerprint,
        )

        run_fp = compute_xauusd_walkforward_fingerprint(
            spec=spec,
            wf_config=wf_config,
            fold_specs=fold_specs,
        )

        # Run Replay across entire dataset window [start_time, end_time)
        full_candles_15m = dataset.get_closed_candles("15m", as_of=spec.end_time)
        if not full_candles_15m:
            raise ValueError(f"No 15m closed candles available in dataset for {spec.instrument}.")

        timestamps = [c.timestamp_close for c in full_candles_15m if spec.start_time <= c.timestamp_close < spec.end_time]
        if not timestamps:
            timestamps = [spec.start_time]

        clock = ReplayClock(timestamps)
        replay = XauUsdPointInTimeReplay(
            dataset=dataset,
            signal_engine=sig_engine,
            risk_planner=risk_plan,
            outcome_engine=outcome_eng,
            execution_policy=self.execution_policy,
            intrabar_policy=self.intrabar_policy,
            signal_profile=spec.signal_profile,
            run_fingerprint=run_fp,
            holding_horizon_bars_15m=spec.holding_horizon_bars_15m,
            holding_horizon_seconds=spec.holding_horizon_seconds,
            max_fill_wait_bars_15m=spec.max_fill_wait_bars_15m,
            max_fill_wait_seconds=spec.max_fill_wait_seconds,
            cost_config=spec.cost_config,
            run_end_time=spec.end_time,
        )

        all_signals, all_trades = replay.run(clock)

        fold_results: List[XauUsdFoldResult] = []
        all_oos_trades: List[XauUsdSimulatedTrade] = []

        for f_spec in fold_specs:
            # 1. Train Partition Evaluation
            train_trades_raw = [
                t for t in all_trades
                if f_spec.train_start <= t.signal_timestamp < f_spec.train_end
            ]
            train_purged = PurgeEngine.filter_partition(
                trades=train_trades_raw,
                partition_start=f_spec.train_start,
                partition_end=f_spec.train_end,
                purge_overlapping=wf_config.purge_overlapping,
            )
            train_metrics = XauUsdMetricsCalculator.calculate(
                signals=[s for s in all_signals if f_spec.train_start <= s.timestamp < f_spec.train_end],
                trades=train_purged,
            )

            # 2. Validation Partition Evaluation
            val_purged: List[XauUsdSimulatedTrade] = []
            val_metrics = None
            if f_spec.val_start and f_spec.val_end:
                val_trades_raw = [
                    t for t in all_trades
                    if f_spec.val_start <= t.signal_timestamp < f_spec.val_end
                ]
                val_purged = PurgeEngine.filter_partition(
                    trades=val_trades_raw,
                    partition_start=f_spec.val_start,
                    partition_end=f_spec.val_end,
                    purge_overlapping=wf_config.purge_overlapping,
                )
                val_metrics = XauUsdMetricsCalculator.calculate(
                    signals=[s for s in all_signals if f_spec.val_start <= s.timestamp < f_spec.val_end],
                    trades=val_purged,
                )

            # 3. OOS Partition Evaluation (with post-boundary embargo)
            oos_trades_raw = [
                t for t in all_trades
                if f_spec.oos_start <= t.signal_timestamp < f_spec.oos_end
            ]
            oos_purged = PurgeEngine.filter_partition(
                trades=oos_trades_raw,
                partition_start=f_spec.oos_start,
                partition_end=f_spec.oos_end,
                purge_overlapping=wf_config.purge_overlapping,
            )
            oos_metrics = XauUsdMetricsCalculator.calculate(
                signals=[s for s in all_signals if f_spec.oos_start <= s.timestamp < f_spec.oos_end],
                trades=oos_purged,
            )

            # Tag OOS trades with fold_id
            tagged_oos_trades = [
                XauUsdSimulatedTrade(
                    trade_id=t.trade_id,
                    side=t.side,
                    candidate_state=t.candidate_state,
                    candidate_user_decision=t.candidate_user_decision,
                    source_signal_fingerprint=t.source_signal_fingerprint,
                    signal_timestamp=t.signal_timestamp,
                    risk_plan_fingerprint=t.risk_plan_fingerprint,
                    planned_risk_amount=t.planned_risk_amount,
                    outcome=t.outcome,
                    fill_timestamp=t.fill_timestamp,
                    fill_price=t.fill_price,
                    exit_timestamp=t.exit_timestamp,
                    exit_price=t.exit_price,
                    dependency_end_timestamp=t.dependency_end_timestamp,
                    gross_pnl_per_unit=t.gross_pnl_per_unit,
                    net_pnl_per_unit=t.net_pnl_per_unit,
                    gross_r=t.gross_r,
                    net_r=t.net_r,
                    gross_return_pct=t.gross_return_pct,
                    net_return_pct=t.net_return_pct,
                    mfe_r=t.mfe_r,
                    mae_r=t.mae_r,
                    holding_duration_seconds=t.holding_duration_seconds,
                    entry_fee=t.entry_fee,
                    exit_fee=t.exit_fee,
                    entry_spread=t.entry_spread,
                    exit_spread=t.exit_spread,
                    entry_slippage=t.entry_slippage,
                    exit_slippage=t.exit_slippage,
                    regime=t.regime,
                    session=t.session,
                    cycle_phase=t.cycle_phase,
                    ambiguity_policy=t.ambiguity_policy,
                    fold_id=f_spec.fold_id,
                    run_fingerprint=run_fp,
                    execution_evidence_fingerprint=t.execution_evidence_fingerprint,
                    dependency_window=t.dependency_window,
                    tp2_reached_after_tp1=t.tp2_reached_after_tp1,
                    max_favorable_extension_r=t.max_favorable_extension_r,
                )
                for t in oos_purged
            ]
            all_oos_trades.extend(tagged_oos_trades)

            fold_results.append(
                XauUsdFoldResult(
                    fold_id=f_spec.fold_id,
                    spec=f_spec,
                    train_metrics=train_metrics,
                    val_metrics=val_metrics,
                    oos_metrics=oos_metrics,
                    train_trade_count=len(train_purged),
                    val_trade_count=len(val_purged),
                    oos_trade_count=len(tagged_oos_trades),
                    train_trades=tuple(train_purged),
                    val_trades=tuple(val_purged),
                    oos_trades=tuple(tagged_oos_trades),
                )
            )

        # 4. Aggregated OOS Metrics
        all_oos_signals = [
            s for s in all_signals
            if any(f.spec.oos_start <= s.timestamp < f.spec.oos_end for f in fold_results)
        ]
        oos_aggregated_metrics = XauUsdMetricsCalculator.calculate(
            signals=all_oos_signals,
            trades=all_oos_trades,
        )

        fold_expectancies = tuple(f.oos_metrics.net_expectancy_r for f in fold_results)

        # 5. Temporal Stability Score
        if len(fold_expectancies) > 1:
            mean_exp = statistics.mean(fold_expectancies)
            stdev_exp = statistics.stdev(fold_expectancies)
            stability_score = round(max(0.0, 1.0 - (stdev_exp / (abs(mean_exp) + 1.0))), 4)
        else:
            stability_score = 1.0 if fold_expectancies and fold_expectancies[0] > 0 else 0.0

        return XauUsdWalkForwardResult(
            wf_config=wf_config,
            run_fingerprint=run_fp,
            folds=tuple(fold_results),
            oos_aggregated_metrics=oos_aggregated_metrics,
            temporal_stability_score=stability_score,
            fold_expectancies_r=fold_expectancies,
        )
