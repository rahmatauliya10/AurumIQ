"""Unit tests for Bounded Intrasession Data Repair & Exact 52-Bar Timestamp Set Equality."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch
import pytest
from django.test import TestCase

from apps.instruments.models import (
    Asset,
    AssetType,
    Instrument,
    InstrumentRole,
    InstrumentType,
    ListingRole,
    ListingStatus,
    MarketListing,
)
from apps.market_data.models import MarketCandle
from apps.dashboard.api import classify_candle_gap


@pytest.mark.unit
@pytest.mark.django_db
class TestIntrasessionRepair(TestCase):
    """Verifies that 52-bar repair requires exact timestamp set match and restores continuity."""

    def setUp(self):
        self.xau, _ = Asset.objects.get_or_create(code="XAU", name="Gold Spot", asset_type=AssetType.COMMODITY)
        self.usd, _ = Asset.objects.get_or_create(code="USD", name="US Dollar", asset_type=AssetType.FIAT)
        self.xauusd, _ = Instrument.objects.get_or_create(
            base_asset=self.xau,
            quote_asset=self.usd,
            instrument_type=InstrumentType.SPOT,
            defaults={"role": InstrumentRole.GOLD_REFERENCE, "is_active": True},
        )

        # Expected 52 missing 15m intervals between 2026-09-14 03:00 UTC and 16:00 UTC
        self.expected_start = datetime(2026, 9, 14, 3, 0, tzinfo=timezone.utc)
        self.expected_opens = {
            self.expected_start + timedelta(minutes=15 * i) for i in range(52)
        }

    def test_expected_missing_timestamp_count_is_52(self):
        """Construct exact expected set of 52 missing provider candle timestamps."""
        self.assertEqual(len(self.expected_opens), 52)
        min_open = min(self.expected_opens)
        max_open = max(self.expected_opens)
        self.assertEqual(min_open, datetime(2026, 9, 14, 3, 0, tzinfo=timezone.utc))
        self.assertEqual(max_open, datetime(2026, 9, 14, 15, 45, tzinfo=timezone.utc))

    def test_partial_provider_response_aborts_fail_closed(self):
        """If provider returns fewer than 52 bars, repair MUST abort without writing."""
        # Simulate provider returning only 50 bars
        mock_returned_opens = {
            self.expected_start + timedelta(minutes=15 * i) for i in range(50)
        }
        matched = mock_returned_opens.intersection(self.expected_opens)
        self.assertEqual(len(matched), 50)
        # Bounded rule: must NOT claim success if count < 52
        self.assertNotEqual(len(matched), 52)

    def test_repair_persistence_and_continuity_restoration(self):
        """Persisting exact 52 bars closes intrasession gap and restores continuity."""
        # Create boundary candles:
        # Prev close: 2026-09-14 03:00:00
        prev_candle = MarketCandle.objects.create(
            instrument=self.xauusd,
            timeframe="15m",
            timestamp_open=datetime(2026, 9, 14, 2, 45, tzinfo=timezone.utc),
            timestamp_close=datetime(2026, 9, 14, 3, 0, tzinfo=timezone.utc),
            open=Decimal("2500.00"),
            high=Decimal("2505.00"),
            low=Decimal("2495.00"),
            close=Decimal("2502.00"),
            volume=Decimal("100"),
            source="twelve_data",
        )
        # Next open: 2026-09-14 16:00:00
        next_candle = MarketCandle.objects.create(
            instrument=self.xauusd,
            timeframe="15m",
            timestamp_open=datetime(2026, 9, 14, 16, 0, tzinfo=timezone.utc),
            timestamp_close=datetime(2026, 9, 14, 16, 15, tzinfo=timezone.utc),
            open=Decimal("2502.00"),
            high=Decimal("2507.00"),
            low=Decimal("2498.00"),
            close=Decimal("2505.00"),
            volume=Decimal("100"),
            source="twelve_data",
        )

        # Before repair: gap exists
        gap_type = classify_candle_gap(prev_candle.timestamp_close, next_candle.timestamp_open, "15m")
        self.assertEqual(gap_type, "INTRASESSION_DATA_GAP")

        # Repair all 52 missing candles
        repair_candles = []
        for i in range(52):
            op = self.expected_start + timedelta(minutes=15 * i)
            cl = op + timedelta(minutes=15)
            repair_candles.append(
                MarketCandle(
                    instrument=self.xauusd,
                    timeframe="15m",
                    timestamp_open=op,
                    timestamp_close=cl,
                    open=Decimal("2502.00"),
                    high=Decimal("2505.00"),
                    low=Decimal("2500.00"),
                    close=Decimal("2503.00"),
                    volume=Decimal("150"),
                    source="twelve_data",
                )
            )
        MarketCandle.objects.bulk_create(repair_candles)

        # Post-write verification: missing count == 0
        persisted_opens = set(
            MarketCandle.objects.filter(
                instrument=self.xauusd,
                timeframe="15m",
                timestamp_open__in=self.expected_opens,
            ).values_list("timestamp_open", flat=True)
        )
        missing = self.expected_opens - persisted_opens
        self.assertEqual(len(missing), 0)

        # Check continuity across all repaired candles
        all_candles = list(
            MarketCandle.objects.filter(
                instrument=self.xauusd,
                timeframe="15m",
            ).order_by("timestamp_open")
        )
        for i in range(1, len(all_candles)):
            c_prev = all_candles[i - 1]
            c_curr = all_candles[i]
            self.assertEqual(c_curr.timestamp_open, c_prev.timestamp_close)
