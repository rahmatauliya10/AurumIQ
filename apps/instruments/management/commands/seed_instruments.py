"""Seed standard assets, instruments, and market listings."""
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db import transaction
from apps.instruments.models import (
    Asset,
    AssetType,
    Instrument,
    InstrumentRole,
    InstrumentType,
    MarketListing,
    ListingStatus,
    ListingRole,
)


class Command(BaseCommand):
    help = "Seed baseline assets, instruments, and exchange listings for XAUT Intelligence"

    def handle(self, *args, **options):
        with transaction.atomic():
            # 1. Assets
            xaut, _ = Asset.objects.get_or_create(
                code="XAUT",
                defaults={"name": "Tether Gold", "asset_type": AssetType.CRYPTO_TOKEN},
            )
            xau, _ = Asset.objects.get_or_create(
                code="XAU",
                defaults={"name": "Spot Gold (Troy Ounce)", "asset_type": AssetType.COMMODITY},
            )
            usdt, _ = Asset.objects.get_or_create(
                code="USDT",
                defaults={"name": "Tether USD", "asset_type": AssetType.CRYPTO_TOKEN},
            )
            usd, _ = Asset.objects.get_or_create(
                code="USD",
                defaults={"name": "United States Dollar", "asset_type": AssetType.FIAT},
            )
            dxy, _ = Asset.objects.get_or_create(
                code="DXY",
                defaults={"name": "US Dollar Index", "asset_type": AssetType.INDEX},
            )

            self.stdout.write(self.style.SUCCESS("Assets seeded: XAUT, XAU, USDT, USD, DXY"))

            # 2. Instruments
            xaut_usdt, _ = Instrument.objects.get_or_create(
                base_asset=xaut,
                quote_asset=usdt,
                instrument_type=InstrumentType.SPOT,
                defaults={"role": InstrumentRole.EXECUTION, "is_active": True},
            )
            xau_usd, _ = Instrument.objects.get_or_create(
                base_asset=xau,
                quote_asset=usd,
                instrument_type=InstrumentType.SPOT,
                defaults={"role": InstrumentRole.GOLD_REFERENCE, "is_active": True},
            )
            usdt_usd, _ = Instrument.objects.get_or_create(
                base_asset=usdt,
                quote_asset=usd,
                instrument_type=InstrumentType.SPOT,
                defaults={"role": InstrumentRole.QUOTE_NORMALIZATION, "is_active": True},
            )

            self.stdout.write(self.style.SUCCESS("Instruments seeded: XAUT/USDT, XAU/USD, USDT/USD"))

            # 3. Market Listings
            # Binance XAUTUSDT (Primary)
            MarketListing.objects.get_or_create(
                instrument=xaut_usdt,
                provider="binance",
                defaults={
                    "provider_symbol": "XAUTUSDT",
                    "listing_role": ListingRole.LEGACY_EXECUTION,
                    "status": ListingStatus.ACTIVE,
                    "tick_size": Decimal("0.01"),
                    "lot_size": Decimal("0.0001"),
                    "fallback_priority": 0,
                },
            )
            # OKX XAUT-USDT (Secondary Fallback)
            MarketListing.objects.get_or_create(
                instrument=xaut_usdt,
                provider="okx",
                defaults={
                    "provider_symbol": "XAUT-USDT",
                    "listing_role": ListingRole.LEGACY_EXECUTION,
                    "status": ListingStatus.ACTIVE,
                    "tick_size": Decimal("0.01"),
                    "lot_size": Decimal("0.0001"),
                    "fallback_priority": 1,
                },
            )
            # Gold Reference XAU/USD
            MarketListing.objects.get_or_create(
                instrument=xau_usd,
                provider="gold_reference",
                defaults={
                    "provider_symbol": "XAUUSD",
                    "listing_role": ListingRole.LEGACY_GOLD_REFERENCE,
                    "status": ListingStatus.ACTIVE,
                    "tick_size": Decimal("0.01"),
                    "lot_size": Decimal("0.01"),
                    "fallback_priority": 0,
                },
            )
            # Twelve Data Primary Spot Gold XAU/USD (Authoritative Qualified Primary)
            MarketListing.objects.update_or_create(
                instrument=xau_usd,
                provider="twelve_data_xauusd",
                defaults={
                    "provider_symbol": "XAU/USD",
                    "listing_role": ListingRole.PRIMARY_XAUUSD_SPOT,
                    "status": ListingStatus.ACTIVE,
                    "tick_size": Decimal("0.01"),
                    "lot_size": Decimal("0.0001"),
                    "fallback_priority": 0,
                },
            )
            # Legacy placeholder xauusd_primary - preserved as dormant / halted generic listing
            MarketListing.objects.update_or_create(
                instrument=xau_usd,
                provider="xauusd_primary",
                defaults={
                    "provider_symbol": "XAUUSD",
                    "listing_role": ListingRole.GENERIC,
                    "status": ListingStatus.HALTED,
                    "tick_size": Decimal("0.01"),
                    "lot_size": Decimal("0.0001"),
                    "fallback_priority": 99,
                },
            )
            # Ensure exactly one active PRIMARY_XAUUSD_SPOT listing for canonical XAUUSD
            MarketListing.objects.filter(
                instrument=xau_usd,
                listing_role=ListingRole.PRIMARY_XAUUSD_SPOT,
            ).exclude(
                provider="twelve_data_xauusd"
            ).update(
                listing_role=ListingRole.GENERIC,
                status=ListingStatus.HALTED,
                fallback_priority=99,
            )
            # Secondary Independent Spot Gold XAU/USD
            MarketListing.objects.update_or_create(
                instrument=xau_usd,
                provider="xauusd_secondary",
                defaults={
                    "provider_symbol": "XAUUSD",
                    "listing_role": ListingRole.SECONDARY_XAUUSD_SPOT,
                    "status": ListingStatus.ACTIVE,
                    "tick_size": Decimal("0.01"),
                    "lot_size": Decimal("0.0001"),
                    "fallback_priority": 1,
                },
            )
            # USDT/USD Normalization Rate
            MarketListing.objects.get_or_create(
                instrument=usdt_usd,
                provider="usdt_usd",
                defaults={
                    "provider_symbol": "USDTUSD",
                    "listing_role": ListingRole.LEGACY_QUOTE_NORMALIZATION,
                    "status": ListingStatus.ACTIVE,
                    "tick_size": Decimal("0.0001"),
                    "lot_size": Decimal("1.0"),
                    "fallback_priority": 0,
                },
            )

            self.stdout.write(self.style.SUCCESS("Market listings seeded successfully."))
