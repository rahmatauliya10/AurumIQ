"""
Targeted tests for Point-In-Time Dataset Loader.

Verifies:
1. 15m/1h/4h/1d are loaded when present in DB.
2. get_closed_candles(tf, as_of=T) never returns a candle that was not closed by T.
3. Missing timeframe data remains fail-closed unavailable.
4. Existing 15m behavior is unchanged.
5. No OOS access is introduced.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest

from apps.instruments.models import Asset, Instrument, InstrumentRole, InstrumentType
from apps.market_data.models import CandleQualityFlag, MarketCandle
from engine.backtest.repository import PointInTimeDataset
from scripts.run_xauusd_calibration import load_point_in_time_dataset_from_db

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def seed_test_candles():
    """Seed test candles across 15m, 1h, 4h, and 1d timeframes in test DB."""
    xau, _ = Asset.objects.get_or_create(code="XAU", defaults={"name": "Gold"})
    usd, _ = Asset.objects.get_or_create(code="USD", defaults={"name": "US Dollar"})
    inst, _ = Instrument.objects.get_or_create(
        base_asset=xau,
        quote_asset=usd,
        defaults={"instrument_type": InstrumentType.SPOT, "role": InstrumentRole.GOLD_REFERENCE},
    )

    t_start = datetime(2024, 2, 8, 19, 12, tzinfo=timezone.utc)

    # 1. Seed 15m candles (20 bars: 5 hours)
    for i in range(20):
        t_open = t_start + timedelta(minutes=15 * i)
        t_close = t_open + timedelta(minutes=15)
        MarketCandle.objects.create(
            instrument=inst,
            source="twelve_data_xauusd",
            timeframe="15m",
            timestamp_open=t_open,
            timestamp_close=t_close,
            open=Decimal(f"{2000 + i}.00"),
            high=Decimal(f"{2005 + i}.00"),
            low=Decimal(f"{1998 + i}.00"),
            close=Decimal(f"{2002 + i}.00"),
            volume=Decimal("100.0"),
            quote_rate=Decimal("1.000000"),
            close_usd=Decimal(f"{2002 + i}.00"),
            is_closed=True,
            data_quality_flag=CandleQualityFlag.OK,
        )

    # 2. Seed 1h candles (8 bars: 8 hours)
    for i in range(8):
        t_open = t_start + timedelta(hours=i)
        t_close = t_open + timedelta(hours=1)
        MarketCandle.objects.create(
            instrument=inst,
            source="twelve_data_xauusd",
            timeframe="1h",
            timestamp_open=t_open,
            timestamp_close=t_close,
            open=Decimal(f"{2000 + i * 2}.00"),
            high=Decimal(f"{2010 + i * 2}.00"),
            low=Decimal(f"{1995 + i * 2}.00"),
            close=Decimal(f"{2005 + i * 2}.00"),
            volume=Decimal("400.0"),
            quote_rate=Decimal("1.000000"),
            close_usd=Decimal(f"{2005 + i * 2}.00"),
            is_closed=True,
            data_quality_flag=CandleQualityFlag.OK,
        )

    # 3. Seed 4h candles (4 bars: 16 hours)
    for i in range(4):
        t_open = t_start + timedelta(hours=4 * i)
        t_close = t_open + timedelta(hours=4)
        MarketCandle.objects.create(
            instrument=inst,
            source="twelve_data_xauusd",
            timeframe="4h",
            timestamp_open=t_open,
            timestamp_close=t_close,
            open=Decimal(f"{2000 + i * 5}.00"),
            high=Decimal(f"{2020 + i * 5}.00"),
            low=Decimal(f"{1990 + i * 5}.00"),
            close=Decimal(f"{2015 + i * 5}.00"),
            volume=Decimal("1600.0"),
            quote_rate=Decimal("1.000000"),
            close_usd=Decimal(f"{2015 + i * 5}.00"),
            is_closed=True,
            data_quality_flag=CandleQualityFlag.OK,
        )

    # 4. Seed 1d candles (3 bars: 3 days)
    for i in range(3):
        t_open = t_start + timedelta(days=i)
        t_close = t_open + timedelta(days=1)
        MarketCandle.objects.create(
            instrument=inst,
            source="twelve_data_xauusd",
            timeframe="1d",
            timestamp_open=t_open,
            timestamp_close=t_close,
            open=Decimal(f"{2000 + i * 10}.00"),
            high=Decimal(f"{2040 + i * 10}.00"),
            low=Decimal(f"{1980 + i * 10}.00"),
            close=Decimal(f"{2030 + i * 10}.00"),
            volume=Decimal("8000.0"),
            quote_rate=Decimal("1.000000"),
            close_usd=Decimal(f"{2030 + i * 10}.00"),
            is_closed=True,
            data_quality_flag=CandleQualityFlag.OK,
        )

    # 5. Seed unclosed candle (should be ignored by loader)
    MarketCandle.objects.create(
        instrument=inst,
        source="twelve_data_xauusd",
        timeframe="15m",
        timestamp_open=t_start + timedelta(minutes=15 * 20),
        timestamp_close=t_start + timedelta(minutes=15 * 21),
        open=Decimal("2050.00"),
        high=Decimal("2060.00"),
        low=Decimal("2040.00"),
        close=Decimal("2055.00"),
        volume=Decimal("50.0"),
        quote_rate=Decimal("1.000000"),
        close_usd=Decimal("2055.00"),
        is_closed=False,
        data_quality_flag=CandleQualityFlag.SUSPECT,
    )

    # 6. Seed a future candle far ahead (OOS)
    MarketCandle.objects.create(
        instrument=inst,
        source="twelve_data_xauusd",
        timeframe="15m",
        timestamp_open=t_start + timedelta(days=30),
        timestamp_close=t_start + timedelta(days=30, minutes=15),
        open=Decimal("2100.00"),
        high=Decimal("2110.00"),
        low=Decimal("2090.00"),
        close=Decimal("2105.00"),
        volume=Decimal("100.0"),
        quote_rate=Decimal("1.000000"),
        close_usd=Decimal("2105.00"),
        is_closed=True,
        data_quality_flag=CandleQualityFlag.OK,
    )


def test_01_all_required_timeframes_loaded():
    """Verify that 15m, 1h, 4h, and 1d are loaded and populated in PointInTimeDataset."""
    t0 = datetime(2024, 2, 8, 19, 12, tzinfo=timezone.utc)
    t1 = t0 + timedelta(days=5)

    dataset = load_point_in_time_dataset_from_db(t0, t1)

    c_15m = dataset.get_closed_candles("15m", as_of=t1)
    c_1h = dataset.get_closed_candles("1h", as_of=t1)
    c_4h = dataset.get_closed_candles("4h", as_of=t1)
    c_1d = dataset.get_closed_candles("1d", as_of=t1)

    assert len(c_15m) == 20, f"Expected 20 15m candles, got {len(c_15m)}"
    assert len(c_1h) == 8, f"Expected 8 1h candles, got {len(c_1h)}"
    assert len(c_4h) == 4, f"Expected 4 4h candles, got {len(c_4h)}"
    assert len(c_1d) == 3, f"Expected 3 1d candles, got {len(c_1d)}"


def test_02_strict_closed_candle_temporal_causality():
    """Verify get_closed_candles(tf, as_of=T) NEVER returns a candle that was not closed by T."""
    t0 = datetime(2024, 2, 8, 19, 12, tzinfo=timezone.utc)
    t1 = t0 + timedelta(days=5)

    dataset = load_point_in_time_dataset_from_db(t0, t1)

    # Check at an intermediate timestamp T (e.g. 2 hours after t0)
    t_test = t0 + timedelta(hours=2)
    for tf in ["15m", "1h", "4h", "1d"]:
        candles = dataset.get_closed_candles(tf, as_of=t_test)
        for c in candles:
            assert c.is_closed is True, f"Unclosed candle returned for tf={tf}"
            c_close = c.timestamp_close.astimezone(timezone.utc) if c.timestamp_close.tzinfo else c.timestamp_close.replace(tzinfo=timezone.utc)
            assert c_close <= t_test, f"Future candle leaked! c.timestamp_close={c_close} > as_of={t_test} for tf={tf}"


def test_03_missing_timeframe_fail_closed():
    """Verify missing timeframe data remains fail-closed unavailable."""
    t0 = datetime(2024, 2, 8, 19, 12, tzinfo=timezone.utc)
    t1 = t0 + timedelta(days=5)

    # Request only 15m and 1h
    dataset = load_point_in_time_dataset_from_db(t0, t1, required_timeframes=("15m", "1h"))

    assert len(dataset.get_closed_candles("15m", as_of=t1)) == 20
    assert len(dataset.get_closed_candles("1h", as_of=t1)) == 8
    # 4h and 1d were omitted -> should be empty list, not crash, not synthesize
    assert dataset.get_closed_candles("4h", as_of=t1) == []
    assert dataset.get_closed_candles("1d", as_of=t1) == []


def test_04_existing_15m_behavior_unchanged():
    """Verify existing 15m candles have identical properties, count, and ordering."""
    t0 = datetime(2024, 2, 8, 19, 12, tzinfo=timezone.utc)
    t1 = t0 + timedelta(days=5)

    ds_mtf = load_point_in_time_dataset_from_db(t0, t1, required_timeframes=("15m", "1h", "4h", "1d"))
    ds_15m_only = load_point_in_time_dataset_from_db(t0, t1, required_timeframes=("15m",))

    c_mtf_15m = ds_mtf.get_closed_candles("15m", as_of=t1)
    c_legacy_15m = ds_15m_only.get_closed_candles("15m", as_of=t1)

    assert len(c_mtf_15m) == len(c_legacy_15m) == 20
    for c1, c2 in zip(c_mtf_15m, c_legacy_15m):
        assert c1.timestamp_open == c2.timestamp_open
        assert c1.timestamp_close == c2.timestamp_close
        assert c1.open == c2.open
        assert c1.close == c2.close
        assert c1.high == c2.high
        assert c1.low == c2.low


def test_05_no_oos_access_introduced():
    """Verify that loader strictly enforces end_time boundary and does not load beyond end_time."""
    t0 = datetime(2024, 2, 8, 19, 12, tzinfo=timezone.utc)
    t_cutoff = t0 + timedelta(days=5)

    # Loader called up to t_cutoff: the day 30 future candle must NOT be loaded
    dataset = load_point_in_time_dataset_from_db(t0, t_cutoff)

    for tf in ["15m", "1h", "4h", "1d"]:
        bars = dataset.get_closed_candles(tf, as_of=datetime(2030, 1, 1, tzinfo=timezone.utc))
        for b in bars:
            b_open = b.timestamp_open.astimezone(timezone.utc) if b.timestamp_open.tzinfo else b.timestamp_open.replace(tzinfo=timezone.utc)
            assert b_open < t_cutoff, f"Candle open {b_open} exceeds cutoff {t_cutoff} for tf={tf}"
