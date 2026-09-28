"""
Governed JSON serialization for Phase 3A Cycle profiles.

Pure Python:
- zero Django imports
- zero network access
- deterministic JSON representation
- deterministic SHA-256 fingerprint
- strict tamper rejection
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum
import hashlib
import hmac
import json
import math
from typing import Any, Dict, Mapping, Optional

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


CYCLE3A_PROFILE_SCHEMA = (
    "aurumiq.cycle3a.profile.v1"
)


def _json_safe(value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, Enum):
        return value.value

    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(
                "Non-finite float forbidden "
                "in Cycle3A serialization."
            )
        return value

    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, datetime):
        if (
            value.tzinfo is None
            or value.tzinfo.utcoffset(value)
            is None
        ):
            raise ValueError(
                "Naive datetime forbidden "
                "in Cycle3A serialization."
            )

        return value.isoformat()

    if isinstance(value, Mapping):
        result = {}

        for key in sorted(
            value.keys(),
            key=lambda item: str(item),
        ):
            if not isinstance(key, str):
                raise TypeError(
                    "Generic Cycle3A JSON mappings "
                    "must use string keys."
                )

            result[key] = _json_safe(
                value[key]
            )

        return result

    if isinstance(value, (list, tuple)):
        return [
            _json_safe(item)
            for item in value
        ]

    if isinstance(value, str):
        return value

    raise TypeError(
        "Unsupported Cycle3A serialization "
        f"type: {type(value).__name__}"
    )


def _sample_evaluation_to_dict(
    evaluation: Optional[SampleEvaluation],
):
    if evaluation is None:
        return None

    return {
        "n_raw": evaluation.n_raw,
        "independent_after_overlap": (
            evaluation.independent_after_overlap
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
        "effective_n": evaluation.effective_n,
        "quality": evaluation.quality.value,
        "weight_multiplier": (
            evaluation.weight_multiplier
        ),
        "is_blocked": evaluation.is_blocked,
        "message": evaluation.message,
    }


def _sample_evaluation_from_dict(
    data: Optional[Mapping[str, Any]],
):
    if data is None:
        return None

    if not isinstance(data, Mapping):
        raise ValueError(
            "swing_sample_evaluation "
            "must be an object."
        )

    try:
        return SampleEvaluation(
            n_raw=int(data["n_raw"]),
            independent_after_overlap=int(
                data[
                    "independent_after_overlap"
                ]
            ),
            temporal_clusters=int(
                data["temporal_clusters"]
            ),
            hhi_norm=float(
                data["hhi_norm"]
            ),
            regime_discount=float(
                data["regime_discount"]
            ),
            clustering_discount=float(
                data["clustering_discount"]
            ),
            effective_n=float(
                data["effective_n"]
            ),
            quality=SampleQuality(
                data["quality"]
            ),
            weight_multiplier=float(
                data["weight_multiplier"]
            ),
            is_blocked=bool(
                data["is_blocked"]
            ),
            message=str(
                data["message"]
            ),
        )
    except (
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise ValueError(
            "Invalid Cycle3A "
            "SampleEvaluation payload."
        ) from exc


def _session_table_to_list(profile):
    table = (
        profile.session_expectancy_table
        or {}
    )

    rows = []

    for key, entry in table.items():
        if (
            not isinstance(key, tuple)
            or len(key) != 2
        ):
            raise ValueError(
                "Invalid session expectancy key."
            )

        sess, regime = key

        if (
            sess != entry.session
            or regime != entry.regime
        ):
            raise ValueError(
                "Session expectancy mapping key "
                "does not match entry."
            )

        rows.append({
            "session": entry.session.value,
            "regime": entry.regime.value,
            "sample_count": (
                entry.sample_count
            ),
            "effective_n": (
                entry.effective_n
            ),
            "win_rate": entry.win_rate,
            "expectancy_r": (
                entry.expectancy_r
            ),
            "is_statistically_significant": (
                entry
                .is_statistically_significant
            ),
        })

    rows.sort(
        key=lambda row: (
            row["session"],
            row["regime"],
        )
    )

    return rows


def _session_table_from_list(rows):
    if rows is None:
        return None

    if not isinstance(rows, list):
        raise ValueError(
            "session_expectancy_table "
            "must be a list."
        )

    table = {}

    for row in rows:
        try:
            session = SessionType(
                row["session"]
            )
            regime = RegimeType(
                row["regime"]
            )

            key = (
                session,
                regime,
            )

            if key in table:
                raise ValueError(
                    "Duplicate session/regime "
                    "calendar key."
                )

            table[key] = (
                SessionExpectancyEntry(
                    session=session,
                    regime=regime,
                    sample_count=int(
                        row["sample_count"]
                    ),
                    effective_n=float(
                        row["effective_n"]
                    ),
                    win_rate=float(
                        row["win_rate"]
                    ),
                    expectancy_r=float(
                        row["expectancy_r"]
                    ),
                    is_statistically_significant=bool(
                        row[
                            "is_statistically_significant"
                        ]
                    ),
                )
            )
        except (
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            raise ValueError(
                "Invalid session expectancy "
                "entry."
            ) from exc

    return table or None


def _calendar_table_to_list(profile):
    table = (
        profile.calendar_effect_table
        or {}
    )

    rows = []

    for key, entry in table.items():
        if key != entry.bucket:
            raise ValueError(
                "Calendar mapping key "
                "does not match entry bucket."
            )

        rows.append({
            "bucket": entry.bucket,
            "sample_count": (
                entry.sample_count
            ),
            "effective_n": (
                entry.effective_n
            ),
            "win_rate": entry.win_rate,
            "expectancy_r": (
                entry.expectancy_r
            ),
            "stability": entry.stability,
            "is_statistically_significant": (
                entry
                .is_statistically_significant
            ),
        })

    rows.sort(
        key=lambda row: row["bucket"]
    )

    return rows


def _calendar_table_from_list(rows):
    if rows is None:
        return None

    if not isinstance(rows, list):
        raise ValueError(
            "calendar_effect_table "
            "must be a list."
        )

    table = {}

    for row in rows:
        try:
            bucket = str(
                row["bucket"]
            )

            if bucket in table:
                raise ValueError(
                    "Duplicate calendar bucket."
                )

            table[bucket] = (
                CalendarEffectEntry(
                    bucket=bucket,
                    sample_count=int(
                        row["sample_count"]
                    ),
                    effective_n=float(
                        row["effective_n"]
                    ),
                    win_rate=float(
                        row["win_rate"]
                    ),
                    expectancy_r=float(
                        row["expectancy_r"]
                    ),
                    stability=float(
                        row["stability"]
                    ),
                    is_statistically_significant=bool(
                        row[
                            "is_statistically_significant"
                        ]
                    ),
                )
            )
        except (
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            raise ValueError(
                "Invalid calendar effect entry."
            ) from exc

    return table or None


def cycle3a_profile_to_payload(
    profile: Cycle3AProfile,
) -> Dict[str, Any]:
    return {
        "name": profile.name,
        "calibration_status": (
            profile.calibration_status.value
        ),
        "target_instrument": (
            profile.target_instrument
        ),
        "timeframe": profile.timeframe,

        "session_max_score": (
            profile.session_max_score
        ),
        "session_min_effective_n": (
            profile.session_min_effective_n
        ),
        "session_expectancy_multiplier": (
            profile
            .session_expectancy_multiplier
        ),
        "session_expectancy_table": (
            _session_table_to_list(profile)
        ),

        "swing_max_score": (
            profile.swing_max_score
        ),
        "swing_min_effective_n": (
            profile.swing_min_effective_n
        ),
        "swing_sample_evaluation": (
            _sample_evaluation_to_dict(
                profile
                .swing_sample_evaluation
            )
        ),
        "swing_maturity_bands": (
            _json_safe(
                profile.swing_maturity_bands
            )
            if profile.swing_maturity_bands
            is not None
            else None
        ),
        "historical_durations": (
            list(profile.historical_durations)
            if profile.historical_durations
            is not None
            else None
        ),
        "swing_duration_percentiles": (
            _json_safe(
                profile
                .swing_duration_percentiles
            )
            if profile
            .swing_duration_percentiles
            is not None
            else None
        ),

        "calendar_max_score": (
            profile.calendar_max_score
        ),
        "calendar_min_effective_n": (
            profile.calendar_min_effective_n
        ),
        "calendar_stability_threshold": (
            profile
            .calendar_stability_threshold
        ),
        "calendar_expectancy_multiplier": (
            profile
            .calendar_expectancy_multiplier
        ),
        "calendar_effect_table": (
            _calendar_table_to_list(profile)
        ),

        "macro_blackout_pre_minutes": (
            profile
            .macro_blackout_pre_minutes
        ),
        "macro_blackout_post_minutes": (
            profile
            .macro_blackout_post_minutes
        ),
        "macro_clear_window_far_minutes": (
            profile
            .macro_clear_window_far_minutes
        ),
        "macro_clear_window_near_minutes": (
            profile
            .macro_clear_window_near_minutes
        ),
        "macro_clear_bonus_far": (
            profile.macro_clear_bonus_far
        ),
        "macro_clear_bonus_near": (
            profile.macro_clear_bonus_near
        ),

        "details": _json_safe(
            profile.details
        ),
    }


def compute_cycle3a_profile_fingerprint(
    profile_payload: Mapping[str, Any],
) -> str:
    canonical = {
        "schema": CYCLE3A_PROFILE_SCHEMA,
        "profile": profile_payload,
    }

    serialized = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )

    return hashlib.sha256(
        serialized.encode("utf-8")
    ).hexdigest()


def serialize_cycle3a_profile(
    profile: Cycle3AProfile,
) -> Dict[str, Any]:
    profile_payload = (
        cycle3a_profile_to_payload(
            profile
        )
    )

    return {
        "schema": CYCLE3A_PROFILE_SCHEMA,
        "profile": profile_payload,
        "profile_fingerprint": (
            compute_cycle3a_profile_fingerprint(
                profile_payload
            )
        ),
    }


def deserialize_cycle3a_profile(
    envelope: Mapping[str, Any],
    expected_instrument: Optional[str] = None,
    expected_timeframe: Optional[str] = None,
    require_production_frozen: bool = False,
) -> Cycle3AProfile:
    if not isinstance(envelope, Mapping):
        raise ValueError(
            "Cycle3A envelope must be an object."
        )

    schema = envelope.get("schema")

    if schema != CYCLE3A_PROFILE_SCHEMA:
        raise ValueError(
            "Invalid Cycle3A profile schema."
        )

    payload = envelope.get("profile")
    fingerprint = envelope.get(
        "profile_fingerprint"
    )

    if not isinstance(payload, Mapping):
        raise ValueError(
            "Cycle3A profile payload missing."
        )

    if (
        not isinstance(fingerprint, str)
        or len(fingerprint) != 64
    ):
        raise ValueError(
            "Cycle3A profile fingerprint "
            "missing or malformed."
        )

    computed = (
        compute_cycle3a_profile_fingerprint(
            payload
        )
    )

    if not hmac.compare_digest(
        fingerprint.lower(),
        computed.lower(),
    ):
        raise ValueError(
            "Cycle3A profile fingerprint "
            "mismatch."
        )

    try:
        status = CalibrationStatus(
            payload["calibration_status"]
        )

        target = str(
            payload["target_instrument"]
        ).strip().upper().replace(
            "/", ""
        )

        timeframe = payload.get(
            "timeframe"
        )

        if not isinstance(timeframe, str):
            raise ValueError(
                "Cycle3A timeframe required."
            )

        if (
            expected_instrument
            is not None
            and target
            != str(expected_instrument)
            .strip()
            .upper()
            .replace("/", "")
        ):
            raise ValueError(
                "Cycle3A instrument mismatch."
            )

        if (
            expected_timeframe
            is not None
            and timeframe
            != expected_timeframe
        ):
            raise ValueError(
                "Cycle3A timeframe mismatch."
            )

        if (
            require_production_frozen
            and status
            != CalibrationStatus
            .PRODUCTION_FROZEN
        ):
            raise ValueError(
                "Cycle3A profile must be "
                "PRODUCTION_FROZEN."
            )

        historical = payload.get(
            "historical_durations"
        )

        if historical is not None:
            if not isinstance(
                historical,
                list,
            ):
                raise ValueError(
                    "historical_durations "
                    "must be a list."
                )

            historical = tuple(
                int(value)
                for value in historical
            )

        return Cycle3AProfile(
            name=str(payload["name"]),
            calibration_status=status,
            target_instrument=target,
            timeframe=timeframe,

            session_max_score=payload.get(
                "session_max_score"
            ),
            session_min_effective_n=payload.get(
                "session_min_effective_n"
            ),
            session_expectancy_multiplier=payload.get(
                "session_expectancy_multiplier"
            ),
            session_expectancy_table=(
                _session_table_from_list(
                    payload.get(
                        "session_expectancy_table"
                    )
                )
            ),

            swing_max_score=payload.get(
                "swing_max_score"
            ),
            swing_min_effective_n=payload.get(
                "swing_min_effective_n"
            ),
            swing_sample_evaluation=(
                _sample_evaluation_from_dict(
                    payload.get(
                        "swing_sample_evaluation"
                    )
                )
            ),
            swing_maturity_bands=payload.get(
                "swing_maturity_bands"
            ),
            historical_durations=historical,
            swing_duration_percentiles=payload.get(
                "swing_duration_percentiles"
            ),

            calendar_max_score=payload.get(
                "calendar_max_score"
            ),
            calendar_min_effective_n=payload.get(
                "calendar_min_effective_n"
            ),
            calendar_stability_threshold=payload.get(
                "calendar_stability_threshold"
            ),
            calendar_expectancy_multiplier=payload.get(
                "calendar_expectancy_multiplier"
            ),
            calendar_effect_table=(
                _calendar_table_from_list(
                    payload.get(
                        "calendar_effect_table"
                    )
                )
            ),

            macro_blackout_pre_minutes=payload.get(
                "macro_blackout_pre_minutes"
            ),
            macro_blackout_post_minutes=payload.get(
                "macro_blackout_post_minutes"
            ),
            macro_clear_window_far_minutes=payload.get(
                "macro_clear_window_far_minutes"
            ),
            macro_clear_window_near_minutes=payload.get(
                "macro_clear_window_near_minutes"
            ),
            macro_clear_bonus_far=payload.get(
                "macro_clear_bonus_far"
            ),
            macro_clear_bonus_near=payload.get(
                "macro_clear_bonus_near"
            ),

            details=payload.get(
                "details"
            ) or {},
        )

    except (
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        if isinstance(exc, ValueError):
            raise

        raise ValueError(
            "Invalid Cycle3A profile payload."
        ) from exc


def is_cycle3a_production_profile_complete(
    profile: Cycle3AProfile,
) -> bool:
    target = (
        str(profile.target_instrument)
        .strip()
        .upper()
        .replace("/", "")
    )

    if (
        profile.calibration_status
        != CalibrationStatus.PRODUCTION_FROZEN
        or target != "XAUUSD"
        or profile.timeframe != "15m"
    ):
        return False

    required = (
        profile.session_max_score,
        profile.session_min_effective_n,
        profile.session_expectancy_multiplier,
        profile.swing_max_score,
        profile.swing_min_effective_n,
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
    )

    if any(value is None for value in required):
        return False

    if not profile.session_expectancy_table:
        return False

    if not profile.historical_durations:
        return False

    if profile.swing_sample_evaluation is None:
        return False

    if not profile.calendar_effect_table:
        return False

    details = profile.details or {}

    if not details.get(
        "calibration_version"
    ):
        return False

    data_fp = details.get(
        "data_fingerprint"
    )

    if (
        not isinstance(data_fp, str)
        or len(data_fp) != 64
    ):
        return False

    return True
