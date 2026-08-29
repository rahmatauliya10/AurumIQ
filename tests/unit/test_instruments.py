"""Unit tests for Asset, Instrument, MarketListing, and ProviderHealthSnapshot models."""
from decimal import Decimal
import pytest
from django.utils import timezone
from apps.instruments.models import (
    Asset,
    AssetType,
    Instrument,
    InstrumentRole,
    InstrumentType,
    MarketListing,
    ListingStatus,
    ProviderHealthSnapshot,
    ProviderHealthStatus,
)


@pytest.mark.unit
@pytest.mark.django_db
def test_asset_creation_and_uniqueness():
    """Verify Asset model instantiation and uniqueness constraint."""
    xaut = Asset.objects.create(
        code="XAUT",
        name="Tether Gold",
        asset_type=AssetType.CRYPTO_TOKEN,
    )
    assert str(xaut) == "XAUT (Crypto Token)"
    assert xaut.code == "XAUT"

    with pytest.raises(Exception):
        Asset.objects.create(
            code="XAUT",
            name="Duplicate Token",
            asset_type=AssetType.CRYPTO_TOKEN,
        )


@pytest.mark.unit
@pytest.mark.django_db
def test_instrument_creation_and_properties():
    """Verify Instrument model relations, symbol property, and role assignment."""
    xaut = Asset.objects.create(code="XAUT", name="Tether Gold")
    usdt = Asset.objects.create(code="USDT", name="Tether USD")
    
    inst = Instrument.objects.create(
        base_asset=xaut,
        quote_asset=usdt,
        instrument_type=InstrumentType.SPOT,
        role=InstrumentRole.EXECUTION,
    )
    assert inst.symbol == "XAUT/USDT"
    assert inst.is_active is True
    assert "XAUT/USDT" in str(inst)


@pytest.mark.unit
@pytest.mark.django_db
def test_market_listing_ordering_and_fallback():
    """Verify MarketListing priority ordering for multi-exchange fallback."""
    xaut = Asset.objects.create(code="XAUT", name="Tether Gold")
    usdt = Asset.objects.create(code="USDT", name="Tether USD")
    inst = Instrument.objects.create(
        base_asset=xaut, quote_asset=usdt, instrument_type=InstrumentType.SPOT
    )

    listing_binance = MarketListing.objects.create(
        instrument=inst,
        provider="binance",
        provider_symbol="XAUTUSDT",
        fallback_priority=0,
        status=ListingStatus.ACTIVE,
    )
    listing_okx = MarketListing.objects.create(
        instrument=inst,
        provider="okx",
        provider_symbol="XAUT-USDT",
        fallback_priority=1,
        status=ListingStatus.ACTIVE,
    )

    listings = list(inst.listings.all())
    assert listings[0] == listing_binance
    assert listings[1] == listing_okx


@pytest.mark.unit
@pytest.mark.django_db
def test_provider_health_snapshot_logging():
    """Verify ProviderHealthSnapshot records temporal latency and status."""
    xaut = Asset.objects.create(code="XAUT", name="Tether Gold")
    usdt = Asset.objects.create(code="USDT", name="Tether USD")
    inst = Instrument.objects.create(base_asset=xaut, quote_asset=usdt)
    listing = MarketListing.objects.create(
        instrument=inst, provider="binance", provider_symbol="XAUTUSDT"
    )

    snapshot = ProviderHealthSnapshot.objects.create(
        listing=listing,
        status=ProviderHealthStatus.HEALTHY,
        checked_at=timezone.now(),
        latency_ms=45,
        consecutive_failures=0,
    )
    assert snapshot.status == ProviderHealthStatus.HEALTHY
    assert snapshot.latency_ms == 45
    assert listing.health_snapshots.count() == 1
