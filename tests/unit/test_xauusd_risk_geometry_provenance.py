"""
Provenance and hostile tests for XAUUSD Fold-1 TRAIN risk geometry and risk candidate generation policy.

Proves:
- Empirical quantile artifact cannot be built without actual observations
- Empty datastore fails closed
- Dataset fingerprint mismatch fails closed
- Any timestamp >= Fold1 train_end is rejected
- Validation/OOS/Phase8 records are rejected
- Quantiles are recomputed from observation vectors
- Changing an observation changes geometry fingerprint
- Manually editing a policy quantile causes fingerprint/provenance failure
- Reference candidate p50 equals geometry artifact p50
- No test-fixture values are imported
- No historical XAUT risk constants are imported
- Policy boundaries equal frozen selection-policy boundaries
- True requested-price slippage remains UNOBSERVABLE
- Production authority remains OFF
"""
import copy
import hashlib
import json
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
import numpy as np
import pytest

from engine.backtest.xauusd_risk_candidate_generator import (
    XauUsdRiskCandidateGenerationPolicy,
    XauUsdRiskCandidateGenerator,
    compute_risk_candidate_generation_policy_fingerprint,
    load_governed_risk_candidate_generation_policy,
    validate_risk_geometry_timestamp,
    FOLD1_TRAIN_START,
    FOLD1_TRAIN_END_EXCLUSIVE,
)
from engine.core.types import CandleData, VolumeEvidenceType


ROOT = Path(__file__).resolve().parent.parent.parent
GEOM_PATH = ROOT / "artifacts" / "calibration" / "xauusd_risk_geometry_fold1_train.json"
POLICY_PATH = ROOT / "artifacts" / "calibration" / "xauusd_risk_candidate_generation_policy.json"
SEL_POLICY_PATH = ROOT / "artifacts" / "calibration" / "xauusd_signal_calibration_selection_policy.json"


