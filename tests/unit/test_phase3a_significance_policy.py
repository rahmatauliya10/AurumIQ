import pytest

from engine.guards.statistical_significance import (
    PHASE3A_SIGNIFICANCE_SCHEMA,
    compute_phase3a_significance_policy_fingerprint,
    evaluate_positive_mean_significance,
)


def test_significance_uses_effective_n():
    low = evaluate_positive_mean_significance(
        mean_return=0.02,
        std_dev=0.10,
        effective_n=25.0,
    )

    high = evaluate_positive_mean_significance(
        mean_return=0.02,
        std_dev=0.10,
        effective_n=100.0,
    )

    assert low.is_significant is False
    assert high.is_significant is True

    assert low.lcb_95 < 0.0
    assert high.lcb_95 > 0.0


def test_significance_numerical_fixture_n100():
    result = evaluate_positive_mean_significance(
        mean_return=0.02,
        std_dev=0.10,
        effective_n=100.0,
    )

    # Existing governed Phase-6 t approximation:
    # df = 99 -> tcrit = 1.671
    # SE = 0.10 / sqrt(100) = 0.01
    # LCB = 0.02 - 1.671*0.01
    #     = 0.00329
    assert result.t_critical == pytest.approx(
        1.671
    )

    assert result.standard_error == pytest.approx(
        0.01
    )

    assert result.lcb_95 == pytest.approx(
        0.00329
    )

    assert result.is_significant is True


def test_significance_decision_uses_unrounded_lcb():
    effective_n = 100.0
    std_dev = 0.10
    t_critical = 1.671
    boundary = (
        t_critical
        * std_dev
        / (effective_n ** 0.5)
    )

    result = evaluate_positive_mean_significance(
        mean_return=(
            boundary + 0.00003
        ),
        std_dev=std_dev,
        effective_n=effective_n,
    )

    assert result.lcb_95 is not None

    # Raw LCB is slightly positive (+0.00003).
    # 4-decimal rounding would produce 0.0000, but unrounded LCB > 0 is True.
    assert result.is_significant is True


def test_tiny_positive_lcb_remains_significant():
    effective_n = 100.0
    std_dev = 0.10
    t_critical = 1.671

    boundary = (
        t_critical
        * std_dev
        / (effective_n ** 0.5)
    )

    result = evaluate_positive_mean_significance(
        mean_return=(
            boundary + 0.000001
        ),
        std_dev=std_dev,
        effective_n=effective_n,
    )

    assert result.is_significant is True


def test_tiny_negative_lcb_is_not_significant():
    effective_n = 100.0
    std_dev = 0.10
    t_critical = 1.671

    boundary = (
        t_critical
        * std_dev
        / (effective_n ** 0.5)
    )

    result = evaluate_positive_mean_significance(
        mean_return=(
            boundary - 0.000001
        ),
        std_dev=std_dev,
        effective_n=effective_n,
    )

    assert result.is_significant is False


def test_negative_or_zero_edge_cannot_be_positive_significant():
    negative = evaluate_positive_mean_significance(
        mean_return=-0.01,
        std_dev=0.10,
        effective_n=100.0,
    )

    zero = evaluate_positive_mean_significance(
        mean_return=0.0,
        std_dev=0.10,
        effective_n=100.0,
    )

    assert negative.is_significant is False
    assert zero.is_significant is False


def test_zero_variance_fails_closed():
    result = evaluate_positive_mean_significance(
        mean_return=0.01,
        std_dev=0.0,
        effective_n=100.0,
    )

    assert result.is_significant is False
    assert result.lcb_95 is None
    assert result.reason == (
        "STANDARD_DEVIATION_NOT_IDENTIFIABLE"
    )


def test_invalid_effective_n_fails_closed():
    result = evaluate_positive_mean_significance(
        mean_return=0.01,
        std_dev=0.10,
        effective_n=1.0,
    )

    assert result.is_significant is False
    assert result.lcb_95 is None
    assert result.reason == (
        "EFFECTIVE_N_INSUFFICIENT_FOR_INFERENCE"
    )


def test_significance_policy_fingerprint_deterministic():
    first = (
        compute_phase3a_significance_policy_fingerprint()
    )
    second = (
        compute_phase3a_significance_policy_fingerprint()
    )

    assert first == second
    assert len(first) == 64

    assert PHASE3A_SIGNIFICANCE_SCHEMA == (
        "aurumiq.phase3a."
        "significance_policy.v1"
    )
