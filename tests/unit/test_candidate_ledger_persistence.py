"""
Unit tests for XAUUSD Candidate Calibration Ledger Persistence and Recovery.
"""
import json
from pathlib import Path
import pytest

from engine.backtest.xauusd_candidate_ledger import (
    REQUIRED_PROVENANCE_KEYS,
    XauUsdCandidateLedgerManager,
)


@pytest.fixture
def sample_provenance():
    return {
        "dataset_fingerprint": "547ec898e42ed0eff2992400d784e40c6b53c1d26b7a5e5722e0eb1128080242",
        "code_revision": "b1cd4b04f4c073d7d8c06a9c1728477b52315d98",
        "selection_policy_fingerprint": "bb40ce20dea1ee264635ffca252be38cbea379bb250d1fc8096d476afd10073b",
        "generator_policy_fingerprint": "111873cfbd11850613bd2496baa3d5e4fc8f1a52f4930a7f694b9bf334e6b46f",
        "cache_semantics_version": "v2_mtf_features",
    }


@pytest.fixture
def sample_candidate_record(sample_provenance):
    return {
        "candidate_id": "XAUUSD_CANDIDATE_012",
        "candidate_index": 12,
        "dataset_fingerprint": sample_provenance["dataset_fingerprint"],
        "code_revision": sample_provenance["code_revision"],
        "selection_policy_fingerprint": sample_provenance["selection_policy_fingerprint"],
        "generator_policy_fingerprint": sample_provenance["generator_policy_fingerprint"],
        "cache_semantics_version": sample_provenance["cache_semantics_version"],
        "run_fingerprint": "run_fp_12345678",
        "buy_trade_count": 371,
        "sell_trade_count": 0,
        "combined_trade_count": 371,
        "buy_effective_n": 280.0,
        "sell_effective_n": 0.0,
        "combined_effective_n": 280.0,
        "buy_lcb95": -0.0201,
        "sell_lcb95": 0.0,
        "combined_lcb95": -0.0201,
        "buy_mdd": 14.62,
        "sell_mdd": 0.0,
        "combined_mdd": 14.62,
        "fold_metrics": {
            "fold_expectancies": [-0.0309, 0.0616, 0.0929, 0.1237, 0.1095],
            "fold_profits": [-5.96, 10.41, 16.54, 25.36, 22.55],
            "positive_fold_count": 4,
            "total_folds": 5,
        },
        "positive_fold_count": 4,
        "temporal_stability": 0.9425,
        "profit_concentration": 36.8,
        "buy_mean_r": 0.05,
        "sell_mean_r": -0.02,
        "buy_temporal_stability": 0.9425,
        "sell_temporal_stability": 0.85,
        "buy_profit_concentration": 36.8,
        "sell_profit_concentration": 20.0,
        "buy_positive_fold_count": 4,
        "sell_positive_fold_count": 2,
        "buy_fold_expectancies": [-0.03, 0.06, 0.09, 0.12, 0.10],
        "sell_fold_expectancies": [-0.01, 0.02, -0.05, 0.01, -0.02],
        "buy_fold_profits": [-5.0, 10.0, 15.0, 25.0, 20.0],
        "sell_fold_profits": [-1.0, 2.0, -5.0, 1.0, -2.0],
        "buy_fold_trade_counts": [50, 60, 70, 80, 111],
        "sell_fold_trade_counts": [10, 15, 20, 20, 20],
        "invalidated_entry_count": 196,
        "stale_tp_negative_gross_count": 0,
        "stale_sl_positive_gross_count": 0,
        "qualification_flags": {
            "qualified": False,
            "buy_eff_n_pass": True,
            "sell_eff_n_pass": False,
            "comb_eff_n_pass": True,
            "lcb95_pass": False,
            "mdd_pass": True,
            "concentration_pass": True,
            "positive_folds_pass": True,
            "stability_pass": True,
        },
        "rejection_reasons": ["LCB95 -0.0201 <= 0.0", "SELL N_eff 0.0 < 60.0"],
    }


import shutil
import uuid

