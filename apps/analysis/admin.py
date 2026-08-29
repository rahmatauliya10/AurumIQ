"""Django Admin configuration for analysis models."""
from django.contrib import admin
from .models import FeatureSnapshotRecord, RegimeSnapshotRecord, StructureSnapshotRecord


@admin.register(FeatureSnapshotRecord)
class FeatureSnapshotRecordAdmin(admin.ModelAdmin):
    list_display = ("instrument", "timeframe", "timestamp", "ema20", "rsi14", "adx", "atr14")
    list_filter = ("timeframe", "instrument")
    search_fields = ("instrument__base_asset__code", "instrument__quote_asset__code")
    readonly_fields = ("created_at",)


@admin.register(RegimeSnapshotRecord)
class RegimeSnapshotRecordAdmin(admin.ModelAdmin):
    list_display = ("instrument", "timeframe", "timestamp", "regime", "confidence")
    list_filter = ("regime", "timeframe", "instrument")
    search_fields = ("instrument__base_asset__code",)
    readonly_fields = ("created_at",)


@admin.register(StructureSnapshotRecord)
class StructureSnapshotRecordAdmin(admin.ModelAdmin):
    list_display = ("instrument", "timeframe", "timestamp", "structure_type", "bos", "last_swing_high_price", "last_swing_low_price")
    list_filter = ("structure_type", "bos", "timeframe")
    search_fields = ("instrument__base_asset__code",)
    readonly_fields = ("created_at",)
