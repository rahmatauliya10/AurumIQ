"""Unit tests for Phase 6 XAUUSD Fingerprinting and Provenance."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest

from engine.backtest.xauusd_fingerprint import (
    compute_xauusd_backtest_fingerprint,
    compute_xauusd_dataset_identity,
    compute_xauusd_walkforward_fingerprint,
)
from engine.backtest.xauusd_types import (
    XauUsdBacktestRunSpec,
    XauUsdCostConfig,
    XauUsdCostScenario,
    XauUsdWalkForwardConfig,
)
from engine.core.types import CandleData, QuoteData


def make_candle(ts_open: datetime, o: Decimal, h: Decimal, l: Decimal, c: Decimal, source_id: str = "TEST") -> CandleData:
    return CandleData(
        timestamp_open=ts_open,
        timestamp_close=ts_open + timedelta(minutes=15),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=Decimal("100"),
        is_closed=True,
        source_id=source_id,
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
    """Test that changing a price, source_id, or quote changes dataset identity."""
    t0 = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    t_end = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)

    candles1 = [make_candle(t0 + timedelta(minutes=15 * i), Decimal("2600.00"), Decimal("2605.00"), Decimal("2595.00"), Decimal("2600.00")) for i in range(40)]
    candles2 = [make_candle(t0 + timedelta(minutes=15 * i), Decimal("2600.00"), Decimal("2605.00"), Decimal("2595.00"), Decimal("2600.00")) for i in range(40)]
    candles2[10] = make_candle(t0 + timedelta(minutes=15 * 10), Decimal("2600.00"), Decimal("2605.00"), Decimal("2595.00"), Decimal("2600.50"))

    hash1 = compute_xauusd_dataset_identity(candles_15m=candles1, start_time=t0, end_time=t_end)
    hash2 = compute_xauusd_dataset_identity(candles_15m=candles2, start_time=t0, end_time=t_end)
    assert hash1 != hash2

    # Test quote sensitivity
    q1 = [QuoteData(timestamp=t0 + timedelta(minutes=1), bid=Decimal("2600.00"), ask=Decimal("2600.50"))]
    q2 = [QuoteData(timestamp=t0 + timedelta(minutes=1), bid=Decimal("2600.10"), ask=Decimal("2600.60"))]

    hash_q1 = compute_xauusd_dataset_identity(candles_15m=candles1, start_time=t0, end_time=t_end, quotes=q1)
    hash_q2 = compute_xauusd_dataset_identity(candles_15m=candles1, start_time=t0, end_time=t_end, quotes=q2)
    assert hash_q1 != hash_q2


def test_backtest_fingerprint_requires_code_revision():
    """Test that missing code_revision strictly raises ValueError."""
    t0 = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="requires an explicit non-empty code_revision"):
        XauUsdBacktestRunSpec(
            instrument="XAUUSD",
            start_time=t0,
            end_time=t1,
            timeframes=("15m",),
            cost_config=XauUsdCostConfig.idealized(),
            cost_scenario=XauUsdCostScenario.IDEALIZED,
            dataset_hash="hash_123",
            holding_horizon_bars_15m=8,
            max_fill_wait_bars_15m=4,
            code_revision="",
        )


def test_walkforward_fingerprint_sensitivity():
    """Test that altering fold count, embargo, or ratios changes walkforward fingerprint."""
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
        holding_horizon_bars_15m=8,
        max_fill_wait_bars_15m=4,
        code_revision="46e388a106b9bdc388e646c73570e7879142c837",
    )

    wf1 = XauUsdWalkForwardConfig(total_folds=3, train_ratio=0.6, val_ratio=0.2, oos_ratio=0.2, embargo_seconds=0.0)
    wf2 = XauUsdWalkForwardConfig(total_folds=3, train_ratio=0.6, val_ratio=0.2, oos_ratio=0.2, embargo_seconds=3600.0)
    wf3 = XauUsdWalkForwardConfig(total_folds=4, train_ratio=0.6, val_ratio=0.2, oos_ratio=0.2, embargo_seconds=0.0)

    fp1 = compute_xauusd_walkforward_fingerprint(spec, wf1)
    fp2 = compute_xauusd_walkforward_fingerprint(spec, wf2)
    fp3 = compute_xauusd_walkforward_fingerprint(spec, wf3)

    assert fp1 != fp2
    assert fp1 != fp3
