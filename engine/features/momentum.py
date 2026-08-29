"""Momentum indicator mathematical calculations (RSI, MACD, ROC)."""
from decimal import Decimal
from typing import List, Optional, Sequence, Tuple
from .trend import calculate_ema


def calculate_rsi(closes: Sequence[Decimal], period: int = 14) -> Optional[float]:
    """
    Calculate Relative Strength Index (RSI) using standard Wilder's smoothing technique.
    Formula:
      RS = AvgGain / AvgLoss
      RSI = 100 - (100 / (1 + RS))
    """
    n = len(closes)
    if n <= period or period <= 0:
        return None

    gains: List[Decimal] = []
    losses: List[Decimal] = []

    for i in range(1, n):
        change = closes[i] - closes[i - 1]
        if change > 0:
            gains.append(change)
            losses.append(Decimal("0.0"))
        else:
            gains.append(Decimal("0.0"))
            losses.append(abs(change))

    if len(gains) < period:
        return None

    # Initial average gain and loss (SMA)
    avg_gain = sum(gains[:period]) / Decimal(str(period))
    avg_loss = sum(losses[:period]) / Decimal(str(period))

    # Wilder's smoothing recursion
    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * Decimal(str(period - 1))) + gains[i]) / Decimal(str(period))
        avg_loss = ((avg_loss * Decimal(str(period - 1))) + losses[i]) / Decimal(str(period))

    if avg_loss == Decimal("0.0"):
        return 100.0 if avg_gain > 0 else 50.0

    rs = avg_gain / avg_loss
    rsi = Decimal("100.0") - (Decimal("100.0") / (Decimal("1.0") + rs))
    return float(round(rsi, 2))


def calculate_macd(
    closes: Sequence[Decimal],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> Tuple[Optional[Decimal], Optional[Decimal], Optional[Decimal]]:
    """
    Calculate Moving Average Convergence Divergence (MACD):
      MACD Line = EMA(fast) - EMA(slow)
      Signal Line = EMA(signal) of MACD Line
      Histogram = MACD Line - Signal Line
    
    Returns: (macd_line, signal_line, histogram)
    """
    if len(closes) < (slow_period + signal_period):
        return None, None, None

    fast_ema_series = calculate_ema(closes, fast_period)
    slow_ema_series = calculate_ema(closes, slow_period)

    # Valid MACD line values starting where slow_ema is valid
    macd_line_series: List[Decimal] = []
    for f_ema, s_ema in zip(fast_ema_series, slow_ema_series):
        if f_ema is not None and s_ema is not None:
            macd_line_series.append(f_ema - s_ema)

    if len(macd_line_series) < signal_period:
        return None, None, None

    signal_ema_series = calculate_ema(macd_line_series, signal_period)

    current_macd = macd_line_series[-1]
    current_signal = signal_ema_series[-1]

    if current_signal is None:
        return current_macd, None, None

    histogram = current_macd - current_signal
    return current_macd, current_signal, histogram


def calculate_roc(closes: Sequence[Decimal], period: int = 12) -> Optional[float]:
    """
    Calculate Rate of Change (ROC %):
      ROC = ((Close_t - Close_{t-period}) / Close_{t-period}) * 100
    """
    if len(closes) <= period or period <= 0:
        return None

    current_c = closes[-1]
    prev_c = closes[-1 - period]

    if prev_c <= 0:
        return None

    roc = ((current_c - prev_c) / prev_c) * Decimal("100.0")
    return float(round(roc, 2))
