"""Unit tests for qualify_twelve_data_xauusd management command.

All tests are strictly mocked and run offline without network access.
"""
import io
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch
import pytest
from django.core.management import call_command
from apps.market_data.providers.base import ProviderHealth, RawCandle
from apps.market_data.models import VolumeEvidenceType
from apps.market_data.management.commands.qualify_twelve_data_xauusd import _get_persisted_xauusd_candle_count


def _make_dummy_candle(tf: str, open_dt: datetime, is_closed: bool = True) -> RawCandle:
    delta = timedelta(minutes=15)
    return RawCandle(
        symbol="XAU/USD",
        timeframe=tf,
        timestamp_open=open_dt,
        timestamp_close=open_dt + delta,
        open=Decimal("2500.00"),
        high=Decimal("2510.00"),
        low=Decimal("2495.00"),
        close=Decimal("2505.00"),
        volume=Decimal("0"),
        is_closed=is_closed,
        source="twelve_data_xauusd",
        volume_evidence=VolumeEvidenceType.UNAVAILABLE,
    )


@pytest.mark.django_db
class TestQualifyTwelveDataCommand:
    """Test suite verifying qualify_twelve_data_xauusd command semantics."""

    def test_offline_never_emits_primary_usable(self):
        out = io.StringIO()
        err = io.StringIO()
        call_command("qualify_twelve_data_xauusd", "--offline", stdout=out, stderr=err)
        output = out.getvalue()

        assert "STATUS: OFFLINE_CONTRACT_CHECK_ONLY" in output
        assert "TWELVE_DATA_XAUUSD_PRIMARY_USABLE" not in output
        assert "Analytical Candle Source: QUALIFICATION_PENDING" in output

    @patch("apps.market_data.management.commands.qualify_twelve_data_xauusd.TwelveDataProvider")
    def test_fast_probe_never_emits_primary_usable_and_emits_fast_probe_pass(self, mock_provider_cls):
        mock_provider = MagicMock()
        mock_provider.is_configured.return_value = True
        mock_provider.health_check.return_value = ProviderHealth(
            provider_id="twelve_data_xauusd", status="HEALTHY", latency_ms=120, checked_at=datetime.now(timezone.utc)
        )
        past_dt = datetime.now(timezone.utc) - timedelta(minutes=30)
        mock_provider.fetch_candles.return_value = [_make_dummy_candle("15m", past_dt, is_closed=True)]
        mock_provider_cls.return_value = mock_provider

        out = io.StringIO()
        err = io.StringIO()
        call_command("qualify_twelve_data_xauusd", stdout=out, stderr=err)
        output = out.getvalue()

        assert "FINAL STATUS: TWELVE_DATA_FAST_PROBE_PASS" in output
        assert "This does NOT constitute comprehensive provider qualification." in output
        assert "TWELVE_DATA_XAUUSD_PRIMARY_USABLE" not in output

    @patch("apps.market_data.management.commands.qualify_twelve_data_xauusd.TwelveDataProvider")
    def test_full_successful_qualification_emits_primary_usable(self, mock_provider_cls):
        mock_provider = MagicMock()
        mock_provider.is_configured.return_value = True
        mock_provider.health_check.return_value = ProviderHealth(
            provider_id="twelve_data_xauusd", status="HEALTHY", latency_ms=150, checked_at=datetime.now(timezone.utc)
        )
        mock_provider.TIMEFRAME_DELTAS = {
            "1m": timedelta(minutes=1),
            "5m": timedelta(minutes=5),
            "15m": timedelta(minutes=15),
            "1h": timedelta(hours=1),
            "4h": timedelta(hours=4),
            "1d": timedelta(days=1),
        }
        past_dt = datetime.now(timezone.utc) - timedelta(minutes=30)
        mock_provider.fetch_candles.return_value = [_make_dummy_candle("15m", past_dt, is_closed=True)]
        mock_provider_cls.return_value = mock_provider

        out = io.StringIO()
        err = io.StringIO()
        call_command("qualify_twelve_data_xauusd", "--full", "--pace-seconds", "0", stdout=out, stderr=err)
        output = out.getvalue()

        assert "FINAL STATUS: TWELVE_DATA_XAUUSD_PRIMARY_USABLE" in output
        assert "Analytical Candle Source: USABLE (Candles Only)" in output

    @patch("apps.market_data.management.commands.qualify_twelve_data_xauusd.TwelveDataProvider")
    def test_full_one_timeframe_failure_emits_limited(self, mock_provider_cls):
        mock_provider = MagicMock()
        mock_provider.is_configured.return_value = True
        mock_provider.health_check.return_value = ProviderHealth(
            provider_id="twelve_data_xauusd", status="HEALTHY", latency_ms=150, checked_at=datetime.now(timezone.utc)
        )
        mock_provider.TIMEFRAME_DELTAS = {
            "1m": timedelta(minutes=1),
            "5m": timedelta(minutes=5),
            "15m": timedelta(minutes=15),
            "1h": timedelta(hours=1),
            "4h": timedelta(hours=4),
            "1d": timedelta(days=1),
        }

        def mock_fetch(symbol, tf, start, end, only_closed=False):
            if tf == "4h":
                raise RuntimeError("Rate limited or timeframe unavailable")
            past_dt = datetime.now(timezone.utc) - timedelta(minutes=30)
            return [_make_dummy_candle(tf, past_dt, is_closed=True)]

        mock_provider.fetch_candles.side_effect = mock_fetch
        mock_provider_cls.return_value = mock_provider

        out = io.StringIO()
        err = io.StringIO()
        call_command("qualify_twelve_data_xauusd", "--full", "--pace-seconds", "0", stdout=out, stderr=err)
        output = out.getvalue()

        assert "FINAL STATUS: TWELVE_DATA_XAUUSD_PRIMARY_LIMITED" in output
        assert "TWELVE_DATA_XAUUSD_PRIMARY_USABLE" not in output

    @patch("apps.market_data.management.commands.qualify_twelve_data_xauusd.TwelveDataProvider")
    def test_future_timestamp_emits_timestamp_semantics_unresolved(self, mock_provider_cls):
        mock_provider = MagicMock()
        mock_provider.is_configured.return_value = True
        mock_provider.health_check.return_value = ProviderHealth(
            provider_id="twelve_data_xauusd", status="HEALTHY", latency_ms=150, checked_at=datetime.now(timezone.utc)
        )
        mock_provider.TIMEFRAME_DELTAS = {
            "1m": timedelta(minutes=1),
            "5m": timedelta(minutes=5),
            "15m": timedelta(minutes=15),
            "1h": timedelta(hours=1),
            "4h": timedelta(hours=4),
            "1d": timedelta(days=1),
        }

        # Return a candle with future open timestamp
        future_dt = datetime.now(timezone.utc) + timedelta(hours=2)
        mock_provider.fetch_candles.return_value = [_make_dummy_candle("15m", future_dt, is_closed=True)]
        mock_provider_cls.return_value = mock_provider

        out = io.StringIO()
        err = io.StringIO()
        call_command("qualify_twelve_data_xauusd", "--full", "--pace-seconds", "0", stdout=out, stderr=err)
        output = out.getvalue()

        assert "FINAL STATUS: TWELVE_DATA_TIMESTAMP_SEMANTICS_UNRESOLVED" in output
        assert "TWELVE_DATA_XAUUSD_PRIMARY_USABLE" not in output

    @patch("apps.market_data.management.commands.qualify_twelve_data_xauusd.TwelveDataProvider")
    def test_closed_15m_validation_failure_prevents_primary_usable(self, mock_provider_cls):
        mock_provider = MagicMock()
        mock_provider.is_configured.return_value = True
        mock_provider.health_check.return_value = ProviderHealth(
            provider_id="twelve_data_xauusd", status="HEALTHY", latency_ms=150, checked_at=datetime.now(timezone.utc)
        )
        mock_provider.TIMEFRAME_DELTAS = {
            "1m": timedelta(minutes=1),
            "5m": timedelta(minutes=5),
            "15m": timedelta(minutes=15),
            "1h": timedelta(hours=1),
            "4h": timedelta(hours=4),
            "1d": timedelta(days=1),
        }

        past_dt = datetime.now(timezone.utc) - timedelta(minutes=30)

        def mock_fetch(symbol, tf, start, end, only_closed=False):
            if only_closed:
                # Violate contract: return an in-progress candle flagged is_closed=False
                return [_make_dummy_candle("15m", past_dt, is_closed=False)]
            return [_make_dummy_candle(tf, past_dt, is_closed=True)]

        mock_provider.fetch_candles.side_effect = mock_fetch
        mock_provider_cls.return_value = mock_provider

        out = io.StringIO()
        err = io.StringIO()
        call_command("qualify_twelve_data_xauusd", "--full", "--pace-seconds", "0", stdout=out, stderr=err)
        output = out.getvalue()

        assert "FINAL STATUS: TWELVE_DATA_XAUUSD_PRIMARY_LIMITED" in output
        assert "Closed 15m validation failed" in output
        assert "TWELVE_DATA_XAUUSD_PRIMARY_USABLE" not in output

    def test_actual_persisted_candle_count_queried_not_hardcoded(self):
        with patch("apps.market_data.models.MarketCandle.objects.filter") as mock_filter:
            mock_filter.return_value.count.return_value = 1337
            count, count_str = _get_persisted_xauusd_candle_count()
            assert count == 1337
            assert count_str == "1337"

        # Check DB exception fallback
        with patch("apps.instruments.models.Instrument.get_canonical_xauusd", side_effect=RuntimeError("DB Down")):
            count, count_str = _get_persisted_xauusd_candle_count()
            assert count is None
            assert count_str == "PERSISTED_CANDLE_COUNT_UNAVAILABLE"

    @patch("apps.market_data.management.commands.qualify_twelve_data_xauusd.TwelveDataProvider")
    def test_missing_api_key_never_emits_usable(self, mock_provider_cls):
        mock_provider = MagicMock()
        mock_provider.is_configured.return_value = False
        mock_provider_cls.return_value = mock_provider

        out = io.StringIO()
        err = io.StringIO()
        call_command("qualify_twelve_data_xauusd", stdout=out, stderr=err)
        err_output = err.getvalue()
        out_output = out.getvalue()

        assert "FINAL STATUS: TWELVE_DATA_API_KEY_NOT_CONFIGURED" in err_output or "FINAL STATUS: TWELVE_DATA_API_KEY_NOT_CONFIGURED" in out_output
        assert "TWELVE_DATA_XAUUSD_PRIMARY_USABLE" not in out_output
        assert "TWELVE_DATA_FAST_PROBE_PASS" not in out_output
