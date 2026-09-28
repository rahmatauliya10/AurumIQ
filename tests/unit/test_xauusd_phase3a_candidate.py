"""
Unit tests for XAUUSD Phase 3A candidate profile generation.

Verifies:
1. Candidate status is CANDIDATE_NOT_FROZEN, production and scoring authorities
   are False, scoring parameters are strictly None, and production profile resolver
   rejects candidate profile.
2. Dynamic qualification yields Session qualified = 0/6, Calendar qualified = 3/169,
   and calendar effect keys match qualified buckets exactly.
3. Swing percentiles (P10..P95) and A16 evaluation metrics are identical to sealed evidence.
4. Candidate generation is deterministic and strictly rejects invalid or authority-bearing evidence.
"""

import copy
import json
from pathlib import Path
import pytest

from apps.backtests.tasks import resolve_xauusd_cycle3a_profile
from engine.core.types import SampleQuality
from engine.cycles.profile import CalibrationStatus
from scripts.build_xauusd_phase3a_candidate import (
    CANDIDATE_SCHEMA,
    CANDIDATE_STATUS,
    EVIDENCE_PATH,
    build_phase3a_candidate_profile,
    compute_candidate_artifact_fingerprint,
    qualify_calendar_buckets,
    qualify_session_buckets,
    validate_descriptive_evidence,
)


@pytest.fixture
def sealed_evidence_data():
    if not EVIDENCE_PATH.exists():
        pytest.skip(f"Sealed evidence file not found: {EVIDENCE_PATH}")
    with EVIDENCE_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def test_candidate_status_and_scoring_disabled(sealed_evidence_data):
    """
    1. Candidate = CANDIDATE_NOT_FROZEN and scoring disabled.
    All scoring parameters must be strictly None.
    Production resolver must return None.
    """
    candidate_artifact, profile = build_phase3a_candidate_profile(
        sealed_evidence_data,
        code_revision="a" * 40,
        created_at="2026-09-22T00:00:00+00:00",
    )

    # Top-level artifact authorities & status
    assert candidate_artifact["status"] == CANDIDATE_STATUS
    assert candidate_artifact["calibration_status"] == CANDIDATE_STATUS
    assert candidate_artifact["production_authority"] is False
    assert candidate_artifact["scoring_authority"] is False
    assert candidate_artifact["candidate_profile_authority"] is False

    # Cycle3AProfile object properties
    assert profile.calibration_status == CalibrationStatus.CANDIDATE_NOT_FROZEN
    assert profile.is_production_scoring_enabled is False
    assert profile.target_instrument == "XAUUSD"
    assert profile.timeframe == "15m"

    # All scoring parameters strictly None
    scoring_params = [
        profile.session_max_score,
        profile.session_min_effective_n,
        profile.session_expectancy_multiplier,
        profile.swing_max_score,
        profile.swing_min_effective_n,
        profile.swing_maturity_bands,
        profile.historical_durations,
        profile.calendar_max_score,
        profile.calendar_min_effective_n,
        profile.calendar_stability_threshold,
        profile.calendar_expectancy_multiplier,
        profile.macro_blackout_pre_minutes,
        profile.macro_blackout_post_minutes,
        profile.macro_clear_window_far_minutes,
        profile.macro_clear_window_near_minutes,
        profile.macro_clear_bonus_far,
        profile.macro_clear_bonus_near,
    ]
    for param in scoring_params:
        assert param is None

    # Candidate profile payload also has None for scoring fields
    candidate_payload = candidate_artifact["candidate_profile"]
    assert candidate_payload["session_max_score"] is None
    assert candidate_payload["swing_max_score"] is None
    assert candidate_payload["calendar_max_score"] is None
    assert candidate_payload["macro_blackout_pre_minutes"] is None

    # Session expectancy table is None/empty
    assert profile.session_expectancy_table is None

    # Production resolver MUST reject candidate envelope
    resolved = resolve_xauusd_cycle3a_profile(
        cycle_3a_profile_dict=candidate_artifact["cycle_3a_profile"]
    )
    assert resolved is None


