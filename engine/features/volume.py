"""Volume indicator calculations (Volume Ratio, Volume Z-Score)."""
from decimal import Decimal
from typing import Optional, Sequence
import math


def calculate_volume_ratio(volumes: Sequence[Decimal], period: int = 20) -> Optional[float]:
    """
    Calculate current bar volume ratio relative to its period-SMA.
    Formula: Volume_t / SMA(Volume, period)
    """
    if len(volumes) < period or period <= 0:
        return None

    window = volumes[-period:]
    mean_vol = sum(window) / Decimal(str(period))

    if mean_vol <= 0:
        return 1.0

    current_vol = volumes[-1]
    ratio = current_vol / mean_vol
    return float(round(ratio, 2))


def calculate_volume_zscore(volumes: Sequence[Decimal], period: int = 20) -> Optional[float]:
    """
    Calculate Volume Z-Score relative to its recent window distribution:
    Formula: (Volume_t - Mean) / StdDev
    """
    if len(volumes) < period or period <= 0:
        return None

    window = [float(v) for v in volumes[-period:]]
    mean = sum(window) / float(period)
    variance = sum((x - mean) ** 2 for x in window) / float(period)
    std_dev = math.sqrt(variance)

    if std_dev == 0.0:
        return 0.0

    current_vol = float(volumes[-1])
    zscore = (current_vol - mean) / std_dev
    return float(round(zscore, 2))
