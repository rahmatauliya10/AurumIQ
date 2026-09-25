"""
Unit tests for Phase 6 / Phase 8 XAUUSD Signal Calibration Selection Policy Governance.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import pytest

from engine.backtest.xauusd_calibration_policy import (
    DEFAULT_POLICY_PATH,
    XauUsdSignalCalibrationSelectionPolicy,
    compute_selection_policy_fingerprint,
    load_governed_selection_policy,
)
from engine.backtest.xauusd_types import XauUsdWalkForwardConfig
from engine.backtest.xauusd_walkforward import XauUsdChronologicalFoldGenerator


@pytest.fixture
def raw_policy_dict():
    """Load authoritative policy raw JSON dictionary."""
    with open(DEFAULT_POLICY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def selection_policy():
    """Load authoritative selection policy object (V1 baseline)."""
    return load_governed_selection_policy(DEFAULT_POLICY_PATH)


class TestPolicyArtifactIntegrity:
    """Artifact schema, fingerprint, and canonical serialization tests."""

    def test_01_policy_artifact_exists_and_loads(self, selection_policy):
        assert selection_policy is not None
        assert selection_policy.policy_id == "XAUUSD-SELECTION-POLICY-20260911"
        assert selection_policy.schema == "aurumiq.calibration.selection_policy.v1"
        assert selection_policy.instrument == "XAUUSD"
        assert selection_policy.base_code_revision == "0fe9c534c632921fa504136e69dc266a05baed82"
        assert selection_policy.decision_at == "2026-09-11T23:45:00Z"
        assert selection_policy.artifact_created_at == "2026-09-13T15:15:00Z"

    def test_01b_v2_policy_artifact_exists_and_loads(self):
        v2_pol = load_governed_selection_policy()
        assert v2_pol is not None
        assert v2_pol.policy_id == "XAUUSD-SELECTION-POLICY-20260924-V2"
        assert v2_pol.schema == "aurumiq.calibration.selection_policy.v2"
        assert v2_pol.code_revision == "b1cd4b04f4c073d7d8c06a9c1728477b52315d98"
        assert v2_pol.dataset_fingerprint == "547ec898e42ed0eff2992400d784e40c6b53c1d26b7a5e5722e0eb1128080242"
        assert v2_pol.required_timeframes == ("15m", "1h", "4h", "1d")
        assert v2_pol.cache_schema == "aurumiq.xauusd_market_cache.v2"
        assert v2_pol.cache_semantics == "v2_mtf_features"
        assert v2_pol.fold_assignment == "ALL_MATCHING_FOLDS"
        assert v2_pol.overall_trade_semantics == "UNIQUE_PHYSICAL_TRADES"

    def test_02_fingerprint_deterministic_and_key_order_invariant(self, raw_policy_dict):
        computed_fp = compute_selection_policy_fingerprint(raw_policy_dict)
        assert computed_fp == raw_policy_dict["policy_fingerprint"]

        # Re-order top-level keys
        reordered = {k: raw_policy_dict[k] for k in sorted(raw_policy_dict.keys(), reverse=True)}
        assert compute_selection_policy_fingerprint(reordered) == computed_fp

        # Mutating a rule alters fingerprint
        mutated = dict(raw_policy_dict)
        mutated["walk_forward_policy"] = dict(raw_policy_dict["walk_forward_policy"])
        mutated["walk_forward_policy"]["total_folds"] = 6
        assert compute_selection_policy_fingerprint(mutated) != computed_fp

    def test_03_xauusd_instrument_strict_enforcement(self, raw_policy_dict):
        mutated = dict(raw_policy_dict)
        mutated["instrument"] = "EURUSD"
        mutated["policy_fingerprint"] = compute_selection_policy_fingerprint(mutated)
        with pytest.raises(ValueError, match="Policy must target 'XAUUSD'"):
            XauUsdSignalCalibrationSelectionPolicy.from_dict(mutated)


class TestDataBoundariesAndIsolation:
    """Historical cutoff, Phase 8 quarantine, and data boundary tests."""

    def test_04_immutable_historical_cutoff(self, selection_policy):
        assert selection_policy.historical_start == datetime(2020, 4, 7, 0, 0, tzinfo=timezone.utc)
        assert selection_policy.historical_end_exclusive == datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
        assert selection_policy.total_duration_days == 2338.0

    def test_05_phase8_exclusion_and_quarantine_gap(self, selection_policy):
        p8_start = selection_policy.phase8_observation_window_start
        cutoff = selection_policy.historical_end_exclusive
        # Observation start must strictly postdate historical cutoff
        assert p8_start > cutoff
        gap = p8_start - cutoff
        # Quarantine gap is approximately 10.46 days
        assert gap.total_seconds() > 10 * 86400
        assert selection_policy.raw_payload["actual_phase8_boundary_reference"]["status"] == "QUARANTINED_EXCLUDED"


class TestWalkForwardAndEmbargoSemantics:
    """Walk-forward fold generation, OOS widths, and dynamic embargo tests."""

    def test_06_walk_forward_5_folds_deterministic_generation(self, selection_policy):
        assert selection_policy.total_folds == 5
        assert selection_policy.rolling_window is False
        assert selection_policy.train_ratio == 0.60
        assert selection_policy.val_ratio == 0.20
        assert selection_policy.oos_ratio == 0.20

        cfg = XauUsdWalkForwardConfig(
            total_folds=5,
            train_ratio=0.60,
            val_ratio=0.20,
            oos_ratio=0.20,
            embargo_seconds=0.0,
            rolling_window=False,
        )
        generated = XauUsdChronologicalFoldGenerator.generate_folds(
            selection_policy.historical_start,
            selection_policy.historical_end_exclusive,
            cfg,
        )
        assert len(generated) == 5

        for gen, declared in zip(generated, selection_policy.folds):
            assert gen.fold_id == declared["fold_id"]
            assert gen.train_start.isoformat() == declared["train_start"]
            assert gen.train_end.isoformat() == declared["train_end"]
            assert gen.val_start.isoformat() == declared["val_start"]
            assert gen.val_end.isoformat() == declared["val_end"]
            assert gen.oos_start.isoformat() == declared["oos_start"]
            assert gen.oos_end.isoformat() == declared["oos_end"]

    def test_07_actual_oos_duration_per_fold_matches_generator_semantics(self, selection_policy):
        # delta_oos = (total_duration * oos_ratio) / total_folds
        # 2338 * 0.20 / 5 = 93.52 days (~93-94 days per fold)
        assert selection_policy.oos_duration_per_fold_days == 93.52
        assert selection_policy.val_duration_per_fold_days == 467.60

        for f in selection_policy.folds:
            assert f["oos_duration_days"] == 93.52

    def test_08_dynamic_embargo_derivation(self, selection_policy):
        # 4 bars fill + 10 bars hold = 14 bars * 900s = 12,600s
        assert selection_policy.validate_dynamic_embargo(4, 10, 12600.0) is True
        assert selection_policy.validate_dynamic_embargo(4, 10, 12599.0) is False

        # Worst-case: 8 bars fill + 32 bars hold = 40 bars * 900s = 36,000s
        assert selection_policy.validate_dynamic_embargo(8, 32, 36000.0) is True
        assert selection_policy.validate_dynamic_embargo(8, 32, 35999.0) is False

        with pytest.raises(ValueError, match="bars are None"):
            selection_policy.validate_dynamic_embargo(None, 10, 12600.0)


class TestPromotionHurdlesAndQuality:
    """Sample quality tiers, expectancy confidence bound, drawdown, and stability tests."""

    def test_09_sample_quality_requirements_tiers(self, selection_policy):
        # BUY and SELL require MEDIUM quality (>= 60.0)
        assert selection_policy.buy_min_effective_n == 60.0
        assert selection_policy.sell_min_effective_n == 60.0
        # Combined requires HIGH quality (>= 100.0)
        assert selection_policy.combined_min_effective_n == 100.0
        assert selection_policy.single_side_promotion_authorized is False

    def test_10_expectancy_confidence_rule(self, selection_policy):
        assert selection_policy.confidence_level == 0.95
        assert selection_policy.alpha == 0.05

        # Test LCB calculation
        # Mean = 0.20, std = 1.0, N_eff = 100 -> t_0.95 approx 1.661 -> LCB > 0
        lcb = selection_policy.compute_effective_n_lcb_95(mean_r=0.20, std_r=1.0, effective_n=100.0)
        assert lcb > 0.0

        # Mean = 0.02, std = 1.0, N_eff = 60 -> t_0.95 approx 1.671 -> SE = 0.129 -> LCB < 0
        lcb_fail = selection_policy.compute_effective_n_lcb_95(mean_r=0.02, std_r=1.0, effective_n=60.0)
        assert lcb_fail < 0.0

    def test_11_drawdown_dual_governance(self, selection_policy):
        assert selection_policy.absolute_max_drawdown_r == 18.0
        assert selection_policy.max_drawdown_deterioration_pct == 10.0
        assert selection_policy.raw_payload["drawdown_governance"]["absolute_drawdown_provenance"] == "EXPLICIT_OWNER_CAPITAL_RISK_DECISION"
        assert selection_policy.raw_payload["drawdown_governance"]["relative_drawdown_provenance"] == "P3B_GOVERNANCE_PRIOR_ART_REUSED_BY_OWNER_DECISION"

    def test_12_temporal_stability_and_concentration(self, selection_policy):
        assert selection_policy.min_positive_folds == 4
        assert selection_policy.min_positive_folds_total == 5
        assert selection_policy.min_temporal_stability_score == 0.50
        assert selection_policy.max_single_fold_profit_concentration_pct == 60.0


class TestSearchBudgetAndAblationGovernance:
    """Candidate budget, Train/Val/OOS isolation, and ablation block comparisons."""

    def test_13_candidate_search_budget_and_overfit_control(self, selection_policy):
        assert selection_policy.max_candidate_evaluations == 100
        tvo = selection_policy.raw_payload["train_val_oos_isolation_policy"]
        assert tvo["same_oos_retuning_permitted"] is False
        assert tvo["second_best_selection_after_oos_permitted"] is False
        assert tvo["threshold_mutation_after_oos_permitted"] is False

    def test_14_ablation_policy_block_comparison(self, selection_policy):
        ab = selection_policy.raw_payload["ablation_policy"]
        assert ab["unit_of_comparison"] == "chronological_blocks_or_folds"
        assert ab["paired_individual_trades_permitted"] is False
        assert "NO_MACRO_BLACKOUT" in ab["macro_blackout_rule"]

    def test_15_phase6_friction_and_production_authority_freeze(self, selection_policy):
        fric = selection_policy.raw_payload["phase6_friction_reference"]
        assert fric["execution_gap_vs_reference_quote_points"] == 0.0
        assert fric["true_requested_price_slippage"] == "UNOBSERVABLE"
        assert fric["reference_spread_points"] == 260.0

        auth = selection_policy.raw_payload["production_authority_status"]
        assert auth["is_production_authorized"] is False
        assert auth["paper_only"] is True
        assert auth["real_order_execution"] == "disabled"
