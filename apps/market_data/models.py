"""Market data storage models: MarketCandle, DataQualitySnapshot, and QuarantineRecord."""
from decimal import Decimal
from django.db import models
from apps.instruments.models import Instrument, MarketListing


class VolumeEvidenceType(models.TextChoices):
    REAL_VOLUME = "REAL_VOLUME", "Real Volume"
    TICK_VOLUME = "TICK_VOLUME", "Tick Volume"
    PROXY_VOLUME = "PROXY_VOLUME", "Proxy Volume"
    UNAVAILABLE = "UNAVAILABLE", "Unavailable / Missing"


class CandleQualityFlag(models.TextChoices):
    OK = "OK", "Good Quality"
    SUSPECT = "SUSPECT", "Suspect Data"
    INTERPOLATED = "INTERPOLATED", "Interpolated Missing Bar"
    QUARANTINED = "QUARANTINED", "Quarantined Source"


class MarketCandle(models.Model):
    """OHLCV candlestick data stored in strict UTC point-in-time sequence."""
    instrument = models.ForeignKey(
        Instrument,
        on_delete=models.CASCADE,
        related_name="candles",
    )
    source = models.CharField(
        max_length=32,
        db_index=True,
        help_text="Origin source exchange or feed ID (e.g. binance, okx, gold_reference)",
    )
    timeframe = models.CharField(
        max_length=8,
        db_index=True,
        help_text="Timeframe interval: 1m, 5m, 15m, 1h, 4h, 1d",
    )
    timestamp_open = models.DateTimeField(db_index=True)
    timestamp_close = models.DateTimeField(db_index=True)
    
    # OHLCV values
    open = models.DecimalField(max_digits=18, decimal_places=8)
    high = models.DecimalField(max_digits=18, decimal_places=8)
    low = models.DecimalField(max_digits=18, decimal_places=8)
    close = models.DecimalField(max_digits=18, decimal_places=8)
    volume = models.DecimalField(max_digits=24, decimal_places=8, default=Decimal("0"))
    volume_evidence = models.CharField(
        max_length=16,
        choices=VolumeEvidenceType.choices,
        default=VolumeEvidenceType.UNAVAILABLE,
        db_index=True,
        help_text="Volume semantics evidence type (REAL_VOLUME, TICK_VOLUME, PROXY_VOLUME, UNAVAILABLE)",
    )
    
    # Normalization & Integrity
    quote_rate = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        default=Decimal("1.000000"),
        help_text="USDT/USD conversion rate applied to this candle",
    )
    close_usd = models.DecimalField(
        max_digits=18,
        decimal_places=8,
        null=True,
        blank=True,
        help_text="Normalized USD price (close * quote_rate)",
    )
    is_closed = models.BooleanField(default=True, db_index=True)
    data_quality_flag = models.CharField(
        max_length=16,
        choices=CandleQualityFlag.choices,
        default=CandleQualityFlag.OK,
        db_index=True,
    )
    source_sequence = models.BigIntegerField(null=True, blank=True)
    ingestion_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("instrument", "source", "timeframe", "timestamp_open")
        ordering = ["timestamp_open"]
        verbose_name = "Market Candle"
        verbose_name_plural = "Market Candles"
        indexes = [
            models.Index(fields=["instrument", "timeframe", "timestamp_open"]),
            models.Index(fields=["instrument", "timeframe", "-timestamp_open"]),
            models.Index(fields=["instrument", "source", "timeframe", "timestamp_open"]),
        ]

    def save(self, *args, **kwargs):
        # Direct USD native pricing vs legacy rate normalization
        if self.close is not None:
            # If quote asset is USD, close_usd is direct close (DIRECT_USD identity semantics)
            if hasattr(self, "instrument") and self.instrument and getattr(self.instrument.quote_asset, "code", None) == "USD":
                self.quote_rate = Decimal("1.000000")
                self.close_usd = self.close.quantize(Decimal("0.00000001"))
            elif self.quote_rate is not None:
                self.close_usd = (self.close * self.quote_rate).quantize(Decimal("0.00000001"))
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return (
            f"{self.instrument.symbol} [{self.timeframe}] @ {self.timestamp_open.isoformat()} "
            f"O:{self.open} H:{self.high} L:{self.low} C:{self.close} ({self.source})"
        )


class DataQualitySnapshot(models.Model):
    """Point-in-time assessment of time-series data quality and integrity."""
    instrument = models.ForeignKey(
        Instrument,
        on_delete=models.CASCADE,
        related_name="quality_snapshots",
    )
    timeframe = models.CharField(max_length=8, db_index=True)
    timestamp = models.DateTimeField(db_index=True)
    quality_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("100.00"),
        help_text="Calculated quality score (0.0 to 100.0)",
    )
    gap_count = models.IntegerField(default=0)
    duplicate_count = models.IntegerField(default=0)
    violation_count = models.IntegerField(default=0)
    is_stale = models.BooleanField(default=False)
    hard_fail = models.BooleanField(
        default=False,
        help_text="If True, hard gate blocks all BUY_WINDOW signals",
    )
    anomalies = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "Data Quality Snapshot"
        verbose_name_plural = "Data Quality Snapshots"
        indexes = [
            models.Index(fields=["instrument", "timeframe", "-timestamp"]),
        ]

    def __str__(self) -> str:
        status = "HARD_FAIL" if self.hard_fail else f"{self.quality_score}%"
        return f"{self.instrument.symbol} [{self.timeframe}] @ {self.timestamp.isoformat()} -> {status}"


class QuarantineRecord(models.Model):
    """Audit log for quarantined providers that exceeded outlier tolerances."""
    listing = models.ForeignKey(
        MarketListing,
        on_delete=models.CASCADE,
        related_name="quarantine_records",
    )
    provider = models.CharField(max_length=32, db_index=True)
    symbol = models.CharField(max_length=64)
    quarantined_at = models.DateTimeField(auto_now_add=True, db_index=True)
    released_at = models.DateTimeField(null=True, blank=True)
    reason = models.TextField()
    basis_deviation = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Deviation percentage from consensus reference",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["-quarantined_at"]
        verbose_name = "Quarantine Record"
        verbose_name_plural = "Quarantine Records"

    def __str__(self) -> str:
        active_str = "ACTIVE" if self.is_active else "RELEASED"
        return f"Quarantine: {self.provider} ({self.symbol}) - {active_str} [{self.reason[:40]}]"
