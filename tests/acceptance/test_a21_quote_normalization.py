"""Acceptance Test A21: Quote Currency Normalization."""
from decimal import Decimal
import pytest
from apps.instruments.models import Asset, Instrument, InstrumentType
from apps.market_data.models import MarketCandle, CandleQualityFlag
from apps.market_data.normalization import QuoteNormalizer
from django.utils import timezone


@pytest.mark.acceptance
@pytest.mark.django_db
def test_a21_quote_currency_normalization_formula():
    """
    A21: Verify that quote normalization rigorously applies:
      XAUT_USD = XAUT_USDT * USDTUSD
    and validates both the QuoteNormalizer and the MarketCandle ORM persistence layer.
    """
    normalizer = QuoteNormalizer()

    raw_xaut_usdt = Decimal("2512.45000000")
    usdt_usd_rate = Decimal("0.999800")  # slight 0.02% discount

    # 1. Test QuoteNormalizer result
    norm_result = normalizer.normalize_price(raw_xaut_usdt, usdt_usd_rate)
    expected_usd = (raw_xaut_usdt * usdt_usd_rate).quantize(Decimal("0.00000001"))

    assert norm_result.normalized_price == expected_usd
    assert norm_result.hard_fail is False

    # 2. Test MarketCandle ORM auto-computation and storage
    xaut = Asset.objects.create(code="XAUT", name="Tether Gold")
    usdt = Asset.objects.create(code="USDT", name="Tether USD")
    inst = Instrument.objects.create(
        base_asset=xaut, quote_asset=usdt, instrument_type=InstrumentType.SPOT
    )

    candle = MarketCandle.objects.create(
        instrument=inst,
        source="binance",
        timeframe="15m",
        timestamp_open=timezone.now(),
        timestamp_close=timezone.now(),
        open=Decimal("2510.00"),
        high=Decimal("2515.00"),
        low=Decimal("2508.00"),
        close=raw_xaut_usdt,
        volume=Decimal("50.0"),
        quote_rate=usdt_usd_rate,
        is_closed=True,
        data_quality_flag=CandleQualityFlag.OK,
    )

    candle.refresh_from_db()
    assert candle.close_usd == expected_usd
    assert candle.close_usd != candle.close  # Verified true normalization difference
