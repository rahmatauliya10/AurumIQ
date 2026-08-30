"""High-level runner orchestrating complete deterministic point-in-time backtests."""
from typing import Optional, Sequence

from engine.backtest.clock import ReplayClock
from engine.backtest.costs import CostModel
from engine.backtest.fingerprint import compute_backtest_fingerprint
from engine.backtest.metrics import BacktestMetricsCalculator
from engine.backtest.outcomes import OutcomeEngine
from engine.backtest.replay import PointInTimeReplay
from engine.backtest.repository import PointInTimeDataset
from engine.backtest.types import (
    BacktestCostConfig,
    BacktestRunResult,
    BacktestRunSpec,
    CostScenario,
)
from engine.core.types import EntryExecutionPolicy, IntrabarPolicy
from engine.risk.planner import RiskPlanner
from engine.signals.engine import XautSignalEngine


class BacktestRunner:
    """
    Coordinates dataset loading, replay execution, metrics aggregation, and provenance generation.
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

    def run(
        self,
        dataset: PointInTimeDataset,
        spec: BacktestRunSpec,
        clock: Optional[ReplayClock] = None,
    ) -> BacktestRunResult:
        """
        Execute full backtest and return immutable BacktestRunResult.
        """
        # 1. Initialize or resolve pure engines (One Engine Rule - A09)
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

        # 2. Build CostModel and OutcomeEngine
        cost_mod = CostModel(config=spec.cost_config)
        outcome_eng = OutcomeEngine(cost_model=cost_mod)

        # 3. Compute Run Fingerprint
        run_fp = compute_backtest_fingerprint(spec)

        # 4. Setup Replay Clock if not explicitly passed
        if clock is None:
            # Query all 15m closed candle timestamps in dataset within spec window
            all_15m = dataset.get_closed_candles("15m", as_of=spec.end_time)
            valid_ts = [
                c.timestamp_close for c in all_15m
                if c.timestamp_close >= spec.start_time and c.timestamp_close <= spec.end_time
            ]
            if not valid_ts:
                raise ValueError("No eligible 15m candle close timestamps found within run specification window.")
            clock = ReplayClock(timestamps=valid_ts)

        # 5. Execute Replay
        replay = PointInTimeReplay(
            dataset=dataset,
            signal_engine=sig_engine,
            risk_planner=risk_plan_engine,
            outcome_engine=outcome_eng,
            execution_policy=self.execution_policy,
            intrabar_policy=self.intrabar_policy,
            run_fingerprint=run_fp,
        )

        signals, trades = replay.run(clock=clock)

        # 6. Aggregate Metrics
        metrics = BacktestMetricsCalculator.calculate(
            signals=signals,
            trades=trades,
        )

        return BacktestRunResult(
            run_spec=spec,
            run_fingerprint=run_fp,
            metrics=metrics,
            trades=tuple(trades),
            signals=tuple(signals),
        )
