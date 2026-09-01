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
    EntryExecutionPolicy,
    IntrabarPolicy,
    QuoteData,
    RegimeType,
    RiskPlanSnapshot,
    RiskSide,
    SessionType,
    SignalSide,
    SignalSnapshot,
)
from engine.risk.xauusd_execution import SideAwareEntryExecutionModel
from engine.risk.xauusd_intrabar import SideAwareIntrabarResolver
from engine.risk.xauusd_policy import XauUsdExecutionPolicy


def _to_utc(dt: datetime) -> datetime:
    """Ensure datetime is UTC."""
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class XauUsdOutcomeEngine:
    """
    Simulates complete point-in-time trade lifecycle after a BUY_WINDOW or SELL_WINDOW candidate signal is emitted.

    Strict Invariants:
      1. Causality: Fill occurs strictly at or after earliest_exec_ts = signal_timestamp + latency.
      2. No Lookahead: Decision evaluation at T has zero knowledge of outcome data.
      3. Intrabar Resolution: Uses Phase 5 SideAwareIntrabarResolver with conservative fallback.
      4. Frozen R Denominator:
         LONG: planned_risk = entry_max - stop_final > 0
         SHORT: planned_risk = stop_final - entry_min > 0
      5. Post-fill MFE/MAE: strictly excludes candles in-progress at fill_timestamp.
      6. TP1 is terminal profit target; TP2 is recorded strictly as observational extension.
      7. Decimal domain arithmetic preserved without float conversion loss.
    """

    def __init__(
        self,
        cost_config: Optional[XauUsdCostConfig] = None,
        execution_model: Optional[SideAwareEntryExecutionModel] = None,
        intrabar_resolver: Optional[SideAwareIntrabarResolver] = None,
        max_fill_wait_bars: int = 16,  # 4 hours on 15m
        holding_horizon_bars_15m: Optional[int] = None,
        holding_horizon_seconds: Optional[float] = None,
    ):
        self.cost_config = cost_config or XauUsdCostConfig.idealized()
        self.execution_model = execution_model
        self.intrabar_resolver = intrabar_resolver or SideAwareIntrabarResolver()
        self.max_fill_wait_bars = max_fill_wait_bars
        self.holding_horizon_bars_15m = holding_horizon_bars_15m
        self.holding_horizon_seconds = holding_horizon_seconds

    def resolve_trade(
        self,
        signal: SignalSnapshot,
        risk_plan: RiskPlanSnapshot,
        future_candles_15m: Sequence[CandleData],
        future_candles_5m: Optional[Sequence[CandleData]] = None,
        future_candles_1m: Optional[Sequence[CandleData]] = None,
        future_quotes: Optional[Sequence[QuoteData]] = None,
        execution_policy: EntryExecutionPolicy = EntryExecutionPolicy.NEXT_BAR_OPEN,
        intrabar_policy: IntrabarPolicy = IntrabarPolicy.LOWER_TIMEFRAME_REPLAY,
        trade_id: str = "trade-1",
        run_fingerprint: str = "",
        fold_id: Optional[int] = None,
        holding_horizon_bars_15m: Optional[int] = None,
        holding_horizon_seconds: Optional[float] = None,
    ) -> XauUsdSimulatedTrade:
        """
        Execute point-in-time simulation of XAUUSD trade lifecycle (LONG or SHORT).
        """
        signal_ts = _to_utc(signal.timestamp)
        side = getattr(risk_plan, "side", SignalSide.LONG)

        # 1. Compute Frozen Planned Risk Denominator
        if side == SignalSide.LONG:
            planned_risk = risk_plan.entry_max - risk_plan.stop_final
        else:
            planned_risk = risk_plan.stop_final - risk_plan.entry_min

        if planned_risk <= Decimal("0"):
            # Invalid planned risk unit -> skip trade
            return XauUsdSimulatedTrade(
                trade_id=trade_id,
                side=side,
                candidate_state=signal.candidate_state,
                candidate_user_decision=signal.candidate_user_decision,
                source_signal_fingerprint=signal.analysis_fingerprint,
                signal_timestamp=signal_ts,
                risk_plan_fingerprint=getattr(risk_plan, "risk_plan_fingerprint", f"risk-{signal.analysis_fingerprint[:16]}"),
                planned_risk_amount=Decimal("0.00"),
                outcome=XauUsdTradeOutcome.SKIPPED,
                run_fingerprint=run_fingerprint,
                fold_id=fold_id,
                dependency_window=(signal_ts, signal_ts),
            )

        # 2. Build or resolve execution model
        exec_model = self.execution_model
        if exec_model is None:
            exec_policy_obj = XauUsdExecutionPolicy(
                latency_seconds=1.0,
                synthetic_spread_pct=(self.cost_config.synthetic_spread_bps / Decimal("10000")) if self.cost_config else Decimal("0.00"),
                slippage_pct=(self.cost_config.entry_slippage_bps / Decimal("10000")) if self.cost_config else Decimal("0.00"),
            )
            exec_model = SideAwareEntryExecutionModel(
                code_revision=getattr(risk_plan, "code_revision", "46e388a106b9bdc388e646c73570e7879142c837"),
                execution_policy=exec_policy_obj,
                phase5_policy_fingerprint=getattr(risk_plan, "phase5_policy_fingerprint", "phase5_policy_default"),
            )

        # 3. Simulate Entry Fill
        risk_side = RiskSide.LONG if side == SignalSide.LONG else RiskSide.SHORT
        if execution_policy == EntryExecutionPolicy.NEXT_BAR_OPEN:
            fill_result = exec_model.simulate_next_bar_open(
                side=risk_side,
                signal_generated_at=signal_ts,
                candles=future_candles_15m[:self.max_fill_wait_bars] if future_candles_15m else (),
                source_phase4_fingerprint=signal.analysis_fingerprint,
            )
        elif execution_policy == EntryExecutionPolicy.MARKET_AFTER_SIGNAL:
            fill_result = exec_model.simulate_market_after_signal(
                side=risk_side,
                signal_generated_at=signal_ts,
                quotes=future_quotes if future_quotes else (),
                source_phase4_fingerprint=signal.analysis_fingerprint,
            )
        elif execution_policy == EntryExecutionPolicy.LIMIT_ZONE:
            limit_p = risk_plan.entry_max if side == SignalSide.LONG else risk_plan.entry_min
            fill_result = exec_model.simulate_limit_zone(
                side=risk_side,
                signal_generated_at=signal_ts,
                limit_price=limit_p,
                quotes=future_quotes if future_quotes else (),
                candles=future_candles_15m[:self.max_fill_wait_bars] if future_candles_15m else (),
                source_phase4_fingerprint=signal.analysis_fingerprint,
            )
        else:
            fill_result = exec_model.simulate_next_bar_open(
                side=risk_side,
                signal_generated_at=signal_ts,
                candles=future_candles_15m[:self.max_fill_wait_bars] if future_candles_15m else (),
                source_phase4_fingerprint=signal.analysis_fingerprint,
            )

        max_search_ts = signal_ts
        if future_candles_15m and len(future_candles_15m) > 0:
            max_search_idx = min(len(future_candles_15m), self.max_fill_wait_bars) - 1
            max_search_ts = _to_utc(future_candles_15m[max_search_idx].timestamp_close)

        if not fill_result.is_filled or fill_result.fill_price is None or fill_result.fill_timestamp is None:
            # NO_FILL outcome
            return XauUsdSimulatedTrade(
                trade_id=trade_id,
                side=side,
                candidate_state=signal.candidate_state,
                candidate_user_decision=signal.candidate_user_decision,
                source_signal_fingerprint=signal.analysis_fingerprint,
                signal_timestamp=signal_ts,
                risk_plan_fingerprint=getattr(risk_plan, "risk_plan_fingerprint", f"risk-{signal.analysis_fingerprint[:16]}"),
                planned_risk_amount=planned_risk,
                outcome=XauUsdTradeOutcome.NO_FILL,
                run_fingerprint=run_fingerprint,
                fold_id=fold_id,
                dependency_window=(signal_ts, max_search_ts),
            )

        fill_ts = _to_utc(fill_result.fill_timestamp)
        fill_price = fill_result.fill_price
        raw_entry_price = fill_result.raw_executable_price or fill_price

        # 4. Filter post-fill parent candles (15m)
        post_fill_15m = [
            c for c in future_candles_15m
            if _to_utc(c.timestamp_close) > fill_ts
        ]

        effective_horizon_bars = (
            holding_horizon_bars_15m
            if holding_horizon_bars_15m is not None
            else self.holding_horizon_bars_15m
        )
        effective_horizon_secs = (
            holding_horizon_seconds
            if holding_horizon_seconds is not None
            else self.holding_horizon_seconds
        )

        # Apply holding horizon limit if configured
        if effective_horizon_bars is not None and effective_horizon_bars > 0:
            eval_candles = post_fill_15m[:effective_horizon_bars]
        else:
            eval_candles = post_fill_15m

        tp1_price = risk_plan.tp1
        sl_price = risk_plan.stop_final
        tp2_price = risk_plan.tp2

        exit_ts: Optional[datetime] = None
        exit_price: Optional[Decimal] = None
        terminal_outcome = XauUsdTradeOutcome.UNRESOLVED
        hit_bar_index: Optional[int] = None
        conservative_flag = False

        # 5. Barrier Resolution Sequence
        for idx, candle in enumerate(eval_candles):
            c_open_ts = _to_utc(candle.timestamp_open)
            c_close_ts = _to_utc(candle.timestamp_close)

            # Check timeout duration if seconds horizon specified
            if effective_horizon_secs is not None and effective_horizon_secs > 0:
                elapsed = (c_close_ts - fill_ts).total_seconds()
                if elapsed > effective_horizon_secs:
                    terminal_outcome = XauUsdTradeOutcome.TIMEOUT
                    exit_ts = c_close_ts
                    exit_price = candle.close
                    hit_bar_index = idx
                    break

            # Find lower-TF bars matching this 15m parent candle
            matching_1m = None
            matching_5m = None
            if intrabar_policy == IntrabarPolicy.LOWER_TIMEFRAME_REPLAY:
                if future_candles_1m:
                    matching_1m = [
                        m for m in future_candles_1m
                        if c_open_ts <= _to_utc(m.timestamp_open) and _to_utc(m.timestamp_close) <= c_close_ts
                    ]
                if future_candles_5m:
                    matching_5m = [
                        m for m in future_candles_5m
                        if c_open_ts <= _to_utc(m.timestamp_open) and _to_utc(m.timestamp_close) <= c_close_ts
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

        # Check if horizon expired without hitting barrier -> TIMEOUT
        if terminal_outcome == XauUsdTradeOutcome.UNRESOLVED and len(eval_candles) > 0:
            if effective_horizon_bars is not None and len(post_fill_15m) >= effective_horizon_bars:
                terminal_outcome = XauUsdTradeOutcome.TIMEOUT
                last_bar = eval_candles[-1]
                exit_ts = _to_utc(last_bar.timestamp_close)
                exit_price = last_bar.close
                hit_bar_index = len(eval_candles) - 1

        if exit_ts is None:
            exit_ts = max_search_ts
        if exit_price is None:
            exit_price = fill_price

        # 6. Exit Costs and Friction Accounting
        raw_exit_price = exit_price
        # Synthetic exit spread: half-spread on exit if mid price
        exit_spread_cost = (
            raw_exit_price * (self.cost_config.synthetic_spread_bps / Decimal("20000"))
        ).quantize(Decimal("0.0001"))
        exit_slippage_cost = (
            raw_exit_price * (self.cost_config.exit_slippage_bps / Decimal("10000"))
        ).quantize(Decimal("0.0001"))
        exit_fee_cost = (
            raw_exit_price * (self.cost_config.exit_fee_bps / Decimal("10000"))
        ).quantize(Decimal("0.0001"))

        if side == SignalSide.LONG:
            # Closing long means selling -> lower proceeds
            effective_exit_price = raw_exit_price - exit_spread_cost - exit_slippage_cost - exit_fee_cost
            gross_pnl = raw_exit_price - raw_entry_price
            net_pnl = effective_exit_price - fill_price
        else:
            # Closing short means buying back -> higher cost
            effective_exit_price = raw_exit_price + exit_spread_cost + exit_slippage_cost + exit_fee_cost
            gross_pnl = raw_entry_price - raw_exit_price
            net_pnl = fill_price - effective_exit_price

        gross_r = (gross_pnl / planned_risk).quantize(Decimal("0.0001"))
        net_r = (net_pnl / planned_risk).quantize(Decimal("0.0001"))

        gross_return_pct = (
            (gross_pnl / raw_entry_price) * Decimal("100")
        ).quantize(Decimal("0.0001")) if raw_entry_price > Decimal("0") else Decimal("0.0000")

        net_return_pct = (
            (net_pnl / fill_price) * Decimal("100")
        ).quantize(Decimal("0.0001")) if fill_price > Decimal("0") else Decimal("0.0000")

        # 7. Post-Fill MFE / MAE Causality (excluding in-progress fill candle)
        # Candles strictly between fill_timestamp and exit_timestamp
        mfe_candles = [
            c for c in eval_candles
            if _to_utc(c.timestamp_open) >= fill_ts and _to_utc(c.timestamp_close) <= exit_ts
        ]

        if mfe_candles:
            if side == SignalSide.LONG:
                max_favorable_price = max(c.high for c in mfe_candles)
                max_adverse_price = min(c.low for c in mfe_candles)
                mfe_unit = max(Decimal("0.0"), max_favorable_price - fill_price)
                mae_unit = max(Decimal("0.0"), fill_price - max_adverse_price)
            else:
                max_favorable_price = min(c.low for c in mfe_candles)
                max_adverse_price = max(c.high for c in mfe_candles)
                mfe_unit = max(Decimal("0.0"), fill_price - max_favorable_price)
                mae_unit = max(Decimal("0.0"), max_adverse_price - fill_price)

            mfe_r = (mfe_unit / planned_risk).quantize(Decimal("0.0001"))
            mae_r = (mae_unit / planned_risk).quantize(Decimal("0.0001"))
        else:
            mfe_r = Decimal("0.0000")
            mae_r = Decimal("0.0000")

        # 8. Observational TP2 Extension Check (if TP1 was reached)
        tp2_reached = False
        max_fav_ext_r = None
        if terminal_outcome == XauUsdTradeOutcome.TP1_FIRST and tp2_price is not None and hit_bar_index is not None:
            remaining_bars = eval_candles[hit_bar_index:]
            if remaining_bars:
                if side == SignalSide.LONG:
                    highest_ext = max(c.high for c in remaining_bars)
                    if highest_ext >= tp2_price:
                        tp2_reached = True
                    ext_unit = max(Decimal("0.0"), highest_ext - fill_price)
                else:
                    lowest_ext = min(c.low for c in remaining_bars)
                    if lowest_ext <= tp2_price:
                        tp2_reached = True
                    ext_unit = max(Decimal("0.0"), fill_price - lowest_ext)
                max_fav_ext_r = (ext_unit / planned_risk).quantize(Decimal("0.0001"))

        holding_secs = max(0.0, (exit_ts - fill_ts).total_seconds())

        return XauUsdSimulatedTrade(
            trade_id=trade_id,
            side=side,
            candidate_state=signal.candidate_state,
            candidate_user_decision=signal.candidate_user_decision,
            source_signal_fingerprint=signal.analysis_fingerprint,
            signal_timestamp=signal_ts,
            risk_plan_fingerprint=getattr(risk_plan, "risk_plan_fingerprint", f"risk-{signal.analysis_fingerprint[:16]}"),
            planned_risk_amount=planned_risk,
            outcome=XauUsdTradeOutcome.CONSERVATIVE_SL_FIRST if conservative_flag else terminal_outcome,
            fill_timestamp=fill_ts,
            fill_price=fill_price,
            exit_timestamp=exit_ts,
            exit_price=effective_exit_price,
            gross_pnl_per_unit=gross_pnl,
            net_pnl_per_unit=net_pnl,
            gross_r=gross_r,
            net_r=net_r,
            gross_return_pct=gross_return_pct,
            net_return_pct=net_return_pct,
            mfe_r=mfe_r,
            mae_r=mae_r,
            holding_duration_seconds=holding_secs,
            entry_fee=fill_result.entry_fee if hasattr(fill_result, "entry_fee") else Decimal("0.0"),
            exit_fee=exit_fee_cost,
            entry_spread=fill_result.synthetic_spread if hasattr(fill_result, "synthetic_spread") else Decimal("0.0"),
            exit_spread=exit_spread_cost,
            entry_slippage=fill_result.slippage if hasattr(fill_result, "slippage") else Decimal("0.0"),
            exit_slippage=exit_slippage_cost,
            regime=getattr(signal, "regime", None) or getattr(signal, "regime_type", None) or getattr(signal, "market_regime", None) or RegimeType.UNKNOWN,
            session=getattr(signal, "session", None) or getattr(signal, "session_type", None) or SessionType.LONDON,
            cycle_phase=getattr(signal, "cycle_phase", None),
            ambiguity_policy=intrabar_policy,
            fold_id=fold_id,
            run_fingerprint=run_fingerprint,
            execution_evidence_fingerprint=fill_result.source_evidence_fingerprint,
            dependency_window=(signal_ts, exit_ts),
            tp2_reached_after_tp1=tp2_reached,
            max_favorable_extension_r=max_fav_ext_r,
        )
