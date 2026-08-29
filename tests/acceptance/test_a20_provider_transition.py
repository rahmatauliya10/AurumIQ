"""Acceptance Test A20: Provider Transition Continuity."""
from decimal import Decimal
import pytest
from apps.market_data.integrity import ProviderContinuityVerifier


@pytest.mark.acceptance
def test_a20_provider_transition_continuity_lifecycle():
    """
    A20: Verify that provider failover flags source_switch=True, enforces FORCE_WAIT
    until 5-point criteria pass (3 healthy candles, basis <= 0.30%, normal spread, no bad ticks).
    """
    verifier = ProviderContinuityVerifier(
        max_basis_diff_pct=Decimal("0.0030"),
        max_spread_pct=Decimal("0.0015"),
        required_candles=3,
    )

    # Step 1: Initial switch trigger -> must force wait because candle count < 3
    res_step1 = verifier.verify_transition(
        old_provider_price=Decimal("2500.00"),
        new_provider_price=Decimal("2502.00"),  # 0.08% basis diff (within 0.30%)
        consecutive_healthy_candles=1,          # only 1 candle so far
        bid=Decimal("2501.50"),
        ask=Decimal("2502.50"),                 # spread = 1.00 / 2502.50 = 0.04% <= 0.15%
        has_bad_ticks=False,
        is_source_switch=True,
    )
    assert res_step1.source_switch is True
    assert res_step1.force_wait is True
    assert res_step1.is_verified is False

    # Step 2: Second candle received -> still must force wait (count = 2 < 3)
    res_step2 = verifier.verify_transition(
        old_provider_price=Decimal("2500.00"),
        new_provider_price=Decimal("2503.00"),
        consecutive_healthy_candles=2,
        bid=Decimal("2502.50"),
        ask=Decimal("2503.50"),
        has_bad_ticks=False,
        is_source_switch=True,
    )
    assert res_step2.force_wait is True
    assert res_step2.is_verified is False

    # Step 3: Bad tick occurs on new provider -> must force wait even if candle count reached
    res_step3_bad = verifier.verify_transition(
        old_provider_price=Decimal("2500.00"),
        new_provider_price=Decimal("2505.00"),
        consecutive_healthy_candles=3,
        bid=Decimal("2504.50"),
        ask=Decimal("2505.50"),
        has_bad_ticks=True,                     # bad tick anomaly
        is_source_switch=False,
    )
    assert res_step3_bad.force_wait is True
    assert res_step3_bad.is_verified is False

    # Step 4: 3 clean consecutive candles, good spread, no bad ticks, normal basis -> VERIFIED!
    res_step4_success = verifier.verify_transition(
        old_provider_price=Decimal("2500.00"),
        new_provider_price=Decimal("2504.00"),  # 0.16% basis
        consecutive_healthy_candles=3,
        bid=Decimal("2503.50"),
        ask=Decimal("2504.50"),                 # normal spread
        has_bad_ticks=False,
        is_source_switch=False,                 # transition finalized
    )
    assert res_step4_success.force_wait is False
    assert res_step4_success.is_verified is True
    assert len(res_step4_success.reasons) == 0
