"""
Unit tests for the empirical XAUUSD calibration runner.

Verifies the 5 strict governance invariants:
1. Runner source code has zero hardcoded empirical metrics (MDD, candidate results, OOS results, manual fold expectancies).
2. Candidate 0 baseline MDD is dynamically derived from actual validation evaluation.
3. Champion selection is not hardcoded to index 0, but dynamically ranked from validation metrics.
4. Champion selection strictly rejects any OOS metrics (isolation / zero data leakage).
5. OOS evaluation requires explicit champion locking before execution.
"""
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from engine.backtest.xauusd_calibration_policy import load_governed_selection_policy
from scripts.run_xauusd_calibration import (
    check_structural_reachability,
    evaluate_candidate_0_baseline,
    select_champion,
    lock_champion,
    evaluate_champion_oos,
)


ROOT = Path(__file__).resolve().parent.parent.parent
RUNNER_SCRIPT = ROOT / "scripts" / "run_xauusd_calibration.py"


def test_01_runner_source_has_no_hardcoded_empirical_metrics():
    """
    Test 1: Guard against hardcoded empirical metrics in the calibration runner.
    Ensures zero fabricated/hardcoded numbers remain in run_xauusd_calibration.py.
    """
    assert RUNNER_SCRIPT.exists(), f"Runner script not found at {RUNNER_SCRIPT}"
    source = RUNNER_SCRIPT.read_text(encoding="utf-8")

    # Hardcoded baseline / results guards
    assert "baseline_mdd_r = 9.42" not in source, "Found hardcoded baseline_mdd_r = 9.42"
    assert "val_cand0_results = {" not in source, "Found hardcoded val_cand0_results dict"
    assert "oos_results = {" not in source, "Found hardcoded oos_results dict"
    assert 'champion_id = "XAUUSD_CANDIDATE_000"' not in source, "Found hardcoded champion_id"
    assert "champion_candidate = cand0" not in source, "Found hardcoded champion_candidate = cand0"

    # Manual fold expectancies guards
    forbidden_expectancies = ["+0.18", "+0.22", "+0.15", "+0.24", "+0.19"]
    for exp in forbidden_expectancies:
        assert exp not in source, f"Found manual fold expectancy string '{exp}' in runner source"


def test_02_candidate_0_baseline_derived_from_evaluator():
    """
    Test 2: Candidate 0 baseline MDD must be derived from the actual validation evaluator,
    never from a hardcoded constant.
    """
    policy = load_governed_selection_policy()
    mock_candidate_0 = MagicMock()

    # Case A: Evaluator reports 11.25 R max drawdown
    def mock_evaluator_a(dataset, cand, pol, cost_cfg=None, runner=None, relative_mdd_ceiling=None):
        return {
            "candidate_id": "XAUUSD_CANDIDATE_000",
            "index": 0,
            "val_max_drawdown_r": 11.25,
            "val_lcb_95": 0.05,
            "qualified": True,
        }

    base_mdd_a, ceiling_a, metrics_a = evaluate_candidate_0_baseline(
        dataset=None,
        candidate_0=mock_candidate_0,
        policy=policy,
        evaluator_fn=mock_evaluator_a,
    )
    assert base_mdd_a == 11.25
    expected_ceiling_a = min(
        policy.absolute_max_drawdown_r,
        11.25 * (1.0 + policy.max_drawdown_deterioration_pct / 100.0),
    )
    assert ceiling_a == pytest.approx(expected_ceiling_a, rel=1e-6)
    assert metrics_a["val_max_drawdown_r"] == 11.25

    # Case B: Evaluator reports 7.80 R max drawdown (dynamically adapts)
    def mock_evaluator_b(dataset, cand, pol, cost_cfg=None, runner=None, relative_mdd_ceiling=None):
        return {
            "candidate_id": "XAUUSD_CANDIDATE_000",
            "index": 0,
            "val_max_drawdown_r": 7.80,
            "val_lcb_95": 0.02,
            "qualified": True,
        }

    base_mdd_b, ceiling_b, metrics_b = evaluate_candidate_0_baseline(
        dataset=None,
        candidate_0=mock_candidate_0,
        policy=policy,
        evaluator_fn=mock_evaluator_b,
    )
    assert base_mdd_b == 7.80
    expected_ceiling_b = min(
        policy.absolute_max_drawdown_r,
        7.80 * (1.0 + policy.max_drawdown_deterioration_pct / 100.0),
    )
    assert ceiling_b == pytest.approx(expected_ceiling_b, rel=1e-6)
    assert base_mdd_b != base_mdd_a


