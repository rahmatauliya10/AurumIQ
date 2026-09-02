"""
Unit tests for Twelve Data Historical Backfill, Safety Hardening, and Offline Contracts.
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import MagicMock, patch
import pytest

from django.core.management import call_command
from django.core.management.base import CommandError

from apps.instruments.models import Instrument, MarketListing, ListingRole, ListingStatus
from apps.market_data.models import MarketCandle, CandleQualityFlag, VolumeEvidenceType
from apps.market_data.providers.base import RawCandle
from apps.market_data.providers.twelve_data import TwelveDataProvider
from apps.market_data.readiness import XauUsdDataReadinessEvaluator, parse_strict_iso_datetime


@pytest.fixture
def seeded_instruments(db):
    """Seed instruments and return canonical XAU/USD."""
    call_command("seed_instruments")
    return Instrument.get_canonical_xauusd()


@pytest.fixture
def mock_usage_telemetry(monkeypatch):
    """Default safe mocked usage telemetry for command executions."""
    monkeypatch.setattr(TwelveDataProvider, "get_api_usage", lambda self: {
        "daily_usage": 100,
        "plan_daily_limit": 800,
        "current_usage": 1,
        "plan_limit": 8,
        "plan_category": "basic",
    })


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
class TestStrictDatetimeContracts:
    """Validate Section 2: Strict ISO Datetime Contract and offset normalization."""

    def test_naive_start_rejected(self, seeded_instruments, mock_usage_telemetry, monkeypatch):
        monkeypatch.setattr(TwelveDataProvider, "is_configured", lambda self: True)
        with pytest.raises(CommandError) as exc:
            call_command(
                "backfill_xauusd_twelve_data",
                start="2020-04-07T00:00:00",
                end="2026-09-01T00:00:00Z",
                timeframes="1d",
            )
        assert "NAIVE_DATETIME_FORBIDDEN" in str(exc.value)

    def test_naive_end_rejected(self, seeded_instruments, mock_usage_telemetry, monkeypatch):
        monkeypatch.setattr(TwelveDataProvider, "is_configured", lambda self: True)
        with pytest.raises(CommandError) as exc:
            call_command(
                "backfill_xauusd_twelve_data",
                start="2020-04-07T00:00:00Z",
                end="2026-09-01 00:00:00",
                timeframes="1d",
            )
        assert "NAIVE_DATETIME_FORBIDDEN" in str(exc.value)

    def test_plus_07_offset_normalized_correctly(self):
        dt = parse_strict_iso_datetime("2020-04-07T07:00:00+07:00")
        assert dt == datetime(2020, 4, 7, 0, 0, 0, tzinfo=timezone.utc)
        assert dt.tzinfo == timezone.utc

    def test_minus_05_offset_normalized_correctly(self):
        dt = parse_strict_iso_datetime("2020-04-06T19:00:00-05:00")
        assert dt == datetime(2020, 4, 7, 0, 0, 0, tzinfo=timezone.utc)
        assert dt.tzinfo == timezone.utc

    def test_audit_command_rejects_naive_timestamps(self, seeded_instruments):
        with pytest.raises(CommandError) as exc:
            call_command(
                "audit_xauusd_readiness",
                expected_start="2020-04-07T00:00:00",
                expected_end="2026-09-01T00:00:00Z",
            )
        assert "NAIVE_DATETIME_FORBIDDEN" in str(exc.value)


@pytest.mark.django_db
class TestTwelveDataDailyQuotaGuard:
    """Validate Twelve Data daily API credit ceiling guard, fail-closed telemetry, and reserves."""

    def test_get_api_usage_success(self, monkeypatch):
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

    def test_get_api_usage_missing_daily_usage_fails(self, monkeypatch):
        import requests
        class MockResponse:
            status_code = 200
            def json(self):
                return {"plan_daily_limit": 800}
        monkeypatch.setattr(requests, "get", lambda *args, **kwargs: MockResponse())
        provider = TwelveDataProvider(api_key="test_key")
        with pytest.raises(RuntimeError) as exc:
            provider.get_api_usage()
        assert "TWELVE_DATA_API_USAGE_MALFORMED" in str(exc.value)

    def test_get_api_usage_missing_plan_daily_limit_fails(self, monkeypatch):
        import requests
        class MockResponse:
            status_code = 200
            def json(self):
                return {"daily_usage": 10}
        monkeypatch.setattr(requests, "get", lambda *args, **kwargs: MockResponse())
        provider = TwelveDataProvider(api_key="test_key")
        with pytest.raises(RuntimeError) as exc:
            provider.get_api_usage()
        assert "TWELVE_DATA_API_USAGE_MALFORMED" in str(exc.value)

    def test_get_api_usage_negative_daily_usage_fails(self, monkeypatch):
        import requests
        class MockResponse:
            status_code = 200
            def json(self):
                return {"daily_usage": -5, "plan_daily_limit": 800}
        monkeypatch.setattr(requests, "get", lambda *args, **kwargs: MockResponse())
        provider = TwelveDataProvider(api_key="test_key")
        with pytest.raises(RuntimeError) as exc:
            provider.get_api_usage()
        assert "TWELVE_DATA_API_USAGE_MALFORMED" in str(exc.value)

    def test_dry_run_api_usage_failure_halts_immediately(self, seeded_instruments, monkeypatch):
        """Section 5: Dry-run must respect quota; fail if api_usage telemetry fails."""
        monkeypatch.setattr(TwelveDataProvider, "is_configured", lambda self: True)
        monkeypatch.setattr(TwelveDataProvider, "get_api_usage", MagicMock(side_effect=RuntimeError("telemetry_down")))
        fetch_mock = MagicMock()
        monkeypatch.setattr(TwelveDataProvider, "fetch_historical_page", fetch_mock)

        with pytest.raises(CommandError) as exc:
            call_command(
                "backfill_xauusd_twelve_data",
                start="2026-08-20T00:00:00Z",
                end="2026-08-31T00:00:00Z",
                timeframes="1d",
                dry_run=True,
            )
        assert "DAILY_CREDIT_GUARD_CHECK_FAILED" in str(exc.value)
        assert fetch_mock.call_count == 0

    def test_safe_ceiling_formula_respects_provider_plan_limit(self, seeded_instruments, monkeypatch):
        """Amendment 2: safe_ceiling = min(configured, provider_plan_daily_limit)."""
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
        # Plan daily limit is reduced to 500 (lower than default ceiling 700). Usage is 498. Reserve is 0.
        # available_historical_credits = 500 - 498 = 2.
        monkeypatch.setattr(TwelveDataProvider, "get_api_usage", lambda self: {
            "daily_usage": 498,
            "plan_daily_limit": 500,
            "current_usage": 0,
            "plan_limit": 8,
            "plan_category": "custom",
        })
        monkeypatch.setattr(TwelveDataProvider, "fetch_historical_page", mock_fetch)

        call_command(
            "backfill_xauusd_twelve_data",
            start="2026-08-01T00:00:00Z",
            end="2026-08-31T00:00:00Z",
            timeframes="1d",
            max_api_requests=10,
            daily_credit_ceiling=700,
            operational_credit_reserve=0,
            pace_seconds=0,
        )
        assert call_count == 2

    def test_periodic_usage_recheck_stops_campaign(self, seeded_instruments, monkeypatch):
        """Section 7: Periodic recheck detects exhaustion and halts."""
        check_count = 0
        def mock_usage(self):
            nonlocal check_count
            check_count += 1
            if check_count == 1:
                return {"daily_usage": 680, "plan_daily_limit": 800, "plan_category": "basic"}
            # On second check, usage jumped to 710 (exceeds safe ceiling 700)
            return {"daily_usage": 710, "plan_daily_limit": 800, "plan_category": "basic"}

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
        monkeypatch.setattr(TwelveDataProvider, "get_api_usage", mock_usage)
        monkeypatch.setattr(TwelveDataProvider, "fetch_historical_page", mock_fetch)

        out = StringIO()
        # Recheck every 1 attempt. First attempt runs, then at attempt 1 recheck triggers, sees usage=710, halts cleanly.
        call_command(
            "backfill_xauusd_twelve_data",
            start="2026-08-01T00:00:00Z",
            end="2026-08-31T00:00:00Z",
            timeframes="1d",
            max_api_requests=10,
            daily_credit_ceiling=700,
            operational_credit_reserve=10,
            usage_recheck_every=1,
            pace_seconds=0,
            stdout=out,
        )
        assert call_count == 1
        assert "TWELVE_DATA_DAILY_CREDIT_GUARD_REACHED" in out.getvalue()


@pytest.mark.django_db
class TestHttpAttemptAccountingAndRetries:
    """Validate true HTTP attempt accounting, retries, and 429 non-retryability."""

    def test_retry_attempts_counted_individually(self, seeded_instruments, mock_usage_telemetry, monkeypatch):
        """Section 3 & 12(I): Failed transient attempt counts against budget."""
        attempt = 0
        def mock_fetch(self, symbol, timeframe, end, outputsize=4900):
            nonlocal attempt
            attempt += 1
            if attempt == 1:
                raise RuntimeError("Connection timed out prematurely")
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
        monkeypatch.setattr(TwelveDataProvider, "fetch_historical_page", mock_fetch)
        monkeypatch.setattr("apps.market_data.management.commands.backfill_xauusd_twelve_data.time.sleep", lambda s: None)

        out = StringIO()
        call_command(
            "backfill_xauusd_twelve_data",
            start="2026-08-30T00:00:00Z",
            end="2026-08-31T00:00:00Z",
            timeframes="1d",
            max_api_requests=5,
            pace_seconds=0,
            stdout=out,
        )
        # Attempt 1 failed (transient retry), attempt 2 succeeded. Total HTTP attempts = 2.
        assert attempt == 2
        assert "Historical HTTP Attempts: 2" in out.getvalue()
        assert "Transient Retry Attempts: 1" in out.getvalue()
        assert "Logical Pages Completed: 1" in out.getvalue()

    def test_request_budget_blocks_retry_when_no_budget_remains(self, seeded_instruments, mock_usage_telemetry, monkeypatch):
        """Section 12(J): If budget is 1 and attempt 1 fails, attempt 2 is blocked."""
        attempt = 0
        def mock_fetch(self, symbol, timeframe, end, outputsize=4900):
            nonlocal attempt
            attempt += 1
            raise RuntimeError("Connection timed out")

        monkeypatch.setattr(TwelveDataProvider, "is_configured", lambda self: True)
        monkeypatch.setattr(TwelveDataProvider, "fetch_historical_page", mock_fetch)
        monkeypatch.setattr("apps.market_data.management.commands.backfill_xauusd_twelve_data.time.sleep", lambda s: None)

        out = StringIO()
        call_command(
            "backfill_xauusd_twelve_data",
            start="2026-08-30T00:00:00Z",
            end="2026-08-31T00:00:00Z",
            timeframes="1d",
            max_api_requests=1,
            pace_seconds=0,
            stdout=out,
        )
        assert attempt == 1
        assert "BACKFILL_REQUEST_BUDGET_REACHED" in out.getvalue()

    def test_429_rate_limit_fails_closed_without_retry(self, seeded_instruments, mock_usage_telemetry, monkeypatch):
        """Amendment 3: HTTP 429 is not treated as a transient retry; fails immediately."""
        attempt = 0
        def mock_fetch(self, symbol, timeframe, end, outputsize=4900):
            nonlocal attempt
            attempt += 1
            raise RuntimeError("TWELVE_DATA_RATE_LIMIT_EXCEEDED: HTTP 429 received from Twelve Data.")

        monkeypatch.setattr(TwelveDataProvider, "is_configured", lambda self: True)
        monkeypatch.setattr(TwelveDataProvider, "fetch_historical_page", mock_fetch)

        with pytest.raises(CommandError) as exc:
            call_command(
                "backfill_xauusd_twelve_data",
                start="2026-08-30T00:00:00Z",
                end="2026-08-31T00:00:00Z",
                timeframes="1d",
                max_api_requests=5,
                pace_seconds=0,
            )
        assert "TWELVE_DATA_RATE_LIMIT_EXCEEDED" in str(exc.value)
        # Never retried
        assert attempt == 1


@pytest.mark.django_db
class TestCompletionSemantics:
    """Validate Section 8, 9, 10: Rigid completion statuses and partial handling."""

    def test_partial_timeframe_cannot_emit_complete(self, seeded_instruments, mock_usage_telemetry, monkeypatch):
        """Section 8 & 12(L): PARTIAL timeframe cannot emit COMPLETE."""
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
        monkeypatch.setattr(TwelveDataProvider, "fetch_historical_page", mock_fetch)

        out = StringIO()
        # Budget 1 request for a 3-day window: cannot complete
        call_command(
            "backfill_xauusd_twelve_data",
            start="2026-08-20T00:00:00Z",
            end="2026-08-25T00:00:00Z",
            timeframes="1d",
            max_api_requests=1,
            pace_seconds=0,
            stdout=out,
        )
        assert "BACKFILL_REQUEST_BUDGET_REACHED" in out.getvalue()
        assert "PILOT_BACKFILL_SUCCESS" not in out.getvalue()
        assert "TWELVE_DATA_BACKFILL_COMPLETE" not in out.getvalue()

    def test_provider_boundary_before_target_sets_partial(self, seeded_instruments, mock_usage_telemetry, monkeypatch):
        """Section 9 & 12(M): Empty list before start -> PROVIDER_BOUNDARY_BEFORE_TARGET."""
        def mock_fetch(self, symbol, timeframe, end, outputsize=4900):
            return []  # No older candles returned

        monkeypatch.setattr(TwelveDataProvider, "is_configured", lambda self: True)
        monkeypatch.setattr(TwelveDataProvider, "fetch_historical_page", mock_fetch)

        out = StringIO()
        call_command(
            "backfill_xauusd_twelve_data",
            start="2026-08-01T00:00:00Z",
            end="2026-08-31T00:00:00Z",
            timeframes="1d",
            max_api_requests=5,
            pace_seconds=0,
            stdout=out,
        )
        assert "PROVIDER_BOUNDARY_BEFORE_TARGET" in out.getvalue()
        assert "TWELVE_DATA_BACKFILL_PARTIAL" in out.getvalue()

    def test_loop_guard_stopped_sets_partial(self, seeded_instruments, mock_usage_telemetry, monkeypatch):
        """Section 10 & 12(N): Non-monotonic backward step triggers LOOP_GUARD_STOPPED."""
        def mock_fetch(self, symbol, timeframe, end, outputsize=4900):
            delta = timedelta(days=1)
            # Returns candle with timestamp_open == end (not strictly earlier)
            return [
                RawCandle(
                    symbol="XAUUSD",
                    timeframe=timeframe,
                    timestamp_open=end,
                    timestamp_close=end + delta,
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
        monkeypatch.setattr(TwelveDataProvider, "fetch_historical_page", mock_fetch)

        out = StringIO()
        call_command(
            "backfill_xauusd_twelve_data",
            start="2026-08-01T00:00:00Z",
            end="2026-08-31T00:00:00Z",
            timeframes="1d",
            max_api_requests=5,
            pace_seconds=0,
            stdout=out,
        )
        assert "LOOP_GUARD_STOPPED" in out.getvalue()
        assert "TWELVE_DATA_BACKFILL_PARTIAL" in out.getvalue()

    def test_all_completed_emits_twelve_data_backfill_complete(self, seeded_instruments, mock_usage_telemetry, monkeypatch):
        """Section 8 & 12(O): All timeframes completed emits TWELVE_DATA_BACKFILL_COMPLETE."""
        def mock_fetch(self, symbol, timeframe, end, outputsize=4900):
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
        monkeypatch.setattr(TwelveDataProvider, "fetch_historical_page", mock_fetch)

        out = StringIO()
        call_command(
            "backfill_xauusd_twelve_data",
            start="2026-08-30T00:00:00Z",
            end="2026-08-31T00:00:00Z",
            timeframes="1d",
            max_api_requests=5,
            pace_seconds=0,
            stdout=out,
        )
        assert "FINAL STATUS: TWELVE_DATA_BACKFILL_COMPLETE" in out.getvalue()


@pytest.mark.django_db
class TestDefensivePersistenceContract:
    """Validate Section 11 & Amendments 1, 5: Defensive RawCandle persistence."""

    def test_non_closed_candle_rejected(self, seeded_instruments):
        inst = seeded_instruments
        from apps.market_data.management.commands.backfill_xauusd_twelve_data import Command
        cmd = Command()
        c = RawCandle(
            symbol="XAUUSD",
            timeframe="15m",
            timestamp_open=datetime(2026, 8, 30, 0, 0, tzinfo=timezone.utc),
            timestamp_close=datetime(2026, 8, 30, 0, 15, tzinfo=timezone.utc),
            open=Decimal("2500.00"),
            high=Decimal("2505.00"),
            low=Decimal("2495.00"),
            close=Decimal("2502.00"),
            volume=Decimal("0"),
            is_closed=False,  # Not closed!
            source="twelve_data_xauusd",
            volume_evidence="UNAVAILABLE",
        )
        with pytest.raises(CommandError) as exc:
            cmd._persist_candles(inst, "twelve_data_xauusd", "15m", [c])
        assert "not closed" in str(exc.value)

    def test_inverted_timestamps_rejected(self, seeded_instruments):
        """Amendment 1: timestamp_close > timestamp_open required."""
        inst = seeded_instruments
        from apps.market_data.management.commands.backfill_xauusd_twelve_data import Command
        cmd = Command()
        t = datetime(2026, 8, 30, 0, 0, tzinfo=timezone.utc)
        c = RawCandle(
            symbol="XAUUSD",
            timeframe="15m",
            timestamp_open=t,
            timestamp_close=t,  # Not strictly greater
            open=Decimal("2500.00"),
            high=Decimal("2505.00"),
            low=Decimal("2495.00"),
            close=Decimal("2502.00"),
            volume=Decimal("0"),
            is_closed=True,
            source="twelve_data_xauusd",
            volume_evidence="UNAVAILABLE",
        )
        with pytest.raises(CommandError) as exc:
            cmd._persist_candles(inst, "twelve_data_xauusd", "15m", [c])
        assert "timestamp_close" in str(exc.value)

    def test_bearish_candle_valid_geometry_accepted(self, seeded_instruments):
        """Amendment 1: close < open is valid for bearish candles."""
        inst = seeded_instruments
        from apps.market_data.management.commands.backfill_xauusd_twelve_data import Command
        cmd = Command()
        t_open = datetime(2026, 8, 30, 0, 0, tzinfo=timezone.utc)
        t_close = datetime(2026, 8, 30, 0, 15, tzinfo=timezone.utc)
        c = RawCandle(
            symbol="XAUUSD",
            timeframe="15m",
            timestamp_open=t_open,
            timestamp_close=t_close,
            open=Decimal("2505.00"),
            high=Decimal("2510.00"),
            low=Decimal("2490.00"),
            close=Decimal("2495.00"),  # Bearish: close < open
            volume=Decimal("0"),
            is_closed=True,
            source="twelve_data_xauusd",
            volume_evidence="UNAVAILABLE",
        )
        saved = cmd._persist_candles(inst, "twelve_data_xauusd", "15m", [c])
        assert saved == 1

    def test_invalid_ohlc_geometry_rejected(self, seeded_instruments):
        inst = seeded_instruments
        from apps.market_data.management.commands.backfill_xauusd_twelve_data import Command
        cmd = Command()
        t_open = datetime(2026, 8, 30, 0, 0, tzinfo=timezone.utc)
        t_close = datetime(2026, 8, 30, 0, 15, tzinfo=timezone.utc)
        c = RawCandle(
            symbol="XAUUSD",
            timeframe="15m",
            timestamp_open=t_open,
            timestamp_close=t_close,
            open=Decimal("2505.00"),
            high=Decimal("2500.00"),  # High < open: violation
            low=Decimal("2490.00"),
            close=Decimal("2495.00"),
            volume=Decimal("0"),
            is_closed=True,
            source="twelve_data_xauusd",
            volume_evidence="UNAVAILABLE",
        )
        with pytest.raises(CommandError) as exc:
            cmd._persist_candles(inst, "twelve_data_xauusd", "15m", [c])
        assert "Invalid OHLC geometry" in str(exc.value)

    def test_invalid_symbol_or_source_rejected(self, seeded_instruments):
        inst = seeded_instruments
        from apps.market_data.management.commands.backfill_xauusd_twelve_data import Command
        cmd = Command()
        t_open = datetime(2026, 8, 30, 0, 0, tzinfo=timezone.utc)
        t_close = datetime(2026, 8, 30, 0, 15, tzinfo=timezone.utc)
        c = RawCandle(
            symbol="EURUSD",  # Wrong symbol!
            timeframe="15m",
            timestamp_open=t_open,
            timestamp_close=t_close,
            open=Decimal("1.1000"),
            high=Decimal("1.1050"),
            low=Decimal("1.0950"),
            close=Decimal("1.1020"),
            volume=Decimal("0"),
            is_closed=True,
            source="twelve_data_xauusd",
            volume_evidence="UNAVAILABLE",
        )
        with pytest.raises(CommandError) as exc:
            cmd._persist_candles(inst, "twelve_data_xauusd", "15m", [c])
        assert "Invalid candle symbol" in str(exc.value)


@pytest.mark.django_db
class TestReadinessHistoricalCoverageContract:
    """Validate that pilot coverage blocks full calibration gate with HISTORICAL_COVERAGE_INCOMPLETE."""

    def test_pilot_coverage_blocks_full_calibration_gate(self, seeded_instruments):
        inst = seeded_instruments
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


class TestNetworkHostileIsolation:
    """Section 12(Q): Prove unit tests perform zero unmocked network requests."""

    def test_unmocked_network_call_is_strictly_blocked(self, monkeypatch):
        import requests
        called = False
        def mock_blocked(*args, **kwargs):
            nonlocal called
            called = True
            raise requests.exceptions.ConnectionError("REAL_NETWORK_ACCESS_BLOCKED_IN_UNIT_TESTS")

        monkeypatch.setattr(requests, "get", mock_blocked)
        provider = TwelveDataProvider(api_key="mock_key")
        with pytest.raises(RuntimeError) as exc:
            provider.get_api_usage()
        assert "TWELVE_DATA_API_USAGE_HTTP_FAILURE" in str(exc.value)
        assert called is True
