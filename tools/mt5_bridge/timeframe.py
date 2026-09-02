"""Timeframe definitions and conversion mapping between AurumIQ and MetaTrader 5."""
from datetime import timedelta
from typing import Dict

# Standard AurumIQ string timeframe to duration mapping
TIMEFRAME_DELTAS: Dict[str, timedelta] = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
}

# Standard MetaTrader 5 timeframe integer constants
# mt5.TIMEFRAME_M1 = 1
# mt5.TIMEFRAME_M5 = 5
# mt5.TIMEFRAME_M15 = 15
# mt5.TIMEFRAME_H1 = 16385
# mt5.TIMEFRAME_H4 = 16388
# mt5.TIMEFRAME_D1 = 16408
TIMEFRAME_TO_MT5_CONSTANT: Dict[str, int] = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "1h": 16385,
    "4h": 16388,
    "1d": 16408,
}

MT5_CONSTANT_TO_TIMEFRAME: Dict[int, str] = {v: k for k, v in TIMEFRAME_TO_MT5_CONSTANT.items()}


def normalize_timeframe_str(tf: str) -> str:
    """Normalize input timeframe string (case-insensitive with whitespace trimmed)."""
    cleaned = tf.strip().lower()
    if cleaned in ("m1", "1"):
        return "1m"
    elif cleaned in ("m5", "5"):
        return "5m"
    elif cleaned in ("m15", "15"):
        return "15m"
    elif cleaned in ("h1", "60"):
        return "1h"
    elif cleaned in ("h4", "240"):
        return "4h"
    elif cleaned in ("d1", "1440", "d"):
        return "1d"
    elif cleaned in TIMEFRAME_DELTAS:
        return cleaned
    raise ValueError(
        f"Unsupported timeframe '{tf}'. Supported: {list(TIMEFRAME_DELTAS.keys())}"
    )


def map_timeframe_to_mt5(tf: str) -> int:
    """Resolve normalized timeframe string to MT5 integer constant."""
    canonical = normalize_timeframe_str(tf)
    return TIMEFRAME_TO_MT5_CONSTANT[canonical]


def get_timeframe_delta(tf: str) -> timedelta:
    """Return explicit interval timedelta for a timeframe."""
    canonical = normalize_timeframe_str(tf)
    return TIMEFRAME_DELTAS[canonical]
