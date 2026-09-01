"""Deterministic outcome resolution engine for XAUUSD with side-aware causal fill, intrabar replay, and normalized R."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional, Sequence, Tuple

from engine.backtest.xauusd_types import (
    XauUsdCostConfig,
    XauUsdSimulatedTrade,
    XauUsdTradeOutcome,
)
from engine.core.types import (
    BarrierHitType,
    CandleData,
    DualSideSignalSnapshot,
    EntryExecutionPolicy,
    IntrabarPolicy,
    QuoteData,
    RegimeType,
    RiskSide,
    SessionType,
    SideRiskPlanSnapshot,
    SignalSide,
    SignalState,
    UserDecision,
)
from engine.risk.xauusd_execution import SideAwareEntryExecutionModel
from engine.risk.xauusd_intrabar import SideAwareIntrabarResolver
from engine.risk.xauusd_policy import XauUsdExecutionPolicy


def _require_utc(dt: datetime, param_name: str = "timestamp") -> datetime:
    """Validate that datetime is explicitly timezone aware and convert to UTC."""
    if dt is None or dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ValueError(f"{param_name} must be timezone-aware with non-None utcoffset (naive timestamps forbidden).")
    return dt.astimezone(timezone.utc)


class XauUsdOutcomeEngine:
    """
    Simulates entry execution, intrabar barrier resolution, and normalized R payoff for XAUUSD trades.

    Strict Invariants:
      1. Zero Double Costs: Entry model owns entry spread and slippage. OutcomeEngine applies exit fees/spread once.
      2. Deterministic Intrabar Resolution: Uses Phase 5 SideAwareIntrabarResolver with conservative SL_FIRST fallback.
      3. Denominator Invariance: Planned risk amount (LONG: entry_max - stop_final, SHORT: stop_final - entry_min)
         is frozen at signal generation and NEVER recalculated post-fill.
      4. Bounded Window Evidence: Evidence beyond declared run_end_time is strictly excluded.
      5. Zero Speculative Sizing: All PnL and payoff metrics are evaluated in per-unit USD and normalized R.
    """

    def __init__(
        self,
        cost_config: XauUsdCostConfig,
        entry_execution_model: Optional[SideAwareEntryExecutionModel] = None,
        intrabar_resolver: Optional[SideAwareIntrabarResolver] = None,
        holding_horizon_bars_15m: Optional[int] = None,
        holding_horizon_seconds: Optional[float] = None,
        max_fill_wait_bars_15m: Optional[int] = None,
        max_fill_wait_seconds: Optional[float] = None,
        code_revision: Optional[str] = None,
        execution_policy_config: Optional[XauUsdExecutionPolicy] = None,
        phase5_policy_fingerprint: Optional[str] = None,
    ):
        self.cost_config = cost_config
        self.holding_horizon_bars_15m = holding_horizon_bars_15m
        self.holding_horizon_seconds = holding_horizon_seconds
        self.max_fill_wait_bars_15m = max_fill_wait_bars_15m
        self.max_fill_wait_seconds = max_fill_wait_seconds

        if entry_execution_model is not None:
            self.entry_execution_model = entry_execution_model
        else:
            if not code_revision or not execution_policy_config or not phase5_policy_fingerprint:
                raise ValueError("XauUsdOutcomeEngine requires entry_execution_model or explicit (code_revision, execution_policy_config, phase5_policy_fingerprint).")
            self.entry_execution_model = SideAwareEntryExecutionModel(
                code_revision=code_revision,
                execution_policy=execution_policy_config,
                phase5_policy_fingerprint=phase5_policy_fingerprint,
            )

        self.intrabar_resolver = intrabar_resolver or SideAwareIntrabarResolver()

    def resolve_trade(
        self,
        signal: DualSideSignalSnapshot,
        risk_plan: SideRiskPlanSnapshot,
        future_candles_15m: Sequence[CandleData],
        future_candles_5m: Optional[Sequence[CandleData]] = None,
        future_candles_1m: Optional[Sequence[CandleData]] = None,
        future_quotes: Optional[Sequence[QuoteData]] = None,
        execution_policy: EntryExecutionPolicy = EntryExecutionPolicy.NEXT_BAR_OPEN,
        intrabar_policy: IntrabarPolicy = IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
        trade_id: str = "test-trade-1",
        run_fingerprint: str = "",
        fold_id: Optional[int] = None,
        holding_horizon_bars_15m: Optional[int] = None,
        holding_horizon_seconds: Optional[float] = None,
        run_end_time: Optional[datetime] = None,
    ) -> XauUsdSimulatedTrade:
        """
        Simulate deterministic trade entry, lifecycle, barrier hits, and normalized R payoff.
        """
        # Validate timezone-aware signal timestamp
        signal_ts = _require_utc(signal.timestamp, "signal.timestamp")
        risk_side = risk_plan.side
        signal_side = SignalSide.LONG if risk_side == RiskSide.LONG else SignalSide.SHORT

        # Validate planned risk amount (denominator R)
        if not risk_plan.is_valid_risk_plan or not risk_plan.execution_eligible:
            return XauUsdSimulatedTrade(
                trade_id=trade_id,
                side=signal_side,
                candidate_state=signal.candidate_state,
                candidate_user_decision=signal.candidate_user_decision,
                source_signal_fingerprint=signal.analysis_fingerprint,
                signal_timestamp=signal_ts,
                risk_plan_fingerprint=risk_plan.risk_plan_fingerprint,
                planned_risk_amount=Decimal("0.00"),
                outcome=XauUsdTradeOutcome.SKIPPED,
                run_fingerprint=run_fingerprint,
                fold_id=fold_id,
                dependency_window=(signal_ts, signal_ts),
                dependency_end_timestamp=signal_ts,
            )

        if risk_side == RiskSide.LONG:
            planned_risk = getattr(risk_plan, "planned_risk_amount", None)
            if planned_risk is None and risk_plan.entry_max is not None and risk_plan.stop_final is not None:
                planned_risk = risk_plan.entry_max - risk_plan.stop_final
        else:
            planned_risk = getattr(risk_plan, "planned_risk_amount", None)
            if planned_risk is None and risk_plan.stop_final is not None and risk_plan.entry_min is not None:
                planned_risk = risk_plan.stop_final - risk_plan.entry_min

        if planned_risk is None or planned_risk <= Decimal("0"):
            raise ValueError(f"Invalid planned_risk_amount {planned_risk} for trade {trade_id}")

        # Bound future evidence strictly within [T, run_end_time]
        run_end_utc = _require_utc(run_end_time, "run_end_time") if run_end_time is not None else None

        valid_candles_15m = [
            c for c in future_candles_15m
            if _require_utc(c.timestamp_close, "candle.timestamp_close") > signal_ts
            and (run_end_utc is None or _require_utc(c.timestamp_close) <= run_end_utc)
        ]
        valid_candles_5m = [
            c for c in (future_candles_5m or ())
            if _require_utc(c.timestamp_close, "candle.timestamp_close") > signal_ts
            and (run_end_utc is None or _require_utc(c.timestamp_close) <= run_end_utc)
        ]
        valid_candles_1m = [
            c for c in (future_candles_1m or ())
            if _require_utc(c.timestamp_close, "candle.timestamp_close") > signal_ts
            and (run_end_utc is None or _require_utc(c.timestamp_close) <= run_end_utc)
        ]
        valid_quotes = [
            q for q in (future_quotes or ())
            if _require_utc(q.timestamp, "quote.timestamp") >= signal_ts
            and (run_end_utc is None or _require_utc(q.timestamp) <= run_end_utc)
        ]

        # 1. Simulate Entry Execution
        if execution_policy == EntryExecutionPolicy.NEXT_BAR_OPEN:
            fill_res = self.entry_execution_model.simulate_next_bar_open(
                side=risk_side,
                signal_generated_at=signal_ts,
                candles=valid_candles_15m,
                source_phase4_fingerprint=signal.analysis_fingerprint,
            )
        elif execution_policy == EntryExecutionPolicy.MARKET_AFTER_SIGNAL:
            fill_res = self.entry_execution_model.simulate_market_after_signal(
                side=risk_side,
                signal_generated_at=signal_ts,
                quotes=valid_quotes,
                source_phase4_fingerprint=signal.analysis_fingerprint,
            )
        elif execution_policy == EntryExecutionPolicy.LIMIT_TOUCH:
            limit_p = risk_plan.entry_max if risk_side == RiskSide.LONG else risk_plan.entry_min
            fill_res = self.entry_execution_model.simulate_limit_zone(
                side=risk_side,
                signal_generated_at=signal_ts,
                limit_price=limit_p,
                quotes=valid_quotes,
                candles=valid_candles_15m,
                source_phase4_fingerprint=signal.analysis_fingerprint,
            )
        else:
            raise ValueError(f"Unknown execution policy: {execution_policy}")

        # Compute explicit fill-search horizon for NO_FILL dependency end
        fill_search_seconds = 3600.0
        if self.max_fill_wait_seconds is not None:
            fill_search_seconds = float(self.max_fill_wait_seconds)
        elif self.max_fill_wait_bars_15m is not None:
            fill_search_seconds = float(self.max_fill_wait_bars_15m * 900)

        no_fill_dep_end = signal_ts + timedelta(seconds=fill_search_seconds)
        if run_end_utc is not None and no_fill_dep_end > run_end_utc:
            no_fill_dep_end = run_end_utc

        evidence_fp = getattr(fill_res, "execution_fingerprint", None) or getattr(fill_res, "source_evidence_fingerprint", "")

        if not fill_res.is_filled or fill_res.fill_price is None or fill_res.fill_timestamp is None:
            return XauUsdSimulatedTrade(
                trade_id=trade_id,
                side=signal_side,
                candidate_state=signal.candidate_state,
                candidate_user_decision=signal.candidate_user_decision,
                source_signal_fingerprint=signal.analysis_fingerprint,
                signal_timestamp=signal_ts,
                risk_plan_fingerprint=risk_plan.risk_plan_fingerprint,
                planned_risk_amount=planned_risk,
                outcome=XauUsdTradeOutcome.NO_FILL,
                run_fingerprint=run_fingerprint,
                fold_id=fold_id,
                dependency_window=(signal_ts, no_fill_dep_end),
                dependency_end_timestamp=no_fill_dep_end,
                execution_evidence_fingerprint=evidence_fp,
            )

        fill_price = fill_res.fill_price
        fill_ts = _require_utc(fill_res.fill_timestamp, "fill_res.fill_timestamp")

        # 2. Intrabar Barrier Resolution
        tp1_price = risk_plan.tp1
        sl_price = risk_plan.stop_final
        tp2_price = risk_plan.tp2

        eff_bars = holding_horizon_bars_15m or self.holding_horizon_bars_15m
        eff_sec = holding_horizon_seconds or self.holding_horizon_seconds

        if eff_bars is None and eff_sec is None:
            raise ValueError("Terminal evaluation requires explicit holding_horizon_bars_15m or holding_horizon_seconds.")

        max_search_ts = fill_ts + timedelta(seconds=eff_sec if eff_sec is not None else float(eff_bars * 900))
        if run_end_utc is not None and max_search_ts > run_end_utc:
            max_search_ts = run_end_utc

        post_fill_15m = [
            c for c in valid_candles_15m
            if _require_utc(c.timestamp_close) >= fill_ts and _require_utc(c.timestamp_open) <= max_search_ts
        ]

        eval_candles = post_fill_15m[:eff_bars] if eff_bars is not None else post_fill_15m

        terminal_outcome = XauUsdTradeOutcome.UNRESOLVED
        exit_ts: Optional[datetime] = None
        exit_price: Optional[Decimal] = None
        hit_bar_index = -1
        max_favorable_price = fill_price
        max_adverse_price = fill_price

        for idx, candle in enumerate(eval_candles):
            c_close_ts = _require_utc(candle.timestamp_close)
            c_open_ts = _require_utc(candle.timestamp_open)

            # Update MFE / MAE tracking
            if risk_side == RiskSide.LONG:
                if candle.high > max_favorable_price:
                    max_favorable_price = candle.high
                if candle.low < max_adverse_price:
                    max_adverse_price = candle.low
            else:
                if candle.low < max_favorable_price:
                    max_favorable_price = candle.low
                if candle.high > max_adverse_price:
                    max_adverse_price = candle.high

            # Find matching lower-TF bars
            matching_1m = None
            matching_5m = None
            if intrabar_policy == IntrabarPolicy.LOWER_TIMEFRAME_REPLAY:
                if valid_candles_1m:
                    matching_1m = [
                        m for m in valid_candles_1m
                        if c_open_ts <= _require_utc(m.timestamp_open) and _require_utc(m.timestamp_close) <= c_close_ts
                    ]
                if valid_candles_5m:
                    matching_5m = [
                        m for m in valid_candles_5m
                        if c_open_ts <= _require_utc(m.timestamp_open) and _require_utc(m.timestamp_close) <= c_close_ts
                    ]

            res = self.intrabar_resolver.resolve(
                side=risk_side,
                parent_candle=candle,
                tp_price=tp1_price,
                sl_price=sl_price,
                fill_timestamp=fill_ts if idx == 0 else None,
                lower_tf_candles_1m=matching_1m,
                lower_tf_candles_5m=matching_5m,
                policy=intrabar_policy,
            )

            if res.barrier_hit == BarrierHitType.TP_FIRST:
                terminal_outcome = XauUsdTradeOutcome.TP1_FIRST
                exit_ts = res.exit_timestamp or c_close_ts
                exit_price = res.exit_price or tp1_price
                hit_bar_index = idx
                break
            elif res.barrier_hit == BarrierHitType.SL_FIRST:
                terminal_outcome = XauUsdTradeOutcome.SL_FIRST
                exit_ts = res.exit_timestamp or c_close_ts
                exit_price = res.exit_price or sl_price
                hit_bar_index = idx
                break

        # Check timeout horizon
        if terminal_outcome == XauUsdTradeOutcome.UNRESOLVED and len(eval_candles) > 0:
            if eff_bars is not None and len(post_fill_15m) >= eff_bars:
                terminal_outcome = XauUsdTradeOutcome.TIMEOUT
                last_bar = eval_candles[-1]
                exit_ts = _require_utc(last_bar.timestamp_close)
                exit_price = last_bar.close
                hit_bar_index = len(eval_candles) - 1

        dependency_end = exit_ts if exit_ts is not None else max_search_ts

        # Observational TP2 Extension Check
        tp2_hit = False
        if terminal_outcome == XauUsdTradeOutcome.TP1_FIRST and tp2_price is not None and hit_bar_index >= 0:
            remaining_candles = eval_candles[hit_bar_index:]
            for rem in remaining_candles:
                if risk_side == RiskSide.LONG and rem.high >= tp2_price:
                    tp2_hit = True
                    break
                elif risk_side == RiskSide.SHORT and rem.low <= tp2_price:
                    tp2_hit = True
                    break

        # 3. Calculate Normalized R and PnL
        if terminal_outcome == XauUsdTradeOutcome.UNRESOLVED or exit_price is None or exit_ts is None:
            return XauUsdSimulatedTrade(
                trade_id=trade_id,
                side=signal_side,
                candidate_state=signal.candidate_state,
                candidate_user_decision=signal.candidate_user_decision,
                source_signal_fingerprint=signal.analysis_fingerprint,
                signal_timestamp=signal_ts,
                risk_plan_fingerprint=risk_plan.risk_plan_fingerprint,
                planned_risk_amount=planned_risk,
                outcome=XauUsdTradeOutcome.UNRESOLVED,
                fill_timestamp=fill_ts,
                fill_price=fill_price,
                exit_timestamp=None,
                exit_price=None,
                dependency_end_timestamp=dependency_end,
                run_fingerprint=run_fingerprint,
                fold_id=fold_id,
                dependency_window=(signal_ts, dependency_end),
                execution_evidence_fingerprint=evidence_fp,
            )

        holding_duration_sec = (exit_ts - fill_ts).total_seconds()

        # Gross PnL per unit
        if risk_side == RiskSide.LONG:
            gross_pnl = exit_price - fill_price
            gross_r = gross_pnl / planned_risk
            mfe_pnl = max_favorable_price - fill_price
            mae_pnl = fill_price - max_adverse_price
        else:
            gross_pnl = fill_price - exit_price
            gross_r = gross_pnl / planned_risk
            mfe_pnl = fill_price - max_favorable_price
            mae_pnl = max_adverse_price - fill_price

        mfe_r = (mfe_pnl / planned_risk).quantize(Decimal("0.0001"))
        mae_r = (mae_pnl / planned_risk).quantize(Decimal("0.0001"))

        # Friction Calculations
        entry_fee = fill_price * (self.cost_config.entry_fee_bps * Decimal("0.0001"))
        entry_spread = getattr(fill_res, "observed_spread", Decimal("0.0")) + getattr(fill_res, "synthetic_spread", Decimal("0.0"))
        entry_slippage = getattr(fill_res, "adverse_slippage", Decimal("0.0"))

        exit_fee = exit_price * (self.cost_config.exit_fee_bps * Decimal("0.0001"))
        exit_spread = exit_price * (self.cost_config.synthetic_spread_bps * Decimal("0.0001"))
        exit_slippage = exit_price * (self.cost_config.exit_slippage_bps * Decimal("0.0001"))

        total_exit_friction = exit_fee + exit_spread + exit_slippage
        net_pnl = gross_pnl - total_exit_friction
        net_r = net_pnl / planned_risk

        gross_ret_pct = (gross_pnl / fill_price) * Decimal("100.0")
        net_ret_pct = (net_pnl / fill_price) * Decimal("100.0")

        return XauUsdSimulatedTrade(
            trade_id=trade_id,
            side=signal_side,
            candidate_state=signal.candidate_state,
            candidate_user_decision=signal.candidate_user_decision,
            source_signal_fingerprint=signal.analysis_fingerprint,
            signal_timestamp=signal_ts,
            risk_plan_fingerprint=risk_plan.risk_plan_fingerprint,
            planned_risk_amount=planned_risk,
            outcome=terminal_outcome,
            fill_timestamp=fill_ts,
            fill_price=fill_price,
            exit_timestamp=exit_ts,
            exit_price=exit_price,
            dependency_end_timestamp=dependency_end,
            gross_pnl_per_unit=gross_pnl.quantize(Decimal("0.0001")),
            net_pnl_per_unit=net_pnl.quantize(Decimal("0.0001")),
            gross_r=gross_r.quantize(Decimal("0.0001")),
            net_r=net_r.quantize(Decimal("0.0001")),
            gross_return_pct=gross_ret_pct.quantize(Decimal("0.0001")),
            net_return_pct=net_ret_pct.quantize(Decimal("0.0001")),
            mfe_r=mfe_r,
            mae_r=mae_r,
            holding_duration_seconds=holding_duration_sec,
            entry_fee=entry_fee,
            exit_fee=exit_fee,
            entry_spread=entry_spread,
            exit_spread=exit_spread,
            entry_slippage=entry_slippage,
            exit_slippage=exit_slippage,
            regime=getattr(signal, "regime", None) or getattr(signal, "regime_type", None) or getattr(signal, "market_regime", None) or RegimeType.UNKNOWN,
            session=getattr(signal, "session", None) or getattr(signal, "session_type", None) or SessionType.LONDON,
            cycle_phase=getattr(signal, "cycle_phase", None),
            ambiguity_policy=intrabar_policy,
            fold_id=fold_id,
            run_fingerprint=run_fingerprint,
            execution_evidence_fingerprint=evidence_fp,
            dependency_window=(signal_ts, dependency_end),
            tp2_reached_after_tp1=tp2_hit,
        )
