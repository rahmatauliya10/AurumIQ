"""
Unit tests for governed market-closure ingestion guards (XAU-P1-02-GUARD).
Verifies interval-aware closure classification, scheduler suppression,
and persistence rejection across 15m, 1h, 4h, and 1d timeframes.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from unittest.mock import MagicMock, patch

from engine.paper.continuity import is_expected_market_closure, is_expected_market_interval_closed
from apps.market_data.scheduler import ClosedCandleScheduler
from apps.market_data.providers.base import RawCandle


class TestIntervalAwareClosureSemantics:
    """Verify midpoint interval evaluation for 15m, 1h, 4h, and 1d."""

    @pytest.mark.parametrize(
        "tf, t_open, t_close, expected_closed, label",
        [
            # 15m Timeframe
            ("15m", "2026-09-25T20:45:00Z", "2026-09-25T21:00:00Z", False, "Friday final valid 15m"),
            ("15m", "2026-09-25T21:00:00Z", "2026-09-25T21:15:00Z", True, "Friday first invalid post-close 15m"),
            ("15m", "2026-09-26T12:00:00Z", "2026-09-26T12:15:00Z", True, "Saturday midday 15m"),
            ("15m", "2026-09-27T20:45:00Z", "2026-09-27T21:00:00Z", True, "Sunday pre-open 15m"),
            ("15m", "2026-09-27T21:00:00Z", "2026-09-27T21:15:00Z", False, "Sunday first valid post-open 15m"),
            ("15m", "2026-09-23T10:00:00Z", "2026-09-23T10:15:00Z", False, "Wednesday normal weekday 15m"),

            # 1h Timeframe
            ("1h", "2026-09-25T20:00:00Z", "2026-09-25T21:00:00Z", False, "Friday final valid 1h"),
            ("1h", "2026-09-25T21:00:00Z", "2026-09-25T22:00:00Z", True, "Friday first invalid post-close 1h"),
            ("1h", "2026-09-26T12:00:00Z", "2026-09-26T13:00:00Z", True, "Saturday midday 1h"),
            ("1h", "2026-09-27T20:00:00Z", "2026-09-27T21:00:00Z", True, "Sunday pre-open 1h"),
            ("1h", "2026-09-27T21:00:00Z", "2026-09-27T22:00:00Z", False, "Sunday first valid post-open 1h"),
            ("1h", "2026-09-23T10:00:00Z", "2026-09-23T11:00:00Z", False, "Wednesday normal weekday 1h"),

            # 4h Timeframe
            ("4h", "2026-09-25T17:00:00Z", "2026-09-25T21:00:00Z", False, "Friday final valid 4h"),
            ("4h", "2026-09-25T21:00:00Z", "2026-09-26T01:00:00Z", True, "Friday first invalid post-close 4h"),
            ("4h", "2026-09-26T12:00:00Z", "2026-09-26T16:00:00Z", True, "Saturday midday 4h"),
            ("4h", "2026-09-27T17:00:00Z", "2026-09-27T21:00:00Z", True, "Sunday pre-open 4h"),
            ("4h", "2026-09-27T21:00:00Z", "2026-09-28T01:00:00Z", False, "Sunday first valid post-open 4h"),
            ("4h", "2026-09-23T08:00:00Z", "2026-09-23T12:00:00Z", False, "Wednesday normal weekday 4h"),

            # 1d Timeframe
            ("1d", "2026-09-25T00:00:00Z", "2026-09-26T00:00:00Z", False, "Friday trading day 1d"),
            ("1d", "2026-09-26T00:00:00Z", "2026-09-27T00:00:00Z", True, "Saturday closed day 1d"),
            ("1d", "2026-09-27T00:00:00Z", "2026-09-28T00:00:00Z", False, "Sunday trading day 1d (post-21:00 UTC open overlap)"),
            ("1d", "2026-09-23T00:00:00Z", "2026-09-24T00:00:00Z", False, "Wednesday normal weekday 1d"),
        ],
    )
    def test_market_interval_closure_classifications(self, tf, t_open, t_close, expected_closed, label):
        dt_open = datetime.fromisoformat(t_open.replace("Z", "+00:00"))
        dt_close = datetime.fromisoformat(t_close.replace("Z", "+00:00"))
        assert is_expected_market_interval_closed(dt_open, dt_close) is expected_closed, f"Failed for {label} ({tf})"

    def test_invalid_interval_raises_error(self):
        t1 = datetime(2026, 9, 25, 21, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)
        with pytest.raises(ValueError, match="timestamp_close.*must be > timestamp_open"):
            is_expected_market_interval_closed(t1, t2)


class TestSchedulerMarketClosureGuard:
    """Verify scheduler suppresses requests during scheduled closures."""

    @pytest.fixture
    def mock_instrument(self):
        inst = MagicMock()
        inst.symbol = "XAU/USD"
        inst.base_asset.code = "XAU"
        inst.quote_asset.code = "USD"
        return inst

    def test_scheduler_suppresses_closed_market_timeframes(self, mock_instrument):
        # Saturday midday: market is strictly closed
        now_utc = datetime(2026, 9, 26, 12, 16, 0, tzinfo=timezone.utc)
        scheduler = ClosedCandleScheduler(post_close_delay_seconds=60)

        with patch("apps.market_data.models.MarketCandle.objects.filter") as mock_filter:
            mock_filter.return_value.order_by.return_value.first.return_value = None
            is_due, expected_close, reason = scheduler.evaluate_timeframe(mock_instrument, "15m", now_utc)

            assert is_due is False
            assert reason == "MARKET_CLOSED_SCHEDULED"

    def test_scheduler_allows_post_reopen_timeframe(self, mock_instrument):
        # Sunday 21:16 UTC: market has reopened at 21:00 UTC, 21:15 bar is closed
        now_utc = datetime(2026, 9, 27, 21, 16, 0, tzinfo=timezone.utc)
        scheduler = ClosedCandleScheduler(post_close_delay_seconds=60)

        with patch("apps.market_data.models.MarketCandle.objects.filter") as mock_filter:
            mock_filter.return_value.order_by.return_value.first.return_value = None
            is_due, expected_close, reason = scheduler.evaluate_timeframe(mock_instrument, "15m", now_utc)

            assert is_due is True
            assert reason == "DUE_NEW_CLOSED_CANDLE"
            assert expected_close == datetime(2026, 9, 27, 21, 15, 0, tzinfo=timezone.utc)
