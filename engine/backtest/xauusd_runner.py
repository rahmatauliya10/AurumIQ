"""High-level deterministic backtest runner and execution orchestrator for XAUUSD."""
from datetime import datetime
from typing import List, Optional, Sequence, Tuple

from engine.backtest.clock import ReplayClock
from engine.backtest.repository import PointInTimeDataset
from engine.backtest.xauusd_ablation import XauUsdAblationEngine
from engine.backtest.xauusd_fingerprint import compute_xauusd_backtest_fingerprint
from engine.backtest.xauusd_metrics import XauUsdMetricsCalculator
from engine.backtest.xauusd_outcomes import XauUsdOutcomeEngine
from engine.backtest.xauusd_replay import XauUsdPointInTimeReplay
from engine.backtest.xauusd_types import (
    XauUsdAblationReport,
    XauUsdAblationType,
    XauUsdBacktestMetrics,
    XauUsdBacktestRunSpec,
    XauUsdSimulatedTrade,
    XauUsdWalkForwardConfig,
    XauUsdWalkForwardResult,
)
from engine.backtest.xauusd_walkforward import XauUsdWalkForwardEngine
from engine.core.types import (
    DualSideSignalSnapshot,
    EntryExecutionPolicy,
    IntrabarPolicy,
)
from engine.risk.xauusd_planner import XauUsdRiskPlanner
from engine.signals.engine import XauUsdSignalEngine


class XauUsdBacktestRunner:
    """
    High-level orchestrator for XAUUSD historical backtesting, walk-forward validation, and ablation studies.

    Strict Invariants:
      1. Pure Python: Decoupled from Django ORM / database calls.
      2. Deterministic: Identical inputs produce identical run fingerprints and trade ledgers.
      3. One Engine Rule: Directly invokes XauUsdSignalEngine and XauUsdRiskPlanner.
      4. Bounded Window Evidence: Evidence at or beyond declared spec.end_time is strictly excluded (< end_time).
      5. Side-Specific Policy Parity: LONG and SHORT execute using their respective Phase 5 policies.
      6. Explicit Provenance: Respects caller-supplied signal_profile and risk_profile without fabrication.
    """

    def __init__(
        self,
        execution_policy: EntryExecutionPolicy = EntryExecutionPolicy.NEXT_BAR_OPEN,
        intrabar_policy: IntrabarPolicy = IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
    ):
        self.execution_policy = execution_policy
        self.intrabar_policy = intrabar_policy

    def run_point_in_time(
        self,
        dataset: PointInTimeDataset,
        spec: XauUsdBacktestRunSpec,
    ) -> Tuple[XauUsdBacktestMetrics, List[XauUsdSimulatedTrade], List[DualSideSignalSnapshot], str]:
        """
        Execute deterministic point-in-time historical backtest for XAUUSD.
        Returns: (metrics, trades, signals, run_fingerprint).
        """
        run_fp = compute_xauusd_backtest_fingerprint(spec)

        sig_engine = XauUsdSignalEngine(
            code_revision=spec.code_revision,
            engine_version=spec.engine_version,
            feature_version=spec.feature_version,
            cycle_version=spec.cycle_version,
        )

        risk_plan = XauUsdRiskPlanner(
            code_revision=spec.code_revision,
            risk_profile=spec.risk_profile,
            risk_version=spec.risk_version,
        )

        outcome_engine = XauUsdOutcomeEngine(
            cost_config=spec.cost_config,
            code_revision=spec.code_revision,
            long_execution_policy=risk_plan.risk_profile.long_execution_policy,
            short_execution_policy=risk_plan.risk_profile.short_execution_policy,
            phase5_policy_fingerprint=risk_plan.policy_fingerprint,
            holding_horizon_bars_15m=spec.holding_horizon_bars_15m,
            holding_horizon_seconds=spec.holding_horizon_seconds,
            max_fill_wait_bars_15m=spec.max_fill_wait_bars_15m,
            max_fill_wait_seconds=spec.max_fill_wait_seconds,
        )

        full_candles_15m = dataset.get_closed_candles("15m", as_of=spec.end_time)
        timestamps = [
            c.timestamp_close for c in full_candles_15m
            if spec.start_time <= c.timestamp_close < spec.end_time
        ]

        if not timestamps:
            raise ValueError(f"No 15m closed candle timestamps found in window [{spec.start_time}, {spec.end_time}).")

        clock = ReplayClock(timestamps)
        replay = XauUsdPointInTimeReplay(
            dataset=dataset,
            signal_engine=sig_engine,
            risk_planner=risk_plan,
            outcome_engine=outcome_engine,
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

        signals, trades = replay.run(clock)
        metrics = XauUsdMetricsCalculator.calculate(signals=signals, trades=trades)

        return metrics, trades, signals, run_fp

    def run_walk_forward(
        self,
        dataset: PointInTimeDataset,
        spec: XauUsdBacktestRunSpec,
        wf_config: XauUsdWalkForwardConfig,
    ) -> XauUsdWalkForwardResult:
        """
        Execute chronological walk-forward validation for XAUUSD.
        """
        wf_engine = XauUsdWalkForwardEngine(
            execution_policy=self.execution_policy,
            intrabar_policy=self.intrabar_policy,
        )
        return wf_engine.run(dataset=dataset, spec=spec, wf_config=wf_config)

    def run_ablation(
        self,
        dataset: PointInTimeDataset,
        baseline_spec: XauUsdBacktestRunSpec,
        ablation_types: Optional[Sequence[XauUsdAblationType]] = None,
    ) -> XauUsdAblationReport:
        """
        Execute paired component ablation study for XAUUSD.
        """
        ab_engine = XauUsdAblationEngine(
            execution_policy=self.execution_policy,
            intrabar_policy=self.intrabar_policy,
        )
        return ab_engine.run_ablation(
            dataset=dataset,
            baseline_spec=baseline_spec,
            ablation_types=ablation_types,
        )