def test_session_zero_and_calendar_three_qualified(sealed_evidence_data):
    """
    2. Session qualified = 0, Calendar qualified = 3,
    and keys candidate == hasil qualification evidence.
    Qualification must be dynamically evaluated from evidence.
    """
    evidence = sealed_evidence_data["evidence"]

    # Evaluate dynamic qualification
    qual_sess_entries, qual_sess_keys = qualify_session_buckets(evidence)
    qual_cal_entries, qual_cal_keys = qualify_calendar_buckets(evidence)

    assert len(qual_sess_keys) == 0
    assert len(qual_cal_keys) == 3

    expected_cal_keys = ["DOW_0_HOUR_23", "DOW_2_HOUR_1", "DOW_4_HOUR_20"]
    assert sorted(qual_cal_keys) == expected_cal_keys

    candidate_artifact, profile = build_phase3a_candidate_profile(
        sealed_evidence_data,
        code_revision="a" * 40,
        created_at="2026-09-22T00:00:00+00:00",
    )

    q = candidate_artifact["qualification"]
    assert q["session"]["qualified_count"] == 0
    assert q["session"]["evaluated_count"] == 6
    assert q["calendar"]["qualified_count"] == 3
    assert q["calendar"]["evaluated_count"] == 169
    assert sorted(q["calendar"]["qualified_buckets"]) == expected_cal_keys

    # Candidate profile calendar effect table keys match qualified buckets
    assert sorted(profile.calendar_effect_table.keys()) == expected_cal_keys

    for key in expected_cal_keys:
        entry = profile.calendar_effect_table[key]
        assert entry.bucket == key
        assert entry.is_statistically_significant is True
        assert entry.stability > 0.90
        assert entry.effective_n > 400.0


def test_swing_percentiles_and_a16_certified(sealed_evidence_data):
    """
    3. Swing percentiles + A16 identik dengan sealed evidence.
    """
    evidence = sealed_evidence_data["evidence"]
    swing_evidence = evidence["swing_evidence"]
    known_pcts = swing_evidence["known_duration_percentiles"]

    candidate_artifact, profile = build_phase3a_candidate_profile(
        sealed_evidence_data,
        code_revision="a" * 40,
        created_at="2026-09-22T00:00:00+00:00",
    )

    q_swing = candidate_artifact["qualification"]["swing"]
    assert q_swing["a16_certified"] is True
    assert q_swing["raw_count"] == 29150
    assert q_swing["effective_n"] == 13953.5
    assert q_swing["cross_gap_duration_pairs"] == 0

    # Percentiles identical to sealed evidence
    assert q_swing["known_duration_percentiles"] == known_pcts
    assert profile.swing_duration_percentiles == known_pcts
    assert profile.swing_duration_percentiles["P10"] == 2.0
    assert profile.swing_duration_percentiles["P50"] == 4.0
    assert profile.swing_duration_percentiles["P95"] == 11.0

    # A16 evaluation in profile
    sample_eval = profile.swing_sample_evaluation
    assert sample_eval is not None
    assert sample_eval.effective_n == 13953.5
    assert sample_eval.n_raw == 29150
    assert sample_eval.quality == SampleQuality.HIGH
    assert sample_eval.weight_multiplier == 1.0
    assert sample_eval.is_blocked is False


def test_candidate_deterministic_and_rejects_invalid_evidence(sealed_evidence_data):
    """
    4. Candidate deterministic + reject evidence jika authority=True
    atau fingerprint/status invalid.
    """
    # 4a. Determinism
    art1, prof1 = build_phase3a_candidate_profile(
        sealed_evidence_data,
        code_revision="b" * 40,
        created_at="2026-09-22T12:00:00+00:00",
    )
    art2, prof2 = build_phase3a_candidate_profile(
        sealed_evidence_data,
        code_revision="b" * 40,
        created_at="2026-09-22T12:00:00+00:00",
    )

    assert art1["candidate_fingerprint"] == art2["candidate_fingerprint"]
    assert art1 == art2
    assert prof1 == prof2

    # 4b. Reject evidence with production_authority=True
    bad_evidence = copy.deepcopy(sealed_evidence_data)
    bad_evidence["production_authority"] = True
    with pytest.raises(ValueError, match="production_authority"):
        build_phase3a_candidate_profile(bad_evidence)

    # 4c. Reject evidence with candidate_profile_authority=True
    bad_evidence2 = copy.deepcopy(sealed_evidence_data)
    bad_evidence2["candidate_profile_authority"] = True
    with pytest.raises(ValueError, match="candidate_profile_authority"):
        build_phase3a_candidate_profile(bad_evidence2)

    # 4d. Reject evidence with invalid status
    bad_evidence3 = copy.deepcopy(sealed_evidence_data)
    bad_evidence3["status"] = "PRODUCTION_FROZEN"
    with pytest.raises(ValueError, match="Expected status"):
        build_phase3a_candidate_profile(bad_evidence3)

    # 4e. Reject evidence with tampered evidence_fingerprint
    bad_evidence4 = copy.deepcopy(sealed_evidence_data)
    bad_evidence4["evidence_fingerprint"] = "0" * 64
    with pytest.raises(ValueError, match="Evidence fingerprint mismatch"):
        build_phase3a_candidate_profile(bad_evidence4)

    # 4f. Reject evidence with tampered artifact_fingerprint
    bad_evidence5 = copy.deepcopy(sealed_evidence_data)
    bad_evidence5["artifact_fingerprint"] = "0" * 64
    with pytest.raises(ValueError, match="Artifact fingerprint mismatch"):
        build_phase3a_candidate_profile(bad_evidence5)
