"""Targeted unit tests verifying XAUUSD operational defaults, provider registry boundaries, and health task behavior."""
import inspect
from unittest.mock import MagicMock, patch
import pytest
from apps.market_data.tasks import (
    check_provider_health_task,
    ingest_primary_candles,
    ingest_resolution_candles,
)
from apps.market_data.providers.registry import registry, ProviderRegistry
from apps.market_data.providers.twelve_data import TwelveDataProvider
from apps.market_data.providers.xauusd_secondary import SecondaryXauUsdSpotProvider
from apps.market_data.providers.binance import BinanceProvider
from apps.market_data.providers.okx import OKXProvider
from apps.market_data.providers.gold_reference import GoldReferenceProvider
from apps.market_data.providers.usdt_usd import UsdtUsdRateProvider


@pytest.mark.unit
def test_ingestion_default_symbols_are_xauusd():
    """Verify default signature parameter for primary and resolution ingestion is XAU/USD."""
    primary_sig = inspect.signature(ingest_primary_candles)
    assert primary_sig.parameters["instrument_symbol"].default == "XAU/USD"

    resolution_sig = inspect.signature(ingest_resolution_candles)
    assert resolution_sig.parameters["instrument_symbol"].default == "XAU/USD"


@pytest.mark.unit
def test_default_registry_contains_only_current_xauusd_providers():
    """Verify default global registry contains only active TwelveData and Secondary XAUUSD providers."""
    # Active XAUUSD providers present
    assert registry.has("twelve_data_xauusd") is True
    assert registry.has("xauusd_secondary") is True

    p1 = registry.get("twelve_data_xauusd")
    assert isinstance(p1, TwelveDataProvider)

    p2 = registry.get("xauusd_secondary")
    assert isinstance(p2, SecondaryXauUsdSpotProvider)

    # Legacy providers NOT registered by default
    legacy_ids = ["binance", "okx", "gold_reference", "usdt_usd"]
    for lid in legacy_ids:
        assert registry.has(lid) is False
        with pytest.raises(KeyError):
            registry.get(lid)


@pytest.mark.unit
def test_legacy_providers_retained_and_can_be_explicitly_registered():
    """Verify legacy provider classes can be instantiated and explicitly registered in isolated registry."""
    custom_reg = ProviderRegistry()
    binance = BinanceProvider()
    okx = OKXProvider()
    gold_ref = GoldReferenceProvider()
    usdt_usd = UsdtUsdRateProvider()

    for p in [binance, okx, gold_ref, usdt_usd]:
        custom_reg.register(p)
        assert custom_reg.has(p.provider_id) is True
        assert custom_reg.get(p.provider_id) == p


@pytest.mark.unit
@pytest.mark.django_db
def test_check_provider_health_task_probes_only_active_providers():
    """Verify check_provider_health_task probes only active registry providers and excludes legacy providers."""
    with patch.object(TwelveDataProvider, "health_check") as mock_p1_health, \
         patch.object(SecondaryXauUsdSpotProvider, "health_check") as mock_p2_health, \
         patch.object(BinanceProvider, "health_check") as mock_binance_health, \
         patch.object(OKXProvider, "health_check") as mock_okx_health:

        mock_health_obj = MagicMock()
        mock_health_obj.status = "HEALTHY"
        mock_health_obj.latency_ms = 42
        mock_health_obj.error_message = None

        mock_p1_health.return_value = mock_health_obj
        mock_p2_health.return_value = mock_health_obj

        result = check_provider_health_task()

        assert result["status"] == "success"
        probed_providers = result["providers"]

        # Only active registered providers are probed
        assert "twelve_data_xauusd" in probed_providers
        assert "xauusd_secondary" in probed_providers
        assert mock_p1_health.called
        assert mock_p2_health.called

        # Legacy providers are NOT probed by default
        assert "binance" not in probed_providers
        assert "okx" not in probed_providers
        assert "gold_reference" not in probed_providers
        assert "usdt_usd" not in probed_providers
        assert not mock_binance_health.called
        assert not mock_okx_health.called


@pytest.mark.unit
def test_active_registry_module_does_not_expose_legacy_providers_or_dead_helpers():
    """Verify active registry module is clean and does not import legacy providers or dead helpers."""
    import apps.market_data.providers.registry as reg_mod
    for dead_attr in [
        "BinanceProvider",
        "OKXProvider",
        "GoldReferenceProvider",
        "UsdtUsdRateProvider",
        "XauUsdSpotProvider",
        "get_configured_gold_reference_url",
    ]:
        assert not hasattr(reg_mod, dead_attr), f"Active registry still exposes {dead_attr}"


@pytest.mark.unit
@pytest.mark.django_db
def test_explicit_historical_xaut_fails_closed_without_legacy_provider_registration():
    """Verify explicit XAUT request fails closed deterministically when legacy provider is not registered."""
    from django.core.management import call_command
    call_command("seed_instruments")

    # Ensure legacy providers are NOT registered
    assert registry.has("binance") is False
    assert registry.has("usdt_usd") is False

    # Explicit XAUT ingestion must fail closed deterministically, not raise an uncaught KeyError
    res = ingest_primary_candles(instrument_symbol="XAUT/USDT")
    assert res["status"] == "error"
    assert res["reason"] == "LEGACY_PROVIDER_NOT_REGISTERED"
    assert "not registered in active registry" in res["message"]
