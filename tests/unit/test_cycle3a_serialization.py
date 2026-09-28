import copy

import pytest

from apps.backtests.tasks import (
    resolve_xauusd_cycle3a_profile,
)
from engine.core.types import (
    CalendarEffectEntry,
    RegimeType,
    SampleEvaluation,
    SampleQuality,
    SessionExpectancyEntry,
    SessionType,
)
from engine.cycles.profile import (
    CalibrationStatus,
    Cycle3AProfile,
)
from engine.cycles.serialization import (
    CYCLE3A_PROFILE_SCHEMA,
    deserialize_cycle3a_profile,
    is_cycle3a_production_profile_complete,
    serialize_cycle3a_profile,
)


def _make_profile(
    status=CalibrationStatus.CANDIDATE_NOT_FROZEN,
):
    sample_eval = SampleEvaluation(
        n_raw=40,
        independent_after_overlap=38,
        temporal_clusters=36,
        hhi_norm=0.05,
        regime_discount=0.02,
        clustering_discount=0.05,
        effective_n=35.28,
        quality=SampleQuality.LOW,
        weight_multiplier=0.5,
        is_blocked=False,
        message="Certified A16 test evidence",
    )

    session_entry = SessionExpectancyEntry(
        session=SessionType.LONDON,
        regime=RegimeType.BULL_TREND,
        sample_count=120,
        effective_n=100.0,
        win_rate=0.60,
        expectancy_r=0.20,
        is_statistically_significant=True,
    )

    calendar_entry = CalendarEffectEntry(
        bucket="DOW_2_HOUR_14",
        sample_count=120,
        effective_n=100.0,
        win_rate=0.61,
        expectancy_r=0.18,
        stability=0.80,
        is_statistically_significant=True,
    )

    return Cycle3AProfile(
        name="XAUUSD_CYCLE3A_TEST",
        calibration_status=status,
        target_instrument="XAUUSD",
        timeframe="15m",

        session_max_score=15.0,
        session_min_effective_n=30.0,
        session_expectancy_multiplier=30.0,
        session_expectancy_table={
            (
                SessionType.LONDON,
                RegimeType.BULL_TREND,
            ): session_entry,
        },

        swing_max_score=20.0,
        swing_min_effective_n=30.0,
        swing_sample_evaluation=sample_eval,
        swing_maturity_bands={
            "P75_90": 20.0,
            "P50_75": 15.0,
            "P25_50": 10.0,
            "P90_plus": 8.0,
            "default": 5.0,
        },
        historical_durations=tuple(
            [5, 10, 15, 20, 25] * 8
        ),
        swing_duration_percentiles={
            "P25": 10.0,
            "P50": 15.0,
            "P75": 20.0,
            "P90": 25.0,
        },

        calendar_max_score=5.0,
        calendar_min_effective_n=30.0,
        calendar_stability_threshold=0.60,
        calendar_expectancy_multiplier=10.0,
        calendar_effect_table={
            "DOW_2_HOUR_14": calendar_entry,
        },

        macro_blackout_pre_minutes=30,
        macro_blackout_post_minutes=30,
        macro_clear_window_far_minutes=120,
        macro_clear_window_near_minutes=60,
        macro_clear_bonus_far=5.0,
        macro_clear_bonus_near=2.0,

        details={
            "calibration_version": "cycle3a-test-v1",
            "data_fingerprint": "a" * 64,
            "generated_at": "2026-09-21T00:00:00+00:00",
        },
    )


def test_cycle3a_profile_roundtrip_is_lossless():
    original = _make_profile()

    envelope = serialize_cycle3a_profile(
        original
    )

    restored = deserialize_cycle3a_profile(
        envelope,
        expected_instrument="XAUUSD",
        expected_timeframe="15m",
    )

    assert envelope["schema"] == (
        CYCLE3A_PROFILE_SCHEMA
    )
    assert len(
        envelope["profile_fingerprint"]
    ) == 64

    assert restored == original


def test_cycle3a_serialization_is_deterministic():
    profile = _make_profile()

    first = serialize_cycle3a_profile(
        profile
    )

    restored = deserialize_cycle3a_profile(
        first
    )

    second = serialize_cycle3a_profile(
        restored
    )

    assert (
        first["profile_fingerprint"]
        == second["profile_fingerprint"]
    )

    assert first["profile"] == second["profile"]


def test_cycle3a_tamper_is_rejected():
    envelope = serialize_cycle3a_profile(
        _make_profile()
    )

    tampered = copy.deepcopy(envelope)

    tampered["profile"][
        "calendar_max_score"
    ] = 99.0

    with pytest.raises(
        ValueError,
        match="fingerprint",
    ):
        deserialize_cycle3a_profile(
            tampered
        )


def test_cycle3a_wrong_schema_is_rejected():
    envelope = serialize_cycle3a_profile(
        _make_profile()
    )

    envelope["schema"] = (
        "aurumiq.cycle3a.profile.v999"
    )

    with pytest.raises(
        ValueError,
        match="schema",
    ):
        deserialize_cycle3a_profile(
            envelope
        )


def test_cycle3a_target_and_timeframe_are_guarded():
    envelope = serialize_cycle3a_profile(
        _make_profile()
    )

    with pytest.raises(
        ValueError,
        match="instrument",
    ):
        deserialize_cycle3a_profile(
            envelope,
            expected_instrument="XAUT",
        )

    with pytest.raises(
        ValueError,
        match="timeframe",
    ):
        deserialize_cycle3a_profile(
            envelope,
            expected_timeframe="1h",
        )


def test_candidate_profile_cannot_gain_production_authority():
    envelope = serialize_cycle3a_profile(
        _make_profile(
            CalibrationStatus.CANDIDATE_NOT_FROZEN
        )
    )

    with pytest.raises(
        ValueError,
        match="PRODUCTION_FROZEN",
    ):
        deserialize_cycle3a_profile(
            envelope,
            expected_instrument="XAUUSD",
            expected_timeframe="15m",
            require_production_frozen=True,
        )

    assert (
        resolve_xauusd_cycle3a_profile(
            cycle_3a_profile_dict=envelope,
        )
        is None
    )


def test_complete_frozen_profile_can_resolve():
    profile = _make_profile(
        CalibrationStatus.PRODUCTION_FROZEN
    )

    envelope = serialize_cycle3a_profile(
        profile
    )

    assert (
        is_cycle3a_production_profile_complete(
            profile
        )
        is True
    )

    resolved = (
        resolve_xauusd_cycle3a_profile(
            cycle_3a_profile_dict=envelope,
        )
    )

    assert resolved is not None
    assert (
        resolved.calibration_status
        == CalibrationStatus.PRODUCTION_FROZEN
    )
    assert resolved.target_instrument == "XAUUSD"
    assert resolved.timeframe == "15m"


def test_existing_champion_still_has_no_cycle3a_authority():
    resolved = (
        resolve_xauusd_cycle3a_profile(
            calibration_artifact_id=(
                "xauusd_calibrated_profile_champion"
            )
        )
    )

    assert resolved is None


def test_resolver_rejects_tampered_frozen_profile():
    envelope = serialize_cycle3a_profile(
        _make_profile(
            CalibrationStatus.PRODUCTION_FROZEN
        )
    )

    envelope["profile"][
        "macro_clear_bonus_far"
    ] = 999.0

    assert (
        resolve_xauusd_cycle3a_profile(
            cycle_3a_profile_dict=envelope
        )
        is None
    )

