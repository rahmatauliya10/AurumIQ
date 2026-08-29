"""Volatility indicator calculations (ATR, Bollinger Bands, Realized Volatility)."""
from decimal import Decimal
from typing import List, Optional, Sequence, Tuple
import math


def calculate_atr(
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    closes: Sequence[Decimal],
    period: int = 14,
) -> Optional[Decimal]:
    """
    Calculate Average True Range (ATR) using standard Wilder's smoothing.
    TR = max(High - Low, |High - Close_prev|, |Low - Close_prev|)
    """
    n = len(closes)
    if n <= period or period <= 0:
        return None

    tr_list: List[Decimal] = []
    for i in range(1, n):
        h = highs[i]
        l = lows[i]
        c_prev = closes[i - 1]
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        tr_list.append(tr)

    if len(tr_list) < period:
        return None

    # Initial ATR (SMA of first period TRs)
    current_atr = sum(tr_list[:period]) / Decimal(str(period))

    # Wilder's smoothing
    for i in range(period, len(tr_list)):
        current_atr = ((current_atr * Decimal(str(period - 1))) + tr_list[i]) / Decimal(str(period))

    return current_atr.quantize(Decimal("0.00000001"))


def calculate_bollinger_bands(
    closes: Sequence[Decimal],
    period: int = 20,
    num_std: float = 2.0,
) -> Tuple[Optional[Decimal], Optional[Decimal], Optional[Decimal], Optional[float]]:
    """
    Calculate Bollinger Bands:
      Middle Band = SMA(period)
      Upper Band = Middle + (num_std * std_dev)
      Lower Band = Middle - (num_std * std_dev)
      Bandwidth % = ((Upper - Lower) / Middle) * 100
      
    Returns: (upper, middle, lower, bandwidth_pct)
    """
    if len(closes) < period or period <= 0:
        return None, None, None, None

    window = closes[-period:]
    float_window = [float(c) for c in window]
    mean = sum(float_window) / float(period)

    variance = sum((x - mean) ** 2 for x in float_window) / float(period)
    std_dev = math.sqrt(variance)

    middle = Decimal(str(mean))
    upper = Decimal(str(mean + (num_std * std_dev)))
    lower = Decimal(str(mean - (num_std * std_dev)))

    bandwidth = ((upper - lower) / middle) * Decimal("100.0") if middle > 0 else Decimal("0.0")

    return (
        upper.quantize(Decimal("0.00000001")),
        middle.quantize(Decimal("0.00000001")),
        lower.quantize(Decimal("0.00000001")),
        float(round(bandwidth, 2)),
    )


def calculate_realized_volatility(
    closes: Sequence[Decimal],
    period: int = 20,
    ddof: int = 0,
) -> Optional[float]:
    """
    Calculate Realized Volatility as the raw (un-annualized) rolling population standard
    deviation (ddof=0) of log returns over `period` bars in percentage points (%).

    Mathematical Formula:
      r_t = ln(Close_t / Close_{t-1})
      mean_r = (1 / N) * sum(r_t) for t = 1..N
      variance = (1 / N) * sum((r_t - mean_r)^2) for t = 1..N
      std_dev = sqrt(variance)
      realized_vol_20 (%) = std_dev * 100.0

    Semantics:
      - Strictly locked to population standard deviation (ddof=0, denominator = N = 20).
      - If ddof != 0 is passed, raises ValueError to enforce mathematical invariant.
      - Unit: Percentage (%) per bar timeframe (e.g. 15m, 1H, 1D).
      - No annualization scaling is applied (raw rolling volatility).
      - Regime HIGH_VOLATILITY threshold of 5.0 corresponds strictly to 5.0% bar volatility.
    """
    if ddof != 0:
        raise ValueError(f"AurumIQ realized volatility is strictly fixed to population standard deviation (ddof=0), got ddof={ddof}")

    if len(closes) <= period or period <= 0:
        return None

    window = closes[-(period + 1):]
    log_returns: List[float] = []

    for i in range(1, len(window)):
        c_curr = float(window[i])
        c_prev = float(window[i - 1])
        if c_prev > 0 and c_curr > 0:
            log_returns.append(math.log(c_curr / c_prev))

    n = len(log_returns)
    if n < period:
        return None

    mean_ret = sum(log_returns) / float(n)
    variance = sum((r - mean_ret) ** 2 for r in log_returns) / float(n)
    std_dev = math.sqrt(variance)

    return float(round(std_dev * 100.0, 4))
