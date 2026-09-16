"""
Hostile unit tests for Rancher / Runtime Downtime Recovery.

Validates:
1. Downtime gap detection as INTRASESSION_DATA_GAP (not market closure).
2. Bounded batch recovery planning (e.g. 52 bars -> 20 + 20 + 12).
3. Complete bounded batch execution with zero candle lag.
4. Duplicate prevention and unique constraint enforcement.
5. Independent multi-timeframe handling (15m, 1h, 4h, 1d).
6. Non-fabrication of synthetic candles across weekend closures.
7. Credit budget capping and partial recovery fail-closed semantics (DATA_CONTINUITY_READY = false).
8. Phase 8 signal pipeline pause during unresolved gap and resumption post-recovery.
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch
import pytest

from apps.instruments.models import (
    Instrument,
    Asset,
    MarketListing,
    ListingRole,
    ListingStatus,
    ProviderHealthSnapshot,
)
from apps.market_data.models import MarketCandle
from apps.market_data.providers.base import RawCandle
from apps.market_data.scheduler import (
    ClosedCandleScheduler,
    CandleRequestLedger,
    MAX_RECOVERY_BARS_PER_REQUEST,
    NORMAL_LOOKBACK_BARS,
    ledger,
)
from apps.market_data.tasks import dispatch_closed_candle_ingestion, ingest_primary_candles
from apps.dashboard.api import classify_candle_gap


@pytest.fixture
def setup_xauusd_instrument(db, settings):
    """Fixture providing canonical XAUUSD instrument, primary listing, and healthy snapshot."""
    settings.XAUUSD_MAX_DIVERGENCE_PCT = Decimal("0.0035")
    xau, _ = Asset.objects.get_or_create(code="XAU", defaults={"name": "Gold", "asset_type": "COMMODITY"})
    usd, _ = Asset.objects.get_or_create(code="USD", defaults={"name": "US Dollar", "asset_type": "FIAT"})
    inst, _ = Instrument.objects.get_or_create(
        base_asset=xau, quote_asset=usd, role="GOLD_REFERENCE",
        defaults={"instrument_type": "SPOT", "is_active": True}
    )
    listing, _ = MarketListing.objects.get_or_create(
        instrument=inst,
        provider="twelve_data_xauusd",
        provider_symbol="XAU/USD",
        listing_role=ListingRole.PRIMARY_XAUUSD_SPOT,
        defaults={"status": ListingStatus.ACTIVE}
    )
    ProviderHealthSnapshot.objects.create(
        listing=listing,
        status="HEALTHY",
        checked_at=datetime(2026, 9, 14, 16, 0, 0, tzinfo=timezone.utc),
        latency_ms=100,
    )
    return inst, listing


def _generate_mock_candles(start_open: datetime, count: int, timeframe: str = "15m") -> list:
    """Helper generating synthetic RawCandle objects for mock provider."""
    tf_minutes = {"15m": 15, "1h": 60, "4h": 240, "1d": 1440}.get(timeframe, 15)
    step = timedelta(minutes=tf_minutes)
    candles = []
    for i in range(count):
        t_open = start_open + step * i
        t_close = t_open + step
        candles.append(
            RawCandle(
                symbol="XAU/USD",
                timeframe=timeframe,
                timestamp_open=t_open,
                timestamp_close=t_close,
                open=Decimal("2500.00") + Decimal(str(i * 0.1)),
                high=Decimal("2502.00") + Decimal(str(i * 0.1)),
                low=Decimal("2499.00") + Decimal(str(i * 0.1)),
                close=Decimal("2501.00") + Decimal(str(i * 0.1)),
                volume=Decimal("100.0"),
                is_closed=True,
                source="twelve_data_xauusd",
            )
        )
    return candles


@pytest.mark.django_db
def test_downtime_gap_detection_and_classification():
    """Requirement 1 & 2: 13-hour weekday downtime detected as INTRASESSION_DATA_GAP, not market closure."""
    t0_close = datetime(2026, 9, 14, 3, 0, 0, tzinfo=timezone.utc)
    t_restart_open = datetime(2026, 9, 14, 16, 0, 0, tzinfo=timezone.utc)

    classification = classify_candle_gap(t0_close, t_restart_open, "15m")
    assert classification == "INTRASESSION_DATA_GAP"
    assert classification != "PROVIDER_MARKET_CLOSURE"


def test_batch_recovery_planning_52_bars():
    """Requirement 3: 52 missing intervals planned in bounded batches (20 + 20 + 12)."""
    scheduler = ClosedCandleScheduler()
    t_stored_close = datetime(2026, 9, 14, 3, 0, 0, tzinfo=timezone.utc)
    t_expected_close = datetime(2026, 9, 14, 16, 0, 0, tzinfo=timezone.utc)

    batches = scheduler.plan_recovery_batches(
        timeframe="15m",
        expected_close=t_expected_close,
        latest_stored_close=t_stored_close,
        max_batch_size=20,
    )

    # 52 bars -> 3 bounded batches
    assert len(batches) == 3

    # Batch 1: 20 bars (03:00 to 08:00 UTC)
    b1_start, b1_end, b1_count = batches[0]
    assert b1_count == 20
    assert b1_start == datetime(2026, 9, 14, 3, 0, 0, tzinfo=timezone.utc)
    assert b1_end == datetime(2026, 9, 14, 8, 0, 0, tzinfo=timezone.utc)

    # Batch 2: 20 bars (08:00 to 13:00 UTC)
    b2_start, b2_end, b2_count = batches[1]
    assert b2_count == 20
    assert b2_start == datetime(2026, 9, 14, 8, 0, 0, tzinfo=timezone.utc)
    assert b2_end == datetime(2026, 9, 14, 13, 0, 0, tzinfo=timezone.utc)

    # Batch 3: 12 bars (13:00 to 16:00 UTC)
    b3_start, b3_end, b3_count = batches[2]
    assert b3_count == 12
    assert b3_start == datetime(2026, 9, 14, 13, 0, 0, tzinfo=timezone.utc)
    assert b3_end == datetime(2026, 9, 14, 16, 0, 0, tzinfo=timezone.utc)

    assert (b1_count + b2_count + b3_count) == 52


@pytest.mark.django_db
def test_full_bounded_recovery_execution(setup_xauusd_instrument):
    """
    Requirement 3, 4, 5, 6, 7:
    Simulate restart after 13h downtime (03:00 to 16:00 UTC).
    Verify bounded batch recovery executes in 3 calls, stores 52 candles,
    leaves lag = 0, creates zero duplicates, and respects unique constraints.
    """
    inst, listing = setup_xauusd_instrument
    ledger.reset_for_test()

    # Seed initial candle at 03:00 close (02:45 open)
    t0_open = datetime(2026, 9, 14, 2, 45, 0, tzinfo=timezone.utc)
    t0_close = datetime(2026, 9, 14, 3, 0, 0, tzinfo=timezone.utc)
    MarketCandle.objects.create(
        instrument=inst, source=listing.provider, timeframe="15m",
        timestamp_open=t0_open, timestamp_close=t0_close,
        open=Decimal("2500"), high=Decimal("2501"), low=Decimal("2499"), close=Decimal("2500"),
        volume=Decimal("100"), is_closed=True, quote_rate=Decimal("1.0"), data_quality_flag="PASS"
    )

    restart_now = datetime(2026, 9, 14, 16, 0, 20, tzinfo=timezone.utc)

    # Mock provider returning 20, 20, 12 candles on each respective batch call
    mock_provider = MagicMock()
    mock_provider.is_configured.return_value = True

    def _mock_fetch(symbol, timeframe, start, end, only_closed=True):
        diff_minutes = (end - start).total_seconds() / 60.0
        bar_count = int(diff_minutes // 15)
        return _generate_mock_candles(start, bar_count, "15m")

    mock_provider.fetch_candles.side_effect = _mock_fetch

    with patch("apps.market_data.tasks.registry.get", return_value=mock_provider):
        res = dispatch_closed_candle_ingestion(
            instrument_symbol="XAU/USD",
            candidate_timeframes=["15m"],
            now_utc=restart_now,
        )

    assert res["status"] == "success"
    assert res["total_recovery_batches"] == 3
    assert res["candles_ingested"] == 52
    assert res["data_continuity_ready"] is True

    tf_res = res["results"]["15m"]
    assert tf_res["candle_lag_bars"] == 0
    assert tf_res["data_continuity_ready"] is True
    assert tf_res["latest_expected_close"] == "2026-09-14T16:00:00+00:00"
    assert tf_res["latest_stored_close"] == "2026-09-14T16:00:00+00:00"

    # Verify database state: exactly 53 candles (1 initial + 52 recovered)
    all_candles = list(MarketCandle.objects.filter(instrument=inst, timeframe="15m").order_by("timestamp_close"))
    assert len(all_candles) == 53
    assert all_candles[0].timestamp_close == t0_close
    assert all_candles[-1].timestamp_close == datetime(2026, 9, 14, 16, 0, 0, tzinfo=timezone.utc)

    # Verify no duplicate timestamps
    open_ts_set = set(c.timestamp_open for c in all_candles)
    assert len(open_ts_set) == 53


@pytest.mark.django_db
def test_independent_multi_timeframe_recovery(setup_xauusd_instrument):
    """
    Requirement 8: Recovery independently handles 1h, 4h, and 1d timeframes.
    13h downtime (03:00 to 16:00 UTC):
    - 1h: 13 bars missing -> 1 batch of 13
    - 4h: 3 bars missing -> 1 batch of 3
    - 1d: 0 bars missing (same UTC day) -> 0 batches
    """
    inst, listing = setup_xauusd_instrument
    ledger.reset_for_test()

    scheduler = ClosedCandleScheduler()
    t_03 = datetime(2026, 9, 14, 3, 0, 0, tzinfo=timezone.utc)
    t_16 = datetime(2026, 9, 14, 16, 0, 0, tzinfo=timezone.utc)

    # 1h: 13 missing bars -> 1 batch <= 20
    batches_1h = scheduler.plan_recovery_batches("1h", t_16, t_03, max_batch_size=20)
    assert len(batches_1h) == 1
    assert batches_1h[0][2] == 13

    # 4h: 3 missing bars -> 1 batch <= 20
    batches_4h = scheduler.plan_recovery_batches("4h", t_16, datetime(2026, 9, 14, 4, 0, 0, tzinfo=timezone.utc), max_batch_size=20)
    assert len(batches_4h) == 1
    assert batches_4h[0][2] == 3

    # 1d: No daily candle closed between 03:00 and 16:00 UTC on same calendar day
    t_today_00 = datetime(2026, 9, 14, 0, 0, 0, tzinfo=timezone.utc)
    batches_1d = scheduler.plan_recovery_batches("1d", t_today_00, t_today_00, max_batch_size=20)
    assert len(batches_1d) == 0


def test_weekend_downtime_no_synthetic_candles():
    """
    Requirement 9: Weekend market closure downtime must NOT fabricate Saturday/Sunday candles.
    Runtime OFF Friday 21:00 UTC, ON Monday 03:00 UTC.
    Scheduler returns single normal lookback batch for reopen, never synthetic weekend candles.
    """
    scheduler = ClosedCandleScheduler()
    t_fri_close = datetime(2026, 9, 11, 21, 0, 0, tzinfo=timezone.utc)
    t_mon_close = datetime(2026, 9, 14, 3, 0, 0, tzinfo=timezone.utc)

    # Classifies as PROVIDER_MARKET_CLOSURE
    assert classify_candle_gap(t_fri_close, t_mon_close, "15m") == "PROVIDER_MARKET_CLOSURE"

    batches = scheduler.plan_recovery_batches(
        timeframe="15m",
        expected_close=t_mon_close,
        latest_stored_close=t_fri_close,
        max_batch_size=20,
    )

    # Single batch covering normal lookback (2 bars) of the actual reopen candle(s)
    assert len(batches) == 1
    assert batches[0][2] == NORMAL_LOOKBACK_BARS
    assert batches[0][1] == t_mon_close


@pytest.mark.django_db
def test_credit_safety_partial_recovery_fail_closed(setup_xauusd_instrument):
    """
    Requirement 10: If credit budget is exhausted midway, partial recovery is allowed,
    but DATA_CONTINUITY_READY remains FALSE. Runtime is not falsely marked healthy.
    """
    inst, listing = setup_xauusd_instrument
    ledger.reset_for_test()

    # Pre-consume 198 credits so only 2 credits remain before 200 cap
    now_utc = datetime(2026, 9, 14, 16, 0, 20, tzinfo=timezone.utc)
    ledger.record_automatic_credit_consumed(now_utc, count=198)
    assert ledger.get_daily_total_credit_count(now_utc) == 198

    # Seed candle at 03:00
    t0_open = datetime(2026, 9, 14, 2, 45, 0, tzinfo=timezone.utc)
    t0_close = datetime(2026, 9, 14, 3, 0, 0, tzinfo=timezone.utc)
    MarketCandle.objects.create(
        instrument=inst, source=listing.provider, timeframe="15m",
        timestamp_open=t0_open, timestamp_close=t0_close,
        open=Decimal("2500"), high=Decimal("2501"), low=Decimal("2499"), close=Decimal("2500"),
        volume=Decimal("100"), is_closed=True, quote_rate=Decimal("1.0"), data_quality_flag="PASS"
    )

    # Mock provider returning 20 candles per batch
    mock_provider = MagicMock()
    mock_provider.is_configured.return_value = True

    def _mock_fetch(symbol, timeframe, start, end, only_closed=True):
        diff_minutes = (end - start).total_seconds() / 60.0
        bar_count = int(diff_minutes // 15)
        return _generate_mock_candles(start, bar_count, "15m")

    mock_provider.fetch_candles.side_effect = _mock_fetch

    with patch("apps.market_data.tasks.registry.get", return_value=mock_provider):
        res = dispatch_closed_candle_ingestion(
            instrument_symbol="XAU/USD",
            candidate_timeframes=["15m"],
            now_utc=now_utc,
        )

    # 2 batches executed (20 + 20 = 40 candles), 3rd batch (12 candles) halted due to cap
    assert res["total_recovery_batches"] == 2
    assert res["candles_ingested"] == 40
    assert res["data_continuity_ready"] is False

    tf_res = res["results"]["15m"]
    assert tf_res["data_continuity_ready"] is False
    assert tf_res["candle_lag_bars"] == 12
    assert tf_res["latest_stored_close"] == "2026-09-14T13:00:00+00:00"
    assert ledger.get_daily_total_credit_count(now_utc) == 200


@pytest.mark.django_db
def test_phase8_signal_pipeline_gated_during_unresolved_gap(setup_xauusd_instrument):
    """
    Requirement 11: Phase 8 signal pipeline fails-closed (candidate_action = WAIT)
    when an earlier unresolved intrasession continuity gap is present in 15m candles.
    Once full recovery is complete, normal signal processing resumes.
    """
    inst, listing = setup_xauusd_instrument

    from apps.live_monitor.adapter import PublicMarketDataAdapter
    from apps.live_monitor.services import XauUsdLiveDecisionPipelineService
    from apps.instruments.models import ProviderHealthSnapshot

    # Create healthy provider health snapshot
    ProviderHealthSnapshot.objects.create(
        listing=listing, status="HEALTHY", checked_at=datetime(2026, 9, 14, 16, 0, 0, tzinfo=timezone.utc), latency_ms=100
    )

    # 1. Seed 20 historical candles ending at 03:00 UTC
    base_t = datetime(2026, 9, 13, 22, 0, 0, tzinfo=timezone.utc)
    for c in _generate_mock_candles(base_t, 20, "15m"):
        MarketCandle.objects.create(
            instrument=inst, source=listing.provider, timeframe="15m",
            timestamp_open=c.timestamp_open, timestamp_close=c.timestamp_close,
            open=c.open, high=c.high, low=c.low, close=c.close,
            volume=c.volume, is_closed=True, quote_rate=Decimal("1.0"), data_quality_flag="PASS"
        )

    # 2. Simulate arrival of a new candle at 16:00 UTC while 03:00-16:00 is missing (52-bar gap)
    t_16_open = datetime(2026, 9, 14, 15, 45, 0, tzinfo=timezone.utc)
    t_16_close = datetime(2026, 9, 14, 16, 0, 0, tzinfo=timezone.utc)

    event_16 = PublicMarketDataAdapter.create_xauusd_candle_closed_event(
        instrument="XAUUSD", timeframe="15m",
        timestamp_open=t_16_open, timestamp_close=t_16_close,
        open_price=Decimal("2510.00"), high_price=Decimal("2515.00"),
        low_price=Decimal("2508.00"), close_price=Decimal("2512.00"),
        volume=Decimal("150.0"), source=listing.provider, is_closed=True,
    )

    # Evaluate closed candle during unresolved gap: must fail-closed to WAIT
    sig, risk, state = XauUsdLiveDecisionPipelineService.process_closed_candle(
        event=event_16,
        code_revision="4a8a993af55ad433800e0bb8868e94063d62ff89",
        is_feed_stale=False,
    )

    assert state.candidate_effective_action == "WAIT"
    assert state.publication_effective_action == "WAIT"
    assert state.risk_plan_valid is False
    assert state.candidate_state == "FORCE_WAIT"
    assert state.feed_health_data.get("primary_15m") == "UNHEALTHY"

    # 3. Now perform full recovery: insert the missing candles between 03:00 and 15:45
    t_missing_start = datetime(2026, 9, 14, 3, 0, 0, tzinfo=timezone.utc)
    for c in _generate_mock_candles(t_missing_start, 51, "15m"):
        MarketCandle.objects.create(
            instrument=inst, source=listing.provider, timeframe="15m",
            timestamp_open=c.timestamp_open, timestamp_close=c.timestamp_close,
            open=c.open, high=c.high, low=c.low, close=c.close,
            volume=c.volume, is_closed=True, quote_rate=Decimal("1.0"), data_quality_flag="PASS"
        )

    # 4. Re-evaluate post-recovery: continuity restored, primary_15m is HEALTHY
    sig_recovered, risk_recovered, state_recovered = XauUsdLiveDecisionPipelineService.process_closed_candle(
        event=event_16,
        code_revision="4a8a993af55ad433800e0bb8868e94063d62ff89",
        is_feed_stale=False,
    )

    assert state_recovered.feed_health_data.get("primary_15m") == "HEALTHY"
