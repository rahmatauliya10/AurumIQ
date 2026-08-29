"""Unit tests for RegimeEngine deterministic classifications."""
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from engine.core.types import FeatureSnapshot, RegimeType
from engine.regime.engine import RegimeEngine


@pytest.mark.unit
def test_regime_classification_unknown_on_insufficient_bars():
    """Regime is UNKNOWN when 200 EMA or core features are None."""
    engine = RegimeEngine()
    features = FeatureSnapshot(
        timestamp=datetime.now(timezone.utc),
        ema20=Decimal("2500"), ema50=Decimal("2490"), ema200=None,  # Missing 200 EMA!
        ema_slope_20=0.1, ema_alignment=1, adx=25.0, plus_di=30.0, minus_di=10.0,
        rsi14=60.0, macd_line=Decimal("5"), macd_signal=Decimal("4"), macd_hist=Decimal("1"), roc12=2.0,
        atr14=Decimal("10"), atr_pct=0.4, bb_upper=Decimal("2520"), bb_middle=Decimal("2500"),
        bb_lower=Decimal("2480"), bb_bandwidth=1.6, realized_vol_20=0.8, volume_ratio_20=1.0, volume_zscore_20=0.0,
    )
    res = engine.classify(features)
    assert res.regime == RegimeType.UNKNOWN
    assert res.confidence == 0.0


@pytest.mark.unit
def test_regime_bull_trend_classification():
    """Verify clean BULL_TREND classification."""
    engine = RegimeEngine()
    features = FeatureSnapshot(
        timestamp=datetime.now(timezone.utc),
        ema20=Decimal("2550"), ema50=Decimal("2500"), ema200=Decimal("2400"),
        ema_slope_20=0.15, ema_alignment=1, adx=30.0, plus_di=35.0, minus_di=10.0,
        rsi14=65.0, macd_line=Decimal("10"), macd_signal=Decimal("8"), macd_hist=Decimal("2"), roc12=4.0,
        atr14=Decimal("10"), atr_pct=0.4, bb_upper=Decimal("2570"), bb_middle=Decimal("2550"),
        bb_lower=Decimal("2530"), bb_bandwidth=1.5, realized_vol_20=1.0, volume_ratio_20=1.2, volume_zscore_20=0.5,
    )
    res = engine.classify(features)
    assert res.regime == RegimeType.BULL_TREND
    assert res.confidence >= 0.80


@pytest.mark.unit
def test_regime_bear_trend_classification():
    """Verify clean BEAR_TREND classification."""
    engine = RegimeEngine()
    features = FeatureSnapshot(
        timestamp=datetime.now(timezone.utc),
        ema20=Decimal("2350"), ema50=Decimal("2400"), ema200=Decimal("2500"),
        ema_slope_20=-0.15, ema_alignment=-1, adx=28.0, plus_di=10.0, minus_di=35.0,
        rsi14=35.0, macd_line=Decimal("-10"), macd_signal=Decimal("-8"), macd_hist=Decimal("-2"), roc12=-4.0,
        atr14=Decimal("10"), atr_pct=0.4, bb_upper=Decimal("2370"), bb_middle=Decimal("2350"),
        bb_lower=Decimal("2330"), bb_bandwidth=1.5, realized_vol_20=1.0, volume_ratio_20=1.2, volume_zscore_20=0.5,
    )
    res = engine.classify(features)
    assert res.regime == RegimeType.BEAR_TREND
    assert res.confidence >= 0.80


@pytest.mark.unit
def test_regime_range_classification():
    """Verify RANGE classification when ADX < 20 and slope is flat."""
    engine = RegimeEngine()
    features = FeatureSnapshot(
        timestamp=datetime.now(timezone.utc),
        ema20=Decimal("2500"), ema50=Decimal("2501"), ema200=Decimal("2499"),
        ema_slope_20=0.01, ema_alignment=0, adx=14.0, plus_di=18.0, minus_di=17.0,
        rsi14=51.0, macd_line=Decimal("0.5"), macd_signal=Decimal("0.4"), macd_hist=Decimal("0.1"), roc12=0.2,
        atr14=Decimal("5"), atr_pct=0.2, bb_upper=Decimal("2510"), bb_middle=Decimal("2500"),
        bb_lower=Decimal("2490"), bb_bandwidth=0.8, realized_vol_20=0.5, volume_ratio_20=0.9, volume_zscore_20=-0.2,
    )
    res = engine.classify(features)
    assert res.regime == RegimeType.RANGE


@pytest.mark.unit
def test_regime_high_volatility_override():
    """High volatility overrides trend states."""
    engine = RegimeEngine()
    features = FeatureSnapshot(
        timestamp=datetime.now(timezone.utc),
        ema20=Decimal("2550"), ema50=Decimal("2500"), ema200=Decimal("2400"),
        ema_slope_20=0.5, ema_alignment=1, adx=40.0, plus_di=35.0, minus_di=10.0,
        rsi14=65.0, macd_line=Decimal("20"), macd_signal=Decimal("15"), macd_hist=Decimal("5"), roc12=10.0,
        atr14=Decimal("100"), atr_pct=4.0,  # Extreme ATR % (> 3.0%)
        bb_upper=Decimal("2750"), bb_middle=Decimal("2550"), bb_lower=Decimal("2350"),
        bb_bandwidth=16.0, realized_vol_20=6.5, volume_ratio_20=3.0, volume_zscore_20=3.5,
    )
    res = engine.classify(features)
    assert res.regime == RegimeType.HIGH_VOLATILITY
