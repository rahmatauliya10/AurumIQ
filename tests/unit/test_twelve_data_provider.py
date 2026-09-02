"""Unit tests for Twelve Data XAU/USD Market Data Provider.

All tests run strictly offline using mocked fixtures.
No real network calls or exposed API keys.
"""
from datetime import datetime, timedelta, timezone, tzinfo
from decimal import Decimal
from unittest.mock import MagicMock, patch
import pytest
import requests

from apps.market_data.providers.base import RawCandle
from apps.market_data.providers.registry import registry
from apps.market_data.providers.twelve_data import TwelveDataProvider, _sanitize_secret


@pytest.fixture
def mock_api_key():
    return "mock_td_secret_key_1234567890abcdef"


@pytest.fixture
def provider(mock_api_key):
    return TwelveDataProvider(api_key=mock_api_key, timeout=5.0)


class TestTwelveDataIntervalMapping:
    """Validate timeframe mapping."""

    @pytest.mark.parametrize(
        ("internal_tf", "expected_provider_interval"),
        [
            ("1m", "1min"),
            ("5m", "5min"),
            ("15m", "15min"),
            ("1h", "1h"),
            ("4h", "4h"),
            ("1d", "1day"),
        ],
    )
    def test_interval_mapping_valid(self, provider, internal_tf, expected_provider_interval):
        assert provider.map_timeframe(internal_tf) == expected_provider_interval

    def test_interval_mapping_unsupported(self, provider):
        with pytest.raises(KeyError, match="UNSUPPORTED_TIMEFRAME"):
            provider.map_timeframe("2m")
        with pytest.raises(KeyError, match="UNSUPPORTED_TIMEFRAME"):
            provider.map_timeframe("30m")
        with pytest.raises(KeyError, match="UNSUPPORTED_TIMEFRAME"):
            provider.map_timeframe("1w")


class TestTwelveDataSymbolSeparation:
    """Validate canonical XAUUSD vs XAU/USD separation and prohibited proxies."""

    def test_canonical_and_provider_symbol_accepted(self, provider):
        assert provider._validate_symbol("XAUUSD") == "XAU/USD"
        assert provider._validate_symbol("XAU/USD") == "XAU/USD"
        assert provider._validate_symbol("xauusd") == "XAU/USD"
        assert provider._validate_symbol("xau/usd") == "XAU/USD"

    @pytest.mark.parametrize("prohibited", ["XAUT", "XAUTUSDT", "PAXG", "PAXGUSDT", "XAUEUR"])
    def test_prohibited_crypto_gold_fallback(self, provider, prohibited):
        with pytest.raises(ValueError, match="PROHIBITED_SYMBOL_FALLBACK"):
            provider._validate_symbol(prohibited)

    def test_unknown_symbol_mismatch(self, provider):
        with pytest.raises(ValueError, match="SYMBOL_MISMATCH"):
            provider._validate_symbol("EURUSD")
        with pytest.raises(ValueError, match="SYMBOL_MISMATCH"):
            provider._validate_symbol("BTCUSD")


