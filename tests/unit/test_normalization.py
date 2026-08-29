"""Unit tests for QuoteNormalizer and two-way stablecoin peg validation (R19 / A21)."""
from decimal import Decimal
import pytest
from apps.market_data.normalization import QuoteNormalizer


@pytest.mark.unit
def test_quote_normalizer_healthy_peg():
    """Verify normal peg within 0.50% threshold emits no warning and no hard fail."""
    normalizer = QuoteNormalizer()
    # Rate = 1.0010 (Deviation = 0.10%)
    res = normalizer.normalize_price(
        raw_price_usdt=Decimal("2500.00"),
        usdt_usd_rate=Decimal("1.001000"),
    )
    assert res.deviation == Decimal("0.001000")
    assert res.is_warning is False
    assert res.hard_fail is False
    assert res.normalized_price == Decimal("2502.50000000")
    assert "OK" in res.message


@pytest.mark.unit
def test_quote_normalizer_warning_peg():
    """Verify moderate peg deviation (>= 0.50% and < 2.00%) emits warning but not hard fail."""
    normalizer = QuoteNormalizer()
    # Rate = 0.9930 (Deviation = 0.70% >= 0.50%)
    res = normalizer.normalize_price(
        raw_price_usdt=Decimal("2500.00"),
        usdt_usd_rate=Decimal("0.993000"),
    )
    assert res.deviation == Decimal("0.007000")
    assert res.is_warning is True
    assert res.hard_fail is False
    assert res.normalized_price == Decimal("2482.50000000")
    assert "WARNING" in res.message


@pytest.mark.unit
def test_quote_normalizer_critical_depeg():
    """Verify severe peg de-anchoring (>= 2.00%) triggers hard_fail to block BUY_WINDOW."""
    normalizer = QuoteNormalizer()
    # Rate = 0.9750 (Deviation = 2.50% >= 2.00%)
    res = normalizer.normalize_price(
        raw_price_usdt=Decimal("2500.00"),
        usdt_usd_rate=Decimal("0.975000"),
    )
    assert res.deviation == Decimal("0.025000")
    assert res.is_warning is True
    assert res.hard_fail is True
    assert "CRITICAL" in res.message
