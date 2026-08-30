"""Point-in-time historical replay orchestrator strictly consuming Phase 4 and Phase 5 engines."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional, Sequence, Tuple

from engine.backtest.clock import ReplayClock
from engine.backtest.costs import CostModel
from engine.backtest.outcomes import OutcomeEngine
from engine.backtest.repository import PointInTimeDataset
from engine.backtest.types import SimulatedTrade
from engine.core.types import (
    EntryExecutionPolicy,
    IntrabarPolicy,
    RiskPlanSnapshot,
    SignalSnapshot,
    SignalState,
    UserDecision,
)
from engine.features.volatility import calculate_atr
from engine.risk.planner import RiskPlanner
from engine.signals.engine import XautSignalEngine
from engine.structure.engine import CausalStructureEngine


def _to_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class PointInTimeReplay:
    """
    Executes causal historical simulation across historical time series.

    Strict Invariants (P6-01..P6-06, P6-C1, A09):
      1. One Engine Rule (A09): Calls exact same XautSignalEngine and RiskPlanner instances.
      2. Decision evaluation at T has access ONLY to closed data with timestamp_close <= T.
      3. Outcome simulation reads post-T data chronologically for fill and barrier resolution.
      4. Zero Django, ORM, or Celery imports.
    """

    def __init__(
        self,
        dataset: PointInTimeDataset,
        signal_engine: XautSignalEngine,
        risk_planner: RiskPlanner,
        outcome_engine: Optional[OutcomeEngine] = None,
        structure_engine: Optional[CausalStructureEngine] = None,
        execution_policy: EntryExecutionPolicy = EntryExecutionPolicy.NEXT_BAR_OPEN,
        intrabar_policy: IntrabarPolicy = IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
        run_fingerprint: str = "",
        fold_id: Optional[int] = None,
        max_holding_bars_15m: int = 96,  # 24h horizon
    ):
        self.dataset = dataset
        self.signal_engine = signal_engine
        self.risk_planner = risk_planner
        self.outcome_engine = outcome_engine or OutcomeEngine()
        self.structure_engine = structure_engine or CausalStructureEngine()
        self.execution_policy = execution_policy
        self.intrabar_policy = intrabar_policy
        self.run_fingerprint = run_fingerprint
        self.fold_id = fold_id
        self.max_holding_bars_15m = max_holding_bars_15m

    def run(self, clock: ReplayClock) -> Tuple[List[SignalSnapshot], List[SimulatedTrade]]:
        """
        Iterate over chronological clock timestamps T and execute simulation.
        """
        signals: List[SignalSnapshot] = []
        trades: List[SimulatedTrade] = []
        trade_counter = 0

        for t in clock:
            t_utc = _to_utc(t)

            # 1. Query point-in-time closed candles <= T (P6-01, P6-03)
            closed_15m = self.dataset.get_closed_candles("15m", as_of=t_utc)
            if not closed_15m:
                continue

            closed_4h = self.dataset.get_closed_candles("4h", as_of=t_utc)
            closed_1d = self.dataset.get_closed_candles("1d", as_of=t_utc)

            # Query PIT reference feeds
            closed_xau = self.dataset.get_xau_candles(as_of=t_utc)
            xau_price, xau_bull, xau_ts = self.dataset.get_xau_reference(as_of=t_utc)
            usdt_rate, usdt_ts = self.dataset.get_usdt_rate(as_of=t_utc)
            macro_ctx = self.dataset.get_macro_context(as_of=t_utc)

            # 2. Master Signal Evaluation @ T (Phase 4 Engine - A09)
            try:
                signal_snapshot = self.signal_engine.analyze(
                    candles_15m=closed_15m,
                    as_of=t_utc,
                    candles_4h=closed_4h if closed_4h else None,
                    candles_1d=closed_1d if closed_1d else None,
                    candles_xau=closed_xau if closed_xau else None,
                    xau_reference_price=xau_price,
                    xau_reference_is_bullish=xau_bull,
                    xau_reference_ts=xau_ts,
                    usdt_rate=usdt_rate,
                    usdt_rate_ts=usdt_ts,
                    macro_context=macro_ctx,
                )
            except Exception:
                # Insufficient bars for full indicator warm-up
                continue

            signals.append(signal_snapshot)

            # 3. Risk Planning @ T (Phase 5 Engine - P6-05)
            if signal_snapshot.state == SignalState.BUY_WINDOW and signal_snapshot.user_decision == UserDecision.BUY:
                # Compute ATR and Structure strictly on closed data <= T
                struct_15m = self.structure_engine.analyze(closed_15m)
                struct_4h = self.structure_engine.analyze(closed_4h) if closed_4h else None
                atr_dec = calculate_atr(
                    [c.high for c in closed_15m],
                    [c.low for c in closed_15m],
                    [c.close for c in closed_15m],
                    period=14,
                )
                atr_val = float(atr_dec) if atr_dec is not None else None
                latest_close = closed_15m[-1].close

                risk_plan = self.risk_planner.plan(
                    signal_snapshot=signal_snapshot,
                    structure_15m=struct_15m,
                    atr14=atr_val,
                    structure_4h=struct_4h,
                    latest_close=latest_close,
                )

                if risk_plan.is_valid_risk_plan and risk_plan.execution_eligible:
                    trade_counter += 1
                    t_id = f"trade-{trade_counter}"

                    # 4. Outcome Simulation (T -> Fill -> Exit)
                    horizon_end = t_utc + timedelta(minutes=15 * self.max_holding_bars_15m)
                    future_15m = self.dataset.get_intrabar_candles("15m", start_ts=t_utc, end_ts=horizon_end)
                    future_5m = self.dataset.get_intrabar_candles("5m", start_ts=t_utc, end_ts=horizon_end)
                    future_1m = self.dataset.get_intrabar_candles("1m", start_ts=t_utc, end_ts=horizon_end)
                    future_quotes = self.dataset.get_quotes(start_ts=t_utc, end_ts=horizon_end)

                    trade = self.outcome_engine.resolve_trade(
                        signal=signal_snapshot,
                        risk_plan=risk_plan,
                        future_candles_15m=future_15m,
                        future_candles_5m=future_5m,
                        future_candles_1m=future_1m,
                        future_quotes=future_quotes,
                        execution_policy=self.execution_policy,
                        intrabar_policy=self.intrabar_policy,
                        trade_id=t_id,
                        run_fingerprint=self.run_fingerprint,
                        fold_id=self.fold_id,
                    )
                    trades.append(trade)

        return signals, trades
