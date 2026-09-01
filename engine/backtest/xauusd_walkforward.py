"""Walk-forward execution, strict OOS parameter isolation, and temporal stability reporting for XAUUSD."""
import statistics
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Tuple

from engine.backtest.clock import ReplayClock
from engine.backtest.folds import ChronologicalFoldGenerator
from engine.backtest.purge import PurgeEngine
from engine.backtest.repository import PointInTimeDataset
from engine.backtest.xauusd_fingerprint import compute_xauusd_backtest_fingerprint
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


def _to_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


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
        """
        config = wf_config or XauUsdWalkForwardConfig()
        fold_specs_raw = ChronologicalFoldGenerator.generate_folds(
            start_time=spec.start_time,
            end_time=spec.end_time,
            config=config,  # Compatible interface
        )

        sig_engine = self.signal_engine or XauUsdSignalEngine(
            code_revision=spec.code_revision,
            engine_version=spec.engine_version,
            feature_version=spec.feature_version,
            cycle_version=spec.cycle_version,
        )

        risk_plan = self.risk_planner or XauUsdRiskPlanner(
            code_revision=spec.code_revision,
            risk_version=spec.risk_version,
        )

        run_fp = compute_xauusd_backtest_fingerprint(spec)

        # 1. Run Master Replay across entire dataset window
        full_candles_15m = dataset.get_closed_candles("15m", as_of=spec.end_time)
        if not full_candles_15m:
            raise ValueError(f"No 15m closed candles available in dataset for {spec.instrument}.")

        timestamps = [c.timestamp_close for c in full_candles_15m if spec.start_time <= c.timestamp_close < spec.end_time]
        if not timestamps:
            raise ValueError(f"No timestamps in range [{spec.start_time}, {spec.end_time}).")

        clock = ReplayClock(timestamps)
        replay = XauUsdPointInTimeReplay(
            dataset=dataset,
            signal_engine=sig_engine,
            risk_planner=risk_plan,
            outcome_engine=self.outcome_engine or XauUsdOutcomeEngine(
                cost_config=spec.cost_config,
                holding_horizon_bars_15m=spec.holding_horizon_bars_15m,
                holding_horizon_seconds=spec.holding_horizon_seconds,
            ),
            execution_policy=self.execution_policy,
            intrabar_policy=self.intrabar_policy,
            run_fingerprint=run_fp,
            holding_horizon_bars_15m=spec.holding_horizon_bars_15m,
            holding_horizon_seconds=spec.holding_horizon_seconds,
        )

        all_signals, all_trades = replay.run(clock)

        fold_results: List[XauUsdFoldResult] = []
        oos_all_trades: List[XauUsdSimulatedTrade] = []
        oos_all_signals: List[DualSideSignalSnapshot] = []
        fold_expectancies: List[float] = []

        # 2. Process each chronological fold partition
        for f_spec_raw in fold_specs_raw:
            f_spec = XauUsdFoldSpec(
                fold_id=f_spec_raw.fold_id,
                train_start=f_spec_raw.train_start,
                train_end=f_spec_raw.train_end,
                val_start=f_spec_raw.val_start,
                val_end=f_spec_raw.val_end,
                oos_start=f_spec_raw.oos_start,
                oos_end=f_spec_raw.oos_end,
                embargo_duration_seconds=f_spec_raw.embargo_duration_seconds,
            )

            # A. TRAIN Partition (with boundary purge)
            train_purge = PurgeEngine.filter_partition(
                trades=all_trades,
                partition_start=f_spec.train_start,
                partition_end=f_spec.train_end,
                purge_overlapping=config.purge_overlapping,
                is_post_boundary_segment=False,
            )
            train_sigs = [s for s in all_signals if f_spec.train_start <= _to_utc(s.timestamp) < f_spec.train_end]
            train_metrics = XauUsdMetricsCalculator.calculate(train_sigs, train_purge.eligible_trades)

            # B. VALIDATION Partition (if configured)
            val_metrics: Optional[XauUsdBacktestMetrics] = None
            val_trade_count = 0
            if f_spec.val_start and f_spec.val_end:
                val_purge = PurgeEngine.filter_partition(
                    trades=all_trades,
                    partition_start=f_spec.val_start,
                    partition_end=f_spec.val_end,
                    embargo_duration_seconds=f_spec.embargo_duration_seconds,
                    purge_overlapping=config.purge_overlapping,
                    is_post_boundary_segment=True,
                )
                val_sigs = [s for s in all_signals if f_spec.val_start <= _to_utc(s.timestamp) < f_spec.val_end]
                val_metrics = XauUsdMetricsCalculator.calculate(val_sigs, val_purge.eligible_trades)
                val_trade_count = len(val_purge.eligible_trades)

            # C. OOS Partition (Strict Out-Of-Sample Evaluation with Embargo)
            oos_purge = PurgeEngine.filter_partition(
                trades=all_trades,
                partition_start=f_spec.oos_start,
                partition_end=f_spec.oos_end,
                embargo_duration_seconds=f_spec.embargo_duration_seconds,
                purge_overlapping=config.purge_overlapping,
                is_post_boundary_segment=True,
            )
            oos_sigs = [s for s in all_signals if f_spec.oos_start <= _to_utc(s.timestamp) < f_spec.oos_end]
            oos_metrics = XauUsdMetricsCalculator.calculate(oos_sigs, oos_purge.eligible_trades)

            oos_all_trades.extend(oos_purge.eligible_trades)
            oos_all_signals.extend(oos_sigs)
            fold_expectancies.append(oos_metrics.net_expectancy_r)

            fold_results.append(
                XauUsdFoldResult(
                    fold_id=f_spec.fold_id,
                    spec=f_spec,
                    train_metrics=train_metrics,
                    val_metrics=val_metrics,
                    oos_metrics=oos_metrics,
                    train_trade_count=len(train_purge.eligible_trades),
                    val_trade_count=val_trade_count,
                    oos_trade_count=len(oos_purge.eligible_trades),
                )
            )

        # 3. Aggregate OOS Metrics
        oos_agg_metrics = XauUsdMetricsCalculator.calculate(oos_all_signals, oos_all_trades)

        # 4. Temporal Stability Score
        if fold_expectancies:
            pos_folds = sum(1 for exp in fold_expectancies if exp > 0)
            stability_score = float(pos_folds / len(fold_expectancies))
        else:
            stability_score = 0.0

        return XauUsdWalkForwardResult(
            wf_config=config,
            run_fingerprint=run_fp,
            folds=tuple(fold_results),
            oos_aggregated_metrics=oos_agg_metrics,
            temporal_stability_score=stability_score,
            fold_expectancies_r=tuple(fold_expectancies),
        )
