"""
Unit & Governance Tests for Phase 3A Five Chronological Folds and Temporal Stability.

Mandate:
- total_folds = 5
- boundary semantics = half-open [start, end)
- outcome-independent equal duration folds
- strict full-window observation containment
- fold expectancy = standardized mean_return / std_dev
- governed stability formula = 1 - (std_exp / (abs(mean_exp) + 1))
- positive fold rule = positive_folds >= 4 and covered_folds == 5
- stability rule = stability_score >= 0.50
- temporal stability passed = positive_fold_rule_passed and stability_rule_passed
- strict future-fold PIT isolation
"""
from datetime import datetime, timedelta, timezone
import math
import pytest

from engine.guards.empirical_a16 import ObservationWindow
from engine.cycles.fold_stability import (
    PHASE3A_FOLD_STABILITY_SCHEMA,
    ChronologicalFold,
    FoldEvidence,
    TemporalStabilityResult,
    build_equal_duration_folds,
    compute_phase3a_fold_stability_policy_fingerprint,
    evaluate_temporal_stability,
    observations_for_fold,
)


def _sample_window(
    start: datetime,
    duration_minutes: int,
    value: float,
    regime: str = "UNKNOWN",
) -> ObservationWindow:
    return ObservationWindow(
        start=start,
        end=start + timedelta(minutes=duration_minutes),
        value=value,
        regime=regime,
    )


def test_five_folds_are_contiguous_non_overlapping():
    start = datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)

    folds = build_equal_duration_folds(start, end, total_folds=5)

    assert len(folds) == 5
    assert folds[0].start == start
    assert folds[4].end == end

    for i in range(4):
        assert folds[i].end == folds[i + 1].start
        assert folds[i].start < folds[i].end

    # All fold IDs are 1-indexed sequential
    assert [f.fold_id for f in folds] == [1, 2, 3, 4, 5]


def test_fold_boundaries_are_half_open():
    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 6, 0, 0, tzinfo=timezone.utc)
    folds = build_equal_duration_folds(start, end, total_folds=5)

    fold1 = folds[0]  # [2026-01-01, 2026-01-02)
    fold2 = folds[1]  # [2026-01-02, 2026-01-03)

    # Observation starting at fold1 start, ending within fold1
    obs_start = _sample_window(fold1.start, 15, 0.01)
    assert obs_start in observations_for_fold([obs_start], fold1)

    # Observation ending exactly at fold1 end
    obs_touching_end = ObservationWindow(
        start=fold1.end - timedelta(minutes=15),
        end=fold1.end,
        value=0.01,
    )
    assert obs_touching_end in observations_for_fold([obs_touching_end], fold1)
    assert obs_touching_end not in observations_for_fold([obs_touching_end], fold2)

    # Observation starting exactly at fold2 start (which is fold1 end)
    obs_at_fold2_start = ObservationWindow(
        start=fold2.start,
        end=fold2.start + timedelta(minutes=15),
        value=0.01,
    )
    assert obs_at_fold2_start not in observations_for_fold([obs_at_fold2_start], fold1)
    assert obs_at_fold2_start in observations_for_fold([obs_at_fold2_start], fold2)


def test_observation_crossing_fold_boundary_is_excluded():
    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 6, 0, 0, tzinfo=timezone.utc)
    folds = build_equal_duration_folds(start, end, total_folds=5)

    fold1 = folds[0]
    fold2 = folds[1]

    # Starts 5 minutes before fold1 ends, ends 10 minutes into fold2
    cross_obs = ObservationWindow(
        start=fold1.end - timedelta(minutes=5),
        end=fold1.end + timedelta(minutes=10),
        value=0.05,
    )

    in_fold1 = observations_for_fold([cross_obs], fold1)
    in_fold2 = observations_for_fold([cross_obs], fold2)

    assert len(in_fold1) == 0
    assert len(in_fold2) == 0


def _build_certified_fold_observations(
    fold: ChronologicalFold,
    mean_val: float,
    count: int = 30,
    fluctuation: float = 0.005,
) -> list[ObservationWindow]:
    """
    Generate non-overlapping observation windows within fold with alternating values
    to ensure non-zero variance and non-zero autocorrelation so A16 certifies.
    """
    duration = (fold.end - fold.start) / (count + 2)
    obs = []
    for i in range(count):
        o_start = fold.start + duration * (i + 1)
        o_end = o_start + duration * 0.5
        # Alternating fluctuation around mean_val
        val = mean_val + (fluctuation if i % 2 == 0 else -fluctuation)
        obs.append(
            ObservationWindow(
                start=o_start,
                end=o_end,
                value=val,
                regime="UNKNOWN",
            )
        )
    return obs


def test_four_of_five_positive_folds_pass_positive_fold_rule():
    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 6, 0, 0, tzinfo=timezone.utc)
    folds = build_equal_duration_folds(start, end, total_folds=5)

    # Folds 1-4 positive (+0.02), Fold 5 negative (-0.02)
    observations = []
    for i, fold in enumerate(folds):
        mean = 0.02 if i < 4 else -0.02
        observations.extend(_build_certified_fold_observations(fold, mean))

    result = evaluate_temporal_stability(observations, folds)

    assert result.fold_count == 5
    assert result.covered_fold_count == 5
    assert result.positive_fold_count == 4
    assert result.positive_fold_rule_passed is True


