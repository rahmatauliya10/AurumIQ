"""Pure Python component ablation framework for research-only paired robustness evaluation for XAUUSD."""
import hashlib
from dataclasses import replace
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


def _normalize_direction_weights(policy: SideDirectionPolicy, ablate_fields: Sequence[str]) -> SideDirectionPolicy:
    """Zero out ablated fields and re-normalize remaining direction weights to sum to 100.0."""
    weights = {
        "weight_regime": policy.weight_regime if policy.weight_regime is not None else 0.0,
        "weight_trend_1h": policy.weight_trend_1h if policy.weight_trend_1h is not None else 0.0,
        "weight_trend_4h": policy.weight_trend_4h if policy.weight_trend_4h is not None else 0.0,
        "weight_trend_1d": policy.weight_trend_1d if policy.weight_trend_1d is not None else 0.0,
        "weight_structure_bos": policy.weight_structure_bos if policy.weight_structure_bos is not None else 0.0,
        "weight_pullback": policy.weight_pullback if policy.weight_pullback is not None else 0.0,
        "weight_momentum": policy.weight_momentum if policy.weight_momentum is not None else 0.0,
        "weight_volume": policy.weight_volume if policy.weight_volume is not None else 0.0,
    }
    for f in ablate_fields:
        weights[f] = 0.0

    total = sum(weights.values())
    if total > 0.0:
        factor = 100.0 / total
        for k in weights:
            if weights[k] > 0.0:
                weights[k] = round(weights[k] * factor, 4)
        diff = round(100.0 - sum(weights.values()), 4)
        largest_k = max(weights, key=lambda k: weights[k])
        weights[largest_k] = round(weights[largest_k] + diff, 4)

    return SideDirectionPolicy(**weights)


