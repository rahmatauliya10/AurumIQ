"""
Build qualified Phase 3A candidate profile from sealed descriptive evidence.

Deterministic, pure-Python script.
Reads:
    artifacts/calibration/xauusd_phase3a_descriptive_evidence.json
Produces:
    artifacts/calibration/xauusd_phase3a_candidate.json

Governance:
- Status: CANDIDATE_NOT_FROZEN
- Production authority: False
- Scoring authority: False
- Session qualified: 0 / 6 (scoring contribution = 0, session_expectancy_table = None)
- Calendar qualified: dynamically selected (only buckets passing SIG and temporal stability)
- Swing duration: A16-certified empirical duration distribution (P10/P25/P50/P75/P90/P95)
- All scoring parameters strictly None
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.core.types import (
    CalendarEffectEntry,
    SampleEvaluation,
    SampleQuality,
)
from engine.cycles.evidence_replay import (
    PHASE3A_EVIDENCE_STATUS,
    compute_phase3a_evidence_fingerprint,
)
from engine.cycles.profile import (
    CalibrationStatus,
    Cycle3AProfile,
)
from engine.cycles.serialization import (
    _sample_evaluation_from_dict,
    cycle3a_profile_to_payload,
    serialize_cycle3a_profile,
)

CANDIDATE_SCHEMA = "aurumiq.phase3a.candidate_artifact.v1"
CANDIDATE_ID = "xauusd_phase3a_candidate"
CANDIDATE_STATUS = "CANDIDATE_NOT_FROZEN"

EVIDENCE_PATH = (
    ROOT
    / "artifacts"
    / "calibration"
    / "xauusd_phase3a_descriptive_evidence.json"
)

OUTPUT_PATH = (
    ROOT
    / "artifacts"
    / "calibration"
    / f"{CANDIDATE_ID}.json"
)


def _git_bin() -> str:
    if shutil.which("git"):
        return "git"
    fallback = Path(
        r"C:\Users\PLANT03\AppData\Local\Programs\Git\cmd\git.exe"
    )
    if fallback.exists():
        return str(fallback)
    return "git"


def get_git_revision(root: Optional[Path] = None) -> str:
    search_root = root or ROOT
    try:
        revision = subprocess.check_output(
            [_git_bin(), "rev-parse", "HEAD"],
            cwd=search_root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if (
            len(revision) == 40
            and all(c in "0123456789abcdef" for c in revision.lower())
        ):
            return revision
    except Exception:
        pass
    return "0" * 40


def compute_descriptive_artifact_fingerprint(
    artifact: Mapping[str, Any],
) -> str:
    payload = {
        key: value
        for key, value in artifact.items()
        if key not in (
            "created_at",
            "artifact_fingerprint",
        )
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_candidate_artifact_fingerprint(
    data: Mapping[str, Any],
) -> str:
    payload = {
        k: v
        for k, v in data.items()
        if k not in (
            "created_at",
            "artifact_fingerprint",
            "candidate_fingerprint",
        )
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_descriptive_evidence(
    evidence_data: Mapping[str, Any],
) -> None:
    if not isinstance(evidence_data, Mapping):
        raise ValueError("Descriptive evidence must be a mapping.")

    status = evidence_data.get("status")
    if status != PHASE3A_EVIDENCE_STATUS:
        raise ValueError(
            f"Expected status {PHASE3A_EVIDENCE_STATUS}, found '{status}'."
        )

    if evidence_data.get("production_authority") is not False:
        raise ValueError(
            "Evidence production_authority must be False."
        )

    if evidence_data.get("candidate_profile_authority") is not False:
        raise ValueError(
            "Evidence candidate_profile_authority must be False."
        )

    evidence = evidence_data.get("evidence")
    if not isinstance(evidence, Mapping):
        raise ValueError("Missing 'evidence' body in descriptive artifact.")

    if evidence.get("production_authority") is not False:
        raise ValueError(
            "Inner evidence production_authority must be False."
        )

    if evidence.get("candidate_profile_authority") is not False:
        raise ValueError(
            "Inner evidence candidate_profile_authority must be False."
        )

    # Validate evidence fingerprint
    declared_ev_fp = evidence_data.get("evidence_fingerprint")
    if not declared_ev_fp or not isinstance(declared_ev_fp, str):
        raise ValueError("Missing evidence_fingerprint.")

    computed_ev_fp = compute_phase3a_evidence_fingerprint(
        dict(evidence)
    )
    if declared_ev_fp.lower() != computed_ev_fp.lower():
        raise ValueError(
            f"Evidence fingerprint mismatch: declared={declared_ev_fp}, "
            f"computed={computed_ev_fp}."
        )

    # Validate artifact fingerprint
    declared_art_fp = evidence_data.get("artifact_fingerprint")
    if not declared_art_fp or not isinstance(declared_art_fp, str):
        raise ValueError("Missing artifact_fingerprint.")

    computed_art_fp = compute_descriptive_artifact_fingerprint(
        evidence_data
    )
    if declared_art_fp.lower() != computed_art_fp.lower():
        raise ValueError(
            f"Artifact fingerprint mismatch: declared={declared_art_fp}, "
            f"computed={computed_art_fp}."
        )


def qualify_session_buckets(
    evidence: Mapping[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Tuple[str, str]]]:
    session_evidence = evidence.get("session_evidence", [])
    temporal_stability = (
        evidence.get("temporal_stability", {})
        .get("session", {})
        .get("buckets", [])
    )
    stability_map = {
        (
            b.get("session"),
            b.get("regime"),
        ): b
        for b in temporal_stability
    }

    qualified_entries: List[Dict[str, Any]] = []
    qualified_keys: List[Tuple[str, str]] = []

    for row in session_evidence:
        session_name = row.get("session")
        regime_name = row.get("regime")
        key = (session_name, regime_name)
        st = stability_map.get(key, {})

        is_sig = bool(row.get("is_statistically_significant", False))
        is_stable = bool(st.get("temporal_stability_passed", False))

        if is_sig and is_stable:
            qualified_entries.append(dict(row))
            qualified_keys.append(key)

    qualified_keys.sort()
    return qualified_entries, qualified_keys


def qualify_calendar_buckets(
    evidence: Mapping[str, Any],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    calendar_evidence = evidence.get("calendar_evidence", [])
    temporal_stability = (
        evidence.get("temporal_stability", {})
        .get("calendar", {})
        .get("buckets", [])
    )
    stability_map = {
        b.get("bucket"): b
        for b in temporal_stability
    }

    qualified_entries: List[Dict[str, Any]] = []
    qualified_keys: List[str] = []

    for row in calendar_evidence:
        bucket = row.get("bucket")
        st = stability_map.get(bucket, {})

        is_sig = bool(row.get("is_statistically_significant", False))
        is_stable = bool(st.get("temporal_stability_passed", False))

        if is_sig and is_stable:
            qualified_entries.append(dict(row))
            qualified_keys.append(bucket)

    qualified_keys.sort()
    return qualified_entries, qualified_keys


def build_phase3a_candidate_profile(
    evidence_data: Mapping[str, Any],
    code_revision: Optional[str] = None,
    created_at: Optional[str] = None,
) -> Tuple[Dict[str, Any], Cycle3AProfile]:
    validate_descriptive_evidence(evidence_data)

    evidence = evidence_data["evidence"]
    revision = code_revision or get_git_revision()
    iso_created_at = (
        created_at
        or datetime.now(timezone.utc).isoformat()
    )

    # 1. Dynamic Qualification
    _, qualified_session_keys = qualify_session_buckets(evidence)
    _, qualified_calendar_keys = qualify_calendar_buckets(evidence)

    # 2. Calendar Effect Table (only qualified buckets)
    cal_ev_map = {
        r["bucket"]: r
        for r in evidence.get("calendar_evidence", [])
    }
    cal_st_map = {
        b["bucket"]: b
        for b in (
            evidence.get("temporal_stability", {})
            .get("calendar", {})
            .get("buckets", [])
        )
    }

    calendar_effect_table: Dict[str, CalendarEffectEntry] = {}
    for bucket in qualified_calendar_keys:
        r = cal_ev_map[bucket]
        st = cal_st_map[bucket]
        calendar_effect_table[bucket] = CalendarEffectEntry(
            bucket=bucket,
            sample_count=int(r["sample_count"]),
            effective_n=float(r["effective_n"]),
            win_rate=float(r["win_rate"]),
            expectancy_r=float(r["expectancy_r"]),
            stability=float(st.get("stability_score", 0.0)),
            is_statistically_significant=True,
        )

    # 3. Swing duration & A16 evaluation
    swing_evidence = evidence.get("swing_evidence", {})
    swing_a16 = swing_evidence.get("a16", {})
    swing_eval_dict = swing_a16.get("evaluation")

    sample_eval = (
        _sample_evaluation_from_dict(swing_eval_dict)
        if swing_eval_dict
        else None
    )

    swing_percentiles = swing_evidence.get(
        "known_duration_percentiles", {}
    )

    # 4. Details / Provenance
    details = {
        "source_artifact": "xauusd_phase3a_descriptive_evidence",
        "source_evidence_fingerprint": evidence_data.get(
            "evidence_fingerprint"
        ),
        "source_artifact_fingerprint": evidence_data.get(
            "artifact_fingerprint"
        ),
        "dataset_fingerprint": evidence.get("dataset_fingerprint"),
        "code_revision": revision,
        "a16_policy_fingerprint": (
            evidence.get("a16", {}).get("policy_fingerprint")
        ),
        "significance_policy_fingerprint": (
            evidence.get("statistical_significance", {}).get(
                "policy_fingerprint"
            )
        ),
        "fold_stability_policy_fingerprint": (
            evidence.get("temporal_stability", {}).get(
                "policy_fingerprint"
            )
        ),
        "qualification_rule": (
            "is_statistically_significant and temporal_stability_passed"
        ),
        "qualified_session_count": len(qualified_session_keys),
        "qualified_calendar_count": len(qualified_calendar_keys),
        "production_authority": False,
        "scoring_authority": False,
    }

    # 5. Build immutable Cycle3AProfile (all scoring parameters strictly None)
    profile = Cycle3AProfile(
        name="XAUUSD_CYCLE3A_CANDIDATE",
        calibration_status=CalibrationStatus.CANDIDATE_NOT_FROZEN,
        target_instrument="XAUUSD",
        timeframe="15m",

        session_max_score=None,
        session_min_effective_n=None,
        session_expectancy_multiplier=None,
        session_expectancy_table=None,

        swing_max_score=None,
        swing_min_effective_n=None,
        swing_sample_evaluation=sample_eval,
        swing_maturity_bands=None,
        historical_durations=None,
        swing_duration_percentiles=swing_percentiles,

        calendar_max_score=None,
        calendar_min_effective_n=None,
        calendar_stability_threshold=None,
        calendar_expectancy_multiplier=None,
        calendar_effect_table=calendar_effect_table or None,

        macro_blackout_pre_minutes=None,
        macro_blackout_post_minutes=None,
        macro_clear_window_far_minutes=None,
        macro_clear_window_near_minutes=None,
        macro_clear_bonus_far=None,
        macro_clear_bonus_near=None,

        details=details,
    )

    profile_payload = cycle3a_profile_to_payload(profile)
    profile_envelope = serialize_cycle3a_profile(profile)

    candidate_artifact: Dict[str, Any] = {
        "schema": CANDIDATE_SCHEMA,
        "artifact_id": CANDIDATE_ID,
        "instrument": "XAUUSD",
        "target_instrument": "XAUUSD",
        "timeframe": "15m",
        "status": CANDIDATE_STATUS,
        "calibration_status": CANDIDATE_STATUS,
        "production_authority": False,
        "scoring_authority": False,
        "candidate_profile_authority": False,
        "created_at": iso_created_at,
        "code_revision": revision,
        "dataset_fingerprint": evidence.get("dataset_fingerprint"),
        "source_evidence_fingerprint": evidence_data.get(
            "evidence_fingerprint"
        ),
        "source_artifact_fingerprint": evidence_data.get(
            "artifact_fingerprint"
        ),
        "a16_policy_fingerprint": (
            evidence.get("a16", {}).get("policy_fingerprint")
        ),
        "significance_policy_fingerprint": (
            evidence.get("statistical_significance", {}).get(
                "policy_fingerprint"
            )
        ),
        "fold_stability_policy_fingerprint": (
            evidence.get("temporal_stability", {}).get(
                "policy_fingerprint"
            )
        ),
        "qualification_rule": (
            "is_statistically_significant and temporal_stability_passed"
        ),
        "qualified_session_count": len(qualified_session_keys),
        "qualified_calendar_count": len(qualified_calendar_keys),
        "qualification": {
            "rule": (
                "is_statistically_significant and temporal_stability_passed"
            ),
            "session": {
                "evaluated_count": len(
                    evidence.get("session_evidence", [])
                ),
                "qualified_count": len(qualified_session_keys),
                "qualified_buckets": [
                    f"{s}:{r}" for s, r in qualified_session_keys
                ],
            },
            "calendar": {
                "evaluated_count": len(
                    evidence.get("calendar_evidence", [])
                ),
                "qualified_count": len(qualified_calendar_keys),
                "qualified_buckets": qualified_calendar_keys,
            },
            "swing": {
                "a16_certified": bool(
                    swing_evidence.get("effective_n_certified", False)
                ),
                "raw_count": int(
                    swing_evidence.get("known_duration_raw_count", 0)
                ),
                "effective_n": float(
                    swing_evidence.get("effective_n", 0.0)
                ),
                "cross_gap_duration_pairs": int(
                    swing_evidence.get(
                        "cross_gap_duration_pairs", 0
                    )
                ),
                "known_duration_percentiles": swing_percentiles,
            },
        },
        "candidate_profile": profile_payload,
        "cycle_3a_profile": profile_envelope,
    }

    fp = compute_candidate_artifact_fingerprint(candidate_artifact)
    candidate_artifact["artifact_fingerprint"] = fp
    candidate_artifact["candidate_fingerprint"] = fp

    return candidate_artifact, profile


def main() -> int:
    if not EVIDENCE_PATH.exists():
        print(
            f"ERROR: Missing descriptive evidence file: {EVIDENCE_PATH}",
            file=sys.stderr,
        )
        return 1

    with EVIDENCE_PATH.open("r", encoding="utf-8") as f:
        evidence_data = json.load(f)

    candidate_artifact, _ = build_phase3a_candidate_profile(
        evidence_data
    )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(
            candidate_artifact,
            f,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        f.write("\n")

    q = candidate_artifact["qualification"]

    print(f"STATUS = {candidate_artifact['status']}")
    print(
        f"SESSION_QUALIFIED = {q['session']['qualified_count']}/"
        f"{q['session']['evaluated_count']}"
    )
    print(
        f"CALENDAR_QUALIFIED = {q['calendar']['qualified_count']}/"
        f"{q['calendar']['evaluated_count']}"
    )
    print(
        f"SWING_A16_CERTIFIED = {q['swing']['a16_certified']}"
    )
    print(
        f"PRODUCTION_AUTHORITY = "
        f"{str(candidate_artifact['production_authority']).lower()}"
    )
    print(
        f"SCORING_AUTHORITY = "
        f"{str(candidate_artifact['scoring_authority']).lower()}"
    )
    print(
        f"CANDIDATE_FINGERPRINT = {candidate_artifact['candidate_fingerprint']}"
    )
    print(f"OUTPUT = {OUTPUT_PATH}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
