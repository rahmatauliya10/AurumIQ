"""Deterministic cost modeling for trade friction, spread, adverse slippage, and explicit fees."""
from decimal import Decimal
from typing import NamedTuple, Optional, Tuple

from engine.backtest.types import BacktestCostConfig


class EntryCostResult(NamedTuple):
    raw_price: Decimal
    effective_entry_price: Decimal
    spread_cost: Decimal
    slippage_cost: Decimal
    fee_cost: Decimal


class ExitCostResult(NamedTuple):
    raw_price: Decimal
    effective_exit_price: Decimal
    spread_cost: Decimal
    slippage_cost: Decimal
    fee_cost: Decimal


class CostModel:
    """
    Simulates trading friction deterministically.

    Strict Invariants (P6-07..P6-12, P6-C3):
      1. Actual ASK quote already embeds spread -> synthetic spread = 0.0 (no double-counting).
      2. Actual BID exit quote already embeds spread -> synthetic spread = 0.0 (no double-counting).
      3. Mid / OHLC candle source applies synthetic spread ONCE.
      4. Slippage is strictly adverse (raises entry price, lowers exit price).
      5. Fees are explicit and separate.
      6. Planned risk unit (P6-C3) is preserved as denominator for R calculations.
    """

    def __init__(self, config: Optional[BacktestCostConfig] = None):
        self.config = config or BacktestCostConfig.idealized()

    def calculate_entry(
        self,
        raw_price: Decimal,
        is_actual_ask_quote: bool = False,
    ) -> EntryCostResult:
        """
        Calculate effective entry price and friction components for a LONG order.
        """
        # 1. Spread calculation
        if is_actual_ask_quote:
            spread_cost = Decimal("0.00")
        else:
            # Half-spread added to mid/OHLC buy price
            spread_cost = (
                raw_price * (self.config.synthetic_spread_bps / Decimal("20000"))
            ).quantize(Decimal("0.01"))

        # 2. Adverse slippage (adds to cost)
        slippage_cost = (
            raw_price * (self.config.entry_slippage_bps / Decimal("10000"))
        ).quantize(Decimal("0.01"))

        price_with_execution_friction = raw_price + spread_cost + slippage_cost

        # 3. Explicit exchange fee
        fee_cost = (
            price_with_execution_friction * (self.config.entry_fee_bps / Decimal("10000"))
        ).quantize(Decimal("0.01"))

        effective_entry = price_with_execution_friction + fee_cost

        return EntryCostResult(
            raw_price=raw_price,
            effective_entry_price=effective_entry,
            spread_cost=spread_cost,
            slippage_cost=slippage_cost,
            fee_cost=fee_cost,
        )

    def calculate_exit(
        self,
        raw_price: Decimal,
        is_actual_bid_quote: bool = False,
    ) -> ExitCostResult:
        """
        Calculate effective exit proceeds per unit and friction components for closing a LONG position.
        """
        # 1. Spread calculation
        if is_actual_bid_quote:
            spread_cost = Decimal("0.00")
        else:
            # Half-spread deducted from mid/OHLC sell price
            spread_cost = (
                raw_price * (self.config.synthetic_spread_bps / Decimal("20000"))
            ).quantize(Decimal("0.01"))

        # 2. Adverse slippage (deducts from proceeds)
        slippage_cost = (
            raw_price * (self.config.exit_slippage_bps / Decimal("10000"))
        ).quantize(Decimal("0.01"))

        price_with_execution_friction = raw_price - spread_cost - slippage_cost

        # 3. Explicit exchange fee
        fee_cost = (
            price_with_execution_friction * (self.config.exit_fee_bps / Decimal("10000"))
        ).quantize(Decimal("0.01"))

        effective_exit = price_with_execution_friction - fee_cost

        return ExitCostResult(
            raw_price=raw_price,
            effective_exit_price=effective_exit,
            spread_cost=spread_cost,
            slippage_cost=slippage_cost,
            fee_cost=fee_cost,
        )

    def compute_r_and_returns(
        self,
        raw_entry_price: Decimal,
        effective_entry_price: Decimal,
        raw_exit_price: Decimal,
        effective_exit_price: Decimal,
        planned_risk_amount: Decimal,
    ) -> Tuple[Decimal, Decimal, Decimal, Decimal, Decimal, Decimal]:
        """
        Compute gross/net PnL per unit, gross/net R, and gross/net return %.
        Strict Invariant (P6-C3):
          planned_risk_amount = entry_max - stop_final > 0
          gross_R = gross_pnl_per_unit / planned_risk_amount
          net_R = net_pnl_per_unit / planned_risk_amount
        """
        if planned_risk_amount <= Decimal("0"):
            raise ValueError(f"planned_risk_amount must be strictly positive, got {planned_risk_amount}")

        gross_pnl = raw_exit_price - raw_entry_price
        net_pnl = effective_exit_price - effective_entry_price

        gross_r = (gross_pnl / planned_risk_amount).quantize(Decimal("0.0001"))
        net_r = (net_pnl / planned_risk_amount).quantize(Decimal("0.0001"))

        gross_return_pct = (
            (gross_pnl / raw_entry_price) * Decimal("100")
        ).quantize(Decimal("0.0001")) if raw_entry_price > Decimal("0") else Decimal("0.0000")

        net_return_pct = (
            (net_pnl / effective_entry_price) * Decimal("100")
        ).quantize(Decimal("0.0001")) if effective_entry_price > Decimal("0") else Decimal("0.0000")

        cost_drag_r = (gross_r - net_r).quantize(Decimal("0.0001"))
        cost_drag_pct = (gross_return_pct - net_return_pct).quantize(Decimal("0.0001"))

        return gross_pnl, net_pnl, gross_r, net_r, gross_return_pct, net_return_pct
