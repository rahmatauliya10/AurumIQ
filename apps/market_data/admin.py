"""Django Admin registration for market data models."""
from django.contrib import admin
from apps.market_data.models import MarketCandle, DataQualitySnapshot, QuarantineRecord


@admin.register(MarketCandle)
class MarketCandleAdmin(admin.ModelAdmin):
    list_display = (
        "instrument",
        "source",
        "timeframe",
        "timestamp_open",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "is_closed",
        "data_quality_flag",
    )
    list_filter = ("timeframe", "source", "data_quality_flag", "is_closed")
    search_fields = ("instrument__base_asset__code", "instrument__quote_asset__code", "source")
    ordering = ("-timestamp_open",)


@admin.register(DataQualitySnapshot)
class DataQualitySnapshotAdmin(admin.ModelAdmin):
    list_display = (
        "instrument",
        "timeframe",
        "timestamp",
        "quality_score",
        "gap_count",
        "duplicate_count",
        "violation_count",
        "is_stale",
        "hard_fail",
    )
    list_filter = ("timeframe", "hard_fail", "is_stale")
    search_fields = ("instrument__base_asset__code", "instrument__quote_asset__code")


@admin.register(QuarantineRecord)
class QuarantineRecordAdmin(admin.ModelAdmin):
    list_display = ("provider", "symbol", "basis_deviation", "is_active", "quarantined_at", "released_at")
    list_filter = ("provider", "is_active")
    search_fields = ("symbol", "reason")
