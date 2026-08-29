"""Acceptance Test A17: XAUT/XAU Integrity Gate."""
from decimal import Decimal
import pytest
from apps.market_data.integrity import MarketIntegrityEngine


@pytest.mark.acceptance
def test_a17_xaut_xau_integrity_gate():
    """
    A17: Verify that severe unnormalized basis spike between XAUT (USD) and spot XAU (USD)
    exceeding 3.0% activates the hard fail gate to block BUY_WINDOW.
    """
    engine = MarketIntegrityEngine(max_xaut_xau_basis_pct=Decimal("0.0300"))

    # Case 1: Healthy normal basis (XAUT=2505.00, XAU=2500.00 -> Basis = 0.20% <= 3%)
    healthy_res = engine.verify_xaut_xau_basis(
        xaut_usd_price=Decimal("2505.00"),
        xau_usd_price=Decimal("2500.00"),
    )
    assert healthy_res.is_valid is True
    assert healthy_res.hard_fail is False
    assert healthy_res.basis_pct == Decimal("0.0020")

    # Case 2: Extreme abnormal basis spike (XAUT=2600.00, XAU=2500.00 -> Basis = 4.00% > 3%)
    spike_res = engine.verify_xaut_xau_basis(
        xaut_usd_price=Decimal("2600.00"),
        xau_usd_price=Decimal("2500.00"),
    )
    assert spike_res.is_valid is False
    assert spike_res.hard_fail is True
    assert spike_res.basis_pct == Decimal("0.0400")
    assert "A17 CRITICAL" in spike_res.message
