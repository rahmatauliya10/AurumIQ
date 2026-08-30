"""Deterministic outcome resolution engine with causal entry fill, intrabar barrier replay, and post-fill MFE/MAE."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional, Sequence, Tuple

from engine.backtest.costs import CostModel
from engine.backtest.types import (
    SimulatedTrade,
    TradeOutcome,
)
from engine.core.types import (
    BarrierHitType,
    CandleData,
    EntryExecutionPolicy,
    IntrabarPolicy,
    QuoteData,
    RiskPlanSnapshot,
    SignalSnapshot,
)
from engine.risk.execution import EntryExecutionModel
from engine.risk.intrabar import IntrabarResolver


def _to_utc(dt: datetime) -> datetime:
    """Ensure datetime is UTC."""
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class OutcomeEngine:
    """
    Simulates complete point-in-time trade lifecycle after a BUY_WINDOW signal is emitted.

    Strict Invariants (P6-C1, P6-C2, P6-C5):
      1. Causality: Fill occurs strictly at or after signal_timestamp + latency.
      2. No Lookahead: Decision evaluation at T has zero knowledge of outcome data.
      3. Intrabar Resolution: Uses Phase 5 IntrabarResolver with conservative fallback.
      4. Frozen R Denominator: planned_risk_amount = entry_max - stop_final > 0.
      5. Post-fill MFE/MAE: strictly excludes candles in-progress at fill_timestamp.
      6. TP1 is terminal profit target; TP2 is recorded strictly as observational extension.
    """

    def __init__(
        self,
        cost_model: Optional[CostModel] = None,
        execution_model: Optional[EntryExecutionModel] = None,
        intrabar_resolver: Optional[IntrabarResolver] = None,
        max_fill_wait_bars: int = 16,  # 4 hours on 15m
    ):
        self.cost_model = cost_model or CostModel()
        self.execution_model = execution_model or EntryExecutionModel()
        self.intrabar_resolver = intrabar_resolver or IntrabarResolver()
        self.max_fill_wait_bars = max_fill_wait_bars

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
    ) -> SimulatedTrade:
        """
        Execute point-in-time simulation of trade lifecycle.
        """
        signal_ts = _to_utc(signal.timestamp)
        planned_risk = (risk_plan.entry_max - risk_plan.stop_final).quantize(Decimal("0.01"))

        if planned_risk <= Decimal("0"):
            # Invalid planned risk unit -> skip trade
            return SimulatedTrade(
                trade_id=trade_id,
                source_signal_fingerprint=signal.analysis_fingerprint,
                signal_timestamp=signal_ts,
                risk_plan_fingerprint=f"risk-{signal.analysis_fingerprint[:16]}",
                planned_risk_amount=Decimal("0.01"),
                outcome=TradeOutcome.SKIPPED,
                run_fingerprint=run_fingerprint,
                fold_id=fold_id,
                dependency_window=(signal_ts, signal_ts),
            )

        # 1. Eligibility Check
        if not risk_plan.is_valid_risk_plan or not risk_plan.execution_eligible:
            return SimulatedTrade(
                trade_id=trade_id,
                source_signal_fingerprint=signal.analysis_fingerprint,
                signal_timestamp=signal_ts,
                risk_plan_fingerprint=f"risk-{signal.analysis_fingerprint[:16]}",
                planned_risk_amount=planned_risk,
                outcome=TradeOutcome.SKIPPED,
                run_fingerprint=run_fingerprint,
                fold_id=fold_id,
                dependency_window=(signal_ts, signal_ts),
            )

        # 2. Entry Fill Simulation (Phase 5 Execution Model)
        fill_res = None
        if execution_policy == EntryExecutionPolicy.NEXT_BAR_OPEN:
            fill_res = self.execution_model.simulate_next_bar_open(
                signal_generated_at=signal_ts,
                candles=future_candles_15m,
                spread_pct=Decimal("0.0"),
                slippage_pct=Decimal("0.0"),
            )
        elif execution_policy == EntryExecutionPolicy.MARKET_AFTER_SIGNAL and future_quotes:
            fill_res = self.execution_model.simulate_market_after_signal(
                signal_generated_at=signal_ts,
                quotes=future_quotes,
                slippage_pct=Decimal("0.0"),
            )
        elif execution_policy == EntryExecutionPolicy.LIMIT_ZONE:
            fill_res = self.execution_model.simulate_limit_zone(
                signal_generated_at=signal_ts,
                risk_plan=risk_plan,
                candles=future_candles_15m,
            )
        else:
            fill_res = self.execution_model.simulate_next_bar_open(
                signal_generated_at=signal_ts,
                candles=future_candles_15m,
                spread_pct=Decimal("0.0"),
                slippage_pct=Decimal("0.0"),
            )

        if not fill_res or not fill_res.is_filled:
            # Determine timeout timestamp for no-fill dependency window
            timeout_ts = signal_ts + timedelta(minutes=15 * self.max_fill_wait_bars)
            return SimulatedTrade(
                trade_id=trade_id,
                source_signal_fingerprint=signal.analysis_fingerprint,
                signal_timestamp=signal_ts,
                risk_plan_fingerprint=f"risk-{signal.analysis_fingerprint[:16]}",
                planned_risk_amount=planned_risk,
                outcome=TradeOutcome.NO_FILL,
                run_fingerprint=run_fingerprint,
                fold_id=fold_id,
                dependency_window=(signal_ts, timeout_ts),
            )

        raw_fill_price = fill_res.fill_price
        fill_ts = _to_utc(fill_res.fill_timestamp)
        is_quote_entry = execution_policy == EntryExecutionPolicy.MARKET_AFTER_SIGNAL

        entry_cost = self.cost_model.calculate_entry(
            raw_price=raw_fill_price,
            is_actual_ask_quote=is_quote_entry,
        )

        tp1_target = risk_plan.tp1
        tp2_target = risk_plan.tp2
        sl_target = risk_plan.stop_final

        # 3. Post-Fill Barrier Simulation
        # Filter subsequent 15m candles occurring on or after fill_ts
        monitoring_bars = [
            c for c in future_candles_15m
            if _to_utc(c.timestamp_close) > fill_ts
        ]

        if not monitoring_bars:
            return SimulatedTrade(
                trade_id=trade_id,
                source_signal_fingerprint=signal.analysis_fingerprint,
                signal_timestamp=signal_ts,
                risk_plan_fingerprint=f"risk-{signal.analysis_fingerprint[:16]}",
                planned_risk_amount=planned_risk,
                outcome=TradeOutcome.UNRESOLVED,
                fill_timestamp=fill_ts,
                fill_price=raw_fill_price,
                entry_fee=entry_cost.fee_cost,
                entry_spread=entry_cost.spread_cost,
                entry_slippage=entry_cost.slippage_cost,
                run_fingerprint=run_fingerprint,
                fold_id=fold_id,
                dependency_window=(signal_ts, fill_ts),
            )

        exit_ts: Optional[datetime] = None
        raw_exit_price: Optional[Decimal] = None
        terminal_outcome: TradeOutcome = TradeOutcome.UNRESOLVED
        applied_policy = intrabar_policy
        tp2_hit = False

        for bar in monitoring_bars:
            b_high = bar.high
            b_low = bar.low
            b_close_ts = _to_utc(bar.timestamp_close)

            hit_tp1 = b_high >= tp1_target
            hit_sl = b_low <= sl_target

            if hit_tp1 and not hit_sl:
                terminal_outcome = TradeOutcome.TP1_FIRST
                exit_ts = b_close_ts
                raw_exit_price = tp1_target
                break

            elif hit_sl and not hit_tp1:
                terminal_outcome = TradeOutcome.SL_FIRST
                exit_ts = b_close_ts
                raw_exit_price = sl_target
                break

            elif hit_tp1 and hit_sl:
                # Ambiguous intrabar hit -> Resolve via Phase 5 IntrabarResolver
                resolution = self.intrabar_resolver.resolve(
                    parent_candle=bar,
                    tp_price=tp1_target,
                    sl_price=sl_target,
                    fill_timestamp=fill_ts,
                    lower_tf_candles_15m=future_candles_15m,
                    lower_tf_candles_5m=future_candles_5m,
                    lower_tf_candles_1m=future_candles_1m,
                    policy=intrabar_policy,
                )

                if resolution.barrier_hit == BarrierHitType.TP_FIRST:
                    terminal_outcome = TradeOutcome.TP1_FIRST
                    raw_exit_price = tp1_target
                    exit_ts = _to_utc(resolution.exit_timestamp)
                elif resolution.barrier_hit == BarrierHitType.SL_FIRST:
                    if resolution.policy_applied == IntrabarPolicy.CONSERVATIVE_SL_FIRST:
                        terminal_outcome = TradeOutcome.CONSERVATIVE_SL_FIRST
                    else:
                        terminal_outcome = TradeOutcome.SL_FIRST
                    raw_exit_price = sl_target
                    exit_ts = _to_utc(resolution.exit_timestamp)
                else:
                    terminal_outcome = TradeOutcome.CONSERVATIVE_SL_FIRST
                    raw_exit_price = sl_target
                    exit_ts = b_close_ts

                applied_policy = resolution.policy_applied
                break

        if terminal_outcome == TradeOutcome.UNRESOLVED or raw_exit_price is None or exit_ts is None:
            # Unresolved before dataset end
            last_bar = monitoring_bars[-1]
            return SimulatedTrade(
                trade_id=trade_id,
                source_signal_fingerprint=signal.analysis_fingerprint,
                signal_timestamp=signal_ts,
                risk_plan_fingerprint=f"risk-{signal.analysis_fingerprint[:16]}",
                planned_risk_amount=planned_risk,
                outcome=TradeOutcome.UNRESOLVED,
                fill_timestamp=fill_ts,
                fill_price=raw_fill_price,
                entry_fee=entry_cost.fee_cost,
                entry_spread=entry_cost.spread_cost,
                entry_slippage=entry_cost.slippage_cost,
                run_fingerprint=run_fingerprint,
                fold_id=fold_id,
                dependency_window=(signal_ts, _to_utc(last_bar.timestamp_close)),
            )

        # 4. Post-Fill MFE / MAE Calculation (P6-C5)
        # Exclude candles whose interval contains or precedes fill_ts
        eligible_mfe_bars: List[CandleData] = []
        if future_candles_1m:
            eligible_mfe_bars = [
                c for c in future_candles_1m
                if _to_utc(c.timestamp_open) >= fill_ts and _to_utc(c.timestamp_close) <= exit_ts
            ]
        elif future_candles_5m:
            eligible_mfe_bars = [
                c for c in future_candles_5m
                if _to_utc(c.timestamp_open) >= fill_ts and _to_utc(c.timestamp_close) <= exit_ts
            ]
        else:
            eligible_mfe_bars = [
                c for c in future_candles_15m
                if _to_utc(c.timestamp_open) >= fill_ts and _to_utc(c.timestamp_close) <= exit_ts
            ]

        if eligible_mfe_bars:
            highest_post_fill = max(c.high for c in eligible_mfe_bars)
            lowest_post_fill = min(c.low for c in eligible_mfe_bars)
        else:
            highest_post_fill = max(raw_fill_price, raw_exit_price)
            lowest_post_fill = min(raw_fill_price, raw_exit_price)

        mfe_abs = max(Decimal("0.0"), highest_post_fill - raw_fill_price)
        mae_abs = max(Decimal("0.0"), raw_fill_price - lowest_post_fill)

        mfe_r = (mfe_abs / planned_risk).quantize(Decimal("0.0001"))
        mae_r = (mae_abs / planned_risk).quantize(Decimal("0.0001"))

        # Check observational TP2 reach
        if tp2_target and highest_post_fill >= tp2_target:
            tp2_hit = True
        max_ext_r = ((highest_post_fill - raw_fill_price) / planned_risk).quantize(Decimal("0.0001"))

        # 5. Exit Friction & Final PnL Accounting
        exit_cost = self.cost_model.calculate_exit(
            raw_price=raw_exit_price,
            is_actual_bid_quote=False,
        )

        (
            gross_pnl,
            net_pnl,
            gross_r,
            net_r,
            gross_ret_pct,
            net_ret_pct,
        ) = self.cost_model.compute_r_and_returns(
            raw_entry_price=raw_fill_price,
            effective_entry_price=entry_cost.effective_entry_price,
            raw_exit_price=raw_exit_price,
            effective_exit_price=exit_cost.effective_exit_price,
            planned_risk_amount=planned_risk,
        )

        holding_duration = (exit_ts - fill_ts).total_seconds()

        # Safe regime extraction from signal if present
        from engine.core.types import RegimeType, SessionType
        regime_enum = RegimeType.BULL_TREND if signal.direction and "BULL" in str(signal.direction) else RegimeType.UNKNOWN
        session_enum = SessionType.LONDON

        return SimulatedTrade(
            trade_id=trade_id,
            source_signal_fingerprint=signal.analysis_fingerprint,
            signal_timestamp=signal_ts,
            risk_plan_fingerprint=f"risk-{signal.analysis_fingerprint[:16]}",
            planned_risk_amount=planned_risk,
            outcome=terminal_outcome,
            fill_timestamp=fill_ts,
            fill_price=raw_fill_price,
            exit_timestamp=exit_ts,
            exit_price=raw_exit_price,
            gross_pnl_per_unit=gross_pnl,
            net_pnl_per_unit=net_pnl,
            gross_r=gross_r,
            net_r=net_r,
            gross_return_pct=gross_ret_pct,
            net_return_pct=net_ret_pct,
            mfe_r=mfe_r,
            mae_r=mae_r,
            holding_duration_seconds=holding_duration,
            entry_fee=entry_cost.fee_cost,
            exit_fee=exit_cost.fee_cost,
            entry_spread=entry_cost.spread_cost,
            exit_spread=exit_cost.spread_cost,
            entry_slippage=entry_cost.slippage_cost,
            exit_slippage=exit_cost.slippage_cost,
            regime=regime_enum,
            session=session_enum,
            ambiguity_policy=applied_policy,
            fold_id=fold_id,
            run_fingerprint=run_fingerprint,
            dependency_window=(signal_ts, exit_ts),
            tp2_reached_after_tp1=tp2_hit,
            max_favorable_extension_r=max_ext_r,
        )
