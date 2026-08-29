"""Causal swing high/low detection ensuring zero future lookahead."""
from decimal import Decimal
from typing import List, Sequence
from engine.core.types import CandleData, SwingPoint, SwingType


def detect_causal_swings(
    candles: Sequence[CandleData],
    left_bars: int = 3,
    right_bars: int = 3,
) -> List[SwingPoint]:
    """
    Detect confirmed swing highs and lows strictly causally.
    
    Causality Invariant:
      A swing at candle `i` requires `left_bars` prior bars and `right_bars` subsequent bars.
      Therefore, the swing at `i` is strictly knowable ONLY when candle `i + right_bars` closes.
      detected_at = timestamp_close of candle[i + right_bars]
      source_timestamp = timestamp_open of candle[i]
      
      No swing with index > (len(candles) - 1 - right_bars) is ever confirmed.
    """
    n = len(candles)
    required_min = left_bars + right_bars + 1
    if n < required_min:
        return []

    swings: List[SwingPoint] = []
    max_eval_idx = n - 1 - right_bars

    for i in range(left_bars, max_eval_idx + 1):
        curr_candle = candles[i]
        curr_high = curr_candle.high
        curr_low = curr_candle.low

        # 1. Check Swing High
        is_swing_high = True
        for l in range(1, left_bars + 1):
            if candles[i - l].high >= curr_high:
                is_swing_high = False
                break
        if is_swing_high:
            for r in range(1, right_bars + 1):
                if candles[i + r].high > curr_high:
                    is_swing_high = False
                    break

        if is_swing_high:
            swings.append(
                SwingPoint(
                    index=i,
                    timestamp=curr_candle.timestamp_open,
                    detected_at=candles[i + right_bars].timestamp_close,
                    price=curr_high,
                    swing_type=SwingType.HIGH,
                    is_confirmed=True,
                )
            )

        # 2. Check Swing Low
        is_swing_low = True
        for l in range(1, left_bars + 1):
            if candles[i - l].low <= curr_low:
                is_swing_low = False
                break
        if is_swing_low:
            for r in range(1, right_bars + 1):
                if candles[i + r].low < curr_low:
                    is_swing_low = False
                    break

        if is_swing_low:
            swings.append(
                SwingPoint(
                    index=i,
                    timestamp=curr_candle.timestamp_open,
                    detected_at=candles[i + right_bars].timestamp_close,
                    price=curr_low,
                    swing_type=SwingType.LOW,
                    is_confirmed=True,
                )
            )

    # Sort chronologically by source timestamp
    swings.sort(key=lambda s: s.timestamp)
    return swings
