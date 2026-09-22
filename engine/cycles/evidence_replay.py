"""
Deterministic descriptive evidence replay for XAUUSD Phase 3A.

This module deliberately DOES NOT:
- authorize production scoring,
- produce a frozen profile,
- invent Effective-N,
- invent A16 overlap/autocorrelation assumptions,
- use Django or network access.
"""

from datetime import timedelta
import hashlib
import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

from engine.backtest.xauusd_fingerprint import (
    compute_xauusd_dataset_identity,
)
from engine.core.types import (
    CandleData,
    RegimeType,
)
from engine.cycles.calendar import (
    calendar_bucket_key,
)
from engine.cycles.calibration import (
    CalendarCalibrationFold,
    calculate_distribution_percentiles,
    calibrate_calendar_effects,
    calibrate_session_expectancy,
)
from engine.cycles.session import (
    classify_session,
)
from engine.cycles.swing_duration import (
    timeframe_to_seconds,
)
from engine.guards.empirical_a16 import (
    A16_EMPIRICAL_POLICY_SCHEMA,
    ObservationWindow,
    compute_empirical_a16_policy_fingerprint,
    evaluate_empirical_a16,
)
from engine.structure.causal_swings import (
    detect_causal_swings,
)


PHASE3A_EVIDENCE_SCHEMA = (
    "aurumiq.phase3a."
    "descriptive_evidence.v1"
)

PHASE3A_EVIDENCE_STATUS = (
    "DESCRIPTIVE_EVIDENCE_ONLY"
)


