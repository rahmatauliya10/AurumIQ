"""ORM Models for persisting FeatureSnapshots, RegimeSnapshots, StructureSnapshots, and CycleSnapshots."""
from decimal import Decimal
from django.db import models
from apps.instruments.models import Instrument


class FeatureSnapshotRecord(models.Model):
    """Persisted technical feature and indicator snapshot."""
    instrument = models.ForeignKey(
        Instrument, on_delete=models.CASCADE, related_name="feature_snapshots"
    )
    timeframe = models.CharField(max_length=16, db_index=True)
    timestamp = models.DateTimeField(db_index=True)
    
    # Trend
    ema20 = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    ema50 = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    ema200 = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    ema_slope_20 = models.FloatField(null=True, blank=True)
    ema_alignment = models.SmallIntegerField(default=0)
    adx = models.FloatField(null=True, blank=True)
    plus_di = models.FloatField(null=True, blank=True)
    minus_di = models.FloatField(null=True, blank=True)
    
    # Momentum
    rsi14 = models.FloatField(null=True, blank=True)
    macd_line = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    macd_signal = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    macd_hist = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    roc12 = models.FloatField(null=True, blank=True)
    
    # Volatility
    atr14 = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    atr_pct = models.FloatField(null=True, blank=True)
    bb_upper = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    bb_middle = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    bb_lower = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    bb_bandwidth = models.FloatField(null=True, blank=True)
    realized_vol_20 = models.FloatField(null=True, blank=True)
    
    # Volume
    volume_ratio_20 = models.FloatField(null=True, blank=True)
    volume_zscore_20 = models.FloatField(null=True, blank=True)

    feature_version = models.CharField(max_length=32, default="feat-2026-v1", db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["instrument", "timeframe", "-timestamp"]),
        ]
        unique_together = ("instrument", "timeframe", "timestamp")

    def __str__(self) -> str:
        return f"Features {self.instrument} [{self.timeframe}] @ {self.timestamp.isoformat()}"


class RegimeSnapshotRecord(models.Model):
    """Persisted market regime classification result."""
    instrument = models.ForeignKey(
        Instrument, on_delete=models.CASCADE, related_name="regime_snapshots"
    )
    timeframe = models.CharField(max_length=16, db_index=True)
    timestamp = models.DateTimeField(db_index=True)
    regime = models.CharField(max_length=32, db_index=True)
    confidence = models.DecimalField(max_digits=5, decimal_places=4)
    details = models.JSONField(default=dict, blank=True)
    feature_version = models.CharField(max_length=32, default="feat-2026-v1", db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["instrument", "timeframe", "-timestamp"]),
        ]
        unique_together = ("instrument", "timeframe", "timestamp")

    def __str__(self) -> str:
        return f"Regime {self.instrument} [{self.timeframe}] @ {self.timestamp.isoformat()} -> {self.regime} ({self.confidence})"


class StructureSnapshotRecord(models.Model):
    """Persisted causal market structure analysis output."""
    instrument = models.ForeignKey(
        Instrument, on_delete=models.CASCADE, related_name="structure_snapshots"
    )
    timeframe = models.CharField(max_length=16, db_index=True)
    timestamp = models.DateTimeField(db_index=True)
    structure_type = models.CharField(max_length=32)
    bos = models.CharField(max_length=16)
    last_swing_high_price = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    last_swing_low_price = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    active_zones = models.JSONField(default=list, blank=True)
    feature_version = models.CharField(max_length=32, default="feat-2026-v1", db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["instrument", "timeframe", "-timestamp"]),
        ]
        unique_together = ("instrument", "timeframe", "timestamp")

    def __str__(self) -> str:
        return f"Structure {self.instrument} [{self.timeframe}] @ {self.timestamp.isoformat()} -> {self.structure_type} BOS:{self.bos}"


class CycleSnapshotRecord(models.Model):
    """Persisted Phase 3A Robust Time Cycle snapshot with version immutability."""
    instrument = models.ForeignKey(
        Instrument, on_delete=models.CASCADE, related_name="cycle_snapshots"
    )
    timeframe = models.CharField(max_length=16, db_index=True)
    timestamp = models.DateTimeField(db_index=True)
    cycle_version = models.CharField(max_length=32, default="3.0.0-3A", db_index=True)
    session = models.CharField(max_length=32, db_index=True)
    session_progress_pct = models.FloatField(default=0.0)
    is_high_liquidity = models.BooleanField(default=False)
    bars_since_last_swing = models.IntegerField(default=0)
    pullback_age_percentile = models.FloatField(null=True, blank=True)
    is_mature_pullback = models.BooleanField(default=False)
    is_blocked_by_event = models.BooleanField(default=False)
    cycle_score_3a = models.FloatField(default=0.0)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["instrument", "timeframe", "-timestamp", "cycle_version"]),
        ]
        unique_together = ("instrument", "timeframe", "timestamp", "cycle_version")

    def __str__(self) -> str:
        return f"Cycle3A {self.instrument} [{self.timeframe}] @ {self.timestamp.isoformat()} ({self.cycle_version}) -> {self.session} Score:{self.cycle_score_3a}"
