from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from engine.core.types import (
    CandleData,
    VolumeEvidenceType,
)
from engine.cycles.evidence_replay import (
    PHASE3A_EVIDENCE_SCHEMA,
    build_phase3a_descriptive_evidence,
    compute_phase3a_evidence_fingerprint,
)


def _candle(
    idx: int,
    base: datetime,
    close: str,
) -> CandleData:
    ts_open = base + timedelta(
        minutes=15 * idx
    )
    px = Decimal(close)

    return CandleData(
        timestamp_open=ts_open,
        timestamp_close=(
            ts_open + timedelta(minutes=15)
        ),
        open=px,
        high=px + Decimal("1"),
        low=px - Decimal("1"),
        close=px,
        volume=Decimal("0"),
        is_closed=True,
        source_id="TEST_XAUUSD",
        volume_evidence=(
            VolumeEvidenceType.UNAVAILABLE
        ),
    )


def test_phase3a_descriptive_replay_is_fail_closed():
    base = datetime(
        2026, 1, 5, 0, 0,
        tzinfo=timezone.utc,
    )

    candles = [
        _candle(
            i,
            base,
            str(2500 + (i % 7)),
        )
        for i in range(40)
    ]

    result = build_phase3a_descriptive_evidence(
        candles=candles,
        instrument="XAUUSD",
        provider="TEST_XAUUSD",
        timeframe="15m",
        code_revision="a" * 40,
    )

    assert (
        result["status"]
        == "DESCRIPTIVE_EVIDENCE_ONLY"
    )

    assert (
        result["production_authority"]
        is False
    )

    assert (
        result["candidate_profile_authority"]
        is False
    )

    assert isinstance(
        result["a16"][
            "effective_n_certified"
        ],
        bool,
    )

    assert (
        result["regime_evidence"][
            "regime"
        ]
        == "UNKNOWN"
    )

    assert (
        result["regime_evidence"][
            "reason"
        ]
        == "XAUUSD_REGIME_CALIBRATION_REQUIRED"
    )


def test_phase3a_session_evidence_uses_unknown_regime_only():
    base = datetime(
        2026, 1, 5, 0, 0,
        tzinfo=timezone.utc,
    )

    candles = [
        _candle(
            i,
            base,
            str(2500 + i),
        )
        for i in range(30)
    ]

    result = build_phase3a_descriptive_evidence(
        candles=candles,
        instrument="XAUUSD",
        provider="TEST_XAUUSD",
        timeframe="15m",
        code_revision="a" * 40,
    )

    rows = result[
        "session_evidence"
    ]

    assert rows

    assert {
        row["regime"]
        for row in rows
    } == {"UNKNOWN"}

    assert all(
        row["effective_n"] >= 0.0
        for row in rows
    )

    assert all(
        row[
            "is_statistically_significant"
        ] is False
        for row in rows
    )


def test_phase3a_replay_detects_market_gap():
    base = datetime(
        2026, 1, 5, 0, 0,
        tzinfo=timezone.utc,
    )

    candles = [
        _candle(0, base, "2500"),
        _candle(1, base, "2501"),

        # indices 2 and 3 missing
        _candle(4, base, "2502"),
        _candle(5, base, "2503"),
    ]

    result = build_phase3a_descriptive_evidence(
        candles=candles,
        instrument="XAUUSD",
        provider="TEST_XAUUSD",
        timeframe="15m",
        code_revision="a" * 40,
    )

    gaps = result["gap_evidence"]

    assert gaps["internal_gap_count"] == 1
    assert gaps["missing_interval_count"] == 2
    assert gaps["contiguous_segment_count"] == 2


def test_phase3a_swing_duration_never_crosses_gap():
    base = datetime(
        2026, 1, 5, 0, 0,
        tzinfo=timezone.utc,
    )

    first = [
        _candle(
            i,
            base,
            str(
                2500
                + [0, 1, 3, 1, 0, 2, 0, 1][i]
            ),
        )
        for i in range(8)
    ]

    second_base = (
        base + timedelta(days=3)
    )

    second = [
        _candle(
            i,
            second_base,
            str(
                2600
                + [0, 2, 4, 2, 0, 3, 0, 1][i]
            ),
        )
        for i in range(8)
    ]

    result = build_phase3a_descriptive_evidence(
        candles=first + second,
        instrument="XAUUSD",
        provider="TEST_XAUUSD",
        timeframe="15m",
        code_revision="a" * 40,
    )

    swing = result["swing_evidence"]

    assert (
        swing[
            "cross_gap_duration_pairs"
        ]
        == 0
    )

    assert (
        swing[
            "contiguous_segment_count"
        ]
        == 2
    )