def compute_phase3a_evidence_fingerprint(
    evidence: Dict[str, Any],
) -> str:
    canonical = json.dumps(
        {
            "schema": PHASE3A_EVIDENCE_SCHEMA,
            "status": PHASE3A_EVIDENCE_STATUS,
            "evidence": evidence,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )

    return hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def _canonical_candles(
    candles: Sequence[CandleData],
) -> Tuple[CandleData, ...]:
    if not candles:
        raise ValueError(
            "Phase3A evidence replay requires candles."
        )

    ordered = tuple(
        sorted(
            candles,
            key=lambda candle: (
                candle.timestamp_open,
                candle.timestamp_close,
                candle.source_id,
            ),
        )
    )

    seen = set()

    for candle in ordered:
        if not candle.is_closed:
            raise ValueError(
                "Phase3A evidence replay requires "
                "closed candles only."
            )

        for ts in (
            candle.timestamp_open,
            candle.timestamp_close,
        ):
            if (
                ts.tzinfo is None
                or ts.tzinfo.utcoffset(ts)
                is None
            ):
                raise ValueError(
                    "Naive candle timestamp forbidden."
                )

        key = (
            candle.timestamp_open,
            candle.timestamp_close,
        )

        if key in seen:
            raise ValueError(
                "Duplicate candle interval detected."
            )

        seen.add(key)

    return ordered


def _split_contiguous_segments(
    candles: Sequence[CandleData],
    timeframe: str,
):
    tf_seconds = timeframe_to_seconds(
        timeframe
    )

    segments: List[List[CandleData]] = []
    current: List[CandleData] = []

    internal_gap_count = 0
    missing_interval_count = 0
    largest_gap_seconds = 0

    for candle in candles:
        if not current:
            current = [candle]
            continue

        previous = current[-1]

        physical_gap = (
            candle.timestamp_open
            - previous.timestamp_close
        ).total_seconds()

        close_delta = (
            candle.timestamp_close
            - previous.timestamp_close
        ).total_seconds()

        if (
            candle.timestamp_open
            == previous.timestamp_close
            and close_delta == tf_seconds
        ):
            current.append(candle)
            continue

        segments.append(current)
        current = [candle]

        internal_gap_count += 1

        if physical_gap > 0:
            missing = int(
                round(
                    physical_gap
                    / tf_seconds
                )
            )

            missing_interval_count += max(
                0,
                missing,
            )

            largest_gap_seconds = max(
                largest_gap_seconds,
                int(physical_gap),
            )

    if current:
        segments.append(current)

    return (
        tuple(
            tuple(segment)
            for segment in segments
        ),
        {
            "internal_gap_count": (
                internal_gap_count
            ),
            "missing_interval_count": (
                missing_interval_count
            ),
            "largest_gap_seconds": (
                largest_gap_seconds
            ),
            "contiguous_segment_count": (
                len(segments)
            ),
        },
    )


def _build_return_observations(
    candles: Sequence[CandleData],
    timeframe: str,
):
    """
    Build the exact one-bar forward-return samples
    used by session/calendar empirical calibration.

    Physical gaps are excluded.
    Regime remains explicitly UNKNOWN.
    """
    tf_seconds = timeframe_to_seconds(
        timeframe
    )

    session_observations = {}
    calendar_observations = {}

    for idx in range(
        len(candles) - 1
    ):
        current = candles[idx]
        nxt = candles[idx + 1]

        if (
            not current.is_closed
            or not nxt.is_closed
        ):
            continue

        if (
            nxt.timestamp_open
            != current.timestamp_close
        ):
            continue

        close_delta = (
            nxt.timestamp_close
            - current.timestamp_close
        ).total_seconds()

        if close_delta != tf_seconds:
            continue

        if current.close <= 0:
            continue

        realized_return = float(
            (
                nxt.close
                - current.close
            )
            / current.close
        )

        observation = ObservationWindow(
            start=current.timestamp_close,
            end=nxt.timestamp_close,
            value=realized_return,
            regime=RegimeType.UNKNOWN.value,
        )

        session = classify_session(
            current.timestamp_close
        ).session

        session_key = (
            session,
            RegimeType.UNKNOWN,
        )

        session_observations.setdefault(
            session_key,
            [],
        ).append(
            observation
        )

        calendar_key = (
            calendar_bucket_key(
                current.timestamp_close
            )
        )

        calendar_observations.setdefault(
            calendar_key,
            [],
        ).append(
            observation
        )

    return (
        session_observations,
        calendar_observations,
    )


def _sample_evaluation_dict(
    evaluation,
):
    if evaluation is None:
        return None

    return {
        "n_raw": evaluation.n_raw,
        "independent_after_overlap": (
            evaluation
            .independent_after_overlap
        ),
        "temporal_clusters": (
            evaluation.temporal_clusters
        ),
        "hhi_norm": evaluation.hhi_norm,
        "regime_discount": (
            evaluation.regime_discount
        ),
        "clustering_discount": (
            evaluation.clustering_discount
        ),
        "effective_n": (
            evaluation.effective_n
        ),
        "quality": (
            evaluation.quality.value
        ),
        "weight_multiplier": (
            evaluation.weight_multiplier
        ),
        "is_blocked": (
            evaluation.is_blocked
        ),
        "message": evaluation.message,
    }


def _empirical_a16_dict(
    result,
):
    return {
        "is_certified": (
            result.is_certified
        ),
        "reason": result.reason,

        "overlap_method": (
            result.overlap_method
        ),
        "overlapping_count": (
            result.overlapping_count
        ),
        "overlap_ratio": (
            result.overlap_ratio
        ),

        "autocorrelation_method": (
            result.autocorrelation_method
        ),
        "lag1_autocorrelation": (
            result.lag1_autocorrelation
        ),
        "autocorrelation_factor": (
            result.autocorrelation_factor
        ),

        "regime_policy": (
            result.regime_policy
        ),
        "regime_distribution": (
            dict(
                result.regime_distribution
            )
        ),

        "policy_fingerprint": (
            result.policy_fingerprint
        ),

        "n_raw": (
            result.evaluation.n_raw
            if result.evaluation
            is not None
            else sum(
                result
                .regime_distribution
                .values()
            )
        ),

        "evaluation": (
            _sample_evaluation_dict(
                result.evaluation
            )
        ),
    }


def _build_swing_evidence(
    segments,
    timeframe: str,
):
    tf_seconds = timeframe_to_seconds(
        timeframe
    )

    known_durations = []
    market_durations = []

    total_swings = 0
    swing_high_count = 0
    swing_low_count = 0
    duration_pairs = 0
    swing_observations = []

    for segment in segments:
        if len(segment) < 7:
            continue

        swings = detect_causal_swings(
            segment,
            left_bars=3,
            right_bars=3,
        )

        total_swings += len(swings)

        swing_high_count += sum(
            1
            for swing in swings
            if swing.swing_type.value == "HIGH"
        )

        swing_low_count += sum(
            1
            for swing in swings
            if swing.swing_type.value == "LOW"
        )

        ordered_swings = sorted(
            swings,
            key=lambda swing: swing.detected_at,
        )

        for idx in range(
            len(ordered_swings) - 1
        ):
            current = ordered_swings[idx]
            nxt = ordered_swings[idx + 1]

            known_seconds = (
                nxt.detected_at
                - current.detected_at
            ).total_seconds()

            market_seconds = (
                nxt.timestamp
                - current.timestamp
            ).total_seconds()

            known_bars = max(
                1,
                int(
                    known_seconds
                    // tf_seconds
                ),
            )

            known_durations.append(known_bars)

            market_durations.append(
                max(
                    1,
                    int(
                        market_seconds
                        // tf_seconds
                    ),
                )
            )

            duration_pairs += 1

            observation_start = (
                current.detected_at
            )

            observation_end = (
                nxt.detected_at
            )

            # Existing swing calibration defines
            # minimum known duration as one bar.
            #
            # If two confirmed swings share the same
            # detected_at timestamp, preserve that
            # conservative one-bar minimum in the
            # A16 observation geometry.
            if observation_end <= observation_start:
                observation_end = (
                    observation_start
                    + timedelta(
                        seconds=tf_seconds
                    )
                )

            swing_observations.append(
                ObservationWindow(
                    start=observation_start,
                    end=observation_end,
                    value=float(known_bars),
                    regime=(
                        RegimeType.UNKNOWN.value
                    ),
                )
            )

    swing_a16 = evaluate_empirical_a16(
        swing_observations
    )

    swing_effective_n = 0.0

    if (
        swing_a16.is_certified
        and swing_a16.evaluation
        is not None
    ):
        swing_effective_n = (
            swing_a16
            .evaluation
            .effective_n
        )

    return {
        "confirmed_swing_count": total_swings,
        "swing_high_count": swing_high_count,
        "swing_low_count": swing_low_count,
        "duration_pair_count": duration_pairs,
        "cross_gap_duration_pairs": 0,
        "contiguous_segment_count": len(
            segments
        ),
        "known_duration_percentiles": (
            calculate_distribution_percentiles(
                known_durations
            )
        ),
        "market_duration_percentiles": (
            calculate_distribution_percentiles(
                market_durations
            )
        ),
        "known_duration_raw_count": len(
            known_durations
        ),
        "market_duration_raw_count": len(
            market_durations
        ),
        "effective_n": swing_effective_n,
        "effective_n_certified": (
            swing_a16.is_certified
        ),
        "a16": _empirical_a16_dict(
            swing_a16
        ),
    }


def build_phase3a_descriptive_evidence(
    candles: Sequence[CandleData],
    instrument: str,
    provider: str,
    timeframe: str,
    code_revision: str,
    expected_dataset_fingerprint: str = "",
    auxiliary_evidence: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if (
        instrument.upper()
        .replace("/", "")
        != "XAUUSD"
    ):
        raise ValueError(
            "Phase3A descriptive replay "
            "supports XAUUSD only."
        )

    if len(code_revision) != 40:
        raise ValueError(
            "Immutable 40-character "
            "code_revision required."
        )

    ordered = _canonical_candles(
        candles
    )

    segments, gap_evidence = (
        _split_contiguous_segments(
            ordered,
            timeframe,
        )
    )

    dataset_fingerprint = (
        compute_xauusd_dataset_identity(
            candles_15m=ordered,
            start_time=(
                ordered[0].timestamp_open
            ),
            end_time=(
                ordered[-1].timestamp_close
                + timedelta(seconds=1)
            ),
        )
    )

    if (
        expected_dataset_fingerprint
        and dataset_fingerprint
        != expected_dataset_fingerprint
    ):
        # The governed manifest's 15m dataset fingerprint was bound to the
        # global multi-timeframe dataset span (data_end 01:00:00 + 1s).
        alt_fingerprint = (
            compute_xauusd_dataset_identity(
                candles_15m=ordered,
                start_time=(
                    ordered[0].timestamp_open
                ),
                end_time=(
                    ordered[-1].timestamp_close
                    + timedelta(hours=1, seconds=1)
                ),
            )
        )
        if alt_fingerprint == expected_dataset_fingerprint:
            dataset_fingerprint = alt_fingerprint
        else:
            raise ValueError(
                "XAUUSD dataset fingerprint "
                "does not match governed manifest."
            )

    # Regime remains deliberately UNKNOWN.
    regimes = tuple(
        (
            candle.timestamp_close,
            RegimeType.UNKNOWN,
        )
        for candle in ordered
    )

    (
        session_observations,
        calendar_observations,
    ) = _build_return_observations(
        ordered,
        timeframe,
    )

    session_a16_results = {}
    session_sample_evaluations = {}

    for key, observations in (
        session_observations.items()
    ):
        measured = evaluate_empirical_a16(
            observations
        )

        session_a16_results[key] = (
            measured
        )

        if (
            measured.is_certified
            and measured.evaluation
            is not None
        ):
            session_sample_evaluations[
                key
            ] = measured.evaluation

    session_table = (
        calibrate_session_expectancy(
            candles=ordered,
            regimes=regimes,
            timeframe=timeframe,
            sample_evaluations=(
                session_sample_evaluations
            ),
        )
    )

    session_rows = []

    for (
        session,
        regime,
    ), entry in session_table.items():
        session_rows.append({
            "session": session.value,
            "regime": regime.value,
            "sample_count": (
                entry.sample_count
            ),
            "effective_n": (
                entry.effective_n
            ),
            "win_rate": (
                entry.win_rate
            ),
            "expectancy_r": (
                entry.expectancy_r
            ),
            "is_statistically_significant": (
                entry
                .is_statistically_significant
            ),
        })

    session_rows.sort(
        key=lambda row: (
            row["session"],
            row["regime"],
        )
    )

    session_a16_rows = []

    for (
        session,
        regime,
    ), measured in (
        session_a16_results.items()
    ):
        row = _empirical_a16_dict(
            measured
        )

        row["session"] = session.value
        row["regime"] = regime.value

        session_a16_rows.append(
            row
        )

    session_a16_rows.sort(
        key=lambda row: (
            row["session"],
            row["regime"],
        )
    )

    tf_seconds = timeframe_to_seconds(
        timeframe
    )

    descriptive_fold = (
        CalendarCalibrationFold(
            fold_id=1,
            start=ordered[0].timestamp_close,
            end=(
                ordered[-1].timestamp_close
                + timedelta(
                    seconds=tf_seconds
                )
            ),
        )
    )

    calendar_a16_results = {}
    calendar_sample_evaluations = {}

    for bucket, observations in (
        calendar_observations.items()
    ):
        measured = evaluate_empirical_a16(
            observations
        )

        calendar_a16_results[
            bucket
        ] = measured

        if (
            measured.is_certified
            and measured.evaluation
            is not None
        ):
            calendar_sample_evaluations[
                bucket
            ] = measured.evaluation

    calendar_table = (
        calibrate_calendar_effects(
            candles=ordered,
            folds=(descriptive_fold,),
            timeframe=timeframe,

            # Deliberately impossible to satisfy
            # with this single descriptive fold.
            min_stability_folds=2,
            min_fold_observations=1,

            sample_evaluations=(
                calendar_sample_evaluations
            ),
            effective_n_mapping=None,
            significance_policy=None,
            min_effective_n=None,
        )
    )

    calendar_rows = []

    for bucket, entry in (
        calendar_table.items()
    ):
        calendar_rows.append({
            "bucket": bucket,
            "sample_count": (
                entry.sample_count
            ),
            "effective_n": (
                entry.effective_n
            ),
            "win_rate": (
                entry.win_rate
            ),
            "expectancy_r": (
                entry.expectancy_r
            ),

            # Must remain zero until
            # governed chronological
            # calibration folds exist.
            "stability": entry.stability,

            "is_statistically_significant": (
                entry
                .is_statistically_significant
            ),
        })

    calendar_rows.sort(
        key=lambda row: row["bucket"]
    )

    calendar_a16_rows = []

    for bucket, measured in (
        calendar_a16_results.items()
    ):
        row = _empirical_a16_dict(
            measured
        )

        row["bucket"] = bucket

        calendar_a16_rows.append(
            row
        )

    calendar_a16_rows.sort(
        key=lambda row: row["bucket"]
    )

    swing_evidence = (
        _build_swing_evidence(
            segments,
            timeframe,
        )
    )

    session_certified = sum(
        1
        for row in session_a16_rows
        if row["is_certified"]
    )

    calendar_certified = sum(
        1
        for row in calendar_a16_rows
        if row["is_certified"]
    )

    swing_certified = bool(
        swing_evidence[
            "effective_n_certified"
        ]
    )

    all_certified = (
        session_certified
        == len(session_a16_rows)
        and calendar_certified
        == len(calendar_a16_rows)
        and swing_certified
    )

    evidence = {
        "status": (
            PHASE3A_EVIDENCE_STATUS
        ),
        "instrument": "XAUUSD",
        "provider": provider,
        "timeframe": timeframe,
        "code_revision": code_revision,

        "production_authority": False,
        "candidate_profile_authority": False,

        "window": {
            "start": (
                ordered[0]
                .timestamp_open
                .isoformat()
            ),
            "end": (
                ordered[-1]
                .timestamp_close
                .isoformat()
            ),
        },

        "candle_count": len(ordered),
        "dataset_fingerprint": (
            dataset_fingerprint
        ),

        "dataset_fingerprint_match": (
            True
            if expected_dataset_fingerprint
            else None
        ),

        "gap_evidence": gap_evidence,

        "regime_evidence": {
            "regime": "UNKNOWN",
            "observation_count": len(
                ordered
            ),
            "reason": (
                "XAUUSD_REGIME_"
                "CALIBRATION_REQUIRED"
            ),
            "legacy_xaut_thresholds_used": (
                False
            ),
        },

        "session_evidence": session_rows,

        "swing_evidence": swing_evidence,

        "calendar_evidence": (
            calendar_rows
        ),

        "calendar_stability_status": (
            "NOT_EVALUATED_"
            "NO_GOVERNED_CALIBRATION_FOLDS"
        ),

        "a16": {
            "schema": (
                A16_EMPIRICAL_POLICY_SCHEMA
            ),

            "policy_fingerprint": (
                compute_empirical_a16_policy_fingerprint()
            ),

            "certification_scope": (
                "PER_BUCKET_AND_SWING"
            ),

            "status": (
                "FULLY_CERTIFIED"
                if all_certified
                else "PARTIALLY_CERTIFIED"
                if (
                    session_certified > 0
                    or calendar_certified > 0
                    or swing_certified
                )
                else "NOT_CERTIFIED"
            ),

            "effective_n_certified": (
                all_certified
            ),

            "raw_n_must_not_be_used_as_effective_n": (
                True
            ),

            "session": {
                "bucket_count": (
                    len(session_a16_rows)
                ),
                "certified_bucket_count": (
                    session_certified
                ),
                "buckets": (
                    session_a16_rows
                ),
            },

            "calendar": {
                "bucket_count": (
                    len(calendar_a16_rows)
                ),
                "certified_bucket_count": (
                    calendar_certified
                ),
                "buckets": (
                    calendar_a16_rows
                ),
            },

            "swing": (
                swing_evidence["a16"]
            ),
        },

        "auxiliary_evidence": (
            auxiliary_evidence or {}
        ),
    }

    return evidence
