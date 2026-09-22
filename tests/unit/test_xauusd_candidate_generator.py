"""
Unit tests for Phase 6 / Phase 8 XAUUSD Candidate Search-Space & Generation Governance.

Availability-Aware Recalibration Tests:
- Direction: 7 active weights sum to 100.0, weight_volume == 0.0
- Timing: 3 active weights sum to 100.0, weight_phase3a == 0.0, weight_volume_response == 0.0
- Baseline candidate 0: 7 equal active direction weights, 3 equal active timing weights
- Feature availability contract and provenance in policy artifact
- Simplex invariants, monotonic gates, deterministic reproducibility, candidate cap <= 100
"""
from pathlib import Path
import pytest

from engine.backtest.xauusd_candidate_generator import (
    DEFAULT_CANDIDATE_POLICY_PATH,
    XauUsdCandidateGenerationPolicy,
    XauUsdCandidateGenerator,
    compute_candidate_generation_policy_fingerprint,
    derive_deterministic_seed,
    load_governed_candidate_generation_policy,
)
from engine.signals.profile import (
    Phase4CalibrationStatus,
    compute_phase4_policy_fingerprint,
)


@pytest.fixture
def candidate_policy():
    """Load authoritative candidate generation policy object."""
    return load_governed_candidate_generation_policy()


@pytest.fixture
def candidate_generator(candidate_policy):
    """Instantiate candidate generator from authoritative policy."""
    return XauUsdCandidateGenerator(candidate_policy)


class TestCandidatePolicyArtifactIntegrity:
    """Artifact schema, fingerprint, and immutability tests."""

    def test_01_artifact_exists_and_loads(self, candidate_policy):
        assert candidate_policy is not None
        assert (
            candidate_policy.schema
            == "aurumiq.calibration.candidate_generation_policy.v1"
        )
        assert (
            candidate_policy.policy_id
            == "XAUUSD-CANDIDATE-GENERATION-POLICY-20260915"
        )
        assert candidate_policy.instrument == "XAUUSD"
        assert candidate_policy.candidate_cap == 100
        assert candidate_policy.auto_increase_permitted is False
        assert candidate_policy.buy_sell_independence is True

    def test_02_fingerprint_deterministic_and_key_order_invariant(
        self, candidate_policy
    ):
        payload = candidate_policy.raw_payload
        computed_fp = compute_candidate_generation_policy_fingerprint(payload)
        assert computed_fp == candidate_policy.policy_fingerprint

        # Re-order top-level keys
        reordered = {
            k: payload[k] for k in sorted(payload.keys(), reverse=True)
        }
        assert (
            compute_candidate_generation_policy_fingerprint(reordered)
            == computed_fp
        )

        # Mutating a rule alters fingerprint
        mutated = dict(payload)
        mutated["candidate_cap"] = 150
        assert (
            compute_candidate_generation_policy_fingerprint(mutated)
            != computed_fp
        )

    def test_03_rejection_of_invalid_instrument_or_schema(
        self, candidate_policy
    ):
        mutated = dict(candidate_policy.raw_payload)
        mutated["instrument"] = "EURUSD"
        mutated["policy_fingerprint"] = (
            compute_candidate_generation_policy_fingerprint(mutated)
        )
        with pytest.raises(ValueError, match="Policy must target 'XAUUSD'"):
            XauUsdCandidateGenerationPolicy.from_dict(mutated)