class TestTwelveDataDecimalAndUTC:
    """Validate direct Decimal(str) parsing and UTC-aware timestamps."""

    @patch("apps.market_data.providers.twelve_data.requests.get")
    def test_decimal_direct_parsing_and_utc_normalization(self, mock_get, provider):
        # 1 completed candle in the past
        past_time = "2026-08-31 10:00:00"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "ok",
            "meta": {"symbol": "XAU/USD", "interval": "15min"},
            "values": [
                {
                    "datetime": past_time,
                    "open": "4350.12345",
                    "high": "4355.98765",
                    "low": "4348.00001",
                    "close": "4352.55555",
                }
            ],
        }
        mock_get.return_value = mock_response

        start = datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc)
        end = datetime(2026, 8, 31, 11, 0, tzinfo=timezone.utc)
        candles = provider.fetch_candles("XAUUSD", "15m", start, end)

        assert len(candles) == 1
        c = candles[0]

        # Verify symbol and source
        assert c.symbol == "XAUUSD"
        assert c.source == "twelve_data_xauusd"

        # Verify direct Decimal precision (not float conversion)
        assert c.open == Decimal("4350.12345")
        assert c.high == Decimal("4355.98765")
        assert c.low == Decimal("4348.00001")
        assert c.close == Decimal("4352.55555")

        # Verify UTC timezone
        assert c.timestamp_open.tzinfo == timezone.utc
        assert c.timestamp_open == datetime(2026, 8, 31, 10, 0, 0, tzinfo=timezone.utc)
        assert c.timestamp_close == datetime(2026, 8, 31, 10, 15, 0, tzinfo=timezone.utc)
        assert c.is_closed is True

        # Verify query params passed to requests.get
        _, kwargs = mock_get.call_args
        params = kwargs["params"]
        assert params["symbol"] == "XAU/USD"
        assert params["interval"] == "15min"
        assert params["timezone"] == "UTC"
        assert params["order"] == "ASC"

    @patch("apps.market_data.providers.twelve_data.requests.get")
    def test_daily_timestamp_utc_parsing(self, mock_get, provider):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "ok",
            "meta": {"symbol": "XAU/USD", "interval": "1day"},
            "values": [
                {
                    "datetime": "2026-08-31",
                    "open": "4300.00",
                    "high": "4360.00",
                    "low": "4290.00",
                    "close": "4350.00",
                }
            ],
        }
        mock_get.return_value = mock_response

        start = datetime(2026, 8, 30, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
        candles = provider.fetch_candles("XAUUSD", "1d", start, end)

        assert len(candles) == 1
        c = candles[0]
        assert c.timestamp_open == datetime(2026, 8, 31, 0, 0, 0, tzinfo=timezone.utc)
        assert c.timestamp_close == datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)


class TestTwelveDataHostileDatetimeContract:
    """Validate strict timezone-aware contract, rejection of naive/ambiguous dates, and start <= end."""

    def test_naive_start_rejected(self, provider):
        naive_start = datetime(2026, 9, 2, 10, 0, 0)
        aware_end = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
        with pytest.raises(ValueError, match="NAIVE_DATETIME_FORBIDDEN"):
            provider.fetch_candles("XAUUSD", "15m", naive_start, aware_end)

    def test_naive_end_rejected(self, provider):
        aware_start = datetime(2026, 9, 2, 10, 0, 0, tzinfo=timezone.utc)
        naive_end = datetime(2026, 9, 2, 12, 0, 0)
        with pytest.raises(ValueError, match="NAIVE_DATETIME_FORBIDDEN"):
            provider.fetch_candles("XAUUSD", "15m", aware_start, naive_end)

    def test_ambiguous_tzinfo_without_utcoffset_rejected(self, provider):
        class AmbiguousTz(tzinfo):
            def utcoffset(self, dt):
                return None
            def tzname(self, dt):
                return "Ambiguous"
            def dst(self, dt):
                return None

        ambiguous_dt = datetime(2026, 9, 2, 10, 0, 0, tzinfo=AmbiguousTz())
        aware_end = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
        with pytest.raises(ValueError, match="NAIVE_DATETIME_FORBIDDEN"):
            provider.fetch_candles("XAUUSD", "15m", ambiguous_dt, aware_end)

    def test_none_datetime_rejected(self, provider):
        aware_dt = datetime(2026, 9, 2, 10, 0, 0, tzinfo=timezone.utc)
        with pytest.raises(ValueError, match="MISSING_DATETIME"):
            provider.fetch_candles("XAUUSD", "15m", None, aware_dt)
        with pytest.raises(ValueError, match="MISSING_DATETIME"):
            provider.fetch_candles("XAUUSD", "15m", aware_dt, None)

    @patch("apps.market_data.providers.twelve_data.requests.get")
    def test_timezone_aware_positive_offset_converted_to_utc(self, mock_get, provider):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "ok",
            "meta": {"symbol": "XAU/USD", "interval": "15min"},
            "values": [],
        }
        mock_get.return_value = mock_response

        # +07:00 timezone (e.g. Jakarta / Bangkok)
        tz_plus7 = timezone(timedelta(hours=7))
        start_plus7 = datetime(2026, 9, 2, 22, 0, 0, tzinfo=tz_plus7)  # 22:00 +07:00 == 15:00 UTC
        end_plus7 = datetime(2026, 9, 2, 23, 0, 0, tzinfo=tz_plus7)    # 23:00 +07:00 == 16:00 UTC

        provider.fetch_candles("XAUUSD", "15m", start_plus7, end_plus7)

        _, kwargs = mock_get.call_args
        params = kwargs["params"]
        assert params["start_date"] == "2026-09-02 15:00:00"
        assert params["end_date"] == "2026-09-02 16:00:00"
        assert params["timezone"] == "UTC"

    @patch("apps.market_data.providers.twelve_data.requests.get")
    def test_timezone_aware_negative_offset_converted_to_utc(self, mock_get, provider):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "ok",
            "meta": {"symbol": "XAU/USD", "interval": "15min"},
            "values": [],
        }
        mock_get.return_value = mock_response

        # -05:00 timezone (e.g. US Eastern Standard Time)
        tz_minus5 = timezone(timedelta(hours=-5))
        start_minus5 = datetime(2026, 9, 2, 10, 0, 0, tzinfo=tz_minus5)  # 10:00 -05:00 == 15:00 UTC
        end_minus5 = datetime(2026, 9, 2, 11, 0, 0, tzinfo=tz_minus5)    # 11:00 -05:00 == 16:00 UTC

        provider.fetch_candles("XAUUSD", "15m", start_minus5, end_minus5)

        _, kwargs = mock_get.call_args
        params = kwargs["params"]
        assert params["start_date"] == "2026-09-02 15:00:00"
        assert params["end_date"] == "2026-09-02 16:00:00"
        assert params["timezone"] == "UTC"

    def test_start_greater_than_end_rejected(self, provider):
        start = datetime(2026, 9, 2, 15, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 9, 2, 14, 0, 0, tzinfo=timezone.utc)  # end < start
        with pytest.raises(ValueError, match="INVALID_BOUNDED_WINDOW"):
            provider.fetch_candles("XAUUSD", "15m", start, end)


