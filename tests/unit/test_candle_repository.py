"""Unit tests for DjangoCandleRepository protocol compliance and point-in-time loading."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest
from engine.core.interfaces import CandleRepository
from engine.core.types import CandleData
from apps.instruments.models import Asset, Instrument, InstrumentType
from apps.market_data.models import MarketCandle, CandleQualityFlag
from apps.market_data.repositories import DjangoCandleRepository


@pytest.fixture
def sample_candles():
    """Seed test candles in PostgreSQL."""
    xaut = Asset.objects.create(code="XAUT", name="Tether Gold")
    usdt = Asset.objects.create(code="USDT", name="Tether USD")
    inst = Instrument.objects.create(
        base_asset=xaut, quote_asset=usdt, instrument_type=InstrumentType.SPOT
    )

    base_time = datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc)
    candles = []
    for i in range(10):
        t_open = base_time + timedelta(minutes=15 * i)
        t_close = t_open + timedelta(minutes=15)
        c = MarketCandle.objects.create(
            instrument=inst,
            source="binance",
            timeframe="15m",
            timestamp_open=t_open,
            timestamp_close=t_close,
            open=Decimal(f"{2500 + i}.00"),
            high=Decimal(f"{2505 + i}.00"),
            low=Decimal(f"{2498 + i}.00"),
            close=Decimal(f"{2502 + i}.00"),
            volume=Decimal("100.0"),
            quote_rate=Decimal("1.000000"),
            close_usd=Decimal(f"{2502 + i}.00"),
            is_closed=True,
            data_quality_flag=CandleQualityFlag.OK,
        )
        candles.append(c)
    return inst, candles


@pytest.mark.unit
@pytest.mark.django_db
def test_django_candle_repository_implements_protocol(sample_candles):
    """Verify DjangoCandleRepository satisfies engine CandleRepository Protocol."""
    repo = DjangoCandleRepository()
    assert isinstance(repo, CandleRepository)


@pytest.mark.unit
@pytest.mark.django_db
def test_load_window_causal_slicing(sample_candles):
    """Verify load_window returns exactly requested bars in chronological order."""
    inst, candles = sample_candles
    repo = DjangoCandleRepository()

    # Load 5 bars up to 8th candle
    end_at = candles[7].timestamp_open
    window = repo.load_window("XAUT/USDT", "15m", end_at=end_at, bars=5)

    assert len(window) == 5
    # Must be pure dataclass
    for c in window:
        assert isinstance(c, CandleData)
        assert hasattr(c, "close")
        assert hasattr(c, "source_id")

    # Chronological ascending order
    assert window[0].timestamp_open < window[-1].timestamp_open
    assert window[-1].timestamp_open == end_at
    assert window[-1].close == candles[7].close


@pytest.mark.unit
@pytest.mark.django_db
def test_quarantined_candles_excluded(sample_candles):
    """Verify quarantined candles are omitted from repo window loading."""
    inst, candles = sample_candles
    candles[5].data_quality_flag = CandleQualityFlag.QUARANTINED
    candles[5].save()

    repo = DjangoCandleRepository()
    end_at = candles[7].timestamp_open
    window = repo.load_window("XAUT/USDT", "15m", end_at=end_at, bars=10)

    # 8 candles total before end_at, 1 is quarantined -> 7 returned
    assert len(window) == 7
    loaded_timestamps = [c.timestamp_open for c in window]
    assert candles[5].timestamp_open not in loaded_timestamps
