"""Comprehensive metrics computation for deterministic XAUUSD backtesting performance, friction, and robustness."""
import math
import statistics
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

from engine.backtest.xauusd_types import (
    XauUsdBacktestMetrics,
    XauUsdSimulatedTrade,
    XauUsdSubsystemBreakdown,
    XauUsdTradeOutcome,
)
from engine.core.types import (
    DualSideSignalSnapshot,
    RegimeType,
    SessionType,
    SignalSide,
    SignalState,
    UserDecision,
)


class XauUsdMetricsCalculator:
    """
    Computes deterministic XAUUSD backtest statistics.

    Strict Invariants:
      1. R denominator is strictly planned_risk_amount.
      2. Drawdown is strictly normalized trade-sequence drawdown in R (zero account sizing).
      3. Subsystem breakdowns preserve the exact same metric formulas across subsets.
      4. Fully side-aware: Supports LONG, SHORT, and COMBINED evaluations independently.
    """

    @classmethod
    def calculate(
        cls,
        signals: Sequence[DualSideSignalSnapshot],
        trades: Sequence[XauUsdSimulatedTrade],
        filter_side: Optional[SignalSide] = None,
    ) -> XauUsdBacktestMetrics:
        """Calculate complete backtest metrics from signal and trade ledgers."""
        # 1. Candidate Counts
        if filter_side == SignalSide.LONG:
            eval_signals = [s for s in signals if s.candidate_state == SignalState.BUY_WINDOW]
            eval_trades = [t for t in trades if t.side == SignalSide.LONG]
        elif filter_side == SignalSide.SHORT:
            eval_signals = [s for s in signals if s.candidate_state == SignalState.SELL_WINDOW]
            eval_trades = [t for t in trades if t.side == SignalSide.SHORT]
        else:
            eval_signals = list(signals)
            eval_trades = list(trades)

        long_candidate_count = sum(1 for s in signals if s.candidate_state == SignalState.BUY_WINDOW)
        short_candidate_count = sum(1 for s in signals if s.candidate_state == SignalState.SELL_WINDOW)
        wait_count = sum(1 for s in signals if s.candidate_state in (SignalState.FORCE_WAIT, SignalState.NO_TRADE, SignalState.AVOID) or s.candidate_user_decision == UserDecision.WAIT)
        conflict_count = sum(1 for s in signals if s.candidate_state == SignalState.CONFLICT)

        if filter_side == SignalSide.LONG:
            candidate_count = long_candidate_count
        elif filter_side == SignalSide.SHORT:
            candidate_count = short_candidate_count
        else:
            candidate_count = len(eval_signals)

        # 2. Risk Validation Funnel
        valid_risk_trades = [t for t in eval_trades if t.outcome != XauUsdTradeOutcome.SKIPPED]
        valid_risk_count = len(valid_risk_trades)
        long_valid_risk_count = sum(1 for t in trades if t.side == SignalSide.LONG and t.outcome != XauUsdTradeOutcome.SKIPPED)
        short_valid_risk_count = sum(1 for t in trades if t.side == SignalSide.SHORT and t.outcome != XauUsdTradeOutcome.SKIPPED)

        execution_eligible_count = valid_risk_count

        # 3. Fill and Execution Stats
        filled_trades = [
            t for t in valid_risk_trades
            if t.fill_timestamp is not None and t.outcome != XauUsdTradeOutcome.NO_FILL
        ]
        fill_count = len(filled_trades)
        no_fill_count = sum(1 for t in valid_risk_trades if t.outcome == XauUsdTradeOutcome.NO_FILL)

        fill_rate = float(fill_count / execution_eligible_count) if execution_eligible_count > 0 else 0.0
        no_fill_rate = float(no_fill_count / execution_eligible_count) if execution_eligible_count > 0 else 0.0
        trade_count = fill_count
        long_trade_count = sum(1 for t in filled_trades if t.side == SignalSide.LONG)
        short_trade_count = sum(1 for t in filled_trades if t.side == SignalSide.SHORT)

        # 4. Terminal Outcome Breakdown
        tp1_first_count = sum(1 for t in filled_trades if t.outcome == XauUsdTradeOutcome.TP1_FIRST)
        sl_first_count = sum(1 for t in filled_trades if t.outcome == XauUsdTradeOutcome.SL_FIRST)
        conservative_sl_first_count = sum(1 for t in filled_trades if t.outcome == XauUsdTradeOutcome.CONSERVATIVE_SL_FIRST)
        unresolved_count = sum(1 for t in filled_trades if t.outcome == XauUsdTradeOutcome.UNRESOLVED)
        timeout_count = sum(1 for t in filled_trades if t.outcome == XauUsdTradeOutcome.TIMEOUT)
        conservative_resolution_rate = (
            float(conservative_sl_first_count / trade_count) if trade_count > 0 else 0.0
        )

        # 5. Payoff Profile (Normalized R)
        net_r_list = [float(t.net_r or Decimal("0")) for t in filled_trades]
        gross_r_list = [float(t.gross_r or Decimal("0")) for t in filled_trades]

        win_trades = [r for r in net_r_list if r > 0.0]
        loss_trades = [r for r in net_r_list if r <= 0.0]

        win_count = len(win_trades)
        loss_count = len(loss_trades)
        win_rate = float(win_count / trade_count) if trade_count > 0 else 0.0
        loss_rate = float(loss_count / trade_count) if trade_count > 0 else 0.0

        avg_win_r = float(statistics.mean(win_trades)) if win_trades else 0.0
        avg_loss_r = float(statistics.mean([abs(r) for r in loss_trades])) if loss_trades else 0.0
        payoff_ratio = float(avg_win_r / avg_loss_r) if avg_loss_r > 0 else 0.0

        # 6. Expectancy & Profitability
        gross_expectancy_r = float(statistics.mean(gross_r_list)) if gross_r_list else 0.0
        net_expectancy_r = (win_rate * avg_win_r) - (loss_rate * avg_loss_r) if trade_count > 0 else 0.0
        average_r = float(statistics.mean(net_r_list)) if net_r_list else 0.0
        median_r = float(statistics.median(net_r_list)) if net_r_list else 0.0

        # 7. Profit Factor
        gross_pnl_wins = sum(float(t.net_pnl_per_unit or Decimal("0")) for t in filled_trades if (t.net_pnl_per_unit or Decimal("0")) > 0)
        gross_pnl_losses = sum(abs(float(t.net_pnl_per_unit or Decimal("0"))) for t in filled_trades if (t.net_pnl_per_unit or Decimal("0")) <= 0)

        if gross_pnl_losses > 0:
            profit_factor = float(gross_pnl_wins / gross_pnl_losses)
        elif gross_pnl_wins > 0:
            profit_factor = 999.0
        else:
            profit_factor = 0.0

        gross_return_pct = float(sum(float(t.gross_return_pct or Decimal("0")) for t in filled_trades))
        net_return_pct = float(sum(float(t.net_return_pct or Decimal("0")) for t in filled_trades))

        # 8. Downside Risk (Normalized Trade Sequence Drawdown in R)
        max_dd_r = 0.0
        current_dd_trades = 0
        max_dd_duration_trades = 0
        peak_r = 0.0
        cum_r = 0.0

        for r in net_r_list:
            cum_r += r
            if cum_r > peak_r:
                peak_r = cum_r
                current_dd_trades = 0
            else:
                dd = peak_r - cum_r
                if dd > max_dd_r:
                    max_dd_r = dd
                current_dd_trades += 1
                if current_dd_trades > max_dd_duration_trades:
                    max_dd_duration_trades = current_dd_trades

        # Maximum Consecutive Losses
        max_consecutive_losses = 0
        cur_loss_streak = 0
        for r in net_r_list:
            if r <= 0.0:
                cur_loss_streak += 1
                if cur_loss_streak > max_consecutive_losses:
                    max_consecutive_losses = cur_loss_streak
            else:
                cur_loss_streak = 0

        # 9. Execution Quality (MFE & MAE in R)
        mfe_r_list = [float(t.mfe_r or Decimal("0")) for t in filled_trades]
        mae_r_list = [float(t.mae_r or Decimal("0")) for t in filled_trades]

        average_mfe_r = float(statistics.mean(mfe_r_list)) if mfe_r_list else 0.0
        median_mfe_r = float(statistics.median(mfe_r_list)) if mfe_r_list else 0.0
        average_mae_r = float(statistics.mean(mae_r_list)) if mae_r_list else 0.0
        median_mae_r = float(statistics.median(mae_r_list)) if mae_r_list else 0.0

        durations = [t.holding_duration_seconds for t in filled_trades if t.holding_duration_seconds is not None]
        avg_holding = float(statistics.mean(durations)) if durations else 0.0
        med_holding = float(statistics.median(durations)) if durations else 0.0

        # 10. Friction & Cost Drag
        tot_entry_fees = float(sum(t.entry_fee for t in filled_trades))
        tot_exit_fees = float(sum(t.exit_fee for t in filled_trades))
        tot_spread = float(sum(t.entry_spread + t.exit_spread for t in filled_trades))
        tot_slip = float(sum(t.entry_slippage + t.exit_slippage for t in filled_trades))

        cost_drag_r = gross_expectancy_r - average_r
        cost_drag_pct = gross_return_pct - net_return_pct

        # 11. Subsystem Breakdowns (if combined calculation)
        subsystems = None
        if filter_side is None:
            subsystems = cls._compute_subsystems(signals, filled_trades)

        return XauUsdBacktestMetrics(
            candidate_count=candidate_count,
            long_candidate_count=long_candidate_count,
            short_candidate_count=short_candidate_count,
            valid_risk_count=valid_risk_count,
            long_valid_risk_count=long_valid_risk_count,
            short_valid_risk_count=short_valid_risk_count,
            execution_eligible_count=execution_eligible_count,
            fill_count=fill_count,
            no_fill_count=no_fill_count,
            fill_rate=fill_rate,
            no_fill_rate=no_fill_rate,
            trade_count=trade_count,
            long_trade_count=long_trade_count,
            short_trade_count=short_trade_count,
            win_count=win_count,
            loss_count=loss_count,
            win_rate=win_rate,
            loss_rate=loss_rate,
            avg_win_r=avg_win_r,
            avg_loss_r=avg_loss_r,
            payoff_ratio=payoff_ratio,
            gross_expectancy_r=gross_expectancy_r,
            net_expectancy_r=net_expectancy_r,
            average_r=average_r,
            median_r=median_r,
            profit_factor=profit_factor,
            gross_return_pct=gross_return_pct,
            net_return_pct=net_return_pct,
            max_drawdown_r=max_dd_r,
            drawdown_duration_trades=max_dd_duration_trades,
            maximum_consecutive_losses=max_consecutive_losses,
            average_mfe_r=average_mfe_r,
            median_mfe_r=median_mfe_r,
            average_mae_r=average_mae_r,
            median_mae_r=median_mae_r,
            average_holding_duration_seconds=avg_holding,
            median_holding_duration_seconds=med_holding,
            tp1_first_count=tp1_first_count,
            sl_first_count=sl_first_count,
            conservative_sl_first_count=conservative_sl_first_count,
            unresolved_count=unresolved_count,
            timeout_count=timeout_count,
            conservative_resolution_rate=conservative_resolution_rate,
            total_entry_fees=tot_entry_fees,
            total_exit_fees=tot_exit_fees,
            total_spread_cost=tot_spread,
            total_slippage_cost=tot_slip,
            cost_drag_r=cost_drag_r,
            cost_drag_pct=cost_drag_pct,
            wait_count=wait_count,
            conflict_count=conflict_count,
            subsystems=subsystems,
        )

    @classmethod
    def _compute_subsystems(
        cls,
        signals: Sequence[DualSideSignalSnapshot],
        filled_trades: Sequence[XauUsdSimulatedTrade],
    ) -> XauUsdSubsystemBreakdown:
        """Partition trade outcomes across side, regime, and session."""
        side_dict: Dict[str, Any] = {}
        for side in [SignalSide.LONG, SignalSide.SHORT]:
            s_trades = [t for t in filled_trades if t.side == side]
            r_list = [float(t.net_r or Decimal("0")) for t in s_trades]
            side_dict[side.value if hasattr(side, "value") else str(side)] = {
                "trade_count": len(s_trades),
                "win_count": sum(1 for r in r_list if r > 0),
                "win_rate": float(sum(1 for r in r_list if r > 0) / len(s_trades)) if s_trades else 0.0,
                "average_r": float(statistics.mean(r_list)) if r_list else 0.0,
            }

        regime_dict: Dict[str, Any] = {}
        for regime in [RegimeType.BULL_TREND, RegimeType.BEAR_TREND, RegimeType.RANGE, RegimeType.HIGH_VOLATILITY, RegimeType.TRANSITION]:
            r_trades = [t for t in filled_trades if t.regime == regime]
            r_list = [float(t.net_r or Decimal("0")) for t in r_trades]
            regime_dict[regime.value if hasattr(regime, "value") else str(regime)] = {
                "trade_count": len(r_trades),
                "win_count": sum(1 for r in r_list if r > 0),
                "win_rate": float(sum(1 for r in r_list if r > 0) / len(r_trades)) if r_trades else 0.0,
                "average_r": float(statistics.mean(r_list)) if r_list else 0.0,
            }

        session_dict: Dict[str, Any] = {}
        for session in [SessionType.ASIA, SessionType.LONDON_PREOPEN, SessionType.LONDON, SessionType.LONDON_NY_OVERLAP, SessionType.NEW_YORK, SessionType.US_LATE]:
            sess_trades = [t for t in filled_trades if t.session == session]
            r_list = [float(t.net_r or Decimal("0")) for t in sess_trades]
            session_dict[session.value if hasattr(session, "value") else str(session)] = {
                "trade_count": len(sess_trades),
                "win_count": sum(1 for r in r_list if r > 0),
                "win_rate": float(sum(1 for r in r_list if r > 0) / len(sess_trades)) if sess_trades else 0.0,
                "average_r": float(statistics.mean(r_list)) if r_list else 0.0,
            }

        return XauUsdSubsystemBreakdown(
            regime_breakdown=regime_dict,
            session_breakdown=session_dict,
            side_breakdown=side_dict,
        )
