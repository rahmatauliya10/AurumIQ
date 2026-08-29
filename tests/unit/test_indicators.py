"""Unit tests with known numerical fixtures for technical indicators."""
from decimal import Decimal
import pytest
from engine.features.trend import calculate_ema, calculate_ema_slope, calculate_ema_alignment, calculate_adx
from engine.features.momentum import calculate_rsi, calculate_macd, calculate_roc
from engine.features.volatility import calculate_atr, calculate_bollinger_bands, calculate_realized_volatility
from engine.features.volume import calculate_volume_ratio, calculate_volume_zscore


@pytest.mark.unit
def test_ema_known_fixture():
    """
    Test EMA against standard known numerical sequence:
    Series: [10, 11, 12, 13, 14, 15] with period=5
    SMA(first 5) = (10+11+12+13+14)/5 = 12.0
    Multiplier = 2 / (5 + 1) = 2/6 = 1/3
    Next EMA = (15 - 12) * (1/3) + 12 = 1.0 + 12.0 = 13.0
    """
    series = [Decimal(str(x)) for x in [10, 11, 12, 13, 14, 15]]
    ema = calculate_ema(series, period=5)

    assert len(ema) == 6
    assert ema[0] is None
    assert ema[1] is None
    assert ema[2] is None
    assert ema[3] is None
    assert ema[4] == Decimal("12.0")
    assert ema[5] == Decimal("13.0")


@pytest.mark.unit
def test_rsi_known_fixture():
    """
    Test RSI against known price sequences.
    A purely increasing series must have RSI = 100.0.
    A purely decreasing series must have RSI = 0.0.
    """
    increasing = [Decimal(str(100 + i)) for i in range(20)]
    rsi_up = calculate_rsi(increasing, period=14)
    assert rsi_up == 100.0

    decreasing = [Decimal(str(100 - i)) for i in range(20)]
    rsi_down = calculate_rsi(decreasing, period=14)
    assert rsi_down == 0.0

    # Alternating equal up/down steps
    alternating = [Decimal("100.0"), Decimal("101.0")] * 15
    rsi_alt = calculate_rsi(alternating, period=14)
    assert rsi_alt is not None
    assert 40.0 <= rsi_alt <= 60.0


@pytest.mark.unit
def test_macd_calculation():
    """Test MACD returns valid line, signal, and histogram."""
    # 40 bars upward series
    series = [Decimal(str(100 + i * 2)) for i in range(40)]
    macd_line, signal_line, hist = calculate_macd(series, fast_period=12, slow_period=26, signal_period=9)

    assert macd_line is not None
    assert signal_line is not None
    assert hist is not None
    assert macd_line > 0  # Upward trending series has positive MACD line


@pytest.mark.unit
def test_atr_known_fixture():
    """
    Test ATR against constant bar range of 5.0 points with no gaps.
    """
    highs = [Decimal(str(105 + i)) for i in range(25)]
    lows = [Decimal(str(100 + i)) for i in range(25)]
    closes = [Decimal(str(103 + i)) for i in range(25)]

    atr = calculate_atr(highs, lows, closes, period=14)
    assert atr is not None
    # With H-L = 5 and prev_C = 103 -> TR = max(5, |106-103|=3, |101-103|=2) = 5.0
    assert atr == Decimal("5.00000000")


@pytest.mark.unit
def test_bollinger_bands():
    """Test Bollinger Bands returns middle SMA and symmetric bands."""
    series = [Decimal("100.0")] * 20
    upper, middle, lower, bandwidth = calculate_bollinger_bands(series, period=20, num_std=2.0)

    assert middle == Decimal("100.00000000")
    assert upper == Decimal("100.00000000")
    assert lower == Decimal("100.00000000")
    assert bandwidth == 0.0


@pytest.mark.unit
def test_roc_calculation():
    """Test Rate of Change percentage calculation."""
    series = [Decimal("100.0")] * 12 + [Decimal("110.0")]
    roc = calculate_roc(series, period=12)
    assert roc == 10.0  # +10% change


@pytest.mark.unit
def test_volume_indicators():
    """Test Volume Ratio and Z-Score calculations."""
    volumes = [Decimal("100.0")] * 19 + [Decimal("200.0")]
    # SMA of 19*100 + 200 = 2100 / 20 = 105.0
    # Ratio = 200 / 105 = 1.90
    ratio = calculate_volume_ratio(volumes, period=20)
    assert ratio == 1.90

    zscore = calculate_volume_zscore(volumes, period=20)
    assert zscore is not None
    assert zscore > 2.0  # Spike volume has high z-score


@pytest.mark.unit
def test_indicator_edge_cases():
    """Test edge cases: insufficient lookback, empty series."""
    empty: list[Decimal] = []
    assert calculate_ema(empty, 20) == []
    assert calculate_rsi(empty, 14) is None
    assert calculate_atr(empty, empty, empty, 14) is None
    assert calculate_ema_slope([], 5) is None
    assert calculate_ema_alignment(None, None, None) == 0
