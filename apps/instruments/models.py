from typing import Optional
from django.db import models


class AssetType(models.TextChoices):
    CRYPTO_TOKEN = "CRYPTO_TOKEN", "Crypto Token"
    COMMODITY = "COMMODITY", "Physical Commodity"
    FIAT = "FIAT", "Fiat Currency"
    INDEX = "INDEX", "Financial Index"


class Asset(models.Model):
    """Abstract economic asset representation (e.g. XAUT, XAU, USDT, USD, DXY)."""
    code = models.CharField(max_length=16, unique=True, db_index=True)
    name = models.CharField(max_length=128)
    asset_type = models.CharField(
        max_length=32,
        choices=AssetType.choices,
        default=AssetType.CRYPTO_TOKEN,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "Asset"
        verbose_name_plural = "Assets"

    def __str__(self) -> str:
        return f"{self.code} ({self.get_asset_type_display()})"


class InstrumentRole(models.TextChoices):
    PRIMARY_SIGNAL = "PRIMARY_SIGNAL", "Primary Signal Target (XAU/USD)"
    PRIMARY_XAUUSD = "PRIMARY_XAUUSD", "Primary Signal Target (XAU/USD)"
    SECONDARY_XAUUSD = "SECONDARY_XAUUSD", "Secondary Consensus Reference (XAU/USD)"
    EXECUTION = "EXECUTION", "Execution Target (XAUT/USDT)"
    GOLD_REFERENCE = "GOLD_REFERENCE", "Canonical Gold Directional Reference (XAU/USD)"
    GOLD_CONFIRMATION = "GOLD_CONFIRMATION", "Secondary Confirmation Proxy (PAXG / Gold Futures)"
    QUOTE_NORMALIZATION = "QUOTE_NORMALIZATION", "Canonical Stablecoin Normalization Rate (USDT/USD)"
    QUOTE_NORMALIZATION_PROXY = "QUOTE_NORMALIZATION_PROXY", "Stablecoin Proxy Normalization Rate (USDT/USDC)"
    MACRO = "MACRO", "Macro USD Filter (DXY / Yields)"


class InstrumentType(models.TextChoices):
    SPOT = "SPOT", "Spot Market"
    FUTURES = "FUTURES", "Futures Contract"
    INDEX = "INDEX", "Market Index"


class Instrument(models.Model):
    """Pair of assets representing a tradable or observable financial instrument."""
    base_asset = models.ForeignKey(
        Asset,
        on_delete=models.PROTECT,
        related_name="base_instruments",
    )
    quote_asset = models.ForeignKey(
        Asset,
        on_delete=models.PROTECT,
        related_name="quote_instruments",
    )
    instrument_type = models.CharField(
        max_length=16,
        choices=InstrumentType.choices,
        default=InstrumentType.SPOT,
    )
    role = models.CharField(
        max_length=32,
        choices=InstrumentRole.choices,
        default=InstrumentRole.EXECUTION,
        db_index=True,
    )
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("base_asset", "quote_asset", "instrument_type")
        ordering = ["base_asset__code", "quote_asset__code"]
        verbose_name = "Instrument"
        verbose_name_plural = "Instruments"

    @property
    def symbol(self) -> str:
        return f"{self.base_asset.code}/{self.quote_asset.code}"

    @classmethod
    def get_canonical_xauusd(cls) -> Optional["Instrument"]:
        """Resolve the canonical XAU/USD primary signal instrument."""
        return cls.objects.filter(
            base_asset__code="XAU",
            quote_asset__code="USD",
            instrument_type=InstrumentType.SPOT,
        ).first()

    @classmethod
    def get_legacy_xaut(cls) -> Optional["Instrument"]:
        """Resolve the historical legacy XAUT/USDT execution instrument."""
        return cls.objects.filter(
            base_asset__code="XAUT",
            quote_asset__code="USDT",
            instrument_type=InstrumentType.SPOT,
        ).first()

    def __str__(self) -> str:
        return f"{self.symbol} [{self.get_instrument_type_display()}] ({self.get_role_display()})"


class ListingStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    HALTED = "HALTED", "Trading Halted"
    DELISTED = "DELISTED", "Delisted"


class MarketListing(models.Model):
    """Venue-specific listing mapping an Instrument to an exchange provider."""
    instrument = models.ForeignKey(
        Instrument,
        on_delete=models.CASCADE,
        related_name="listings",
    )
    provider = models.CharField(max_length=32, db_index=True)  # e.g., binance, okx, gold_reference
    provider_symbol = models.CharField(max_length=64)          # e.g., XAUTUSDT, XAUT-USDT, XAUUSD
    status = models.CharField(
        max_length=16,
        choices=ListingStatus.choices,
        default=ListingStatus.ACTIVE,
        db_index=True,
    )
    tick_size = models.DecimalField(max_digits=12, decimal_places=6, default=0.01)
    lot_size = models.DecimalField(max_digits=12, decimal_places=6, default=0.0001)
    fallback_priority = models.IntegerField(
        default=0,
        help_text="Priority for failover ordering (0 = primary, 1 = secondary fallback, etc.)",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("instrument", "provider")
        ordering = ["fallback_priority", "provider"]
        verbose_name = "Market Listing"
        verbose_name_plural = "Market Listings"

    def __str__(self) -> str:
        return f"{self.provider.upper()}:{self.provider_symbol} -> {self.instrument.symbol}"


class ProviderHealthStatus(models.TextChoices):
    HEALTHY = "HEALTHY", "Healthy"
    DEGRADED = "DEGRADED", "Degraded"
    UNHEALTHY = "UNHEALTHY", "Unhealthy"
    QUARANTINED = "QUARANTINED", "Quarantined"
    UNKNOWN = "UNKNOWN", "Unknown"


class ProviderHealthSnapshot(models.Model):
    """Temporal point-in-time record of market provider connectivity and health."""
    listing = models.ForeignKey(
        MarketListing,
        on_delete=models.CASCADE,
        related_name="health_snapshots",
    )
    status = models.CharField(
        max_length=16,
        choices=ProviderHealthStatus.choices,
        default=ProviderHealthStatus.UNKNOWN,
        db_index=True,
    )
    checked_at = models.DateTimeField(db_index=True)
    latency_ms = models.IntegerField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    consecutive_failures = models.IntegerField(default=0)
    reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-checked_at"]
        verbose_name = "Provider Health Snapshot"
        verbose_name_plural = "Provider Health Snapshots"
        indexes = [
            models.Index(fields=["listing", "-checked_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.listing} @ {self.checked_at.isoformat()} -> {self.status}"
