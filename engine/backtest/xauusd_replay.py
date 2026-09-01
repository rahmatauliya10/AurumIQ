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


def _to_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class XauUsdPointInTimeReplay:
    """
    Executes causal historical simulation across historical time series for XAUUSD.

    Strict Invariants:
      1. One Engine Rule (Phase 4): Calls exact XauUsdSignalEngine instance.
      2. One Engine Rule (Phase 5): Calls exact XauUsdRiskPlanner instance.
      3. Point-in-Time Causality: Decision evaluation at T has access ONLY to closed data with timestamp_close <= T.
      4. Closed-Candle Contract: Unclosed candle <= T activates Phase 4 safety hold (FORCE_WAIT -> WAIT).
      5. Outcome Simulation: Reads strictly post-T data chronologically for fill and barrier resolution.
      6. Zero Django, ORM, or Celery imports in pure calculation engine.
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
        run_fingerprint: str = "",
        fold_id: Optional[int] = None,
        holding_horizon_bars_15m: Optional[int] = None,
        holding_horizon_seconds: Optional[float] = None,
        signal_profile: Optional[Phase4SignalProfile] = None,
    ):
        self.dataset = dataset
        self.signal_engine = signal_engine
        self.risk_planner = risk_planner
        self.outcome_engine = outcome_engine or XauUsdOutcomeEngine(
            holding_horizon_bars_15m=holding_horizon_bars_15m,
            holding_horizon_seconds=holding_horizon_seconds,
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
        self.signal_profile = signal_profile

    def run(self, clock: ReplayClock) -> Tuple[List[DualSideSignalSnapshot], List[XauUsdSimulatedTrade]]:
        """
        Iterate over chronological clock timestamps T and execute simulation.
        """
        signals: List[DualSideSignalSnapshot] = []
        trades: List[XauUsdSimulatedTrade] = []
        trade_counter = 0

        for t in clock:
            t_utc = _to_utc(t)

            # 1. Query point-in-time closed candles <= T
            closed_15m = self.dataset.get_closed_candles("15m", as_of=t_utc)
            if not closed_15m:
                continue

            closed_1h = self.dataset.get_closed_candles("1h", as_of=t_utc)
            closed_4h = self.dataset.get_closed_candles("4h", as_of=t_utc)
            closed_1d = self.dataset.get_closed_candles("1d", as_of=t_utc)

            macro_ctx = self.dataset.get_macro_context(as_of=t_utc)
            cycle_3a = self.dataset.get_cycle_3a(as_of=t_utc, cycle_version=getattr(self.signal_engine, "cycle_version", "3.0.0-3A"))
            if macro_ctx is None and cycle_3a is not None and getattr(cycle_3a, "macro_event", None):
                macro_ctx = cycle_3a.macro_event

            # Check unclosed candle safety semantics at <= T
            raw_15m = getattr(self.dataset, "_candles", {}).get("15m", [])
            has_unclosed_le_t = any(
                _to_utc(c.timestamp_close) <= t_utc and not c.is_closed
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
            try:
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
                    cycle_3a=cycle_3a,
                    runtime_health=rfh,
                    profile=self.signal_profile,
                    instrument="XAUUSD",
                    timeframe="15m",
                    as_of=t_utc,
                )
            except Exception:
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
                atr_14 = calculate_atr(highs, lows, closes, 14)

                atr_4h = None
                if closed_4h and len(closed_4h) >= 15:
                    atr_4h = calculate_atr([c.high for c in closed_4h], [c.low for c in closed_4h], [c.close for c in closed_4h], 14)

                # Execute Phase 5 LONG Risk Planning
                risk_plan = self.risk_planner.plan_long(
                    phase4_snapshot=signal_snapshot,
                    structure_15m=structure_15m,
                    atr14=atr_14 or Decimal("3.00"),
                )

                if risk_plan.is_valid_risk_plan and risk_plan.execution_eligible:
                    # Query strictly post-T future candles & quotes
                    future_15m = [c for c in raw_15m if _to_utc(c.timestamp_close) > t_utc]
                    future_5m = [c for c in getattr(self.dataset, "_candles", {}).get("5m", []) if _to_utc(c.timestamp_close) > t_utc]
                    future_1m = [c for c in getattr(self.dataset, "_candles", {}).get("1m", []) if _to_utc(c.timestamp_close) > t_utc]
                    future_quotes = [q for q in getattr(self.dataset, "_quotes", []) if _to_utc(q.timestamp) >= t_utc]

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
                        )
                    )

            # 5. Check if Candidate Signal Triggers SHORT Planning
            elif cand_state == SignalState.SELL_WINDOW and cand_decision == UserDecision.SELL:
                trade_counter += 1
                trade_id = f"trade-short-{trade_counter}-{t_utc.strftime('%Y%m%d%H%M')}"

                highs = [c.high for c in closed_15m]
                lows = [c.low for c in closed_15m]
                closes = [c.close for c in closed_15m]
                atr_14 = calculate_atr(highs, lows, closes, 14)

                # Execute Phase 5 SHORT Risk Planning
                risk_plan = self.risk_planner.plan_short(
                    phase4_snapshot=signal_snapshot,
                    structure_15m=structure_15m,
                    atr14=atr_14 or Decimal("3.00"),
                )

                if risk_plan.is_valid_risk_plan and risk_plan.execution_eligible:
                    # Query strictly post-T future candles & quotes
                    future_15m = [c for c in raw_15m if _to_utc(c.timestamp_close) > t_utc]
                    future_5m = [c for c in getattr(self.dataset, "_candles", {}).get("5m", []) if _to_utc(c.timestamp_close) > t_utc]
                    future_1m = [c for c in getattr(self.dataset, "_candles", {}).get("1m", []) if _to_utc(c.timestamp_close) > t_utc]
                    future_quotes = [q for q in getattr(self.dataset, "_quotes", []) if _to_utc(q.timestamp) >= t_utc]

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
                        )
                    )

        return signals, trades
