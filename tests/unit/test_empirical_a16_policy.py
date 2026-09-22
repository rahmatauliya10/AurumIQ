from datetime import (
    datetime,
    timedelta,
    timezone,
)

import pytest

from engine.guards.empirical_a16 import (
    A16_EMPIRICAL_POLICY_SCHEMA,
    ObservationWindow,
    compute_empirical_a16_policy_fingerprint,
    compute_lag1_autocorrelation,
    compute_overlap_ratio,
    evaluate_empirical_a16,
)


BASE = datetime(
    2026, 1, 1,
    tzinfo=timezone.utc,
)


def _obs(
    start_minute: int,
    end_minute: int,
    value: float,
) -> ObservationWindow:
    return ObservationWindow(
        start=(
            BASE
            + timedelta(
                minutes=start_minute
            )
        ),
        end=(
            BASE
            + timedelta(
                minutes=end_minute
            )
        ),
        value=value,
        regime="UNKNOWN",
    )


def test_adjacent_windows_are_not_overlap():
    observations = [
        _obs(0, 15, 1.0),
        _obs(15, 30, 2.0),
        _obs(30, 45, 3.0),
    ]

    result = compute_overlap_ratio(
        observations
    )

    assert result.overlapping_count == 0
    assert result.overlap_ratio == 0.0


def test_true_temporal_overlap_is_measured():
    observations = [
        _obs(0, 20, 1.0),
        _obs(15, 30, 2.0),
        _obs(30, 45, 3.0),
    ]

    result = compute_overlap_ratio(
        observations
    )

    assert result.overlapping_count == 1
    assert result.overlap_ratio == pytest.approx(
        1.0 / 3.0,
        abs=1e-6,
    )


def test_lag1_autocorrelation_is_empirical():
    result = compute_lag1_autocorrelation(
        [1.0, 2.0, 3.0, 4.0, 5.0]
    )

    assert result is not None
    assert result == pytest.approx(
        1.0,
        abs=1e-12,
    )


def test_negative_autocorrelation_is_discounted_too():
    rho = compute_lag1_autocorrelation(
        [1.0, -1.0, 1.0, -1.0, 1.0]
    )

    assert rho is not None
    assert rho == pytest.approx(
        -1.0,
        abs=1e-12,
    )

    observations = [
        _obs(
            i * 15,
            (i + 1) * 15,
            1.0 if i % 2 == 0 else -1.0,
        )
        for i in range(125)
    ]

    result = evaluate_empirical_a16(
        observations
    )

    assert result.is_certified is True
    assert result.autocorrelation_factor == 1.0
    assert (
        result.evaluation.clustering_discount
        > 0.0
    )


def test_unknown_regime_is_fail_closed_concentration():
    values = (
        [1.0, 0.0, -1.0, 0.0]
        * 30
    )

    observations = [
        _obs(
            i * 15,
            (i + 1) * 15,
            value,
        )
        for i, value in enumerate(values)
    ]

    result = evaluate_empirical_a16(
        observations
    )

    assert result.is_certified is True

    assert result.regime_distribution == {
        "UNKNOWN": 120
    }

    assert (
        result.evaluation.hhi_norm
        == 1.0
    )

    assert (
        result.evaluation.regime_discount
        == 0.5
    )

    assert (
        result.evaluation.effective_n
        < result.evaluation.n_raw
    )


def test_zero_variance_fails_closed():
    observations = [
        _obs(
            i * 15,
            (i + 1) * 15,
            1.0,
        )
        for i in range(100)
    ]

    result = evaluate_empirical_a16(
        observations
    )

    assert result.is_certified is False
    assert result.evaluation is None
    assert (
        result.reason
        == "AUTOCORRELATION_NOT_IDENTIFIABLE"
    )


def test_policy_fingerprint_is_deterministic():
    first = (
        compute_empirical_a16_policy_fingerprint()
    )

    second = (
        compute_empirical_a16_policy_fingerprint()
    )

    assert first == second
    assert len(first) == 64

    assert A16_EMPIRICAL_POLICY_SCHEMA == (
        "aurumiq.a16.empirical_policy.v1"
    )
