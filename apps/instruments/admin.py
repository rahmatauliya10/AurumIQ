"""Django Admin registration for instruments domain."""
from django.contrib import admin
from apps.instruments.models import Asset, Instrument, MarketListing, ProviderHealthSnapshot


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "asset_type", "created_at")
    search_fields = ("code", "name")
    list_filter = ("asset_type",)


@admin.register(Instrument)
class InstrumentAdmin(admin.ModelAdmin):
    list_display = ("__str__", "base_asset", "quote_asset", "instrument_type", "role", "is_active")
    list_filter = ("instrument_type", "role", "is_active")
    search_fields = ("base_asset__code", "quote_asset__code")


@admin.register(MarketListing)
class MarketListingAdmin(admin.ModelAdmin):
    list_display = ("provider", "provider_symbol", "instrument", "status", "fallback_priority", "updated_at")
    list_filter = ("provider", "status", "fallback_priority")
    search_fields = ("provider_symbol", "provider", "instrument__base_asset__code")


@admin.register(ProviderHealthSnapshot)
class ProviderHealthSnapshotAdmin(admin.ModelAdmin):
    list_display = ("listing", "status", "latency_ms", "consecutive_failures", "checked_at")
    list_filter = ("status", "listing__provider")
    search_fields = ("listing__provider_symbol", "reason")
    readonly_fields = ("checked_at",)