def test_03_champion_selection_is_not_hardcoded_to_index_0():
    """
    Test 3: Champion selection ranks candidates strictly on VAL metrics,
    and must pick candidate with highest val_lcb_95 (not hardcoded index 0).
    """
    candidate_0 = {
        "candidate_id": "XAUUSD_CANDIDATE_000",
        "index": 0,
        "val_lcb_95": 0.0450,
        "val_max_drawdown_r": 9.50,
        "buy_effective_n": 75.0,
        "sell_effective_n": 80.0,
        "combined_effective_n": 155.0,
        "qualified": True,
    }
    candidate_42 = {
        "candidate_id": "XAUUSD_CANDIDATE_042",
        "index": 42,
        "val_lcb_95": 0.1280,  # Significantly higher LCB95
        "val_max_drawdown_r": 8.10,
        "buy_effective_n": 95.0,
        "sell_effective_n": 105.0,
        "combined_effective_n": 200.0,
        "qualified": True,
    }
    candidate_88 = {
        "candidate_id": "XAUUSD_CANDIDATE_088",
        "index": 88,
        "val_lcb_95": 0.0920,
        "val_max_drawdown_r": 6.50,
        "buy_effective_n": 65.0,
        "sell_effective_n": 70.0,
        "combined_effective_n": 135.0,
        "qualified": True,
    }

    champion = select_champion([candidate_0, candidate_42, candidate_88])
    assert champion is not None
    assert champion["index"] == 42
    assert champion["candidate_id"] == "XAUUSD_CANDIDATE_042"
    assert champion["val_lcb_95"] == 0.1280

    # Test deterministic tie-breaking (equal LCB95 -> lower drawdown wins)
    cand_tie_1 = {
        "candidate_id": "CAND_TIE_1",
        "index": 10,
        "val_lcb_95": 0.1000,
        "val_max_drawdown_r": 10.0,
        "qualified": True,
    }
    cand_tie_2 = {
        "candidate_id": "CAND_TIE_2",
        "index": 20,
        "val_lcb_95": 0.1000,
        "val_max_drawdown_r": 7.5,  # Better drawdown
        "qualified": True,
    }
    tie_champion = select_champion([cand_tie_1, cand_tie_2])
    assert tie_champion["index"] == 20


def test_04_selection_strictly_rejects_oos_metrics():
    """
    Test 4: Selection function must raise an error if any candidate contains OOS metrics.
    Ensures zero data leakage and strict VAL-only ranking.
    """
    candidate_with_oos = {
        "candidate_id": "XAUUSD_CANDIDATE_015",
        "index": 15,
        "val_lcb_95": 0.1500,
        "val_max_drawdown_r": 7.0,
        "oos_mean_r": 0.25,  # Leakage!
        "qualified": True,
    }
    with pytest.raises(ValueError, match="OOS_METRICS_LEAKAGE"):
        select_champion([candidate_with_oos])

    candidate_with_oos_lcb = {
        "candidate_id": "XAUUSD_CANDIDATE_016",
        "index": 16,
        "val_lcb_95": 0.1500,
        "val_max_drawdown_r": 7.0,
        "oos_lcb_95": 0.10,  # Leakage!
        "qualified": True,
    }
    with pytest.raises(ValueError, match="OOS_METRICS_LEAKAGE"):
        select_champion([candidate_with_oos_lcb])


def test_05_oos_evaluation_requires_champion_locked():
    """
    Test 5: OOS evaluation must refuse to run unless the champion is formally locked.
    Verifies that lock_champion sets CHAMPION_LOCKED_BEFORE_OOS and that
    evaluate_champion_oos enforces champion_locked=True.
    """
    policy = load_governed_selection_policy()
    mock_dataset = MagicMock()
    mock_candidate = MagicMock()

    # Attempting OOS with champion_locked=False must fail closed
    with pytest.raises(RuntimeError, match="CHAMPION_NOT_LOCKED"):
        evaluate_champion_oos(
            dataset=mock_dataset,
            champion_candidate=mock_candidate,
            policy=policy,
            champion_locked=False,
        )

    # Calling lock_champion sets the locked state and produces evidence fingerprint
    champion_dict = {
        "candidate_id": "XAUUSD_CANDIDATE_042",
        "index": 42,
        "val_lcb_95": 0.1280,
        "val_max_drawdown_r": 8.10,
        "champion_fingerprint": "sig_fp_dummy:risk_fp_dummy",
    }
    locked_dict = lock_champion(
        champion_dict=champion_dict,
        selection_policy=policy,
        dataset_fingerprint="2c45cf9c026ba698cf98357a70bb4e16d4128f7c9e05f63d6f1c7136f3fa1417",
    )
    assert locked_dict["CHAMPION_LOCKED_BEFORE_OOS"] is True
    assert "selection_evidence_fingerprint" in locked_dict
    assert len(locked_dict["selection_evidence_fingerprint"]) == 64