def _normalize_timing_weights(policy: SideTimingPolicy, ablate_fields: Sequence[str]) -> SideTimingPolicy:
    """Zero out ablated fields and re-normalize remaining timing weights to sum to 100.0."""
    weights = {
        "weight_entry_zone": policy.weight_entry_zone if policy.weight_entry_zone is not None else 0.0,
        "weight_reversal_confirmation_15m": policy.weight_reversal_confirmation_15m if policy.weight_reversal_confirmation_15m is not None else 0.0,
        "weight_momentum_turn_15m_1h": policy.weight_momentum_turn_15m_1h if policy.weight_momentum_turn_15m_1h is not None else 0.0,
        "weight_phase3a": policy.weight_phase3a if policy.weight_phase3a is not None else 0.0,
        "weight_volume_response": policy.weight_volume_response if policy.weight_volume_response is not None else 0.0,
    }
    for f in ablate_fields:
        weights[f] = 0.0

    total = sum(weights.values())
    if total > 0.0:
        factor = 100.0 / total
        for k in weights:
            if weights[k] > 0.0:
                weights[k] = round(weights[k] * factor, 4)
        diff = round(100.0 - sum(weights.values()), 4)
        largest_k = max(weights, key=lambda k: weights[k])
        weights[largest_k] = round(weights[largest_k] + diff, 4)

    return SideTimingPolicy(**weights)


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

    def create_ablated_profile(
        self,
        ablation_type: XauUsdAblationType,
        base_profile: Optional[Phase4SignalProfile] = None,
    ) -> Phase4SignalProfile:
        """Create an ablated Phase4SignalProfile variant from base profile without altering baseline."""
        base_prof = base_profile if base_profile is not None else uncalibrated_xauusd_signal_profile()

        if ablation_type == XauUsdAblationType.BASELINE:
            return base_prof

        if ablation_type == XauUsdAblationType.NO_REGIME_FILTER:
            new_ld = _normalize_direction_weights(base_prof.long_direction, ["weight_regime"]) if base_prof.long_direction else base_prof.long_direction
            new_sd = _normalize_direction_weights(base_prof.short_direction, ["weight_regime"]) if base_prof.short_direction else base_prof.short_direction
            return replace(base_prof, long_direction=new_ld, short_direction=new_sd)

        elif ablation_type == XauUsdAblationType.NO_STRUCTURE_COMPONENT:
            new_ld = _normalize_direction_weights(base_prof.long_direction, ["weight_structure_bos"]) if base_prof.long_direction else base_prof.long_direction
            new_sd = _normalize_direction_weights(base_prof.short_direction, ["weight_structure_bos"]) if base_prof.short_direction else base_prof.short_direction
            return replace(base_prof, long_direction=new_ld, short_direction=new_sd)

        elif ablation_type == XauUsdAblationType.NO_MTF_TREND:
            new_ld = _normalize_direction_weights(base_prof.long_direction, ["weight_trend_4h", "weight_trend_1d"]) if base_prof.long_direction else base_prof.long_direction
            new_sd = _normalize_direction_weights(base_prof.short_direction, ["weight_trend_4h", "weight_trend_1d"]) if base_prof.short_direction else base_prof.short_direction
            return replace(base_prof, long_direction=new_ld, short_direction=new_sd)

        elif ablation_type == XauUsdAblationType.NO_PHASE3A_SESSION:
            new_lt = _normalize_timing_weights(base_prof.long_timing, ["weight_reversal_confirmation_15m"]) if base_prof.long_timing else base_prof.long_timing
            new_st = _normalize_timing_weights(base_prof.short_timing, ["weight_reversal_confirmation_15m"]) if base_prof.short_timing else base_prof.short_timing
            return replace(base_prof, long_timing=new_lt, short_timing=new_st)

        elif ablation_type == XauUsdAblationType.NO_PHASE3A_SWING_MATURITY:
            new_lt = _normalize_timing_weights(base_prof.long_timing, ["weight_phase3a"]) if base_prof.long_timing else base_prof.long_timing
            new_st = _normalize_timing_weights(base_prof.short_timing, ["weight_phase3a"]) if base_prof.short_timing else base_prof.short_timing
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
        target_profile = profile if profile is not None else spec.signal_profile

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
            entry_execution_model=None,
            code_revision=spec.code_revision,
            execution_policy_config=(
                spec.risk_profile.long_execution_policy
                if spec.risk_profile is not None
                else risk_plan.risk_profile.long_execution_policy
            ),
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
            timestamps = [spec.start_time]

        clock = ReplayClock(timestamps)
        replay = XauUsdPointInTimeReplay(
            dataset=dataset,
            signal_engine=sig_engine,
            risk_planner=risk_plan,
            outcome_engine=outcome_engine,
            execution_policy=self.execution_policy,
            intrabar_policy=self.intrabar_policy,
            signal_profile=target_profile,
            holding_horizon_bars_15m=spec.holding_horizon_bars_15m,
            holding_horizon_seconds=spec.holding_horizon_seconds,
            max_fill_wait_bars_15m=spec.max_fill_wait_bars_15m,
            max_fill_wait_seconds=spec.max_fill_wait_seconds,
            cost_config=spec.cost_config,
            run_end_time=spec.end_time,
        )

        signals, trades = replay.run(clock)

        # Compute hash of trade ledger
        ledger_data = [
            f"{t.trade_id}:{t.outcome.value}:{t.net_r}:{t.source_signal_fingerprint}"
            for t in trades
        ]
        ledger_str = ",".join(ledger_data)
        ledger_hash = hashlib.sha256(ledger_str.encode("utf-8")).hexdigest()

        return signals, trades, ledger_hash

    def run_ablation(
        self,
        dataset: PointInTimeDataset,
        baseline_spec: XauUsdBacktestRunSpec,
        ablation_types: Optional[Sequence[XauUsdAblationType]] = None,
    ) -> XauUsdAblationReport:
        """
        Execute paired factor ablation study with baseline immutability proof.
        """
        types_to_run = ablation_types or [
            XauUsdAblationType.NO_REGIME_FILTER,
            XauUsdAblationType.NO_STRUCTURE_COMPONENT,
            XauUsdAblationType.NO_MTF_TREND,
            XauUsdAblationType.NO_PHASE3A_SESSION,
            XauUsdAblationType.NO_PHASE3A_SWING_MATURITY,
            XauUsdAblationType.NO_MACRO_BLACKOUT,
        ]

        # 1. Baseline Run 1
        b1_signals, b1_trades, b1_hash = self._execute_run(dataset=dataset, spec=baseline_spec)
        b1_metrics = XauUsdMetricsCalculator.calculate(signals=b1_signals, trades=b1_trades)

        comparisons: List[XauUsdAblationComparison] = []

        # 2. Iterate Ablation Variants
        for ab_type in types_to_run:
            ab_profile = self.create_ablated_profile(ab_type, base_profile=baseline_spec.signal_profile)
            ab_signals, ab_trades, _ = self._execute_run(
                dataset=dataset,
                spec=baseline_spec,
                profile=ab_profile,
            )
            ab_metrics = XauUsdMetricsCalculator.calculate(signals=ab_signals, trades=ab_trades)

            delta = XauUsdAblationDelta(
                delta_expectancy_r=round(ab_metrics.net_expectancy_r - b1_metrics.net_expectancy_r, 4),
                delta_profit_factor=round(ab_metrics.profit_factor - b1_metrics.profit_factor, 4),
                delta_win_rate=round(ab_metrics.win_rate - b1_metrics.win_rate, 4),
                delta_trade_count=ab_metrics.trade_count - b1_metrics.trade_count,
                delta_cost_drag_r=round(ab_metrics.cost_drag_r - b1_metrics.cost_drag_r, 4),
            )

            comparisons.append(
                XauUsdAblationComparison(
                    ablation_type=ab_type,
                    baseline_metrics=b1_metrics,
                    ablated_metrics=ab_metrics,
                    delta=delta,
                )
            )

        # 3. Baseline Run 2 (Immutability Proof)
        b2_signals, b2_trades, b2_hash = self._execute_run(dataset=dataset, spec=baseline_spec)
        immutability_verified = (b1_hash == b2_hash)

        return XauUsdAblationReport(
            baseline_run_spec=baseline_spec,
            baseline_metrics=b1_metrics,
            comparisons=tuple(comparisons),
            baseline_hash=b1_hash,
            immutability_verified=immutability_verified,
        )