class TestSeedDerivationGovernance:
    """Deterministic seed derivation and sensitivity tests."""

    def test_04_seed_is_deterministic(self, candidate_policy):
        s1 = derive_deterministic_seed(
            candidate_policy.selection_policy_fingerprint_reference,
            candidate_policy.code_revision,
            candidate_policy.dataset_fingerprint_reference,
        )
        s2 = derive_deterministic_seed(
            candidate_policy.selection_policy_fingerprint_reference,
            candidate_policy.code_revision,
            candidate_policy.dataset_fingerprint_reference,
        )
        assert s1 == s2
        assert isinstance(s1, int)
        assert s1 > 0

    def test_05_altering_dataset_hash_changes_seed_and_candidates(
        self, candidate_policy
    ):
        base_seed = derive_deterministic_seed(
            candidate_policy.selection_policy_fingerprint_reference,
            candidate_policy.code_revision,
            candidate_policy.dataset_fingerprint_reference,
        )
        alt_dataset_hash = (
            "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        )
        alt_seed = derive_deterministic_seed(
            candidate_policy.selection_policy_fingerprint_reference,
            candidate_policy.code_revision,
            alt_dataset_hash,
        )
        assert base_seed != alt_seed

        gen_base = XauUsdCandidateGenerator(candidate_policy)
        gen_alt = XauUsdCandidateGenerator(
            candidate_policy, dataset_fingerprint=alt_dataset_hash
        )

        c0_base = gen_base.generate_candidate(1)
        c0_alt = gen_alt.generate_candidate(1)
        assert compute_phase4_policy_fingerprint(
            c0_base
        ) != compute_phase4_policy_fingerprint(c0_alt)


class TestCandidateSimplexAndMonotonicityInvariants:
    """Mathematical invariants of generated candidates."""

    def test_06_direction_weights_simplex_invariants(
        self, candidate_generator
    ):
        candidates = candidate_generator.generate_all_candidates(count=20)
        for c in candidates:
            # Long direction active components (6 components, regime & volume disabled)
            l_active = [
                c.long_direction.weight_trend_1h,
                c.long_direction.weight_trend_4h,
                c.long_direction.weight_trend_1d,
                c.long_direction.weight_structure_bos,
                c.long_direction.weight_pullback,
                c.long_direction.weight_momentum,
            ]
            assert all(w >= 0.0 for w in l_active)
            assert abs(sum(l_active) - 100.0) < 1e-4
            assert c.long_direction.weight_regime == 0.0
            assert c.long_direction.weight_volume == 0.0

            l_all = l_active + [
                c.long_direction.weight_regime,
                c.long_direction.weight_volume,
            ]
            assert abs(sum(l_all) - 100.0) < 1e-4

            # Short direction active components (6 components, regime & volume disabled)
            s_active = [
                c.short_direction.weight_trend_1h,
                c.short_direction.weight_trend_4h,
                c.short_direction.weight_trend_1d,
                c.short_direction.weight_structure_bos,
                c.short_direction.weight_pullback,
                c.short_direction.weight_momentum,
            ]
            assert all(w >= 0.0 for w in s_active)
            assert abs(sum(s_active) - 100.0) < 1e-4
            assert c.short_direction.weight_regime == 0.0
            assert c.short_direction.weight_volume == 0.0

            s_all = s_active + [
                c.short_direction.weight_regime,
                c.short_direction.weight_volume,
            ]
            assert abs(sum(s_all) - 100.0) < 1e-4

    def test_07_timing_weights_simplex_invariants(
        self, candidate_generator
    ):
        candidates = candidate_generator.generate_all_candidates(count=20)
        for c in candidates:
            # Long timing active components
            l_active = [
                c.long_timing.weight_entry_zone,
                c.long_timing.weight_reversal_confirmation_15m,
                c.long_timing.weight_momentum_turn_15m_1h,
            ]
            assert all(w >= 0.0 for w in l_active)
            assert abs(sum(l_active) - 100.0) < 1e-4
            assert c.long_timing.weight_phase3a == 0.0
            assert c.long_timing.weight_volume_response == 0.0

            l_all = l_active + [
                c.long_timing.weight_phase3a,
                c.long_timing.weight_volume_response,
            ]
            assert abs(sum(l_all) - 100.0) < 1e-4

            # Short timing active components
            s_active = [
                c.short_timing.weight_entry_zone,
                c.short_timing.weight_reversal_confirmation_15m,
                c.short_timing.weight_momentum_turn_15m_1h,
            ]
            assert all(w >= 0.0 for w in s_active)
            assert abs(sum(s_active) - 100.0) < 1e-4
            assert c.short_timing.weight_phase3a == 0.0
            assert c.short_timing.weight_volume_response == 0.0

            s_all = s_active + [
                c.short_timing.weight_phase3a,
                c.short_timing.weight_volume_response,
            ]
            assert abs(sum(s_all) - 100.0) < 1e-4

    def test_08_gate_monotonicity_invariants(self, candidate_generator):
        candidates = candidate_generator.generate_all_candidates(count=20)
        for c in candidates:
            # Long gate
            assert 0.0 <= c.long_gate.threshold_watch_direction <= 100.0
            assert 0.0 <= c.long_gate.threshold_ready_direction <= 100.0
            assert 0.0 <= c.long_gate.threshold_window_direction <= 100.0
            assert (
                c.long_gate.threshold_watch_direction
                <= c.long_gate.threshold_ready_direction
                <= c.long_gate.threshold_window_direction
            )
            assert (
                c.long_gate.threshold_ready_timing
                <= c.long_gate.threshold_window_timing
            )

            # Short gate
            assert 0.0 <= c.short_gate.threshold_watch_direction <= 100.0
            assert 0.0 <= c.short_gate.threshold_ready_direction <= 100.0
            assert 0.0 <= c.short_gate.threshold_window_direction <= 100.0
            assert (
                c.short_gate.threshold_watch_direction
                <= c.short_gate.threshold_ready_direction
                <= c.short_gate.threshold_window_direction
            )
            assert (
                c.short_gate.threshold_ready_timing
                <= c.short_gate.threshold_window_timing
            )

    def test_09_buy_and_sell_side_independence(self, candidate_generator):
        candidates = candidate_generator.generate_all_candidates(count=20)
        identical_pair_count = 0
        for c in candidates[1:]:  # skip baseline candidate 0
            if (
                c.long_direction.weight_trend_1h
                == c.short_direction.weight_trend_1h
                and c.long_timing.weight_entry_zone
                == c.short_timing.weight_entry_zone
                and c.long_gate.threshold_window_direction
                == c.short_gate.threshold_window_direction
            ):
                identical_pair_count += 1
        assert identical_pair_count == 0

    def test_10_equal_weight_baseline_candidate_0(self, candidate_generator):
        c0 = candidate_generator.generate_candidate(0)
        assert c0.details["is_baseline"] is True

        # Direction: 6 active components sum to 100.0 (~16.6667 each), regime == 0.0, volume == 0.0
        assert c0.long_direction.weight_regime == 0.0
        assert c0.short_direction.weight_regime == 0.0
        assert c0.long_direction.weight_volume == 0.0
        assert c0.short_direction.weight_volume == 0.0
        l_dir = [
            c0.long_direction.weight_trend_1h,
            c0.long_direction.weight_trend_4h,
            c0.long_direction.weight_trend_1d,
            c0.long_direction.weight_structure_bos,
            c0.long_direction.weight_pullback,
            c0.long_direction.weight_momentum,
        ]
        assert abs(sum(l_dir) - 100.0) < 1e-4
        assert all(16.0 <= w <= 17.5 for w in l_dir)

        s_dir = [
            c0.short_direction.weight_trend_1h,
            c0.short_direction.weight_trend_4h,
            c0.short_direction.weight_trend_1d,
            c0.short_direction.weight_structure_bos,
            c0.short_direction.weight_pullback,
            c0.short_direction.weight_momentum,
        ]
        assert abs(sum(s_dir) - 100.0) < 1e-4
        assert all(16.0 <= w <= 17.5 for w in s_dir)

        # Timing: 3 active components sum to 100.0 (~33.3333 each), disabled == 0.0
        assert c0.long_timing.weight_phase3a == 0.0
        assert c0.short_timing.weight_phase3a == 0.0
        assert c0.long_timing.weight_volume_response == 0.0
        assert c0.short_timing.weight_volume_response == 0.0
        l_tim = [
            c0.long_timing.weight_entry_zone,
            c0.long_timing.weight_reversal_confirmation_15m,
            c0.long_timing.weight_momentum_turn_15m_1h,
        ]
        assert abs(sum(l_tim) - 100.0) < 1e-4
        assert all(33.0 <= w <= 34.0 for w in l_tim)

        s_tim = [
            c0.short_timing.weight_entry_zone,
            c0.short_timing.weight_reversal_confirmation_15m,
            c0.short_timing.weight_momentum_turn_15m_1h,
        ]
        assert abs(sum(s_tim) - 100.0) < 1e-4
        assert all(33.0 <= w <= 34.0 for w in s_tim)

        # Monotonic gates
        assert (
            c0.long_gate.threshold_watch_direction
            <= c0.long_gate.threshold_ready_direction
            <= c0.long_gate.threshold_window_direction
        )
        assert c0.is_fully_configured is True


class TestGenerationBudgetAndDeduplication:
    """Candidate count cap and uniqueness guarantees."""

    def test_11_generate_exactly_100_distinct_candidates(
        self, candidate_generator
    ):
        candidates = candidate_generator.generate_all_candidates(count=100)
        assert len(candidates) == 100

        fingerprints = [
            compute_phase4_policy_fingerprint(c) for c in candidates
        ]
        unique_fingerprints = set(fingerprints)
        assert len(unique_fingerprints) == 100

    def test_12_reproducibility_identical_runs_match_exactly(
        self, candidate_generator
    ):
        run1 = candidate_generator.generate_all_candidates(count=25)
        run2 = candidate_generator.generate_all_candidates(count=25)

        for c1, c2 in zip(run1, run2):
            assert compute_phase4_policy_fingerprint(
                c1
            ) == compute_phase4_policy_fingerprint(c2)

    def test_13_budget_cap_enforcement(self, candidate_generator):
        with pytest.raises(ValueError, match="exceeds governed cap"):
            candidate_generator.generate_all_candidates(count=101)


class TestIsolationAndProductionAuthorityFreeze:
    """Safety and governance enforcement tests."""

    def test_14_production_authority_strictly_off(self, candidate_generator):
        candidates = candidate_generator.generate_all_candidates(count=10)
        for c in candidates:
            assert c.is_production_authorized is False
            assert (
                c.calibration_status
                == Phase4CalibrationStatus.CANDIDATE_NOT_FROZEN
            )

    def test_15_oos_exclusion_api_boundary(self, candidate_generator):
        import inspect
        sig = inspect.signature(candidate_generator.generate_all_candidates)
        params = list(sig.parameters.keys())
        assert "oos" not in params
        assert "outcomes" not in params
        assert "trades" not in params
        assert "metrics" not in params


class TestFeatureAvailabilityContract:
    """Contractual verification of structural and authority availability."""

    def test_16_feature_availability_policy_and_candidate_invariants(
        self, candidate_policy, candidate_generator
    ):
        fa = candidate_policy.feature_availability
        assert fa is not None

        # Direction availability
        assert fa["direction"]["weight_regime"] == "DISABLED_UNCALIBRATED"
        assert fa["direction"]["weight_volume"] == "DISABLED_STRUCTURAL"
        for k in [
            "weight_trend_1h", "weight_trend_4h",
            "weight_trend_1d", "weight_structure_bos", "weight_pullback",
            "weight_momentum",
        ]:
            assert fa["direction"][k] == "ACTIVE"

        # Timing availability
        assert fa["timing"]["weight_phase3a"] == "DISABLED_AUTHORITY_LOCK"
        assert fa["timing"]["weight_volume_response"] == "DISABLED_STRUCTURAL"
        for k in [
            "weight_entry_zone", "weight_reversal_confirmation_15m",
            "weight_momentum_turn_15m_1h",
        ]:
            assert fa["timing"][k] == "ACTIVE"

        # Provenance verification
        prov = fa["provenance"]
        assert prov["volume_evidence"] == "UNAVAILABLE on governed XAUUSD dataset"
        assert prov["phase3a_source"] == "xauusd_phase3a_candidate.json"
        assert prov["phase3a_status"] == "CANDIDATE_NOT_FROZEN"
        assert prov["phase3a_scoring_authority"] is False

        # Verify across generated candidates
        candidates = candidate_generator.generate_all_candidates(count=25)
        for candidate in candidates:
            # Regime direction = 0
            assert candidate.long_direction.weight_regime == 0.0
            assert candidate.short_direction.weight_regime == 0.0

            # Volume direction = 0
            assert candidate.long_direction.weight_volume == 0.0
            assert candidate.short_direction.weight_volume == 0.0

            # Phase3a timing = 0
            assert candidate.long_timing.weight_phase3a == 0.0
            assert candidate.short_timing.weight_phase3a == 0.0

            # Volume timing = 0
            assert candidate.long_timing.weight_volume_response == 0.0
            assert candidate.short_timing.weight_volume_response == 0.0

            # Direction sum = 100
            l_dir_weights = [
                candidate.long_direction.weight_regime,
                candidate.long_direction.weight_trend_1h,
                candidate.long_direction.weight_trend_4h,
                candidate.long_direction.weight_trend_1d,
                candidate.long_direction.weight_structure_bos,
                candidate.long_direction.weight_pullback,
                candidate.long_direction.weight_momentum,
                candidate.long_direction.weight_volume,
            ]
            s_dir_weights = [
                candidate.short_direction.weight_regime,
                candidate.short_direction.weight_trend_1h,
                candidate.short_direction.weight_trend_4h,
                candidate.short_direction.weight_trend_1d,
                candidate.short_direction.weight_structure_bos,
                candidate.short_direction.weight_pullback,
                candidate.short_direction.weight_momentum,
                candidate.short_direction.weight_volume,
            ]
            assert abs(sum(l_dir_weights) - 100.0) < 1e-4
            assert abs(sum(s_dir_weights) - 100.0) < 1e-4

            # Timing sum = 100
            l_tim_weights = [
                candidate.long_timing.weight_entry_zone,
                candidate.long_timing.weight_reversal_confirmation_15m,
                candidate.long_timing.weight_momentum_turn_15m_1h,
                candidate.long_timing.weight_phase3a,
                candidate.long_timing.weight_volume_response,
            ]
            s_tim_weights = [
                candidate.short_timing.weight_entry_zone,
                candidate.short_timing.weight_reversal_confirmation_15m,
                candidate.short_timing.weight_momentum_turn_15m_1h,
                candidate.short_timing.weight_phase3a,
                candidate.short_timing.weight_volume_response,
            ]
            assert abs(sum(l_tim_weights) - 100.0) < 1e-4
            assert abs(sum(s_tim_weights) - 100.0) < 1e-4