class TestTwelveDataClosedCandleHandling:
    """Validate distinction between latest completed candle and in-progress candle."""

    @patch("apps.market_data.providers.twelve_data.requests.get")
    def test_in_progress_candle_discarded_when_only_closed_true(self, mock_get, provider):
        # Suppose now is 2026-09-02 12:08:00 UTC
        # Candle 12:00 -> 12:15 is in progress (close_time 12:15 > now)
        # Candle 11:45 -> 12:00 is completed (close_time 12:00 <= now)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "ok",
            "meta": {"symbol": "XAU/USD", "interval": "15min"},
            "values": [
                {
                    "datetime": "2026-09-02 11:45:00",
                    "open": "4350.00",
                    "high": "4355.00",
                    "low": "4348.00",
                    "close": "4352.00",
                },
                {
                    "datetime": "2026-09-02 12:00:00",
                    "open": "4352.00",
                    "high": "4358.00",
                    "low": "4351.00",
                    "close": "4357.00",
                },
            ],
        }
        mock_get.return_value = mock_response

        fixed_now = datetime(2026, 9, 2, 12, 8, 0, tzinfo=timezone.utc)
        with patch.object(provider, "_get_now_utc", return_value=fixed_now):
            # By default only_closed=True
            candles = provider.fetch_candles(
                "XAUUSD", "15m",
                start=datetime(2026, 9, 2, 11, 0, tzinfo=timezone.utc),
                end=datetime(2026, 9, 2, 13, 0, tzinfo=timezone.utc),
                only_closed=True,
            )

            # In-progress candle (12:00) MUST be filtered out
            assert len(candles) == 1
            assert candles[0].timestamp_open == datetime(2026, 9, 2, 11, 45, tzinfo=timezone.utc)
            assert candles[0].is_closed is True

            # If only_closed=False, both are returned and 12:00 is marked is_closed=False
            all_candles = provider.fetch_candles(
                "XAUUSD", "15m",
                start=datetime(2026, 9, 2, 11, 0, tzinfo=timezone.utc),
                end=datetime(2026, 9, 2, 13, 0, tzinfo=timezone.utc),
                only_closed=False,
            )
            assert len(all_candles) == 2
            assert all_candles[0].is_closed is True
            assert all_candles[1].is_closed is False


