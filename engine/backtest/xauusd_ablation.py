"""Pure Python component ablation framework for research-only paired robustness evaluation for XAUUSD."""
import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

from engine.backtest.clock import ReplayClock
from engine.backtest.repository import PointInTimeDataset
from engine.backtest.xauusd_fingerprint import compute_xauusd_backtest_fingerprint
from engine.backtest.xauusd_metrics import XauUsdMetricsCalculator
from engine.backtest.xauusd_outcomes import XauUsdOutcomeEngine
from engine.backtest.xauusd_replay import XauUsdPointInTimeReplay
from engine.backtest.xauusd_types import (
    XauUsdAblationComparison,
    XauUsdAblationDelta,
    XauUsdAblationReport,
    XauUsdAblationType,
    XauUsdBacktestMetrics,
    XauUsdBacktestRunSpec,
    XauUsdSimulatedTrade,
)
from engine.core.types import (
    DualSideSignalSnapshot,
    EntryExecutionPolicy,
    FeedCriticality,
    IntrabarPolicy,
)
from engine.risk.xauusd_planner import XauUsdRiskPlanner
from engine.signals.engine import XauUsdSignalEngine
from engine.signals.profile import (
    Phase4FeedPolicy,
    Phase4SignalProfile,
    SideDirectionPolicy,
    SideTimingPolicy,
    uncalibrated_xauusd_signal_profile,
)


