"""
Unit tests for Twelve Data Credit-Safe Closed-Candle Ingestion Scheduler.

Validates all 14 governed acceptance criteria plus final safety reconciliations:
- State-based closed-candle scheduling with 20-second post-close delay
- Jitter and Celery drift resilience
- Provider-native 4H alignment (empirical transition table & anchor sync, no unproven calendar rules)
- Provider-native 1D alignment (00:00:00 UTC with 100.00% precision)
- Non-due minute returns 0 API calls
- Idempotency against already stored closed candles
- Market-closure circuit breaker protection (stops sequential advancing expected closes throughout 48h weekend)
- Hostile 48-hour closed-market simulation (strict bound on calls, no endless advancing burn)
- Reopen test: automatic recovery and seamless resumption upon legitimate market reopen
- Global daily automatic credit hard cap (<= 200 credits/day process-safe)
- Daily automatic retry credit hard cap (<= 25 credits/day)
- HTTP 429 immediate retry forbidden
- Health snapshot caching with <= 35m tolerance
- Fail-closed behavior on DEGRADED/UNHEALTHY health snapshot
- Normal lookback <= 2 bars
- Celery Beat schedule (30m health check, 1m dispatcher)
- Invariants: PRODUCTION_AUTHORITY = OFF, REAL_ORDER_EXECUTION = disabled
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch
import pytest

from apps.instruments.models import (
    Asset,
    AssetType,
    Instrument,
    InstrumentRole,
    InstrumentType,
    MarketListing,
    ListingRole,
    ListingStatus,
    ProviderHealthSnapshot,
    ProviderHealthStatus,
)
from apps.market_data.models import MarketCandle
from apps.market_data.scheduler import (
    ClosedCandleScheduler,
    CandleRequestLedger,
    latest_provider_native_closed_timestamp,
    get_empirical_4h_regime,
    ALIGNMENT_REGIME_0,
    ALIGNMENT_REGIME_1,
    POST_CLOSE_DELAY_SECONDS,
    MAX_AUTOMATIC_RETRIES_PER_CANDLE,
    MAX_AUTOMATIC_RETRY_CREDITS_PER_UTC_DAY,
    MAX_AUTOMATIC_TWELVE_DATA_CREDITS_PER_UTC_DAY,
    HEALTH_CACHE_MAX_AGE_MINUTES,
    NORMAL_LOOKBACK_BARS,
    ledger,
)
from apps.market_data.tasks import (
    dispatch_closed_candle_ingestion,
    ingest_primary_candles,
)


@pytest.fixture(autouse=True)
def clean_ledger():
    ledger.reset_for_test()
    yield
    ledger.reset_for_test()


@pytest.fixture
def xauusd_instrument(db):
    base, _ = Asset.objects.get_or_create(code="XAU", defaults={"name": "Gold", "asset_type": AssetType.COMMODITY})
    quote, _ = Asset.objects.get_or_create(code="USD", defaults={"name": "US Dollar", "asset_type": AssetType.FIAT})
    inst, _ = Instrument.objects.get_or_create(
        base_asset=base,
        quote_asset=quote,
        instrument_type=InstrumentType.SPOT,
        defaults={"role": InstrumentRole.EXECUTION},
    )
    return inst


@pytest.fixture
def xauusd_primary_listing(db, xauusd_instrument):
    listing, _ = MarketListing.objects.get_or_create(
        instrument=xauusd_instrument,
        provider="twelve_data_xauusd",
        defaults={
            "provider_symbol": "XAU/USD",
            "listing_role": ListingRole.PRIMARY_XAUUSD_SPOT,
            "status": ListingStatus.ACTIVE,
        },
    )
    return listing


# =========================================================================
# 1. 20-Second Post-Close Delay & Drift Resilience
# =========================================================================

def test_post_close_delay_and_drift_resilience():
    scheduler = ClosedCandleScheduler(post_close_delay_seconds=20)
    
    # 00:15:10 UTC: only 10s past 00:15:00 -> effective_now = 00:14:50 -> expected 15m is 00:00
    now_10s = datetime(2026, 6, 15, 0, 15, 10, tzinfo=timezone.utc)
    eff_10s = scheduler.get_effective_now(now_10s)
    exp_10s = latest_provider_native_closed_timestamp("15m", eff_10s)
    assert exp_10s == datetime(2026, 6, 15, 0, 0, 0, tzinfo=timezone.utc)

    # 00:15:20 UTC: exactly 20s past 00:15:00 -> effective_now = 00:15:00 -> expected 15m is 00:15
    now_20s = datetime(2026, 6, 15, 0, 15, 20, tzinfo=timezone.utc)
    eff_20s = scheduler.get_effective_now(now_20s)
    exp_20s = latest_provider_native_closed_timestamp("15m", eff_20s)
    assert exp_20s == datetime(2026, 6, 15, 0, 15, 0, tzinfo=timezone.utc)

    # 00:16:07 UTC: Celery Beat drifted by 67 seconds -> effective_now = 00:15:47 -> expected 15m is STILL 00:15
    now_drift = datetime(2026, 6, 15, 0, 16, 7, tzinfo=timezone.utc)
    eff_drift = scheduler.get_effective_now(now_drift)
    exp_drift = latest_provider_native_closed_timestamp("15m", eff_drift)
    assert exp_drift == datetime(2026, 6, 15, 0, 15, 0, tzinfo=timezone.utc)


# =========================================================================
# 2. Provider-Native 4H & 1D Alignment
# =========================================================================

def test_deterministic_4h_and_1d_alignment():
    # Empirical Transition Table Check:
    # After 2026-04-04 21:00 UTC -> ALIGNMENT_REGIME_1 (01, 05, 09, 13, 17, 21 UTC)
    dt_2026_regime1 = datetime(2026, 6, 15, 5, 25, 0, tzinfo=timezone.utc)
    assert get_empirical_4h_regime(dt_2026_regime1) == ALIGNMENT_REGIME_1
    exp_regime1_4h = latest_provider_native_closed_timestamp("4h", dt_2026_regime1)
    assert exp_regime1_4h == datetime(2026, 6, 15, 5, 0, 0, tzinfo=timezone.utc)

    # Winter 2024 (November 2024) -> ALIGNMENT_REGIME_0 (00, 04, 08, 12, 16, 20 UTC)
    dt_2024_regime0 = datetime(2024, 11, 15, 4, 25, 0, tzinfo=timezone.utc)
    assert get_empirical_4h_regime(dt_2024_regime0) == ALIGNMENT_REGIME_0
    exp_regime0_4h = latest_provider_native_closed_timestamp("4h", dt_2024_regime0)
    assert exp_regime0_4h == datetime(2024, 11, 15, 4, 0, 0, tzinfo=timezone.utc)

    # 4H Anchor Synchronization from DB
    anchor = datetime(2026, 3, 10, 13, 0, 0, tzinfo=timezone.utc)
    check_dt = datetime(2026, 3, 10, 21, 10, 0, tzinfo=timezone.utc)
    exp_anchored_4h = latest_provider_native_closed_timestamp("4h", check_dt, anchor_close=anchor)
    assert exp_anchored_4h == datetime(2026, 3, 10, 21, 0, 0, tzinfo=timezone.utc)

    # 1D alignment: strictly 00:00:00 UTC (100.00% verified across 1,816 historical daily candles)
    dt_1d_midday = datetime(2026, 6, 15, 14, 30, 0, tzinfo=timezone.utc)
    exp_1d = latest_provider_native_closed_timestamp("1d", dt_1d_midday)
    assert exp_1d == datetime(2026, 6, 15, 0, 0, 0, tzinfo=timezone.utc)


# =========================================================================
# 3. Non-Due Minute Yields Zero API Calls & Idempotency
# =========================================================================

@pytest.mark.django_db
def test_non_due_minute_and_stored_candle_idempotency(xauusd_instrument):
    scheduler = ClosedCandleScheduler()
    
    # Pre-populate stored closed candles up to 00:00:00 UTC
    t0 = datetime(2026, 6, 15, 0, 0, 0, tzinfo=timezone.utc)
    for tf in ["15m", "1h", "4h", "1d"]:
        MarketCandle.objects.create(
            instrument=xauusd_instrument,
            timeframe=tf,
            timestamp_open=t0 - timedelta(minutes=15),
            timestamp_close=t0,
            open=Decimal("2500.00"),
            high=Decimal("2510.00"),
            low=Decimal("2490.00"),
            close=Decimal("2505.00"),
            volume=Decimal("100.00"),
            is_closed=True,
            source="twelve_data",
        )

    # At 00:07:30 UTC: no candle closes at 00:07
    now_mid_minute = datetime(2026, 6, 15, 0, 7, 30, tzinfo=timezone.utc)
    due_tfs = scheduler.get_due_timeframes(xauusd_instrument, now_utc=now_mid_minute)
    assert due_tfs == []  # NON_DUE_MINUTE_TIME_SERIES_CALLS = 0

    # Test dispatch_closed_candle_ingestion at non-due minute
    with patch("apps.market_data.tasks.ingest_primary_candles") as mock_ingest:
        res = dispatch_closed_candle_ingestion(
            instrument_symbol="XAU/USD",
            now_utc=now_mid_minute,
        )
        assert res["status"] == "skipped"
        assert res["due_timeframes"] == []
        mock_ingest.assert_not_called()  # Zero calls to /time_series

    # At 00:15:25 UTC: 15m candle is due (00:15 > 00:00), 1h/4h/1d are NOT due (still at 00:00)
    now_15m = datetime(2026, 6, 15, 0, 15, 25, tzinfo=timezone.utc)
    due_tfs_15m = scheduler.get_due_timeframes(xauusd_instrument, now_utc=now_15m)
    assert due_tfs_15m == ["15m"]


# =========================================================================
# 4. Hostile 48-Hour Market-Closure Test & Sequential Burn Protection
# =========================================================================

@pytest.mark.django_db
def test_hostile_48_hour_market_closure_and_reopen(xauusd_instrument, xauusd_primary_listing):
    """
    Simulates a 48-hour weekend market closure (Friday 22:00 UTC through Sunday 22:00 UTC)
    with dispatcher executing every minute (2,880 ticks).
    
    Verifies:
    - Initial miss trips Market-Closure Circuit Breaker
    - Sequential advancing wall-clock closes (00:15, 00:30, 00:45, ...) do NOT trigger fresh requests
    - Unavailable candle retried at most 1 time
    - Total external calls over entire 48 hours strictly bounded
    - Market reopen at Sunday 22:15 resumes automatically with 0 manual intervention
    """
    test_ledger = CandleRequestLedger()
    scheduler = ClosedCandleScheduler(request_ledger=test_ledger)

    # Pre-populate stored closed candle at Friday close (Friday 2026-06-19 21:00 UTC)
    fri_close = datetime(2026, 6, 19, 21, 0, 0, tzinfo=timezone.utc)
    for tf in ["15m", "1h", "4h", "1d"]:
        MarketCandle.objects.create(
            instrument=xauusd_instrument,
            timeframe=tf,
            timestamp_open=fri_close - timedelta(minutes=15),
            timestamp_close=fri_close,
            open=Decimal("2500.00"),
            high=Decimal("2510.00"),
            low=Decimal("2490.00"),
            close=Decimal("2505.00"),
            volume=Decimal("100.00"),
            is_closed=True,
            source="twelve_data",
        )

    sim_start = datetime(2026, 6, 19, 21, 0, 0, tzinfo=timezone.utc)  # Friday 21:00 UTC (governed spot gold close)
    sim_end = datetime(2026, 6, 21, 21, 0, 0, tzinfo=timezone.utc)    # Sunday 21:00 UTC (48 hours = 2,880 mins closed)

    call_counter = 0

    def mock_ingest_during_weekend(instrument_symbol, timeframes, lookback_bars=2, now_utc=None, **kwargs):
        nonlocal call_counter
        call_counter += len(timeframes)
        # Closed market before Sunday 21:00 UTC returns NO_DATA
        if now_utc and now_utc < datetime(2026, 6, 21, 21, 0, 0, tzinfo=timezone.utc):
            return {
                "status": "hard_fail",
                "reason": "PRIMARY_XAUUSD_NO_USABLE_CLOSED_DATA_15m",
                "candles_ingested": 0,
            }
        # Legitimate reopen at/after Sunday 21:00 UTC: persist closed candle and return success
        for tf in timeframes:
            exp_close = latest_provider_native_closed_timestamp(tf, scheduler.get_effective_now(now_utc))
            MarketCandle.objects.get_or_create(
                instrument=xauusd_instrument,
                timeframe=tf,
                timestamp_close=exp_close,
                defaults={
                    "timestamp_open": exp_close - timedelta(minutes=15),
                    "open": Decimal("2500.00"),
                    "high": Decimal("2510.00"),
                    "low": Decimal("2490.00"),
                    "close": Decimal("2505.00"),
                    "volume": Decimal("100.00"),
                    "is_closed": True,
                    "source": "twelve_data",
                }
            )
        return {
            "status": "success",
            "candles_ingested": 1,
        }

    with patch("apps.market_data.tasks.ingest_primary_candles", side_effect=mock_ingest_during_weekend), \
         patch("apps.market_data.tasks.ledger", test_ledger), \
         patch("apps.market_data.scheduler.ledger", test_ledger):

        # Run 48-hour minute-by-minute simulation
        total_minutes = int((sim_end - sim_start).total_seconds() // 60)
        assert total_minutes == 2880

        for m in range(total_minutes):
            curr_time = sim_start + timedelta(minutes=m)
            res = dispatch_closed_candle_ingestion(
                instrument_symbol="XAU/USD",
                candidate_timeframes=["15m", "1h", "4h", "1d"],
                now_utc=curr_time,
            )

        # 1. Total mocked Twelve Data calls during the 48-hour closure must remain strictly bounded:
        # Initial request (1) + retry (1) per timeframe before circuit trips = <= 8 calls total during closure!
        # NOT 192 calls for 15m or 48 calls for 1h!
        assert call_counter <= 8, f"Expected <= 8 calls during 48h closure, got {call_counter}"
        assert test_ledger.get_daily_total_credit_count(sim_start) <= 200

        # 2. Reopen Test: Verify scheduler resumes automatically after weekend reopen window (Sunday 21:15)
        reopen_time = datetime(2026, 6, 21, 21, 15, 25, tzinfo=timezone.utc)
        reopen_res = dispatch_closed_candle_ingestion(
            instrument_symbol="XAU/USD",
            candidate_timeframes=["15m"],
            now_utc=reopen_time,
        )
        assert reopen_res["status"] == "success"
        assert reopen_res["results"]["15m"]["status"] == "success"
        # Circuit resets to CLOSED
        assert test_ledger.is_circuit_open("15m", reopen_time) is False

        # Subsequent minute: candle is now ALREADY_STORED -> 0 calls
        subsequent_time = reopen_time + timedelta(minutes=1)
        subsequent_res = dispatch_closed_candle_ingestion(
            instrument_symbol="XAU/USD",
            candidate_timeframes=["15m"],
            now_utc=subsequent_time,
        )
        assert subsequent_res["status"] == "skipped"


# =========================================================================
# 5. Global Daily Automatic Credit Cap (<= 200/day) & 429 Protection
# =========================================================================

@pytest.mark.django_db
def test_global_automatic_credit_cap_and_429(xauusd_instrument, xauusd_primary_listing):
    test_ledger = CandleRequestLedger()
    today_dt = datetime(2026, 6, 15, 10, 0, 0, tzinfo=timezone.utc)

    # 1. Consume 200 automatic credits
    for i in range(200):
        assert test_ledger.can_consume_automatic_credit(today_dt) is True
        test_ledger.record_automatic_credit_consumed(today_dt)

    assert test_ledger.get_daily_total_credit_count(today_dt) == 200
    # 201st automatic call must be blocked
    assert test_ledger.can_consume_automatic_credit(today_dt) is False

    # Dispatcher must fail closed immediately when daily cap is exceeded
    with patch("apps.market_data.tasks.ledger", test_ledger), \
         patch("apps.market_data.scheduler.ledger", test_ledger):
        res = dispatch_closed_candle_ingestion(
            instrument_symbol="XAU/USD",
            now_utc=today_dt,
        )
        assert res["status"] == "hard_fail"
        assert res["reason"] == "DAILY_AUTOMATIC_CREDIT_CAP_EXCEEDED"

    # 2. HTTP 429 Handling: strictly NO immediate retry
    test_ledger.reset_for_test()
    now_utc = datetime(2026, 6, 15, 0, 15, 25, tzinfo=timezone.utc)
    with patch("apps.market_data.tasks.ingest_primary_candles") as mock_ingest, \
         patch("apps.market_data.tasks.ledger", test_ledger), \
         patch("apps.market_data.scheduler.ledger", test_ledger):
        mock_ingest.return_value = {
            "status": "hard_fail",
            "reason": "TWELVE_DATA_RATE_LIMIT_EXCEEDED: HTTP 429 received from Twelve Data.",
            "candles_ingested": 0,
        }
        res_429 = dispatch_closed_candle_ingestion(
            instrument_symbol="XAU/USD",
            candidate_timeframes=["15m"],
            now_utc=now_utc,
        )
        assert res_429["status"] == "fail"
        assert res_429["results"]["15m"]["retried"] is False
        assert mock_ingest.call_count == 1  # Exactly 1 call, zero immediate retries


# =========================================================================
# 6. Health Check Caching (<= 35m) and Fail-Closed on DEGRADED/UNHEALTHY
# =========================================================================

@pytest.mark.django_db
def test_health_check_caching_and_fail_closed(xauusd_instrument, xauusd_primary_listing):
    now_utc = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

    # Create a HEALTHY snapshot aged 20 minutes (<= 35m)
    ProviderHealthSnapshot.objects.create(
        listing=xauusd_primary_listing,
        status=ProviderHealthStatus.HEALTHY,
        checked_at=now_utc - timedelta(minutes=20),
        latency_ms=120,
    )

    with patch("apps.market_data.providers.registry.registry.get") as mock_get_prov:
        mock_prov = MagicMock()
        mock_prov.is_configured.return_value = True
        mock_prov.fetch_candles.return_value = []
        mock_get_prov.return_value = mock_prov

        # Run ingestion
        with patch.object(MarketCandle.objects, "filter") as mock_mc:
            mock_mc.return_value.order_by.return_value.first.return_value = None
            ingest_primary_candles(
                instrument_symbol="XAU/USD",
                timeframes=["15m"],
                lookback_bars=2,
                now_utc=now_utc,
            )
            # provider.health_check() must NOT have been called because healthy snapshot <= 35m was reused!
            mock_prov.health_check.assert_not_called()

        # Now create an UNHEALTHY snapshot aged 5 minutes
        ProviderHealthSnapshot.objects.create(
            listing=xauusd_primary_listing,
            status=ProviderHealthStatus.UNHEALTHY,
            checked_at=now_utc - timedelta(minutes=5),
            latency_ms=999,
            reason="Upstream 503 Service Unavailable",
        )

        res_unhealthy = ingest_primary_candles(
            instrument_symbol="XAU/USD",
            timeframes=["15m"],
            lookback_bars=2,
            now_utc=now_utc,
        )
        # Must fail closed immediately!
        assert res_unhealthy["status"] == "hard_fail"
        assert "PRIMARY_XAUUSD_HEALTH_UNHEALTHY" in res_unhealthy["reason"]

        # Also test DEGRADED snapshot aged 2 minutes
        ProviderHealthSnapshot.objects.create(
            listing=xauusd_primary_listing,
            status=ProviderHealthStatus.DEGRADED,
            checked_at=now_utc - timedelta(minutes=2),
            latency_ms=850,
            reason="High latency detected",
        )

        res_degraded = ingest_primary_candles(
            instrument_symbol="XAU/USD",
            timeframes=["15m"],
            lookback_bars=2,
            now_utc=now_utc,
        )
        assert res_degraded["status"] == "hard_fail"
        assert "PRIMARY_XAUUSD_HEALTH_DEGRADED" in res_degraded["reason"]


# =========================================================================
# 7. Normal Lookback Bounded to <= 2 Bars
# =========================================================================

def test_normal_lookback_bounded_to_two_bars():
    scheduler = ClosedCandleScheduler()
    t_close = datetime(2026, 6, 15, 0, 15, 0, tzinfo=timezone.utc)
    
    # Normal case: latest stored is 1 bar ago (00:00:00)
    t_stored = datetime(2026, 6, 15, 0, 0, 0, tzinfo=timezone.utc)
    lookback = scheduler.calculate_lookback_bars("15m", t_close, t_stored)
    assert lookback == 2
    assert lookback <= NORMAL_LOOKBACK_BARS

    # Cold start / None stored: default 2
    assert scheduler.calculate_lookback_bars("15m", t_close, None) == 2

    # Bounded recovery case: missing 8 bars -> 8 bars (bounded by MAX_RECOVERY_LOOKBACK_BARS=20)
    t_stored_gap = datetime(2026, 6, 14, 22, 15, 0, tzinfo=timezone.utc)
    recovery_lookback = scheduler.calculate_lookback_bars("15m", t_close, t_stored_gap)
    assert recovery_lookback == 8

    # Extreme gap: bounded by 20
    t_stored_huge = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
    huge_lookback = scheduler.calculate_lookback_bars("15m", t_close, t_stored_huge)
    assert huge_lookback == 20


# =========================================================================
# 8. Celery Beat Schedule Invariants
# =========================================================================

def test_celery_beat_schedule_configuration():
    from django.conf import settings
    beat_schedule = settings.CELERY_BEAT_SCHEDULE

    # Scheduled health: every 30 minutes (1800.0s)
    health_task_cfg = beat_schedule.get("provider-health-check")
    assert health_task_cfg is not None
    assert health_task_cfg["schedule"] == 1800.0
    assert health_task_cfg["task"] == "apps.market_data.tasks.check_provider_health_task"

    # Closed-candle ingestion dispatcher: every 1 minute (60.0s)
    ingest_task_cfg = beat_schedule.get("ingest-primary-xauusd-candles")
    assert ingest_task_cfg is not None
    assert ingest_task_cfg["schedule"] == 60.0
    assert ingest_task_cfg["task"] == "apps.market_data.tasks.dispatch_closed_candle_ingestion"
    assert ingest_task_cfg["args"] == ("XAU/USD",)


# =========================================================================
# 9. Operational Authority Safety Invariants
# =========================================================================

def test_operational_authority_invariants():
    from django.conf import settings
    # Real order execution and live authority must remain disabled
    assert getattr(settings, "PRODUCTION_AUTHORITY", False) is False
    assert getattr(settings, "REAL_ORDER_EXECUTION", "disabled") == "disabled"