class TestTwelveDataOHLCValidation:
    """Validate strict OHLC geometry enforcement."""

    @pytest.mark.parametrize(
        ("o", "h", "l", "c"),
        [
            ("4350", "4340", "4330", "4335"),  # high < open
            ("4350", "4360", "4370", "4355"),  # low > high
            ("4350", "4360", "4355", "4352"),  # low > close
            ("-4350", "4360", "4340", "4355"), # negative open
            ("4350", "0", "4340", "4355"),     # zero high
        ],
    )
    @patch("apps.market_data.providers.twelve_data.requests.get")
    def test_corrupt_ohlc_raises_value_error(self, mock_get, provider, o, h, l, c):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "ok",
            "meta": {"symbol": "XAU/USD", "interval": "15min"},
            "values": [{"datetime": "2026-08-31 10:00:00", "open": o, "high": h, "low": l, "close": c}],
        }
        mock_get.return_value = mock_response

        with pytest.raises(ValueError, match="TWELVE_DATA_INVALID_OHLC"):
            provider.fetch_candles(
                "XAUUSD", "15m",
                start=datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc),
                end=datetime(2026, 8, 31, 11, 0, tzinfo=timezone.utc),
            )


class TestTwelveDataFailClosedAndErrorHandling:
    """Validate all fail-closed modes (missing key, timeout, 429, http failure, mismatches)."""

    def test_missing_api_key_fail_closed(self):
        with patch.dict("os.environ", {}, clear=True):
            p = TwelveDataProvider(api_key=None)
            assert p.is_configured() is False
            with pytest.raises(RuntimeError, match="TWELVE_DATA_API_KEY_NOT_CONFIGURED"):
                p.fetch_candles(
                    "XAUUSD", "15m",
                    start=datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc),
                    end=datetime(2026, 8, 31, 11, 0, tzinfo=timezone.utc),
                )
            health = p.health_check()
            assert health.status == "NOT_CONFIGURED"

    @patch("apps.market_data.providers.twelve_data.requests.get")
    def test_timeout_fail_closed(self, mock_get, provider):
        mock_get.side_effect = requests.exceptions.Timeout("Connection timed out")
        with pytest.raises(RuntimeError, match="TWELVE_DATA_TIMEOUT"):
            provider.fetch_candles(
                "XAUUSD", "15m",
                start=datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc),
                end=datetime(2026, 8, 31, 11, 0, tzinfo=timezone.utc),
            )

    @patch("apps.market_data.providers.twelve_data.requests.get")
    def test_http_429_fail_closed(self, mock_get, provider):
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_get.return_value = mock_response

        with pytest.raises(RuntimeError, match="TWELVE_DATA_RATE_LIMIT_EXCEEDED"):
            provider.fetch_candles(
                "XAUUSD", "15m",
                start=datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc),
                end=datetime(2026, 8, 31, 11, 0, tzinfo=timezone.utc),
            )

    @patch("apps.market_data.providers.twelve_data.requests.get")
    def test_http_error_fail_closed(self, mock_get, provider):
        mock_response = MagicMock()
        mock_response.status_code = 502
        mock_response.text = "Bad Gateway"
        mock_get.return_value = mock_response

        with pytest.raises(RuntimeError, match="TWELVE_DATA_HTTP_ERROR"):
            provider.fetch_candles(
                "XAUUSD", "15m",
                start=datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc),
                end=datetime(2026, 8, 31, 11, 0, tzinfo=timezone.utc),
            )

    @patch("apps.market_data.providers.twelve_data.requests.get")
    def test_provider_api_error_response_fail_closed(self, mock_get, provider):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "error",
            "message": "Invalid API key or permission denied",
        }
        mock_get.return_value = mock_response

        with pytest.raises(RuntimeError, match="TWELVE_DATA_API_ERROR"):
            provider.fetch_candles(
                "XAUUSD", "15m",
                start=datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc),
                end=datetime(2026, 8, 31, 11, 0, tzinfo=timezone.utc),
            )

    @patch("apps.market_data.providers.twelve_data.requests.get")
    def test_symbol_mismatch_fail_closed(self, mock_get, provider):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "ok",
            "meta": {"symbol": "EUR/USD", "interval": "15min"},
            "values": [{"datetime": "2026-08-31 10:00:00", "open": "1.1", "high": "1.2", "low": "1.0", "close": "1.15"}],
        }
        mock_get.return_value = mock_response

        with pytest.raises(ValueError, match="TWELVE_DATA_SYMBOL_MISMATCH"):
            provider.fetch_candles(
                "XAUUSD", "15m",
                start=datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc),
                end=datetime(2026, 8, 31, 11, 0, tzinfo=timezone.utc),
            )

    @patch("apps.market_data.providers.twelve_data.requests.get")
    def test_interval_mismatch_fail_closed(self, mock_get, provider):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "ok",
            "meta": {"symbol": "XAU/USD", "interval": "1min"},
            "values": [{"datetime": "2026-08-31 10:00:00", "open": "4350", "high": "4355", "low": "4348", "close": "4352"}],
        }
        mock_get.return_value = mock_response

        with pytest.raises(ValueError, match="TWELVE_DATA_INTERVAL_MISMATCH"):
            provider.fetch_candles(
                "XAUUSD", "15m",
                start=datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc),
                end=datetime(2026, 8, 31, 11, 0, tzinfo=timezone.utc),
            )


