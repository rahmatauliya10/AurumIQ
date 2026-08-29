"""Unit tests for Causal Market Structure Engine."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest
from engine.core.types import CandleData, BosType, StructureType, SwingType
from engine.structure.causal_swings import detect_causal_swings
from engine.structure.engine import CausalStructureEngine


def _make_candle(idx: int, o: str, h: str, l: str, c: str) -> CandleData:
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=15 * idx)
    return CandleData(
        timestamp_open=t0,
        timestamp_close=t0 + timedelta(minutes=15),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(l),
        close=Decimal(c),
        volume=Decimal("100"),
        is_closed=True,
    )


@pytest.mark.unit
def test_causal_swing_confirmation_timing():
    """
    Verify causality: A swing high at bar 3 (with left=3, right=3) is confirmed
    ONLY after bar 6 is closed, and detected_at timestamp is strictly bar 6's timestamp.
    """
    candles = [
        _make_candle(0, "100", "102", "98", "101"),
        _make_candle(1, "101", "105", "100", "104"),
        _make_candle(2, "104", "108", "102", "106"),
        _make_candle(3, "106", "120", "105", "118"),  # Highest peak!
        _make_candle(4, "118", "115", "110", "112"),
        _make_candle(5, "112", "110", "105", "108"),
        _make_candle(6, "108", "105", "100", "102"),  # Bar 6 confirms bar 3!
    ]

    # Evaluation with 6 bars (0 to 5) -> right_bars=3 requires bar 6 -> No swing confirmed yet!
    swings_5 = detect_causal_swings(candles[:6], left_bars=3, right_bars=3)
    assert len(swings_5) == 0

    # Evaluation with 7 bars (0 to 6) -> Swing at bar 3 is now confirmed!
    swings_6 = detect_causal_swings(candles, left_bars=3, right_bars=3)
    assert len(swings_6) == 1
    s = swings_6[0]
    assert s.index == 3
    assert s.price == Decimal("120")
    assert s.swing_type == SwingType.HIGH
    assert s.timestamp == candles[3].timestamp_open
    assert s.detected_at == candles[6].timestamp_close  # Strictly causal detection time (when bar 6 closes)!


@pytest.mark.unit
def test_break_of_structure_detection():
    """
    Verify Break of Structure (BOS):
    Current closed candle breaks above confirmed swing high price.
    """
    # 7 bars that establish swing high at bar 3 (high=120) + 1 bar breaking above 120 (close=125)
    candles = [
        _make_candle(0, "100", "102", "98", "101"),
        _make_candle(1, "101", "105", "100", "104"),
        _make_candle(2, "104", "108", "102", "106"),
        _make_candle(3, "106", "120", "105", "118"),  # Swing high at 120
        _make_candle(4, "118", "115", "110", "112"),
        _make_candle(5, "112", "110", "105", "108"),
        _make_candle(6, "108", "105", "100", "102"),  # Confirms swing high
        _make_candle(7, "102", "126", "102", "125"),  # Close 125 > 120 -> Bullish BOS!
    ]

    engine = CausalStructureEngine()
    res = engine.analyze(candles, atr=Decimal("5.0"))

    assert res.bos == BosType.BULLISH
    assert res.last_swing_high is not None
    assert res.last_swing_high.price == Decimal("120")


@pytest.mark.unit
def test_support_resistance_zones_clustering():
    """Verify ATR-normalized zone clustering for support/resistance."""
    candles = [
        _make_candle(0, "100", "102", "98", "101"),
        _make_candle(1, "101", "105", "100", "104"),
        _make_candle(2, "104", "108", "102", "106"),
        _make_candle(3, "106", "120", "105", "118"),
        _make_candle(4, "118", "115", "110", "112"),
        _make_candle(5, "112", "110", "105", "108"),
        _make_candle(6, "108", "105", "100", "102"),
    ]

    engine = CausalStructureEngine()
    res = engine.analyze(candles, atr=Decimal("4.0"))

    # Zone created around 120 with tolerance = 0.5 * 4.0 = 2.0 -> [120 - 1.0, 120 + 1.0] = [119.0, 121.0]
    assert len(res.zones) >= 1
    rz = [z for z in res.zones if z.zone_type == "RESISTANCE"][0]
    assert rz.price_low == Decimal("119.00000000")
    assert rz.price_high == Decimal("121.00000000")
    assert rz.is_active is True
