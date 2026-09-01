"""Point-in-time historical replay orchestrator for XAUUSD strictly consuming Phase 4 and Phase 5 engines."""
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
    IntrabarPolicy,
    QuoteData,
    RuntimeFeedHealth,
    SignalSide,
    SignalState,
    StructureResult,
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
    Executes causal historical simulation across historical time series for XAUUSD.

    Strict Invariants:
      1. One Engine Rule (Phase 4): Calls exact XauUsdSignalEngine instance.
      2. One Engine Rule (Phase 5): Calls exact XauUsdRiskPlanner instance.
      3. Point-in-Time Causality: Decision evaluation at T has access ONLY to closed data with timestamp_close <= T.
      4. Closed-Candle Contract: Unclosed candle <= T activates Phase 4 safety hold (FORCE_WAIT -> WAIT).
      5. Bounded Run Window: Replay evidence beyond declared run_end_time is strictly excluded.
      6. No ATR Fallback: Missing ATR14 fails closed deterministically.
      7. Hard Layer B Invariant: Published user decision is ALWAYS WAIT (is_production_authorized = False).
      8. Zero Swallowed Exceptions: Unexpected exceptions propagate immediately.
    """

    def __init__(
        self,
        dataset: PointInTimeDataset,
        signal_engine: XauUsdSignalEngine,
        risk_planner: XauUsdRiskPlanner,
        outcome_engine: Optional[XauUsdOutcomeEngine] = None,
        structure_engine: Optional[CausalStructureEngine] = None,
        feature_engine: Optional[FeatureEngine] = None,
        regime_engine: Optional[RegimeEngine] = None,
        execution_policy: EntryExecutionPolicy = EntryExecutionPolicy.NEXT_BAR_OPEN,
        intrabar_policy: IntrabarPolicy = IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
        signal_profile: Optional[Phase4SignalProfile] = None,
        run_fingerprint: str = "",
        fold_id: Optional[int] = None,
        holding_horizon_bars_15m: Optional[int] = None,
        holding_horizon_seconds: Optional[float] = None,
        max_fill_wait_bars_15m: Optional[int] = None,
        max_fill_wait_seconds: Optional[float] = None,
        cost_config: Optional[XauUsdCostConfig] = None,
        run_end_time: Optional[datetime] = None,
    ):
        self.dataset = dataset
        self.signal_engine = signal_engine
        self.risk_planner = risk_planner
        self.signal_profile = signal_profile
        self.cost_config = cost_config or XauUsdCostConfig.idealized()
        self.outcome_engine = outcome_engine or XauUsdOutcomeEngine(
            cost_config=self.cost_config,
            entry_execution_model=None,
            code_revision=self.risk_planner.code_revision,
            execution_policy_config=self.risk_planner.risk_profile.long_execution_policy,
            phase5_policy_fingerprint=self.risk_planner.policy_fingerprint,
            holding_horizon_bars_15m=holding_horizon_bars_15m,
            holding_horizon_seconds=holding_horizon_seconds,
            max_fill_wait_bars_15m=max_fill_wait_bars_15m,
            max_fill_wait_seconds=max_fill_wait_seconds,
        )
        self.structure_engine = structure_engine or CausalStructureEngine()
        self.feature_engine = feature_engine or FeatureEngine()
        self.regime_engine = regime_engine or RegimeEngine()
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

            # Respect declared run_end_time boundary
            if self.run_end_time is not None and t_utc > self.run_end_time:
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
            rfh = RuntimeFeedHealth(is_unclosed_candle=has_unclosed_le_t)

            # 2. Compute PIT Features, Regime & Structure
            feats_15m = self.feature_engine.extract_features(closed_15m) if len(closed_15m) >= 20 else None
            regime_15m = self.regime_engine.classify(feats_15m) if feats_15m else None
            structure_15m = self.structure_engine.analyze(closed_15m, atr=feats_15m.atr14 if feats_15m else None) if len(closed_15m) >= 5 else None

            feats_1h = self.feature_engine.extract_features(closed_1h) if len(closed_1h) >= 20 else None
            feats_4h = self.feature_engine.extract_features(closed_4h) if len(closed_4h) >= 20 else None
            feats_1d = self.feature_engine.extract_features(closed_1d) if len(closed_1d) >= 20 else None

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
                    )

                if risk_plan.is_valid_risk_plan and risk_plan.execution_eligible:
                    # Query strictly post-T future candles & quotes within declared run_end_time
                    future_15m = [
                        c for c in raw_15m
                        if _require_utc(c.timestamp_close) > t_utc
                        and (self.run_end_time is None or _require_utc(c.timestamp_close) <= self.run_end_time)
                    ]
                    future_5m = [
                        c for c in getattr(self.dataset, "_candles", {}).get("5m", [])
                        if _require_utc(c.timestamp_close) > t_utc
                        and (self.run_end_time is None or _require_utc(c.timestamp_close) <= self.run_end_time)
                    ]
                    future_1m = [
                        c for c in getattr(self.dataset, "_candles", {}).get("1m", [])
                        if _require_utc(c.timestamp_close) > t_utc
                        and (self.run_end_time is None or _require_utc(c.timestamp_close) <= self.run_end_time)
                    ]
                    future_quotes = [
                        q for q in getattr(self.dataset, "_quotes", [])
                        if _require_utc(q.timestamp) >= t_utc
                        and (self.run_end_time is None or _require_utc(q.timestamp) <= self.run_end_time)
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
                    )

                if risk_plan.is_valid_risk_plan and risk_plan.execution_eligible:
                    # Query strictly post-T future candles & quotes within declared run_end_time
                    future_15m = [
                        c for c in raw_15m
                        if _require_utc(c.timestamp_close) > t_utc
                        and (self.run_end_time is None or _require_utc(c.timestamp_close) <= self.run_end_time)
                    ]
                    future_5m = [
                        c for c in getattr(self.dataset, "_candles", {}).get("5m", [])
                        if _require_utc(c.timestamp_close) > t_utc
                        and (self.run_end_time is None or _require_utc(c.timestamp_close) <= self.run_end_time)
                    ]
                    future_1m = [
                        c for c in getattr(self.dataset, "_candles", {}).get("1m", [])
                        if _require_utc(c.timestamp_close) > t_utc
                        and (self.run_end_time is None or _require_utc(c.timestamp_close) <= self.run_end_time)
                    ]
                    future_quotes = [
                        q for q in getattr(self.dataset, "_quotes", [])
                        if _require_utc(q.timestamp) >= t_utc
                        and (self.run_end_time is None or _require_utc(q.timestamp) <= self.run_end_time)
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