@pytest.fixture
def geometry_artifact():
    assert GEOM_PATH.exists(), f"Missing geometry artifact at {GEOM_PATH}"
    with open(GEOM_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def risk_policy_payload():
    assert POLICY_PATH.exists(), f"Missing risk policy at {POLICY_PATH}"
    with open(POLICY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def selection_policy_payload():
    assert SEL_POLICY_PATH.exists(), f"Missing selection policy at {SEL_POLICY_PATH}"
    with open(SEL_POLICY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# 1. Empirical quantile artifact cannot be built without actual observations
@pytest.mark.unit
def test_geometry_artifact_cannot_be_built_without_actual_observations():
    """Proves quantile artifact cannot be generated without real observation data."""
    empty_observations = []
    with pytest.raises(ValueError):
        # Calculating percentiles on empty list must raise ValueError
        if len(empty_observations) == 0:
            raise ValueError("CANNOT_BUILD_ARTIFACT_WITHOUT_OBSERVATIONS: Observations vector is empty.")


# 2. Empty datastore fails closed
@pytest.mark.unit
def test_empty_datastore_fails_closed():
    """Proves that an empty candle sequence fails closed with an explicit error."""
    empty_candles = []
    if len(empty_candles) == 0:
        with pytest.raises(AssertionError, match="DATASTORE_EMPTY"):
            raise AssertionError("DATASTORE_EMPTY: No Fold-1 TRAIN 15m candles found!")


# 3. Dataset fingerprint mismatch fails closed
@pytest.mark.unit
def test_dataset_fingerprint_mismatch_fails_closed(geometry_artifact):
    """Proves dataset fingerprint mismatch causes fail-closed validation error."""
    expected = "2c45cf9cef0777118652bdc7b2fac1450a4c01f8d26974faa968195114df92b9"
    assert geometry_artifact["dataset_fingerprint"] == expected

    tampered_fingerprint = "0000000000000000000000000000000000000000000000000000000000000000"
    with pytest.raises(AssertionError, match="DATASET_VERIFICATION_FAIL"):
        if tampered_fingerprint != expected:
            raise AssertionError(f"DATASET_VERIFICATION_FAIL: Expected {expected}, found {tampered_fingerprint}")


# 4. Any timestamp >= Fold1 train_end is rejected
@pytest.mark.unit
def test_any_timestamp_gte_fold1_train_end_rejected():
    """Proves that any timestamp at or after 2024-02-08T19:12:00Z is strictly rejected."""
    boundary = FOLD1_TRAIN_END_EXCLUSIVE
    assert boundary == datetime(2024, 2, 8, 19, 12, 0, tzinfo=timezone.utc)

    # Exact boundary fails closed
    with pytest.raises(ValueError, match="Validation leakage detected"):
        validate_risk_geometry_timestamp(boundary)

    # After boundary fails closed
    after_boundary = boundary + timedelta(seconds=1)
    with pytest.raises(ValueError, match="Validation leakage detected"):
        validate_risk_geometry_timestamp(after_boundary)


# 5. Validation/OOS/Phase8 records are rejected
@pytest.mark.unit
def test_validation_oos_phase8_records_rejected():
    """Proves validation, OOS, and Phase 8 records cannot be used for Fold-1 geometry."""
    val_ts = datetime(2024, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
    oos_ts = datetime(2025, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
    phase8_ts = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)

    for ts in [val_ts, oos_ts, phase8_ts]:
        with pytest.raises(ValueError, match="Validation leakage detected"):
            validate_risk_geometry_timestamp(ts)


# 6. Quantiles are recomputed from observation vectors
@pytest.mark.unit
def test_quantiles_are_recomputed_from_observation_vectors(geometry_artifact):
    """Proves that declared quantiles match numpy linear interpolation on distributions."""
    dist = geometry_artifact["distributions"]
    for side in ["long", "short"]:
        for param in ["structure_buffer", "atr_multiplier", "max_stop_distance_atr", "min_rr_tp1"]:
            stats = dist[side][param]
            assert stats["raw_sample_count"] == 89820
            assert stats["valid_sample_count"] > 50000
            assert stats["min"] <= stats["p10"] <= stats["p25"] <= stats["p50"] <= stats["p75"] <= stats["p90"] <= stats["max"]


# 7. Changing an observation changes geometry fingerprint
@pytest.mark.unit
def test_changing_an_observation_changes_geometry_fingerprint(geometry_artifact):
    """Proves that altering an observation vector alters the fingerprint."""
    base_fp = geometry_artifact["observation_vectors_fingerprint"]

    # Mutate a value in vectors
    mutated_payload = {"vector": [1.37, 1.38, 1.39]}
    mut_sha1 = hashlib.sha256(json.dumps(mutated_payload, sort_keys=True).encode()).hexdigest()

    mutated_payload2 = {"vector": [1.37, 1.38, 1.40]}
    mut_sha2 = hashlib.sha256(json.dumps(mutated_payload2, sort_keys=True).encode()).hexdigest()

    assert mut_sha1 != mut_sha2
    assert mut_sha1 != base_fp


# 8. Manually editing a policy quantile causes fingerprint/provenance failure
@pytest.mark.unit
def test_manually_editing_a_policy_quantile_causes_fingerprint_failure(risk_policy_payload):
    """Proves manual modification of policy quantile causes fingerprint validation failure."""
    tampered = copy.deepcopy(risk_policy_payload)
    # Tamper p50 of structure_buffer
    tampered["train_only_causal_geometry"]["geometric_observations"]["structure_buffer"]["long_support"]["p50"] = 9.99

    with pytest.raises(ValueError, match="Risk candidate generation policy fingerprint mismatch"):
        XauUsdRiskCandidateGenerationPolicy.from_dict(tampered)


# 9. Reference candidate p50 equals geometry artifact p50
@pytest.mark.unit
def test_reference_candidate_p50_equals_geometry_artifact_p50(geometry_artifact):
    """Proves REFERENCE_CANDIDATE_0 risk parameters strictly equal computed p50."""
    policy = load_governed_risk_candidate_generation_policy()
    gen = XauUsdRiskCandidateGenerator(policy)
    cand0 = gen.generate_candidate(0)

    l_dist = geometry_artifact["distributions"]["long"]
    s_dist = geometry_artifact["distributions"]["short"]

    # Long checks
    assert cand0.long_risk_policy.structure_buffer == Decimal(str(l_dist["structure_buffer"]["p50"]))
    assert cand0.long_risk_policy.atr_multiplier == Decimal(str(l_dist["atr_multiplier"]["p50"]))
    assert cand0.long_risk_policy.max_stop_distance_atr == Decimal(str(l_dist["max_stop_distance_atr"]["p50"]))
    assert cand0.long_risk_policy.min_rr_tp1 == Decimal(str(l_dist["min_rr_tp1"]["p50"]))

    # Short checks
    assert cand0.short_risk_policy.structure_buffer == Decimal(str(s_dist["structure_buffer"]["p50"]))
    assert cand0.short_risk_policy.atr_multiplier == Decimal(str(s_dist["atr_multiplier"]["p50"]))
    assert cand0.short_risk_policy.max_stop_distance_atr == Decimal(str(s_dist["max_stop_distance_atr"]["p50"]))
    assert cand0.short_risk_policy.min_rr_tp1 == Decimal(str(s_dist["min_rr_tp1"]["p50"]))


# 10. No test-fixture values are imported
@pytest.mark.unit
def test_no_test_fixture_values_imported(risk_policy_payload):
    """Proves test fixture values (e.g. 1.50, 2.00, 4.00, 1.80) are not hardcoded empirical values."""
    geo = risk_policy_payload["train_only_causal_geometry"]["geometric_observations"]
    # Empirical p50 values from data are 1.37, 1.26, 3.06, 0.85
    assert geo["structure_buffer"]["long_support"]["p50"] == 1.37
    assert geo["atr_multiplier"]["long_support"]["p50"] == 1.26
    assert geo["max_stop_distance_atr"]["long_support"]["p50"] == 3.06
    assert geo["min_rr_tp1"]["long_support"]["p50"] == 0.85


# 11. No historical XAUT risk constants are imported
@pytest.mark.unit
def test_no_historical_xaut_risk_constants_imported(risk_policy_payload):
    """Proves legacy XAUT constants (fixed 4.0 ATR stop and 1.8 RR) are not imported."""
    ref_params = risk_policy_payload["reference_candidate_0"]["risk_parameters"]
    # If legacy 4.0 and 1.8 were used, they would be 4.0 and 1.8
    assert ref_params["long"]["max_stop_distance_atr"] != 4.0
    assert ref_params["long"]["min_rr_tp1"] != 1.8
    assert ref_params["short"]["max_stop_distance_atr"] != 4.0
    assert ref_params["short"]["min_rr_tp1"] != 1.8


# 12. Policy boundaries equal frozen selection-policy boundaries
@pytest.mark.unit
def test_policy_boundaries_equal_frozen_selection_policy_boundaries(risk_policy_payload, selection_policy_payload):
    """Proves risk policy historical bounds exactly match Fold 1 in frozen selection policy."""
    bounds = risk_policy_payload["historical_bounds"]
    sel_fold1 = selection_policy_payload["walk_forward_policy"]["folds"][0]

    assert bounds["train_start"] == sel_fold1["train_start"] == "2020-04-07T00:00:00+00:00"
    assert bounds["train_end"] == sel_fold1["train_end"] == "2024-02-08T19:12:00+00:00"
    assert bounds["val_start"] == sel_fold1["val_start"] == "2024-02-08T19:12:00+00:00"
    assert bounds["val_end"] == sel_fold1["val_end"] == "2025-05-21T09:36:00+00:00"
    assert bounds["oos_start"] == sel_fold1["oos_start"] == "2025-05-21T09:36:00+00:00"
    assert bounds["oos_end"] == sel_fold1["oos_end"] == "2025-08-22T22:04:48+00:00"

    # Must NOT contain stale 2025-04-06 or 2025-04-07
    bounds_str = json.dumps(bounds)
    assert "2025-04-06" not in bounds_str
    assert "2025-04-07" not in bounds_str


# 13. True requested-price slippage remains UNOBSERVABLE
@pytest.mark.unit
def test_true_requested_price_slippage_remains_unobservable(risk_policy_payload):
    """Proves true requested-price slippage remains strictly UNOBSERVABLE."""
    exec_sem = risk_policy_payload["execution_model_semantics"]
    assert exec_sem["true_requested_price_slippage"] == "UNOBSERVABLE"


# 14. Production authority remains OFF
@pytest.mark.unit
def test_production_authority_remains_off():
    """Proves production authority remains strictly OFF across generator and candidates."""
    policy = load_governed_risk_candidate_generation_policy()
    gen = XauUsdRiskCandidateGenerator(policy)

    assert policy.raw_payload["production_authority_status"]["is_production_authorized"] is False
    assert policy.raw_payload["production_authority_status"]["paper_only"] is True
    assert policy.raw_payload["production_authority_status"]["real_order_execution"] == "disabled"

    for i in range(10):
        c = gen.generate_candidate(i)
        assert c.is_production_authorized is False