def test_06_structural_reachability_prefilter():
    """
    Complementary check: Verify structural reachability prefilter correctly rejects
    candidates with impossible thresholds without running expensive replay.
    """
    from engine.signals.profile import (
        Phase4SignalProfile,
        SideDirectionPolicy,
        SideTimingPolicy,
        SideGatePolicy,
    )

    # Valid profile with reachable thresholds (6 active direction components sum to 100.0, regime=0, volume=0)
    profile = Phase4SignalProfile(
        long_direction=SideDirectionPolicy(
            weight_regime=0.0,
            weight_trend_1h=20.0,
            weight_trend_4h=20.0,
            weight_trend_1d=15.0,
            weight_structure_bos=15.0,
            weight_pullback=15.0,
            weight_momentum=15.0,
            weight_volume=0.0,
        ),
        long_timing=SideTimingPolicy(
            weight_entry_zone=35.0,
            weight_reversal_confirmation_15m=35.0,
            weight_momentum_turn_15m_1h=30.0,
            weight_phase3a=0.0,
            weight_volume_response=0.0,
        ),
        long_gate=SideGatePolicy(
            threshold_watch_direction=40.0,
            threshold_ready_direction=50.0,
            threshold_ready_timing=50.0,
            threshold_window_direction=55.0,
            threshold_window_timing=55.0,
        ),
        short_direction=SideDirectionPolicy(
            weight_regime=0.0,
            weight_trend_1h=20.0,
            weight_trend_4h=20.0,
            weight_trend_1d=15.0,
            weight_structure_bos=15.0,
            weight_pullback=15.0,
            weight_momentum=15.0,
            weight_volume=0.0,
        ),
        short_timing=SideTimingPolicy(
            weight_entry_zone=35.0,
            weight_reversal_confirmation_15m=35.0,
            weight_momentum_turn_15m_1h=30.0,
            weight_phase3a=0.0,
            weight_volume_response=0.0,
        ),
        short_gate=SideGatePolicy(
            threshold_watch_direction=40.0,
            threshold_ready_direction=50.0,
            threshold_ready_timing=50.0,
            threshold_window_direction=55.0,
            threshold_window_timing=55.0,
        ),
    )
    reachable, msg = check_structural_reachability(profile)
    assert reachable is True
    assert msg == "REACHABLE"

    # Profile with impossible threshold (e.g. READY direction = 120.0 > 100.0 max)
    unreachable_profile = Phase4SignalProfile(
        long_direction=SideDirectionPolicy(
            weight_regime=0.0,
            weight_trend_1h=20.0,
            weight_trend_4h=20.0,
            weight_trend_1d=15.0,
            weight_structure_bos=15.0,
            weight_pullback=15.0,
            weight_momentum=15.0,
            weight_volume=0.0,
        ),
        long_timing=SideTimingPolicy(
            weight_entry_zone=35.0,
            weight_reversal_confirmation_15m=35.0,
            weight_momentum_turn_15m_1h=30.0,
            weight_phase3a=0.0,
            weight_volume_response=0.0,
        ),
        long_gate=SideGatePolicy(
            threshold_watch_direction=40.0,
            threshold_ready_direction=120.0,
            threshold_ready_timing=50.0,
            threshold_window_direction=125.0,
            threshold_window_timing=55.0,
        ),
        short_direction=SideDirectionPolicy(
            weight_regime=0.0,
            weight_trend_1h=20.0,
            weight_trend_4h=20.0,
            weight_trend_1d=15.0,
            weight_structure_bos=15.0,
            weight_pullback=15.0,
            weight_momentum=15.0,
            weight_volume=0.0,
        ),
        short_timing=SideTimingPolicy(
            weight_entry_zone=35.0,
            weight_reversal_confirmation_15m=35.0,
            weight_momentum_turn_15m_1h=30.0,
            weight_phase3a=0.0,
            weight_volume_response=0.0,
        ),
        short_gate=SideGatePolicy(
            threshold_watch_direction=40.0,
            threshold_ready_direction=50.0,
            threshold_ready_timing=50.0,
            threshold_window_direction=55.0,
            threshold_window_timing=55.0,
        ),
    )
    reachable, msg = check_structural_reachability(unreachable_profile)
    assert reachable is False
    assert "max reachable" in msg