def test_phase3a_replay_fingerprint_is_deterministic():
    base = datetime(
        2026, 1, 5, 0, 0,
        tzinfo=timezone.utc,
    )

    candles = [
        _candle(
            i,
            base,
            str(2500 + i),
        )
        for i in range(30)
    ]

    first = build_phase3a_descriptive_evidence(
        candles=candles,
        instrument="XAUUSD",
        provider="TEST_XAUUSD",
        timeframe="15m",
        code_revision="a" * 40,
    )

    second = build_phase3a_descriptive_evidence(
        candles=list(reversed(candles)),
        instrument="XAUUSD",
        provider="TEST_XAUUSD",
        timeframe="15m",
        code_revision="a" * 40,
    )

    assert first == second

    assert (
        compute_phase3a_evidence_fingerprint(
            first
        )
        == compute_phase3a_evidence_fingerprint(
            second
        )
    )

    assert PHASE3A_EVIDENCE_SCHEMA == (
        "aurumiq.phase3a."
        "descriptive_evidence.v1"
    )


def test_phase3a_replay_rejects_open_candle():
    base = datetime(
        2026, 1, 5,
        tzinfo=timezone.utc,
    )

    candle = _candle(
        0,
        base,
        "2500",
    )

    object.__setattr__(
        candle,
        "is_closed",
        False,
    )

    with pytest.raises(
        ValueError,
        match="closed",
    ):
        build_phase3a_descriptive_evidence(
            candles=[candle],
            instrument="XAUUSD",
            provider="TEST",
            timeframe="15m",
            code_revision="a" * 40,
        )


def _varied_candles(
    count: int = 600,
):
    base = datetime(
        2026, 1, 5, 0, 0,
        tzinfo=timezone.utc,
    )

    pattern = [
        0, 1, 3, 2, 5, 1, -1, 2,
        4, 0, -2, 1, 3, -1, 2, 0,
    ]

    candles = []

    for idx in range(count):
        px = Decimal(
            str(
                2500
                + pattern[
                    idx % len(pattern)
                ]
                + (idx // 80)
            )
        )

        candles.append(
            CandleData(
                timestamp_open=(
                    base
                    + timedelta(
                        minutes=15 * idx
                    )
                ),
                timestamp_close=(
                    base
                    + timedelta(
                        minutes=15 * (idx + 1)
                    )
                ),
                open=px,
                high=px + Decimal("2"),
                low=px - Decimal("2"),
                close=px,
                volume=Decimal("0"),
                is_closed=True,
                source_id="TEST_XAUUSD",
                volume_evidence=(
                    VolumeEvidenceType.UNAVAILABLE
                ),
            )
        )

    return candles


def test_phase3a_empirical_a16_matches_calibration_samples():
    evidence = build_phase3a_descriptive_evidence(
        candles=_varied_candles(),
        instrument="XAUUSD",
        provider="TEST_XAUUSD",
        timeframe="15m",
        code_revision="a" * 40,
    )

    assert evidence["production_authority"] is False
    assert (
        evidence["candidate_profile_authority"]
        is False
    )

    a16 = evidence["a16"]

    assert len(
        a16["policy_fingerprint"]
    ) == 64

    session_a16 = {
        (
            row["session"],
            row["regime"],
        ): row
        for row in a16[
            "session"
        ]["buckets"]
    }

    for row in evidence[
        "session_evidence"
    ]:
        key = (
            row["session"],
            row["regime"],
        )

        measured = session_a16[key]

        assert (
            measured["n_raw"]
            == row["sample_count"]
        )

        if measured["is_certified"]:
            assert (
                row["effective_n"]
                == measured[
                    "evaluation"
                ]["effective_n"]
            )

        # A16 certification is NOT
        # statistical significance.
        assert (
            row[
                "is_statistically_significant"
            ]
            is False
        )

    calendar_a16 = {
        row["bucket"]: row
        for row in a16[
            "calendar"
        ]["buckets"]
    }

    for row in evidence[
        "calendar_evidence"
    ]:
        measured = calendar_a16[
            row["bucket"]
        ]

        assert (
            measured["n_raw"]
            == row["sample_count"]
        )

        if measured["is_certified"]:
            assert (
                row["effective_n"]
                == measured[
                    "evaluation"
                ]["effective_n"]
            )

        assert (
            row[
                "is_statistically_significant"
            ]
            is False
        )


def test_phase3a_swing_a16_uses_actual_duration_samples():
    evidence = build_phase3a_descriptive_evidence(
        candles=_varied_candles(),
        instrument="XAUUSD",
        provider="TEST_XAUUSD",
        timeframe="15m",
        code_revision="a" * 40,
    )

    swing = evidence["swing_evidence"]
    measured = evidence["a16"]["swing"]

    assert (
        measured["n_raw"]
        == swing[
            "known_duration_raw_count"
        ]
    )

    assert (
        measured["regime_distribution"]
        == {
            "UNKNOWN": measured["n_raw"]
        }
    )

    if measured["is_certified"]:
        assert (
            swing["effective_n"]
            == measured[
                "evaluation"
            ]["effective_n"]
        )
        assert (
            swing[
                "effective_n_certified"
            ]
            is True
        )