class TestTwelveDataVolumeAndExecutionSemantics:
    """Validate volume classification and absence of fabricated quotes."""

    @patch("apps.market_data.providers.twelve_data.requests.get")
    def test_volume_classified_as_unavailable(self, mock_get, provider):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "ok",
            "meta": {"symbol": "XAU/USD", "interval": "15min"},
            "values": [
                {
                    "datetime": "2026-08-31 10:00:00",
                    "open": "4350.00",
                    "high": "4355.00",
                    "low": "4348.00",
                    "close": "4352.00",
                }
            ],
        }
        mock_get.return_value = mock_response

        candles = provider.fetch_candles(
            "XAUUSD", "15m",
            start=datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc),
            end=datetime(2026, 8, 31, 11, 0, tzinfo=timezone.utc),
        )
        assert candles[0].volume_evidence == "UNAVAILABLE"
        assert candles[0].volume == Decimal("0")

    def test_ticker_returns_none_preventing_fabricated_execution_quotes(self, provider):
        assert provider.fetch_ticker("XAUUSD") is None
        assert provider.fetch_ticker("XAU/USD") is None


class TestTwelveDataSecretLeakage:
    """Verify secrets are masked and never exposed in exceptions or logs."""

    def test_secret_sanitizer(self):
        raw = "https://api.twelvedata.com/time_series?symbol=XAU/USD&apikey=super_secret_12345&interval=15min"
        sanitized = _sanitize_secret(raw, "super_secret_12345")
        assert "super_secret_12345" not in sanitized
        assert "***MASKED***" in sanitized

    @patch("apps.market_data.providers.twelve_data.requests.get")
    def test_secret_never_in_exception(self, mock_get, provider, mock_api_key):
        mock_get.side_effect = requests.exceptions.RequestException(
            f"Failed to connect to https://api.twelvedata.com?apikey={mock_api_key}"
        )
        with pytest.raises(RuntimeError) as exc_info:
            provider.fetch_candles(
                "XAUUSD", "15m",
                start=datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc),
                end=datetime(2026, 8, 31, 11, 0, tzinfo=timezone.utc),
            )
        assert mock_api_key not in str(exc_info.value)
        assert "***MASKED***" in str(exc_info.value)


class TestTwelveDataRegistryIntegration:
    """Verify provider is registered in central ProviderRegistry."""

    def test_twelve_data_in_registry(self):
        assert registry.has("twelve_data_xauusd")
        reg_provider = registry.get("twelve_data_xauusd")
        assert isinstance(reg_provider, TwelveDataProvider)
        assert reg_provider.provider_id == "twelve_data_xauusd"
