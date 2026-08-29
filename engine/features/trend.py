"""Trend indicator mathematical calculations (EMA, Slope, Alignment, ADX)."""
from decimal import Decimal
from typing import List, Optional, Sequence, Tuple
import math


def calculate_ema(values: Sequence[Decimal], period: int) -> List[Optional[Decimal]]:
    """
    Calculate Exponential Moving Average (EMA) series using standard Wilder/TradingView formula.
    Multiplier = 2 / (period + 1).
    First valid point at index `period - 1` initialized with Simple Moving Average (SMA).
    """
    if len(values) < period or period <= 0:
        return [None] * len(values)

    result: List[Optional[Decimal]] = [None] * (period - 1)
    # Initialize with SMA
    sma = sum(values[:period]) / Decimal(str(period))
    result.append(sma)

    multiplier = Decimal("2.0") / Decimal(str(period + 1))
    current_ema = sma

    for val in values[period:]:
        current_ema = ((val - current_ema) * multiplier) + current_ema
        result.append(current_ema)

    return result


def calculate_ema_slope(
    ema_series: Sequence[Optional[Decimal]], lookback: int = 5
) -> Optional[float]:
    """
    Calculate normalized slope (% change per bar) of the EMA over `lookback` bars.
    Formula: ((EMA_t - EMA_{t-lookback}) / EMA_{t-lookback}) * 100 / lookback
    """
    if len(ema_series) <= lookback:
        return None
    
    current_val = ema_series[-1]
    prev_val = ema_series[-1 - lookback]

    if current_val is None or prev_val is None or prev_val <= 0:
        return None

    pct_change = float((current_val - prev_val) / prev_val) * 100.0
    return pct_change / float(lookback)


def calculate_ema_alignment(
    ema_fast: Optional[Decimal],
    ema_mid: Optional[Decimal],
    ema_slow: Optional[Decimal],
) -> int:
    """
    Evaluate structural EMA stack alignment:
      +1: Bullish Stack (Fast > Mid > Slow)
      -1: Bearish Stack (Fast < Mid < Slow)
       0: Mixed / Transition
    """
    if ema_fast is None or ema_mid is None or ema_slow is None:
        return 0

    if ema_fast > ema_mid > ema_slow:
        return 1
    elif ema_fast < ema_mid < ema_slow:
        return -1
    return 0


def calculate_adx(
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    closes: Sequence[Decimal],
    period: int = 14,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Calculate Average Directional Index (ADX), Plus Directional Indicator (+DI),
    and Minus Directional Indicator (-DI) using standard Wilder's smoothing.
    
    Returns: (ADX, +DI, -DI)
    """
    n = len(closes)
    if n < (2 * period) or period <= 0:
        return None, None, None

    tr_list: List[float] = []
    plus_dm_list: List[float] = []
    minus_dm_list: List[float] = []

    for i in range(1, n):
        h, h_prev = float(highs[i]), float(highs[i - 1])
        l, l_prev = float(lows[i]), float(lows[i - 1])
        c_prev = float(closes[i - 1])

        # True Range
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        tr_list.append(tr)

        # Directional Movement
        up_move = h - h_prev
        down_move = l_prev - l

        if up_move > down_move and up_move > 0:
            plus_dm_list.append(up_move)
        else:
            plus_dm_list.append(0.0)

        if down_move > up_move and down_move > 0:
            minus_dm_list.append(down_move)
        else:
            minus_dm_list.append(0.0)

    if len(tr_list) < (2 * period - 1):
        return None, None, None

    # Initial 14-period sums (Wilder initialization)
    smooth_tr = sum(tr_list[:period])
    smooth_plus_dm = sum(plus_dm_list[:period])
    smooth_minus_dm = sum(minus_dm_list[:period])

    dx_list: List[float] = []
    
    # Calculate initial +DI, -DI, DX
    if smooth_tr > 0:
        p_di = (smooth_plus_dm / smooth_tr) * 100.0
        m_di = (smooth_minus_dm / smooth_tr) * 100.0
        di_sum = p_di + m_di
        dx = (abs(p_di - m_di) / di_sum * 100.0) if di_sum > 0 else 0.0
    else:
        p_di, m_di, dx = 0.0, 0.0, 0.0

    dx_list.append(dx)

    # Smooth subsequent values
    for i in range(period, len(tr_list)):
        smooth_tr = smooth_tr - (smooth_tr / period) + tr_list[i]
        smooth_plus_dm = smooth_plus_dm - (smooth_plus_dm / period) + plus_dm_list[i]
        smooth_minus_dm = smooth_minus_dm - (smooth_minus_dm / period) + minus_dm_list[i]

        if smooth_tr > 0:
            p_di = (smooth_plus_dm / smooth_tr) * 100.0
            m_di = (smooth_minus_dm / smooth_tr) * 100.0
            di_sum = p_di + m_di
            dx = (abs(p_di - m_di) / di_sum * 100.0) if di_sum > 0 else 0.0
        else:
            p_di, m_di, dx = 0.0, 0.0, 0.0

        dx_list.append(dx)

    if len(dx_list) < period:
        return None, None, None

    # Initial ADX is SMA of first 14 DX values
    adx = sum(dx_list[:period]) / period

    # Subsequent Wilder smoothing of ADX
    for i in range(period, len(dx_list)):
        adx = ((adx * (period - 1)) + dx_list[i]) / period

    return round(adx, 2), round(p_di, 2), round(m_di, 2)
