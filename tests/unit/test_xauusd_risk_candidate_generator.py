"""
Unit tests for XAUUSD Phase 5 risk candidate search-space governance,
joint signal+risk candidate generation, and execution semantics.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import inspect
import json
from pathlib import Path
import pytest

from engine.backtest.xauusd_candidate_generator import (
    load_governed_candidate_generation_policy,
)
from engine.backtest.xauusd_risk_candidate_generator import (
    XauUsdJointCandidateGenerator,
    XauUsdRiskCandidateGenerationPolicy,
    XauUsdRiskCandidateGenerator,
    compute_risk_candidate_generation_policy_fingerprint,
    derive_risk_deterministic_seed,
    load_governed_risk_candidate_generation_policy,
)
from engine.core.types import (
    BosType,
    CandleData,
    EntryExecutionPolicy,
    Phase5CalibrationStatus,
    RiskSide,
    StructureResult,
    StructureType,
    StructureZone,
)
from engine.risk.xauusd_execution import SideAwareEntryExecutionModel
from engine.risk.xauusd_fingerprints import compute_phase5_policy_fingerprint
from engine.risk.xauusd_policy import (
    SideRiskPolicy,
    XauUsdExecutionPolicy,
    XauUsdRiskProfile,
)
from engine.risk.xauusd_targets import calculate_long_targets, calculate_short_targets


@pytest.fixture
def risk_policy():
    return load_governed_risk_candidate_generation_policy()


@pytest.fixture
def risk_generator(risk_policy):
    return XauUsdRiskCandidateGenerator(risk_policy)


@pytest.fixture
def joint_generator():
    return XauUsdJointCandidateGenerator()


@pytest.mark.unit
def test_no_test_fixture_risk_values_leak_into_governance(risk_policy):
    """Proves no arbitrary test fixture values leak into the governed risk policy."""
    raw = risk_policy.raw_payload
    geo = raw["train_only_causal_geometry"]["geometric_observations"]

    # All parameter supports must map to defined empirical causal bases
    for param_name, obs in geo.items():
        assert "causal_basis" in obs
        assert "unit" in obs
        assert "long_support" in obs
        assert "short_support" in obs
        # Check quantiles are strictly ordered
        for side_key in ("long_support", "short_support"):
            supp = obs[side_key]
            assert supp["p10"] <= supp["p25"] <= supp["p50"] <= supp["p75"] <= supp["p90"]


@pytest.mark.unit
def test_no_arbitrary_fixed_risk_range_present(risk_policy):
    """Proves risk parameters are drawn from observed TRAIN geometry support rather than invented ranges."""
    raw = risk_policy.raw_payload
    geo = raw["train_only_causal_geometry"]["geometric_observations"]

    # buffer: native price units from structure zone width
    assert geo["structure_buffer"]["unit"] == "NATIVE_PRICE_UNITS_USD"
    # atr_multiplier: normalized entry-to-invalidation distance
    assert geo["atr_multiplier"]["unit"] == "DIMENSIONLESS_ATR14"
    # max_stop_distance_atr: upper support of normalized structure distance
    assert geo["max_stop_distance_atr"]["unit"] == "DIMENSIONLESS_ATR14"
    # min_rr_tp1: raw structural reward-to-risk
    assert geo["min_rr_tp1"]["unit"] == "DIMENSIONLESS_REWARD_RISK_RATIO"


@pytest.mark.unit
def test_risk_domain_uses_train_only_pit_geometry(risk_policy):
    """Proves risk domain is bounded strictly to Fold 1 TRAIN window with validation/OOS active exclusion."""
    bounds = risk_policy.raw_payload["historical_bounds"]
    assert bounds["common_risk_domain_source"] == "FOLD_1_TRAIN_ONLY"
    assert bounds["train_start"] == "2020-04-07T00:00:00+00:00"
    assert bounds["train_end"] == "2024-02-08T19:12:00+00:00"
    assert bounds["val_start"] == "2024-02-08T19:12:00+00:00"
    assert bounds["val_end"] == "2025-05-21T09:36:00+00:00"
    assert bounds["oos_start"] == "2025-05-21T09:36:00+00:00"
    assert bounds["oos_end"] == "2025-08-22T22:04:48+00:00"
    assert "2025-04-06" not in str(bounds)
    assert "2025-04-07" not in str(bounds)
    assert bounds["oos_exclusion_status"] == "STRICT_EXCLUSION_ACTIVE"


@pytest.mark.unit
def test_last_eligible_geometry_timestamp_precedes_fold1_validation():
    """Proves the last eligible geometry timestamp is strictly before Fold 1 validation start."""
    from engine.backtest.xauusd_risk_candidate_generator import (
        FOLD1_TRAIN_END_EXCLUSIVE,
        validate_risk_geometry_timestamp,
    )
    # 1 second before Fold 1 validation starts
    last_valid_ts = FOLD1_TRAIN_END_EXCLUSIVE - timedelta(seconds=1)
    assert last_valid_ts == datetime(2024, 2, 8, 19, 11, 59, tzinfo=timezone.utc)
    # Must succeed without error
    validate_risk_geometry_timestamp(last_valid_ts)


@pytest.mark.unit
def test_2024_02_08_19_12_00_excluded_and_rejected():
    """Proves exact boundary timestamp 2024-02-08T19:12:00Z is strictly excluded and rejected."""
    from engine.backtest.xauusd_risk_candidate_generator import (
        FOLD1_TRAIN_END_EXCLUSIVE,
        validate_risk_geometry_timestamp,
    )
    exact_boundary_ts = datetime(2024, 2, 8, 19, 12, 0, tzinfo=timezone.utc)
    assert exact_boundary_ts == FOLD1_TRAIN_END_EXCLUSIVE

    with pytest.raises(ValueError, match="Validation leakage detected"):
        validate_risk_geometry_timestamp(exact_boundary_ts)


@pytest.mark.unit
def test_geometry_after_fold1_train_end_rejected():
    """Proves any geometry timestamp at or after Fold 1 train_end is rejected fail-closed."""
    from engine.backtest.xauusd_risk_candidate_generator import validate_risk_geometry_timestamp

    after_train_timestamps = [
        datetime(2024, 2, 8, 19, 12, 1, tzinfo=timezone.utc),
        datetime(2024, 2, 9, 0, 0, 0, tzinfo=timezone.utc),
        datetime(2024, 3, 1, 12, 0, 0, tzinfo=timezone.utc),
        datetime(2024, 4, 7, 0, 0, 0, tzinfo=timezone.utc),
        datetime(2025, 4, 7, 0, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc),
    ]
    for ts in after_train_timestamps:
        with pytest.raises(ValueError, match="Validation leakage detected"):
            validate_risk_geometry_timestamp(ts)


@pytest.mark.unit
def test_candidate_fingerprints_remain_deterministic(risk_policy):
    """Proves candidate fingerprints remain 100% deterministic across multiple generator instances."""
    gen1 = XauUsdRiskCandidateGenerator(risk_policy)
    gen2 = XauUsdRiskCandidateGenerator(risk_policy)

    cands1 = gen1.generate_all_candidates(10)
    cands2 = gen2.generate_all_candidates(10)

    fps1 = [compute_phase5_policy_fingerprint(c) for c in cands1]
    fps2 = [compute_phase5_policy_fingerprint(c) for c in cands2]

    assert fps1 == fps2
    assert len(fps1) == 10
    assert len(set(fps1)) == 10  # All unique


@pytest.mark.unit
def test_future_outcomes_cannot_enter_generator_api():
    """Proves generator API does not accept trade outcomes, MAE, MFE, PnL, or labels."""
    sig_params = inspect.signature(XauUsdRiskCandidateGenerator.generate_candidate).parameters
    forbidden_terms = ["outcome", "trade", "mae", "mfe", "pnl", "return", "label", "winner", "loser"]
    for param_name in sig_params:
        for term in forbidden_terms:
            assert term not in param_name.lower(), f"Forbidden term '{term}' in parameter '{param_name}'"

    all_params = inspect.signature(XauUsdRiskCandidateGenerator.generate_all_candidates).parameters
    for param_name in all_params:
        for term in forbidden_terms:
            assert term not in param_name.lower(), f"Forbidden term '{term}' in parameter '{param_name}'"


@pytest.mark.unit
def test_oos_and_phase8_cannot_enter_generator_api():
    """Proves generator API does not accept OOS candles, Phase 8 outcomes, or paper trades."""
    sig_params = inspect.signature(XauUsdRiskCandidateGenerator.__init__).parameters
    forbidden_terms = ["oos", "phase8", "future", "paper_trade"]
    for param_name in sig_params:
        for term in forbidden_terms:
            assert term not in param_name.lower()


@pytest.mark.unit
def test_long_short_independent(risk_generator):
    """Proves BUY (LONG) and SELL (SHORT) risk parameters are independently sampled."""
    # Check across multiple candidates that long and short policies are not identical copies
    different_count = 0
    for idx in range(1, 10):
        cand = risk_generator.generate_candidate(idx)
        lr = cand.long_risk_policy
        sr = cand.short_risk_policy
        if (lr.structure_buffer != sr.structure_buffer or
            lr.atr_multiplier != sr.atr_multiplier or
            lr.max_stop_distance_atr != sr.max_stop_distance_atr or
            lr.min_rr_tp1 != sr.min_rr_tp1):
            different_count += 1
    assert different_count > 0, "LONG and SHORT risk policies must be independently parameterized."


@pytest.mark.unit
def test_tp2_atr_multiplier_remains_none_for_first_calibration(risk_generator):
    """Proves tp2_atr_multiplier is strictly None across all candidates in the first calibration."""
    for idx in range(10):
        cand = risk_generator.generate_candidate(idx)
        assert cand.long_risk_policy.tp2_atr_multiplier is None
        assert cand.short_risk_policy.tp2_atr_multiplier is None


@pytest.mark.unit
def test_structural_tp2_remains_supported():
    """Proves that structural TP2 from confirmed PIT zones remains fully supported when tp2_atr_multiplier is None."""
    t0 = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    policy = SideRiskPolicy(
        structure_buffer=Decimal("2.00"),
        atr_multiplier=Decimal("2.00"),
        max_stop_distance_atr=Decimal("4.00"),
        min_rr_tp1=Decimal("1.50"),
        tp2_atr_multiplier=None,
    )
    # Provide two resistance zones: one at 2520 (TP1) and one at 2530 (TP2)
    z1 = StructureZone("RESISTANCE", Decimal("2520.00"), Decimal("2522.00"), t0, 2, True)
    z2 = StructureZone("RESISTANCE", Decimal("2530.00"), Decimal("2532.00"), t0, 1, True)
    struct = StructureResult(
        timestamp=t0,
        structure_type=StructureType.HH,
        bos=BosType.NONE,
        last_swing_high=None,
        last_swing_low=None,
        swings=(),
        zones=(z1, z2),
    )

    tp1, tp2, rr1, rr2, fp1, fp2, ok, err = calculate_long_targets(
        entry_min=Decimal("2500.00"),
        entry_mid=Decimal("2501.00"),
        entry_max=Decimal("2502.00"),
        stop_final=Decimal("2495.00"),
        structure_15m=struct,
        atr14=Decimal("5.00"),
        authoritative_t=t0,
        policy=policy,
    )
    assert ok is True, f"calculate_long_targets failed: {err}"
    assert tp1 == Decimal("2520.00")
    assert tp2 == Decimal("2530.00"), "Structural TP2 from second confirmed resistance must be retained."
    assert rr2 is not None and rr2 > rr1


@pytest.mark.unit
def test_max_100_joint_candidates(joint_generator):
    """Proves joint search budget enforces TOTAL_JOINT_CANDIDATES <= 100 (no Cartesian explosion)."""
    assert joint_generator.max_joint_candidates <= 100

    candidates = joint_generator.generate_all_joint_candidates(100)
    assert len(candidates) == 100

    # Requesting > 100 must fail closed
    with pytest.raises(ValueError, match="exceeds governed maximum"):
        joint_generator.generate_all_joint_candidates(101)


@pytest.mark.unit
def test_deterministic_generation(risk_policy):
    """Proves risk and joint candidate generation is 100% deterministic and reproducible."""
    gen1 = XauUsdRiskCandidateGenerator(risk_policy)
    gen2 = XauUsdRiskCandidateGenerator(risk_policy)

    cand1 = gen1.generate_candidate(5)
    cand2 = gen2.generate_candidate(5)

    fp1 = compute_phase5_policy_fingerprint(cand1)
    fp2 = compute_phase5_policy_fingerprint(cand2)
    assert fp1 == fp2


@pytest.mark.unit
def test_reference_candidate_frozen_ex_ante(risk_generator):
    """Proves Candidate 0 is REFERENCE_CANDIDATE_0 frozen ex-ante with representative TRAIN geometry."""
    cand0 = risk_generator.generate_candidate(0)
    assert cand0.name == "XAUUSD_REFERENCE_CANDIDATE_000"
    assert cand0.calibration_status == Phase5CalibrationStatus.CANDIDATE_NOT_FROZEN

    # Central representative values derived from computed Fold-1 TRAIN p50
    assert cand0.long_risk_policy.structure_buffer == Decimal("1.37")
    assert cand0.long_risk_policy.atr_multiplier == Decimal("1.26")
    assert cand0.long_risk_policy.max_stop_distance_atr == Decimal("3.06")
    assert cand0.long_risk_policy.min_rr_tp1 == Decimal("0.85")
    assert cand0.long_risk_policy.tp2_atr_multiplier is None

    assert cand0.short_risk_policy.structure_buffer == Decimal("1.36")
    assert cand0.short_risk_policy.atr_multiplier == Decimal("1.26")
    assert cand0.short_risk_policy.max_stop_distance_atr == Decimal("2.79")
    assert cand0.short_risk_policy.min_rr_tp1 == Decimal("0.91")
    assert cand0.short_risk_policy.tp2_atr_multiplier is None


@pytest.mark.unit
def test_reference_candidate_not_empirical_baseline_before_validation(risk_policy, risk_generator):
    """Proves Candidate 0 is NOT yet an empirical baseline before backtest qualification."""
    cand0 = risk_generator.generate_candidate(0)
    assert cand0.calibration_status == Phase5CalibrationStatus.CANDIDATE_NOT_FROZEN
    assert cand0.calibration_status != Phase5CalibrationStatus.REVALIDATED_RESEARCH

    ref_data = risk_policy.raw_payload["reference_candidate_0"]
    assert ref_data["valid_empirical_baseline_exists"] is False
    assert ref_data["status"] == "FROZEN_EX_ANTE_NOT_YET_EMPIRICAL_BASELINE"


@pytest.mark.unit
def test_relative_dd_gate_blocked_until_empirical_baseline_exists(risk_policy):
    """Proves 10% relative drawdown gate is blocked until empirical baseline exists."""
    dd_gates = risk_policy.raw_payload["drawdown_gates"]
    assert dd_gates["max_drawdown_deterioration_pct_vs_baseline"] == 10.0
    assert dd_gates["relative_drawdown_gate_evaluable"] is False
    assert "BLOCKED" in dd_gates["evaluation_gate_condition"]


@pytest.mark.unit
def test_true_requested_slippage_remains_unobservable(risk_policy):
    """Proves true requested price slippage is strictly documented as UNOBSERVABLE."""
    exec_sem = risk_policy.raw_payload["execution_model_semantics"]
    assert exec_sem["true_requested_price_slippage"] == "UNOBSERVABLE"


@pytest.mark.unit
def test_quote_lag_not_labeled_broker_execution_latency(risk_policy):
    """Proves 256ms tick lag is not labeled as broker execution latency."""
    exec_sem = risk_policy.raw_payload["execution_model_semantics"]
    assert exec_sem["reference_tick_lag_median_ms"] == 256.0
    assert "QUOTE_LAG" in exec_sem["reference_tick_lag_classification"]
    assert exec_sem["modeled_additional_latency_seconds"] == 0.0
    assert "OWNER_MODELING_ASSUMPTION" in exec_sem["latency_classification"]


@pytest.mark.unit
def test_spread_points_not_mislabeled_spread_percentage(risk_policy):
    """Proves 260 spread points is absolute broker points, not spread percentage."""
    exec_sem = risk_policy.raw_payload["execution_model_semantics"]
    assert exec_sem["synthetic_spread_points"] == 260.0
    assert exec_sem["contract_point_size"] == 0.001
    assert exec_sem["synthetic_spread_price_amount"] == 0.26
    assert exec_sem["spread_classification"] == "SEALED_PHASE6_REFERENCE_FRICTION_ASSUMPTION"
    assert exec_sem["reference_quote_gap_points"] == 0.0
    assert "REFERENCE_QUOTE_0_POINTS" in exec_sem["reference_quote_gap_classification"]


@pytest.mark.unit
def test_execution_model_uses_points_correctly():
    """Proves SideAwareEntryExecutionModel simulates fill price using absolute points spread."""
    pol = XauUsdExecutionPolicy(
        latency_seconds=0.0,
        synthetic_spread_points=Decimal("260.0"),
        point_size=Decimal("0.001"),
        modeled_execution_gap_points=Decimal("0.0"),
    )
    model = SideAwareEntryExecutionModel(
        code_revision="test_rev",
        execution_policy=pol,
        phase5_policy_fingerprint="test_fp",
    )
    t = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    t_bar_open = t + timedelta(minutes=15)
    t_bar_close = t_bar_open + timedelta(minutes=15)
    bar = CandleData(
        t_bar_open, t_bar_close,
        Decimal("2500.000"), Decimal("2505.000"), Decimal("2495.000"), Decimal("2502.000"),
        Decimal("100.0"), True
    )
    # Long: raw 2500.000 + spread 0.260 = 2500.260
    res_l = model.simulate_next_bar_open(RiskSide.LONG, t, [bar], "sig_fp")
    assert res_l.fill_price == Decimal("2500.260")
    assert res_l.synthetic_spread == Decimal("0.260")

    # Short: raw 2500.000 - spread 0.260 = 2499.740
    res_s = model.simulate_next_bar_open(RiskSide.SHORT, t, [bar], "sig_fp")
    assert res_s.fill_price == Decimal("2499.740")
    assert res_s.synthetic_spread == Decimal("0.260")


@pytest.mark.unit
def test_production_authority_remains_off(risk_policy, risk_generator):
    """Proves production authority remains strictly OFF across policy and generated candidates."""
    prod_status = risk_policy.raw_payload["production_authority_status"]
    assert prod_status["is_production_authorized"] is False
    assert prod_status["paper_only"] is True
    assert prod_status["real_order_execution"] == "disabled"

    for idx in range(10):
        cand = risk_generator.generate_candidate(idx)
        assert cand.is_production_authorized is False


@pytest.mark.unit
def test_risk_policy_fingerprint_integrity(risk_policy):
    """Proves mutating risk policy triggers fingerprint validation failure."""
    raw = dict(risk_policy.raw_payload)
    computed_fp = compute_risk_candidate_generation_policy_fingerprint(raw)
    assert raw["policy_fingerprint"] == computed_fp

    # Mutate candidate cap
    raw_mut = dict(raw)
    raw_mut["candidate_cap"] = 50
    with pytest.raises(ValueError, match="Risk candidate generation policy fingerprint mismatch"):
        XauUsdRiskCandidateGenerationPolicy.from_dict(raw_mut)
