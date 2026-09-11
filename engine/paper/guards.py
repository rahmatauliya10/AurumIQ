"""Structural safety guards and fail-closed execution barriers for Phase 8 Paper Runner."""
from typing import Any, NoReturn

# Explicit authoritative safety constants
PAPER_ONLY: bool = True
REAL_ORDER_EXECUTION: str = "disabled"
LIVE_MONEY_TRADING_AUTHORIZED: bool = False
BROKER_ORDER_DISPATCH_ENABLED: bool = False


# Forbidden execution symbols that must NEVER exist or be called in Phase 8
FORBIDDEN_EXECUTION_SYMBOLS = {
    "order_send",
    "OrderSend",
    "order_check",
    "OrderCheck",
    "order_calc_margin",
    "positions_get",
    "trade_order",
    "broker_order",
    "execute_live_order",
    "submit_broker_order",
    "modify_broker_position",
}


def assert_paper_execution_safety(attempted_action: str = "trade") -> None:
    """
    Strict structural assertion guaranteeing Phase 8 remains paper-only.

    Raises RuntimeError immediately if any live execution or real broker order
    path is ever triggered.
    """
    if not PAPER_ONLY or REAL_ORDER_EXECUTION != "disabled" or LIVE_MONEY_TRADING_AUTHORIZED:
        raise RuntimeError(
            "CRITICAL SAFETY VIOLATION: Phase 8 runtime invariant corrupted. "
            "PAPER_ONLY must be True and REAL_ORDER_EXECUTION must be 'disabled'."
        )

    if any(forbidden in attempted_action.lower() for forbidden in ["send", "submit", "real", "broker_live"]):
        raise RuntimeError(
            f"CRITICAL SAFETY VIOLATION: Real order execution attempted in Phase 8 Paper Runner: '{attempted_action}'."
        )


def fail_closed_broker_action(*args: Any, **kwargs: Any) -> NoReturn:
    """Fail-closed sentinel replacing any broker execution hook."""
    raise RuntimeError(
        "FATAL: Broker execution is strictly disabled in Phase 8 Live Paper Observation."
    )
