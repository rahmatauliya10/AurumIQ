"""Unit tests for Phase 6 XAUUSD Fingerprinting and Provenance."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest

from engine.backtest.xauusd_fingerprint import (
    compute_xauusd_backtest_fingerprint,
    compute_xauusd_dataset_identity,
)
from engine.backtest.xauusd_types import (
    XauUsdBacktestRunSpec,
    XauUsdCostConfig,
    XauUsdCostScenario,
)
from engine.core.types import CandleData


def make_candle(ts_open: datetime, o: Decimal, h: Decimal, l: Decimal, c: Decimal) -> CandleData:
    return CandleData(
        timestamp_open=ts_open,
        timestamp_close=ts_open + timedelta(minutes=15),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=Decimal("100"),
        is_closed=True,
        source_id="TEST",
    )


def test_dataset_identity_determinism():
    """Test that dataset identity is strictly deterministic for identical candle streams."""
    t0 = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    t_end = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)

    candles1 = [make_candle(t0 + timedelta(minutes=15 * i), Decimal("2600.00"), Decimal("2605.00"), Decimal("2595.00"), Decimal("2600.00")) for i in range(40)]
    candles2 = [make_candle(t0 + timedelta(minutes=15 * i), Decimal("2600.00"), Decimal("2605.00"), Decimal("2595.00"), Decimal("2600.00")) for i in range(40)]

    hash1 = compute_xauusd_dataset_identity(candles_15m=candles1, start_time=t0, end_time=t_end)
    hash2 = compute_xauusd_dataset_identity(candles_15m=candles2, start_time=t0, end_time=t_end)

    assert hash1 == hash2
    assert len(hash1) == 64


def test_dataset_identity_sensitivity_to_data_change():
    """Test that changing a single price in the dataset changes the dataset hash."""
    t0 = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    t_end = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)

    candles1 = [make_candle(t0 + timedelta(minutes=15 * i), Decimal("2600.00"), Decimal("2605.00"), Decimal("2595.00"), Decimal("2600.00")) for i in range(40)]
    candles2 = [make_candle(t0 + timedelta(minutes=15 * i), Decimal("2600.00"), Decimal("2605.00"), Decimal("2595.00"), Decimal("2600.00")) for i in range(40)]
    # Mutate 1 candle
    candles2[10] = make_candle(t0 + timedelta(minutes=15 * 10), Decimal("2600.00"), Decimal("2605.00"), Decimal("2595.00"), Decimal("2600.50"))

    hash1 = compute_xauusd_dataset_identity(candles_15m=candles1, start_time=t0, end_time=t_end)
    hash2 = compute_xauusd_dataset_identity(candles_15m=candles2, start_time=t0, end_time=t_end)

    assert hash1 != hash2


def test_backtest_fingerprint_requires_code_revision():
    """Test that missing code_revision strictly raises ValueError."""
    t0 = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)

    spec = XauUsdBacktestRunSpec(
        instrument="XAUUSD",
        start_time=t0,
        end_time=t1,
        timeframes=("15m",),
        cost_config=XauUsdCostConfig.idealized(),
        cost_scenario=XauUsdCostScenario.IDEALIZED,
        dataset_hash="hash_123",
        code_revision="",
    )

    with pytest.raises(ValueError, match="requires an explicit non-empty code_revision"):
        compute_xauusd_backtest_fingerprint(spec)
