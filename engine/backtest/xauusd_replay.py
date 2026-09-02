"""Point-in-time timeline replay engine for XAUUSD validating Phase 4 and Phase 5 rules without lookahead bias."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional, Sequence, Tuple

from engine.backtest.clock import ReplayClock
from engine.backtest.repository import PointInTimeDataset
from engine.backtest.xauusd_outcomes import XauUsdOutcomeEngine
from engine.backtest.xauusd_types import (
    XauUsdCostConfig,
    XauUsdSimulatedTrade,
    XauUsdTradeOutcome,
)
from engine.core.types import (
    CandleData,
    DualSideSignalSnapshot,
    EntryExecutionPolicy,
    FeedHealthStatus,
    IntrabarPolicy,
    QuoteData,
    RuntimeFeedHealth,
    SignalSide,
    SignalState,
    UserDecision,
)
from engine.features.engine import FeatureEngine
from engine.features.volatility import calculate_atr
from engine.regime.engine import RegimeEngine
from engine.risk.xauusd_planner import XauUsdRiskPlanner
from engine.signals.engine import XauUsdSignalEngine
from engine.signals.profile import Phase4SignalProfile
from engine.structure.engine import CausalStructureEngine


def _require_utc(dt: datetime, param_name: str = "timestamp") -> datetime:
    """Validate that datetime is explicitly timezone aware and convert to UTC."""
    if dt is None or dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ValueError(f"{param_name} must be timezone-aware with non-None utcoffset (naive timestamps forbidden).")
    return dt.astimezone(timezone.utc)


class XauUsdPointInTimeReplay:
    """
    Chronological point-in-time replay engine for XAUUSD.

    Strict Invariants:
      1. Zero Lookahead Bias: At any time T, only data with timestamp_close <= T is visible to signal & risk evaluation.
      2. Layer B Invariant: SignalEngine evaluates candidate state (Layer A), while published user_decision remains WAIT.
      3. Denominator Integrity: planned_risk_amount is established at signal generation and never altered.
      4. Bounded Window Evidence: Evidence at or beyond declared run_end_time is strictly excluded (< end_time).
      5. PIT 4H Structure & Phase 3A Parity: Causal 4H structure and PIT Phase 3A cycle snapshots are passed into risk and signal engines.
      6. Lossless Provenance: Deterministic execution and policy fingerprints link signals, risk plans, and trades.
    """

    def __init__(
        self,
        dataset: PointInTimeDataset,
        signal_engine: XauUsdSignalEngine,
        risk_planner: XauUsdRiskPlanner,
        outcome_engine: Optional[XauUsdOutcomeEngine] = None,
        feature_engine: Optional[FeatureEngine] = None,
        regime_engine: Optional[RegimeEngine] = None,
        structure_engine: Optional[CausalStructureEngine] = None,
        execution_policy: EntryExecutionPolicy = EntryExecutionPolicy.NEXT_BAR_OPEN,
        intrabar_policy: IntrabarPolicy = IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
        cost_config: Optional[XauUsdCostConfig] = None,
        signal_profile: Optional[Phase4SignalProfile] = None,
        run_fingerprint: str = "",
        fold_id: Optional[int] = None,
        holding_horizon_bars_15m: Optional[int] = None,
        holding_horizon_seconds: Optional[float] = None,
        max_fill_wait_bars_15m: Optional[int] = None,
        max_fill_wait_seconds: Optional[float] = None,
        run_end_time: Optional[datetime] = None,
    ):
        self.dataset = dataset
        self.signal_engine = signal_engine
        self.risk_planner = risk_planner
        self.cost_config = cost_config or XauUsdCostConfig.idealized()
        self.signal_profile = signal_profile

        if outcome_engine is not None:
            self.outcome_engine = outcome_engine
        else:
            self.outcome_engine = XauUsdOutcomeEngine(
                cost_config=self.cost_config,
                holding_horizon_bars_15m=holding_horizon_bars_15m,
                holding_horizon_seconds=holding_horizon_seconds,
                max_fill_wait_bars_15m=max_fill_wait_bars_15m,
                max_fill_wait_seconds=max_fill_wait_seconds,
                code_revision=signal_engine.code_revision,
                long_execution_policy=risk_planner.risk_profile.long_execution_policy,
                short_execution_policy=risk_planner.risk_profile.short_execution_policy,
                phase5_policy_fingerprint=risk_planner.policy_fingerprint,
            )

        self.feature_engine = feature_engine or FeatureEngine()
        self.regime_engine = regime_engine or RegimeEngine()
        self.structure_engine = structure_engine or CausalStructureEngine()
        self.execution_policy = execution_policy
        self.intrabar_policy = intrabar_policy
        self.run_fingerprint = run_fingerprint
        self.fold_id = fold_id
        self.holding_horizon_bars_15m = holding_horizon_bars_15m
        self.holding_horizon_seconds = holding_horizon_seconds
        self.max_fill_wait_bars_15m = max_fill_wait_bars_15m
        self.max_fill_wait_seconds = max_fill_wait_seconds
        self.run_end_time = _require_utc(run_end_time, "run_end_time") if run_end_time is not None else None

    def run(self, clock: ReplayClock) -> Tuple[List[DualSideSignalSnapshot], List[XauUsdSimulatedTrade]]:
        """
        Iterate through timeline clock evaluating point-in-time signals and executing outcomes.
        """
        signals: List[DualSideSignalSnapshot] = []
        trades: List[XauUsdSimulatedTrade] = []
        trade_counter = 0

        for t_step in clock:
            t_utc = _require_utc(t_step, "clock_step")

            # Respect declared run_end_time boundary [start_time, end_time)
            if self.run_end_time is not None and t_utc >= self.run_end_time:
                break

            # 1. PIT Closed-Candle Extraction
            closed_15m = self.dataset.get_closed_candles("15m", as_of=t_utc)
            closed_1h = self.dataset.get_closed_candles("1h", as_of=t_utc)
            closed_4h = self.dataset.get_closed_candles("4h", as_of=t_utc)
            closed_1d = self.dataset.get_closed_candles("1d", as_of=t_utc)

            # Check unclosed candle safety hold
            raw_15m = getattr(self.dataset, "_candles", {}).get("15m", [])
            has_unclosed_le_t = any(
                _require_utc(c.timestamp_close) <= t_utc and not c.is_closed
                for c in raw_15m
            )
            macro_ctx = self.dataset.get_macro_context(as_of=t_utc)
            cycle_3a_snap = self.dataset.get_cycle_3a(as_of=t_utc)

            # Derive macro feed health and blackout state strictly from PIT evidence
            macro_health = FeedHealthStatus.MISSING
            is_blackout = False

            if macro_ctx is not None:
                macro_health = FeedHealthStatus.HEALTHY if macro_ctx.is_feed_healthy else FeedHealthStatus.UNHEALTHY
                if macro_ctx.is_in_blackout:
                    is_blackout = True
            elif cycle_3a_snap is not None and cycle_3a_snap.macro_event is not None:
                macro_health = FeedHealthStatus.HEALTHY if cycle_3a_snap.macro_event.is_feed_healthy else FeedHealthStatus.UNHEALTHY
                if cycle_3a_snap.macro_event.is_in_blackout:
                    is_blackout = True

            rfh = RuntimeFeedHealth(
                primary_15m=FeedHealthStatus.HEALTHY if closed_15m else FeedHealthStatus.MISSING,
                primary_1h=FeedHealthStatus.HEALTHY if closed_1h else FeedHealthStatus.MISSING,
                primary_4h=FeedHealthStatus.HEALTHY if closed_4h else FeedHealthStatus.MISSING,
                primary_1d=FeedHealthStatus.HEALTHY if closed_1d else FeedHealthStatus.MISSING,
                macro_blackout_feed=macro_health,
                is_macro_blackout=is_blackout,
                phase3a=FeedHealthStatus.HEALTHY if cycle_3a_snap else FeedHealthStatus.MISSING,
                is_unclosed_candle=has_unclosed_le_t,
            )

            # 2. Compute PIT Features, Regime & Structure
            feats_15m = self.feature_engine.extract_features(closed_15m) if len(closed_15m) >= 20 else None
            regime_15m = self.regime_engine.classify(feats_15m) if feats_15m else None
            structure_15m = self.structure_engine.analyze(closed_15m, atr=feats_15m.atr14 if feats_15m else None) if len(closed_15m) >= 5 else None

            feats_1h = self.feature_engine.extract_features(closed_1h) if len(closed_1h) >= 20 else None
            feats_4h = self.feature_engine.extract_features(closed_4h) if len(closed_4h) >= 20 else None
            feats_1d = self.feature_engine.extract_features(closed_1d) if len(closed_1d) >= 20 else None

            # Causal 4H Structure for Phase 5 Structural Targets
            structure_4h = self.structure_engine.analyze(closed_4h, atr=feats_4h.atr14 if feats_4h else None) if len(closed_4h) >= 5 else None

            # PIT Phase 3A Cycle and Macro Context
            cycle_3a_snap = self.dataset.get_cycle_3a(as_of=t_utc)

            # 3. Master Dual-Side Signal Evaluation @ T (Phase 4 Engine)
            # Propagate exceptions directly - NO swallowing!
            signal_snapshot = self.signal_engine.analyze(
                closed_candles_15m=closed_15m,
                closed_candles_1h=closed_1h if closed_1h else None,
                closed_candles_4h=closed_4h if closed_4h else None,
                closed_candles_1d=closed_1d if closed_1d else None,
                regime_15m=regime_15m,
                features_15m=feats_15m,
                features_1h=feats_1h,
                features_4h=feats_4h,
                features_1d=feats_1d,
                structure_15m=structure_15m,
                cycle_3a=cycle_3a_snap,
                runtime_health=rfh,
                profile=self.signal_profile,
                instrument="XAUUSD",
                timeframe="15m",
                as_of=t_utc,
            )

            if signal_snapshot is None:
                continue

            signals.append(signal_snapshot)

            # Check candidate state and decision (Layer A)
            cand_state = signal_snapshot.candidate_state
            cand_decision = signal_snapshot.candidate_user_decision

            # 4. Check if Candidate Signal Triggers LONG Planning
            if cand_state == SignalState.BUY_WINDOW and cand_decision == UserDecision.BUY:
                trade_counter += 1
                trade_id = f"trade-long-{trade_counter}-{t_utc.strftime('%Y%m%d%H%M')}"

                highs = [c.high for c in closed_15m]
                lows = [c.low for c in closed_15m]
                closes = [c.close for c in closed_15m]
                atr_14 = calculate_atr(highs, lows, closes, 14) if len(closed_15m) >= 15 else None

                # Execute Phase 5 LONG Risk Planning (No synthetic ATR fallback!)
                if atr_14 is None:
                    risk_plan = self.risk_planner._build_invalid_snapshot(
                        side=SignalSide.LONG,
                        source_phase4_fingerprint=signal_snapshot.analysis_fingerprint,
                        source_candidate_state=cand_state,
                        source_candidate_decision=cand_decision,
                        authoritative_t=t_utc,
                        atr_value=Decimal("0"),
                        reasons=("ATR14 unavailable from closed 15m candles.",),
                    )
                else:
                    risk_plan = self.risk_planner.plan_long(
                        phase4_snapshot=signal_snapshot,
                        structure_15m=structure_15m,
                        atr14=atr_14,
                        structure_4h=structure_4h,
                    )

                if risk_plan.is_valid_risk_plan and risk_plan.execution_eligible:
                    # Query strictly post-T future candles & quotes within declared run_end_time (< end_time)
                    future_15m = [
                        c for c in raw_15m
                        if _require_utc(c.timestamp_close) > t_utc
                        and (self.run_end_time is None or _require_utc(c.timestamp_close) < self.run_end_time)
                    ]
                    future_5m = [
                        c for c in getattr(self.dataset, "_candles", {}).get("5m", [])
                        if _require_utc(c.timestamp_close) > t_utc
                        and (self.run_end_time is None or _require_utc(c.timestamp_close) < self.run_end_time)
                    ]
                    future_1m = [
                        c for c in getattr(self.dataset, "_candles", {}).get("1m", [])
                        if _require_utc(c.timestamp_close) > t_utc
                        and (self.run_end_time is None or _require_utc(c.timestamp_close) < self.run_end_time)
                    ]
                    future_quotes = [
                        q for q in getattr(self.dataset, "_quotes", [])
                        if _require_utc(q.timestamp) >= t_utc
                        and (self.run_end_time is None or _require_utc(q.timestamp) < self.run_end_time)
                    ]

                    sim_trade = self.outcome_engine.resolve_trade(
                        signal=signal_snapshot,
                        risk_plan=risk_plan,
                        future_candles_15m=future_15m,
                        future_candles_5m=future_5m if future_5m else None,
                        future_candles_1m=future_1m if future_1m else None,
                        future_quotes=future_quotes if future_quotes else None,
                        execution_policy=self.execution_policy,
                        intrabar_policy=self.intrabar_policy,
                        trade_id=trade_id,
                        run_fingerprint=self.run_fingerprint,
                        fold_id=self.fold_id,
                        holding_horizon_bars_15m=self.holding_horizon_bars_15m,
                        holding_horizon_seconds=self.holding_horizon_seconds,
                        run_end_time=self.run_end_time,
                    )
                    trades.append(sim_trade)
                else:
                    # Record skipped / invalid risk trade
                    trades.append(
                        XauUsdSimulatedTrade(
                            trade_id=trade_id,
                            side=SignalSide.LONG,
                            candidate_state=cand_state,
                            candidate_user_decision=cand_decision,
                            source_signal_fingerprint=signal_snapshot.analysis_fingerprint,
                            signal_timestamp=t_utc,
                            risk_plan_fingerprint=getattr(risk_plan, "risk_plan_fingerprint", f"risk-{signal_snapshot.analysis_fingerprint[:16]}"),
                            planned_risk_amount=Decimal("0.00"),
                            outcome=XauUsdTradeOutcome.SKIPPED,
                            run_fingerprint=self.run_fingerprint,
                            fold_id=self.fold_id,
                            dependency_window=(t_utc, t_utc),
                            dependency_end_timestamp=t_utc,
                        )
                    )

            # 5. Check if Candidate Signal Triggers SHORT Planning
            elif cand_state == SignalState.SELL_WINDOW and cand_decision == UserDecision.SELL:
                trade_counter += 1
                trade_id = f"trade-short-{trade_counter}-{t_utc.strftime('%Y%m%d%H%M')}"

                highs = [c.high for c in closed_15m]
                lows = [c.low for c in closed_15m]
                closes = [c.close for c in closed_15m]
                atr_14 = calculate_atr(highs, lows, closes, 14) if len(closed_15m) >= 15 else None

                # Execute Phase 5 SHORT Risk Planning (No synthetic ATR fallback!)
                if atr_14 is None:
                    risk_plan = self.risk_planner._build_invalid_snapshot(
                        side=SignalSide.SHORT,
                        source_phase4_fingerprint=signal_snapshot.analysis_fingerprint,
                        source_candidate_state=cand_state,
                        source_candidate_decision=cand_decision,
                        authoritative_t=t_utc,
                        atr_value=Decimal("0"),
                        reasons=("ATR14 unavailable from closed 15m candles.",),
                    )
                else:
                    risk_plan = self.risk_planner.plan_short(
                        phase4_snapshot=signal_snapshot,
                        structure_15m=structure_15m,
                        atr14=atr_14,
                        structure_4h=structure_4h,
                    )

                if risk_plan.is_valid_risk_plan and risk_plan.execution_eligible:
                    # Query strictly post-T future candles & quotes within declared run_end_time (< end_time)
                    future_15m = [
                        c for c in raw_15m
                        if _require_utc(c.timestamp_close) > t_utc
                        and (self.run_end_time is None or _require_utc(c.timestamp_close) < self.run_end_time)
                    ]
                    future_5m = [
                        c for c in getattr(self.dataset, "_candles", {}).get("5m", [])
                        if _require_utc(c.timestamp_close) > t_utc
                        and (self.run_end_time is None or _require_utc(c.timestamp_close) < self.run_end_time)
                    ]
                    future_1m = [
                        c for c in getattr(self.dataset, "_candles", {}).get("1m", [])
                        if _require_utc(c.timestamp_close) > t_utc
                        and (self.run_end_time is None or _require_utc(c.timestamp_close) < self.run_end_time)
                    ]
                    future_quotes = [
                        q for q in getattr(self.dataset, "_quotes", [])
                        if _require_utc(q.timestamp) >= t_utc
                        and (self.run_end_time is None or _require_utc(q.timestamp) < self.run_end_time)
                    ]

                    sim_trade = self.outcome_engine.resolve_trade(
                        signal=signal_snapshot,
                        risk_plan=risk_plan,
                        future_candles_15m=future_15m,
                        future_candles_5m=future_5m if future_5m else None,
                        future_candles_1m=future_1m if future_1m else None,
                        future_quotes=future_quotes if future_quotes else None,
                        execution_policy=self.execution_policy,
                        intrabar_policy=self.intrabar_policy,
                        trade_id=trade_id,
                        run_fingerprint=self.run_fingerprint,
                        fold_id=self.fold_id,
                        holding_horizon_bars_15m=self.holding_horizon_bars_15m,
                        holding_horizon_seconds=self.holding_horizon_seconds,
                        run_end_time=self.run_end_time,
                    )
                    trades.append(sim_trade)
                else:
                    # Record skipped / invalid risk trade
                    trades.append(
                        XauUsdSimulatedTrade(
                            trade_id=trade_id,
                            side=SignalSide.SHORT,
                            candidate_state=cand_state,
                            candidate_user_decision=cand_decision,
                            source_signal_fingerprint=signal_snapshot.analysis_fingerprint,
                            signal_timestamp=t_utc,
                            risk_plan_fingerprint=getattr(risk_plan, "risk_plan_fingerprint", f"risk-{signal_snapshot.analysis_fingerprint[:16]}"),
                            planned_risk_amount=Decimal("0.00"),
                            outcome=XauUsdTradeOutcome.SKIPPED,
                            run_fingerprint=self.run_fingerprint,
                            fold_id=self.fold_id,
                            dependency_window=(t_utc, t_utc),
                            dependency_end_timestamp=t_utc,
                        )
                    )

        return signals, trades
