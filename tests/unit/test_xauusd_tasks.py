"""
Unit tests for Phase 4 XAUUSD Celery task routing and provider health resolution.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest

from apps.instruments.models import (
    Asset,
    AssetType,
    Instrument,
    InstrumentRole,
    InstrumentType,
    ListingRole,
    ListingStatus,
    MarketListing,
    ProviderHealthSnapshot,
)
from apps.market_data.models import MarketCandle
from apps.signals.models import SignalRecord
from apps.signals.tasks import analyze_closed_candle


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
def populated_candles(db, xauusd_instrument):
    t0 = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    for i in range(30):
        ts = t0 - timedelta(minutes=15 * (30 - i))
        MarketCandle.objects.create(
            instrument=xauusd_instrument,
            timeframe="15m",
            timestamp_open=ts - timedelta(minutes=15),
            timestamp_close=ts,
            open=Decimal("2500.00"),
            high=Decimal("2510.00"),
            low=Decimal("2490.00"),
            close=Decimal("2505.00"),
            volume=Decimal("1000.00"),
            is_closed=True,
            source="test_primary",
        )
    return t0


@pytest.mark.django_db
def test_xauusd_task_primary_secondary_routing(xauusd_instrument, populated_candles):
    """Verify primary and secondary provider health resolution via ListingRole."""
    t0 = populated_candles
    t0_iso = t0.isoformat()

    # Create Primary & Secondary listings
    primary_listing = MarketListing.objects.create(
        instrument=xauusd_instrument,
        provider="primary_venue",
        provider_symbol="XAUUSD",
        listing_role=ListingRole.PRIMARY_XAUUSD_SPOT,
        status=ListingStatus.ACTIVE,
    )
    secondary_listing = MarketListing.objects.create(
        instrument=xauusd_instrument,
        provider="secondary_venue",
        provider_symbol="XAUUSD",
        listing_role=ListingRole.SECONDARY_XAUUSD_SPOT,
        status=ListingStatus.ACTIVE,
    )

    # 1. Primary HEALTHY, Secondary MISSING -> Success (Layer B: NO_TRADE / WAIT, NOT FORCE_WAIT)
    ProviderHealthSnapshot.objects.create(
        listing=primary_listing,
        status="HEALTHY",
        checked_at=t0,
    )

    res = analyze_closed_candle(
        instrument_id=xauusd_instrument.id,
        timeframe="15m",
        candle_timestamp_iso=t0_iso,
        code_revision="test-rev-p4",
        macro_context="CLEAR",
        is_stale_feed=False,
    )

    assert res["status"] == "SUCCESS"
    assert res["state"] == "NO_TRADE"
    assert res["user_decision"] == "WAIT"
    assert res["direction_score"] is None
    assert res["timing_score"] is None

    # 2. Primary TRANSITION -> trips FORCE_WAIT
    ProviderHealthSnapshot.objects.create(
        listing=primary_listing,
        status="TRANSITION",
        checked_at=t0 + timedelta(minutes=1),
    )

    res_trans = analyze_closed_candle(
        instrument_id=xauusd_instrument.id,
        timeframe="15m",
        candle_timestamp_iso=(t0 + timedelta(minutes=1)).isoformat(),
        code_revision="test-rev-p4",
        macro_context="CLEAR",
        is_stale_feed=False,
    )

    assert res_trans["status"] == "SUCCESS"
    assert res_trans["state"] == "FORCE_WAIT"
    assert res_trans["user_decision"] == "WAIT"
