"""Unit tests for Binance, OKX, Gold Reference, and USDT/USD provider adapters."""
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch, MagicMock
import pytest
from apps.market_data.providers.base import RawCandle, ProviderHealth, TickerSnapshot
from apps.market_data.providers.binance import BinanceProvider
from apps.market_data.providers.okx import OKXProvider
from apps.market_data.providers.gold_reference import GoldReferenceProvider
from apps.market_data.providers.usdt_usd import UsdtUsdRateProvider
from apps.market_data.providers.registry import ProviderRegistry


@pytest.mark.unit
def test_binance_provider_candle_parsing():
    """Verify BinanceProvider parses raw klines format properly."""
    provider = BinanceProvider()
    assert provider.provider_id == "binance"
    assert provider.map_timeframe("15m") == "15m"

    mock_kline = [
        1724900000000,      # open time
        "2510.50",          # open
        "2515.00",          # high
        "2508.00",          # low
        "2512.20",          # close
        "125.45",           # volume
        1724900899999,      # close time
        "315000.00",        # quote volume
        150,                # trades
        "60.0", "150000.0", "0"
    ]

    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [mock_kline]
        mock_get.return_value = mock_resp

        start = datetime(2026, 8, 29, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 8, 29, 1, 0, tzinfo=timezone.utc)
        candles = provider.fetch_candles("XAUT/USDT", "15m", start, end)

        assert len(candles) == 1
        c = candles[0]
        assert isinstance(c, RawCandle)
        assert c.open == Decimal("2510.50")
        assert c.high == Decimal("2515.00")
        assert c.low == Decimal("2508.00")
        assert c.close == Decimal("2512.20")
        assert c.volume == Decimal("125.45")
        assert c.source == "binance"


@pytest.mark.unit
def test_okx_provider_candle_parsing():
    """Verify OKXProvider parses OKX candle array format properly."""
    provider = OKXProvider()
    assert provider.provider_id == "okx"
    assert provider.map_timeframe("1h") == "1H"

    mock_candle = [
        "1724900000000",    # ts
        "2510.00",          # o
        "2520.00",          # h
        "2505.00",          # l
        "2518.00",          # c
        "50.5",             # vol
        "127000.0",         # volCcy
        "127000.0",         # volCcyQuote
        "1"                 # confirm (closed)
    ]

    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"code": "0", "data": [mock_candle]}
        mock_get.return_value = mock_resp

        start = datetime(2026, 8, 29, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 8, 29, 2, 0, tzinfo=timezone.utc)
        candles = provider.fetch_candles("XAUT-USDT", "1h", start, end)

        assert len(candles) == 1
        c = candles[0]
        assert c.open == Decimal("2510.00")
        assert c.high == Decimal("2520.00")
        assert c.low == Decimal("2505.00")
        assert c.close == Decimal("2518.00")
        assert c.is_closed is True
        assert c.source == "okx"


@pytest.mark.unit
def test_usdt_usd_rate_provider():
    """Verify UsdtUsdRateProvider parses inverse USDCUSDT rate."""
    provider = UsdtUsdRateProvider()
    assert provider.provider_id == "usdt_usd"

    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        # 1 USDC = 1.0005 USDT -> 1 USDT = 1 / 1.0005 = 0.999500 USD
        mock_resp.json.return_value = {"price": "1.0005"}
        mock_get.return_value = mock_resp

        rate = provider.get_current_rate()
        assert isinstance(rate, Decimal)
        assert rate == Decimal("0.999500")


@pytest.mark.unit
def test_provider_registry_management():
    """Verify ProviderRegistry registration and lookup."""
    reg = ProviderRegistry()
    binance = BinanceProvider()
    okx = OKXProvider()

    reg.register(binance)
    reg.register(okx)

    assert reg.has("binance") is True
    assert reg.has("okx") is True
    assert reg.has("unknown") is False
    assert reg.get("binance") == binance

    with pytest.raises(KeyError):
        reg.get("non_existent")
