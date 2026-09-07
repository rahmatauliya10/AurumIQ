"""Dynamic execution commission calculation and fee conversion for XAUUSD.

Adheres strictly to Pre-Phase-8 Calibration Governance:
- Raw Spread commissions are native USD per lot per side.
- Converted dynamically at runtime via execution notional:
    fee_bps = (fee_usd / notional_usd) * 10000
- Standard account tier commission is strictly USD 0.00.
- Fixed reference-price bps are excluded from calibration models.
"""
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any


def calculate_execution_notional(
    volume_lots: Decimal,
    contract_size: Decimal,
    execution_price: Decimal,
) -> Decimal:
    """Calculate total transaction notional in USD.
    
    Formula: notional_usd = volume_lots * contract_size * execution_price
    """
    if volume_lots <= Decimal("0"):
        raise ValueError("volume_lots must be strictly positive.")
    if contract_size <= Decimal("0"):
        raise ValueError("contract_size must be strictly positive.")
    if execution_price <= Decimal("0"):
        raise ValueError("execution_price must be strictly positive.")
    
    return volume_lots * contract_size * execution_price


def calculate_dynamic_fee_bps(
    commission_usd_per_lot_per_side: Decimal,
    contract_size: Decimal,
    execution_price: Decimal,
) -> Decimal:
    """Calculate effective basis-point fee for one execution side dynamically.
    
    Formula:
        fee_bps = (commission_usd_per_lot_per_side / (contract_size * execution_price)) * 10000
    """
    if commission_usd_per_lot_per_side < Decimal("0"):
        raise ValueError("commission_usd_per_lot_per_side cannot be negative.")
    if commission_usd_per_lot_per_side == Decimal("0"):
        return Decimal("0.0000")
    
    if contract_size <= Decimal("0"):
        raise ValueError("contract_size must be strictly positive.")
    if execution_price <= Decimal("0"):
        raise ValueError("execution_price must be strictly positive.")
    
    one_lot_notional = contract_size * execution_price
    fee_bps = (commission_usd_per_lot_per_side / one_lot_notional) * Decimal("10000")
    return fee_bps.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def calculate_side_fee_usd(
    volume_lots: Decimal,
    commission_usd_per_lot_per_side: Decimal,
) -> Decimal:
    """Calculate fee in USD for a single order fill (entry or exit)."""
    if volume_lots <= Decimal("0"):
        raise ValueError("volume_lots must be strictly positive.")
    if commission_usd_per_lot_per_side < Decimal("0"):
        raise ValueError("commission_usd_per_lot_per_side cannot be negative.")
    
    return (volume_lots * commission_usd_per_lot_per_side).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def calculate_round_trip_cost_bps(
    base_spread_bps: Decimal,
    entry_fee_bps: Decimal,
    exit_fee_bps: Decimal,
    entry_slippage_bps: Decimal = Decimal("0.0"),
    exit_slippage_bps: Decimal = Decimal("0.0"),
) -> Decimal:
    """Calculate total expected round-trip friction in basis points.
    
    Formula:
        total_friction_bps = base_spread_bps + entry_fee_bps + exit_fee_bps + entry_slippage_bps + exit_slippage_bps
    """
    if base_spread_bps < Decimal("0"):
        raise ValueError("base_spread_bps cannot be negative.")
    if entry_fee_bps < Decimal("0") or exit_fee_bps < Decimal("0"):
        raise ValueError("Fee bps cannot be negative.")
    if entry_slippage_bps < Decimal("0") or exit_slippage_bps < Decimal("0"):
        raise ValueError("Slippage bps cannot be negative.")
    
    return base_spread_bps + entry_fee_bps + exit_fee_bps + entry_slippage_bps + exit_slippage_bps


# -----------------------------------------------------------------------------
# Account Currency Semantics (Directive: STANDARD_CENT USC support)
# -----------------------------------------------------------------------------

ACCOUNT_CURRENCY_USD = "USD"
ACCOUNT_CURRENCY_USC = "USC"
CENT_RATIO_USC_PER_USD = Decimal("100")


def get_account_currency_for_tier(account_tier: str) -> str:
    """Derive expected account balance currency from execution account tier.
    
    Explicitly distinguishes:
    - STANDARD / RAW_SPREAD -> USD
    - STANDARD_CENT         -> USC (United States Cents)
    """
    clean_tier = str(account_tier or "").strip().upper()
    if clean_tier in ("STANDARD_CENT", "CENT"):
        return ACCOUNT_CURRENCY_USC
    return ACCOUNT_CURRENCY_USD


def convert_currency(
    amount: Decimal,
    from_currency: str,
    to_currency: str,
) -> Decimal:
    """Convert monetary amount between market quote currency (USD) and account balance currency (USC/USD).
    
    Guarantees:
    - USD to USC: amount * 100
    - USC to USD: amount / 100
    - Identity: amount if from_currency == to_currency
    - Fails closed with ValueError on any unrecognized currency conversion.
    """
    from_c = str(from_currency).strip().upper()
    to_c = str(to_currency).strip().upper()

    if from_c == to_c:
        return amount

    if from_c == ACCOUNT_CURRENCY_USD and to_c == ACCOUNT_CURRENCY_USC:
        return (amount * CENT_RATIO_USC_PER_USD).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    if from_c == ACCOUNT_CURRENCY_USC and to_c == ACCOUNT_CURRENCY_USD:
        return (amount / CENT_RATIO_USC_PER_USD).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

    raise ValueError(
        f"CURRENCY_CONVERSION_ERROR: Unsupported monetary conversion from '{from_currency}' to '{to_currency}'."
    )
