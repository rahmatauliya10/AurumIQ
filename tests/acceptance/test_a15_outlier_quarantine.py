"""Acceptance Test A15: Provider Outlier Quarantine."""
from decimal import Decimal
import pytest
from apps.market_data.integrity import MarketIntegrityEngine


@pytest.mark.acceptance
def test_a15_outlier_quarantine_filter():
    """
    A15: Verify that any provider whose price deviates > 0.50% from the
    multi-source median is flagged as QUARANTINED and excluded from valid providers.
    """
    engine = MarketIntegrityEngine(outlier_threshold_pct=Decimal("0.0050"))

    # Multi-source snapshot:
    # Binance: 2500.00, OKX: 2502.00, Kraken: 2499.50 -> Median ~ 2500.00
    # RogueFeed: 2520.00 (Deviation = (2520 - 2500)/2500 = 0.80% > 0.50%)
    provider_prices = {
        "binance": Decimal("2500.00"),
        "okx": Decimal("2502.00"),
        "kraken": Decimal("2499.50"),
        "rogue_exchange": Decimal("2520.00"),
    }

    result = engine.evaluate_provider_outliers(provider_prices)

    # Assertions
    assert "rogue_exchange" in result.quarantined_providers
    assert "rogue_exchange" not in result.valid_providers
    assert "binance" in result.valid_providers
    assert "okx" in result.valid_providers
    assert "kraken" in result.valid_providers

    assert result.deviations["rogue_exchange"] > Decimal("0.0050")  # > 0.50%
    assert result.deviations["binance"] <= Decimal("0.0050")