def test_three_of_five_positive_folds_fail():
    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 6, 0, 0, tzinfo=timezone.utc)
    folds = build_equal_duration_folds(start, end, total_folds=5)

    # Folds 1-3 positive (+0.02), Folds 4-5 negative (-0.02)
    observations = []
    for i, fold in enumerate(folds):
        mean = 0.02 if i < 3 else -0.02
        observations.extend(_build_certified_fold_observations(fold, mean))

    result = evaluate_temporal_stability(observations, folds)

    assert result.covered_fold_count == 5
    assert result.positive_fold_count == 3
    assert result.positive_fold_rule_passed is False
    assert result.temporal_stability_passed is False


def test_temporal_stability_formula_exact():
    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 6, 0, 0, tzinfo=timezone.utc)
    folds = build_equal_duration_folds(start, end, total_folds=5)

    # Supply folds with identical positive distributions
    observations = []
    for fold in folds:
        observations.extend(_build_certified_fold_observations(fold, 0.02))

    result = evaluate_temporal_stability(observations, folds)

    assert result.covered_fold_count == 5
    assert result.positive_fold_count == 5
    assert result.positive_fold_rule_passed is True

    # Identical expectancies -> std = 0 -> stability = 1.0
    assert result.std_fold_expectancy == pytest.approx(0.0, abs=1e-6)
    assert result.stability_score == pytest.approx(1.0, abs=1e-6)
    assert result.stability_rule_passed is True
    assert result.temporal_stability_passed is True


def test_missing_fold_fails_closed():
    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 6, 0, 0, tzinfo=timezone.utc)
    folds = build_equal_duration_folds(start, end, total_folds=5)

    # Only supply observations for folds 1-4, fold 5 has 0 observations
    observations = []
    for i in range(4):
        observations.extend(_build_certified_fold_observations(folds[i], 0.02))

    result = evaluate_temporal_stability(observations, folds)

    assert result.covered_fold_count == 4
    assert result.positive_fold_rule_passed is False
    assert result.stability_score is None
    assert result.stability_rule_passed is False
    assert result.temporal_stability_passed is False


def test_fold_a16_uses_actual_effective_n():
    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 6, 0, 0, tzinfo=timezone.utc)
    folds = build_equal_duration_folds(start, end, total_folds=5)

    observations = []
    for fold in folds:
        observations.extend(_build_certified_fold_observations(fold, 0.02, count=30))

    result = evaluate_temporal_stability(observations, folds)

    for fold_ev in result.folds:
        assert fold_ev.raw_n == 30
        assert fold_ev.a16_certified is True
        # Effective N is measured, positive, and <= raw N
        assert 0.0 < fold_ev.effective_n <= 30.0


def test_fold_significance_uses_effective_n():
    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 6, 0, 0, tzinfo=timezone.utc)
    folds = build_equal_duration_folds(start, end, total_folds=5)

    # Fold 1 with tiny samples (raw count 5 -> effective N small -> not significant)
    # Fold 2 with large samples (raw count 100 -> effective N high -> significant)
    obs_fold1 = _build_certified_fold_observations(folds[0], 0.015, count=5, fluctuation=0.03)
    obs_fold2 = _build_certified_fold_observations(folds[1], 0.015, count=100, fluctuation=0.03)
    obs_rest = []
    for f in folds[2:]:
        obs_rest.extend(_build_certified_fold_observations(f, 0.02, count=30))

    result = evaluate_temporal_stability(obs_fold1 + obs_fold2 + obs_rest, folds)

    # Fold 1 cannot be statistically significant with small effective N
    assert result.folds[0].significant_positive is False
    # Fold 2 with large sample count and positive edge is significant
    assert result.folds[1].significant_positive is True


def test_policy_fingerprint_is_deterministic():
    fp1 = compute_phase3a_fold_stability_policy_fingerprint()
    fp2 = compute_phase3a_fold_stability_policy_fingerprint()

    assert fp1 == fp2
    assert len(fp1) == 64
    assert PHASE3A_FOLD_STABILITY_SCHEMA == "aurumiq.phase3a.fold_stability.v1"


def test_future_fold_mutation_does_not_change_prior_fold():
    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 6, 0, 0, tzinfo=timezone.utc)
    folds = build_equal_duration_folds(start, end, total_folds=5)

    base_obs = []
    for f in folds:
        base_obs.extend(_build_certified_fold_observations(f, 0.02, count=30))

    before = evaluate_temporal_stability(base_obs, folds)

    # Mutate ONLY fold 5
    mutated_obs = [o for o in base_obs if o.end <= folds[4].start]
    mutated_obs.extend(_build_certified_fold_observations(folds[4], -0.05, count=30))

    after = evaluate_temporal_stability(mutated_obs, folds)

    # Prior folds 1-4 must be bit-for-bit identical
    for i in range(4):
        assert before.folds[i] == after.folds[i]

    # Fold 5 reflects the mutation
    assert before.folds[4] != after.folds[4]


def test_reversed_input_is_deterministic():
    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 6, 0, 0, tzinfo=timezone.utc)
    folds = build_equal_duration_folds(start, end, total_folds=5)

    observations = []
    for f in folds:
        observations.extend(_build_certified_fold_observations(f, 0.02, count=30))

    result_forward = evaluate_temporal_stability(observations, folds)
    result_reversed = evaluate_temporal_stability(list(reversed(observations)), folds)

    assert result_forward == result_reversed
