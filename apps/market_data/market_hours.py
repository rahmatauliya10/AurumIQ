"""
Canonical market hours and closure helpers for market data domain.
Re-exports governed closure functions from engine.paper.continuity.
"""
from engine.paper.continuity import (
    is_expected_market_closure,
    is_expected_market_interval_closed,
    get_expected_15m_closes,
)

__all__ = [
    "is_expected_market_closure",
    "is_expected_market_interval_closed",
    "get_expected_15m_closes",
]