def _to_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class XauUsdAblationEngine:
    """
    Executes paired component ablation against the sealed XAUUSD baseline.

    Strict Invariants:
      1. Zero mutation of baseline XauUsdSignalEngine production defaults.
      2. Phase 3B production weight remains hard-locked to 0.0 on baseline.
      3. Hard gate ablations (e.g. NO_MACRO_BLACKOUT) are strictly labeled unsafe research.
      4. Baseline immutability proof: Baseline 1 and Baseline 2 produce identical fingerprints and trade ledgers.
    """

    def __init__(
        self,
        execution_policy: EntryExecutionPolicy = EntryExecutionPolicy.NEXT_BAR_OPEN,
        intrabar_policy: IntrabarPolicy = IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
    ):
        self.execution_policy = execution_policy
        self.intrabar_policy = intrabar_policy

    def create_ablated_profile(self, ablation_type: XauUsdAblationType) -> Phase4SignalProfile:
        """Create an ablated Phase4SignalProfile variant without altering baseline."""
        base_prof = uncalibrated_xauusd_signal_profile()

        if ablation_type == XauUsdAblationType.BASELINE:
            return base_prof

        from dataclasses import replace

        if ablation_type == XauUsdAblationType.NO_REGIME_FILTER:
            new_ld = replace(base_prof.long_direction, weight_regime=0.0) if base_prof.long_direction else base_prof.long_direction
            new_sd = replace(base_prof.short_direction, weight_regime=0.0) if base_prof.short_direction else base_prof.short_direction
            return replace(base_prof, long_direction=new_ld, short_direction=new_sd)

        elif ablation_type == XauUsdAblationType.NO_STRUCTURE_COMPONENT:
            new_ld = replace(base_prof.long_direction, weight_structure_bos=0.0) if base_prof.long_direction else base_prof.long_direction
            new_sd = replace(base_prof.short_direction, weight_structure_bos=0.0) if base_prof.short_direction else base_prof.short_direction
            return replace(base_prof, long_direction=new_ld, short_direction=new_sd)

        elif ablation_type == XauUsdAblationType.NO_MTF_TREND:
            new_ld = replace(base_prof.long_direction, weight_trend_4h=0.0, weight_trend_1d=0.0) if base_prof.long_direction else base_prof.long_direction
            new_sd = replace(base_prof.short_direction, weight_trend_4h=0.0, weight_trend_1d=0.0) if base_prof.short_direction else base_prof.short_direction
            return replace(base_prof, long_direction=new_ld, short_direction=new_sd)

        elif ablation_type == XauUsdAblationType.NO_PHASE3A_SESSION:
            new_lt = replace(base_prof.long_timing, weight_reversal_confirmation_15m=0.0) if base_prof.long_timing else base_prof.long_timing
            new_st = replace(base_prof.short_timing, weight_reversal_confirmation_15m=0.0) if base_prof.short_timing else base_prof.short_timing
            return replace(base_prof, long_timing=new_lt, short_timing=new_st)

        elif ablation_type == XauUsdAblationType.NO_PHASE3A_SWING_MATURITY:
            new_lt = replace(base_prof.long_timing, weight_phase3a=0.0) if base_prof.long_timing else base_prof.long_timing
            new_st = replace(base_prof.short_timing, weight_phase3a=0.0) if base_prof.short_timing else base_prof.short_timing
            return replace(base_prof, long_timing=new_lt, short_timing=new_st)

        elif ablation_type == XauUsdAblationType.NO_MACRO_BLACKOUT:
            new_fp = replace(base_prof.feed_policy, macro_blackout=FeedCriticality.OPTIONAL) if base_prof.feed_policy else base_prof.feed_policy
            return replace(base_prof, feed_policy=new_fp)

        elif ablation_type == XauUsdAblationType.WITH_PHASE3B_RESEARCH:
            return base_prof

        return base_prof

    def _execute_run(
        self,
        dataset: PointInTimeDataset,
        spec: XauUsdBacktestRunSpec,
        profile: Optional[Phase4SignalProfile] = None,
    ) -> Tuple[List[DualSideSignalSnapshot], List[XauUsdSimulatedTrade], str]:
        """Execute a single backtest replay run and return signals, trades, and ledger hash."""
        sig_engine = XauUsdSignalEngine(
            code_revision=spec.code_revision,
            engine_version=spec.engine_version,
            feature_version=spec.feature_version,
            cycle_version=spec.cycle_version,
        )
        risk_plan = XauUsdRiskPlanner(
            code_revision=spec.code_revision,
            risk_version=spec.risk_version,
        )
        run_fp = compute_xauusd_backtest_fingerprint(spec)

        full_candles_15m = dataset.get_closed_candles("15m", as_of=spec.end_time)
        timestamps = [c.timestamp_close for c in full_candles_15m if spec.start_time <= c.timestamp_close < spec.end_time]
        clock = ReplayClock(timestamps)

        replay = XauUsdPointInTimeReplay(
            dataset=dataset,
            signal_engine=sig_engine,
            risk_planner=risk_plan,
            outcome_engine=XauUsdOutcomeEngine(
                cost_config=spec.cost_config,
                holding_horizon_bars_15m=spec.holding_horizon_bars_15m,
                holding_horizon_seconds=spec.holding_horizon_seconds,
            ),
            execution_policy=self.execution_policy,
            intrabar_policy=self.intrabar_policy,
            run_fingerprint=run_fp,
            holding_horizon_bars_15m=spec.holding_horizon_bars_15m,
            holding_horizon_seconds=spec.holding_horizon_seconds,
            signal_profile=profile,
        )

        signals, trades = replay.run(clock)

        # Compute trade ledger hash
        h = hashlib.sha256()
        for t in trades:
            h.update(f"{t.trade_id}:{t.side}:{t.outcome}:{t.net_r}:{t.signal_timestamp.isoformat()}".encode("utf-8"))
        ledger_hash = h.hexdigest()

        return signals, trades, ledger_hash

    def run_ablation(
        self,
        dataset: PointInTimeDataset,
        baseline_spec: XauUsdBacktestRunSpec,
        ablation_types: Optional[Sequence[XauUsdAblationType]] = None,
    ) -> XauUsdAblationReport:
        """
        Execute paired ablation study with baseline immutability verification.
        """
        if ablation_types is None:
            ablation_types = [
                XauUsdAblationType.NO_REGIME_FILTER,
                XauUsdAblationType.NO_STRUCTURE_COMPONENT,
                XauUsdAblationType.NO_MTF_TREND,
                XauUsdAblationType.NO_PHASE3A_SESSION,
                XauUsdAblationType.NO_PHASE3A_SWING_MATURITY,
                XauUsdAblationType.NO_MACRO_BLACKOUT,
            ]

        # 1. Baseline Run 1
        b1_sigs, b1_trades, b1_hash = self._execute_run(dataset, baseline_spec)
        b1_metrics = XauUsdMetricsCalculator.calculate(b1_sigs, b1_trades)

        comparisons: List[XauUsdAblationComparison] = []

        # 2. Execute Ablation Variants
        for ab_type in ablation_types:
            ab_profile = self.create_ablated_profile(ab_type)
            from dataclasses import replace
            ab_spec = replace(baseline_spec, ablation_type=ab_type)

            ab_sigs, ab_trades, _ = self._execute_run(dataset, ab_spec, profile=ab_profile)
            ab_metrics = XauUsdMetricsCalculator.calculate(ab_sigs, ab_trades)

            delta = XauUsdAblationDelta(
                delta_expectancy_r=ab_metrics.net_expectancy_r - b1_metrics.net_expectancy_r,
                delta_profit_factor=ab_metrics.profit_factor - b1_metrics.profit_factor,
                delta_win_rate=ab_metrics.win_rate - b1_metrics.win_rate,
                delta_trade_count=ab_metrics.trade_count - b1_metrics.trade_count,
                delta_cost_drag_r=ab_metrics.cost_drag_r - b1_metrics.cost_drag_r,
            )

            comparisons.append(
                XauUsdAblationComparison(
                    ablation_type=ab_type,
                    baseline_metrics=b1_metrics,
                    ablated_metrics=ab_metrics,
                    delta=delta,
                )
            )

        # 3. Baseline Run 2 (Immutability Verification)
        b2_sigs, b2_trades, b2_hash = self._execute_run(dataset, baseline_spec)
        b2_metrics = XauUsdMetricsCalculator.calculate(b2_sigs, b2_trades)

        immutability_verified = (b1_hash == b2_hash) and (b1_metrics == b2_metrics)

        return XauUsdAblationReport(
            baseline_run_spec=baseline_spec,
            baseline_metrics=b1_metrics,
            comparisons=tuple(comparisons),
            baseline_hash=b1_hash,
            immutability_verified=immutability_verified,
        )
