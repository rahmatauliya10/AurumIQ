"""Comprehensive metrics computation for backtesting performance, friction, and robustness."""
import math
import statistics
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Tuple

from engine.backtest.types import (
    BacktestMetrics,
    SimulatedTrade,
    SubsystemPerformance,
    TradeOutcome,
)
from engine.core.types import SignalSnapshot, UserDecision


class BacktestMetricsCalculator:
    """
    Computes deterministic backtest statistics.

    Strict Invariants (P6-C3, P6-C4):
      1. R denominator is strictly planned_risk_amount.
      2. Drawdown is strictly normalized trade-sequence drawdown in R.
      3. Sharpe/Sortino are explicitly computed on daily normalized returns series.
      4. Subsystem breakdowns preserve the same metric formulas across subsets.
    """

    @staticmethod
    def calculate(
        signals: Sequence[SignalSnapshot],
        trades: Sequence[SimulatedTrade],
    ) -> BacktestMetrics:
        """Calculate complete backtest metrics from signal and trade ledgers."""
        signal_count = len(signals)
        wait_count = sum(1 for s in signals if s.user_decision == UserDecision.WAIT)
        avoid_count = sum(1 for s in signals if s.user_decision == UserDecision.AVOID)
        buy_window_count = sum(1 for s in signals if s.user_decision == UserDecision.BUY)

        valid_risk_plan_count = len(trades)
        eligible_trades = [t for t in trades if t.outcome != TradeOutcome.SKIPPED]
        execution_eligible_count = len(eligible_trades)

        filled_trades = [t for t in eligible_trades if t.fill_timestamp is not None and t.outcome != TradeOutcome.NO_FILL]
        fill_count = len(filled_trades)
        no_fill_count = sum(1 for t in eligible_trades if t.outcome == TradeOutcome.NO_FILL)

        fill_rate = float(fill_count / execution_eligible_count) if execution_eligible_count > 0 else 0.0
        no_fill_rate = float(no_fill_count / execution_eligible_count) if execution_eligible_count > 0 else 0.0
        trade_count = fill_count

        # Outcome breakdown
        tp1_first_count = sum(1 for t in filled_trades if t.outcome == TradeOutcome.TP1_FIRST)
        sl_first_count = sum(1 for t in filled_trades if t.outcome == TradeOutcome.SL_FIRST)
        conservative_sl_first_count = sum(1 for t in filled_trades if t.outcome == TradeOutcome.CONSERVATIVE_SL_FIRST)
        unresolved_count = sum(1 for t in filled_trades if t.outcome == TradeOutcome.UNRESOLVED)
        conservative_resolution_rate = (
            float(conservative_sl_first_count / trade_count) if trade_count > 0 else 0.0
        )

        # Payoff profile
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

        # Expectancy & Profitability
        gross_expectancy_r = float(statistics.mean(gross_r_list)) if gross_r_list else 0.0
        net_expectancy_r = (win_rate * avg_win_r) - (loss_rate * avg_loss_r) if trade_count > 0 else 0.0
        average_r = float(statistics.mean(net_r_list)) if net_r_list else 0.0
        median_r = float(statistics.median(net_r_list)) if net_r_list else 0.0

        # Profit Factor
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

        # Downside Risk (Normalized Trade Sequence Drawdown in R - P6-C4)
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

        # Consecutive Losses
        current_streak = 0
        max_consecutive_losses = 0
        for r in net_r_list:
            if r <= 0.0:
                current_streak += 1
                if current_streak > max_consecutive_losses:
                    max_consecutive_losses = current_streak
            else:
                current_streak = 0

        # Post-Fill Excursions (MFE & MAE)
        mfe_list = [float(t.mfe_r or Decimal("0")) for t in filled_trades]
        mae_list = [float(t.mae_r or Decimal("0")) for t in filled_trades]
        dur_list = [float(t.holding_duration_seconds or 0.0) for t in filled_trades]

        avg_mfe_r = float(statistics.mean(mfe_list)) if mfe_list else 0.0
        med_mfe_r = float(statistics.median(mfe_list)) if mfe_list else 0.0
        avg_mae_r = float(statistics.mean(mae_list)) if mae_list else 0.0
        med_mae_r = float(statistics.median(mae_list)) if mae_list else 0.0
        avg_dur = float(statistics.mean(dur_list)) if dur_list else 0.0
        med_dur = float(statistics.median(dur_list)) if dur_list else 0.0

        # Cost Drag
        tot_entry_fees = float(sum(t.entry_fee for t in filled_trades))
        tot_exit_fees = float(sum(t.exit_fee for t in filled_trades))
        tot_spread = float(sum(t.entry_spread + t.exit_spread for t in filled_trades))
        tot_slippage = float(sum(t.entry_slippage + t.exit_slippage for t in filled_trades))
        cost_drag_r = gross_expectancy_r - net_expectancy_r
        cost_drag_pct = gross_return_pct - net_return_pct

        # Normalized Daily Sharpe / Sortino
        daily_sharpe, daily_sortino = BacktestMetricsCalculator._calculate_daily_ratios(filled_trades)

        # Subsystem Breakdown (Regime, Session)
        subsystems = BacktestMetricsCalculator._calculate_subsystems(signals, filled_trades)

        return BacktestMetrics(
            signal_count=signal_count,
            buy_window_count=buy_window_count,
            valid_risk_plan_count=valid_risk_plan_count,
            execution_eligible_count=execution_eligible_count,
            fill_count=fill_count,
            no_fill_count=no_fill_count,
            fill_rate=fill_rate,
            no_fill_rate=no_fill_rate,
            trade_count=trade_count,
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
            max_trade_sequence_drawdown_r=max_dd_r,
            drawdown_duration_trades=max_dd_duration_trades,
            maximum_consecutive_losses=max_consecutive_losses,
            average_mfe_r=avg_mfe_r,
            median_mfe_r=med_mfe_r,
            average_mae_r=avg_mae_r,
            median_mae_r=med_mae_r,
            average_holding_duration_seconds=avg_dur,
            median_holding_duration_seconds=med_dur,
            tp1_first_count=tp1_first_count,
            sl_first_count=sl_first_count,
            conservative_sl_first_count=conservative_sl_first_count,
            unresolved_count=unresolved_count,
            conservative_resolution_rate=conservative_resolution_rate,
            total_entry_fees=tot_entry_fees,
            total_exit_fees=tot_exit_fees,
            total_spread_cost=tot_spread,
            total_slippage_cost=tot_slippage,
            cost_drag_r=cost_drag_r,
            cost_drag_pct=cost_drag_pct,
            wait_count=wait_count,
            avoid_count=avoid_count,
            normalized_daily_sharpe=daily_sharpe,
            normalized_daily_sortino=daily_sortino,
            subsystems=subsystems,
        )

    @staticmethod
    def _calculate_daily_ratios(trades: Sequence[SimulatedTrade]) -> Tuple[Optional[float], Optional[float]]:
        """Calculate normalized daily Sharpe and Sortino ratios from daily net R series."""
        if not trades:
            return None, None

        daily_r: Dict[str, float] = {}
        for t in trades:
            if t.exit_timestamp and t.net_r is not None:
                day_str = t.exit_timestamp.strftime("%Y-%m-%d")
                daily_r[day_str] = daily_r.get(day_str, 0.0) + float(t.net_r)

        if len(daily_r) < 2:
            return None, None

        r_values = list(daily_r.values())
        mean_r = statistics.mean(r_values)
        stdev_r = statistics.stdev(r_values) if len(r_values) > 1 else 0.0

        daily_sharpe = float(math.sqrt(365.0) * (mean_r / stdev_r)) if stdev_r > 0 else 0.0

        downside_diffs = [min(0.0, r) ** 2 for r in r_values]
        downside_std = math.sqrt(sum(downside_diffs) / len(r_values)) if downside_diffs else 0.0
        daily_sortino = float(math.sqrt(365.0) * (mean_r / downside_std)) if downside_std > 0 else 0.0

        return daily_sharpe, daily_sortino

    @staticmethod
    def _calculate_subsystems(
        signals: Sequence[SignalSnapshot],
        trades: Sequence[SimulatedTrade],
    ) -> SubsystemPerformance:
        """Partition performance across regime and session tags."""
        regime_trades: Dict[str, List[SimulatedTrade]] = {}
        session_trades: Dict[str, List[SimulatedTrade]] = {}

        for t in trades:
            reg_key = str(t.regime.value) if hasattr(t.regime, "value") else str(t.regime)
            sess_key = str(t.session.value) if hasattr(t.session, "value") else str(t.session)
            regime_trades.setdefault(reg_key, []).append(t)
            session_trades.setdefault(sess_key, []).append(t)

        reg_breakdown: Dict[str, Any] = {}
        for reg_k, reg_t_list in regime_trades.items():
            r_vals = [float(t.net_r or Decimal("0")) for t in reg_t_list]
            w_count = sum(1 for r in r_vals if r > 0)
            reg_breakdown[reg_k] = {
                "trade_count": len(reg_t_list),
                "win_rate": float(w_count / len(reg_t_list)) if reg_t_list else 0.0,
                "net_expectancy_r": float(statistics.mean(r_vals)) if r_vals else 0.0,
            }

        sess_breakdown: Dict[str, Any] = {}
        for sess_k, sess_t_list in session_trades.items():
            r_vals = [float(t.net_r or Decimal("0")) for t in sess_t_list]
            w_count = sum(1 for r in r_vals if r > 0)
            sess_breakdown[sess_k] = {
                "trade_count": len(sess_t_list),
                "win_rate": float(w_count / len(sess_t_list)) if sess_t_list else 0.0,
                "net_expectancy_r": float(statistics.mean(r_vals)) if r_vals else 0.0,
            }

        return SubsystemPerformance(
            regime_breakdown=reg_breakdown,
            session_breakdown=sess_breakdown,
            cycle_breakdown={},
        )