@pytest.fixture
def custom_tmp_dir():
    d = Path("scratch/ledger_tests") / str(uuid.uuid4())[:8]
    d.mkdir(parents=True, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_01_atomic_write_and_read(custom_tmp_dir, sample_provenance, sample_candidate_record):
    ledger_file = custom_tmp_dir / "test_ledger.json"
    mgr = XauUsdCandidateLedgerManager(ledger_file, sample_provenance)

    eval_result = {"val_lcb_95": -0.0201, "trade_count": 371, "qualified": False}
    mgr.record_candidate(sample_candidate_record, eval_result=eval_result)

    assert ledger_file.exists()
    with open(ledger_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["schema"] == "aurumiq.calibration.candidate_ledger.v1"
    assert data["candidate_count"] == 1
    stored_cand = data["candidates"][0]
    assert stored_cand["candidate_id"] == "XAUUSD_CANDIDATE_012"
    assert stored_cand["combined_effective_n"] == 280.0
    assert stored_cand["eval_result"] == eval_result
    assert stored_cand["buy_mean_r"] == 0.05
    assert stored_cand["sell_mean_r"] == -0.02
    assert stored_cand["buy_temporal_stability"] == 0.9425
    assert stored_cand["sell_temporal_stability"] == 0.85
    assert stored_cand["buy_profit_concentration"] == 36.8
    assert stored_cand["sell_profit_concentration"] == 20.0
    assert stored_cand["buy_positive_fold_count"] == 4
    assert stored_cand["sell_positive_fold_count"] == 2


def test_02_provenance_validation_exact_match(custom_tmp_dir, sample_provenance, sample_candidate_record):
    ledger_file = custom_tmp_dir / "test_ledger.json"
    mgr = XauUsdCandidateLedgerManager(ledger_file, sample_provenance)
    mgr.record_candidate(sample_candidate_record, eval_result={"val_lcb_95": -0.0201})

    # New manager instance reading the same file
    mgr2 = XauUsdCandidateLedgerManager(ledger_file, sample_provenance)
    assert mgr2.can_reuse("XAUUSD_CANDIDATE_012") is True
    res = mgr2.get_candidate_eval_result("XAUUSD_CANDIDATE_012")
    assert res == {"val_lcb_95": -0.0201}


def test_03_provenance_mismatch_triggers_recomputation(custom_tmp_dir, sample_provenance, sample_candidate_record):
    ledger_file = custom_tmp_dir / "test_ledger.json"
    mgr = XauUsdCandidateLedgerManager(ledger_file, sample_provenance)
    mgr.record_candidate(sample_candidate_record, eval_result={"val_lcb_95": -0.0201})

    # Test mismatch on each provenance key
    for key in (
        "dataset_fingerprint",
        "code_revision",
        "selection_policy_fingerprint",
        "generator_policy_fingerprint",
        "cache_semantics_version",
    ):
        alt_prov = dict(sample_provenance)
        alt_prov[key] = "corrupted_or_different_value"
        mgr_alt = XauUsdCandidateLedgerManager(ledger_file, alt_prov)
        assert mgr_alt.can_reuse("XAUUSD_CANDIDATE_012") is False
        assert mgr_alt.get_candidate_eval_result("XAUUSD_CANDIDATE_012") is None


def test_04_incremental_updates(custom_tmp_dir, sample_provenance, sample_candidate_record):
    ledger_file = custom_tmp_dir / "test_ledger.json"
    mgr = XauUsdCandidateLedgerManager(ledger_file, sample_provenance)

    rec1 = dict(sample_candidate_record)
    rec1["candidate_id"] = "XAUUSD_CANDIDATE_001"
    rec1["candidate_index"] = 1
    mgr.record_candidate(rec1, eval_result={"val_lcb_95": -0.10})

    rec2 = dict(sample_candidate_record)
    rec2["candidate_id"] = "XAUUSD_CANDIDATE_002"
    rec2["candidate_index"] = 2
    mgr.record_candidate(rec2, eval_result={"val_lcb_95": -0.05})

    assert mgr.candidate_count == 2
    assert mgr.can_reuse("XAUUSD_CANDIDATE_001") is True
    assert mgr.can_reuse("XAUUSD_CANDIDATE_002") is True


def test_05_unknown_candidate_cannot_be_reused(custom_tmp_dir, sample_provenance):
    ledger_file = custom_tmp_dir / "test_ledger.json"
    mgr = XauUsdCandidateLedgerManager(ledger_file, sample_provenance)
    assert mgr.can_reuse("XAUUSD_CANDIDATE_999") is False
    assert mgr.get_candidate_eval_result("XAUUSD_CANDIDATE_999") is None
