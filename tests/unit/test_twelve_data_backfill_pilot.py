"""
Unit tests for Twelve Data Historical Backfill Pilot, Primary Binding, and Backward Paging.
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch
import pytest

from django.core.management import call_command
from django.core.management.base import CommandError

from apps.instruments.models import Instrument, MarketListing, ListingRole, ListingStatus
from apps.market_data.models import MarketCandle, CandleQualityFlag, VolumeEvidenceType
from apps.market_data.providers.base import RawCandle
from apps.market_data.providers.twelve_data import TwelveDataProvider
from apps.market_data.readiness import XauUsdDataReadinessEvaluator


@pytest.fixture
def seeded_instruments(db):
    """Seed instruments and return canonical XAU/USD."""
    call_command("seed_instruments")
    return Instrument.get_canonical_xauusd()


@pytest.mark.django_db
class TestTwelveDataAuthoritativeBinding:
    """Validate Twelve Data authoritative primary binding and topology invariants."""

    def test_authoritative_primary_is_twelve_data(self, seeded_instruments):
        inst = seeded_instruments
        active_primaries = inst.listings.filter(
            listing_role=ListingRole.PRIMARY_XAUUSD_SPOT,
            status=ListingStatus.ACTIVE,
        )
        assert active_primaries.count() == 1
        primary = active_primaries.first()
        assert primary.provider == "twelve_data_xauusd"
        assert primary.provider_symbol == "XAU/USD"
        assert primary.fallback_priority == 0

    def test_legacy_xauusd_primary_is_dormant_generic(self, seeded_instruments):
        inst = seeded_instruments
        legacy = inst.listings.filter(provider="xauusd_primary").first()
        assert legacy is not None
        assert legacy.listing_role == ListingRole.GENERIC
        assert legacy.status == ListingStatus.HALTED
        assert legacy.fallback_priority == 99

    def test_secondary_remains_xauusd_secondary(self, seeded_instruments):
        inst = seeded_instruments
        sec = inst.listings.filter(provider="xauusd_secondary").first()
        assert sec is not None
        assert sec.listing_role == ListingRole.SECONDARY_XAUUSD_SPOT
        assert sec.status == ListingStatus.ACTIVE

    def test_seed_instruments_is_strictly_idempotent(self, seeded_instruments):
        inst = seeded_instruments
        # Run seed a second and third time
        call_command("seed_instruments")
        call_command("seed_instruments")

        active_primaries = inst.listings.filter(
            listing_role=ListingRole.PRIMARY_XAUUSD_SPOT,
            status=ListingStatus.ACTIVE,
        )
        assert active_primaries.count() == 1
        assert active_primaries.first().provider == "twelve_data_xauusd"


@pytest.mark.django_db
class TestTwelveDataFetchHistoricalPage:
    """Validate fetch_historical_page contract and limits."""

    def test_invalid_outputsize_rejected(self):
        provider = TwelveDataProvider(api_key="mock_key")
        with pytest.raises(ValueError) as exc:
            provider.fetch_historical_page("XAU/USD", "15m", datetime.now(timezone.utc), outputsize=5001)
        assert "INVALID_OUTPUTSIZE" in str(exc.value)

        with pytest.raises(ValueError) as exc:
            provider.fetch_historical_page("XAU/USD", "15m", datetime.now(timezone.utc), outputsize=0)
        assert "INVALID_OUTPUTSIZE" in str(exc.value)

    def test_unconfigured_provider_fails_closed(self, monkeypatch):
        monkeypatch.delenv("TWELVE_DATA_API_KEY", raising=False)
        provider = TwelveDataProvider(api_key="")
        with pytest.raises(RuntimeError) as exc:
            provider.fetch_historical_page("XAU/USD", "15m", datetime.now(timezone.utc))
        assert "TWELVE_DATA_API_KEY_NOT_CONFIGURED" in str(exc.value)

    @patch("apps.market_data.providers.twelve_data.requests.get")
    def test_fetch_historical_page_order_and_closed_candles(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "meta": {"symbol": "XAU/USD", "interval": "15min"},
            "values": [
                {"datetime": "2026-08-31 23:30:00", "open": "2500.0", "high": "2505.0", "low": "2498.0", "close": "2502.0"},
                {"datetime": "2026-08-31 23:45:00", "open": "2502.0", "high": "2508.0", "low": "2501.0", "close": "2506.0"},
            ],
            "status": "ok",
        }
        mock_get.return_value = mock_resp

        provider = TwelveDataProvider(api_key="mock_key")
        end_dt = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
        candles = provider.fetch_historical_page("XAU/USD", "15m", end=end_dt, outputsize=10)

        assert len(candles) == 2
        assert candles[0].timestamp_open < candles[1].timestamp_open
        assert candles[0].open == Decimal("2500.0")
        assert candles[1].close == Decimal("2506.0")


@pytest.mark.django_db
class TestBackfillCommandPaginationAndBudget:
    """Validate backfill command request budget, monotonicity, and resumability."""

    def test_budget_stop_respected(self, seeded_instruments, monkeypatch):
        inst = seeded_instruments

        # Mock fetch_historical_page to return 5 synthetic candles each time
        call_count = 0
        def mock_fetch_page(self, symbol, timeframe, end, outputsize=4900):
            nonlocal call_count
            call_count += 1
            # Return candles stepped backward
            delta = timedelta(minutes=15)
            batch = []
            for i in range(5):
                t_open = end - (5 - i) * delta
                batch.append(
                    RawCandle(
                        symbol="XAUUSD",
                        timeframe=timeframe,
                        timestamp_open=t_open,
                        timestamp_close=t_open + delta,
                        open=Decimal("2500.00"),
                        high=Decimal("2505.00"),
                        low=Decimal("2495.00"),
                        close=Decimal("2502.00"),
                        volume=Decimal("0"),
                        is_closed=True,
                        source="twelve_data_xauusd",
                        volume_evidence="UNAVAILABLE",
                    )
                )
            return batch

        from apps.market_data.providers.twelve_data import TwelveDataProvider
        monkeypatch.setattr(TwelveDataProvider, "is_configured", lambda self: True)
        monkeypatch.setattr(TwelveDataProvider, "fetch_historical_page", mock_fetch_page)

        # Execute with budget of 3 requests
        call_command(
            "backfill_xauusd_twelve_data",
            start="2026-06-01T00:00:00Z",
            end="2026-09-01T00:00:00Z",
            timeframes="15m",
            max_api_requests=3,
            pace_seconds=0,
        )

        assert call_count == 3
        # In database, candles are saved
        saved = MarketCandle.objects.filter(instrument=inst, source="twelve_data_xauusd", timeframe="15m").count()
        assert saved > 0

    def test_idempotent_backfill_rerun(self, seeded_instruments, monkeypatch):
        inst = seeded_instruments

        def mock_fetch_page(self, symbol, timeframe, end, outputsize=4900):
            delta = timedelta(days=1)
            t_open = datetime(2026, 8, 30, 0, 0, tzinfo=timezone.utc)
            return [
                RawCandle(
                    symbol="XAUUSD",
                    timeframe=timeframe,
                    timestamp_open=t_open,
                    timestamp_close=t_open + delta,
                    open=Decimal("2500.00"),
                    high=Decimal("2505.00"),
                    low=Decimal("2495.00"),
                    close=Decimal("2502.00"),
                    volume=Decimal("0"),
                    is_closed=True,
                    source="twelve_data_xauusd",
                    volume_evidence="UNAVAILABLE",
                )
            ]

        from apps.market_data.providers.twelve_data import TwelveDataProvider
        monkeypatch.setattr(TwelveDataProvider, "is_configured", lambda self: True)
        monkeypatch.setattr(TwelveDataProvider, "fetch_historical_page", mock_fetch_page)

        # Run first time
        call_command(
            "backfill_xauusd_twelve_data",
            start="2026-08-29T00:00:00Z",
            end="2026-08-31T00:00:00Z",
            timeframes="1d",
            max_api_requests=1,
            pace_seconds=0,
        )
        count_1 = MarketCandle.objects.filter(instrument=inst, source="twelve_data_xauusd", timeframe="1d").count()

        # Run second time without changes
        call_command(
            "backfill_xauusd_twelve_data",
            start="2026-08-29T00:00:00Z",
            end="2026-08-31T00:00:00Z",
            timeframes="1d",
            max_api_requests=1,
            pace_seconds=0,
        )
        count_2 = MarketCandle.objects.filter(instrument=inst, source="twelve_data_xauusd", timeframe="1d").count()

        assert count_1 == count_2 == 1


@pytest.mark.django_db
class TestReadinessHistoricalCoverageContract:
    """Validate that 90-day pilot leaves full calibration gate blocked with HISTORICAL_COVERAGE_INCOMPLETE."""

    def test_pilot_coverage_blocks_full_calibration_gate(self, seeded_instruments):
        inst = seeded_instruments
        # Create 30 15m candles in August 2026 (satisfying warm-up bars, but NOT full coverage back to 2020-04-07)
        base = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
        delta = timedelta(minutes=15)
        for i in range(30):
            t_open = base + i * delta
            MarketCandle.objects.create(
                instrument=inst,
                source="twelve_data_xauusd",
                timeframe="15m",
                timestamp_open=t_open,
                timestamp_close=t_open + delta,
                open=Decimal("2500.00"),
                high=Decimal("2505.00"),
                low=Decimal("2495.00"),
                close=Decimal("2502.00"),
                volume=Decimal("0"),
                volume_evidence=VolumeEvidenceType.UNAVAILABLE,
                quote_rate=Decimal("1.000000"),
                close_usd=Decimal("2502.00"),
                is_closed=True,
                data_quality_flag=CandleQualityFlag.OK,
            )

        # Audit with full intended historical coverage bounds (2020-04-07 to 2026-09-01)
        report = XauUsdDataReadinessEvaluator.evaluate(
            instrument=inst,
            expected_coverage_start=datetime(2020, 4, 7, 0, 0, tzinfo=timezone.utc),
            expected_coverage_end=datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc),
        )

        assert report.candle_gate_passed is True
        assert report.coverage_complete is False
        assert report.decision == "CALIBRATION_DATA_NOT_READY"
        assert report.passed is False
        assert any("HISTORICAL_COVERAGE_INCOMPLETE" in r for r in report.reasons)
        assert report.primary_provider == "twelve_data_xauusd"
        assert report.primary_symbol == "XAU/USD"


@pytest.mark.django_db
class TestTwelveDataDailyQuotaGuard:
    """Validate Twelve Data daily API credit ceiling guard across process resumes."""

    def test_get_api_usage_success(self, monkeypatch):
        from apps.market_data.providers.twelve_data import TwelveDataProvider
        import requests

        class MockResponse:
            status_code = 200
            def json(self):
                return {
                    "timestamp": "2026-09-02 17:09:51",
                    "current_usage": 1,
                    "plan_limit": 8,
                    "daily_usage": 115,
                    "plan_daily_limit": 800,
                    "plan_category": "basic",
                }

        monkeypatch.setattr(requests, "get", lambda *args, **kwargs: MockResponse())
        provider = TwelveDataProvider(api_key="test_key")
        usage = provider.get_api_usage()

        assert usage["daily_usage"] == 115
        assert usage["plan_daily_limit"] == 800
        assert usage["plan_category"] == "basic"

    def test_get_api_usage_429_rate_limit(self, monkeypatch):
        from apps.market_data.providers.twelve_data import TwelveDataProvider
        import requests

        class MockResponse:
            status_code = 429
            text = "Too many requests"

        monkeypatch.setattr(requests, "get", lambda *args, **kwargs: MockResponse())
        provider = TwelveDataProvider(api_key="test_key")

        with pytest.raises(RuntimeError) as exc_info:
            provider.get_api_usage()
        assert "TWELVE_DATA_RATE_LIMITED" in str(exc_info.value)

    def test_backfill_daily_credit_guard_halts_cleanly_when_ceiling_reached(self, seeded_instruments, monkeypatch):
        inst = seeded_instruments
        from apps.market_data.providers.twelve_data import TwelveDataProvider

        fetch_called = False
        def mock_fetch(self, symbol, timeframe, end, outputsize=4900):
            nonlocal fetch_called
            fetch_called = True
            return []

        monkeypatch.setattr(TwelveDataProvider, "is_configured", lambda self: True)
        monkeypatch.setattr(TwelveDataProvider, "get_api_usage", lambda self: {
            "daily_usage": 720,
            "plan_daily_limit": 800,
            "current_usage": 0,
            "plan_limit": 8,
            "plan_category": "basic",
        })
        monkeypatch.setattr(TwelveDataProvider, "fetch_historical_page", mock_fetch)

        # Call with ceiling 700. Usage is 720. Guard should trigger and halt cleanly with 0 historical calls.
        call_command(
            "backfill_xauusd_twelve_data",
            start="2026-08-20T00:00:00Z",
            end="2026-08-31T00:00:00Z",
            timeframes="1d",
            max_api_requests=10,
            daily_credit_ceiling=700,
            pace_seconds=0,
        )

        assert fetch_called is False

    def test_backfill_daily_credit_guard_caps_effective_budget(self, seeded_instruments, monkeypatch):
        inst = seeded_instruments
        from apps.market_data.providers.twelve_data import TwelveDataProvider

        call_count = 0
        def mock_fetch(self, symbol, timeframe, end, outputsize=4900):
            nonlocal call_count
            call_count += 1
            delta = timedelta(days=1)
            t_open = end - delta
            return [
                RawCandle(
                    symbol="XAUUSD",
                    timeframe=timeframe,
                    timestamp_open=t_open,
                    timestamp_close=end,
                    open=Decimal("2500.00"),
                    high=Decimal("2505.00"),
                    low=Decimal("2495.00"),
                    close=Decimal("2502.00"),
                    volume=Decimal("0"),
                    is_closed=True,
                    source="twelve_data_xauusd",
                    volume_evidence="UNAVAILABLE",
                )
            ]

        monkeypatch.setattr(TwelveDataProvider, "is_configured", lambda self: True)
        # Usage is 698. Ceiling is 700. Remaining allowance is 2 credits.
        monkeypatch.setattr(TwelveDataProvider, "get_api_usage", lambda self: {
            "daily_usage": 698,
            "plan_daily_limit": 800,
            "current_usage": 0,
            "plan_limit": 8,
            "plan_category": "basic",
        })
        monkeypatch.setattr(TwelveDataProvider, "fetch_historical_page", mock_fetch)

        # Even though max_api_requests is 10, effective budget should be capped at 2.
        call_command(
            "backfill_xauusd_twelve_data",
            start="2026-08-01T00:00:00Z",
            end="2026-08-31T00:00:00Z",
            timeframes="1d",
            max_api_requests=10,
            daily_credit_ceiling=700,
            pace_seconds=0,
        )

        assert call_count == 2

