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

        if k == 1:
            train_dur = total_duration * config.train_ratio
            val_dur = total_duration * config.val_ratio
            oos_dur = total_duration * config.oos_ratio

            t_end = start_utc + train_dur
            v_start = t_end
            v_end = v_start + val_dur if config.val_ratio > 0.0 else None
            o_start = (v_end if v_end is not None else t_end) + timedelta(seconds=config.embargo_seconds)
            o_end = end_utc

            folds.append(
                XauUsdFoldSpec(
                    fold_id=1,
                    train_start=start_utc,
                    train_end=t_end,
                    val_start=v_start if config.val_ratio > 0.0 else None,
                    val_end=v_end if config.val_ratio > 0.0 else None,
                    oos_start=o_start,
                    oos_end=o_end,
                    embargo_duration_seconds=config.embargo_seconds,
                )
            )
            return folds

        # Multi-fold chronological walk-forward
        # Each fold advances the test segment chronologically
        oos_step = total_duration / (k + 2)  # Segment-based chunking
        train_window = oos_step * 2

        for fold_idx in range(1, k + 1):
            if config.rolling_window:
                f_train_start = start_utc + (oos_step * (fold_idx - 1))
            else:
                f_train_start = start_utc

            f_train_end = f_train_start + train_window
            if config.val_ratio > 0.0:
                f_val_start = f_train_end
                f_val_end = f_val_start + (oos_step * 0.5)
                f_oos_start = f_val_end + timedelta(seconds=config.embargo_seconds)
            else:
                f_val_start = None
                f_val_end = None
                f_oos_start = f_train_end + timedelta(seconds=config.embargo_seconds)

            f_oos_end = min(f_oos_start + oos_step, end_utc)

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
    Executes chronological walk-forward analysis with label purging, embargo, and strict OOS isolation for XAUUSD.

    Strict Invariants:
      1. Chronological order strictly enforced across all folds (no random shuffle).
      2. Half-open intervals: [start, end).
      3. Exact dependency windows are purged across partition boundaries.
      4. Post-boundary embargo excluded from subsequent partition evaluation.
      5. OOS Isolation: candidate selection API cannot accept OOS data.
      6. Generates descriptive temporal stability report without arbitrary pass/fail filters.
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

    def run_walkforward(
        self,
        dataset: PointInTimeDataset,
        spec: XauUsdBacktestRunSpec,
        wf_config: Optional[XauUsdWalkForwardConfig] = None,
    ) -> XauUsdWalkForwardResult:
        """
        Execute chronological walk-forward validation across all folds.
        Requires explicit caller-supplied wf_config.
        """
        if wf_config is None:
            raise ValueError("wf_config must be explicitly provided (legacy defaults removed).")

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

        run_fp = compute_xauusd_walkforward_fingerprint(
            spec=spec,
            wf_config=wf_config,
            fold_specs=fold_specs,
        )

        # Run Replay across entire dataset window
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
            outcome_engine=self.outcome_engine,
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
                trades=train_purged.eligible_trades,
            )

            # 2. Validation Partition Evaluation (if configured)
            val_metrics: Optional[XauUsdBacktestMetrics] = None
            val_trades_list: Tuple[XauUsdSimulatedTrade, ...] = ()
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
                val_trades_list = tuple(val_purged.eligible_trades)
                val_metrics = XauUsdMetricsCalculator.calculate(
                    signals=[s for s in all_signals if f_spec.val_start <= s.timestamp < f_spec.val_end],
                    trades=val_purged.eligible_trades,
                )

            # 3. OOS Partition Evaluation (with Embargo applied)
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
                trades=oos_purged.eligible_trades,
            )

            fold_results.append(
                XauUsdFoldResult(
                    fold_id=f_spec.fold_id,
                    spec=f_spec,
                    train_metrics=train_metrics,
                    val_metrics=val_metrics,
                    oos_metrics=oos_metrics,
                    train_trade_count=len(train_purged.eligible_trades),
                    val_trade_count=len(val_trades_list),
                    oos_trade_count=len(oos_purged.eligible_trades),
                    train_trades=tuple(train_purged.eligible_trades),
                    val_trades=val_trades_list,
                    oos_trades=tuple(oos_purged.eligible_trades),
                )
            )
            all_oos_trades.extend(oos_purged.eligible_trades)

        # Aggregate Out-of-Sample Metrics
        agg_oos_metrics = XauUsdMetricsCalculator.calculate(
            signals=[],
            trades=all_oos_trades,
        )

        fold_expectancies = tuple(f.oos_metrics.net_expectancy_r for f in fold_results)
        stability_score = 1.0
        if len(fold_expectancies) > 1:
            mean_exp = statistics.mean(fold_expectancies)
            std_exp = statistics.stdev(fold_expectancies)
            stability_score = round(max(0.0, 1.0 - (std_exp / (abs(mean_exp) + 1e-4))), 4)

        return XauUsdWalkForwardResult(
            wf_config=wf_config,
            run_fingerprint=run_fp,
            folds=tuple(fold_results),
            oos_aggregated_metrics=agg_oos_metrics,
            temporal_stability_score=stability_score,
            fold_expectancies_r=fold_expectancies,
        )
