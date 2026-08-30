"""Walk-forward execution, strict OOS parameter isolation, and temporal stability reporting."""
import hashlib
import statistics
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Tuple

from engine.backtest.clock import ReplayClock
from engine.backtest.costs import CostModel
from engine.backtest.fingerprint import compute_backtest_fingerprint
from engine.backtest.folds import ChronologicalFoldGenerator
from engine.backtest.metrics import BacktestMetricsCalculator
from engine.backtest.outcomes import OutcomeEngine
from engine.backtest.purge import PurgeEngine
from engine.backtest.replay import PointInTimeReplay
from engine.backtest.repository import PointInTimeDataset
from engine.backtest.types import (
    BacktestMetrics,
    BacktestRunSpec,
    FoldDataResult,
    FoldSpec,
    SelectionPolicy,
    SimulatedTrade,
    TemporalStabilityReport,
    WalkForwardConfig,
    WalkForwardResult,
)
from engine.core.types import EntryExecutionPolicy, IntrabarPolicy, SignalSnapshot
from engine.risk.planner import RiskPlanner
from engine.signals.engine import XautSignalEngine


def _to_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class WalkForwardEngine:
    """
    Executes chronological walk-forward analysis with label purging, embargo, and strict OOS isolation.

    Strict Invariants (P6-19..P6-23, A34, A35):
      1. Chronological order strictly enforced across all folds.
      2. Exact dependency windows are purged across partition boundaries.
      3. Post-boundary embargo excluded from subsequent partition evaluation.
      4. OOS Isolation: candidate selection API cannot accept OOS data.
      5. Generates descriptive temporal stability report without arbitrary pass/fail filters.
    """

    def __init__(
        self,
        signal_engine: Optional[XautSignalEngine] = None,
        risk_planner: Optional[RiskPlanner] = None,
        execution_policy: EntryExecutionPolicy = EntryExecutionPolicy.NEXT_BAR_OPEN,
        intrabar_policy: IntrabarPolicy = IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
    ):
        self.signal_engine = signal_engine
        self.risk_planner = risk_planner
        self.execution_policy = execution_policy
        self.intrabar_policy = intrabar_policy

    def run_walkforward(
        self,
        dataset: PointInTimeDataset,
        spec: BacktestRunSpec,
        wf_config: Optional[WalkForwardConfig] = None,
    ) -> WalkForwardResult:
        """
        Execute chronological walk-forward validation across all folds.
        """
        config = wf_config or WalkForwardConfig()
        folds_specs = ChronologicalFoldGenerator.generate_folds(
            start_time=spec.start_time,
            end_time=spec.end_time,
            config=config,
        )

        sig_engine = self.signal_engine or XautSignalEngine(
            code_revision=spec.code_revision,
            engine_version=spec.engine_version,
            config_version=spec.config_version,
            feature_version=spec.feature_version,
            cycle_version=spec.cycle_version,
        )

        risk_plan_engine = self.risk_planner or RiskPlanner(
            code_revision=spec.code_revision,
            risk_version=spec.risk_version,
            execution_model_version=spec.execution_model_version,
            config_version=spec.config_version,
        )

        cost_mod = CostModel(config=spec.cost_config)
        outcome_eng = OutcomeEngine(cost_model=cost_mod)

        fold_results: List[FoldDataResult] = []
        all_oos_trades: List[SimulatedTrade] = []
        all_oos_signals: List[SignalSnapshot] = []

        for fold_spec in folds_specs:
            # 1. Run simulation across fold span [train_start, oos_end)
            all_15m = dataset.get_closed_candles("15m", as_of=fold_spec.oos_end)
            fold_ts = [
                c.timestamp_close for c in all_15m
                if c.timestamp_close >= fold_spec.train_start and c.timestamp_close < fold_spec.oos_end
            ]
            if not fold_ts:
                continue

            clock = ReplayClock(timestamps=fold_ts)
            replay = PointInTimeReplay(
                dataset=dataset,
                signal_engine=sig_engine,
                risk_planner=risk_plan_engine,
                outcome_engine=outcome_eng,
                execution_policy=self.execution_policy,
                intrabar_policy=self.intrabar_policy,
                run_fingerprint=compute_backtest_fingerprint(spec),
                fold_id=fold_spec.fold_id,
            )

            signals, raw_trades = replay.run(clock=clock)

            # 2. Filter TRAIN partition (apply exact dependency purge at train_end)
            train_purge = PurgeEngine.filter_partition(
                trades=raw_trades,
                partition_start=fold_spec.train_start,
                partition_end=fold_spec.train_end,
                embargo_duration_seconds=0.0,
                purge_overlapping=config.purge_overlapping_dependencies,
                is_post_boundary_segment=False,
            )
            train_signals = [s for s in signals if fold_spec.train_start <= _to_utc(s.timestamp) < fold_spec.train_end]
            train_metrics = BacktestMetricsCalculator.calculate(signals=train_signals, trades=train_purge.eligible_trades)

            # 3. Filter VALIDATION partition (if present)
            val_metrics = None
            val_trades: Tuple[SimulatedTrade, ...] = ()
            if fold_spec.val_start and fold_spec.val_end:
                val_purge = PurgeEngine.filter_partition(
                    trades=raw_trades,
                    partition_start=fold_spec.val_start,
                    partition_end=fold_spec.val_end,
                    embargo_duration_seconds=fold_spec.embargo_duration_seconds,
                    purge_overlapping=config.purge_overlapping_dependencies,
                    is_post_boundary_segment=True,
                )
                val_trades = val_purge.eligible_trades
                val_signals = [s for s in signals if fold_spec.val_start <= _to_utc(s.timestamp) < fold_spec.val_end]
                val_metrics = BacktestMetricsCalculator.calculate(signals=val_signals, trades=val_trades)

            # 4. Filter OOS partition (apply post-boundary embargo, terminal segment)
            oos_purge = PurgeEngine.filter_partition(
                trades=raw_trades,
                partition_start=fold_spec.oos_start,
                partition_end=fold_spec.oos_end,
                embargo_duration_seconds=fold_spec.embargo_duration_seconds,
                purge_overlapping=False,  # Terminal segment
                is_post_boundary_segment=True,
            )
            oos_signals = [s for s in signals if fold_spec.oos_start <= _to_utc(s.timestamp) < fold_spec.oos_end]
            oos_metrics = BacktestMetricsCalculator.calculate(signals=oos_signals, trades=oos_purge.eligible_trades)

            all_oos_trades.extend(oos_purge.eligible_trades)
            all_oos_signals.extend(oos_signals)

            total_purged = len(train_purge.purged_trades)
            total_embargoed = len(oos_purge.embargoed_trades)

            fold_data = FoldDataResult(
                fold_id=fold_spec.fold_id,
                spec=fold_spec,
                train_metrics=train_metrics,
                oos_metrics=oos_metrics,
                train_trades=train_purge.eligible_trades,
                oos_trades=oos_purge.eligible_trades,
                validation_metrics=val_metrics,
                validation_trades=val_trades,
                purged_count=total_purged,
                embargoed_count=total_embargoed,
                total_samples_before_filter=len(raw_trades),
            )
            fold_results.append(fold_data)

        # 5. Build Temporal Stability Report
        stability_report = self._build_stability_report(fold_results, all_oos_signals, all_oos_trades)

        # 6. Build Provenance Fingerprint
        wf_fp = self._compute_walkforward_fingerprint(spec, config)

        return WalkForwardResult(
            config=config,
            folds=tuple(fold_results),
            stability_report=stability_report,
            walkforward_fingerprint=wf_fp,
        )

    @staticmethod
    def select_candidate_from_train_val(
        candidate_evaluations: Sequence[Tuple[str, BacktestMetrics, Optional[BacktestMetrics]]],
        policy: SelectionPolicy = SelectionPolicy.VALIDATION_EXPECTANCY_R,
    ) -> str:
        """
        OOS Isolation Gate (P6-22, A35):
        Candidate selection API strictly receives ONLY train and validation metrics.
        OOS metrics cannot participate in selection by API design.
        Explicit SelectionPolicy prevents hidden optimization objectives.
        """
        if not candidate_evaluations:
            raise ValueError("No candidate evaluations provided for selection.")

        best_candidate = candidate_evaluations[0][0]
        best_score = -999.0

        for cand_id, train_m, val_m in candidate_evaluations:
            if policy == SelectionPolicy.VALIDATION_PROFIT_FACTOR:
                score = val_m.profit_factor if val_m else train_m.profit_factor
            elif policy == SelectionPolicy.TRAIN_EXPECTANCY_R:
                score = train_m.net_expectancy_r
            else:  # VALIDATION_EXPECTANCY_R (default)
                score = val_m.net_expectancy_r if val_m else train_m.net_expectancy_r

            if score > best_score:
                best_score = score
                best_candidate = cand_id

        return best_candidate

    @staticmethod
    def _build_stability_report(
        folds: Sequence[FoldDataResult],
        oos_signals: Sequence[SignalSnapshot],
        oos_trades: Sequence[SimulatedTrade],
    ) -> TemporalStabilityReport:
        """Build descriptive cross-fold stability summary."""
        total_folds = len(folds)
        if total_folds == 0:
            empty_m = BacktestMetricsCalculator.calculate([], [])
            return TemporalStabilityReport(
                total_folds=0,
                positive_expectancy_folds=0,
                oos_expectancies_r=(),
                oos_profit_factors=(),
                oos_drawdowns_r=(),
                median_oos_expectancy_r=0.0,
                worst_oos_expectancy_r=0.0,
                best_oos_expectancy_r=0.0,
                aggregate_oos_metrics=empty_m,
                is_stable_positive=False,
            )

        oos_exp = tuple(f.oos_metrics.net_expectancy_r for f in folds)
        oos_pf = tuple(f.oos_metrics.profit_factor for f in folds)
        oos_dd = tuple(f.oos_metrics.max_trade_sequence_drawdown_r for f in folds)

        pos_count = sum(1 for e in oos_exp if e > 0.0)
        med_exp = float(statistics.median(oos_exp)) if oos_exp else 0.0
        worst_exp = float(min(oos_exp)) if oos_exp else 0.0
        best_exp = float(max(oos_exp)) if oos_exp else 0.0

        agg_metrics = BacktestMetricsCalculator.calculate(signals=oos_signals, trades=oos_trades)
        is_stable = (pos_count == total_folds) and (med_exp > 0.0)

        return TemporalStabilityReport(
            total_folds=total_folds,
            positive_expectancy_folds=pos_count,
            oos_expectancies_r=oos_exp,
            oos_profit_factors=oos_pf,
            oos_drawdowns_r=oos_dd,
            median_oos_expectancy_r=med_exp,
            worst_oos_expectancy_r=worst_exp,
            best_oos_expectancy_r=best_exp,
            aggregate_oos_metrics=agg_metrics,
            is_stable_positive=is_stable,
        )

    @staticmethod
    def _compute_walkforward_fingerprint(spec: BacktestRunSpec, config: WalkForwardConfig) -> str:
        """Compute SHA-256 fingerprint of walk-forward configuration."""
        base_fp = compute_backtest_fingerprint(spec)
        h = hashlib.sha256()
        h.update(f"base:{base_fp}".encode("utf-8"))
        h.update(f"folds:{config.total_folds}".encode("utf-8"))
        h.update(f"train_ratio:{config.train_ratio}".encode("utf-8"))
        h.update(f"val_ratio:{config.val_ratio}".encode("utf-8"))
        h.update(f"oos_ratio:{config.oos_ratio}".encode("utf-8"))
        h.update(f"embargo:{config.embargo_seconds}".encode("utf-8"))
        h.update(f"purge:{config.purge_overlapping_dependencies}".encode("utf-8"))
        h.update(f"rolling:{config.rolling_window}".encode("utf-8"))
        return h.hexdigest()
