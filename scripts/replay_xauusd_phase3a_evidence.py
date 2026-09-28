"""
Replay governed XAUUSD historical evidence for Phase 3A.

READ-ONLY DATABASE WORKFLOW.

Produces:
    artifacts/calibration/
    xauusd_phase3a_descriptive_evidence.json

Governance:
- descriptive evidence only
- zero production authority
- zero candidate-profile authority
- zero invented Effective-N
- zero XAUT regime thresholds
- candle fingerprint must match governed manifest
- macro evidence comes from CURRENT append-only PIT tables,
  not the stale macro status in the old data manifest
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "config.settings.development",
)

import django

django.setup()


from django.db.models import F, Max, Min

from apps.instruments.models import (
    ListingRole,
    ListingStatus,
    MarketListing,
)
from apps.market_data.models import (
    CandleQualityFlag,
    MacroEventIdentity,
    MacroObservationVintage,
    MacroScheduleVintage,
    MarketCandle,
)
from engine.backtest.xauusd_calibration_policy import (
    load_governed_selection_policy,
)
from engine.core.types import (
    CandleData,
    VolumeEvidenceType,
)
from engine.cycles.evidence_replay import (
    PHASE3A_EVIDENCE_SCHEMA,
    build_phase3a_descriptive_evidence,
    compute_phase3a_evidence_fingerprint,
)


ARTIFACT_SCHEMA = (
    "aurumiq.phase3a.descriptive_artifact.v1"
)

ARTIFACT_ID = (
    "xauusd_phase3a_descriptive_evidence"
)

OUTPUT_PATH = (
    ROOT
    / "artifacts"
    / "calibration"
    / f"{ARTIFACT_ID}.json"
)

MANIFEST_PATH = (
    ROOT
    / "artifacts"
    / "calibration"
    / "xauusd_data_manifest.json"
)


def _git_bin() -> str:
    if shutil.which("git"):
        return "git"
    fallback = Path(r"C:\Users\PLANT03\AppData\Local\Programs\Git\cmd\git.exe")
    if fallback.exists():
        return str(fallback)
    return "git"


def _git_revision() -> str:
    revision = subprocess.check_output(
        [_git_bin(), "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
    ).strip()

    if (
        len(revision) != 40
        or any(
            char not in "0123456789abcdef"
            for char in revision.lower()
        )
    ):
        raise RuntimeError(
            "Immutable 40-character Git revision required."
        )

    return revision


def _require_clean_worktree() -> None:
    dirty = subprocess.check_output(
        [_git_bin(), "status", "--porcelain"],
        cwd=ROOT,
        text=True,
    ).strip()

    if dirty:
        raise RuntimeError(
            "WORKTREE_NOT_CLEAN: Commit replay code "
            "before sealing historical evidence."
        )


def _load_manifest() -> Dict[str, Any]:
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(
            f"Missing governed candle manifest: "
            f"{MANIFEST_PATH}"
        )

    with MANIFEST_PATH.open(
        "r",
        encoding="utf-8",
    ) as handle:
        data = json.load(handle)

    if data.get("instrument") != "XAUUSD":
        raise ValueError(
            "Manifest instrument must be XAUUSD."
        )

    expected_fp = (
        data.get(
            "phase6_15m_dataset_fingerprint"
        )
        or data.get("dataset_fingerprint")
    )

    if (
        not isinstance(expected_fp, str)
        or len(expected_fp) != 64
    ):
        raise ValueError(
            "Governed 15m dataset fingerprint "
            "missing from manifest."
        )

    expected_count = (
        data.get("timeframe_counts", {})
        .get("15m")
    )

    if not isinstance(expected_count, int):
        raise ValueError(
            "Governed 15m candle count "
            "missing from manifest."
        )

    return data


def _resolve_primary_listing():
    qs = MarketListing.objects.filter(
        listing_role=(
            ListingRole.PRIMARY_XAUUSD_SPOT
        ),
        status=ListingStatus.ACTIVE,
    ).select_related(
        "instrument",
        "instrument__base_asset",
        "instrument__quote_asset",
    )

    count = qs.count()

    if count != 1:
        raise RuntimeError(
            "Expected exactly one ACTIVE "
            "PRIMARY_XAUUSD_SPOT listing, "
            f"found {count}."
        )

    listing = qs.get()

    instrument = listing.instrument

    if (
        instrument.base_asset.code != "XAU"
        or instrument.quote_asset.code != "USD"
    ):
        raise RuntimeError(
            "PRIMARY_XAUUSD_SPOT listing does not "
            "resolve to XAU/USD."
        )

    return listing


def _to_candle_data(
    record: MarketCandle,
) -> CandleData:
    raw_evidence = getattr(
        record,
        "volume_evidence",
        "UNAVAILABLE",
    )

    try:
        volume_evidence = VolumeEvidenceType(
            raw_evidence
        )
    except (TypeError, ValueError):
        volume_evidence = (
            VolumeEvidenceType.UNAVAILABLE
        )

    return CandleData(
        timestamp_open=record.timestamp_open,
        timestamp_close=record.timestamp_close,
        open=record.open,
        high=record.high,
        low=record.low,
        close=record.close,
        volume=record.volume,
        is_closed=record.is_closed,
        source_id=record.source,
        quote_rate=record.quote_rate,
        close_usd=record.close_usd,
        volume_evidence=volume_evidence,
    )


def _load_governed_candles(
    listing,
    start_time,
    end_time_exclusive,
):
    """
    Window membership follows candle OPEN time.

    This deliberately includes the final:
        2026-08-31 23:45
        -> 2026-09-01 00:00

    while still respecting the governed
    end_time_exclusive boundary.
    """
    qs = (
        MarketCandle.objects.filter(
            instrument=listing.instrument,
            source=listing.provider,
            timeframe="15m",
            timestamp_open__gte=start_time,
            timestamp_open__lt=end_time_exclusive,
            is_closed=True,
        )
        .exclude(
            data_quality_flag=(
                CandleQualityFlag.QUARANTINED
            )
        )
        .order_by(
            "timestamp_open",
            "timestamp_close",
        )
    )

    candles = [
        _to_candle_data(record)
        for record in qs.iterator(
            chunk_size=10000
        )
    ]

    return candles


def _iso_or_none(value):
    return (
        value.astimezone(
            timezone.utc
        ).isoformat()
        if value is not None
        else None
    )


def _build_macro_pit_summary(
    historical_end,
) -> Dict[str, Any]:
    """
    Macro evidence is sourced from the CURRENT
    append-only PIT database.

    Do not use xauusd_data_manifest.json macro
    readiness fields because that manifest predates
    the macro remediation.
    """
    schedule_qs = (
        MacroScheduleVintage.objects.filter(
            scheduled_at__lte=historical_end,
        )
    )

    observation_qs = (
        MacroObservationVintage.objects.filter(
            known_at__lte=historical_end,
        )
    )

    schedule_bounds = schedule_qs.aggregate(
        earliest_known=Min("known_at"),
        latest_known=Max("known_at"),
        earliest_scheduled=Min(
            "scheduled_at"
        ),
        latest_scheduled=Max(
            "scheduled_at"
        ),
    )

    observation_bounds = (
        observation_qs.aggregate(
            earliest_known=Min("known_at"),
            latest_known=Max("known_at"),
            earliest_release=Min(
                "source_published_at"
            ),
            latest_release=Max(
                "source_published_at"
            ),
        )
    )

    schedule_count = schedule_qs.count()

    schedule_known_before_release = (
        schedule_qs.filter(
            known_at__lt=F("scheduled_at")
        ).count()
    )

    schedule_snapshot_count = (
        schedule_qs.filter(
            source_snapshot__isnull=False
        ).count()
    )

    observation_count = (
        observation_qs.count()
    )

    observation_snapshot_count = (
        observation_qs.filter(
            source_snapshot__isnull=False
        ).count()
    )

    def pct(
        numerator: int,
        denominator: int,
    ) -> float:
        if denominator <= 0:
            return 0.0

        return round(
            numerator
            / denominator
            * 100.0,
            2,
        )

    return {
        "identity_count": (
            MacroEventIdentity.objects.count()
        ),
        "schedule": {
            "count": schedule_count,
            "known_before_release_count": (
                schedule_known_before_release
            ),
            "known_before_release_pct": pct(
                schedule_known_before_release,
                schedule_count,
            ),
            "with_source_snapshot_count": (
                schedule_snapshot_count
            ),
            "with_source_snapshot_pct": pct(
                schedule_snapshot_count,
                schedule_count,
            ),
            "earliest_known": _iso_or_none(
                schedule_bounds[
                    "earliest_known"
                ]
            ),
            "latest_known": _iso_or_none(
                schedule_bounds[
                    "latest_known"
                ]
            ),
            "earliest_scheduled": (
                _iso_or_none(
                    schedule_bounds[
                        "earliest_scheduled"
                    ]
                )
            ),
            "latest_scheduled": (
                _iso_or_none(
                    schedule_bounds[
                        "latest_scheduled"
                    ]
                )
            ),
        },
        "observation": {
            "count": observation_count,
            "with_source_snapshot_count": (
                observation_snapshot_count
            ),
            "with_source_snapshot_pct": pct(
                observation_snapshot_count,
                observation_count,
            ),
            "earliest_known": _iso_or_none(
                observation_bounds[
                    "earliest_known"
                ]
            ),
            "latest_known": _iso_or_none(
                observation_bounds[
                    "latest_known"
                ]
            ),
            "earliest_release": (
                _iso_or_none(
                    observation_bounds[
                        "earliest_release"
                    ]
                )
            ),
            "latest_release": (
                _iso_or_none(
                    observation_bounds[
                        "latest_release"
                    ]
                )
            ),
        },
    }


def _compute_artifact_fingerprint(
    artifact: Dict[str, Any],
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

    return hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def main() -> int:
    print(
        "=== XAUUSD PHASE 3A "
        "DESCRIPTIVE EVIDENCE REPLAY ==="
    )

    _require_clean_worktree()

    code_revision = _git_revision()

    print(
        f"CODE_REVISION = {code_revision}"
    )

    manifest = _load_manifest()

    manifest_dataset_fp = (
        manifest.get(
            "phase6_15m_dataset_fingerprint"
        )
        or manifest["dataset_fingerprint"]
    )

    manifest_count = (
        manifest["timeframe_counts"]["15m"]
    )

    policy = (
        load_governed_selection_policy()
    )

    print(
        "SELECTION_POLICY_ID = "
        f"{policy.policy_id}"
    )
    print(
        "SELECTION_POLICY_FINGERPRINT = "
        f"{policy.policy_fingerprint}"
    )

    listing = _resolve_primary_listing()

    print(
        "PRIMARY_PROVIDER = "
        f"{listing.provider}"
    )
    print(
        "PRIMARY_SYMBOL = "
        f"{listing.provider_symbol}"
    )

    if (
        manifest.get("primary_provider")
        != listing.provider
    ):
        raise RuntimeError(
            "Primary provider mismatch between "
            "manifest and current listing."
        )

    candles = _load_governed_candles(
        listing=listing,
        start_time=policy.historical_start,
        end_time_exclusive=(
            policy.historical_end_exclusive
        ),
    )

    actual_count = len(candles)

    print(
        f"15M_CANDLE_COUNT = {actual_count}"
    )
    print(
        "EXPECTED_15M_CANDLE_COUNT = "
        f"{manifest_count}"
    )

    if actual_count != manifest_count:
        raise RuntimeError(
            "15m candle count mismatch: "
            f"actual={actual_count}, "
            f"expected={manifest_count}"
        )

    macro_summary = (
        _build_macro_pit_summary(
            policy.historical_end_exclusive
        )
    )

    auxiliary = {
        "selection_policy": {
            "policy_id": (
                policy.policy_id
            ),
            "policy_fingerprint": (
                policy.policy_fingerprint
            ),
            "historical_start": (
                policy.historical_start
                .isoformat()
            ),
            "historical_end_exclusive": (
                policy
                .historical_end_exclusive
                .isoformat()
            ),
        },
        "macro_pit": macro_summary,

        # Explicitly record why old manifest
        # macro status is not authoritative now.
        "legacy_manifest_macro_status": {
            "manifest_generated_at": (
                manifest.get(
                    "generated_at"
                )
            ),
            "manifest_decision": (
                manifest.get(
                    "hard_data_readiness_gate",
                    {},
                ).get("decision")
            ),
            "used_as_current_macro_evidence": (
                False
            ),
        },
    }

    evidence = (
        build_phase3a_descriptive_evidence(
            candles=candles,
            instrument="XAUUSD",
            provider=listing.provider,
            timeframe="15m",
            code_revision=code_revision,
            expected_dataset_fingerprint=(
                manifest_dataset_fp
            ),
            auxiliary_evidence=auxiliary,
            fold_window_start=policy.historical_start,
            fold_window_end=(
                policy.historical_end_exclusive
            ),
        )
    )

    evidence_fp = (
        compute_phase3a_evidence_fingerprint(
            evidence
        )
    )

    print(
        "DATASET_FINGERPRINT = "
        f"{evidence['dataset_fingerprint']}"
    )
    print(
        "EXPECTED_DATASET_FINGERPRINT = "
        f"{manifest_dataset_fp}"
    )
    print(
        "DATASET_FINGERPRINT_MATCH = "
        f"{evidence['dataset_fingerprint'] == manifest_dataset_fp}"
    )

    if (
        evidence["dataset_fingerprint"]
        != manifest_dataset_fp
    ):
        raise RuntimeError(
            "Governed dataset fingerprint mismatch."
        )

    artifact = {
        "schema": ARTIFACT_SCHEMA,
        "artifact_id": ARTIFACT_ID,
        "instrument": "XAUUSD",
        "timeframe": "15m",
        "status": (
            "DESCRIPTIVE_EVIDENCE_ONLY"
        ),

        "production_authority": False,
        "candidate_profile_authority": False,

        "created_at": (
            datetime.now(
                timezone.utc
            ).isoformat()
        ),

        "code_revision": code_revision,

        "selection_policy_fingerprint": (
            policy.policy_fingerprint
        ),

        "dataset_fingerprint": (
            evidence["dataset_fingerprint"]
        ),

        "legacy_manifest_dataset_fingerprint": (
            manifest_dataset_fp
        ),

        "evidence_schema": (
            PHASE3A_EVIDENCE_SCHEMA
        ),

        "evidence_fingerprint": (
            evidence_fp
        ),

        "evidence": evidence,
    }

    artifact["artifact_fingerprint"] = (
        _compute_artifact_fingerprint(
            artifact
        )
    )

    OUTPUT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with OUTPUT_PATH.open(
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            artifact,
            handle,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        handle.write("\n")

    print(
        "EVIDENCE_FINGERPRINT = "
        f"{evidence_fp}"
    )

    print(
        "ARTIFACT_FINGERPRINT = "
        f"{artifact['artifact_fingerprint']}"
    )

    print(
        "OUTPUT = "
        f"{OUTPUT_PATH}"
    )

    print()
    print(
        "STATUS = DESCRIPTIVE_EVIDENCE_ONLY"
    )
    print(
        "PRODUCTION_AUTHORITY = false"
    )
    print(
        "CANDIDATE_PROFILE_AUTHORITY = false"
    )
    a16 = evidence["a16"]

    print(
        "A16_POLICY_FINGERPRINT = "
        f"{a16['policy_fingerprint']}"
    )

    print(
        "A16_STATUS = "
        f"{a16['status']}"
    )

    print(
        "A16_SESSION_CERTIFIED_BUCKETS = "
        f"{a16['session']['certified_bucket_count']}"
        "/"
        f"{a16['session']['bucket_count']}"
    )

    print(
        "A16_CALENDAR_CERTIFIED_BUCKETS = "
        f"{a16['calendar']['certified_bucket_count']}"
        "/"
        f"{a16['calendar']['bucket_count']}"
    )

    print(
        "A16_SWING_CERTIFIED = "
        f"{a16['swing']['is_certified']}"
    )

    print(
        "A16_GLOBAL_CERTIFICATION = "
        f"{a16['effective_n_certified']}"
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
